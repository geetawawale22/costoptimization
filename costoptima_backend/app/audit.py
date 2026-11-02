"""One audit recorder for transactional mutations and independent security events.
Compatibility: future models must register an explicit safe-field policy in POLICIES;
semantic jobs/files use record() in their transaction or record_independent().
"""
from contextvars import ContextVar
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import hmac
import json
import secrets
from pathlib import Path
from uuid import uuid4
from sqlalchemy import event, inspect, select
from app.config import get_settings

_context = ContextVar("audit_context", default=None)
POLICIES = {
    "users": {"name", "employee_id", "department", "job_title", "company", "role_id", "status", "is_deleted", "deactivation_reason"},
    "roles": {"name", "description", "is_deleted"},
    "permissions": {"resource", "action", "description", "is_deleted"},
    "role_permissions": {"role_id", "permission_id", "is_deleted"},
    "app_config": {"group", "key", "type", "is_secret", "is_active", "is_deleted"},
    "sessions": {"user_id", "expires_at", "is_deleted"},
}
SENSITIVE = {"password", "hashed_password", "secret", "token", "cookie", "assertion", "authorization", "content", "email", "mobile"}


def register_resource(model, fields):
    """Register a future model's reviewed safe-field policy; never infer secrets."""
    mapper = inspect(model)
    columns = set(mapper.columns.keys())
    if not set(fields) <= columns or any(any(word in field.lower() for word in SENSITIVE) for field in fields):
        raise ValueError("Audit policies must contain only reviewed, non-sensitive model fields")
    POLICIES[mapper.local_table.name] = set(fields)


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def context():
    return _context.get() or {}


@contextmanager
def activity_context(**values):
    """Reusable trusted context for requests and future background jobs."""
    token = _context.set({"request_id":str(uuid4()), **context(), **values})
    try:
        yield context()
    finally:
        _context.reset(token)


def signing_key():
    settings = get_settings()
    value = settings.audit_signing_key
    if not value:
        path = Path(__file__).resolve().parents[1] / ".audit-signing-key"
        value = path.read_text(encoding="ascii").strip()
    if len(value) < 64:
        raise RuntimeError("Audit signing key must contain at least 64 characters")
    return value.encode("ascii")


def session_ref(token):
    return hmac.new(signing_key(), b"session:" + token.encode(), hashlib.sha256).hexdigest() if token else None


def safe(value, depth=0):
    if depth > 5:
        return "[depth limit]"
    if isinstance(value, dict):
        return {str(k)[:100]: ({"changed": True} if any(s in str(k).lower() for s in SENSITIVE) else safe(v, depth+1)) for k, v in list(value.items())[:100]}
    if isinstance(value, (list, tuple, set)):
        items = [safe(v, depth+1) for v in list(value)[:200]]
        return items if len(value) <= 200 else {"items":items, "total":len(value), "truncated":True}
    if isinstance(value, datetime):
        return value.isoformat(timespec="microseconds")
    if value is None or isinstance(value, (bool, int, float)):
        return value
    text = str(value)
    return text if len(text) <= 500 else text[:480]+" [truncated]"


def canonical(values):
    return json.dumps({k: safe(v) for k, v in values.items() if k not in {"id", "integrity_mac"}}, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()


def make_event(resource, action, outcome="success", *, target_type=None, target_id=None, changes=None, metadata=None, summary=None, error_category=None, http_status=None):
    ctx = context()
    row = dict(event_id=str(uuid4()), occurred_at=now(), recorded_at=now(),
        actor_user_id=ctx.get("actor_user_id"), session_ref=ctx.get("session_ref"), request_id=ctx.get("request_id"),
        resource=resource[:100], action=action[:100], target_type=target_type, target_id=str(target_id)[:128] if target_id is not None else None,
        outcome=outcome, http_method=ctx.get("http_method"), route=ctx.get("route"), http_status=http_status,
        client_ip=ctx.get("client_ip"), user_agent=ctx.get("user_agent"), summary=(summary or resource+"."+action)[:500],
        changes=safe(changes) if changes else None, metadata=safe(metadata) if metadata else None,
        error_category=error_category, integrity_key_id=get_settings().audit_key_id)
    for field in ("changes", "metadata"):
        encoded = json.dumps(row[field], ensure_ascii=True, allow_nan=False).encode()
        if len(encoded) > 32768:
            row[field] = {"truncated":True,"bytes":len(encoded),"sha256":hashlib.sha256(encoded).hexdigest()}
    row["integrity_mac"] = hmac.new(signing_key(), canonical(row), hashlib.sha256).hexdigest()
    return row


def verify_integrity(row):
    if row["integrity_key_id"] != get_settings().audit_key_id:
        return "unknown_key"
    expected = hmac.new(signing_key(), canonical(row), hashlib.sha256).hexdigest()
    return "valid" if hmac.compare_digest(expected, row["integrity_mac"]) else "invalid"


def record(db, resource, action, **values):
    """Queues an event for insertion in the caller's business transaction."""
    db.info.setdefault("audit_explicit", []).append(make_event(resource, action, **values))


def fallback(row):
    from app.observability import log_audit_fallback
    log_audit_fallback(row)


def record_independent(resource, action, **values):
    """Security/failure events survive rollback; failure is durably signalled."""
    from app.database import engine
    from app.models import AuditEvent
    row = make_event(resource, action, **values)
    try:
        with engine.begin() as connection:
            connection.execute(AuditEvent.__table__.insert(), [row])
        return True
    except Exception:
        fallback(row)
        return False


def snapshot(db, obj):
    state = inspect(obj)
    if state.pending:
        return {}
    # Read actual persisted values, even if SQLAlchemy discarded old attributes.
    table = state.mapper.local_table
    pk = state.identity
    if not pk:
        return {}
    row = db.connection().execute(select(table).where(*[col == value for col, value in zip(table.primary_key.columns, pk)])).mappings().first()
    return dict(row) if row else {}


def before_flush(db, flush_context, instances):
    if db.info.get("audit_skip"):
        return
    from app.models import AuditEvent
    if any(isinstance(x, AuditEvent) for x in db.dirty | db.deleted):
        raise RuntimeError("Audit history is append-only")
    pending = db.info.setdefault("audit_changes", {})
    for obj in db.new | db.dirty | db.deleted:
        table = inspect(obj).mapper.local_table.name
        if table not in POLICIES or table in db.info.get("audit_suppress", set()):
            continue
        if obj not in db.new and obj not in db.deleted and not db.is_modified(obj, include_collections=False):
            continue
        pending.setdefault(id(obj), (obj, snapshot(db, obj), obj in db.new, obj in db.deleted))


def flush_events(db):
    from app.models import AuditEvent
    rows = db.info.pop("audit_explicit", [])
    for obj, old, created, deleted in db.info.pop("audit_changes", {}).values():
        table = inspect(obj).mapper.local_table.name
        changed = {}
        for field in POLICIES[table]:
            previous = old.get(field)
            current = None if deleted else getattr(obj, field, None)
            if previous != current:
                changed[field] = {"before": safe(previous), "after": safe(current)}
        for field in (["hashed_password", "email", "mobile"] if table == "users" else ["value", "content"] if table == "app_config" else []):
            if old.get(field) != getattr(obj, field, None):
                changed[field] = {"changed": True}
        if not changed:
            continue
        action = "created" if created else "deleted" if deleted or (not old.get("is_deleted") and getattr(obj, "is_deleted", False)) else "updated"
        if table == "users" and "status" in changed and not created:
            action = "activated" if obj.status == "Active" else "deactivated"
        if table == "app_config" and "is_active" in changed and not created:
            action = "enabled" if obj.is_active else "disabled"
        if table == "sessions":
            action = "created" if created else "revoked" if (deleted or (not old.get("is_deleted") and obj.is_deleted)) else "updated"
        target = session_ref(obj.id) if table == "sessions" else obj.id
        rows.append(make_event(table, action, target_type=table, target_id=target, changes=changed))
    if rows:
        db.connection().execute(AuditEvent.__table__.insert(), rows)


def after_flush(db, flush_context):
    if not db.info.get("audit_skip"):
        flush_events(db)


def before_commit(db):
    if db.info.get("audit_skip"):
        return
    db.flush()
    flush_events(db)


def reset(db):
    for key in ("audit_changes", "audit_explicit", "audit_suppress", "audit_skip"):
        db.info.pop(key, None)


def guard_bulk(execute_state):
    """Prevent bulk writes from silently bypassing the change recorder."""
    statement = execute_state.statement
    if execute_state.is_update or execute_state.is_delete or execute_state.is_insert:
        table = getattr(getattr(statement, "table", None), "name", None)
        if table == "audit_events" or table in POLICIES:
            raise RuntimeError("Use tracked ORM mutations or the centralized audit recorder, not bulk DML")
    from sqlalchemy.sql.elements import TextClause
    if isinstance(statement, TextClause):
        text = statement.text.lstrip().lower()
        if not text.startswith(("select", "with", "explain")):
            raise RuntimeError("Raw mutation SQL is not supported by audited application sessions")


def install(session_class):
    if event.contains(session_class, "before_flush", before_flush):
        return
    event.listen(session_class, "do_orm_execute", guard_bulk)
    event.listen(session_class, "before_flush", before_flush)
    event.listen(session_class, "after_flush_postexec", after_flush)
    event.listen(session_class, "before_commit", before_commit)
    event.listen(session_class, "after_commit", reset)
    event.listen(session_class, "after_rollback", reset)

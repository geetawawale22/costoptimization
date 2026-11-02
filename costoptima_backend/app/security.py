import hashlib
import hmac
import re
import secrets
import threading
import time
from collections import OrderedDict, deque
from datetime import datetime
from fastapi import Depends, HTTPException, Request
from passlib.hash import pbkdf2_sha256
from passlib.context import CryptContext
from fastapi import status
from sqlalchemy.orm import Session
from app.config import get_settings
from app.database import get_db
from app.models import User, Role, Permission, RolePermission, LoginSession

COOKIE_NAME = "costoptima_new_session"
SESSION_PATTERN = re.compile(r"co4\.[A-Za-z0-9_-]{60}\Z")
SCOPED_PAIRS = {(resource, action) for resource in ("users", "roles", "settings") for action in ("view", "create", "update", "delete")}
PASSWORD_ROUNDS = 600_000
pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto", pbkdf2_sha256__default_rounds=PASSWORD_ROUNDS)
_dummy_hash = pbkdf2_sha256.using(rounds=PASSWORD_ROUNDS).hash(secrets.token_urlsafe(32))
_attempts = OrderedDict()
_attempt_lock = threading.Lock()


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return pwd_context.verify(plain, hashed)
    except (ValueError, TypeError):
        return False

def verify_login_password(password, user):
    return verify_password(password, user.hashed_password if user else _dummy_hash)


def check_login_limit(request, email):
    now = time.monotonic()
    client = request.client.host if request.client else "unknown"
    keys = [("ip", client), ("account", client, email.lower())]
    with _attempt_lock:
        for key in keys:
            bucket = _attempts.setdefault(key, deque())
            while bucket and bucket[0] <= now - 900:
                bucket.popleft()
            _attempts.move_to_end(key)
            limit = 50 if key[0] == "ip" else 10
            if len(bucket) >= limit:
                raise HTTPException(429, "Too many login attempts. Try again later.", headers={"Retry-After": "900"})
        for key in keys:
            _attempts[key].append(now)
        while len(_attempts) > 10_000:
            _attempts.popitem(last=False)


def csrf_token(session_id):
    return hmac.new(get_settings().csrf_signing_key.encode(), session_id.encode(), hashlib.sha256).hexdigest()


def current_user(request: Request, db: Session = Depends(get_db)):
    token = request.cookies.get(COOKIE_NAME, "")
    if not SESSION_PATTERN.fullmatch(token):
        raise HTTPException(401, "Not authenticated")
    session = db.query(LoginSession).filter(LoginSession.id == token, LoginSession.is_deleted.is_(False)).first()
    if not session or not secrets.compare_digest(session.id, token) or session.expires_at <= datetime.utcnow():
        raise HTTPException(401, "Not authenticated")
    user = db.query(User).filter(User.id == session.user_id, User.is_deleted.is_(False), User.status == "Active").first()
    if not user:
        raise HTTPException(401, "Not authenticated")
    from app.audit import context, session_ref
    context().update(actor_user_id=user.id, session_ref=session_ref(token))
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        supplied = request.headers.get("x-csrf-token", "")
        if not secrets.compare_digest(supplied.encode("utf-8"), csrf_token(token).encode("ascii")):
            raise HTTPException(403, "Invalid CSRF token")
    return user


def permission_pairs(db, user):
    if user.role_id is None:
        return set()
    rows = (db.query(Permission.resource, Permission.action)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .join(Role, Role.id == RolePermission.role_id)
            .filter(Role.id == user.role_id, Role.is_deleted.is_(False),
                    RolePermission.is_deleted.is_(False), Permission.is_deleted.is_(False)).all())
    return set(rows)


def require_permission(resource: str, action: str):
    """Dependency factory gating an endpoint behind a (resource, action) permission,
    e.g. require_permission('masters', 'update'). Checked via Role -> RolePermission -> Permission."""

    def _checker(user: User = Depends(current_user), db: Session = Depends(get_db)) -> User:
        if not user.role_id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Your account has no role assigned")
        grant = (
            db.query(RolePermission)
            .join(Permission, RolePermission.permission_id == Permission.id)
            .join(Role, Role.id == RolePermission.role_id)
            .filter(
                Role.is_deleted.is_(False),
                RolePermission.role_id == user.role_id,
                RolePermission.is_deleted.is_(False),
                Permission.resource == resource,
                Permission.action == action,
                Permission.is_deleted.is_(False),
            )
            .first()
        )
        if not grant:
            raise HTTPException(status.HTTP_403_FORBIDDEN,
                                 detail=f"Your role does not have '{action}' permission on '{resource}'")
        return user

    return _checker


def ensure_permission(db, user, resource, action):
    if (resource, action) not in permission_pairs(db, user):
        raise HTTPException(403, "Permission denied")


def role_pairs(db, role_id):
    return set(db.query(Permission.resource, Permission.action).join(RolePermission, RolePermission.permission_id == Permission.id)
               .filter(RolePermission.role_id == role_id, RolePermission.is_deleted.is_(False), Permission.is_deleted.is_(False)).all())


def management_lock(db, actor, resource, action):
    # All new-application access mutations lock in the same order. The old app does not share these safeguards.
    db.query(Role).order_by(Role.id).populate_existing().with_for_update().all()
    db.query(User).order_by(User.id).populate_existing().with_for_update().all()
    db.query(RolePermission).order_by(RolePermission.id).populate_existing().with_for_update().all()
    db.refresh(actor)
    if actor.is_deleted or actor.status != "Active":
        raise HTTPException(401, "Not authenticated")
    ensure_permission(db, actor, resource, action)


def admin_count(db):
    users = db.query(User).filter(User.is_deleted.is_(False), User.status == "Active").all()
    return sum(SCOPED_PAIRS <= permission_pairs(db, user) for user in users)


def protect_target(db, actor, target):
    if not permission_pairs(db, target) <= permission_pairs(db, actor):
        raise HTTPException(403, "Cannot manage an account with permissions you do not hold")


def validate_role_assignment(db, actor, role_id):
    ensure_permission(db, actor, "roles", "update")
    role = db.query(Role).filter(Role.id == role_id, Role.is_deleted.is_(False)).first()
    if not role:
        raise HTTPException(422, "Select an existing active role")
    if not role_pairs(db, role.id) <= permission_pairs(db, actor):
        raise HTTPException(403, "Cannot assign permissions you do not hold")


def guard_admin_count(db, before):
    db.flush()
    if before and not admin_count(db):
        raise HTTPException(409, "This change would remove the last active administrator")





SESSION_COOKIE_NAME = COOKIE_NAME
get_current_user = current_user

def generate_session_token():
    return "co4." + secrets.token_urlsafe(45)

def user_can(db: Session, user: User, resource: str, action: str) -> bool:
    if not user.role_id:
        return False
    grant = (
        db.query(RolePermission)
        .join(Permission, RolePermission.permission_id == Permission.id)
            .join(Role, Role.id == RolePermission.role_id)
        .filter(
            Role.is_deleted.is_(False),
                RolePermission.role_id == user.role_id,
            RolePermission.is_deleted.is_(False),
            Permission.resource == resource,
            Permission.action == action,
            Permission.is_deleted.is_(False),
        )
        .first()
    )
    return bool(grant)

def require_any_permission(*permissions: tuple[str, str]):
    def _checker(user: User = Depends(current_user), db: Session = Depends(get_db)) -> User:
        if not user.role_id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Your account has no role assigned")
        for resource, action in permissions:
            if user_can(db, user, resource, action):
                return user
        allowed = ", ".join(f"{action} on {resource}" for resource, action in permissions)
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            detail=f"Your role needs one of these permissions: {allowed}")

    return _checker

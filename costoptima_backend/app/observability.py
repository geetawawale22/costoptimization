"""Outer ASGI request tracing; no bodies, query strings, cookies or exception text."""
import json
import os
import traceback
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from time import perf_counter
from uuid import uuid4
from datetime import datetime
from starlette.concurrency import run_in_threadpool
from app.config import get_settings
from app.audit import activity_context, context, record_independent, session_ref, now


def logger(name, filename):
    log = logging.getLogger(name)
    if not log.handlers:
        directory = Path(get_settings().audit_log_dir)
        directory.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(directory / filename, maxBytes=20*1024*1024, backupCount=10, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(message)s"))
        log.addHandler(handler)
        log.setLevel(logging.INFO)
        log.propagate = False
    return log


def technical(event, **values):
    logger("costoptima.requests", "requests.jsonl").info(json.dumps({"timestamp":now().isoformat()+"Z", "event":event, **values}, default=str, ensure_ascii=True))


def log_audit_fallback(row):
    # Contains only the recorder's already-sanitized, signed event.
    log = logger("costoptima.audit_fallback", "audit-fallback.jsonl")
    log.critical(json.dumps({"event":"audit.persistence_failed", "audit":row}, default=str))
    for handler in log.handlers:
        handler.flush()
        if getattr(handler, "stream", None):
            os.fsync(handler.stream.fileno())
    technical("audit.persistence_failed", request_id=row.get("request_id"))


def resolve_session(token):
    from app.database import SessionLocal
    from app.models import LoginSession, User
    from app.security import SESSION_PATTERN
    if not token or not SESSION_PATTERN.fullmatch(token):
        return {}
    with SessionLocal() as db:
        session = db.get(LoginSession, token)
        if not session:
            return {}
        ref = session_ref(token)
        if session.is_deleted:
            return {"session_ref":ref}
        if session.expires_at <= datetime.utcnow():
            return {"session_ref":ref, "expired":True}
        user = db.get(User, session.user_id)
        return {"session_ref":ref, "actor_user_id":user.id} if user and not user.is_deleted and user.status == "Active" else {"session_ref":ref}


class RequestTracing:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        from starlette.requests import Request
        from app.security import COOKIE_NAME
        request = Request(scope)
        started = perf_counter()
        status = 500
        request_id = str(uuid4())
        from starlette.routing import Match
        matched_route = "[unmatched]"
        # Origin/CSRF middleware can reject before routing; still record a safe route template.
        for candidate in getattr(scope.get("app"), "routes", []):
            if candidate.matches(scope)[0] == Match.FULL:
                matched_route = getattr(candidate, "path", "[unmatched]")
                break
        values = dict(request_id=request_id, http_method=scope["method"], route=matched_route,
            client_ip=request.client.host[:45] if request.client else None,
            user_agent=request.headers.get("user-agent", "")[:512])
        with activity_context(**values) as ctx:
            try:
                ctx.update(await run_in_threadpool(resolve_session, request.cookies.get(COOKIE_NAME)))
            except Exception:
                technical("session.context_unavailable", request_id=request_id)
            async def traced_send(message):
                nonlocal status
                route = scope.get("route")
                ctx["route"] = getattr(route, "path", ctx["route"])
                if message["type"] == "http.response.start":
                    status = message["status"]
                    headers = list(message.get("headers", []))
                    headers.append((b"x-request-id", request_id.encode()))
                    message = {**message, "headers":headers}
                await send(message)
            error = None
            frames = None
            try:
                await self.app(scope, receive, traced_send)
            except Exception as exc:
                error = type(exc).__name__
                frames = [{"file":Path(frame.filename).name, "line":frame.lineno, "function":frame.name} for frame in traceback.extract_tb(exc.__traceback__)[-12:]]
                raise
            finally:
                route = scope.get("route")
                ctx["route"] = getattr(route, "path", ctx["route"])
                path = scope.get("path", "")
                outcome = "denied" if status in {401,403} else "failure" if status >= 400 else "success"
                if ctx.get("expired"):
                    await run_in_threadpool(record_independent, "sessions", "expiration_observed", outcome="observed", http_status=status)
                if (status >= 400 or error) and not ctx.get("failure_recorded"):
                    if error:
                        outcome = "failure"
                    resource = "auth" if path.startswith("/api/auth/") or path == "/sso" else ctx.get("resource") or ("authorization" if status in {401,403} else "server")
                    action = "login" if path == "/api/auth/login" else "sso.exchange" if path == "/api/auth/sso/exchange" else "request_denied" if status in {401,403} else "request_failed"
                    if resource == "masters":
                        if "/import/" in path:action="import.failed"
                        elif path.endswith(("/export", "/template")) or path=="/api/master-workbook":action="export.failed"
                    if resource == "auth" and action in {"login", "sso.exchange"}:
                        outcome = "failure"
                    await run_in_threadpool(record_independent, resource, action, outcome=outcome, http_status=status, error_category=error or "http_"+str(status), target_type=ctx.get("target_type"), target_id=ctx.get("target_id"), metadata=ctx.get("event_metadata"))
                elif ("/sso" in path) and not ctx.get("sso_recorded"):
                    action = "sso.login" if path.endswith("/login") else "sso.callback" if path in {"/sso", "/api/auth/sso/callback"} else None
                    if action:
                        await run_in_threadpool(record_independent, "auth", action, outcome="attempt", http_status=status)
                technical("http.request", request_id=request_id, actor_user_id=ctx.get("actor_user_id"), session_ref=ctx.get("session_ref"), method=scope["method"], route=ctx["route"], status=status, duration_ms=round((perf_counter()-started)*1000,3), error_category=error, stack=frames)

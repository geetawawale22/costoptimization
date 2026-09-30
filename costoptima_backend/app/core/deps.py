from datetime import datetime
from fastapi import Depends, HTTPException, status, Request
from sqlalchemy.orm import Session as DbSession
from app.database import get_db
from app.models.user import User, Session as SessionModel, RolePermission, Permission

SESSION_COOKIE_NAME = "session_id"


def get_current_user(request: Request, db: DbSession = Depends(get_db)) -> User:
    """Session-based auth: reads the opaque session id from an HttpOnly cookie, looks it
    up in the sessions table (not expired, not soft-deleted), and returns the user."""
    credentials_exception = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        raise credentials_exception

    session = (
        db.query(SessionModel)
        .filter(SessionModel.id == token, SessionModel.is_deleted.is_(False))
        .first()
    )
    if not session or session.expires_at < datetime.utcnow():
        raise credentials_exception

    user = db.query(User).filter(User.id == session.user_id, User.is_deleted.is_(False)).first()
    if not user or user.status != "Active":
        raise credentials_exception

    session.last_seen_at = datetime.utcnow()
    db.commit()
    return user


def require_permission(resource: str, action: str):
    """Dependency factory gating an endpoint behind a (resource, action) permission,
    e.g. require_permission('masters', 'update'). Checked via Role -> RolePermission -> Permission."""

    def _checker(user: User = Depends(get_current_user), db: DbSession = Depends(get_db)) -> User:
        if not user.role_id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Your account has no role assigned")
        grant = (
            db.query(RolePermission)
            .join(Permission, RolePermission.permission_id == Permission.id)
            .filter(
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


def require_any_permission(*permissions: tuple[str, str]):
    def _checker(user: User = Depends(get_current_user), db: DbSession = Depends(get_db)) -> User:
        if not user.role_id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Your account has no role assigned")
        for resource, action in permissions:
            if user_can(db, user, resource, action):
                return user
        allowed = ", ".join(f"{action} on {resource}" for resource, action in permissions)
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            detail=f"Your role needs one of these permissions: {allowed}")

    return _checker


def user_can(db: DbSession, user: User, resource: str, action: str) -> bool:
    if not user.role_id:
        return False
    grant = (
        db.query(RolePermission)
        .join(Permission, RolePermission.permission_id == Permission.id)
        .filter(
            RolePermission.role_id == user.role_id,
            RolePermission.is_deleted.is_(False),
            Permission.resource == resource,
            Permission.action == action,
            Permission.is_deleted.is_(False),
        )
        .first()
    )
    return bool(grant)

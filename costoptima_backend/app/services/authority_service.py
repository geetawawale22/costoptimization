"""Commodity-scoped authorization, migrated from the legacy Authority_Matrix.

Legacy granted authority per commodity, not globally. This module answers
"may this user act on this commodity?" from ``user_commodity_roles``.

Deny-by-default
---------------
A user with no row for a commodity is denied, rather than falling back to
``User.role_id``. Falling back would silently grant a PCE for one commodity the same
authority over every other commodity - broader access than the legacy system gave, arrived
at by accident. If a genuine global role is needed, grant it explicitly through
``GLOBAL_ROLE_NAMES`` below.

This is enforcement logic, so it is deliberately small and readable.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.authority import UserCommodityRole
from app.models.user import Role, User

# Roles that legacy treated as landscape-wide. 'Superuser' appears in Folder_Authority
# against the PCM_Master root rather than a single commodity zone, so it is global by
# construction. Everything else is commodity-scoped.
GLOBAL_ROLE_NAMES = {"superuser", "super admin", "admin"}


def _norm(value) -> str:
    return " ".join(str(value or "").split()).casefold()


def is_global(user: User) -> bool:
    role = getattr(user, "role", None)
    return bool(role and _norm(role.name) in GLOBAL_ROLE_NAMES)


def roles_for_commodity(db: Session, user_id: int, commodity: str) -> list[str]:
    """Return the role names this user holds over this commodity."""
    rows = (
        db.query(UserCommodityRole, Role)
        .join(Role, Role.id == UserCommodityRole.role_id)
        .filter(
            UserCommodityRole.user_id == user_id,
            UserCommodityRole.status == "Active",
        )
        .all()
    )
    target = _norm(commodity)
    return [role.name for link, role in rows if _norm(link.commodity) == target]


def commodities_for_user(db: Session, user_id: int) -> list[str]:
    rows = (
        db.query(UserCommodityRole)
        .filter(UserCommodityRole.user_id == user_id, UserCommodityRole.status == "Active")
        .all()
    )
    return sorted({row.commodity for row in rows})


def may_act_on_commodity(db: Session, user: User, commodity: str,
                         required_roles: set[str] | None = None) -> bool:
    """Deny-by-default authorization check.

    ``required_roles`` narrows the check to specific role names (case-insensitive). With
    none supplied, holding any active role over the commodity is sufficient.
    """
    if user is None:
        return False
    if is_global(user):
        return True
    held = roles_for_commodity(db, user.id, commodity)
    if not held:
        return False
    if not required_roles:
        return True
    wanted = {_norm(name) for name in required_roles}
    return any(_norm(name) in wanted for name in held)


def assert_may_act_on_commodity(db: Session, user: User, commodity: str,
                                required_roles: set[str] | None = None) -> None:
    """Raise HTTP 403 when the user has no authority over the commodity."""
    if not may_act_on_commodity(db, user, commodity, required_roles):
        from fastapi import HTTPException

        raise HTTPException(
            status_code=403,
            detail={
                "code": "COMMODITY_FORBIDDEN",
                "message": f"No active role over commodity {commodity!r}.",
                "commodity": commodity,
            },
        )

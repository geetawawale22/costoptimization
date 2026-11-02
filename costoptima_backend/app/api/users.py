"""
Reusable server-side pagination helper.

Every list endpoint in the app should accept `page` (1-based) and `page_size` query
params and return a PaginatedResponse. Keeping this in one place means every page in
the UI gets identical pagination semantics (page, page_size, total, total_pages) with
no per-endpoint drift.
"""
import math
from typing import Generic, List, Optional, TypeVar, Annotated
from pydantic import BaseModel, BeforeValidator, PlainSerializer
from sqlalchemy.orm import Query

T = TypeVar("T")

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 500


def _coerce_int(v):
    """Runs at VALIDATION time (model construction / from_attributes). Turns a stray
    float (NaN, Infinity, or a whole-number float like 2.0) into a clean int/None."""
    if v is None or isinstance(v, bool):
        return v
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return None
        return int(round(v))
    return v


def _serialize_int(v):
    """Runs at SERIALIZATION time (response dump). Belt-and-suspenders alongside
    _coerce_int: pydantic-core's int serializer raises
    "TypeError: 'float' object cannot be interpreted as an integer" whenever the value
    actually stored on the model instance is still a raw float when dump_python()/
    dump_json() runs — which validation-time coercion alone does not always catch
    (e.g. instances built via from_attributes off a fresh ORM object, or any path that
    doesn't route the value through the field's BeforeValidator first). A
    PlainSerializer intercepts unconditionally at dump time, so this class of crash
    can no longer happen regardless of how the float got onto the instance."""
    if v is None or isinstance(v, bool):
        return v
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return None
        return int(round(v))
    return v


SafeInt = Annotated[
    int,
    BeforeValidator(_coerce_int),
    PlainSerializer(_serialize_int, return_type=Optional[int], when_used="always"),
]
SafeOptionalInt = Annotated[
    Optional[int],
    BeforeValidator(_coerce_int),
    PlainSerializer(_serialize_int, return_type=Optional[int], when_used="always"),
]


class PaginatedResponse(BaseModel, Generic[T]):
    items: List[T]
    total: SafeInt
    page: SafeInt
    page_size: SafeInt
    total_pages: SafeInt


def paginate_query(query: Query, page: int = 1, page_size: int = DEFAULT_PAGE_SIZE):
    """Applies offset/limit to a SQLAlchemy Query and returns
    (items, total, page, page_size, total_pages). `total` is computed with a COUNT
    query rather than len(all()) so it stays cheap on large tables."""
    page = max(1, page or 1)
    page_size = min(max(1, page_size or DEFAULT_PAGE_SIZE), MAX_PAGE_SIZE)

    total = query.order_by(None).count()
    items = query.offset((page - 1) * page_size).limit(page_size).all()
    total_pages = (total + page_size - 1) // page_size if page_size else 0

    return items, total, page, page_size, total_pages


def paginate_list(items: List[T], page: int = 1, page_size: int = DEFAULT_PAGE_SIZE):
    """Same contract as paginate_query but for an already-materialized Python list
    (used where filtering happens in-memory, e.g. searching JSON row_data)."""
    page = max(1, page or 1)
    page_size = min(max(1, page_size or DEFAULT_PAGE_SIZE), MAX_PAGE_SIZE)

    total = len(items)
    start = (page - 1) * page_size
    page_items = items[start:start + page_size]
    total_pages = (total + page_size - 1) // page_size if page_size else 0

    return page_items, total, page, page_size, total_pages

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func
from app.database import get_db
from app.models import User, Role, LoginSession, stamp_create, stamp_update
from app.schemas import UserOut, UserCreate, UserUpdate
from app.security import (require_permission, hash_password, management_lock, protect_target,
    validate_role_assignment, admin_count, guard_admin_count, ensure_permission)
router = APIRouter(prefix="/api", tags=["Users"])
@router.get("/users", response_model=PaginatedResponse[UserOut])
def list_users(page: int = 1, page_size: int = DEFAULT_PAGE_SIZE, q: str = "",
               db: Session = Depends(get_db), _=Depends(require_permission("users", "view"))):
    query = db.query(User).options(joinedload(User.role)).filter(User.is_deleted.is_(False))
    if q:
        like = f"%{q}%"
        query = query.filter((User.name.ilike(like)) | (User.email.ilike(like)) | (User.employee_id.ilike(like)))
    query = query.order_by(User.name)
    items, total, page, page_size, total_pages = paginate_query(query, page, page_size)
    return PaginatedResponse[UserOut](items=items, total=total, page=page, page_size=page_size, total_pages=total_pages)


@router.post("/users", response_model=UserOut)
def create_user(payload: UserCreate, db: Session = Depends(get_db),
                 user=Depends(require_permission("users", "create"))):
    management_lock(db, user, "users", "create")
    if len(payload.password) < 15 or len(payload.password) > 128:
        raise HTTPException(422, "Password must contain 15 to 128 characters")
    if payload.role_id is not None:
        validate_role_assignment(db, user, payload.role_id)
    if db.query(User).filter(User.email == payload.email).first():
        raise HTTPException(status_code=400, detail="A user with this email already exists")
    if payload.employee_id and db.query(User).filter(User.employee_id == payload.employee_id, User.is_deleted.is_(False)).first():
        raise HTTPException(status_code=400, detail="A user with this employee ID already exists")
    new_user = User(
        name=payload.name, employee_id=payload.employee_id, email=payload.email, job_title=payload.job_title,
        department=payload.department, company=payload.company, mobile=payload.mobile, role_id=payload.role_id,
        hashed_password=hash_password(payload.password),
    )
    stamp_create(new_user, user.id)
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return new_user


@router.get("/users/{user_id}", response_model=UserOut)
def get_user(user_id: int, db: Session = Depends(get_db), _=Depends(require_permission("users", "view"))):
    u = db.query(User).filter(User.id == user_id, User.is_deleted.is_(False)).first()
    if not u:
        raise HTTPException(status_code=404, detail="User not found")
    return u


@router.put("/users/{user_id}", response_model=UserOut)
def update_user(user_id: int, payload: UserUpdate, db: Session = Depends(get_db),
                 user=Depends(require_permission("users", "update"))):
    u = db.query(User).filter(User.id == user_id, User.is_deleted.is_(False)).first()
    if not u:
        raise HTTPException(status_code=404, detail="User not found")
    management_lock(db, user, "users", "update")
    db.refresh(u)
    protect_target(db, user, u)
    before = admin_count(db)
    if "role_id" in payload.model_fields_set and payload.role_id != u.role_id:
        if u.id == user.id:
            raise HTTPException(409, "You cannot change your own role")
        ensure_permission(db, user, "roles", "update")
        if payload.role_id is not None:
            validate_role_assignment(db, user, payload.role_id)
    if payload.status is not None:
        if payload.status not in {"Active", "Inactive"}:
            raise HTTPException(422, "Status must be Active or Inactive")
        if u.id == user.id and payload.status != u.status:
            raise HTTPException(409, "You cannot change your own activation status")
        if payload.status == "Inactive":
            ensure_permission(db, user, "users", "delete")
            for session in db.query(LoginSession).filter(LoginSession.user_id == u.id, LoginSession.is_deleted.is_(False)):
                session.is_deleted = True
                stamp_update(session, user.id)
    if payload.employee_id:
        duplicate = db.query(User).filter(
            User.employee_id == payload.employee_id,
            User.id != user_id,
            User.is_deleted.is_(False),
        ).first()
        if duplicate:
            raise HTTPException(status_code=400, detail="A user with this employee ID already exists")
    if "name" in payload.model_fields_set and (payload.name is None or not payload.name.strip()):
        raise HTTPException(422, "Name is required")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(u, field, value)
    stamp_update(u, user.id)
    guard_admin_count(db, before)
    db.commit()
    db.refresh(u)
    return u


@router.delete("/users/{user_id}")
def deactivate_user(user_id: int, reason: str = "Deactivated by admin",
                     db: Session = Depends(get_db), user=Depends(require_permission("users", "delete"))):
    """Soft-delete: sets status=Inactive (mirrors the legacy 'Persons Left' pattern);
    is_deleted stays False here since the account itself isn't being purged, just disabled."""
    u = db.query(User).filter(User.id == user_id, User.is_deleted.is_(False)).first()
    if not u:
        raise HTTPException(status_code=404, detail="User not found")
    management_lock(db, user, "users", "delete")
    db.refresh(u)
    protect_target(db, user, u)
    if u.id == user.id:
        raise HTTPException(409, "You cannot deactivate your own account")
    before = admin_count(db)
    for session in db.query(LoginSession).filter(LoginSession.user_id == u.id, LoginSession.is_deleted.is_(False)):
        session.is_deleted = True
        stamp_update(session, user.id)
    u.status = "Inactive"
    u.deactivation_reason = reason
    stamp_update(u, user.id)
    guard_admin_count(db, before)
    db.commit()
    return {"message": "User deactivated", "user_id": user_id}


@router.get("/user-statistics")
def user_statistics(db: Session = Depends(get_db), _=Depends(require_permission("users", "view"))):
    q = db.query(User).filter(User.is_deleted.is_(False))
    return {"total": q.count(), "active": q.filter(User.status == "Active").count(),
            "inactive": q.filter(User.status == "Inactive").count(),
            "admins": q.join(Role, Role.id == User.role_id).filter(Role.name.ilike("%admin%")).count()}

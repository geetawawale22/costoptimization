from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session, joinedload
from typing import List, Union
from app.database import get_db
from app.models import User, Role, Permission, RolePermission, LoginSession, stamp_create, stamp_update
from app.schemas import (UserOut, UserCreate, UserUpdate, RoleOut, RoleCreate, RoleWithPermissions,
                         RolePermissionSet, PermissionOut, PermissionCreate, PermissionUpdate)
from app.security import (current_user as get_current_user, require_permission, hash_password,
    management_lock, protect_target, validate_role_assignment, admin_count, guard_admin_count,
    ensure_permission, permission_pairs, role_pairs)
from app.audit import record as audit_record
from app.api.users import PaginatedResponse, paginate_query, DEFAULT_PAGE_SIZE
router = APIRouter(prefix="/api", tags=["Users, Roles & Permissions"])
@router.get("/roles", response_model=Union[List[RoleOut], PaginatedResponse[RoleOut]])
def list_roles(
    request: Request,
    page: int = 1,
    page_size: int | None = None,
    q: str = "",
    db: Session = Depends(get_db),
    _=Depends(require_permission("roles", "view")),
):
    from app.api.settings import read_settings
    page_size = page_size or read_settings(db)["ui.page_size"]
    query = db.query(Role).filter(Role.is_deleted.is_(False))

    if q:
        query = query.filter(Role.name.ilike(f"%{q}%"))

    query = query.order_by(Role.name)

    wants_page = any(key in request.query_params for key in ("page", "page_size", "q"))
    if not wants_page:
        return query.all()

    items, total, page, page_size, total_pages = paginate_query(query, page, page_size)

    return PaginatedResponse[RoleOut](
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.post("/roles", response_model=RoleOut)
def create_role(payload: RoleCreate, db: Session = Depends(get_db),
                 user=Depends(require_permission("roles", "create"))):
    management_lock(db, user, "roles", "create")
    before = admin_count(db)
    if db.query(Role).filter(Role.name == payload.name, Role.is_deleted.is_(False)).first():
        raise HTTPException(status_code=400, detail=f"A role named '{payload.name}' already exists")
    role = Role(**payload.model_dump())
    stamp_create(role, user.id)
    db.add(role)
    guard_admin_count(db, before)
    db.commit()
    db.refresh(role)
    return role


@router.put("/roles/{role_id}", response_model=RoleOut)
def update_role(role_id: int, payload: RoleCreate, db: Session = Depends(get_db),
                 user=Depends(require_permission("roles", "update"))):
    management_lock(db, user, "roles", "update")
    before = admin_count(db)
    role = db.query(Role).filter(Role.id == role_id, Role.is_deleted.is_(False)).first()
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    if not role_pairs(db, role.id) <= permission_pairs(db, user):
        raise HTTPException(403, "Cannot manage a role with permissions you do not hold")
    for field, value in payload.model_dump().items():
        setattr(role, field, value)
    stamp_update(role, user.id)
    guard_admin_count(db, before)
    db.commit()
    db.refresh(role)
    return role


@router.delete("/roles/{role_id}")
def delete_role(role_id: int, db: Session = Depends(get_db),
                 user=Depends(require_permission("roles", "delete"))):
    """Soft-deletes the role, soft-deletes its role_permissions, and un-assigns any
    user still on this role (role_id -> NULL) so they immediately lose every
    permission the role granted rather than silently keeping access to a 'deleted' role."""
    management_lock(db, user, "roles", "delete")
    before = admin_count(db)
    role = db.query(Role).filter(Role.id == role_id, Role.is_deleted.is_(False)).first()
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    if not role_pairs(db, role.id) <= permission_pairs(db, user):
        raise HTTPException(403, "Cannot manage a role with permissions you do not hold")
    if db.query(User.id).filter(User.role_id == role_id).first():
        raise HTTPException(409, "Reassign all users before deleting this role")
    role.is_deleted = True
    stamp_update(role, user.id)
    for grant in db.query(RolePermission).filter(RolePermission.role_id == role_id):
        grant.is_deleted = True
    affected = db.query(User).filter(User.role_id == role_id, User.is_deleted.is_(False)).all()
    for u in affected:
        u.role_id = None
        stamp_update(u, user.id)
    guard_admin_count(db, before)
    db.commit()
    return {"message": "Role deleted", "role_id": role_id, "users_unassigned": len(affected)}


@router.get("/roles/{role_id}/permissions", response_model=RoleWithPermissions)
def get_role_permissions(role_id: int, db: Session = Depends(get_db), _=Depends(require_permission("roles", "view"))):
    role = db.query(Role).filter(Role.id == role_id, Role.is_deleted.is_(False)).first()
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    perms = [rp.permission for rp in role.role_permissions if not rp.is_deleted and not rp.permission.is_deleted]
    return RoleWithPermissions(id=role.id, name=role.name, description=role.description, permissions=perms)


@router.put("/roles/{role_id}/permissions", response_model=RoleWithPermissions)
def set_role_permissions(role_id: int, payload: RolePermissionSet, db: Session = Depends(get_db),
                          user=Depends(require_permission("roles", "update"))):
    """Replaces a role's full permission set with the given list of permission ids."""
    management_lock(db, user, "roles", "update")
    before = admin_count(db)
    role = db.query(Role).filter(Role.id == role_id, Role.is_deleted.is_(False)).first()
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")

    if len(set(payload.permission_ids)) != len(payload.permission_ids):
        raise HTTPException(422, "Duplicate permission IDs")
    valid = {p.id: p for p in db.query(Permission).filter(Permission.id.in_(payload.permission_ids), Permission.is_deleted.is_(False))}
    if set(valid) != set(payload.permission_ids):
        raise HTTPException(422, "Unknown or deleted permission")
    held = permission_pairs(db, user)
    current = role_pairs(db, role_id)
    selected = {(p.resource, p.action) for p in valid.values()}
    db.info.setdefault("audit_suppress", set()).add("role_permissions")
    before_ids = sorted(rp.permission_id for rp in db.query(RolePermission).filter(RolePermission.role_id == role_id, RolePermission.is_deleted.is_(False)))
    if not (selected - current) <= held:
        raise HTTPException(403, "Cannot grant permissions you do not hold")
    if not (current - selected) <= held:
        raise HTTPException(403, "Cannot revoke permissions you do not hold")
    for grant in db.query(RolePermission).filter(RolePermission.role_id == role_id):
        grant.is_deleted = True
    for pid in payload.permission_ids:
        existing = db.query(RolePermission).filter(
            RolePermission.role_id == role_id, RolePermission.permission_id == pid
        ).first()
        if existing:
            existing.is_deleted = False
            stamp_update(existing, user.id)
        else:
            rp = RolePermission(role_id=role_id, permission_id=pid)
            stamp_create(rp, user.id)
            db.add(rp)
    if before_ids != sorted(payload.permission_ids):
        audit_record(db, "roles", "permissions_changed", target_type="roles", target_id=role_id,
                     changes={"permission_ids":{"before":before_ids,"after":sorted(payload.permission_ids)}})
    guard_admin_count(db, before)
    db.commit()

    perms = [rp.permission for rp in role.role_permissions if not rp.is_deleted]
    return RoleWithPermissions(id=role.id, name=role.name, description=role.description, permissions=perms)


@router.get("/permissions", response_model=Union[List[PermissionOut], PaginatedResponse[PermissionOut]])
def list_permissions(
    request: Request,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
    resource: str = "",
    q: str = "",
    action: str = "",
    db: Session = Depends(get_db),
    _=Depends(require_permission("roles", "view")),
):
    query = db.query(Permission).filter(Permission.is_deleted.is_(False))

    if resource:
        query = query.filter(Permission.resource == resource)

    if q:
        like = f"%{q}%"
        query = query.filter(Permission.resource.ilike(like) | Permission.action.ilike(like) | Permission.description.ilike(like))
    if action:
        query = query.filter(Permission.action == action)
    query = query.order_by(Permission.resource, Permission.action)

    wants_page = any(key in request.query_params for key in ("page", "page_size", "resource", "q", "action"))
    if not wants_page:
        return query.all()

    items, total, page, page_size, total_pages = paginate_query(query, page, page_size)

    return PaginatedResponse[PermissionOut](
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.post("/permissions", response_model=PermissionOut)
def create_permission(payload: PermissionCreate, db: Session = Depends(get_db),
                       user=Depends(require_permission("permissions", "create"))):
    management_lock(db, user, "permissions", "create")
    before = admin_count(db)
    exists = db.query(Permission).filter(Permission.resource == payload.resource,
                                          Permission.action == payload.action).first()
    if exists and not exists.is_deleted:
        raise HTTPException(status_code=400,
                             detail=f"Permission '{payload.resource}:{payload.action}' already exists")
    if exists and exists.is_deleted:
        exists.is_deleted = False
        exists.description = payload.description
        stamp_update(exists, user.id)
        guard_admin_count(db, before)
        db.commit()
        db.refresh(exists)
        return exists
    perm = Permission(**payload.model_dump())
    stamp_create(perm, user.id)
    db.add(perm)
    guard_admin_count(db, before)
    db.commit()
    db.refresh(perm)
    return perm


@router.put("/permissions/{permission_id}", response_model=PermissionOut)
def update_permission(permission_id: int, payload: PermissionUpdate, db: Session = Depends(get_db),
                       user=Depends(require_permission("permissions", "update"))):
    management_lock(db, user, "permissions", "update")
    before = admin_count(db)
    perm = db.query(Permission).filter(Permission.id == permission_id, Permission.is_deleted.is_(False)).first()
    if not perm:
        raise HTTPException(status_code=404, detail="Permission not found")
    proposed = (payload.resource if payload.resource is not None else perm.resource,
                payload.action if payload.action is not None else perm.action)
    assigned = db.query(RolePermission.id).filter(RolePermission.permission_id == perm.id, RolePermission.is_deleted.is_(False)).first()
    if assigned and proposed != (perm.resource, perm.action) and (
            (perm.resource, perm.action) not in permission_pairs(db, user) or proposed not in permission_pairs(db, user)):
        raise HTTPException(403, "Cannot redefine granted permissions you do not hold")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(perm, field, value)
    stamp_update(perm, user.id)
    guard_admin_count(db, before)
    db.commit()
    db.refresh(perm)
    return perm


@router.delete("/permissions/{permission_id}")
def delete_permission(permission_id: int, db: Session = Depends(get_db),
                       user=Depends(require_permission("permissions", "delete"))):
    management_lock(db, user, "permissions", "delete")
    before = admin_count(db)
    perm = db.query(Permission).filter(Permission.id == permission_id, Permission.is_deleted.is_(False)).first()
    if not perm:
        raise HTTPException(status_code=404, detail="Permission not found")
    assigned = db.query(RolePermission.id).filter(RolePermission.permission_id == perm.id, RolePermission.is_deleted.is_(False)).first()
    if assigned and (perm.resource, perm.action) not in permission_pairs(db, user):
        raise HTTPException(403, "Cannot remove granted permissions you do not hold")
    perm.is_deleted = True
    stamp_update(perm, user.id)
    for grant in db.query(RolePermission).filter(RolePermission.permission_id == permission_id):
        grant.is_deleted = True
    guard_admin_count(db, before)
    db.commit()
    return {"message": "Permission deleted", "permission_id": permission_id}

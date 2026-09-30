from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from app.database import get_db
from app.models.user import User, Role, Permission, RolePermission, NotificationRule
from app.models.mixins import stamp_create, stamp_update
from app.core.pagination import PaginatedResponse, paginate_query, DEFAULT_PAGE_SIZE
from app.schemas.user import (
    UserOut, UserCreate, UserUpdate, RoleOut, RoleCreate, RoleWithPermissions, RolePermissionSet,
    PermissionOut, PermissionCreate, PermissionUpdate, NotificationRuleOut, NotificationRuleCreate,
)
from app.core.security import hash_password
from app.core.deps import get_current_user, require_permission

router = APIRouter(tags=["Users, Roles & Permissions"])


# ---------- Users ----------
@router.get("/users", response_model=PaginatedResponse[UserOut])
def list_users(page: int = 1, page_size: int = DEFAULT_PAGE_SIZE, q: str = "",
               db: Session = Depends(get_db), _=Depends(require_permission("users", "view"))):
    query = db.query(User).filter(User.is_deleted.is_(False))
    if q:
        like = f"%{q}%"
        query = query.filter((User.name.ilike(like)) | (User.email.ilike(like)) | (User.employee_id.ilike(like)))
    query = query.order_by(User.name)
    items, total, page, page_size, total_pages = paginate_query(query, page, page_size)
    return PaginatedResponse(items=items, total=total, page=page, page_size=page_size, total_pages=total_pages)


@router.post("/users", response_model=UserOut)
def create_user(payload: UserCreate, db: Session = Depends(get_db),
                 user=Depends(require_permission("users", "create"))):
    if db.query(User).filter(User.email == payload.email, User.is_deleted.is_(False)).first():
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
    if payload.employee_id:
        duplicate = db.query(User).filter(
            User.employee_id == payload.employee_id,
            User.id != user_id,
            User.is_deleted.is_(False),
        ).first()
        if duplicate:
            raise HTTPException(status_code=400, detail="A user with this employee ID already exists")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(u, field, value)
    stamp_update(u, user.id)
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
    u.status = "Inactive"
    u.deactivation_reason = reason
    stamp_update(u, user.id)
    db.commit()
    return {"message": "User deactivated", "user_id": user_id}


@router.delete("/users")
def deactivate_all_users(reason: str = "Deactivated by admin",
                         db: Session = Depends(get_db), user=Depends(require_permission("users", "delete"))):
    users = db.query(User).filter(User.is_deleted.is_(False), User.id != user.id, User.status != "Inactive").all()
    for u in users:
        u.status = "Inactive"
        u.deactivation_reason = reason
        stamp_update(u, user.id)
    db.commit()
    return {"message": "Users deactivated", "users_deactivated": len(users)}


# ---------- Roles ----------
@router.get("/roles", response_model=PaginatedResponse[RoleOut])
def list_roles(
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
    q: str = "",
    db: Session = Depends(get_db),
    _=Depends(require_permission("roles", "view")),
):
    query = db.query(Role).filter(Role.is_deleted.is_(False))

    if q:
        query = query.filter(Role.name.ilike(f"%{q}%"))

    query = query.order_by(Role.name)

    items, total, page, page_size, total_pages = paginate_query(
        query, page, page_size
    )

    return PaginatedResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.post("/roles", response_model=RoleOut)
def create_role(payload: RoleCreate, db: Session = Depends(get_db),
                 user=Depends(require_permission("roles", "create"))):
    if db.query(Role).filter(Role.name == payload.name, Role.is_deleted.is_(False)).first():
        raise HTTPException(status_code=400, detail=f"A role named '{payload.name}' already exists")
    role = Role(**payload.model_dump())
    stamp_create(role, user.id)
    db.add(role)
    db.commit()
    db.refresh(role)
    return role


@router.put("/roles/{role_id}", response_model=RoleOut)
def update_role(role_id: int, payload: RoleCreate, db: Session = Depends(get_db),
                 user=Depends(require_permission("roles", "update"))):
    role = db.query(Role).filter(Role.id == role_id, Role.is_deleted.is_(False)).first()
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    for field, value in payload.model_dump().items():
        setattr(role, field, value)
    stamp_update(role, user.id)
    db.commit()
    db.refresh(role)
    return role


@router.delete("/roles/{role_id}")
def delete_role(role_id: int, db: Session = Depends(get_db),
                 user=Depends(require_permission("roles", "delete"))):
    """Soft-deletes the role, soft-deletes its role_permissions, and un-assigns any
    user still on this role (role_id -> NULL) so they immediately lose every
    permission the role granted rather than silently keeping access to a 'deleted' role."""
    role = db.query(Role).filter(Role.id == role_id, Role.is_deleted.is_(False)).first()
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    role.is_deleted = True
    stamp_update(role, user.id)
    db.query(RolePermission).filter(RolePermission.role_id == role_id).update({"is_deleted": True})
    affected = db.query(User).filter(User.role_id == role_id, User.is_deleted.is_(False)).all()
    for u in affected:
        u.role_id = None
        stamp_update(u, user.id)
    db.commit()
    return {"message": "Role deleted", "role_id": role_id, "users_unassigned": len(affected)}


@router.delete("/roles")
def delete_all_roles(db: Session = Depends(get_db), user=Depends(require_permission("roles", "delete"))):
    roles = db.query(Role).filter(Role.is_deleted.is_(False)).all()
    role_ids = [r.id for r in roles]
    for role in roles:
        role.is_deleted = True
        stamp_update(role, user.id)
    if role_ids:
        db.query(RolePermission).filter(RolePermission.role_id.in_(role_ids)).update({"is_deleted": True}, synchronize_session=False)
        affected = db.query(User).filter(User.role_id.in_(role_ids), User.is_deleted.is_(False)).all()
        for u in affected:
            u.role_id = None
            stamp_update(u, user.id)
    else:
        affected = []
    db.commit()
    return {"message": "Roles deleted", "roles_deleted": len(roles), "users_unassigned": len(affected)}


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
    role = db.query(Role).filter(Role.id == role_id, Role.is_deleted.is_(False)).first()
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")

    db.query(RolePermission).filter(RolePermission.role_id == role_id).update({"is_deleted": True})
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
    db.commit()

    perms = [rp.permission for rp in role.role_permissions if not rp.is_deleted]
    return RoleWithPermissions(id=role.id, name=role.name, description=role.description, permissions=perms)


# ---------- Permissions catalogue (dynamic — creatable/editable/deletable from the UI) ----------
@router.get("/permissions", response_model=PaginatedResponse[PermissionOut])
def list_permissions(
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
    resource: str = "",
    db: Session = Depends(get_db),
    _=Depends(require_permission("roles", "view")),
):
    query = db.query(Permission).filter(Permission.is_deleted.is_(False))

    if resource:
        query = query.filter(Permission.resource == resource)

    query = query.order_by(Permission.resource, Permission.action)

    items, total, page, page_size, total_pages = paginate_query(
        query, page, page_size
    )

    return PaginatedResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.post("/permissions", response_model=PermissionOut)
def create_permission(payload: PermissionCreate, db: Session = Depends(get_db),
                       user=Depends(require_permission("roles", "create"))):
    exists = db.query(Permission).filter(Permission.resource == payload.resource,
                                          Permission.action == payload.action).first()
    if exists and not exists.is_deleted:
        raise HTTPException(status_code=400,
                             detail=f"Permission '{payload.resource}:{payload.action}' already exists")
    if exists and exists.is_deleted:
        exists.is_deleted = False
        exists.description = payload.description
        stamp_update(exists, user.id)
        db.commit()
        db.refresh(exists)
        return exists
    perm = Permission(**payload.model_dump())
    stamp_create(perm, user.id)
    db.add(perm)
    db.commit()
    db.refresh(perm)
    return perm


@router.put("/permissions/{permission_id}", response_model=PermissionOut)
def update_permission(permission_id: int, payload: PermissionUpdate, db: Session = Depends(get_db),
                       user=Depends(require_permission("roles", "update"))):
    perm = db.query(Permission).filter(Permission.id == permission_id, Permission.is_deleted.is_(False)).first()
    if not perm:
        raise HTTPException(status_code=404, detail="Permission not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(perm, field, value)
    stamp_update(perm, user.id)
    db.commit()
    db.refresh(perm)
    return perm


@router.delete("/permissions/{permission_id}")
def delete_permission(permission_id: int, db: Session = Depends(get_db),
                       user=Depends(require_permission("roles", "delete"))):
    perm = db.query(Permission).filter(Permission.id == permission_id, Permission.is_deleted.is_(False)).first()
    if not perm:
        raise HTTPException(status_code=404, detail="Permission not found")
    perm.is_deleted = True
    stamp_update(perm, user.id)
    db.query(RolePermission).filter(RolePermission.permission_id == permission_id).update({"is_deleted": True})
    db.commit()
    return {"message": "Permission deleted", "permission_id": permission_id}


@router.delete("/permissions")
def delete_all_permissions(db: Session = Depends(get_db), user=Depends(require_permission("roles", "delete"))):
    permissions = db.query(Permission).filter(Permission.is_deleted.is_(False)).all()
    permission_ids = [p.id for p in permissions]
    for perm in permissions:
        perm.is_deleted = True
        stamp_update(perm, user.id)
    if permission_ids:
        db.query(RolePermission).filter(RolePermission.permission_id.in_(permission_ids)).update(
            {"is_deleted": True}, synchronize_session=False)
    db.commit()
    return {"message": "Permissions deleted", "permissions_deleted": len(permissions)}


# ---------- Notification routing (commodity -> To/CC), replaces 'Duplicate Entry' sheet ----------
@router.get("/notification-rules", response_model=List[NotificationRuleOut])
def list_notification_rules(db: Session = Depends(get_db), _=Depends(require_permission("settings", "view"))):
    return db.query(NotificationRule).filter(NotificationRule.is_deleted.is_(False)).all()


@router.put("/notification-rules", response_model=NotificationRuleOut)
def upsert_notification_rule(payload: NotificationRuleCreate, db: Session = Depends(get_db),
                              user=Depends(require_permission("settings", "update"))):
    rule = db.query(NotificationRule).filter(NotificationRule.commodity == payload.commodity,
                                              NotificationRule.is_deleted.is_(False)).first()
    if rule:
        rule.to_emails = payload.to_emails
        rule.cc_emails = payload.cc_emails
        stamp_update(rule, user.id)
    else:
        rule = NotificationRule(**payload.model_dump())
        stamp_create(rule, user.id)
        db.add(rule)
    db.commit()
    db.refresh(rule)
    return rule

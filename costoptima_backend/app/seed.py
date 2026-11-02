"""
CostOptima seed script — populates:
  - the Permission catalogue (one row per page/model x action)
  - 4 roles (Super Admin, Admin, HOD, Masters Update) with a sensible default
    permission matrix assigned via RolePermission
  - one starter user per role, for testing session login as each role
  - the Masters workbook types/tabs/columns (Column Position, Sheet Name,
    Shift Factor Rate, Master Output Matrix, Sand Columns, SBC Format, plus
    Authority Matrix retained but unlinked from the sidebar)
  - the commodity types + their Master User/PCE/Self workbook files
  - the commodity config engine's starter input field rows

Run with:  python -m app.seed
"""
from pathlib import Path
import sys

from app.database import Base, engine, SessionLocal
from app.models import Role, Permission, RolePermission, User
# from app.models import WorkbookType, WorkbookTab, WorkbookTabColumn
# from app import import_support_workbook
# from app.models.commodity import CommodityType, FieldDefinition
from app.models import stamp_create
from app.security import hash_password

Base.metadata.create_all(bind=engine)
db = SessionLocal()

# ---------------------------------------------------------------------------
# Permission catalogue — one row per (resource, action). 'resource' covers both
# pages (boms, masters, commodity_config, converters, settings,
# users, roles) and, within the Workbook Engine, the underlying model.
# ---------------------------------------------------------------------------
RESOURCES = ["boms", "masters", "commodity_config", "converters", "settings", "users", "roles",
             "permissions", "workbook_config", "merge", "calculation_config"]
ACTIONS = ["create", "view", "update", "delete"]

# Update a permission catalogue/grants without running business-data initialization.
# Example: python -m app.seed --permissions-only calculation_config
permission_scope = None
if "--permissions-only" in sys.argv:
    index = sys.argv.index("--permissions-only")
    permission_scope = sys.argv[index + 1] if len(sys.argv) > index + 1 else ""
    if permission_scope not in RESOURCES:
        raise SystemExit("Specify a known resource after --permissions-only")
    RESOURCES = [permission_scope]

permission_by_key = {}
for resource in RESOURCES:
    for action in ACTIONS:
        perm = db.query(Permission).filter(Permission.resource == resource, Permission.action == action).first()
        if not perm:
            perm = Permission(resource=resource, action=action,
                               description=f"Can {action} {resource.replace('_', ' ')}")
            db.add(perm)
            db.commit()
            db.refresh(perm)
        permission_by_key[(resource, action)] = perm

# ---------------------------------------------------------------------------
# Roles + their default permission matrix
# ---------------------------------------------------------------------------
ROLE_MATRIX = {
    "Super Admin": {r: ACTIONS for r in RESOURCES},  # full access everywhere
    "Admin": {
        "boms": ACTIONS, "masters": ACTIONS, "commodity_config": ACTIONS,
        "converters": ["view"], "settings": ["view", "update"],
        "users": ["view", "create", "update"], "roles": ["view"],
        "permissions": ["view"], "workbook_config": ACTIONS,
        "merge": ["view", "create"],
        "calculation_config": ACTIONS,
    },
    "HOD": {
        "boms": ["view", "create"], "masters": ["view"], "commodity_config": ["view"],
        "converters": ["view"], "settings": ["view"],
        "users": ["view"], "roles": ["view"], "permissions": ["view"], "workbook_config": ["view"],
        "merge": ["view"],

    },
    "Masters Update": {
        "boms": ["view"], "masters": ["create", "view", "update"],
        "commodity_config": ["create", "view", "update"],
        "converters": ["view"], "settings": ["view"],
        "workbook_config": ["view"],
    },
}

role_by_name = {}
for role_name, matrix in ROLE_MATRIX.items():
    if permission_scope and permission_scope not in matrix:
        continue
    role = db.query(Role).filter(Role.name == role_name).first()
    if permission_scope and (not role or role.is_deleted):
        continue
    if not role:
        role = Role(name=role_name, description=f"{role_name} role")
        db.add(role)
        db.commit()
        db.refresh(role)
    role_by_name[role_name] = role

    for resource, actions in matrix.items():
        if permission_scope and resource != permission_scope:
            continue
        for action in actions:
            perm = permission_by_key[(resource, action)]
            exists = db.query(RolePermission).filter(
                RolePermission.role_id == role.id, RolePermission.permission_id == perm.id
            ).first()
            if not exists:
                rp = RolePermission(role_id=role.id, permission_id=perm.id)
                db.add(rp)
    db.commit()

if permission_scope:
    db.close()
    print(f"Permissions initialized: {permission_scope}; no business data seeded.")
    raise SystemExit(0)
# ---------------------------------------------------------------------------
# One starter user per role (session-login credentials for testing each role)
# ---------------------------------------------------------------------------
STARTER_USERS = [
    ("Super Admin User", "superadmin@costoptima.com", "superadmin123", "Super Admin", "Platform Owner"),
    ("Admin User", "admin@costoptima.com", "admin123", "Admin", "Platform Admin"),
    ("HOD User", "hod@costoptima.com", "hod123", "HOD", "Head of Department"),
    ("Masters Update User", "masters@costoptima.com", "masters123", "Masters Update", "Master Data Analyst"),
]
for name, email, pwd, role_name, title in STARTER_USERS:
    if not db.query(User).filter(User.email == email).first():
        u = User(name=name, email=email, hashed_password=hash_password(pwd),
                 job_title=title, department="Operations", role_id=role_by_name[role_name].id, status="Active")
        db.add(u)
        db.commit()
        db.refresh(u)
        stamp_create(u, u.id)  # seeded users are their own creator for audit purposes
        db.commit()
        print(f"Seeded {role_name} user: {email} / {pwd}")


# Excel layout/format metadata recovered from the legacy master workbooks. These are
# configuration values, not business logic: changing a workbook layout only changes
# this table (or the equivalent /config/app endpoint).

"""
Shared pytest fixtures for the CostOptima API test suite.
Uses an isolated on-disk SQLite test database so tests run fast and never touch
your real MySQL database. Session-cookie auth is exercised via TestClient's
built-in cookie jar (no manual Authorization header needed).
"""
import os
import sys

TEST_DB_PATH = os.path.join(os.path.dirname(__file__), "costoptima_test.db")
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB_PATH}"
os.environ["SECRET_KEY"] = "test-secret-key"
os.environ["UPLOAD_DIR"] = os.path.join(os.path.dirname(__file__), "test_uploads")
os.environ["FRONTEND_ORIGIN"] = "http://testserver"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient

from app.database import Base, engine, SessionLocal
from app.main import app
from app.models.user import Role, Permission, RolePermission, User
from app.models.workbook import WorkbookType, WorkbookTab, WorkbookTabColumn
from app.models.commodity import CommodityType, FieldDefinition, CalcRule
from app.core.security import hash_password

RESOURCES = ["calculations", "masters", "commodity_config", "outputs", "history", "settings", "users", "roles", "workbook_config", "merge"]
ACTIONS = ["create", "view", "update", "delete"]


@pytest.fixture(scope="session", autouse=True)
def _setup_test_database():
    if os.path.exists(TEST_DB_PATH):
        os.remove(TEST_DB_PATH)
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()

    perm_by_key = {}
    for resource in RESOURCES:
        for action in ACTIONS:
            p = Permission(resource=resource, action=action)
            db.add(p)
    db.commit()
    for p in db.query(Permission).all():
        perm_by_key[(p.resource, p.action)] = p

    matrix = {
        "Admin": {r: ACTIONS for r in RESOURCES},
        "Editor": {"masters": ["create", "view", "update", "delete"], "commodity_config": ["create", "view", "update", "delete"],
                   "calculations": ["view"], "outputs": ["view"], "history": ["view"], "settings": ["view"]},
        "Engineer": {"calculations": ACTIONS, "masters": ["view"], "outputs": ["view"], "history": ["view"], "settings": ["view"]},
        "Viewer": {"masters": ["view"], "calculations": ["view"], "outputs": ["view"], "history": ["view"],
                   "settings": ["view"], "users": ["view"], "roles": ["view"], "commodity_config": ["view"]},
    }
    roles = {}
    for role_name, perms in matrix.items():
        role = Role(name=role_name)
        db.add(role)
        db.commit()
        db.refresh(role)
        roles[role_name] = role
        for resource, actions in perms.items():
            for action in actions:
                db.add(RolePermission(role_id=role.id, permission_id=perm_by_key[(resource, action)].id))
        db.commit()

    users = [
        ("Admin User", "admin@costoptima.com", "admin123", roles["Admin"].id),
        ("Editor User", "editor@costoptima.com", "editor123", roles["Editor"].id),
        ("Engineer User", "engineer@costoptima.com", "engineer123", roles["Engineer"].id),
        ("Viewer User", "viewer@costoptima.com", "viewer123", roles["Viewer"].id),
    ]
    for name, email, pwd, role_id in users:
        db.add(User(name=name, email=email, hashed_password=hash_password(pwd), role_id=role_id, status="Active"))
    db.commit()

    wt = WorkbookType(code="test_masters", name="Test Masters", description="Seeded for tests")
    db.add(wt)
    db.commit()
    db.refresh(wt)

    tab = WorkbookTab(workbook_type_id=wt.id, tab_name="Master Header")
    db.add(tab)
    db.commit()
    db.refresh(tab)

    for i, (name, dtype, uniq, req) in enumerate([
        ("Colname", "string", False, True), ("Header", "string", False, True),
        ("Unique", "string", False, False), ("SheetName", "string", True, True),
        ("Position", "number", False, False),
    ]):
        db.add(WorkbookTabColumn(tab_id=tab.id, column_name=name, data_type=dtype, is_unique_key=uniq,
                                  is_required=req, position=i))
    db.commit()

    ro_tab = WorkbookTab(workbook_type_id=wt.id, tab_name="Logbook", read_only=True)
    db.add(ro_tab)
    db.commit()
    db.refresh(ro_tab)
    commodity = CommodityType(code="test_sheet_metal", name="Test Sheet Metal")
    db.add(commodity)
    db.commit()
    db.refresh(commodity)
    db.add(FieldDefinition(commodity_type_id=commodity.id, field_name="Rate/UOM", data_type="number",
                            source_type="master_lookup", position=0))
    db.add(CalcRule(commodity_type_id=commodity.id, formula_expr="shift_rate / pc_per_shift",
                     output_field="Molding Cost", priority=1))
    db.commit()
    db.close()

    yield

    engine.dispose()
    if os.path.exists(TEST_DB_PATH):
        os.remove(TEST_DB_PATH)


@pytest.fixture()
def db_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture()
def client():
    """Function-scoped so each test starts with a clean cookie jar (no leaked session)."""
    with TestClient(app) as c:
        yield c


def _new_logged_in_client(email, password):
    c = TestClient(app)
    r = c.post("/auth/login", data={"username": email, "password": password})
    assert r.status_code == 200, r.text
    return c


@pytest.fixture()
def admin_client():
    """Each role fixture gets its OWN TestClient/cookie jar — critical when a single
    test needs two different roles at once (e.g. asserting one role is denied while
    another is allowed); sharing one client would let the second login's session
    cookie silently overwrite the first's."""
    return _new_logged_in_client("admin@costoptima.com", "admin123")


@pytest.fixture()
def editor_client():
    return _new_logged_in_client("editor@costoptima.com", "editor123")


@pytest.fixture()
def engineer_client():
    return _new_logged_in_client("engineer@costoptima.com", "engineer123")


@pytest.fixture()
def viewer_client():
    return _new_logged_in_client("viewer@costoptima.com", "viewer123")


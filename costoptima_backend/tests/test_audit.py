"""Isolated in-memory verification only: never connects to the shared MySQL DB."""
import json
from datetime import datetime, timedelta
from uuid import uuid4
import pytest
from sqlalchemy import create_engine, select, event, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient
from app import database, audit
from app.models import Base, AuditEvent, User, Role, Permission, RolePermission, LoginSession, AppConfig
from app.config import get_settings
from app.main import app
from app.security import hash_password, COOKIE_NAME


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    engine = create_engine("sqlite://", connect_args={"check_same_thread":False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)  # Only isolated memory tables, never production DDL.
    factory = sessionmaker(bind=engine, class_=database.ApplicationSession, expire_on_commit=False)
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "SessionLocal", factory)
    settings = get_settings()
    monkeypatch.setattr(settings, "audit_signing_key", "a"*64)
    monkeypatch.setattr(settings, "audit_log_dir", str(tmp_path))
    with TestClient(app) as client:
        yield engine, factory, client
    engine.dispose()


def rows(engine):
    with engine.connect() as c:
        return [dict(r) for r in c.execute(select(AuditEvent.__table__).order_by(AuditEvent.id)).mappings()]


def user(db, name="Fixture"):
    item = User(name=name, email=uuid4().hex+"@example.com", status="Active", hashed_password=hash_password("isolated test password only"))
    db.add(item); db.commit()
    return item


def test_atomic_changes_redaction_and_integrity(isolated):
    engine, factory, client = isolated
    with audit.activity_context(actor_user_id=101,session_ref="b"*64,request_id=str(uuid4())):
        with factory() as db:
            u = user(db)
            u.department = "Quality";u.hashed_password="must-not-appear";db.commit()
            config = AppConfig(group="GENERAL",key="FIXTURE",type="secret",is_secret=True,is_active=True,value="never-log-this-secret",content=None)
            db.add(config);db.commit();config.value="second-secret";db.commit()
    records=rows(engine)
    assert len(records)==4
    assert {r["session_ref"] for r in records}=={"b"*64}
    assert records[1]["changes"]["department"]=={"before":None,"after":"Quality"}
    assert records[1]["changes"]["hashed_password"]=={"changed":True}
    serialized=json.dumps(records,default=str)
    assert "must-not-appear" not in serialized and "never-log-this-secret" not in serialized and "second-secret" not in serialized
    assert all(audit.verify_integrity(r)=="valid" for r in records)
    records[0]["summary"]="tampered"
    assert audit.verify_integrity(records[0])=="invalid"
    with factory() as db:
        u=db.get(User,1);u.department="rollback";db.flush();db.rollback()
    assert len(rows(engine))==4
    with factory() as db:assert db.get(User,1).department=="Quality"


def test_audit_failure_rolls_back_business_change(isolated):
    engine, factory, client=isolated
    with factory() as db:u=user(db)
    def fail(conn,cursor,statement,parameters,context,many):
        if statement.startswith("INSERT INTO audit_events"):raise RuntimeError("isolated audit failure")
    event.listen(engine,"before_cursor_execute",fail)
    try:
        with factory() as db:
            db.get(User,1).name="should rollback"
            with pytest.raises(RuntimeError):db.commit()
            db.rollback()
    finally:event.remove(engine,"before_cursor_execute",fail)
    with factory() as db:assert db.get(User,1).name=="Fixture"
    assert len(rows(engine))==1


def setup_admin(factory):
    with factory() as db:
        role=Role(name="Isolated admin");db.add(role);db.flush()
        for resource in ["users","roles","permissions","settings"]:
            for action in ["view","create","update","delete"]:
                p=Permission(resource=resource,action=action);db.add(p);db.flush()
                db.add(RolePermission(role_id=role.id,permission_id=p.id))
        u=user(db);u.role_id=role.id;db.commit()
        return u.id,u.email,role.id


def login(client,email):
    headers={"Origin":next(iter(get_settings().origins)),"X-CostOptima-Request":"1"}
    response=client.post("/api/auth/login",headers=headers,data={"username":email,"password":"isolated test password only"})
    assert response.status_code==200,response.text
    headers["X-CSRF-Token"]=response.json()["csrf_token"]
    return headers


def test_current_modules_session_correlation_and_api(isolated):
    engine,factory,client=isolated
    uid,email,rid=setup_admin(factory)
    headers=login(client,email)
    assert client.get('/api/auth/me').status_code==200
    r=client.post('/api/users',headers=headers,json={"name":"New fixture","email":"new.fixture@example.com","password":"isolated created password"})
    assert r.status_code==200,r.text
    target=r.json()['id']
    assert client.put(f'/api/users/{target}',headers=headers,json={"department":"Quality","role_id":rid}).status_code==200
    r=client.post('/api/roles',headers=headers,json={"name":"Second fixture role","description":"Before"});assert r.status_code==200,r.text
    other_role=r.json()['id']
    assert client.put(f'/api/roles/{other_role}',headers=headers,json={"name":"Second fixture role","description":"After"}).status_code==200
    with factory() as db:pids=list(db.scalars(select(Permission.id)))
    assert client.put(f'/api/roles/{other_role}/permissions',headers=headers,json={"permission_ids":pids[:2]}).status_code==200
    r=client.post('/api/permissions',headers=headers,json={"resource":"isolated_fixture","action":"view"});assert r.status_code==200,r.text
    pid=r.json()['id']
    assert client.put(f'/api/permissions/{pid}',headers=headers,json={"description":"Updated fixture"}).status_code==200
    assert client.delete(f'/api/permissions/{pid}',headers=headers).status_code==200
    r=client.post('/api/app-config',headers=headers,json={"group":"GENERAL","key":"ISOLATED_FIXTURE","type":"string","value":"safe-value"});assert r.status_code==200,r.text
    cid=r.json()['id']
    assert client.put(f'/api/app-config/{cid}',headers=headers,json={"value":"new-value"}).status_code==200
    assert client.post(f'/api/app-config/{cid}/toggle',headers=headers,json={}).status_code==200
    assert client.delete(f'/api/app-config/{cid}',headers=headers).status_code==200
    assert client.put(f'/api/users/{target}',headers=headers,json={"status":"Inactive","deactivation_reason":"Fixture only"}).status_code==200
    assert client.put(f'/api/users/{target}',headers=headers,json={"status":"Active"}).status_code==200
    r=client.get('/api/reports/audit-log?page_size=2');assert r.status_code==200,r.text
    listing=r.json();assert len(listing['items'])==2 and listing['total']>2
    assert client.get('/api/reports/audit-log/options').status_code==200
    detail=client.get('/api/reports/audit-log/'+listing['items'][0]['event_id']);assert detail.status_code==200 and detail.json()['integrity']=='valid'
    session=listing['items'][0]['session_ref']
    assert session
    assert client.get('/api/reports/audit-log',params={'session_ref':session}).json()['total']>=10
    assert client.get('/api/reports/audit-log',params={'q':'fixture'}).status_code==422
    assert client.post('/api/auth/logout',headers=headers).status_code==200
    assert client.get('/api/auth/me').status_code==401
    r=client.post('/api/auth/login',headers=headers,data={'username':email,'password':'wrong password'})
    assert r.status_code==401
    history=rows(engine)
    names=[(r['resource'],r['action']) for r in history]
    for pair in [('auth','login'),('auth','logout'),('sessions','created'),('sessions','revoked'),('users','activated'),('users','deactivated'),('roles','permissions_changed'),('app_config','disabled')]:assert pair in names,pair
    assert names.count(('roles','permissions_changed'))==1
    assert len({r['event_id'] for r in history})==len(history)
    assert any(r['outcome']=='failure' or r['outcome']=='denied' for r in history)
    assert all(audit.verify_integrity(r)=='valid' for r in history)
    assert not any(client.cookies.get(COOKIE_NAME,'[none]') in json.dumps(r,default=str) for r in history)


def test_expiration_forbidden_and_request_trace(isolated):
    engine,factory,client=isolated
    with factory() as db:u=user(db);email=u.email
    headers=login(client,email)
    assert client.get('/api/reports/audit-log').status_code==403
    token=client.cookies.get(COOKIE_NAME)
    with factory() as db:
        s=db.get(LoginSession,token);s.expires_at=datetime.utcnow()-timedelta(seconds=1);db.commit()
    response=client.get('/api/auth/me');assert response.status_code==401 and response.headers.get('x-request-id')
    assert any(r['action']=='expiration_observed' for r in rows(engine))
    assert client.post('/api/users',json={}).status_code==403
    assert any(r['route']=='/api/users' and r['outcome']=='denied' for r in rows(engine))
    import logging
    handler=logging.getLogger('costoptima.requests').handlers[0]
    log=open(handler.baseFilename,encoding='utf-8').read()
    assert 'http.request' in log and 'duration_ms' in log and token not in log


def test_append_only_and_independent_failure(isolated,monkeypatch):
    engine,factory,client=isolated
    with factory() as db:user(db)
    with factory() as db:
        e=db.query(AuditEvent).first();e.summary='forbidden'
        with pytest.raises(RuntimeError):db.commit()
        db.rollback()
    captured=[]
    monkeypatch.setattr(audit,'fallback',lambda row:captured.append(row))
    def fail(*args):raise RuntimeError('storage offline')
    monkeypatch.setattr(engine,'begin',fail)
    assert audit.record_independent('auth','login',outcome='failure') is False
    assert len(captured)==1



def test_bulk_guard_and_noop_permissions(isolated):
    engine,factory,client=isolated
    uid,email,rid=setup_admin(factory)
    headers=login(client,email)
    with factory() as db:
        pids=list(db.scalars(select(Permission.id)))
        with pytest.raises(RuntimeError):db.query(User).update({"name":"bypass"})
        with pytest.raises(RuntimeError):db.execute(text("DELETE FROM audit_events"))
        db.rollback()
    before=len(rows(engine))
    assert client.put(f'/api/roles/{rid}/permissions',headers=headers,json={"permission_ids":pids}).status_code==200
    assert len(rows(engine))==before
    # CSRF failure does not mutate the target or produce a success event.
    response=client.put(f'/api/users/{uid}',headers={"Origin":next(iter(get_settings().origins)),"X-CostOptima-Request":"1"},json={"department":"Forbidden"})
    assert response.status_code==403
    with factory() as db:assert db.get(User,uid).department is None


def test_log_rotation_configuration(isolated):
    import logging
    handler=logging.getLogger('costoptima.requests').handlers[0]
    assert handler.maxBytes==20*1024*1024 and handler.backupCount==10



def test_logout_survives_audit_outage(isolated,monkeypatch):
    engine,factory,client=isolated
    with factory() as db:u=user(db);email=u.email
    headers=login(client,email)
    token=client.cookies.get(COOKIE_NAME)
    captured=[]
    monkeypatch.setattr(audit,'fallback',lambda row:captured.append(row))
    def fail(conn,cursor,statement,parameters,context,many):
        if statement.startswith("INSERT INTO audit_events"):raise RuntimeError("isolated audit outage")
    event.listen(engine,'before_cursor_execute',fail)
    try:assert client.post('/api/auth/logout',headers=headers).status_code==200
    finally:event.remove(engine,'before_cursor_execute',fail)
    with factory() as db:assert db.get(LoginSession,token).is_deleted
    assert captured and captured[0]['action']=='logout'


def test_cursors_errors_and_nonlive_sso_event(isolated,monkeypatch):
    engine,factory,client=isolated
    uid,email,rid=setup_admin(factory)
    headers=login(client,email)
    first=client.get('/api/reports/audit-log',params={'page_size':2}).json()
    cursor=first['next_cursor']
    second=client.get('/api/reports/audit-log',params={'page_size':2,'anchor':first['anchor'],**cursor}).json()
    assert not ({e['event_id'] for e in first['items']} & {e['event_id'] for e in second['items']})
    assert client.get('/api/reports/audit-log',params={'outcome':'invalid'}).status_code==422
    assert client.get('/api/reports/audit-log',params={'date_from':'2030-01-01','date_to':'2020-01-01'}).status_code==422
    assert client.get('/health').json()=={'status':'ok'}
    # Only an isolated configuration-error branch; never contacts the provider.
    from app.api import auth
    monkeypatch.setattr(auth,'_sso_readiness',lambda:(False,'Isolated configuration failure'))
    response=client.get('/api/auth/sso/login',follow_redirects=False)
    assert response.status_code==303
    assert any(r['action']=='sso.login' and r['outcome']=='failure' for r in rows(engine))

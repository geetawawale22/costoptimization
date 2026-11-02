"""Authentication/CSRF/guard/audit tests use only isolated Step6 fixtures."""
from io import BytesIO
import openpyxl
from test_masters import isolated,login
from app.config import get_settings
from app.models import Permission,RolePermission,User,AuditEvent
from app.api import calculations


def package_input():
    w=openpyxl.Workbook();s=w.active;s.title='BOM'
    s.append(['0 / 1 / 2 / 3','V','Supplier','End Item','Customer No','Type','Commodity','Qty/Veh'])
    s.append([1,1,'fixture','Root','001','PKG',None,1]);b=BytesIO();w.save(b);return b.getvalue()


def confirmed(client):
    response=client.get('/api/calculations/development/snapshot')
    assert response.status_code==200,response.text
    return {'confirmation':'development-only','snapshot_token':response.json()['confirmation_token']}


def test_backend_permission_and_default_guard(isolated,monkeypatch):
    _,factory,client=isolated
    assert client.get('/api/calculations/development/capabilities').status_code==401
    headers=login(client)
    assert client.get('/api/calculations/development/capabilities').status_code==403
    with factory() as db:
        user=db.query(User).first()
        for action in ['view','create']:
            p=Permission(resource='boms',action=action);db.add(p);db.flush();db.add(RolePermission(role_id=user.role_id,permission_id=p.id))
        db.commit()
    monkeypatch.setattr(get_settings(),'calculation_development_enabled',False)
    assert client.get('/api/calculations/development/capabilities').json()['production_enabled'] is False
    response=client.post('/api/calculations/development/runs',headers=headers,files={'file':('input.xlsx',package_input())})
    assert response.status_code==409


def test_development_input_output_csrf_and_central_audit(isolated,monkeypatch,tmp_path):
    _,factory,client=isolated;headers=login(client)
    with factory() as db:
        user=db.query(User).first()
        for action in ['view','create']:
            p=Permission(resource='boms',action=action);db.add(p);db.flush();db.add(RolePermission(role_id=user.role_id,permission_id=p.id))
        db.commit()
    monkeypatch.setattr(get_settings(),'calculation_development_enabled',True)
    monkeypatch.setattr(calculations,'ROOT',tmp_path/'runs')
    bad={k:v for k,v in headers.items() if k!='X-CSRF-Token'}
    assert client.post('/api/calculations/development/runs',headers=bad,files={'file':('input.xlsx',package_input())},data=confirmed(client)).status_code==403
    response=client.post('/api/calculations/development/runs',headers=headers,files={'file':('input.xlsx',package_input())},data=confirmed(client))
    assert response.status_code==200,response.text
    result=response.json();assert result['production_costing_enabled'] is False
    assert result['rows'][0]['status']=='success'
    assert result['complete'] is False
    data=client.get(result['download_url']);assert data.status_code==200
    w=openpyxl.load_workbook(BytesIO(data.content));assert 'Diagnostics' in w.sheetnames
    with factory() as db:
        events=db.query(AuditEvent).filter(AuditEvent.resource=='calculations').order_by(AuditEvent.occurred_at,AuditEvent.id).all()
        assert [e.action for e in events]==['execution.started','execution.completed','export.generated']
        assert len({e.session_ref for e in events})==1


def test_malformed_input_is_audited_without_output(isolated,monkeypatch,tmp_path):
    _,factory,client=isolated;headers=login(client)
    with factory() as db:
        user=db.query(User).first();permission=Permission(resource='boms',action='create')
        db.add(permission);db.flush();db.add(RolePermission(role_id=user.role_id,permission_id=permission.id));db.commit()
    monkeypatch.setattr(get_settings(),'calculation_development_enabled',True)
    monkeypatch.setattr(calculations,'ROOT',tmp_path/'runs')
    response=client.post('/api/calculations/development/runs',headers=headers,files={'file':('bad.xlsx',b'not a workbook')},data=confirmed(client))
    assert response.status_code==422
    assert not (tmp_path/'runs').exists()
    with factory() as db:
        events=db.query(AuditEvent).filter(AuditEvent.resource=='calculations').order_by(AuditEvent.occurred_at,AuditEvent.id).all()
        assert [event.action for event in events]==['execution.started','execution.failed']
        assert events[-1].outcome=='failure'
        assert len({event.session_ref for event in events})==1


def test_calculation_does_not_run_if_start_audit_cannot_persist(isolated,monkeypatch,tmp_path):
    _,factory,client=isolated;headers=login(client)
    with factory() as db:
        user=db.query(User).first();permission=Permission(resource='boms',action='create')
        db.add(permission);db.flush();db.add(RolePermission(role_id=user.role_id,permission_id=permission.id));db.commit()
    monkeypatch.setattr(get_settings(),'calculation_development_enabled',True)
    monkeypatch.setattr(calculations,'ROOT',tmp_path/'runs')
    monkeypatch.setattr(calculations,'record_independent',lambda *args,**kwargs:False)
    response=client.post('/api/calculations/development/runs',headers=headers,files={'file':('input.xlsx',package_input())},data=confirmed(client))
    assert response.status_code==503
    assert not (tmp_path/'runs').exists()


def authorize(factory):
    with factory() as db:
        user=db.query(User).first()
        for action in ['view','create']:
            permission=Permission(resource='boms',action=action);db.add(permission);db.flush()
            db.add(RolePermission(role_id=user.role_id,permission_id=permission.id))
        db.commit()


def test_snapshot_confirmation_staleness_and_guarded_history(isolated,monkeypatch,tmp_path):
    from sqlalchemy import text
    engine,factory,client=isolated;headers=login(client);authorize(factory)
    monkeypatch.setattr(calculations,'ROOT',tmp_path/'runs')
    monkeypatch.setattr(get_settings(),'calculation_development_enabled',True)
    assert client.post('/api/calculations/development/runs',headers=headers,files={'file':('input.xlsx',package_input())}).status_code==409
    confirmation=confirmed(client)
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO src_fixture__master(row_id,code,rate) VALUES(1,'0007',1.25)"))
    stale=client.post('/api/calculations/development/runs',headers=headers,files={'file':('input.xlsx',package_input())},data=confirmation)
    assert stale.status_code==409 and 'changed' in stale.text
    assert not (tmp_path/'runs').exists()
    response=client.post('/api/calculations/development/runs',headers=headers,files={'file':('input.xlsx',package_input())},data=confirmed(client))
    assert response.status_code==200,response.text
    payload=response.json();rid=payload['metadata']['run_id']
    assert payload['metadata']['master_snapshot']['source_rows']==1
    assert payload['metadata']['verification_status']=='development-unverified'
    history=client.get('/api/calculations/development/runs').json()
    assert history['total']==1 and history['items'][0]['run_id']==rid
    assert history['items'][0]['production_enabled'] is False
    assert client.get(f'/api/calculations/development/runs/{rid}').json()==payload
    preview=client.get(f'/api/calculations/development/runs/{rid}/preview',params={'sheet':'Diagnostics','rows':2,'columns':3})
    assert preview.status_code==200,preview.text
    assert len(preview.json()['rows'])<=2 and len(preview.json()['rows'][0])<=3
    assert preview.json()['formulas_evaluated'] is False
    assert client.get(f'/api/calculations/development/runs/{rid}/preview?rows=101').status_code==422
    assert client.get(f'/api/calculations/development/runs/{rid}/preview?sheet=Missing').status_code==404
    # Published artifact integrity is checked before serving bytes.
    folder=tmp_path/'runs'/'1'/rid
    (folder/'output.xlsx').write_bytes(b'tampered')
    assert client.get(payload['download_url']).status_code==404
    monkeypatch.setattr(get_settings(),'calculation_development_enabled',False)
    for url in ['/snapshot','/runs',f'/runs/{rid}',f'/runs/{rid}/preview',f'/runs/{rid}/workbook']:
        assert client.get('/api/calculations/development'+url).status_code==409


def test_calculation_write_attempt_is_rejected_by_read_only_session(isolated,monkeypatch,tmp_path):
    from sqlalchemy import text
    _,factory,client=isolated;headers=login(client);authorize(factory)
    monkeypatch.setattr(get_settings(),'calculation_development_enabled',True)
    monkeypatch.setattr(calculations,'ROOT',tmp_path/'runs')
    def unsafe_execute(data,master,**kwargs):
        master.db.execute(text('UPDATE master_source SET row_count=900'))
    monkeypatch.setattr(calculations,'execute',unsafe_execute)
    response=client.post('/api/calculations/development/runs',headers=headers,files={'file':('input.xlsx',package_input())},data=confirmed(client))
    assert response.status_code==503
    with factory() as db: assert db.execute(text('SELECT sum(row_count) FROM master_source')).scalar()==0
    assert not (tmp_path/'runs').exists()


def test_completion_audit_failure_cannot_publish_output(isolated,monkeypatch,tmp_path):
    _,factory,client=isolated;headers=login(client);authorize(factory)
    monkeypatch.setattr(get_settings(),'calculation_development_enabled',True)
    monkeypatch.setattr(calculations,'ROOT',tmp_path/'runs')
    def fail(*args,**kwargs): raise RuntimeError('isolated completion audit failure')
    monkeypatch.setattr(calculations,'record',fail)
    response=client.post('/api/calculations/development/runs',headers=headers,files={'file':('input.xlsx',package_input())},data=confirmed(client))
    assert response.status_code==503
    assert client.get('/api/calculations/development/runs').json()['total']==0
    assert list((tmp_path/'runs'/'1').iterdir())==[]


def test_other_accounts_cannot_read_artifacts_or_reuse_snapshot(isolated,monkeypatch,tmp_path):
    from app.models import Role
    from app.security import hash_password
    _,factory,client=isolated;headers=login(client);authorize(factory)
    monkeypatch.setattr(get_settings(),'calculation_development_enabled',True)
    monkeypatch.setattr(calculations,'ROOT',tmp_path/'runs')
    proof=confirmed(client)
    response=client.post('/api/calculations/development/runs',headers=headers,files={'file':('input.xlsx',package_input())},data=proof)
    assert response.status_code==200,response.text
    rid=response.json()['metadata']['run_id']
    with factory() as db:
        owner=db.query(User).first()
        db.add(User(name='Other fixture',email='other.fixture@example.com',hashed_password=hash_password('isolated other password'),role_id=owner.role_id,status='Active'));db.commit()
    client.post('/api/auth/logout',headers=headers)
    new=client.post('/api/auth/login',headers={k:v for k,v in headers.items() if k!='X-CSRF-Token'},data={'username':'other.fixture@example.com','password':'isolated other password'})
    assert new.status_code==200,new.text
    headers['X-CSRF-Token']=new.json()['csrf_token']
    assert client.get('/api/calculations/development/runs').json()['total']==0
    for ending in ['', '/workbook','/preview']:
        assert client.get(f'/api/calculations/development/runs/{rid}'+ending).status_code==404
    assert client.post('/api/calculations/development/runs',headers=headers,files={'file':('input.xlsx',package_input())},data=proof).status_code==409


def test_partial_workbook_is_published_downloadable_and_audited_as_generated(isolated,monkeypatch,tmp_path):
    """Real parser/engine/writer with isolated masters, not a mocked successful result."""
    from decimal import Decimal
    _,factory,client=isolated;headers=login(client);authorize(factory)
    monkeypatch.setattr(get_settings(),'calculation_development_enabled',True)
    monkeypatch.setattr(calculations,'ROOT',tmp_path/'runs')
    book=openpyxl.Workbook();sheet=book.active;sheet.title='BOM'
    sheet.append(['0 / 1 / 2 / 3','V','Supplier','End Item','Customer No','Type','Commodity','Qty/Veh'])
    sheet.append([1,1,'fixture','Root','0001','PKG',None,1])
    sheet.append([1,1,'fixture','Root','0002','MAKE','HARD',1])
    buffer=BytesIO();book.save(buffer)
    response=client.post('/api/calculations/development/runs',headers=headers,
        files={'file':('partial.xlsx',buffer.getvalue())},data=confirmed(client))
    assert response.status_code==200,response.text
    payload=response.json();assert payload['status']=='partial'
    assert payload['counts']['total']==2
    assert payload['counts']['calculated']==1
    assert payload['counts']['blocked']+payload['counts']['partial']==1
    assert {row['inputs']['Customer No'] for row in payload['rows']}=={'0001','0002'}
    assert payload['rows'][0]['calculation_status']=='CALCULATED'
    incomplete=next(row for row in payload['rows'] if row['inputs']['Customer No']=='0002')
    assert incomplete['calculation_status'] in {'BLOCKED','PARTIAL'}
    assert incomplete['values'].get('Grand Total Cost/Pc') is None
    download=client.get(payload['download_url']);assert download.status_code==200
    artifact=openpyxl.load_workbook(BytesIO(download.content),data_only=True)
    assert 'Calculation_Diagnostics' in artifact.sheetnames
    assert any('0002' in str(cell.value) for row in artifact['Calculation_Diagnostics'] for cell in row)
    history=client.get('/api/calculations/development/runs').json()['items'][0]
    assert history['status']=='partial' and history['counts']==payload['counts']
    assert client.get(payload['preview_url'],params={'sheet':'Calculation_Diagnostics'}).status_code==200
    assert client.get('/api/calculations/development/runs/'+payload['metadata']['run_id']).json()==payload
    with factory() as db:
        completed=db.query(AuditEvent).filter_by(resource='calculations',action='execution.completed').one()
        assert completed.outcome=='success'
        assert completed.metadata_json['artifact_generated'] is True
        assert completed.metadata_json['calculation_status']=='partial'
        assert completed.metadata_json['counts']==payload['counts']


def test_enabled_flag_does_not_open_production_environment(isolated,monkeypatch):
    _,factory,client=isolated;login(client);authorize(factory)
    settings=get_settings();monkeypatch.setattr(settings,'calculation_development_enabled',True)
    monkeypatch.setattr(settings,'app_env','production')
    cap=client.get('/api/calculations/development/capabilities').json()
    assert cap['development_enabled'] is False and cap['environment_verified'] is False
    assert cap['production_enabled'] is False
    assert client.get('/api/calculations/development/snapshot').status_code==409


def test_enabled_local_environment_rejects_nonlocal_frontend(isolated,monkeypatch):
    _,factory,client=isolated;login(client);authorize(factory)
    settings=get_settings();monkeypatch.setattr(settings,'calculation_development_enabled',True)
    monkeypatch.setattr(settings,'app_env','development')
    monkeypatch.setattr(settings,'frontend_origin','https://production.example.com')
    assert client.get('/api/calculations/development/capabilities').json()['development_enabled'] is False
    assert client.get('/api/calculations/development/snapshot').status_code==409

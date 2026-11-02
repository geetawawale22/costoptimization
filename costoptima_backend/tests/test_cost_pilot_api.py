"""Cost Pilot presentation APIs use existing isolated masters/auth/audit fixtures only."""
from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
import openpyxl
from sqlalchemy import select
from test_masters import isolated, login
from test_calculation_api import authorize, confirmed
from app.api import calculations
from app.calculations import development as dev
from app.config import get_settings
from app.models import User, Permission, RolePermission


def input_book(project='Current project',partial=False):
    book=openpyxl.Workbook();sheet=book.active;sheet.title='BOM'
    sheet.append(['0 / 1 / 2 / 3','V','Supplier','End Item','Customer No','Type','Commodity','Qty/Veh','Project','Part Name'])
    sheet.append([1,1,'Fixture','Root','0001','PKG',None,1,project,'Air filter'])
    if partial:sheet.append([1,1,'Fixture','Root','0002','MAKE','HARD',1,project,'Housing'])
    stream=BytesIO();book.save(stream);return stream.getvalue()


def setup(isolated,monkeypatch,tmp_path):
    _,factory,client=isolated;headers=login(client);authorize(factory)
    monkeypatch.setattr(get_settings(),'calculation_development_enabled',True)
    monkeypatch.setattr(calculations,'ROOT',tmp_path/'runs')
    return factory,client,headers


def create(client,headers,filename='costing.xlsx',project='Current project',partial=False):
    result=client.post('/api/calculations/development/runs',headers=headers,
        files={'file':(filename,input_book(project,partial))},data=confirmed(client))
    assert result.status_code==200,result.text
    return result.json()


def settings_permission(factory):
    with factory() as db:
        user=db.query(User).first();p=Permission(resource='settings',action='view')
        db.add(p);db.flush();db.add(RolePermission(role_id=user.role_id,permission_id=p.id));db.commit()


def test_calculation_history_filters_labels_and_download_title(isolated,monkeypatch,tmp_path):
    _,client,headers=setup(isolated,monkeypatch,tmp_path)
    first=create(client,headers,'bravo.xlsx','Project B')
    second=create(client,headers,'alpha.xlsx','Project A',True)
    assert second['metadata']['uploaded_by']=='Fixture operator'
    assert second['metadata']['project']=='Project A'
    response=client.get('/api/calculations/development/runs',params={'q':'Project A','status':'partial'})
    assert response.status_code==200,response.text
    result=response.json();assert result['total']==1
    item=result['items'][0]
    assert item['run_id']==second['metadata']['run_id'] and item['counts']['total']==2
    assert item['uploaded_by']=='Fixture operator' and item['part_name']=='Air filter, Housing'
    sorted_items=client.get('/api/calculations/development/runs',params={'sort':'name_asc','page_size':1}).json()
    assert sorted_items['items'][0]['input_name']=='alpha.xlsx' and sorted_items['total_pages']==2
    page=client.get('/api/calculations/development/runs',params={'sort':'name_asc','page_size':1,'page':2}).json()
    assert page['items'][0]['input_name']=='bravo.xlsx'
    future=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()
    assert client.get('/api/calculations/development/runs',params={'date_from':future}).json()['total']==0
    assert client.get('/api/calculations/development/runs?sort=SQL').status_code==422
    assert client.get('/api/calculations/development/runs?status=unknown').status_code==422
    assert client.get('/api/calculations/development/runs',params={'date_from':future,'date_to':'2000-01-01T00:00:00Z'}).status_code==422
    download=client.get(second['download_url'])
    assert download.status_code==200
    from urllib.parse import unquote
    recorded_name=second['metadata']['output_name']
    assert recorded_name==item['output_name']
    assert unquote(download.headers['content-disposition'].split("filename*=UTF-8''",1)[1])==recorded_name
    assert 'Project A' in recorded_name and recorded_name.endswith('_1.xlsx')


def test_legacy_signed_manifest_is_not_rewritten(isolated,monkeypatch,tmp_path):
    _,client,headers=setup(isolated,monkeypatch,tmp_path);run=create(client,headers)
    path=calculations.ROOT/'1'/run['metadata']['run_id']/'manifest.json'
    item=json.loads(path.read_bytes())
    for key in ['uploaded_by','project','part_name','counts','status','integrity_mac']:item.pop(key,None)
    item['integrity_mac']=dev.signature(item,'pcm-development-artifact')
    original=dev.canonical(item);path.write_bytes(original)
    listing=client.get('/api/calculations/development/runs').json()['items'][0]
    assert listing['uploaded_by']=='Account 1' and listing['counts']['calculated']==1
    assert path.read_bytes()==original
    assert client.get(run['download_url']).status_code==200


def test_activity_uses_existing_ledger_and_account_authorization(isolated,monkeypatch,tmp_path):
    factory,client,headers=setup(isolated,monkeypatch,tmp_path)
    run=create(client,headers,'activity.xlsx',partial=True);rid=run['metadata']['run_id']
    assert client.get('/api/reports/audit-log/activity').status_code==403
    settings_permission(factory)
    response=client.get('/api/reports/audit-log/activity',params={'q':'activity.xlsx','resource':'calculations','action':'execution.completed','status':'partial'})
    assert response.status_code==200,response.text
    item=response.json()['items'][0]
    assert item['run_id']==rid and item['input_name']=='activity.xlsx'
    assert item['related_result_url']=='/calculations/'+rid
    assert item['actor_name']=='Fixture operator' and item['calculation_status']=='partial'
    from types import SimpleNamespace
    from app.api.audit import _related_calculation
    assert _related_calculation({'resource':'calculations','target_id':rid,'metadata':{}},SimpleNamespace(id=999),True)['related_result_url'] is None
    assert client.get('/api/reports/audit-log/activity',params={'session_ref':'not-this-session'}).json()['total']==0
    assert client.get('/api/reports/audit-log/activity',params={'actor_user_id':9999}).json()['total']==0
    # Removing calculation viewing permission keeps the ledger visible but removes artifact links.
    with factory() as db:
        permission=db.query(Permission).filter_by(resource='boms',action='view').one()
        db.query(RolePermission).filter_by(permission_id=permission.id).one().is_deleted=True;db.commit()
    item=client.get('/api/reports/audit-log/activity',params={'action':'execution.completed'}).json()['items'][0]
    assert item['run_id']==rid and item['related_result_url'] is None
    assert client.get(run['download_url']).status_code==403


def test_formula_inventory_is_read_only_honest_and_permission_checked(isolated):
    _,factory,client=isolated
    assert client.get('/api/formulas').status_code==401
    login(client)
    assert client.get('/api/formulas').status_code==403
    settings_permission(factory)
    response=client.get('/api/formulas',params={'page_size':2000})
    assert response.status_code==200,response.text
    payload=response.json();assert payload['runtime_source']=='python'
    assert payload['read_only'] is True and payload['database_formulas_active'] is False
    assert payload['total']>50 and all(item['editable'] is False for item in payload['items'])
    assert any('MARKUP/Pc'==item['name'] and item['master_dependencies'] for item in payload['items'])
    assert any(item['kind']=='reviewed_consumption_expression' for item in payload['items'])
    assert all(not item['source_file'].startswith(('C:','/')) for item in payload['items'])
    assert client.post('/api/formulas',json={'expression':"__import__('os')"}).status_code in {403,405}
    search=client.get('/api/formulas',params={'q':'std_bop','page_size':2000}).json()
    assert search['total']>0 and all('std_bop' in str(item) for item in search['items'])

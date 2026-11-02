"""Master management tests use only isolated memory tables, never production fixtures."""
from io import BytesIO
from pathlib import Path
import tempfile
from datetime import datetime
from types import SimpleNamespace
from decimal import Decimal
import pytest,openpyxl
from sqlalchemy import create_engine,select,Table,Column,Integer,String,Numeric,MetaData,func
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient
from app import database,audit
from app.models import Base,User,Role,Permission,RolePermission,AuditEvent
from app.config import get_settings
from app.security import hash_password
from app.masters import metadata as m,repository as repo,imports,excel
from app.main import app


@pytest.fixture
def isolated(monkeypatch,tmp_path):
 from collections import OrderedDict
 from app import security
 # Each isolated app fixture owns its own in-memory login throttle.
 monkeypatch.setattr(security,"_attempts",OrderedDict())
 engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
 Base.metadata.create_all(engine);m.metadata.create_all(engine)
 factory=sessionmaker(bind=engine,class_=database.ApplicationSession,expire_on_commit=False)
 monkeypatch.setattr(database,'engine',engine);monkeypatch.setattr(database,'SessionLocal',factory)
 monkeypatch.setattr(get_settings(),'audit_signing_key','b'*64);monkeypatch.setattr(get_settings(),'audit_log_dir',str(tmp_path/'logs'))
 monkeypatch.setattr(imports,'STAGING',tmp_path/'staging')
 for name in ['src_fixture__master','src_other__master']:
  Table(name,MetaData(),Column('row_id',Integer,primary_key=True),Column('code',String(255)),Column('rate',Numeric(20,6))).create(engine)
 with factory() as db:
  c=db.connection();mid=c.execute(m.masters.insert().values(master_code='fixture',master_name='Fixture',sheet_name='Fixture',header_kind='0',unique_column='Code',sort_order=0,view_name='vw_fixture')).inserted_primary_key[0]
  for number,name in enumerate(['src_fixture__master','src_other__master']):
   fid=c.execute(m.families.insert().values(folder_path=name,display_name=name,sort_order=number)).inserted_primary_key[0]
   sid=c.execute(m.sources.insert().values(master_id=mid,family_id=fid,data_table_name=name,source_file=name+'.xlsx',row_count=0,expected_rows=0,merge_order=number)).inserted_primary_key[0]
   c.execute(m.columns.insert(),[dict(source_id=sid,position=0,column_key='row_id',header_text='(system)',group_text=None,sql_type='BIGINT AUTO_INCREMENT PK',storage_type='BIGINT AUTO_INCREMENT PK'),dict(source_id=sid,position=1,column_key='code',header_text='Code',group_text=None,sql_type='VARCHAR(255)',storage_type='VARCHAR(255)'),dict(source_id=sid,position=2,column_key='rate',header_text='Rate',group_text=None,sql_type='DECIMAL(20,6)',storage_type='DECIMAL(20,6)')])
  c.exec_driver_sql("CREATE VIEW vw_fixture AS SELECT code,rate,'src_fixture__master' AS source_folder,row_id AS source_row_id FROM src_fixture__master UNION ALL SELECT code,rate,'src_other__master',row_id FROM src_other__master")
  role=Role(name='Isolated master manager');db.add(role);db.flush()
  for action in ['view','create','update','delete']:
   permission=Permission(resource='masters',action=action);db.add(permission);db.flush();db.add(RolePermission(role_id=role.id,permission_id=permission.id))
  db.add(User(name='Fixture operator',email='master.fixture@example.com',hashed_password=hash_password('isolated master test password'),role_id=role.id,status='Active'));db.commit()
 with TestClient(app) as client:yield engine,factory,client
 engine.dispose()


def login(client):
 headers={'Origin':next(iter(get_settings().origins)),'X-CostOptima-Request':'1'}
 response=client.post('/api/auth/login',headers=headers,data={'username':'master.fixture@example.com','password':'isolated master test password'});assert response.status_code==200,response.text
 headers['X-CSRF-Token']=response.json()['csrf_token'];return headers


def upload_bytes(rows,kind='0'):
 w=openpyxl.Workbook();s=w.active
 if kind in {'1','0,1'}:s.append(['IDs','Prices'])
 if kind!='Single':s.append(['Code','Rate'])
 for row in rows:s.append(row)
 b=BytesIO();w.save(b);return b.getvalue()


def test_crud_merged_audit_and_permissions(isolated):
 engine,factory,client=isolated
 assert client.get('/api/masters').status_code==401
 h=login(client)
 assert client.get('/api/masters').json()['total']==1
 response=client.post('/api/sources/1/rows',headers=h,json={'code':'006054959B91','rate':'1.234567'});assert response.status_code==200,response.text
 rid=response.json()['row_id']
 assert client.get('/api/masters/fixture/rows').json()['items'][0]['code']=='006054959B91'
 assert client.put(f'/api/sources/1/rows/{rid}',headers=h,json={'rate':'2.000001'}).status_code==200
 assert client.get('/api/sources/1/rows',params={'filter_column':'code','filter_value':'006054959B91'}).json()['total']==1
 assert client.put(f'/api/sources/2/rows/{rid}',headers=h,json={'rate':'3'}).status_code==404
 assert client.post('/api/sources/1/rows',headers=h,json={'rate':'1.0000001'}).status_code==422
 assert client.post('/api/masters/fixture/publish',headers=h,json={}).status_code==409
 assert client.post('/api/admin/reseed',headers=h,json={}).status_code==409
 assert client.delete(f'/api/sources/1/rows/{rid}',headers=h).status_code==200
 history=client.get('/api/sources/1/history').json();assert any(e['action']=='row.updated' and e['changes']['rate']['after']=='2.000001' for e in history['events'])
 assert len({e['session_ref'] for e in history['events']})==1


def test_import_modes_binding_stale_preview_and_exports(isolated):
 engine,factory,client=isolated;h=login(client)
 content=upload_bytes([['006054959B91',1.123456]])
 def preview(mode='append',file=content):
  r=client.post('/api/sources/1/import/preview',headers=h,files={'file':('fixture.xlsx',file)},data={'mode':mode});assert r.status_code==200,r.text;return r.json()
 def execute(p,**kw):return client.post('/api/sources/1/import/execute',headers=h,json={'confirmation_token':p['confirmation_token'],**kw})
 p=preview();assert p['can_execute'];assert execute(p).json()['inserted']==1
 p=preview('upsert');assert execute(p).json()['skipped']==1
 new=upload_bytes([['006054959B91',2]])
 p=preview('upsert',new);assert execute(p).json()['updated']==1
 p=preview('replace');assert execute(p).status_code==409
 assert execute(p,confirm_source='src_fixture__master').json()['inserted']==1
 assert execute(p,confirm_source='src_fixture__master').status_code==409
 p=preview();assert client.post('/api/sources/2/import/execute',headers=h,json={'confirmation_token':p['confirmation_token']}).status_code==403
 assert client.post('/api/sources/1/rows',headers=h,json={'code':'another','rate':'1'}).status_code==200
 assert execute(p).status_code==409
 export=client.get('/api/sources/1/export');assert export.status_code==200
 with factory() as db:s=repo.source(db,1)
 parsed=excel.parse(export.content,s);assert not parsed['errors'];assert parsed['rows'][0]['code']=='006054959B91'
 template=client.get('/api/sources/1/template');assert not excel.parse(template.content,s)['rows']
 bad=preview(file=b'not an Excel file');assert not bad['can_execute'] and bad['errors']
 assert client.get('/api/sources/1/history').json()['batches']


def test_header_variants_and_formula_text():
 cols=[dict(position=0,column_key='row_id',header_text='system',group_text=None,sql_type='BIGINT AUTO_INCREMENT PK'),dict(position=1,column_key='code',header_text='Code',group_text='IDs',sql_type='VARCHAR(255)'),dict(position=2,column_key='rate',header_text='Rate',group_text='Prices',sql_type='DECIMAL(20,6)')]
 for kind in ['0','1','0,1','Single']:
  source={'columns':cols,'header_kind':kind,'sheet_name':'Fixture','data_table_name':'fixture','unique_column':'Code'}
  blob=excel.source_workbook(source,[{'code':'006054959B91','rate':Decimal('1.123456')}])
  parsed=excel.parse(blob,source);assert not parsed['errors'];assert parsed['rows'][0]['code']=='006054959B91'
  assert parsed['rows'][0]['rate']==Decimal('1.123456')
  empty=excel.source_workbook(source,[],template=True);assert not excel.parse(empty,source)['rows']
 source={'columns':cols,'header_kind':'0','sheet_name':'Fixture'}
 blob=excel.source_workbook(source,[{'code':'=UNTRUSTED()','rate':Decimal('1')}])
 w=openpyxl.load_workbook(BytesIO(blob),data_only=False);assert w.active['A2'].data_type=='s'


def test_rejected_import_does_not_modify_rows(isolated,monkeypatch):
 engine,factory,client=isolated;h=login(client)
 r=client.post('/api/sources/1/import/preview',headers=h,files={'file':('bad.xlsx',upload_bytes([['x','not numeric']]))},data={'mode':'append'})
 assert not r.json()['can_execute']
 assert client.get('/api/sources/1/rows').json()['total']==0
 assert client.get('/api/sources/2/rows').json()['total']==0
 assert client.post('/api/sources/1/import/execute',headers=h,json={'confirmation_token':'forged'}).status_code==403



def test_import_audit_failure_rolls_back_source(isolated):
 from sqlalchemy import event
 engine,factory,client=isolated;h=login(client)
 r=client.post('/api/sources/1/import/preview',headers=h,files={'file':('good.xlsx',upload_bytes([['00999',1.25]]))},data={'mode':'append'})
 token=r.json()['confirmation_token'];failed=[False]
 def fail_once(conn,cursor,statement,parameters,ctx,many):
  if statement.startswith('INSERT INTO audit_events') and not failed[0]:failed[0]=True;raise RuntimeError('Isolated audit-write failure')
 event.listen(engine,'before_cursor_execute',fail_once)
 try:r=client.post('/api/sources/1/import/execute',headers=h,json={'confirmation_token':token})
 finally:event.remove(engine,'before_cursor_execute',fail_once)
 assert r.status_code==409
 assert client.get('/api/sources/1/rows').json()['total']==0
 events=client.get('/api/sources/1/history').json()['events']
 assert sum(e['action']=='import.failed' for e in events)==1
 assert not any(e['action']=='row.created' for e in events)


def test_existing_duplicate_warning_and_missing_columns(isolated):
 engine,factory,client=isolated;h=login(client)
 client.post('/api/sources/1/rows',headers=h,json={'code':'00999','rate':'1'})
 r=client.post('/api/sources/1/import/preview',headers=h,files={'file':('dupe.xlsx',upload_bytes([['00999',2]]))},data={'mode':'append'})
 preview=r.json();assert preview['duplicates'][0]['existing']
 assert client.post('/api/sources/1/import/execute',headers=h,json={'confirmation_token':preview['confirmation_token']}).status_code==409
 assert client.post('/api/sources/1/import/execute',headers=h,json={'confirmation_token':preview['confirmation_token'],'accept_duplicates':True}).json()['inserted']==1
 w=openpyxl.Workbook();w.active.append(['Unknown']);w.active.append(['data']);b=BytesIO();w.save(b)
 r=client.post('/api/sources/1/import/preview',headers=h,files={'file':('bad.xlsx',b.getvalue())},data={'mode':'append'})
 assert r.json()['errors'] and not r.json()['can_execute']
 assert client.get('/api/sources/1/rows').json()['total']==2


def test_sparse_raw_grid_preserves_positions_and_rejects_hidden_data():
 from app.masters.bootstrap import verified_assy_source
 from app.masters.validation import validate_row
 cols=[dict(position=0,column_key='row_id',header_text='system',group_text=None,sql_type='BIGINT AUTO_INCREMENT PK')]
 for pos,key in [(1,'col_a'),(25,'col_y'),(76,'col_bx'),(149,'col_es'),(170,'col_fn')]:
  cols.append(dict(position=pos,column_key=key,header_text=key,group_text=None,sql_type='VARCHAR(255)'))
 source={'columns':cols,'header_kind':'Single','sheet_name':'Assy','data_table_name':'src_trim__assy','unique_column':None}
 rows=[{'col_a':'00017','col_y':'unheaded note','col_bx':'6','col_es':'10','col_fn':'0'},{}, {'col_fn':'0'}]
 blob=excel.source_workbook(source,rows)
 parsed=excel.parse(blob,source);assert not parsed['errors']
 assert len(parsed['rows'])==3 and parsed['rows'][0]['col_y']=='unheaded note'
 assert parsed['rows'][1]['col_fn'] is None
 w=openpyxl.load_workbook(BytesIO(blob));sheet=w.active
 assert sheet['A1'].value=='00017' and sheet['Y1'].value=='unheaded note'
 assert sheet['BX1'].value=='6' and sheet['ES1'].value=='10' and sheet['FN3'].value=='0'
 assert sheet['B1'].value is None and sheet.max_column==170
 sheet['W1']='unexpected data';b=BytesIO();w.save(b)
 assert excel.parse(b.getvalue(),source)['errors']
 assert validate_row(source,{'col_a':'x'*255})[1]==[]
 assert validate_row(source,{'col_a':'x'*256})[1]
 assert not excel.parse(excel.source_workbook(source,[],template=True),source)['rows']


def test_mixed_union_exports_restore_source_numeric_types(isolated):
 from app.masters import merge
 engine,factory,client=isolated;h=login(client)
 # Model MySQL's common text type across one numeric and one textual source.
 with engine.begin() as c:
  c.exec_driver_sql('DROP VIEW vw_fixture')
  c.exec_driver_sql('DROP TABLE src_other__master')
  Table('src_other__master',MetaData(),Column('row_id',Integer,primary_key=True),Column('code',String(255)),Column('rate',String(255))).create(c)
  c.execute(m.columns.update().where(m.columns.c.source_id==2,m.columns.c.column_key=='rate').values(sql_type='VARCHAR(255)',storage_type='VARCHAR(255)'))
  c.exec_driver_sql("CREATE VIEW vw_fixture AS SELECT code,CAST(rate AS TEXT) rate,'src_fixture__master' source_folder,row_id source_row_id FROM src_fixture__master UNION ALL SELECT code,rate,'src_other__master',row_id FROM src_other__master")
 assert client.post('/api/sources/1/rows',headers=h,json={'code':'00017','rate':'0.009500'}).status_code==200
 assert client.post('/api/sources/2/rows',headers=h,json={'code':'00018','rate':'0.009500'}).status_code==200
 response=client.get('/api/master-workbook');assert response.status_code==200
 sheet=openpyxl.load_workbook(BytesIO(response.content)).active
 assert sheet['A2'].value=='00017' and sheet['A3'].value=='00018'
 assert sheet['B2'].value==0.009 and sheet['B2'].data_type=='n'
 assert sheet['B3'].value=='0.009500' and sheet['B3'].data_type=='s'
 sheet=openpyxl.load_workbook(BytesIO(client.get('/api/master-workbook?exact=true').content)).active
 assert sheet['B2'].value==0.0095 and sheet['B3'].value=='0.009500'
 sheet=openpyxl.load_workbook(BytesIO(client.get('/api/masters/fixture/export').content)).active
 assert sheet['B2'].value==0.0095 and sheet['B3'].value=='0.009500'


def test_export_evidence_types_staleness_and_blank_layout(tmp_path,monkeypatch):
 import gzip,xml.etree.ElementTree as ET
 from app.masters import compatibility as compat
 fields=[dict(column_key='value',position=1,sql_type='VARCHAR(255)')]
 root=ET.Element('export-format');source=ET.SubElement(root,'source',table='src_evidence',mapping_sha256=compat.mapping_digest(fields),row_ids_sha256=compat.value_digest([1,2,3,4]))
 for rid,kind,value in [(1,'number','211'),(2,'boolean','False'),(3,'date','2022-03-05T00:00:00'),(4,'number','00017')]:
  ET.SubElement(source,'type',row=str(rid),field='value',kind=kind,sha256=compat.value_digest(value))
 identity=[('src_evidence',1),('src_evidence',2)]
 layout=ET.SubElement(root,'layout',master='norms_pnf',row_order_sha256=compat.value_digest(identity))
 for rid,pos in [(1,3),(2,53)]:ET.SubElement(layout,'row',table='src_evidence',row=str(rid),excel_row=str(pos))
 path=tmp_path/'format.xml.gz'
 with gzip.open(path,'wb') as stream:stream.write(ET.tostring(root))
 monkeypatch.setattr(compat,'PROFILE',path);e=compat.ExportFormat()
 assert e.prepare([{'data_table_name':'src_evidence','columns':fields}])['src_evidence']
 assert e.convert('src_evidence',1,'value','211')==Decimal('211')
 assert e.convert('src_evidence',2,'value','False') is False
 assert e.convert('src_evidence',3,'value','2022-03-05T00:00:00')==datetime(2022,3,5)
 assert e.convert('src_evidence',4,'value','00017')=='00017'
 assert e.convert('src_evidence',1,'value','212')=='212' and e.warnings
 assert e.convert('src_evidence',1,'value','211',False)=='211'
 e.check_row_evidence([{'folder_path':'Fixture','data_table_name':'src_evidence'}],[{'source_folder':'Fixture','source_row_id':99}])
 assert any('row identities changed' in message for message in e.warnings)
 rows=[{'source_folder':'Fixture','source_row_id':1},{'source_folder':'Fixture','source_row_id':2}]
 assert e.row_positions({'master_code':'norms_pnf'},rows,{'Fixture':{'data_table_name':'src_evidence'}})==[3,53]
 assert e.row_positions({'master_code':'norms_pnf'},list(reversed(rows)),{'Fixture':{'data_table_name':'src_evidence'}}) is None
 w=openpyxl.Workbook();excel.block(w.active,{'header_kind':'0'},[{'column_key':'value','header_text':'Value','group_text':None}], [{'value':1},{'value':2}],row_positions=[3,53])
 assert w.active['A3'].value==1 and w.active['A53'].value==2 and w.active['A4'].value is None


def test_assy_semantic_mapping_uses_real_supplier_columns(isolated):
 engine,factory,client=isolated;h=login(client)
 with engine.begin() as c:
  mid=c.execute(m.masters.insert().values(master_code='assy',master_name='Assy',sheet_name='Assy',header_kind='Single',unique_column=None,sort_order=1,view_name='vw_assy')).inserted_primary_key[0]
  sid=c.execute(m.sources.insert().values(master_id=mid,family_id=1,data_table_name='src_test__assy',source_file='Assy.xlsx',row_count=3,expected_rows=3,merge_order=0)).inserted_primary_key[0]
  columns=[dict(source_id=sid,position=0,column_key='row_id',header_text='system',group_text=None,sql_type='BIGINT AUTO_INCREMENT PK',storage_type='BIGINT AUTO_INCREMENT PK')]
  positions=[(1,'col_a'),(2,'col_b'),(76,'col_bx'),(77,'col_by'),(149,'col_es'),(150,'col_et')]
  columns.extend(dict(source_id=sid,position=pos,column_key=key,header_text=key,group_text=None,sql_type='VARCHAR(255)',storage_type='VARCHAR(255)') for pos,key in positions)
  c.execute(m.columns.insert(),columns)
  t=Table('src_test__assy',MetaData(),Column('row_id',Integer,primary_key=True),*[Column(key,String(255)) for pos,key in positions]);t.create(c)
  c.execute(t.insert(),[dict(row_id=1,col_a=None,col_b='MHR',col_bx=None,col_by='CT per Point',col_es=None,col_et='Handling Time'),dict(row_id=2,col_a=None,col_b='Fix',col_bx=None,col_by='Fix',col_es=None,col_et='Fix'),dict(row_id=3,col_a='Supplier',col_b='Manual',col_bx='CT per Point',col_by='Manual',col_es='Handling Time',col_et='Manual')])
 response=client.get('/api/masters/assy');assert response.status_code==200,response.text
 mapping=response.json()['lookup_mapping']
 assert mapping['blocks']['CT per Point']['supplier_field']=='col_bx'
 assert mapping['blocks']['Handling Time']['supplier_field']=='col_es'
 assert mapping['blocks']['CT per Point']['operation_fields']['Fix']=='col_by'
 assert mapping['blocks']['Handling Time']['operation_fields']['Fix']=='col_et'
 assert mapping['costing_integration_enabled'] is False
 assert client.post('/api/masters/assy/publish',headers=h,json={}).status_code==409


def test_legacy_rounding_includes_original_source_save_precision():
 w=openpyxl.Workbook();sheet=w.active
 excel.write_cell(sheet,1,1,Decimal('415.66249999999997'),3,legacy_rounding=True)
 excel.write_cell(sheet,1,2,Decimal('0.19349999999999998'),3,legacy_rounding=True)
 excel.write_cell(sheet,1,3,Decimal('0.19349999999999998'),None)
 assert sheet['A1'].value==Decimal('415.663')
 assert sheet['B1'].value==Decimal('0.194')
 assert sheet['C1'].value==Decimal('0.19349999999999998')

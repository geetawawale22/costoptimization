"""Cost Pilot master review tests use isolated memory tables only."""
from test_masters import isolated, login
from app.models import AuditEvent,RolePermission


def test_review_is_read_only_then_signed_commit_preserves_identifiers(isolated):
    _,factory,client=isolated;headers=login(client)
    preview=client.post('/api/sources/1/rows/review',headers=headers,json={'values':{'code':'00017','rate':'1.234567'}})
    assert preview.status_code==200,preview.text
    item=preview.json();assert item['changes']['code']['after']=='00017'
    assert client.get('/api/sources/1/rows').json()['total']==0
    commit=client.post('/api/sources/1/rows/commit',headers=headers,json={'values':item['values'],'confirmation_token':item['confirmation_token']})
    assert commit.status_code==200,commit.text
    assert commit.json()['code']=='00017'
    with factory() as db:
        rows=db.query(AuditEvent).filter(AuditEvent.resource=='masters',AuditEvent.action=='row.created').all()
        assert len(rows)==1 and rows[0].changes['rate']['after']=='1.234567'


def test_review_rejects_duplicate_blank_and_lossy_keys_or_values(isolated):
    _,_,client=isolated;headers=login(client)
    assert client.post('/api/sources/1/rows',headers=headers,json={'code':'00017','rate':'1'}).status_code==200
    for values,status in [({'code':'00017','rate':'2'},409),({'code':'','rate':'2'},422),({'code':'00018','rate':'1.1234567'},422)]:
        response=client.post('/api/sources/1/rows/review',headers=headers,json={'values':values})
        assert response.status_code==status,response.text
    assert client.get('/api/sources/1/rows').json()['total']==1


def test_changed_or_stale_confirmation_cannot_overwrite_record(isolated):
    _,_,client=isolated;headers=login(client)
    row=client.post('/api/sources/1/rows',headers=headers,json={'code':'00017','rate':'1'}).json()
    preview=client.post('/api/sources/1/rows/review',headers=headers,json={'row_id':row['row_id'],'values':{'rate':'2'}}).json()
    body={'values':{'rate':'3'},'confirmation_token':preview['confirmation_token']}
    assert client.post('/api/sources/1/rows/commit',headers=headers,json=body).status_code==409
    assert client.put('/api/sources/1/rows/'+str(row['row_id']),headers=headers,json={'rate':'4'}).status_code==200
    body['values']=preview['values']
    assert client.post('/api/sources/1/rows/commit',headers=headers,json=body).status_code==409
    assert client.get('/api/sources/1/rows').json()['items'][0]['rate']=='4.000000'


def test_reference_check_blocks_delete_without_changing_history(isolated):
    _,_,client=isolated;headers=login(client)
    row=client.post('/api/sources/1/rows',headers=headers,json={'code':'00017','rate':'1'}).json()
    client.post('/api/sources/2/rows',headers=headers,json={'code':'00017','rate':'2'})
    response=client.post('/api/sources/1/rows/review',headers=headers,json={'row_id':row['row_id'],'delete':True})
    assert response.status_code==409,response.text
    assert response.json()['detail']['references'][0]['kind']=='matching_business_key'
    assert client.delete('/api/sources/1/rows/'+str(row['row_id']),headers=headers).status_code==409
    assert client.get('/api/sources/1/rows').json()['total']==1
    assert not any(x['action']=='row.deleted' for x in client.get('/api/sources/1/history').json()['events'])


def test_review_is_bound_to_source_and_authorization(isolated):
    _,factory,client=isolated;headers=login(client)
    item=client.post('/api/sources/1/rows/review',headers=headers,json={'values':{'code':'00017','rate':'1'}}).json()
    assert client.post('/api/sources/2/rows/commit',headers=headers,json={'values':item['values'],'confirmation_token':item['confirmation_token']}).status_code==403
    with factory() as db:
        [db.delete(grant) for grant in db.query(RolePermission).all()];db.commit()
    assert client.post('/api/sources/1/rows/review',headers=headers,json={'values':{'code':'00018','rate':'1'}}).status_code==403
    assert client.post('/api/sources/1/rows/commit',headers=headers,json={'values':item['values'],'confirmation_token':item['confirmation_token']}).status_code==403


def test_keyless_create_confirmation_cannot_be_replayed(isolated):
    from app.masters import metadata as meta
    engine,_,client=isolated;headers=login(client)
    with engine.begin() as conn:
        conn.execute(meta.masters.update().values(unique_column=None))
    item=client.post('/api/sources/1/rows/review',headers=headers,json={'values':{'code':'00017','rate':'1'}}).json()
    body={'values':item['values'],'confirmation_token':item['confirmation_token']}
    assert client.post('/api/sources/1/rows/commit',headers=headers,json=body).status_code==200
    assert client.post('/api/sources/1/rows/commit',headers=headers,json=body).status_code==409
    assert client.get('/api/sources/1/rows').json()['total']==1


def test_replace_import_cannot_remove_referenced_source_records(isolated):
    from test_masters import upload_bytes
    _,_,client=isolated;headers=login(client)
    for source in [1,2]:
        assert client.post(f'/api/sources/{source}/rows',headers=headers,json={'code':'00017','rate':'1'}).status_code==200
    preview=client.post('/api/sources/1/import/preview',headers=headers,files={'file':('replacement.xlsx',upload_bytes([['00018',2]]))},data={'mode':'replace'}).json()
    result=client.post('/api/sources/1/import/execute',headers=headers,json={'confirmation_token':preview['confirmation_token'],'confirm_source':'src_fixture__master'})
    assert result.status_code==409,result.text
    rows=client.get('/api/sources/1/rows').json()['items']
    assert len(rows)==1 and rows[0]['code']=='00017'

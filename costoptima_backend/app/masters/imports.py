"""Two-phase, session-bound imports. Staged files are never costing inputs."""
from pathlib import Path
from hashlib import sha256
import base64,json,hmac,time,secrets
from sqlalchemy import select,func
from fastapi import HTTPException
from app.masters import metadata as m
from app.masters import repository as repo
from app.masters.excel import parse,MAX_FILE
from app.audit import record,context,signing_key,now

STAGING=Path(__file__).resolve().parents[2]/'master-imports'


def fingerprint(db,s):
    digest=sha256()
    t=repo.data_table(db,s)
    for row in db.execute(select(t).order_by(t.c.row_id)).mappings():digest.update(json.dumps(repo.public(dict(row)),sort_keys=True,default=str).encode())
    return digest.hexdigest()


def sign(data):
    body=base64.urlsafe_b64encode(json.dumps(data,sort_keys=True,separators=(',',':')).encode()).decode()
    return body+'.'+hmac.new(signing_key(),b'master-preview:'+body.encode(),'sha256').hexdigest()


def unsign(value):
    try:
        body,signature=value.rsplit('.',1)
        if not hmac.compare_digest(signature,hmac.new(signing_key(),b'master-preview:'+body.encode(),'sha256').hexdigest()):raise ValueError()
        data=json.loads(base64.urlsafe_b64decode(body))
        if time.time()-data['issued']>1800 or time.time()<data['issued']:raise ValueError()
        return data
    except Exception:raise HTTPException(403,'Import preview is invalid or expired; upload again')


def preview(db,s,user,content,filename,mode,mapping=None):
    if mode not in {'append','replace','upsert'}:raise HTTPException(422,'Unsupported import mode')
    if not filename.lower().endswith('.xlsx') or len(content)>MAX_FILE:raise HTTPException(422,'Upload a .xlsx file no larger than 12 MB')
    filename=Path(filename).name[:255]
    ctx=context()
    try:result=parse(content,s,mapping)
    except Exception:
        result={'rows':[],'errors':[{'row':0,'column':'','message':'Workbook could not be parsed safely'}],'duplicates':[],'mapping':{},'input_columns':[],'detected_header_row':None}
    key=repo.key_column(s)
    if key and mode=='append':
        t=repo.data_table(db,s)
        existing=set(db.scalars(select(t.c[key]).where(t.c[key].is_not(None))))
        for number,row in enumerate(result['rows'],1):
            if row.get(key) in existing:result['duplicates'].append({'row':number,'key':str(row[key]),'existing':True})
    if mode=='upsert':
        if not key:result['errors'].append({'row':0,'column':'','message':'This source has no unambiguous upsert key'})
        elif any(r.get(key) is None for r in result['rows']):result['errors'].append({'row':0,'column':key,'message':'Upsert requires a nonblank key on every row'})
        if result['duplicates']:result['errors'].append({'row':0,'column':key or '','message':'Upsert input keys must be unique'})
        if key:
            t=repo.data_table(db,s)
            if db.execute(select(t.c[key]).where(t.c[key].is_not(None)).group_by(t.c[key]).having(func.count()>1).limit(1)).first():result['errors'].append({'row':0,'column':key,'message':'Existing source has duplicate keys; upsert is ambiguous'})
    file_hash=sha256(content).hexdigest()
    batch_id=db.connection().execute(m.batches.insert().values(source_id=s['source_id'],import_mode=mode,file_name=filename,rows_inserted=0,rows_updated=0,rows_rejected=len(result['rows']) if result['errors'] else 0,rows_skipped=0,created_by=user.name,actor_user_id=user.id,session_ref=ctx.get('session_ref'),request_id=ctx.get('request_id'),created_at=now(),status='rejected' if result['errors'] else 'preview',file_sha256=file_hash)).inserted_primary_key[0]
    if result['errors']:
        db.connection().execute(m.errors.insert(),[dict(batch_id=batch_id,row_number=e['row'],column_key=e['column'][:64],message=e['message'][:1000]) for e in result['errors'][:500]])
    STAGING.mkdir(parents=True,exist_ok=True)
    path=STAGING/(str(batch_id)+'-'+secrets.token_hex(16)+'.xlsx')
    token=None
    if not result['errors']:
        with path.open('xb') as f:f.write(content)
        db.connection().execute(m.batches.update().where(m.batches.c.batch_id==batch_id).values(staging_file=str(path)))
        token=sign({'batch':batch_id,'source':s['source_id'],'actor':user.id,'session':ctx.get('session_ref'),'sha':file_hash,'mapping':result['mapping'],'mode':mode,'issued':time.time(),'source_hash':fingerprint(db,s)})
    record(db,'masters','import.attempted',outcome='failure' if result['errors'] else 'attempt',target_type=s['data_table_name'],target_id=batch_id,metadata={'source_id':s['source_id'],'master_code':s['master_code'],'rows':len(result['rows']),'errors':len(result['errors']),'mode':mode})
    db.commit()
    return repo.public({'batch_id':batch_id,'confirmation_token':token,'preview':result['rows'][:50],'total_rows':len(result['rows']),'errors':result['errors'][:500],'duplicates':result['duplicates'][:100],'mapping':result['mapping'],'input_columns':result['input_columns'],'detected_header_row':result['detected_header_row'],'can_execute':not result['errors']})


def execute(db,s,user,token,confirm_source='',accept_duplicates=False):
    data=unsign(token)
    if data['source']!=s['source_id'] or data['actor']!=user.id or data['session']!=context().get('session_ref'):raise HTTPException(403,'Import belongs to a different source, user or session')
    batch=db.execute(select(m.batches).where(m.batches.c.batch_id==data['batch'],m.batches.c.source_id==s['source_id']).with_for_update()).mappings().first()
    if not batch or batch['status']!='preview':raise HTTPException(409,'Import is no longer pending')
    path=Path(batch['staging_file']).resolve()
    if path.parent!=STAGING.resolve():raise HTTPException(409,'Invalid staged file')
    try:content=path.read_bytes()
    except OSError:raise HTTPException(409,'Staged workbook is unavailable; upload again')
    if sha256(content).hexdigest()!=data['sha']:raise HTTPException(409,'Staged file changed')
    if fingerprint(db,s)!=data['source_hash']:raise HTTPException(409,'Source changed after preview; upload and preview again')
    result=parse(content,s,data['mapping'])
    if result['errors']:raise HTTPException(422,'Validation changed; no source rows were modified')
    if result['duplicates'] and not accept_duplicates:raise HTTPException(409,'Acknowledge duplicate-key warnings before importing')
    mode=data['mode']
    if mode=='append' and not accept_duplicates:
        key=repo.key_column(s)
        if key:
            existing_table=repo.data_table(db,s)
            existing=set(db.scalars(select(existing_table.c[key]).where(existing_table.c[key].is_not(None))))
            if any(row.get(key) in existing for row in result['rows']):raise HTTPException(409,'Acknowledge duplicate keys already present in this source')
    if mode=='replace' and confirm_source!=s['data_table_name']:raise HTTPException(409,'Type the exact source-table name to confirm replacement')
    t=repo.data_table(db,s);inserted=updated=skipped=0;key=repo.key_column(s)
    if mode=='replace':
        for existing in db.execute(select(t)).mappings():
            references=repo.deletion_references(db,s,dict(existing))
            if references:raise HTTPException(409,{'message':'Replacement would remove a referenced source record. Review dependencies or use a validated upsert.','row_id':existing['row_id'],'references':references})
    try:
        if mode=='replace':
            for row in db.execute(select(t)).mappings():repo.change(db,s,'row.deleted',row['row_id'],dict(row),{},user)
            db.connection().execute(t.delete())
        for values in result['rows']:
            old=db.execute(select(t).where(t.c[key]==values[key])).mappings().first() if mode=='upsert' and key else None
            if old:
                before=dict(old)
                if all(before.get(k)==v for k,v in values.items()):skipped+=1;continue
                db.connection().execute(t.update().where(t.c.row_id==old['row_id']).values(**values));updated+=1
                repo.change(db,s,'row.updated',old['row_id'],before,values,user)
            else:
                rid=db.connection().execute(t.insert().values(**values)).inserted_primary_key[0];inserted+=1
                repo.change(db,s,'row.created',rid,{},values,user)
        repo.touch(db,s,user)
        db.connection().execute(m.batches.update().where(m.batches.c.batch_id==batch['batch_id']).values(status='completed',rows_inserted=inserted,rows_updated=updated,rows_skipped=skipped,rows_rejected=0,finished_at=now()))
        record(db,'masters','import.succeeded',target_type=s['data_table_name'],target_id=batch['batch_id'],metadata={'source_id':s['source_id'],'master_code':s['master_code'],'inserted':inserted,'updated':updated,'skipped':skipped,'mode':mode})
        db.commit()
    except Exception:
        db.rollback()
        db.connection().execute(m.batches.update().where(m.batches.c.batch_id==batch['batch_id']).values(status='failed',finished_at=now()))
        record(db,'masters','import.failed',outcome='failure',target_type=s['data_table_name'],target_id=batch['batch_id'],error_category='transaction_failed',metadata={'source_id':s['source_id']})
        db.commit()
        context()['import_failure_recorded']=True
        context()['failure_recorded']=True
        raise HTTPException(409,'Import transaction failed and source changes were rolled back')
    path.unlink(missing_ok=True)
    return {'batch_id':batch['batch_id'],'inserted':inserted,'updated':updated,'skipped':skipped,'rejected':0}

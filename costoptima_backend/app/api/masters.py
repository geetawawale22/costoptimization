from io import BytesIO
import json
from datetime import datetime
from fastapi import APIRouter,Depends,Query,HTTPException,UploadFile,File,Form
from fastapi.responses import StreamingResponse
from pydantic import BaseModel,Field
from sqlalchemy import select,func,cast,String,or_,inspect
from sqlalchemy.orm import Session
from app.database import get_db
from app.security import require_permission
from app.masters import repository as repo,metadata as meta,merge,imports,excel
from app.models import AuditEvent
from app.audit import record,record_independent,context

router=APIRouter(prefix='/api',tags=['Master Data'])
VIEW=require_permission('masters','view');CREATE=require_permission('masters','create');UPDATE=require_permission('masters','update');DELETE=require_permission('masters','delete')


def download(db,user,data,name,source_id=None,master_code=None,kind='source'):
    record(db,'masters','export.generated',target_type=kind,target_id=source_id or master_code,metadata={'source_id':source_id,'master_code':master_code,'bytes':len(data)})
    db.commit()
    return StreamingResponse(BytesIO(data),media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',headers={'Content-Disposition':'attachment; filename="'+name+'"'})


def filters(column,value):return {column:value} if column and value is not None else {}


@router.get('/masters')
def list_masters(q:str='',status:str='',db:Session=Depends(get_db),user=Depends(VIEW)):
    results=[]
    categories={}
    for family in db.execute(select(meta.sources.c.master_id,meta.families.c.display_name).join(meta.families,meta.sources.c.family_id==meta.families.c.family_id)).mappings():categories.setdefault(family['master_id'],set()).add(family['display_name'])
    for row in db.execute(select(meta.masters).order_by(meta.masters.c.sort_order)).mappings():
        m=dict(row);m['categories']=sorted(categories.get(m['master_id'],set()))
        if q and q.casefold() not in (m['master_code']+' '+m['master_name']).casefold():continue
        counts=db.execute(select(func.count(),func.coalesce(func.sum(meta.sources.c.row_count),0)).where(meta.sources.c.master_id==m['master_id'])).one()
        m.update(source_count=counts[0],row_count=int(counts[1]),status=('available' if counts[1] else 'empty') if inspect(db.connection()).has_table(m['view_name']) else 'blocked')
        if status and status!=m['status']:continue
        results.append(m)
    return {'items':results,'total':len(results),'costing_integration_enabled':False}


@router.get('/master-workbook')
def master_workbook(float_decimals:int|None=Query(3,ge=0,le=6),exact:bool=False,db:Session=Depends(get_db),user=Depends(VIEW)):
    data,warnings=merge.workbook(db,None if exact else float_decimals)
    return download(db,user,data,'Master Rate Data.xlsx',kind='master-workbook')


@router.get('/masters/{code}')
def detail(code:str,db:Session=Depends(get_db),user=Depends(VIEW)):
    m=repo.master(db,code);m['sources']=repo.source_list(db,m['master_id']);m['columns']=repo.merged_fields(db,m['master_id']);m['merge_columns']=list(db.scalars(select(meta.merge_columns.c.column_name).where(meta.merge_columns.c.master_id==m['master_id'])));m['output_columns']=repo.output_fields(db,m['master_id']);m['configuration_editable']=False;m['lookup_mapping']=repo.assy_lookup_mapping(db,m);m['costing_integration_enabled']=False;return m


@router.get('/masters/{code}/sources')
def master_sources(code:str,db:Session=Depends(get_db),user=Depends(VIEW)):
    return {'items':repo.source_list(db,repo.master(db,code)['master_id'])}


@router.get('/masters/{code}/rows')
def merged_rows(code:str,page:int=Query(1,ge=1,le=100000),page_size:int=Query(50,ge=1,le=200),q:str=Query('',max_length=200),family:str='',filter_column:str='',filter_value:str|None=None,db:Session=Depends(get_db),user=Depends(VIEW)):
    try:return merge.browse(db,repo.master(db,code),page,page_size,q,family,filters(filter_column,filter_value))
    except ValueError as exc:raise HTTPException(422,str(exc))


@router.get('/masters/{code}/duplicates')
def duplicates(code:str,db:Session=Depends(get_db),user=Depends(VIEW)):return merge.duplicates(db,repo.master(db,code))


@router.get('/masters/{code}/export')
def merged_export(code:str,db:Session=Depends(get_db),user=Depends(VIEW)):
    return download(db,user,merge.export_master(db,repo.master(db,code)),code+'.xlsx',master_code=code,kind='merged')


@router.get('/sources/{source_id}')
def source_detail(source_id:int,db:Session=Depends(get_db),user=Depends(VIEW)):return repo.source(db,source_id)


@router.get('/sources/{source_id}/rows')
def source_rows(source_id:int,page:int=Query(1,ge=1,le=100000),page_size:int=Query(50,ge=1,le=200),q:str=Query('',max_length=200),filter_column:str='',filter_value:str|None=None,db:Session=Depends(get_db),user=Depends(VIEW)):
    return repo.browse(db,repo.source(db,source_id),page,page_size,q,filters(filter_column,filter_value))


class RowReview(BaseModel):
    values:dict=Field(default_factory=dict)
    row_id:int|None=Field(None,ge=1)
    delete:bool=False


@router.post('/sources/{source_id}/rows/review')
def review_row(source_id:int,payload:RowReview,db:Session=Depends(get_db),user=Depends(VIEW)):
    permission=DELETE if payload.delete else UPDATE if payload.row_id is not None else CREATE
    permission(user,db)
    if payload.delete and payload.row_id is None:raise HTTPException(422,'Select a source record to delete')
    return repo.review_mutation(db,repo.source(db,source_id,lock=True),payload.values,user,payload.row_id,payload.delete)


class RowCommit(BaseModel):
    values:dict=Field(default_factory=dict)
    confirmation_token:str=Field(max_length=20000)


@router.post('/sources/{source_id}/rows/commit')
def commit_row(source_id:int,payload:RowCommit,db:Session=Depends(get_db),user=Depends(VIEW)):
    approved=imports.unsign(payload.confirmation_token)
    if approved.get('kind')!='source-row' or approved.get('source')!=source_id:raise HTTPException(403,'Review the selected source record first')
    action=approved.get('action');row_id=approved.get('row')
    if action not in {'create','update','delete'}:raise HTTPException(403,'Invalid source record operation')
    permission=DELETE if action=='delete' else UPDATE if action=='update' else CREATE
    permission(user,db)
    result=repo.mutate(db,repo.source(db,source_id,lock=True),payload.values,user,row_id,delete=action=='delete',confirmation_token=payload.confirmation_token)
    db.commit()
    return result


@router.post('/sources/{source_id}/rows')
def add_row(source_id:int,values:dict,confirmation_token:str|None=Query(None,max_length=20000),db:Session=Depends(get_db),user=Depends(CREATE)):
    result=repo.mutate(db,repo.source(db,source_id,lock=True),values,user,confirmation_token=confirmation_token);db.commit();return result


@router.put('/sources/{source_id}/rows/{row_id}')
def update_row(source_id:int,row_id:int,values:dict,confirmation_token:str|None=Query(None,max_length=20000),db:Session=Depends(get_db),user=Depends(UPDATE)):
    result=repo.mutate(db,repo.source(db,source_id,lock=True),values,user,row_id,confirmation_token=confirmation_token);db.commit();return result


@router.delete('/sources/{source_id}/rows/{row_id}')
def delete_row(source_id:int,row_id:int,confirmation_token:str|None=Query(None,max_length=20000),db:Session=Depends(get_db),user=Depends(DELETE)):
    result=repo.mutate(db,repo.source(db,source_id,lock=True),{},user,row_id,delete=True,confirmation_token=confirmation_token);db.commit();return result


@router.get('/sources/{source_id}/template')
def template(source_id:int,db:Session=Depends(get_db),user=Depends(VIEW)):
    s=repo.source(db,source_id);return download(db,user,excel.source_workbook(s,[],template=True),'source-'+str(source_id)+'-template.xlsx',source_id,s['master_code'],'template')


@router.get('/sources/{source_id}/export')
def source_export(source_id:int,db:Session=Depends(get_db),user=Depends(VIEW)):
    s=repo.source(db,source_id);t=repo.data_table(db,s);rows=[dict(r) for r in db.execute(select(t).order_by(t.c.row_id)).mappings()]
    return download(db,user,excel.source_workbook(s,rows),'source-'+str(source_id)+'.xlsx',source_id,s['master_code'])


@router.post('/sources/{source_id}/import/preview')
async def import_preview(source_id:int,file:UploadFile=File(...),mode:str=Form('append'),mapping:str=Form('{}'),db:Session=Depends(get_db),user=Depends(UPDATE)):
    try:map_data=json.loads(mapping)
    except ValueError:raise HTTPException(422,'Invalid column mapping')
    if not isinstance(map_data,dict):raise HTTPException(422,'Column mapping must be an object')
    require_permission('masters','create')(user,db)
    content=await file.read(excel.MAX_FILE+1)
    return imports.preview(db,repo.source(db,source_id,lock=True),user,content,file.filename or '',mode,map_data)


class ExecuteImport(BaseModel):
    confirmation_token:str=Field(max_length=20000)
    confirm_source:str=''
    accept_duplicates:bool=False


@router.post('/sources/{source_id}/import/execute')
def execute_import(source_id:int,payload:ExecuteImport,db:Session=Depends(get_db),user=Depends(UPDATE)):
    s=repo.source(db,source_id,lock=True)
    # Replace is destructive; permission and exact typed-source confirmation are required.
    try:
        require_permission('masters','create')(user,db)
        data=imports.unsign(payload.confirmation_token)
        if data['mode']=='replace':require_permission('masters','delete')(user,db)
        return imports.execute(db,s,user,payload.confirmation_token,payload.confirm_source,payload.accept_duplicates)
    except HTTPException as exc:
        if not context().get('import_failure_recorded'):
            db.rollback()
            record_independent('masters','import.failed',outcome='failure',target_type=s['data_table_name'],target_id=source_id,error_category='http_'+str(exc.status_code),metadata={'source_id':source_id})
            context()['failure_recorded']=True
        raise



@router.get('/sources/{source_id}/history')
def history(source_id:int,page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=200),db:Session=Depends(get_db),user=Depends(VIEW)):
    s=repo.source(db,source_id)
    count=db.scalar(select(func.count()).select_from(meta.batches).where(meta.batches.c.source_id==source_id))
    batches=[dict(r) for r in db.execute(select(meta.batches).where(meta.batches.c.source_id==source_id).order_by(meta.batches.c.batch_id.desc()).offset((page-1)*page_size).limit(page_size)).mappings()]
    for b in batches:
        b.pop('staging_file',None);b.pop('file_sha256',None)
        b['errors']=[dict(r) for r in db.execute(select(meta.errors).where(meta.errors.c.batch_id==b['batch_id']).limit(500)).mappings()]
    T=AuditEvent.__table__
    event_count=db.scalar(select(func.count()).select_from(T).where(T.c.resource=='masters',T.c.target_type==s['data_table_name']))
    audit=[dict(r) for r in db.execute(select(T.c.event_id,T.c.occurred_at,T.c.actor_user_id,T.c.session_ref,T.c.request_id,T.c.action,T.c.outcome,T.c.changes,T.c.summary).where(T.c.resource=='masters',T.c.target_type==s['data_table_name']).order_by(T.c.id.desc()).offset((page-1)*page_size).limit(page_size)).mappings()]
    total=max(count,event_count)
    return {'batches':batches,'events':audit,'page':page,'page_size':page_size,'total':total,'total_pages':max(1,(total+page_size-1)//page_size)}


@router.post('/masters/{code}/publish')
def publish_guard(code:str,user=Depends(UPDATE)):raise HTTPException(409,'Costing integration is guarded; this module cannot publish authoritative costing data')


@router.post('/admin/reseed')
def reseed_guard(user=Depends(UPDATE)):raise HTTPException(409,'Automatic bulk reseeding is disabled; maintained master data is never overwritten automatically')

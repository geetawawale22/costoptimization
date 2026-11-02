"""All dynamic identifiers come from relational metadata, never user SQL."""
import re
from decimal import Decimal
from sqlalchemy import Table, MetaData, select, func, cast, String, or_, and_, text, inspect
from sqlalchemy.exc import NoSuchTableError
from fastapi import HTTPException
from app.masters.metadata import masters,sources,columns,families,merge_columns,batches,errors
from app.audit import record,context,now


def ident(value):
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}",value): raise HTTPException(409,"Invalid configured SQL identifier")
    return value


def master(db,code):
    row=db.execute(select(masters).where(masters.c.master_code==code)).mappings().first()
    if not row: raise HTTPException(404,"Master not found")
    context().update(resource='masters',target_type='master_definition',target_id=code,event_metadata={'master_code':code})
    return dict(row)


def source(db,source_id,lock=False,set_context=True):
    query=select(sources,masters.c.master_code,masters.c.master_name,masters.c.sheet_name,masters.c.header_kind,masters.c.unique_column,families.c.folder_path,families.c.display_name).join(masters,sources.c.master_id==masters.c.master_id).join(families,sources.c.family_id==families.c.family_id).where(sources.c.source_id==source_id)
    if lock:query=query.with_for_update()
    row=db.execute(query).mappings().first()
    if not row:raise HTTPException(404,"Source not found")
    result=dict(row)
    if set_context:context().update(resource='masters',target_type=result['data_table_name'],target_id=source_id,event_metadata={'source_id':source_id,'master_code':result['master_code']})
    result['available']=inspect(db.connection()).has_table(result['data_table_name']);result['columns']=field_list(db,source_id);result['merge_columns']=list(db.scalars(select(merge_columns.c.column_name).where(merge_columns.c.master_id==result['master_id'])));return result


def field_list(db,source_id):
    return [dict(r) for r in db.execute(select(columns).where(columns.c.source_id==source_id).order_by(columns.c.position)).mappings()]


def data_table(db,source):
    try:return Table(ident(source['data_table_name']),MetaData(),autoload_with=db.connection())
    except NoSuchTableError:raise HTTPException(409,'This source table awaits an approved storage/schema resolution: '+source['data_table_name'])


def source_list(db,master_id):
    return [source(db,sid,set_context=False) for sid in db.scalars(select(sources.c.source_id).where(sources.c.master_id==master_id).order_by(sources.c.merge_order))]


def merged_fields(db,master_id):
    result={}
    for s in source_list(db,master_id):
        for col in s['columns']:
            if col['column_key']!='row_id' and col['column_key'] not in result:result[col['column_key']]=col
    return list(result.values())


def union_order(db,m):
    return source_list(db,m['master_id'])


def key_column(s):
    candidates=[c['column_key'] for c in s['columns'] if c['header_text']==s['unique_column'] or c['column_key']==s['unique_column']]
    return candidates[0] if len(candidates)==1 else None


def public(value):
    if isinstance(value,Decimal):return str(value)
    if isinstance(value,dict):return {k:public(v) for k,v in value.items()}
    if isinstance(value,list):return [public(v) for v in value]
    return value


def browse(db,s,page=1,page_size=50,q='',filters=None):
    t=data_table(db,s);conditions=[]
    if q:
        conditions.append(or_(*[cast(c,String).contains(q,autoescape=True) for c in t.c if c.name!='row_id']))
    for k,v in (filters or {}).items():
        if k not in t.c:raise HTTPException(422,'Unknown filter column')
        conditions.append(cast(t.c[k],String)==str(v))
    count=db.scalar(select(func.count()).select_from(t).where(*conditions))
    rows=db.execute(select(t).where(*conditions).order_by(t.c.row_id).offset((page-1)*page_size).limit(page_size)).mappings().all()
    return {'items':public([dict(r) for r in rows]),'total':count,'page':page,'page_size':page_size,'total_pages':max(1,(count+page_size-1)//page_size),'columns':s['columns']}


def touch(db,s,user):
    t=data_table(db,s)
    count=db.scalar(select(func.count()).select_from(t))
    db.connection().execute(sources.update().where(sources.c.source_id==s['source_id']).values(row_count=count,updated_at=now(),updated_by=user.name))


def change(db,s,action,row_id,before,after,user):
    fields={k:{'before':public(before.get(k)),'after':public(after.get(k))} for k in set(before)|set(after) if k!='row_id' and before.get(k)!=after.get(k)}
    record(db,'masters',action,target_type=s['data_table_name'],target_id=row_id,changes=fields,metadata={'master_code':s['master_code'],'source_id':s['source_id'],'family':s['folder_path']})


def deletion_references(db,s,before):
    """Conservative references from declared semantic fields and real SQL FKs."""
    references=[]
    if s.get('header_kind')=='Single' and before.get('row_id',0)<=3:
        references.append({'kind':'source_header','source':s['source_id'],'message':'This row may define positional source headers; deletion requires a reviewed mapping change.'})
    key=key_column(s)
    if key and before.get(key) is not None:
        label=next((str(f.get('header_text') or '').strip().casefold() for f in s['columns'] if f['column_key']==key),'')
        if label:
            candidates=db.execute(select(columns.c.source_id,columns.c.column_key,columns.c.header_text,sources.c.data_table_name).join(sources,columns.c.source_id==sources.c.source_id).where(columns.c.source_id!=s['source_id'])).mappings()
            for field in candidates:
                if str(field['header_text'] or '').strip().casefold()!=label:continue
                table=Table(ident(field['data_table_name']),MetaData(),autoload_with=db.connection())
                if field['column_key'] not in table.c:raise HTTPException(409,'A referenced source definition needs review before deletion')
                found=db.execute(select(table.c.row_id).where(table.c[field['column_key']]==before[key]).limit(1)).first()
                if found:references.append({'kind':'matching_business_key','source_id':field['source_id'],'field':field['column_key'],'row_id':found[0]})
    inspector=inspect(db.connection())
    if db.bind.dialect.name in {'mysql','mariadb'}:
        incoming=db.execute(text('SELECT TABLE_NAME,COLUMN_NAME,REFERENCED_COLUMN_NAME FROM information_schema.KEY_COLUMN_USAGE WHERE REFERENCED_TABLE_SCHEMA=DATABASE() AND REFERENCED_TABLE_NAME=:name'),{'name':s['data_table_name']}).mappings()
        foreign=[(x['TABLE_NAME'],[x['COLUMN_NAME']],[x['REFERENCED_COLUMN_NAME']]) for x in incoming]
    else:
        foreign=[(name,fk['constrained_columns'],fk['referred_columns']) for name in inspector.get_table_names() for fk in inspector.get_foreign_keys(name) if fk['referred_table']==s['data_table_name']]
    for name,local,remote in foreign:
        table=Table(ident(name),MetaData(),autoload_with=db.connection())
        conditions=[table.c[a]==before.get(b) for a,b in zip(local,remote)]
        if conditions and db.execute(select(func.count()).select_from(table).where(*conditions)).scalar():
            references.append({'kind':'foreign_key','table':name,'fields':local})
    return references


def prepare_mutation(db,s,values,row_id=None,delete=False):
    from app.masters.validation import validate_row
    table=data_table(db,s);before={};warnings=[]
    if row_id is not None:
        row=db.execute(select(table).where(table.c.row_id==row_id).with_for_update()).mappings().first()
        if not row:raise HTTPException(404,'Source row not found')
        before=dict(row)
    if delete:
        refs=deletion_references(db,s,before)
        if refs:raise HTTPException(409,{'message':'This record may be referenced. Review its dependencies before deleting it.','references':refs})
        return before,{}, {},warnings
    data,issues=validate_row(s,values,partial=row_id is not None)
    if issues:raise HTTPException(422,issues)
    after={**before,**data};key=key_column(s)
    if key:
        if after.get(key) is None:raise HTTPException(422,'The configured business key is required')
        matches=db.scalar(select(func.count()).select_from(table).where(table.c[key]==after[key],table.c.row_id!=row_id)) if row_id is not None else db.scalar(select(func.count()).select_from(table).where(table.c[key]==after[key]))
        if matches:
            if row_id is None or before.get(key)!=after[key]:raise HTTPException(409,'A record with this business key already exists in this source')
            warnings.append('Other rows share this business key. Only the explicitly selected row will change.')
    return before,after,data,warnings


def review_mutation(db,s,values,user,row_id=None,delete=False):
    from app.masters.imports import sign,fingerprint
    import time
    from hashlib import sha256
    import json
    before,after,data,warnings=prepare_mutation(db,s,values,row_id,delete)
    changes={k:{'before':public(before.get(k)),'after':public(after.get(k))} for k in sorted(set(before)|set(after)) if k!='row_id' and before.get(k)!=after.get(k)}
    action='delete' if delete else 'update' if row_id is not None else 'create'
    digest=lambda v:sha256(json.dumps(public(v),sort_keys=True,default=str).encode()).hexdigest()
    token=sign({'kind':'source-row','source':s['source_id'],'row':row_id,'action':action,'actor':user.id,'session':context().get('session_ref'),'issued':time.time(),'before':digest(before),'values':digest(data),'source_hash':fingerprint(db,s)})
    return {'confirmation_token':token,'action':action,'source_id':s['source_id'],'row_id':row_id,'changes':changes,'values':public(data),'warnings':warnings,'can_save':True}


def mutate(db,s,values,user,row_id=None,delete=False,confirmation_token=None):
    before,after,data,warnings=prepare_mutation(db,s,values,row_id,delete)
    if confirmation_token:
        from app.masters.imports import unsign,fingerprint
        from hashlib import sha256
        import json
        expected=unsign(confirmation_token)
        action='delete' if delete else 'update' if row_id is not None else 'create'
        digest=lambda v:sha256(json.dumps(public(v),sort_keys=True,default=str).encode()).hexdigest()
        if any((expected.get('kind')!='source-row',expected.get('source')!=s['source_id'],expected.get('row')!=row_id,expected.get('action')!=action,expected.get('actor')!=user.id,expected.get('session')!=context().get('session_ref'))):raise HTTPException(403,'This review belongs to another record or account')
        if expected.get('source_hash')!=fingerprint(db,s) or expected.get('before')!=digest(before) or expected.get('values')!=digest(data):raise HTTPException(409,'The record or proposed values changed. Review the changes again.')
    t=data_table(db,s)
    if delete:
        db.connection().execute(t.delete().where(t.c.row_id==row_id));after={};action='row.deleted'
    else:
        if row_id is None:
            result=db.connection().execute(t.insert().values(**data));row_id=result.inserted_primary_key[0];action='row.created'
        else:
            if data:db.connection().execute(t.update().where(t.c.row_id==row_id).values(**data))
            action='row.updated'
        after=dict(db.execute(select(t).where(t.c.row_id==row_id)).mappings().one())
    if before!=after:change(db,s,action,row_id,before,after,user)
    touch(db,s,user)
    return public(after) if not delete else {'deleted':row_id}


def output_fields(db,master_id):
    from app.masters.metadata import output_columns
    configured=[dict(r) for r in db.execute(select(output_columns).where(output_columns.c.master_id==master_id).order_by(output_columns.c.position)).mappings()]
    if not configured:return merged_fields(db,master_id)
    fields={f['column_key']:f for f in merged_fields(db,master_id)}
    return [{**fields.get(c['column_key'],{}),**c} for c in configured]


def assy_lookup_mapping(db,m):
    """Resolve supplier and operation fields from the raw Assy semantic headers.
    No costing is executed; old erroneous BN/EA ranges are never used.
    """
    if m['master_code']!='assy':return None
    ss=source_list(db,m['master_id'])
    if len(ss)!=1 or m['header_kind']!='Single':raise HTTPException(409,'Assy lookup source is ambiguous')
    source=ss[0];t=data_table(db,source)
    headers=[dict(r) for r in db.execute(select(t).order_by(t.c.row_id).limit(3)).mappings()]
    if len(headers)!=3:raise HTTPException(409,'Assy semantic headers are unavailable')
    fields={c['position']:c['column_key'] for c in source['columns'] if c['position']>0}
    names={'mhr':'MHR','ct per point':'CT per Point','handling time':'Handling Time'};blocks=[]
    for position,key in fields.items():
        label=str(headers[0].get(key) or '').strip().casefold()
        if label in names:blocks.append((position,names[label]))
    if len(blocks)!=3:raise HTTPException(409,'Assy semantic header blocks are ambiguous')
    blocks.sort();result={}
    for i,(start,name) in enumerate(blocks):
        end=blocks[i+1][0] if i+1<len(blocks) else max(fields)+1
        supplier=fields.get(start-1)
        if not supplier:raise HTTPException(409,'Assy supplier field is missing')
        operations={str(headers[1][key]):key for pos,key in fields.items() if start<=pos<end and headers[1].get(key) is not None}
        result[name]={'supplier_field':supplier,'operation_fields':operations}
    return {'source_id':source['source_id'],'source_table':source['data_table_name'],'policy':'supplier_specific_semantic_columns','blocks':result,'costing_integration_enabled':False,'blank_supplier_policy':'unverified; costing blocked'}

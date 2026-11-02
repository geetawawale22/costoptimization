"""Explicit one-time workbook initializer, never invoked by application startup.
No legacy files are read by normal master-management requests.
"""
import argparse
import re
from pathlib import Path
from collections import defaultdict,Counter
from decimal import Decimal
import openpyxl
from sqlalchemy import Table,Column,BigInteger,Integer,MetaData,select,inspect,func,text
from app.masters import metadata as m
from app.masters.excel import parse,clean
from app.masters.validation import validate_row
from app.audit import activity_context,record,now

WIDE={'src_trim__assy','src_casting_forging_machining__casting_recipie_master','src_root__gross_consumption_base'}


def verified_assy_source(source,path):
    """Initialization only: distinguish raw-grid values from Excel formatting.
    Keep sparse Excel positions; blank alignment belongs to output metadata.
    Formula cells count as fields even when their cached result is empty.
    """
    w=openpyxl.load_workbook(path,read_only=True,data_only=False)
    try:
        sheet=w['Assy']
        populated={cell.column for row in sheet for cell in row if cell.value is not None}
    finally:w.close()
    declared={c['position'] for c in source['columns'] if c['position']>0}
    if not populated or not populated.issubset(declared):raise ValueError('Assy contains undeclared populated columns; review the source specification')
    kept=[]
    for field in source['columns']:
        if field['position']==0 or field['position'] in populated:
            field=dict(field)
            if field['position']>0:field['storage_type']='VARCHAR(256)' if field['position']<=50 else 'TEXT'
            kept.append(field)
    return {**source,'columns':kept,'output_width':max(populated)}


def specification(path,authority,legacy_root=None):
    w=openpyxl.load_workbook(path,read_only=True,data_only=True)
    fields=defaultdict(list)
    for table,position,key,header,group,typ in list(w['Source fields'].values)[1:]:
        fields[table].append(dict(position=int(position),column_key=key,header_text=header,group_text=group,sql_type=typ,storage_type='TEXT' if table in WIDE and typ=='VARCHAR(255)' and not (table=='src_trim__assy' and int(position)<=50) else typ))
    result=[]
    source_rows={r[0]:r for r in list(w['Source tables (89)'].values)[1:]}
    for order,row in enumerate(list(w['Merged views (28)'].values)[1:]):
        view,name,sheet,kind,key,position,count,ordered=row
        definition=dict(master_code=view[3:],master_name=name,sheet_name=sheet,header_kind=str(kind),unique_column=key,norms_position=int(position) if position is not None else None,sort_order=order,view_name=view)
        source_list=[]
        for index,table in enumerate(ordered.split(' | ')):
            _,_,_,_,family,expected,_,file=source_rows[table]
            source_list.append(dict(data_table_name=table,folder_path=family,source_file=file,expected_rows=expected,merge_order=index,columns=fields[table]))
        assert len(source_list)==count
        result.append((definition,source_list))
    a=openpyxl.load_workbook(authority,read_only=True,data_only=True)
    merging=list(a['Column_Merging'].values);merge={}
    for index,name in enumerate(merging[0]):
        if name:merge[name]=[row[index] for row in merging[1:] if index<len(row) and row[index] is not None]
    assert len(result)==28 and sum(len(s) for _,s in result)==89 and sum(len(s['columns']) for _,ss in result for s in ss)==1938
    root=Path(legacy_root) if legacy_root else Path(authority).parent.parent/'PCM_master'
    for _,ss in result:
        for index,source in enumerate(ss):
            if source['data_table_name']=='src_trim__assy':ss[index]=verified_assy_source(source,root/source['source_file'])
    return result,merge


def expected_table(source):
    return Table(source['data_table_name'],MetaData(),*[Column(c['column_key'],BigInteger().with_variant(Integer(),'sqlite') if c['position']==0 else m.sql_type(c['storage_type']),primary_key=c['position']==0,autoincrement=True if c['position']==0 else 'auto',nullable=c['position']!=0) for c in source['columns']],mysql_engine='InnoDB',mysql_charset='utf8mb4',mysql_row_format='DYNAMIC')


def create_view(connection,definition,source_list):
    from app.masters.repository import ident
    quote=lambda value:'`'+ident(value)+'`'
    fields=list(dict.fromkeys(c['column_key'] for s in source_list for c in s['columns'] if c['column_key']!='row_id'))
    selects=[];cases=[]
    for index,s in enumerate(source_list):
        present={c['column_key'] for c in s['columns']};folder=s['folder_path'].replace("'","''")
        fields_sql=','.join(quote(c) if c in present else 'NULL AS '+quote(c) for c in fields)
        selects.append('SELECT '+fields_sql+", '"+folder+"' AS source_folder, row_id AS source_row_id FROM "+quote(s['data_table_name']))
        cases.append("WHEN '"+folder+"' THEN "+str(index))
    sql='CREATE VIEW '+quote(definition['view_name'])+' AS '+' UNION ALL '.join(selects)+' ORDER BY CASE source_folder '+' '.join(cases)+' END, source_row_id'
    connection.exec_driver_sql(sql)


def recover(source,definition,all_sources,loaded,reference):
    # The legacy merge is ordered concatenation. Recover only its missing segment,
    # after checking the reference row count and every known source's key sequence.
    w=openpyxl.load_workbook(reference,read_only=True,data_only=True)
    sheet=w[definition['sheet_name']];offset=definition['norms_position'] or 0
    fields=[c for c in source['columns'] if c['position']>0]
    reference_rows=[dict(zip([f['column_key'] for f in fields],[clean(v) for v in values])) for values in sheet.iter_rows(min_row=3,min_col=offset+1,max_col=offset+len(fields),values_only=True) if any(clean(v) is not None for v in values)]
    w.close()
    if len(reference_rows)!=sum(s['expected_rows'] for s in all_sources):raise ValueError('Recovery reference block count does not match ordered source definitions')
    key=next((f['column_key'] for f in fields if f['header_text']==definition['unique_column']),None)
    position=0;missing=[]
    for s in all_sources:
        segment=reference_rows[position:position+s['expected_rows']];position+=s['expected_rows']
        if s['data_table_name']==source['data_table_name']:missing=segment
        else:
            known=loaded.get(s['data_table_name'],[])
            if len(known)!=len(segment) or (key and any(str(a.get(key))!=str(b.get(key)) for a,b in zip(known,segment))):raise ValueError('Known source precedence/key sequence does not match recovery reference')
    rows=[]
    for data in missing:
        for f in fields:
            value=data[f['column_key']]
            if f['sql_type']=='DECIMAL(20,6)' and value is not None:data[f['column_key']]=Decimal(str(int(value) if isinstance(value,bool) else value)).quantize(Decimal('.000001'))
        valid,issues=validate_row({**definition,**source},data)
        if issues:raise ValueError('Recovered values do not match source types')
        rows.append(valid)
    return rows


def output_layout(definition,source_list,reference):
    from openpyxl.utils import get_column_letter
    from app.masters.excel import norm
    w=openpyxl.load_workbook(reference,read_only=True,data_only=True);sheet=w[definition['sheet_name']]
    fields={}
    for s in source_list:
        for c in s['columns']:
            if c['column_key']!='row_id':fields.setdefault(c['column_key'],c)
    offset=definition['norms_position'] or 0
    width=len(fields)
    if definition['norms_position'] is None:width=sheet.max_column
    if definition['header_kind']=='Single':width=max(s.get('output_width',max(c['position'] for c in s['columns'])) for s in source_list)
    kind=definition['header_kind'];used=set();result=[]
    for index in range(width):
        header=sheet.cell(1 if kind=='0' else 2,offset+index+1).value if kind!='Single' else get_column_letter(index+1)
        group=sheet.cell(1,offset+index+1).value if kind in {'1','0,1'} else None
        key=None
        if kind=='Single':key='col_'+get_column_letter(index+1).lower()
        elif header is not None and str(header).startswith('Unnamed:'):
            try:key='col_'+get_column_letter(int(str(header).split(':')[1].strip())+1).lower()
            except ValueError:pass
        else:
            candidates=[k for k,f in fields.items() if norm(f['header_text'])==norm(header) and k not in used]
            if not candidates:
                unmangled=re.sub(r'\.\d+$','',norm(header))
                candidates=[k for k,f in fields.items() if norm(f['header_text'])==unmangled and k not in used]
            if len(candidates)>1:candidates=[k for k in candidates if norm(fields[k]['group_text']).split('.')[0]==norm(group).split('.')[0]]
            if candidates:key=candidates[0]
        if key not in fields:key=None
        if key:used.add(key)
        result.append(dict(position=index+1,column_key=key,header_text=None if kind=='Single' else str(header) if header is not None else None,group_text=str(group) if group is not None else None))
    w.close()
    missing=set(fields)-used
    # Nonempty source fields absent from the reference remain available in source
    # and merged exports; workbook parity must never silently discard their values.
    for key in fields:
        if key in missing:
            field=fields[key]
            result.append(dict(position=len(result)+1,column_key=key,header_text=field['header_text'],group_text=field['group_text']))
    return result


def initialize(workbook,authority,legacy_root,reference,load=False):
    from app.database import engine,SessionLocal
    spec,merge=specification(workbook,authority,legacy_root)
    conflicts=[];created=[];reused=[];views_created=[];loaded={}
    # Parse and validate references before any source data changes.
    if load:
        for definition,ss in spec:
            for s in ss:
                if s['source_file'].startswith('reference/'):continue
                path=Path(legacy_root)/s['source_file']
                if not path.is_file():conflicts.append({'object':s['data_table_name'],'reason':'Missing legacy file '+s['source_file']});continue
                result=parse(path.read_bytes(),{**definition,**s,'merge_columns':merge.get(definition['master_name'],[])},legacy=True)
                if result['errors']:conflicts.append({'object':s['data_table_name'],'reason':result['errors'][:3]});continue
                if len(result['rows'])!=s['expected_rows']:conflicts.append({'object':s['data_table_name'],'reason':'Expected '+str(s['expected_rows'])+' rows, parsed '+str(len(result['rows']))})
                loaded[s['data_table_name']]=result['rows']
        for definition,ss in spec:
            for s in ss:
                if s['source_file'].startswith('reference/'):
                    try:loaded[s['data_table_name']]=recover(s,definition,ss,loaded,reference)
                    except Exception as exc:conflicts.append({'object':s['data_table_name'],'reason':str(exc)})
    with engine.connect() as c:
        if c.exec_driver_sql("SELECT GET_LOCK('costoptima_master_initialization',10)").scalar()!=1:raise RuntimeError('Initialization is already running')
        try:
            inspector=inspect(c)
            for table in m.metadata.sorted_tables:
                if not inspector.has_table(table.name):table.create(c);c.commit()
                else:
                    actual={col['name'] for col in inspect(c).get_columns(table.name)}
                    if actual!=set(table.c.keys()):raise RuntimeError('Incompatible metadata table: '+table.name)
            for definition,ss in spec:
                valid=True
                for s in ss:
                    table=expected_table(s)
                    if inspect(c).has_table(table.name):
                        actual=inspect(c).get_columns(table.name)
                        expected=list(table.c)
                        same=len(actual)==len(expected) and all(a['name']==e.name and str(a['type']).lower().replace('numeric','decimal')==str(e.type.compile(dialect=c.dialect)).lower().replace('numeric','decimal') and a['nullable']==e.nullable for a,e in zip(actual,expected))
                        if not same:conflicts.append({'object':table.name,'reason':'Existing source schema differs'});valid=False;continue
                        reused.append(table.name)
                    else:
                        try:table.create(c);c.commit();created.append(table.name)
                        except Exception as exc:
                            c.rollback();conflicts.append({'object':table.name,'reason':type(exc).__name__+' creating required source table'});valid=False
                existing=inspect(c).get_view_names()
                if definition['view_name'] in existing:
                    expected=set(c['column_key'] for s in ss for c in s['columns'] if c['column_key']!='row_id')|{'source_folder','source_row_id'}
                    actual={col['name'] for col in inspect(c).get_columns(definition['view_name'])}
                    if actual!=expected:conflicts.append({'object':definition['view_name'],'reason':'Existing view differs'});continue
                elif inspect(c).has_table(definition['view_name']):conflicts.append({'object':definition['view_name'],'reason':'Name occupied by table'});continue
                elif valid:create_view(c,definition,ss);c.commit();views_created.append(definition['view_name'])
        finally:c.exec_driver_sql("SELECT RELEASE_LOCK('costoptima_master_initialization')");c.commit()
    blocked={v['object'] for v in conflicts}
    with activity_context():
        with SessionLocal() as db:
            connection=db.connection()
            family_ids={r.folder_path:r.family_id for r in db.execute(select(m.families))}
            for definition,ss in spec:
                existing=db.execute(select(m.masters).where(m.masters.c.master_code==definition['master_code'])).mappings().first()
                if existing:
                    if any(existing[k]!=v for k,v in definition.items()):raise RuntimeError('Existing master definition conflicts: '+definition['master_code'])
                    mid=existing['master_id']
                else:mid=connection.execute(m.masters.insert().values(**definition)).inserted_primary_key[0]
                if not db.scalar(select(func.count()).select_from(m.output_columns).where(m.output_columns.c.master_id==mid)):
                    layout=output_layout(definition,ss,reference)
                    connection.execute(m.output_columns.insert(),[dict(master_id=mid,**col) for col in layout])
                for column in merge.get(definition['master_name'],[]):
                    if not db.scalar(select(m.merge_columns.c.merge_column_id).where(m.merge_columns.c.master_id==mid,m.merge_columns.c.column_name==column)):connection.execute(m.merge_columns.insert().values(master_id=mid,column_name=column))
                for s in ss:
                    folder=s['folder_path']
                    if folder not in family_ids:family_ids[folder]=connection.execute(m.families.insert().values(folder_path=folder,display_name=folder,sort_order=len(family_ids))).inserted_primary_key[0]
                    existing=db.execute(select(m.sources).where(m.sources.c.data_table_name==s['data_table_name'])).mappings().first()
                    if existing:
                        # Resume this explicit initialization only for its own empty,
                        # untouched metadata records. Never replace maintained rows.
                        if load and existing['row_count']==0 and existing['updated_by']=='Explicit initialization' and s['data_table_name'] not in blocked:
                            t=expected_table(s)
                            if connection.execute(select(func.count()).select_from(t)).scalar()==0:
                                rows=loaded.get(s['data_table_name'],[])
                                for offset in range(0,len(rows),500):connection.execute(t.insert(),rows[offset:offset+500])
                                connection.execute(m.sources.update().where(m.sources.c.source_id==existing['source_id']).values(row_count=len(rows)))
                                record(db,'masters','reference_loaded',target_type=s['data_table_name'],target_id=existing['source_id'],metadata={'source_id':existing['source_id'],'rows':len(rows),'resumed_initialization':True})
                        continue
                    sid=connection.execute(m.sources.insert().values(master_id=mid,family_id=family_ids[folder],data_table_name=s['data_table_name'],source_file=s['source_file'],row_count=0,expected_rows=s['expected_rows'],merge_order=s['merge_order'],updated_at=now(),updated_by='Explicit initialization')).inserted_primary_key[0]
                    connection.execute(m.columns.insert(),[dict(source_id=sid,**col) for col in s['columns']])
                    if load and s['data_table_name'] not in blocked and (s['data_table_name'] in created or connection.execute(select(func.count()).select_from(expected_table(s))).scalar()==0):
                        rows=loaded.get(s['data_table_name'],[])
                        t=expected_table(s)
                        for offset in range(0,len(rows),500):connection.execute(t.insert(),rows[offset:offset+500])
                        connection.execute(m.sources.update().where(m.sources.c.source_id==sid).values(row_count=len(rows)))
                        record(db,'masters','reference_loaded',outcome='success',target_type=s['data_table_name'],target_id=sid,metadata={'source_id':sid,'rows':len(rows),'source_file':s['source_file']})
            record(db,'masters','initialized',metadata={'source_tables_created':len(created),'views_created':len(views_created),'conflicts':conflicts})
            db.commit()
    print({'tables_created':len(created),'tables_reused':len(reused),'views_created':len(views_created),'masters':28,'sources':89,'fields':sum(len(s['columns']) for _,ss in spec for s in ss),'loaded_rows':sum(len(v) for k,v in loaded.items() if k not in blocked),'conflicts':conflicts})


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--workbook',required=True);parser.add_argument('--authority',required=True);parser.add_argument('--legacy-root',required=True);parser.add_argument('--reference',required=True);parser.add_argument('--load-reference-data',action='store_true');args=parser.parse_args()
    initialize(args.workbook,args.authority,args.legacy_root,args.reference,args.load_reference_data)

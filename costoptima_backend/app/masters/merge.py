"""Ordered live views and fresh configured workbook generation; no costing switch."""
from io import BytesIO
from decimal import Decimal,InvalidOperation
from fastapi import HTTPException
from sqlalchemy.exc import NoSuchTableError
from sqlalchemy import Table,MetaData,select,func,case,cast,String,or_
import openpyxl
from app.masters import repository as repo
from app.masters.metadata import masters
from app.masters.excel import block,source_workbook
from app.masters.compatibility import ExportFormat


def view(db,m):
    try:return Table(repo.ident(m['view_name']),MetaData(),autoload_with=db.connection())
    except NoSuchTableError:raise HTTPException(409,'Merged view awaits its source schema: '+m['view_name'])


def query(db,m,q='',family='',filters=None):
    t=view(db,m);conditions=[]
    if family:conditions.append(t.c.source_folder==family)
    if q:conditions.append(or_(*[cast(c,String).contains(q,autoescape=True) for c in t.c if c.name not in {'source_folder','source_row_id'}]))
    for key,value in (filters or {}).items():
        if key not in t.c:raise ValueError('Unknown merged filter column')
        conditions.append(cast(t.c[key],String)==str(value))
    order={s['folder_path']:s['merge_order'] for s in repo.source_list(db,m['master_id'])}
    return t,select(t).where(*conditions).order_by(case(order,value=t.c.source_folder),t.c.source_row_id),conditions


def source_typed_rows(db,m,rows):
    """Undo MySQL UNION text promotion using trusted source-field metadata.
    Never infer numeric types from text: identifiers and mixed raw grids remain text.
    """
    numeric={s['folder_path']:{f['column_key'] for f in s['columns'] if f['sql_type']=='DECIMAL(20,6)'} for s in repo.source_list(db,m['master_id'])}
    result=[]
    for original in rows:
        row=dict(original)
        for key in numeric.get(row['source_folder'],set()):
            if row.get(key) is not None:
                try:
                    value=Decimal(str(row[key]))
                    if not value.is_finite():raise InvalidOperation()
                    row[key]=value
                except (InvalidOperation,ValueError):raise HTTPException(409,'Merged numeric value is incompatible with its source definition')
        result.append(row)
    return result


def browse(db,m,page,page_size,q='',family='',filters=None):
    t,stmt,conditions=query(db,m,q,family,filters)
    count=db.scalar(select(func.count()).select_from(t).where(*conditions))
    data=db.execute(stmt.offset((page-1)*page_size).limit(page_size)).mappings().all()
    return {'items':repo.public(source_typed_rows(db,m,data)),'columns':[f for f in repo.output_fields(db,m['master_id']) if f['column_key'] is not None],'total':count,'page':page,'page_size':page_size,'total_pages':max(1,(count+page_size-1)//page_size)}


def duplicates(db,m):
    fields=repo.merged_fields(db,m['master_id']);keys=[f['column_key'] for f in fields if f['header_text']==m['unique_column']]
    if len(keys)!=1:return {'key':None,'items':[],'message':'No unambiguous unique key configured for this raw grid'}
    t=view(db,m);key=t.c[keys[0]]
    duplicate=db.execute(select(key.label('value'),func.count().label('count')).where(key.is_not(None)).group_by(key).having(func.count()>1).limit(500)).mappings().all()
    return {'key':keys[0],'items':repo.public([dict(r) for r in duplicate]),'limit':500,'policy':'Warnings only; merged rows retain all duplicates. Legacy first match follows configured source order then source row ID.'}


def export_master(db,m,decimals=None):
    _,stmt,_=query(db,m)
    rows=source_typed_rows(db,m,db.execute(stmt).mappings())
    return source_workbook({**m,'columns':[f for f in repo.output_fields(db,m['master_id']) if f['column_key'] is not None]},rows,decimals=decimals)


def workbook(db,decimals=3):
    w=openpyxl.Workbook();w.remove(w.active);warnings=[];ends={};formatting=ExportFormat()
    for m in db.execute(select(masters).order_by(masters.c.sort_order)).mappings():
        fields=repo.output_fields(db,m['master_id'])
        _,stmt,_=query(db,m)
        rows=source_typed_rows(db,m,db.execute(stmt).mappings())
        ss=repo.source_list(db,m['master_id']);by_family={s['folder_path']:s for s in ss};valid=formatting.prepare(ss)
        formatting.check_row_evidence(ss,rows)
        row_positions=formatting.row_positions(m,rows,by_family)
        for row in rows:
            source=by_family[row['source_folder']];table=source['data_table_name']
            for key in list(row):
                if key not in ('source_folder','source_row_id'):row[key]=formatting.convert(table,row['source_row_id'],key,row[key],valid.get(table,False))
        sheet=w[m['sheet_name']] if m['sheet_name'] in w.sheetnames else w.create_sheet(m['sheet_name'])
        offset=m['norms_position'] or 0
        if m['norms_position'] is not None and offset<ends.get(sheet.title,0):
            warnings.append(m['master_code']+': shifted from '+str(offset)+' to '+str(ends[sheet.title])+' to avoid overlap')
            offset=ends[sheet.title]
        ends[sheet.title]=block(sheet,m,fields,rows,offset=offset,decimals=decimals,legacy_rounding=decimals==3,row_positions=row_positions)
    formatting.apply_presentation(w,decimals)
    warnings.extend(formatting.warnings)
    if warnings:
        report=w.create_sheet('Export warnings')
        for message in warnings:report.append([message])
    result=BytesIO();w.save(result);return result.getvalue(),warnings

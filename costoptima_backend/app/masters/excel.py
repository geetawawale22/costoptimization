"""Excel parsing/export driven solely by database definitions at runtime."""
from io import BytesIO
from decimal import Decimal
from zipfile import ZipFile, BadZipFile
from pathlib import Path
import re
import openpyxl
from openpyxl.styles import Font,PatternFill,Alignment
from openpyxl.utils import get_column_letter,column_index_from_string
from fastapi import HTTPException
from app.masters.validation import clean,validate_row

MAX_FILE=12*1024*1024
MAX_ROWS=50000


def norm(v):return str(v).strip().casefold() if v is not None else ''


def open_file(content):
    if not content or len(content)>MAX_FILE:raise ValueError('Excel file must be nonempty and at most 12 MB')
    try:
        with ZipFile(BytesIO(content)) as z:
            if sum(i.file_size for i in z.infolist())>100*1024*1024:raise ValueError('Excel expanded size exceeds 100 MB')
            if any('vbaProject' in n for n in z.namelist()):raise ValueError('Macro-enabled workbooks are not accepted')
        return openpyxl.load_workbook(BytesIO(content),read_only=True,data_only=True,keep_links=False)
    except (BadZipFile,KeyError,TypeError) as exc:raise ValueError('Invalid .xlsx workbook') from exc


def cell_value(cell):
    value=clean(cell.value)
    if isinstance(value,(int,float)) and re.fullmatch(r'0{2,}',cell.number_format or ''):
        return str(int(value)).zfill(len(cell.number_format))
    return value


def parse(content,source,mapping=None,legacy=False):
    workbook=open_file(content)
    try:
        sheet=workbook.worksheets[0]
        if sheet.max_row>MAX_ROWS+12 or sheet.max_column>512 or sheet.max_row*sheet.max_column>2000000:raise ValueError('Workbook exceeds the supported dimensions')
        grid=[[cell_value(c) for c in row] for row in sheet.iter_rows()]
    finally:workbook.close()
    fields=[f for f in source['columns'] if f['column_key']!='row_id']
    kind=source['header_kind'];issues=[];matched={};header=0
    if kind=='Single':
        matched={f['column_key']:f['position']-1 for f in fields};start=0
        known=set(matched.values())
        if any(any(v is not None and i not in known for i,v in enumerate(row)) for row in grid):issues.append({'row':1,'column':'','message':'Unexpected populated raw-grid columns; blank positional columns must remain empty'})
    else:
        candidates=range(1,min(10,len(grid))) if kind in {'1','0,1'} else range(min(10,len(grid)))
        header=max(candidates,key=lambda i:sum(norm(v) in {norm(f['header_text']) for f in fields if f['header_text']} for v in grid[i]),default=1 if kind!='0' else 0)
        labels=grid[header] if header<len(grid) else []
        banners=grid[header-1] if header else []
        explicit=mapping or {}
        for field in fields:
            key=field['column_key'];label=field['header_text'];options=[]
            if key in explicit:
                try:options=[int(explicit[key])]
                except (ValueError,TypeError):options=[]
            elif label is None and key.startswith('col_'):
                index=column_index_from_string(key[4:].upper())-1
                if len(labels)==len(fields) and field['position']-1<len(labels) and labels[field['position']-1] is None:index=field['position']-1
                if index<len(labels):options=[index]
            else:
                bases=source.get('merge_columns',[]) if kind=='0,1' else []
                normalized=lambda v: next((norm(base) for base in bases if norm(base) in norm(v)),norm(v))
                options=[i for i,v in enumerate(labels) if normalized(v)==norm(label) or norm(v)==norm(key)]
                if len(options)>1:
                    options=[i for i in options if norm(banners[i] if i<len(banners) else None)==norm(field['group_text'])]
            if len(options)>1:
                peers=[f for f in fields if norm(f['header_text'])==norm(label) and norm(f['group_text'])==norm(field['group_text'])]
                if len(peers)==len(options):options=[options[peers.index(field)]]
            if len(options)!=1 or options[0]<0 or options[0]>=len(labels):
                issues.append({'row':header+1,'column':key,'message':'Missing or ambiguous header; provide an explicit zero-based column mapping'})
            else:matched[key]=options[0]
        if len(set(matched.values()))!=len(matched):issues.append({'row':header+1,'column':'','message':'Multiple configured fields map to the same Excel column'})
        unknown=[i for i,v in enumerate(labels) if i not in matched.values() and (v is not None or any(i<len(r) and r[i] is not None for r in grid[header+1:]))]
        if unknown and not legacy:issues.append({'row':header+1,'column':'','message':'Unknown populated columns: '+', '.join(str(i)+': '+str(labels[i]) for i in unknown)})
        start=header+1
    rows=[];row_numbers=[]
    for number,row in enumerate(grid[start:],start+1):
        values={key:row[i] if i<len(row) else None for key,i in matched.items()}
        if kind!='Single' and all(v is None for v in values.values()):continue
        if legacy:
            for field in fields:
                value=values.get(field['column_key'])
                if field['sql_type']=='DECIMAL(20,6)' and isinstance(value,(int,float)):
                    values[field['column_key']]=Decimal(str(int(value) if isinstance(value,bool) else value)).quantize(Decimal('.000001'))
        valid,errors=validate_row(source,values)
        issues.extend({'row':number,'column':e['column'],'message':e['message']} for e in errors)
        rows.append(valid);row_numbers.append(number)
    if kind=='Single':
        while rows and all(v is None for v in rows[-1].values()):rows.pop();row_numbers.pop()
    key=next((f['column_key'] for f in fields if f['header_text']==source.get('unique_column')),None)
    seen=set();duplicates=[]
    if key:
        for index,row in enumerate(rows):
            value=row.get(key)
            if value is not None and value in seen:duplicates.append({'row':row_numbers[index],'key':str(value)})
            seen.add(value)
    return {'rows':rows,'errors':issues,'duplicates':duplicates,'detected_header_row':None if kind=='Single' else header+1,'mapping':matched,'input_columns':[{'index':i,'header':str(v) if v is not None else ''} for i,v in enumerate(grid[header] if grid else [])]}


def write_cell(sheet,row,col,value,decimals=None,legacy_rounding=False):
    if isinstance(value,Decimal):
        # The explicitly labelled legacy 3-place workbook mode reproduces pandas float_format.
        if decimals is not None:
            # Legacy read_without_formula() saves via openpyxl's %.16g before pandas %.3f.
            value=Decimal(format(float(format(float(value),'.16g')),'.'+str(decimals)+'f')) if legacy_rounding else value.quantize(Decimal(1).scaleb(-decimals))
    cell=sheet.cell(row,col,value)
    if isinstance(value,str):cell.data_type='s'
    if isinstance(value,(Decimal,int,float)) and not isinstance(value,bool):cell.number_format='0.'+'0'*(decimals if decimals is not None else 6)
    return cell


def block(sheet,definition,fields,rows,offset=0,decimals=3,template=False,legacy_rounding=False,row_positions=None):
    kind=definition['header_kind'];fields=[f for f in fields if f['column_key']!='row_id']
    start=1 if kind=='Single' else 3 if kind in {'1','0,1'} else 2
    if kind!='Single':
        for index,field in enumerate(fields,offset+1):
            if kind in {'1','0,1'}:write_cell(sheet,1,index,field['group_text'])
            cell=write_cell(sheet,start-1,index,field['header_text'])
            cell.font=Font(bold=True,color='FFFFFF');cell.fill=PatternFill('solid',fgColor='344657')
            sheet.column_dimensions[get_column_letter(index)].width=22
    for index_row,row in enumerate(rows):
        number=row_positions[index_row] if row_positions is not None else start+index_row
        for index,field in enumerate(fields,offset+1):
            column=offset+field['position'] if kind=='Single' else index
            write_cell(sheet,number,column,row.get(field['column_key']),decimals,legacy_rounding=legacy_rounding)
    sheet.freeze_panes='A'+str(start)
    return offset+(max((f['position'] for f in fields),default=0) if kind=='Single' else len(fields))


def source_workbook(source,rows,template=False,decimals=None):
    w=openpyxl.Workbook();sheet=w.active;sheet.title=source['sheet_name'][:31]
    block(sheet,source,source['columns'],[] if template else rows,decimals=decimals)
    if template:
        instructions=w.create_sheet('Instructions')
        instructions.append(['Source',source['data_table_name']]);instructions.append(['Unique key',source.get('unique_column')]);instructions.append(['Header layout',source['header_kind']]);instructions.append(['Modes','append; replace with explicit confirmation; upsert only with an unambiguous configured key'])
        instructions.append(['Column','Excel header','SQL type','Example (not imported)'])
        for f in source['columns']:
            if f['column_key']!='row_id':instructions.append([f['column_key'],f['header_text'],f['sql_type'],'006054959B91' if f['sql_type']=='VARCHAR(255)' else '1.000000'])
    output=BytesIO();w.save(output);return output.getvalue()

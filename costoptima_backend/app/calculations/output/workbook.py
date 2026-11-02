"""Generate a fresh development workbook from same-run results and named fields.

No old workbook, reference calculated value or Excel master range is read here.
The Diagnostics sheet always discloses unresolved records and parity status.
"""
from io import BytesIO
from copy import copy
from decimal import Decimal,InvalidOperation,ROUND_HALF_UP
from datetime import date,datetime
import re
import json
import math
from openpyxl import Workbook
from openpyxl.styles import Font,PatternFill,Alignment,Border,Side
from openpyxl.utils import get_column_letter
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from .layouts import LAYOUTS,MASTER_MATRIX,SBC_FOOTER
from .formulas import numeric_cell, same_number, sum_range, sumproducts, row_arithmetic, sum_row, rounded_equal, cell_reference, persist_numeric_caches
from ..common import get,normalized


def excel_value(value):
    if isinstance(value,Decimal):return float(value) if value.is_finite() else str(value)
    if isinstance(value,float) and not math.isfinite(value):return str(value)
    if isinstance(value,(dict,list,tuple)):return str(value)
    return value


def safe_text(cell,value):
    value=excel_value(value)
    if isinstance(value,str):value=ILLEGAL_CHARACTERS_RE.sub(lambda match: r'\x'+format(ord(match.group()),'02x'),value)
    cell.value=value
    # User identifiers/labels beginning '=' must never become executable formulas.
    if isinstance(value,str):cell.data_type='s'


def sheet_name(value,existing):
    value=re.sub(r'[\\/*?:\[\]]','_',str(value)).strip()[:31] or 'Data'
    candidate=value;number=2
    while candidate.casefold() in {n.casefold() for n in existing}:
        suffix='_'+str(number);candidate=value[:31-len(suffix)]+suffix;number+=1
    return candidate


def table(book,name,headers,rows,formats=None):
    ws=book.create_sheet(sheet_name(name,book.sheetnames));ws.freeze_panes='A2';ws.sheet_view.showGridLines=False
    # Reuse style definitions within this sheet. Each cell receives its own
    # StyleArray copy: later footer/detail overrides cannot affect other cells.
    header_style=None;data_styles={}
    for j,header in enumerate(headers,1):
        cell=ws.cell(1,j);safe_text(cell,str(header))
        if header_style is None:
            cell.fill=PatternFill('solid',fgColor='1F4E78');cell.font=Font(name='Calibri',size=10,bold=True,color='FFFFFF');cell.alignment=Alignment(wrap_text=True,vertical='center')
            header_style=copy(cell._style)
        else:cell._style=copy(header_style)
    ws.row_dimensions[1].height=32
    for i,row in enumerate(rows,2):
        for j,value in enumerate(row,1):
            cell=ws.cell(i,j);safe_text(cell,value)
            number_format=formats[j-1] if formats and j<=len(formats) else '#,##0.000' if isinstance(value,(int,float,Decimal)) else 'General'
            if isinstance(value,datetime):number_format='dd.mmm.yyyy hh:mm:ss'
            elif isinstance(value,date):number_format='dd.mmm.yyyy'
            style_key=(isinstance(value,str) and len(value)>30,number_format)
            if style_key not in data_styles:
                cell.font=Font(name='Calibri',size=9);cell.alignment=Alignment(vertical='top',wrap_text=style_key[0]);cell.number_format=number_format
                data_styles[style_key]=copy(cell._style)
            else:cell._style=copy(data_styles[style_key])
    for j,header in enumerate(headers,1):ws.column_dimensions[get_column_letter(j)].width=32 if any(s in str(header).casefold() for s in ('desc','issue','message','field')) else max(12,min(24,len(str(header))+3))
    if headers:ws.auto_filter.ref=f'A1:{get_column_letter(len(headers))}{max(1,len(rows)+1)}'
    return ws


def layout_key(row):
    c,t=row.commodity,row.type
    if row.dependency_only:t=str(get(row.inputs,'Type') or '').upper()
    elif str(row.inputs.get('_ownership_type',t)).upper()=='BUY NON STD':return 'BUY_NON_STD'
    elif str(row.inputs.get('_ownership_type',t)).upper()=='SPECIAL PROCESS':return 'HARD_SPECIAL_PROCESS'
    if c=='HARD':return 'HARD_'+t.replace(' ','_')
    if c=='SOFT':return 'SOFT'
    if not c:return t.replace(' ','_')
    return c.replace(' ','_')


def result_value(row,key,source=None,template=None):
    aliases={'BASIC Cost/Pc':'Basic Cost/Pc','RM Cost / Pc':'RM Cost/Pc','Total Markup':'MARKUP/Pc','Markup':'MARKUP/Pc',
             'ICC Cost':'ICC COST','REJ Cost':'REJ COST','OH Cost':'OH COST','PROFIT Cost':'PROFIT COST','Grand Total Cost':'Grand Total Cost/Pc',
             'O&H Cost':'OH COST','MARKUP total':'MARKUP/Pc','RM Cost':'RM Cost/Pc','Process Cost':'Process Cost/Pc',
             'RM Rate/UOM':'Rate/UOM','Rate Kg':'Material Rate','RM Cost / Pc':'RM Cost/Pc',
             'Basic Cost':'Basic Cost/Pc','Total/Pc':'Total Cost/Pc','Total Cost':'Total Cost/Pc','PnF':'PnF/Pc','MARKUP total':'MARKUP/Pc','Markup Total':'MARKUP/Pc','MARKUP':'MARKUP/Pc','Blank Markup':'Markup','BASIC Cost/Veh':'Basic Cost/Veh','Basic Cost/ pc':'Basic Cost/Pc','Basic cost/ pc':'Basic Cost/Pc','Total GDC & Process':'Process Cost/Pc'}
    field=aliases.get(key,key)
    explicit={"GDC Burning Loss %":"Burning Loss in %","GDC Burning Loss Kg":"Burning Loss in Kg",
      "GDC Gross Wt":"Gross Wt","GDC Material Rate":"Rate/UOM","GDC Scrap Rate":"Scrap Rate","GDC Scrap Wt":"Scrap Wt",
      "GDC Scrap Cost":"Scrap Cost","GDC Core Cost":"Core Cost","GDC De-coring Cost":"De coring cost",
      "GDC Casting Process Cost":"GDC Process cost","GDC Fettling Cost":"GDC-Fettling","GDC Shot Blasting Cost":"GDC-Shot Blasting",
      "GDC RM Cost/Pc":"RM Cost/Pc","GDC Process Cost":"Process Cost/Pc","GDC Basic Cost/Pc":"Basic Cost/Pc",
      "GDC Total Markup":"MARKUP/Pc","GDC Total":"Total","GDC Casting Cost":"GDC Cost","GDC RM Cost Before Scrap":"RM Cost with out scrap cost"}
    for label in ("ICC","Handling","REJ","OH","PROFIT"):explicit["GDC "+label+" Cost"]=label+" COST"
    prefixed={
      'PDC Burning Loss %':'Burning loss in %','PDC Burning Loss Kg':'Burning loss in Kg',
      'PDC Gross Wt':'GROSS WEIGHT','PDC Shot Wt':'SHOT WEIGHT','PDC Scrap Wt':'Scrap Weight',
      'PDC Melting Cost':'MELTING COST','PDC Machine Cost':'PDC cost',
      'PDC Fettling Cost':'PDC FETTLING','PDC Shot Blasting Cost':'SHOT BLASTING',
      'PDC Basic Process':'PDC Basic Process','PDC Conversion Cost':'PDC Conversion cost',
      'PDC Conversion/Kg':'PDC Conversion /Kg',
      'Thermal Material Rate':'Rate/UOM','Thermal PnF':'PnF/Pc','Thermal Total Cost':'Total Cost/Pc',
      'PCBA Markup/Pc':'PCBA Markup/Pc','PCBA Soldering Rate':'SOLDERING',
      'Sand Casting Scrap Rate/Kg':'Scrap Rate','Sand Casting Scrap Weight':'Scrap Wt'}
    field=explicit.get(key,prefixed.get(key,field))
    if key=='PCBA Basic Cost/Pc' and source=='result':return get(row.values,key)
    if field==key:
        for prefix in ('BUY NON STD ','BUY STD ','HARD ','SOFT ','PKG ','FRT ','FORGING ','PDC ','Thermal ','PCBA ','Sand Casting '):
            if key.startswith(prefix):
                named=key[len(prefix):]
                if named.endswith(' Rate') and named[:-5] in {'ICC','REJ','OH','PROFIT'}:named=named[:-5]
                field=aliases.get(named,named)
                break
    field={'BNS Make PnF/Pc':'PnF/Pc','BNS Make Grand Total Cost/Pc':'Grand Total Cost/Pc','Gross Length Derived':'L','Gross Width Derived':'W','Wastage':'% wastage','B Surface Area':"B' Surface area",'Consumables Cost':'Consumables','Considered con for RM cost':'Effective Gross Consumption','Material':'Material Family','Grade':'Material Grade'}.get(field,field)
    if row.commodity=='THERMAL' and key=='Rate Kg':field='Rate Kg'
    if template in {"SOFT","STAMPING","GDC","PDC","FORGING","SAND_CASTING"} and key in {"ICC","Rej","OH","Profit","Handling","Dies & Fixture Maint. cost"}:
        field={"ICC":"ICC COST","Rej":"REJ COST","OH":"OH COST","Profit":"PROFIT COST","Handling":"Handling COST","Dies & Fixture Maint. cost":"Dies COST"}[key]
    if key=='Date':
        # Primary soft887 / stamping692 replace their detail Date with fd2.
        # Casting, forging and thermal retain the actual detail input date;
        # BOM/Master_Output report date is projected separately below.
        if template in {'SOFT','STAMPING'} and get(row.inputs,'_report_datetime') is not None:
            return get(row.inputs,'_report_datetime')
        if template not in {'FORGING','GDC','PDC','SAND_CASTING','THERMAL'} and get(row.inputs,'_report_date') is not None:
            return get(row.inputs,'_report_date')
    if source in {'source','input','execution_date'}:
        if template=='HARD_MAKE' and key in {'RM Base','Sector & Location'} and get(row.values,key) is not None:
            # make_new14710-14725 displays the named first-MAKE selectors.
            return get(row.values,key)
        if key=='Supplier' and template in {'SOFT','STAMPING','FORGING','GDC','PDC','SAND_CASTING','THERMAL','HARD_MAKE','HARD_BUY_NON_STD'}:
            return get(row.inputs,'Costing Supplier') or get(row.inputs,'Supplier')
        if key=='Type' and not row.dependency_only:return row.inputs.get('_ownership_type',get(row.inputs,key))
        value=get(row.inputs,key)
        # Original HARD detail writes DB-derived family/grade when input omitted it.
        if value is None and key in {'Material','Grade','Material Family','Material Sub Family','Material Grade'}:return get(row.values,field)
        return value
    if source=='result':return get(row.values,field)
    value=get(row.values,field)
    return get(row.inputs,key) if value is None else value



# Original PCM master_output:15215-15470 selects columns from Master Output
# Matrix.xlsx by active output family, then sorts owners by End Item and Type.
def output_family(row):
    type_=str(row.inputs.get('_ownership_type',row.type) or '').strip().upper()
    commodity=str(row.commodity or '').strip().upper()
    if type_ in {'END ITEM','BUY NON STD'} or not commodity:return type_
    if commodity=='HARD':return commodity+' '+type_
    return commodity


def master_output(book,run,variants,diag,projections=None):
    families={output_family(row) for row in run.rows}
    if run.summaries.get('seat',{}).get('enabled'):families.add('SEAT')
    definitions=[];bom_prefix=True
    for source_item in MASTER_MATRIX:
        # PCM15127-15178 /15317-15346 filters selected families first, then
        # takes the consecutive BOM=Y prefix. Later Y flags are branch fields.
        if not any(source_item['mappings'].get(family) is not None for family in families):continue
        item={**source_item,'_from_bom':bool(bom_prefix and source_item['bom'])}
        if not source_item['bom']:bom_prefix=False
        header=item['header']
        if item['_from_bom'] and not (header=='Seat_Function' and run.summaries.get('seat',{}).get('enabled')) and not any(get(row.inputs,'Commodity' if header=='HARD / SOFT' else header) is not None or (header=='Seat_Function' and get(row.values,header) is not None) for row in run.rows):continue
        definitions.append(item)
    if not definitions:
        identity=['Record ID','Customer No','Supplier','Purchasing Supplier','Costing Supplier','End Item','Commodity','Type','Execution status']
        return table(book,'Master_Output',identity,[[row.record_id,row.inputs.get('Customer No'),row.inputs.get('Supplier'),
            row.inputs.get('Purchasing Supplier') or row.inputs.get('Supplier'),row.inputs.get('Costing Supplier') or row.inputs.get('Supplier'),
            row.inputs.get('End Item'),row.commodity,row.type,row.calculation_status] for row in run.rows])
    supplier_fields=['Purchasing Supplier','Costing Supplier'] if any(
        get(row.inputs,'Costing Supplier') not in (None,'') and normalized(get(row.inputs,'Costing Supplier'))!=normalized(get(row.inputs,'Supplier'))
        for row in run.rows) else []
    rows=[]
    for row in sorted(run.rows,key=lambda r:(str(get(r.inputs,'End Item') or ''),str(r.inputs.get('_ownership_type',r.type) or ''),str(r.inputs.get('_record_id',r.record_id)))):
        values=[]
        for item in definitions:
            header=item['header'];mapping=item['mappings'].get(output_family(row))
            if item['_from_bom']:
                key='Commodity' if header=='HARD / SOFT' else header
                value=row.inputs.get('_ownership_type',row.type) if key=='Type' else get(row.inputs,key)
                if key=='Date' and get(row.inputs,'_report_date') is not None:value=get(row.inputs,'_report_date')
                if key=='Seat_Function' and value in (None,''):value=get(row.values,key)
            elif mapping is None or not str(mapping).strip():value=None
            else:
                # The matrix's ICC/Rej/OH/Profit cells name supplier-sheet
                # amount columns, not the similarly named percentage inputs.
                cost_fields={'ICC Cost':'ICC COST','REJ Cost':'REJ COST','O&H Cost':'OH COST','PROFIT Cost':'PROFIT COST','Handling_Soft Cost':'Handling COST'}
                mapped=cost_fields.get(header,str(mapping)) if str(mapping).strip().casefold() not in {'markup'} else str(mapping)
                value=result_value(row,mapped,source='result' if row.status!='success' else None,template=layout_key(row))
                if value is None:diag.append([row.record_id,'Master_Output mapping unresolved: '+header+' <- '+str(mapping)])
            values.append(value)
        identities=[get(row.inputs,field) or get(row.inputs,'Supplier') for field in supplier_fields]
        rows.append(values+identities+[row.inputs.get('_variants',{}).get(v) for v in variants]+[row.calculation_status])
    ws=table(book,'Master_Output',['Commodity' if item['header']=='HARD / SOFT' and item['bom'] else item['header'] for item in definitions]+supplier_fields+variants+['Execution status'],rows)
    for j,item in enumerate(definitions,1):
        if item['header'] in {'Customer No','Supplier No','Material Code','PF Code'}:
            for row in ws.iter_rows(min_row=2,min_col=j,max_col=j):row[0].number_format='@'
    # Primary master_output15400-15463 links mapped values to the exact detail
    # row. Keep writer-owned identity bindings instead of repeating first-match
    # Customer No searches across suppliers or reusing any reference workbook.
    if projections:
        ordered=sorted(run.rows,key=lambda r:(str(get(r.inputs,'End Item') or ''),str(r.inputs.get('_ownership_type',r.type) or ''),str(r.inputs.get('_record_id',r.record_id))))
        for index,row in enumerate(ordered,2):
            sources=projections.get(row.record_id,[])
            if len(sources)!=1:
                if len(sources)>1:diag.append([row.record_id,'Master_Output detail formulas withheld: duplicate source-record identity.'])
                continue
            detail_sheet,detail_row,detail_columns=sources[0]
            for column,item in enumerate(definitions,1):
                if item['_from_bom']:continue
                mapping=item['mappings'].get(output_family(row))
                if not isinstance(mapping,str) or not mapping.strip():continue
                matches=[j for j,(label,*_) in enumerate(detail_columns,1)
                         if re.sub(r'\s+','',str(label)).casefold()==re.sub(r'\s+','',mapping).casefold()]
                cell=ws.cell(index,column);expected=numeric_cell(cell)
                if len(matches)==1 and expected is not None:
                    cell_reference(cell,detail_sheet.cell(detail_row,matches[0]),expected)
    return ws


def end_item_output(book,roots,variants):
    # end_item:10793-10811 and merge:17645-17722 retain all six owner fields.
    identity=['End Item','Supplier','Customer No','Desc','Type','Qty/Veh']
    rows=[[get(r.inputs,k) if k!='Type' else r.inputs.get('_ownership_type',r.type) for k in identity]+[r.values.get(v) if r.status=='success' else None for v in variants]+[r.calculation_status] for r in roots]
    ws=table(book,'End Item',identity+variants+['Execution status'],rows)
    for j,v in enumerate(variants,7):
        cell=ws.cell(len(roots)+4,j)
        safe_text(cell,sum((r.values[v] for r in roots),Decimal(0)) if all(r.status=='success' and r.values.get(v) is not None for r in roots) else None)
        if cell.value is not None:sum_range(cell,j,2,len(roots)+1,cell.value)
        cell.number_format='#,##0.00';cell.fill=PatternFill('solid',fgColor='FFC000')
    for label,width in [('A',15),('B',15),('C',20),('D',20)]:ws.column_dimensions[label].width=width
    return ws


def sbc_output(book,run,roots,variants,diag):
    # Original end_item:10815-10862 / merge:17556-17630 uses the uploaded
    # SBC row-2 semantic variant names above its row-1 display labels.
    layout=run.metadata.get('input',{}).get('sbc_layout') or run.metadata.get('sbc_layout') or {}
    identity=layout.get('identity_headers') or ['End Item','Customer No','Desc']
    configured=layout.get('variants') or [{'key':v,'label':v} for v in variants]
    if not layout:diag.append(['sbc_layout','Uploaded SBC display labels were unavailable; named BOM variant labels were retained.'])
    configured=[item for item in configured if item.get('key') in variants]
    selected=[]
    for row in roots:
        flags=[row.inputs.get('_variants',{}).get(item['key']) for item in configured]
        try:present=sum((Decimal(str(value)) for value in flags if value not in (None,'')),Decimal(0))!=0
        except (InvalidOperation,TypeError,ValueError):
            diag.append([row.record_id,'SBC variant selection contains a nonnumeric flag; root omitted.']);continue
        if present:selected.append(row)
    first_by_customer={}
    for row in roots:
        customer=str(get(row.inputs,'Customer No') or '').casefold()
        if customer in first_by_customer:diag.append([row.record_id,'SBC preserves original first Customer No match; duplicate owner identifier requires review.'])
        first_by_customer.setdefault(customer,row)
    rows=[]
    for row in selected:
        producer=first_by_customer[str(get(row.inputs,'Customer No') or '').casefold()]
        rows.append([get(row.inputs,key) for key in identity]+[producer.values.get(item['key']) if producer.status=='success' else None for item in configured]+[producer.calculation_status])
    ws=table(book,'SBC',identity+[str(item.get('label') or item['key']) for item in configured]+['Execution status'],rows)
    ws.insert_rows(1);ws.freeze_panes='D3';ws.row_dimensions[1].height=15
    for j,item in enumerate(configured,len(identity)+1):safe_text(ws.cell(1,j),item['key'])
    ws.auto_filter.ref=f'A2:{get_column_letter(len(identity)+len(configured))}{max(2,len(selected)+2)}'
    total_row=len(selected)+5
    for j,item in enumerate(configured,len(identity)+1):
        values=[first_by_customer[str(get(r.inputs,'Customer No') or '').casefold()] for r in selected]
        total=sum((r.values[item['key']] for r in values),Decimal(0)) if all(r.status=='success' and r.values.get(item['key']) is not None for r in values) else None
        safe_text(ws.cell(total_row,j),total)
        if total is not None:sum_range(ws.cell(total_row,j),j,3,len(selected)+2,total)
        ws.cell(total_row,j).fill=PatternFill('solid',fgColor='FFC000');ws.cell(total_row,j).number_format='#,##0.00'
    for j in range(1,len(identity)+len(configured)+1):ws.column_dimensions[get_column_letter(j)].width=25 if j<=3 else 11
    thin=Side(style='thin',color='000000')
    for offset,values in enumerate(SBC_FOOTER):
        for j,value in enumerate(values,1):
            cell=ws.cell(len(selected)+9+offset,j);safe_text(cell,value)
            cell.font=Font(name='Calibri',size=8);cell.alignment=Alignment(vertical='center',wrap_text=True)
            cell.border=Border(top=thin,bottom=thin,left=thin,right=thin)
            if offset in (0,10):cell.fill=PatternFill('solid',fgColor='F79646')
    return ws


def analysis_output(book,run,variants,diag):
    # Original Analysis:11208,11255-11261,11939-11985. These categories
    # have different producers; generic totals must never be relabelled as them.
    categories=['RM cost','Process cost','Buy Std','Buy Non Std','Assy','Special Process','Markup','RM cost Soft','Process cost Soft','Markup Soft','PCBA','Machine Scrap Recovery','Other6','PnF','Total']
    breakup=['ICC Cost','REJ Cost','O&H Cost','PROFIT Cost']
    series={item['variant']:item for item in run.summaries.get('legacy_analysis',[])}
    data=[[category]+[series.get(v,{}).get('values',{}).get(category) for v in variants] for category in categories]
    ws=table(book,'Analysis',['Cost Driver']+variants,data)
    ws.column_dimensions['A'].width=30
    safe_text(ws.cell(23,1),'Extra Information for 3DX  format suitability , already included in above table.')
    ws.cell(23,1).font=Font(name='Calibri',size=10,bold=True,color='FF0000')
    for i,label in enumerate(breakup,25):
        safe_text(ws.cell(i,1),label)
        for j,v in enumerate(variants,2):safe_text(ws.cell(i,j),series.get(v,{}).get('values',{}).get(label))
    # Original Analysis templates leave A18/A19 blank; the numeric and
    # Boolean reconciliation cells remain in the variant columns.
    roots=[row for row in run.rows if normalized(row.type)=='end item']
    for column,variant in enumerate(variants,2):
        amounts=[get(row.values,variant) for row in roots]
        complete=bool(roots) and all(row.calculation_status=='CALCULATED' for row in roots) and all(value is not None for value in amounts)
        total=sum((Decimal(str(value)) for value in amounts),Decimal(0)) if complete else None
        safe_text(ws.cell(18,column),total)
        calculated=series.get(variant,{}).get('values',{}).get('Total')
        safe_text(ws.cell(19,column),None if total is None or calculated is None else Decimal(str(calculated)).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)==total.quantize(Decimal('.01'),rounding=ROUND_HALF_UP))
        # Original11980-11985: the displayed category total is a local
        # SUM and the reconciliation links the current End Item total, rather
        # than leaving static snapshots in cells that Excel users can edit.
        has_total = sum_range(ws.cell(16,column),column,2,15,calculated)
        linked = False
        if 'End Item' in book.sheetnames and total is not None:
            owner_sheet=book['End Item']
            matches=[cell.column for cell in owner_sheet[1] if cell.value==variant]
            if len(matches)==1:
                linked=cell_reference(ws.cell(18,column),owner_sheet.cell(len(roots)+4,matches[0]),total)
        if has_total or linked:
            rounded_equal(ws.cell(19,column),ws.cell(16,column),ws.cell(18,column),ws.cell(19,column).value)
    for j in range(2,len(variants)+2):ws.cell(16,j).fill=PatternFill('solid',fgColor='FFC000')
    if not series:diag.append(['analysis_layout','Original category rows restored; legacy category producers are not available for this run. Generic totals are confined to Calculation Overview.'])
    for v,item in series.items():
        if item.get('status')!='success':diag.append(['analysis:'+str(v),'Legacy category totals incomplete: '+str(item.get('blocked_records',[]))])
    generic=run.summaries.get('Analysis',[]);keys=['RM cost','Process cost','Markup','PnF']
    table(book,'Calculation Overview',['Variant']+keys+['Execution status'],[[a['variant']]+[a.get('values',{}).get(k) for k in keys]+[a['status']] for a in generic])
    return ws



def pcba_body_formulas(ws,quantity_headers):
    """Verified local original arithmetic for the existing component projection."""
    headers={cell.value:cell.column for cell in ws[1]}
    # Original pcba_op14092-14097/14140-14152: restore local body arithmetic,
    # while all external VLOOKUP results remain same-run database values.
    arithmetic=[('Landed Cost/Pc',['FOB','EXCHANGE','Landing Factor'],'*'),
        ('Process Cost',['Solder Points','SOLDERING'],'*'),
        ('Basic Cost/Pc',['Landed Cost/Pc','Process Cost'],'+'),
        ('Total Cost',['Basic Cost/Pc','MARKUP total'],'+')]
    for row_index in range(2,ws.max_row+1):
        for target,operands,operation in arithmetic[:3]:
            cell=ws.cell(row_index,headers[target])
            row_arithmetic(cell,[headers[name] for name in operands],operation,cell.value)
        cell=ws.cell(row_index,headers['MARKUP total'])
        sum_row(cell,headers['ICC COST'],headers['PROFIT COST'],cell.value)
        target,operands,operation=arithmetic[-1];cell=ws.cell(row_index,headers[target])
        row_arithmetic(cell,[headers[name] for name in operands],operation,cell.value)
        cell=ws.cell(row_index,headers['Grand Total Cost'])
        sum_row(cell,headers['Total Cost'],headers['PnF'],cell.value)
        for name in quantity_headers:
            cell=ws.cell(row_index,headers[name+' Cost/Veh'])
            row_arithmetic(cell,[headers[name],headers['Grand Total Cost']],'*',cell.value)


def pcba_component_output(book,run,diag):
    # Original pcba_op13989-14026 and14148-14152. Each output row remains a
    # component; board aggregation is not substituted for component unit costs.
    boards=[];seen=set()
    for candidate in [*getattr(run,'producer_rows',[]),*run.rows]:
        identity=candidate.inputs.get('_record_id',candidate.record_id)
        if (candidate.commodity=='PCBA' and (candidate.values.get('_pcba_component_results') or candidate.inputs.get('_pcba_components'))
                and identity not in seen):
            boards.append(candidate);seen.add(identity)
    if not boards:return
    base=['Supplier','End Item','Customer No','Material Code']
    fields=['Material Family','Material Sub Family','Material Grade','RM Supplier','FOB','RM Base','CURRENCY','Landing Factor','EXCHANGE','Landed Cost/Pc','Solder Points','SOLDERING','Process Cost','Basic Cost/Pc','ICC','REJ','OH','PROFIT','ICC COST','REJ COST','OH COST','PROFIT COST','MARKUP total','Total Cost','PnF','Grand Total Cost']
    quantity_headers=list(dict.fromkeys(name for board in boards for name in board.inputs.get('_pcba_quantity_headers',[])))
    rows=[]
    for board in boards:
        inputs=board.inputs.get('_pcba_components',[])
        results=board.values.get('_pcba_component_results',[])
        count=max(len(inputs),len(results))
        indexed={component.get('component_index',index):component for index,component in enumerate(results)}
        for index in range(count):
            original=inputs[index] if index<len(inputs) else {}
            component=indexed.get(index,{'Material Code':get(original,'Material Code'),'values':{}})
            calculated=component.get('values',{})
            values=[get(original,'Supplier') or get(board.inputs,'Supplier'),get(original,'End Item') or get(board.inputs,'End Item'),get(board.inputs,'Customer No'),component.get('Material Code',get(original,'Material Code'))]
            for field in fields:
                value=get(board.values,'RM Base',get(board.inputs,'RM Base')) if field=='RM Base' else get(calculated,field)
                if value is None:diag.append([board.record_id,'PCBA_OP component mapping unresolved: '+field])
                values.append(value)
            for name in quantity_headers:values.append(get(original,name))
            for name in quantity_headers:
                raw=get(original,name);cost=get(calculated,'Grand Total Cost')
                if cost is None:values.append(None)
                elif raw in (None,''):values.append(0)
                else:
                    try:values.append(Decimal(str(raw))*Decimal(str(cost)))
                    except (InvalidOperation,TypeError,ValueError):values.append(None);diag.append([board.record_id,'PCBA_OP invalid component quantity: '+name])
            rows.append(values)
    ws=table(book,'PCBA_OP',base+fields+quantity_headers+[name+' Cost/Veh' for name in quantity_headers],rows)
    pcba_body_formulas(ws,quantity_headers)


def end_item_analysis_output(book,run,variants,diag):
    """Original12091 category rows and owner blocks at12181-12193."""
    categories=['RM cost','Process cost','Buy Std','Buy Non Std','PCBA','Assy','Special Process','Markup','Machine Scrap Recovery','PnF','Total']
    series=run.summaries.get('end_item_analysis',[])
    owners=list(dict.fromkeys(str(item['End Item']) for item in series))
    ws=book.create_sheet(sheet_name('End Item Analysis',book.sheetnames));ws.sheet_view.showGridLines=False;ws.freeze_panes='B3'
    for index,owner in enumerate(owners):
        start=index*(len(variants)+2)+1
        safe_text(ws.cell(1,start+1),owner);safe_text(ws.cell(2,start),'Cost Driver')
        ws.column_dimensions[get_column_letter(start)].width=30
        for j,variant in enumerate(variants,start+1):safe_text(ws.cell(2,j),variant)
        for row,label in enumerate(categories,3):safe_text(ws.cell(row,start),label)
        safe_text(ws.cell(15,start),'Extra Information for 3DX  format suitability , already included in above table.')
        ws.cell(15,start).font=Font(color='FF0000',bold=True,size=10)
        for row,label in enumerate(['ICC Cost','REJ Cost','O&H Cost','PROFIT Cost'],17):safe_text(ws.cell(row,start),label)
        for j,variant in enumerate(variants,start+1):
            result=next((item for item in series if str(item['End Item'])==owner and item['variant']==variant),None)
            values=result.get('values',{}) if result else {}
            if not result or result['status']!='success':diag.append(['end_item_analysis:'+owner+':'+variant,'Owner category totals incomplete; no values filled from failed producers.'])
            for row,label in [*enumerate(categories,3),*enumerate(['ICC Cost','REJ Cost','O&H Cost','PROFIT Cost'],17)]:
                cell=ws.cell(row,j);safe_text(cell,values.get(label));cell.number_format='#,##0.00'
            ws.cell(13,j).fill=PatternFill('solid',fgColor='FFC000')
        for j in range(start,start+len(variants)+1):
            ws.cell(2,j).fill=PatternFill('solid',fgColor='1F4E78');ws.cell(2,j).font=Font(color='FFFFFF',bold=True,size=9)
    return ws



def thermal_columns(make):
    """Exact primary hvac15520-15575/make_cols15609-15674 order."""
    identity=['PF Code','Project','Part Name','BC / SBC / TKO / RoCM','Date','Estimation Revision','0 / 1 / 2 / 3','Supplier','Function','End Item','Supplier No','Customer No','Desc','2D DWG/CAD Revision No','Type','Qty/Veh']
    if make:identity+=['Qty for Fin Height Calc']
    fields=[(name,name,'input','General') for name in identity+['Material Code']]
    fields += [(name,name,'result','General') for name in ['Material Sub Family','Material Grade']]
    fields += [(name,name,'input','General') for name in ['Color','L','W','H','thk']]
    if make:
        # Input's Flow type becomes the original named output Flowtype.
        fields.insert(next(i for i,item in enumerate(fields) if item[0]=='L'),('Flowtype','Flowtype','result','General'))
        fields += [(name,name,'input','General') for name in ['Tube Height (Post Forming)','FPDM']]
        fields += [(name,name,'result','General') for name in ['Tube Pitch','Blank Length Allowance','Blank Width Allowance','Input Blank Length','Input Blank Width','Height/100','Fin Height','Fin Qty','Fin Length']]
    else:fields += [('Part Wt','Part Wt','input','General')]
    fields += [('Gross Wt','Gross Wt','result','General'),('RM Base','RM Base','input','General')]
    fields += [(name,name,'result','General') for name in ['Rate Kg','RM Cost','Core Coversion Cost Rate','Conversion Cost','Process Cost','BASIC Cost/Pc','ICC','REJ','OH','PROFIT','ICC COST','REJ COST','OH COST','PROFIT COST','MARKUP total','Total Cost']+(['Total Cost/Veh'] if make else ['PnF','Grand Total Cost'])]
    return fields



def paint_component_output(book,run):
    """Primary paint612-634/merge19362-19421: component lines plus anchor totals."""
    owners=[row for row in run.rows if row.values.get('_paint_result')]
    if not owners:return
    headers=['Sr','Supplier','End Item','Make Part no','Customer No','Desc','Qty/Veh','L','W','H','thk','Finish','Painting Area (in SQM)','Paint System','Material Code','Material','Consumption (in Litre)','No of parts / Jig','Make Part Cost','RM Base','Rate / Litre','RM cost / Pc','Consumable / Masking Cost','Hanger Cost','Process Cost/Pc','Paint RM Cost','Paint Process Cost','Basic Cost','ICC','REJ ON INJ MOLDED PART','REJ ON MATERIAL & PROCESS','OVERALL REJECTION','OVERHEAD','PROFIT','ICC Cost','REJ ON INJ MOLDED PART Cost','REJ ON MATERIAL & PROCESS Cost','OVERALL REJECTION Cost','OVERHEAD Cost','PROFIT Cost','Paint Markup','Pnf','Total Cost/Pc']
    rows=[];seen=set()
    for owner in owners:
        paint=owner.values['_paint_result'];components=paint.get('_paint_component_results',[])
        if not components:continue
        anchor=components[0]['inputs'].get('_record_id')
        if anchor in seen:continue
        seen.add(anchor)
        for index,component in enumerate(components):
            inputs=component['inputs'];values=[]
            for header in headers:
                if header in {'Rate / Litre','RM cost / Pc','Consumable / Masking Cost'}:value=component.get(header)
                elif header=='Make Part Cost':value=paint.get(header) if index==0 else None
                elif header in headers[23:]:
                    key={'Process Cost/Pc':'Paint Process Cost','Pnf':'Paint PnF','Total Cost/Pc':'Paint Total Cost/Pc'}.get(header,header)
                    value=paint.get(key) if index==0 else None
                else:value=get(inputs,header)
                values.append(value)
            rows.append(values)
    table(book,'Paint',headers,rows)


def stamping_columns(columns,rows):
    """stamping668-759 keeps optional-consumption projection supplier-wide."""
    optional=any(r.values.get('_stamping_optional_consumption') for r in rows)
    conditional={'Blank calculation','Blank Length Allowance','Blank Width Allowance','Cal Blank Cons','Cal Parts Per Blank','Gross cal Method','STD Gross factor','L','W','Cal parts / Sheet','Cal Gross Consumption','Considered con for RM cost','Cal Yield','Total Surface Area',"B' Surface area"}
    only_if_input={'Blank Length','Blank Width','Blank Consumption','No of parts / Blank','No of parts / Sheet'}
    result=[]
    for column in columns:
        label=column[0]
        if optional and label in conditional:continue
        if label in only_if_input and not any(any(normalized(k)==normalized(column[1]) for k in r.inputs) for r in rows):continue
        result.append(column)
    return result


def detail_columns(columns,rows):
    """Add the actual consumable triplets before scrap, not one generic total."""
    lines=[line for row in rows for line in row.values.get('_consumable_lines',[])]
    names=list(dict.fromkeys(str(line.get('name') or line.get('grade')) for line in lines if line.get('name') or line.get('grade')))
    dynamic=[(label,name,'consumable_'+kind,'General') for name in names for kind,label in [('name',name),('rate',name+' Rate'),('cost',name+' Cost')]]
    position=next((i for i,c in enumerate(columns) if c[0]=='Scrap Rate'),len(columns))
    return columns[:position]+dynamic+columns[position:]


def detail_value(row,key,source,template):
    if source.startswith('consumable_'):
        lines=[line for line in row.values.get('_consumable_lines',[]) if str(line.get('name') or line.get('grade'))==key]
        if not lines:return None
        kind=source[len('consumable_'):]
        if kind=='name':return key
        values=[line.get(kind) for line in lines]
        if any(v is None for v in values):return None
        if kind=='rate':return values[0] if all(v==values[0] for v in values) else None
        return sum((Decimal(str(v)) for v in values),Decimal(0))
    return result_value(row,key,source,template)


def detail_rates(sheet,columns,rows,key,diag):
    """Original supplier row1 is rate metadata, row2 contains cost headings."""
    percentages={'ICC':'ICC','Rej':'REJ','REJ':'REJ','OH':'OH','Profit':'PROFIT','PROFIT':'PROFIT','Handling':'Handling','Dies & Fixture Maint. cost':'Dies & Fixture Maint. cost'}
    for j,(label,field,source,_) in enumerate(columns,1):
        rates=[]
        for row in rows:
            if row.status!='success':continue
            value=None
            if label in percentages:value=get(row.values,percentages[label])
            for line in row.values.get('_process_costs',[]):
                if normalized(line.get('name'))==normalized(label):value=line.get('rate')
            if value is None:value=get(row.values.get('_master_rates',{}),label)
            if value is not None:rates.append(value)
        if rates and all(v==rates[0] for v in rates):
            safe_text(sheet.cell(1,j),rates[0]);sheet.cell(1,j).number_format='0.00%' if label in percentages else '#,##0.000'
        elif rates:diag.append([sheet.title,'Rate row omitted for '+label+': records use different verified rates.'])


def _footer_amount_columns(columns,rows,fields,key):
    """Translate known summary result fields to their displayed amount columns.

    Headings such as HARD ICC are rates, while SOFT/GDC ICC are amounts.
    Only explicit source-backed headings and independently matching row amounts
    qualify; an unrelated column with the same total is never selected.
    """
    labels={
        'RM Cost/Pc':{'RM Cost/Pc','RM Cost / Pc','RM Cost'},
        'Process Cost/Pc':{'Process Cost/Pc','Process Cost'},
        'MARKUP/Pc':{'MARKUP/Pc','Total Markup'},
        'Total':{'Total'},'Total Cost/Pc':{'Total Cost/Pc'},
        'ICC COST':{'ICC COST','ICC Cost'},'REJ COST':{'REJ COST','REJ Cost'},
        'OH COST':{'OH COST','OH Cost'},'PROFIT COST':{'PROFIT COST','PROFIT Cost'},
        'Handling COST':{'Handling'},'Dies COST':{'Dies & Fixture Maint. cost'}}
    if key in {'SOFT','STAMPING','GDC'}:
        for field,label in [('ICC COST','ICC'),('REJ COST','Rej'),('OH COST','OH'),('PROFIT COST','Profit')]:
            labels[field].add(label)
    selected=[]
    for field in fields:
        candidates=[]
        for index,(label,source_field,source,_) in enumerate(columns,1):
            if source!='result' or label not in labels.get(field,set()):continue
            if all(same_number(detail_value(row,source_field,source,key),get(row.values,field)) for row in rows):
                candidates.append(index)
        if len(candidates)!=1:return None
        selected.append(candidates[0])
    return selected


def detail_footer(sheet,columns,rows,variants,key,diag):
    """Source SUMPRODUCT summaries evaluated from named results (not masters).

    Physical placement matches part1:3890-3946, part1_stamp:6451-6494,
    make_new:15006-15026 and gdc_casting:9233-9266. A blocked contribution
    leaves its total blank; neither missing inputs nor failed results become zero.
    """
    if key=='BUY_NON_STD' and variants:
        # Original merge18930-18985: category, markup, PCBA deduction and
        # independent total reconciliation, followed by four cost breakups.
        label_col=len(columns);base=len(rows)
        specs=[(4,'Buy Non Std','Basic Cost/Pc'),(5,'Markup','MARKUP/Pc'),
               (7,'Buy Non Std PCBA','PCBA Basic Cost/Pc'),(14,'ICC Cost','ICC COST'),
               (15,'REJ Cost','REJ COST'),(16,'O&H Cost','OH COST'),(17,'PROFIT Cost','PROFIT COST')]
        for offset,label,field in specs:
            safe_text(sheet.cell(base+offset,label_col),label)
            for column,variant in enumerate(variants,len(columns)+1):
                total=Decimal(0);failed=False
                for row in rows:
                    value=get(row.values,field)
                    if field=='PCBA Basic Cost/Pc' and row.commodity!='PCBA':value=Decimal(0)
                    raw=[value,get(row.inputs,'Qty/Veh'),row.inputs.get('_variants',{}).get(variant)]
                    if row.calculation_status!='CALCULATED' or any(v is None for v in raw):failed=True;break
                    try:total+=Decimal(str(raw[0]))*Decimal(str(raw[1]))*Decimal(str(raw[2] or 0))
                    except (InvalidOperation,ValueError,TypeError):failed=True;break
                safe_text(sheet.cell(base+offset,column),None if failed else total)
        safe_text(sheet.cell(base+4,19),'End Item Supplier')
        safe_text(sheet.cell(base+5,19),'Markup')
        suppliers=list(dict.fromkeys(get(row.values,'End Item Supplier') for row in rows if get(row.values,'End Item Supplier') is not None))
        safe_text(sheet.cell(base+4,20),suppliers[0] if len(suppliers)==1 else None)
        rates=list(dict.fromkeys(get(row.values,'Purchasing Markup Rate') for row in rows if get(row.values,'Purchasing Markup Rate') is not None))
        safe_text(sheet.cell(base+5,20),rates[0] if len(rates)==1 and len(suppliers)==1 else None)
        sheet.cell(base+5,20).number_format='0.00%'
        if len(suppliers)>1 or len(rates)>1:diag.append([sheet.title,'Multiple End Item suppliers/markup rates: legacy single-supplier footer metadata withheld; row calculations remain owner-scoped.'])
        safe_text(sheet.cell(base+13,label_col),'Extra Information for 3DX  format suitability , already included in above table.')
        sheet.cell(base+13,label_col).font=Font(name='Calibri',size=10,bold=True,color='FF0000')
        safe_text(sheet.cell(base+6,label_col),'Total')
        safe_text(sheet.cell(base+9,label_col),'Calculated Total')
        safe_text(sheet.cell(base+10,label_col),'Reconciliation')
        for column,variant in enumerate(variants,len(columns)+1):
            basic=numeric_cell(sheet.cell(base+4,column));markup=numeric_cell(sheet.cell(base+5,column));pcba=numeric_cell(sheet.cell(base+7,column))
            if basic is not None and pcba is not None:safe_text(sheet.cell(base+4,column),basic-pcba)
            total=None if any(v is None for v in (basic,markup,pcba)) else basic+markup-pcba
            safe_text(sheet.cell(base+6,column),total)
            parts=[]
            for row in rows:
                final=get(row.values,'Total/Pc');qty=get(row.inputs,'Qty/Veh');flag=row.inputs.get('_variants',{}).get(variant)
                if row.calculation_status!='CALCULATED' or any(v is None for v in (final,qty,flag)):break
                parts.append(Decimal(str(final))*Decimal(str(qty))*Decimal(str(flag or 0)))
            check=sum(parts,Decimal(0))-pcba if len(parts)==len(rows) and pcba is not None else None
            safe_text(sheet.cell(base+9,column),check)
            safe_text(sheet.cell(base+10,column),None if total is None or check is None else total.quantize(Decimal('.01'),rounding=ROUND_HALF_UP)==check.quantize(Decimal('.01'),rounding=ROUND_HALF_UP))
        # Only the verified original 30-column projection can own these
        # formulas. A missing/partial amount remains blank and never becomes an
        # Excel IFERROR(...,0). External master VLOOKUPs remain DB values.
        expected_labels={8:'Qty/Veh',19:'Basic Cost/Pc',20:'PCBA Basic Cost/Pc',
            25:'ICC Cost',26:'REJ Cost',27:'O&H Cost',28:'PROFIT Cost',29:'Markup',30:'Total/Pc'}
        if len(columns)==30 and all(columns[index-1][0]==label for index,label in expected_labels.items()):
            for row_number in range(2,base+2):
                for cost,rate in [(25,21),(26,22),(27,23),(28,24)]:
                    row_arithmetic(sheet.cell(row_number,cost),[19,rate],'*',numeric_cell(sheet.cell(row_number,cost)))
                row_arithmetic(sheet.cell(row_number,30),[19,29],'+',numeric_cell(sheet.cell(row_number,30)))
            for column in range(len(columns)+1,len(columns)+len(variants)+1):
                pcba_cell=sheet.cell(base+7,column)
                sumproducts(pcba_cell,[20],8,column,2,base+1,numeric_cell(pcba_cell),blank_columns=(20,))
                for offset,amount_column in [(4,19),(5,29),(9,30),(14,25),(15,26),(16,27),(17,28)]:
                    cell=sheet.cell(base+offset,column)
                    sumproducts(cell,[amount_column],8,column,2,base+1,numeric_cell(cell),
                        subtract=pcba_cell if offset in {4,9} else None)
                cell=sheet.cell(base+6,column)
                sum_range(cell,column,base+4,base+5,numeric_cell(cell))
                cell=sheet.cell(base+10,column)
                rounded_equal(cell,sheet.cell(base+6,column),sheet.cell(base+9,column),cell.value)
        return
    if key in {'PKG','FRT'} and variants:
        # Primary merge19153-19178 /19208-19234: Pnf is a variant
        # SUMPRODUCT of Basic Cost, Qty/Veh and the variant flag, not a rate.
        labels=[column[0] for column in columns]
        label_col=labels.index('Markup')+1 if 'Markup' in labels else len(columns)
        line=len(rows)+4
        safe_text(sheet.cell(line,label_col),'Pnf')
        for column,variant in enumerate(variants,len(columns)+1):
            total=Decimal(0);failed=False
            for row in rows:
                raw=[get(row.values,'Basic Cost/Pc'),get(row.inputs,'Qty/Veh'),row.inputs.get('_variants',{}).get(variant)]
                if row.calculation_status!='CALCULATED' or any(value is None for value in raw):failed=True;break
                try:total+=Decimal(str(raw[0]))*Decimal(str(raw[1]))*Decimal(str(raw[2] or 0))
                except (InvalidOperation,ValueError,TypeError):failed=True;break
            cell=sheet.cell(line,column);safe_text(cell,None if failed else total)
            cell.number_format='#,##0.00';cell.fill=PatternFill('solid',fgColor='FFC000')
            if failed:diag.append([sheet.title+':'+variant,'Incomplete Pnf footer: missing/blocked contribution.'])
            else:
                amounts=[index for index,item in enumerate(columns,1) if item[0]=='Basic Cost']
                quantities=[index for index,item in enumerate(columns,1) if item[0]=='Qty/Veh']
                if len(amounts)==len(quantities)==1:
                    sumproducts(cell,amounts,quantities[0],column,2,len(rows)+1,total)
        return
    if key not in {'SOFT','STAMPING','GDC','PDC','HARD_MAKE'} or not variants:return
    headers=[c[0] for c in columns];label_col=len(columns)
    quantity_columns=[j for j,column in enumerate(columns,1) if column[0]=='Qty/Veh']
    first_data=3 if sheet.freeze_panes=='A3' else 2
    if key in {'SOFT','STAMPING'}:
        specifications=[(6,'RM Cost',('RM Cost/Pc',)),(7,'Process Cost',('Process Cost/Pc',)),(8,'Markup',('MARKUP/Pc',)),(11,'Calculated Total',('Total',)),(16,'ICC Cost',('ICC COST',)),(17,'REJ Cost',('REJ COST',)),(18,'O&H Cost',('OH COST','Handling COST' if key=='SOFT' else 'Dies COST')),(19,'PROFIT Cost',('PROFIT COST',))]
    elif key=='HARD_MAKE':
        specifications=[(5,'RM Cost',('RM Cost/Pc',)),(6,'Process Cost',('Process Cost/Pc',)),(7,'Markup',('MARKUP/Pc',)),(10,'Calculated Total',('Total Cost/Pc',)),(14,'ICC Cost',('ICC COST',)),(15,'REJ Cost',('REJ COST',)),(16,'O&H Cost',('OH COST',)),(17,'PROFIT Cost',('PROFIT COST',))]
    else:
        specifications=[(7,'RM Cost',('RM Cost/Pc',)),(8,'Process Cost',('Process Cost/Pc',)),(9,'Markup',('MARKUP/Pc',)),(10,'Total',('Total',)),(13,'ICC Cost',('ICC COST',)),(14,'REJ Cost',('REJ COST',)),(15,'O&H Cost',('OH COST',)),(16,'PROFIT Cost',('PROFIT COST',))]
    for offset,label,fields in specifications:
        line=len(rows)+offset;safe_text(sheet.cell(line,label_col),label)
        for j,variant in enumerate(variants,len(columns)+1):
            total=Decimal(0);failed=False
            for row in rows:
                flag=row.inputs.get('_variants',{}).get(variant)
                qty=get(row.inputs,'Qty/Veh')
                amounts=[get(row.values,f) for f in fields]
                if row.status!='success' or qty is None or flag is None or any(a is None for a in amounts):failed=True;break
                try:total+=sum((Decimal(str(a)) for a in amounts),Decimal(0))*Decimal(str(qty))*Decimal(str(flag))
                except (InvalidOperation,ValueError,TypeError):failed=True;break
            cell=sheet.cell(line,j);safe_text(cell,None if failed else total);cell.number_format='#,##0.00'
            if failed:diag.append([sheet.title+':'+variant,'Incomplete '+label+' footer: missing/blocked contribution.'])
            elif len(quantity_columns)==1:
                amount_columns=_footer_amount_columns(columns,rows,fields,key)
                if amount_columns:sumproducts(cell,amount_columns,quantity_columns[0],j,first_data,first_data+len(rows)-1,total)
            if label in {'Total','Calculated Total'}:cell.fill=PatternFill('solid',fgColor='FFC000')
    if key in {'SOFT','STAMPING','HARD_MAKE'}:
        offset=9 if key in {'SOFT','STAMPING'} else 8
        line=len(rows)+offset;safe_text(sheet.cell(line,label_col),'Total')
        for j in range(len(columns)+1,len(columns)+len(variants)+1):
            offsets=[6,7,8] if key in {'SOFT','STAMPING'} else [5,6,7]
            values=[numeric_cell(sheet.cell(len(rows)+o,j)) for o in offsets]
            cell=sheet.cell(line,j);safe_text(cell,None if any(v is None for v in values) else sum(values))
            if cell.value is not None:sum_range(cell,j,len(rows)+offsets[0],len(rows)+offsets[-1],cell.value)
            cell.number_format='#,##0.00';cell.fill=PatternFill('solid',fgColor='FFC000')


def excel_tables(book):
    """Add tables only to the actual header/data range, excluding rate/footer rows."""
    from openpyxl.worksheet.table import Table,TableStyleInfo
    from openpyxl.utils.cell import range_boundaries
    for index,sheet in enumerate(book,1):
        ref=sheet.auto_filter.ref
        if not ref:continue
        a,header,b,end=range_boundaries(ref)
        if end<=header:continue
        labels=[sheet.cell(header,j).value for j in range(a,b+1)]
        if any(not isinstance(v,str) or not v for v in labels) or len({v.casefold() for v in labels})!=len(labels):continue
        tab=Table(displayName='PCM_'+str(index),ref=ref)
        tab.tableStyleInfo=TableStyleInfo(name='TableStyleMedium2',showRowStripes=False,showColumnStripes=False)
        sheet.add_table(tab)


def source_input_output(book,metadata,diag=None):
    """Original conditional family input sheets, safely reconstructed as data.

    PCM17741-18250 copies the selected family intermediates to final output.
    Input expressions remain strings through safe_text; cached values, dates,
    identifiers, source blanks and explicit number formats remain plain cells.
    No uploaded workbook package, hyperlink, macro or external reference is used.
    """
    from ..input import _INPUT_COPY_TARGETS, MAX_INPUT_COPY_CELLS, MAX_INPUT_COPY_RECTANGLE
    allowed={(item[0],item[1]) for item in _INPUT_COPY_TARGETS.values()}
    if diag is None:diag=[]
    active={str(branch.get('commodity') or '').strip().upper() for branch in metadata.get('branch_counts',[])}
    from collections import Counter
    copies=metadata.get('input_sheet_copies',[])
    eligible=lambda item: bool(item.get('always_include')) or ((item.get('family'),item.get('title')) in allowed and item.get('family') in active)
    target_counts=Counter(item.get('title') for item in copies if eligible(item))
    consumed=set();count=0
    for copied in copies:
        family,title=copied.get('family'),copied.get('title')
        if not eligible(copied):continue
        if not isinstance(title,str) or len(title)>31 or re.search(r'[\\/*?:\[\]]',title):continue
        if target_counts[title]!=1 or title.casefold() in consumed or title.casefold() in {name.casefold() for name in book.sheetnames}:
            diag.append(['input_copy:'+str(copied.get('source_sheet')),
                         'Several source input sheets target the same original output name; no arbitrary copy was selected.'])
            continue
        rows,columns,header=copied.get('rows'),copied.get('columns'),copied.get('export_header_row')
        cells=copied.get('cells',[])
        if (not all(isinstance(value,int) and not isinstance(value,bool) for value in (rows,columns,header))
                or not 1<=header<=rows or not 1<=columns<=2048 or rows>100000
                or rows*columns>MAX_INPUT_COPY_RECTANGLE or count+len(cells)>MAX_INPUT_COPY_CELLS):
            diag.append(['input_copy:'+str(copied.get('source_sheet')),'Input reference copy exceeds verified output bounds.'])
            continue
        ws=book.create_sheet(title);ws.sheet_view.showGridLines=False
        ws.freeze_panes='A'+str(header+1)
        for row,column,value,number_format in cells:
            if not (isinstance(row,int) and isinstance(column,int) and 1<=row<=rows and 1<=column<=columns):
                raise ValueError('Input copy cell is outside verified worksheet bounds')
            cell=ws.cell(row,column);safe_text(cell,value)
            cell.number_format=number_format if isinstance(number_format,str) and len(number_format)<=255 else 'General'
            cell.font=Font(name='Calibri',size=9)
            cell.alignment=Alignment(vertical='top')
        for j in range(1,columns+1):
            cell=ws.cell(header,j)
            cell.fill=PatternFill('solid',fgColor='1F4E78');cell.font=Font(name='Calibri',size=10,bold=True,color='FFFFFF')
            cell.alignment=Alignment(wrap_text=True,vertical='center')
            ws.column_dimensions[get_column_letter(j)].width=20 if normalized(cell.value) in {'desc','customer no'} else 15
        ws.row_dimensions[header].height=32
        ws.auto_filter.ref=f'A{header}:{get_column_letter(columns)}{rows}'
        count+=len(cells);consumed.add(title.casefold())
        diag.append(['input_copy:'+str(copied.get('source_sheet')),
                     'Original '+title+' input projection restored; uploaded expressions are inert and never used as master values.'])



def _diagnostic_value(value):
    """Plain bounded diagnostic text; never an executable Excel expression."""
    if value is None:return None
    if isinstance(value,(dict,list,tuple)):
        return json.dumps(value,default=str,ensure_ascii=False)[:32000]
    return value if not isinstance(value,str) else value[:32000]


def _safe_diagnostic_fields(values):
    return {key:value for key,value in values.items() if not str(key).startswith('_') and
            not any(word in normalized(key) for word in ('password','token','cookie','secret','assertion','authorization','encryption'))}


def calculation_diagnostics(book,run):
    """One row ledger plus field-level evidence; partials never become totals."""
    headers=['Record ID','Record role','Customer No','End Item','Commodity','Type',
        'Purchasing Supplier','Costing Supplier','Calculation status','Verification status',
        'Missing master fields','Missing dependencies','Failure reason','Source sheet','Source row','Available intermediate fields']
    details=[];intermediates=[];all_rows=[*run.rows,*getattr(run,'producer_rows',[])]
    def append(record_id,inputs,values,commodity,type_,status,issues,role):
        master=[];dependencies=[]
        def classify(issue):
            code=str(issue.get('code',''))
            if 'master' in code or 'rate' in code or 'mapping' in code:master.append(str(issue.get('field') or issue.get('master') or issue.get('message') or code))
            if any(word in code for word in ('producer','depend','end_item','component')):dependencies.append(str(issue.get('field') or issue.get('message') or code))
            for key in ('producer_issues','partial_issues'):
                for child in issue.get(key,[]) or []:
                    if isinstance(child,dict):classify(child)
        for issue in issues:classify(issue)
        safe=_safe_diagnostic_fields(values)
        details.append([record_id,role,get(inputs,'Customer No'),get(inputs,'End Item'),commodity,type_,
            get(inputs,'Purchasing Supplier') or inputs.get('_purchasing_supplier') or get(inputs,'Supplier'),
            get(inputs,'Costing Supplier') or inputs.get('_costing_supplier') or get(inputs,'Supplier'),status,
            'UNVERIFIED','; '.join(dict.fromkeys(master)),'; '.join(dict.fromkeys(dependencies)),
            '; '.join(str(issue.get('code',''))+': '+str(issue.get('message','')) for issue in issues)[:32000],
            inputs.get('_sheet'),inputs.get('_row'),'; '.join(safe)[:32000]])
        for key,value in safe.items():intermediates.append([record_id,role,status,'Calculated',key,_diagnostic_value(value)])
        for key,value in _safe_diagnostic_fields(inputs).items():intermediates.append([record_id,role,status,'Input',key,_diagnostic_value(value)])
        for key in ('_process_costs','_hardening_costs','_consumable_lines','_pcba_component_results','_paint_result'):
            if values.get(key):intermediates.append([record_id,role,status,'Calculated details',key,_diagnostic_value(values[key])])
    for row in all_rows:
        append(row.record_id,row.inputs,row.values,row.commodity,row.type,row.calculation_status,row.issues,'PRODUCER' if row.dependency_only else 'OWNER')
        for index,component in enumerate(row.values.get('_pcba_component_results',[])):
            source_index=component.get('component_index',index)
            originals=row.inputs.get('_pcba_components',[])
            original=originals[source_index] if source_index<len(originals) else {}
            append(row.record_id+':component:'+str(source_index),original,component.get('values',{}),'PCBA','COMPONENT',
                component.get('calculation_status','CALCULATED'),component.get('issues',[]),'COMPONENT')
    # Keep all readable matched input lines, including ambiguous and paint groups,
    # as source evidence. These are never independent economic contributions.
    seen_details=set()
    for owner in run.rows:
        for key in ('_details','_paint_components','_soft_components'):
            for source in owner.inputs.get(key,[]) or []:
                identity=source.get('_record_id')
                if not identity or identity in seen_details:continue
                seen_details.add(identity)
                append(identity,source,{},get(source,'Commodity'),get(source,'Type'),'UNVERIFIED',
                    [{'code':'input_detail_evidence','message':'Original normalized detail retained; economic cost belongs only to the linked calculation owner.'}],'INPUT DETAIL')
    for record in run.metadata.get('unassigned_details',[]):
        append(record.get('_record_id'),record,{},get(record,'Commodity'),get(record,'Type'),'BLOCKED',
            [{'code':'unassigned_detail','message':'Readable detail has no uniquely verified BOM owner; not costed or added to totals.'}],'UNASSIGNED INPUT')
    for index,issue in enumerate(run.issues):
        append('input-issue:'+str(index),{'_sheet':issue.get('sheet'),'_row':issue.get('row')},{},None,None,
               'BLOCKED' if issue.get('severity')=='error' else 'UNVERIFIED',[issue],'INPUT DIAGNOSTIC')
    table(book,'Calculation_Diagnostics',headers,details)
    table(book,'Calculation_Intermediates',['Record ID','Record role','Calculation status','Value origin','Field','Value'],intermediates)
    counts=run.counts
    table(book,'Calculation_Summary',['Metric','Value'],[
        ['Overall status',run.status],['Verification status','UNVERIFIED'],['Result approval','Review required'],
        *[[key,value] for key,value in counts.items()],['Producer rows',len(run.producer_rows)],
        ['Unassigned detail rows',len(run.metadata.get('unassigned_details',[]))],
        ['Notice','Incomplete final costs and dependent totals are blank. Available intermediate values are not approved final costs.']])


def build_workbook(run):
    book=Workbook();book.remove(book.active)
    book.properties.title='Cost Pilot - Calculation Output'
    book.properties.creator='Cost Pilot'
    book.properties.subject='Calculated costs and available results requiring review'
    diag=[['review_required','Cost Pilot calculation output. All values require review and independent acceptance.'],
          ['legacy_numerically_verified',False],['run_id',run.metadata.get('run_id')],['coverage',run.metadata.get('coverage')]]
    for issue in run.issues:diag.append([issue.get('code','input'),issue.get('message',str(issue))])
    groups={};projections={}
    for row in [*getattr(run,'producer_rows',[]),*run.rows]:
        if normalized(row.type)=='end item':continue
        key=layout_key(row)
        # Primary merge17466 has one sheet for each Type projection. Only the
        # original supplier-detail families are split by costing supplier.
        supplier_keys={'SOFT','STAMPING','FORGING','GDC','PDC','SAND_CASTING','THERMAL'}
        supplier=str(get(row.inputs,'Costing Supplier') or get(row.inputs,'Supplier') or '') if key in supplier_keys else ''
        groups.setdefault((key,supplier,row.values.get('_thermal_supplier_path') if row.commodity=='THERMAL' else None),[]).append(row)
        for issue in row.issues:diag.append([row.record_id+':'+str(issue.get('code')),issue.get('message')])
    for (key,supplier,thermal_path),rows in groups.items():
        layout=LAYOUTS.get(key,{})
        detail=layout.get('detail',{})
        columns=layout.get('columns',[])
        if detail:
            before=[(x['label'],x.get('key',x['label']),x.get('source'), 'General') for x in detail.get('before_consumables',[])]
            before_process=[(x['label'],x.get('key',x['label']),x.get('source'),'General') for x in detail.get('before_process',[])]
            process_names=list(dict.fromkeys(line['name'] for row in rows for line in row.inputs.get('_process_lines',[])))
            process_columns=[(name,name,'input','General') for name in process_names]
            after=[(x['label'],x.get('key',x['label']),x.get('source'),'General') for x in detail.get('after_process',[])]
            columns=before+before_process+process_columns+after
        if key=='HARD_MAKE':
            # make_new14645 omits geometry columns with no populated source input.
            columns=[c for c in columns if c[0] not in {'Color','L','W','H','thk'} or any(get(r.inputs,c[0]) is not None for r in rows)]
        if key=='STAMPING':columns=stamping_columns(columns,rows)
        if key=='THERMAL':
            columns=thermal_columns(thermal_path=='make')
        if key in {'SOFT','STAMPING'}:columns=detail_columns(columns,rows)
        if key=='PCBA':
            # pcba_op13959-13984 board summary, distinct from its component table.
            identity=['Supplier','End Item','Customer No','Desc','Sr','Qty/Veh','Commodity','Surface Area (in SQM)']
            board_fields=['Process / Assy Name','RM Cost/Pc','Process Cost/Pc','ICC Cost','REJ Cost','O&H Cost','PROFIT Cost','PCBA Markup/Pc','PnF/Pc','Basic Cost/Pc','Total Cost/Pc']
            columns=[(name,'Supplier No' if name=='Sr' else name,'input','General') for name in identity]+[(name,name,'result','General') for name in board_fields]
        if not columns:
            keys=list(dict.fromkeys(k for row in rows for k in row.values if not str(k).startswith('_')))
            identities=['Customer No','Supplier','Purchasing Supplier','Costing Supplier','End Item','Commodity','Type']
            columns=[(key,key,'input','General') for key in identities]+[(key,key,'result','General') for key in keys if key not in identities]
        variants=list(dict.fromkeys(v for row in rows for v in row.inputs.get('_variants',{})))
        headers=[c[0] for c in columns]+variants+['Execution status']
        data=[]
        for row in rows:
            values=[]
            for label,field,source,format_ in columns:
                value=detail_value(row,field,source,key)
                if value is None and source=='result':diag.append([row.record_id,'Output mapping unresolved: '+str(field)])
                values.append(value)
            data.append(values+[row.inputs.get('_variants',{}).get(v) for v in variants]+[row.calculation_status])
        prefixes={'SOFT':'ST_','STAMPING':'SM_','FORGING':'FG_','GDC':'GDC_Output_','PDC':'PDC_Output_','SAND_CASTING':'Sand_Output_','THERMAL':'Th_'}
        name=prefixes[key]+supplier if key in prefixes else 'PCBA' if key=='PCBA' else layout.get('sheet',{}).get('name',key)
        if key=='THERMAL':name=('make_' if thermal_path=='make' else 'Th_')+supplier
        if not layout:name='Unresolved_'+key
        ws=table(book,name,headers,data,[c[3] for c in columns]+['General']*(len(variants)+1))
        if detail or key=='THERMAL':
            # Original supplier detail has a top rate row, then named headers.
            ws.insert_rows(1);ws.freeze_panes='A3';ws.row_dimensions[2].height=detail.get('header_height') or 32
            for j,label in enumerate(headers,1):
                width=detail.get('widths',{}).get(label)
                if width:ws.column_dimensions[get_column_letter(j)].width=width
                for row_index in range(3,len(rows)+3):ws.row_dimensions[row_index].height=detail.get('data_height') or 15
                for role,row_index in [('header',2)]+[('data',index) for index in range(3,len(rows)+3)]:
                    style=detail.get('styles',{}).get(role,{}).get(label)
                    if not style:continue
                    from xml.etree.ElementTree import fromstring
                    from openpyxl.styles import Font,PatternFill,Border,Alignment
                    for attr,klass in [('font',Font),('fill',PatternFill),('border',Border),('alignment',Alignment)]:
                        if style.get(attr):
                            try:setattr(ws.cell(row_index,j),attr,klass.from_tree(fromstring(style[attr])))
                            except (TypeError,ValueError):pass
                    if style.get('number_format'):ws.cell(row_index,j).number_format=style['number_format']
                    # Legacy accounting styles must not turn native dates into serial numbers.
                    cell=ws.cell(row_index,j)
                    if isinstance(cell.value,datetime):cell.number_format='dd.mmm.yyyy hh:mm:ss'
                    elif isinstance(cell.value,date):cell.number_format='dd.mmm.yyyy'
            ws.auto_filter.ref=f'A2:{get_column_letter(len(headers))}{len(rows)+2}'
            detail_rates(ws,columns,rows,key,diag)
        detail_footer(ws,columns,rows,variants,key,diag)
        first_data_row=3 if detail or key=='THERMAL' else 2
        for index,row in enumerate(rows,first_data_row):
            projections.setdefault(row.record_id,[]).append((ws,index,columns))
    def render(label,callback,*args):
        try:return callback(*args)
        except (ArithmeticError,ValueError,KeyError,TypeError,IndexError,AttributeError) as error:
            issue={'code':'output_projection_error','message':label+' projection is incomplete: '+str(error),'error_type':type(error).__name__}
            run.issues.append(issue);diag.append([label,issue['message']])
            return None
    from .estimation import part_estimation_output,forging_estimation_output,casting_intermediate_output
    render('Casting intermediates',casting_intermediate_output,book,run,diag,safe_text,table)
    render('Casting estimates',part_estimation_output,book,run,diag,safe_text,result_value,sheet_name)
    render('Forging estimates',forging_estimation_output,book,run,diag,safe_text,result_value,sheet_name)
    render('Paint',paint_component_output,book,run)
    render('PCBA_OP',pcba_component_output,book,run,diag)
    variants=list(dict.fromkeys(v for r in run.rows for v in r.inputs.get('_variants',{})))
    render('Master_Output',master_output,book,run,variants,diag,projections)
    if 'Master_Output' not in book:table(book,'Master_Output',['Record ID','Customer No','Supplier','End Item','Execution status'],[[r.record_id,r.inputs.get('Customer No'),r.inputs.get('Supplier'),r.inputs.get('End Item'),r.calculation_status] for r in run.rows])
    roots=[r for r in run.rows if normalized(r.type)=='end item']
    if roots:
        render('End Item',end_item_output,book,roots,variants)
        if any(not str(r.commodity or '').strip() for r in roots):
            render('SBC',sbc_output,book,run,roots,variants,diag)
    render('Analysis',analysis_output,book,run,variants,diag)
    if run.summaries.get('create_end_item_analysis'):
        render('End Item Analysis',end_item_analysis_output,book,run,variants,diag)
    seat=run.summaries.get('seat',{})
    if seat.get('enabled'):
        from .seat import seat_output
        render('Seat',seat_output,book,seat,safe_text)
        for issue in seat.get('issues',[]):diag.append([issue.get('code','seat'),str(issue)])
        for block in seat.get('blocks',[]):
            for row in block.get('rows',[]):
                if row.get('status')!='success':diag.append(['seat:'+str(row.get('End Item'))+':'+str(block['key']),str(row.get('issues',[]))])
    render('Input copies',source_input_output,book,run.metadata,diag)
    calculation_diagnostics(book,run)
    table(book,'Diagnostics',['Event / record','Detail'],diag)
    # Original merge17586/17650/17729/17734 writes the final summary sheets
    # before supplier detail/estimate sheets. Keep every remaining sheet and its
    # relative order; diagnostics remain at the end for partial-result review.
    final_summaries=('SBC','End Item','Master_Output','Analysis','End Item Analysis')
    position=0
    for name in final_summaries:
        if name in book.sheetnames:
            book.move_sheet(name,offset=position-book.index(book[name]));position+=1
    excel_tables(book)
    book.calculation.fullCalcOnLoad=True
    output=BytesIO();book.save(output);return persist_numeric_caches(output.getvalue(),book)

"""Source-backed per-part sheets within the existing workbook generation flow.

PCM sand_output_format8410-8713, gdc_output_format9754-10238 and
pdc_output_format10247-10737 supply the exact labels, positions and styles.
Only already-calculated named results are consumed here. No workbook masters,
legacy calculated values or independent calculation engine are introduced.
"""
from decimal import Decimal, InvalidOperation
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.cell.cell import MergedCell
from openpyxl.utils.cell import range_boundaries, get_column_letter
from .layouts import PART_DETAIL_LAYOUTS
from ..common import get, normalized
from ..merging.rollup import amount

ZERO = Decimal(0)


def _style(cell, settings):
    color = lambda v: str(v).lstrip('#')
    cell.font = Font(name=settings.get('font_name', 'Calibri'),
                     size=float(settings.get('font_size', 10)),
                     bold=bool(settings.get('bold')), color=color(settings.get('font_color', '000000')))
    if settings.get('bg_color'):cell.fill = PatternFill('solid', fgColor=color(settings['bg_color']))
    cell.alignment = Alignment(horizontal=settings.get('align', 'center'),
                               vertical='center' if settings.get('valign') == 'vcenter' else 'top',
                               wrap_text=bool(settings.get('text_wrap')))
    if settings.get('border'):
        line=Side(style='thin', color='000000');cell.border=Border(left=line,right=line,top=line,bottom=line)
    cell.number_format = settings.get('num_format', '#,##0.00')


def _template(book, name, spec, write, new_name):
    sheet=book.create_sheet(new_name(name,book.sheetnames));sheet.sheet_view.showGridLines=False
    for method,address,value,style in spec['operations']:
        bounds=range_boundaries(address);left,top,right,bottom=bounds
        if method=='merge_range' and address not in sheet.merged_cells:
            # Constant presentation ranges are copied from the source. Dynamic
            # process totals are placed separately below the actual data rows.
            if any(not (right<r.min_col or left>r.max_col or bottom<r.min_row or top>r.max_row) for r in sheet.merged_cells):continue
            sheet.merge_cells(address)
        cell=sheet.cell(top,left)
        if isinstance(cell,MergedCell):continue
        write(cell,value)
        settings=spec['styles'].get(style,{})
        for row in sheet.iter_rows(min_row=top,max_row=bottom,min_col=left,max_col=right):
            for target in row:_style(target,settings)
    for address,width in spec['widths'].items():
        left,_,right,_=range_boundaries(address+'1' if ':' not in address else address.replace(':','1:')+'1')
        for col in range(left,right+1):sheet.column_dimensions[get_column_letter(col)].width=width
    for row,height in spec['heights'].items():sheet.row_dimensions[row].height=height
    sheet.freeze_panes='F14' if spec['source']!='sand_output_format' else 'F12'
    sheet.print_options.horizontalCentered=True;sheet.sheet_properties.pageSetUpPr.fitToPage=True
    sheet.page_setup.orientation='landscape';sheet.page_setup.paperSize=sheet.PAPERSIZE_A4
    sheet.page_setup.fitToWidth=1;sheet.page_setup.fitToHeight=0
    return sheet


def _kind(row):return normalized(row.inputs.get('_ownership_type',get(row.inputs,'Type',row.type)))


def _numeric(value):
    try:return Decimal(str(value)) if value is not None and value!='' else None
    except (InvalidOperation,ValueError,TypeError):return None


def _weighted(rows, field, variant, value_of, *, empty=ZERO):
    if not rows:return empty
    total=ZERO
    for row in rows:
        if row.status!='success':return None
        flag=_numeric(row.inputs.get('_variants',{}).get(variant))
        qty=_numeric(get(row.inputs,'Qty/Veh'))
        value=_numeric(value_of(row,field))
        if flag is None or qty is None or value is None:return None
        total+=flag*qty*value
    return total


def _sum(values):return None if any(value is None for value in values) else sum(values,ZERO)


def _final(rows,variant):
    try:return sum((amount(row,variant) for row in rows),ZERO)
    except (ValueError,TypeError,InvalidOperation,KeyError):return None
    except Exception as error:
        # Expected missing/blocked contributions are represented by the normal
        # calculation error; unexpected writer failures must still surface.
        from ..common import CalculationError
        if isinstance(error,CalculationError):return None
        raise


def _parts(run,commodity,diag):
    rows=[row for row in [*getattr(run,'producer_rows',[]),*run.rows]
          if row.commodity==commodity and _kind(row)!='end item'
          and (row.dependency_only or _kind(row)!='buy non std')]
    groups={}
    for row in rows:
        supplier=get(row.inputs,'Costing Supplier') or get(row.inputs,'Supplier')
        groups.setdefault((normalized(get(row.inputs,'End Item')),normalized(supplier)),[]).append(row)
    for key,group in groups.items():
        bases=[row for row in group if _kind(row) in {'make','buy non std'}
               and (row.dependency_only or _kind(row)!='buy non std')]
        if not bases:continue
        if len(bases)!=1:
            diag.append([commodity+':'+key[0],'Per-part estimate withheld: more than one MAKE/producer owns this End Item and costing supplier.'])
            continue
        yield bases[0],group


def part_estimation_output(book,run,diag,write,value_of,new_name):
    """Reuse original detailed layouts with explicit owner-scoped contributions.

    Original GDC10015-10086/PDC10485-10579 reads supplier-wide footer totals.
    Those totals can mix different End Items. Consistent with the approved
    ownership correction, this adaptation filters same-run final components by
    End Item, excluding dependency-only producer duplicates. This intentional
    difference is visible in Diagnostics; it is not a numerical parity claim.
    """
    for commodity,key,suffix in [('GDC','GDC','_gdc'),('PDC','PDC','_pdc'),('SAND CASTING','SAND_CASTING','_sand')]:
        spec=PART_DETAIL_LAYOUTS[key]
        for base,group in _parts(run,commodity,diag):
            customer=str(get(base.inputs,'Customer No') or base.record_id)
            sheet=_template(book,customer[:25]+suffix,spec,write,new_name)
            value=lambda row,field:value_of(row,field,template=key)
            def put(address,number):
                cell=sheet[address]
                if not isinstance(cell,MergedCell):write(cell,number)
            for address,field in spec['fields'].items():
                put(address,value_of(base,field,source=None if base.status=='success' else 'result',template=key) if not address.startswith('D') else value(base,field))
            if base.status!='success':diag.append([sheet.title,'Casting producer is blocked; unresolved cost cells remain blank.'])
            owner=normalized(get(base.inputs,'End Item'))
            owned=[row for row in run.rows if not row.dependency_only and normalized(get(row.inputs,'End Item'))==owner and _kind(row)!='end item']
            variants=list(dict.fromkeys(v for row in owned for v in row.inputs.get('_variants',{})))
            variant=variants[0] if variants else None
            if len(variants)>1:diag.append([sheet.title,'Original single-estimate projection uses first named variant '+str(variant)+'; all variants remain in detailed/End Item sheets.'])
            processes=[row for row in group if _kind(row)=='process']
            first=12 if key=='SAND_CASTING' else 14
            total_row=max(first+len(processes),17 if key=='SAND_CASTING' else 19)
            # Source fixed total merges must not swallow additional process rows.
            for merged in list(sheet.merged_cells.ranges):
                if merged.min_row>=first and merged.max_row>=first and merged.min_col<=8:
                    sheet.unmerge_cells(str(merged))
            for index,row in enumerate(processes,first):
                lines=[line for line in row.values.get('_process_costs',[]) if _numeric(line.get('quantity')) not in (None,ZERO)]
                selected=lines[0] if lines else {}
                if len(lines)>1:diag.append([sheet.title+':'+row.record_id,'Original process-detail display selects the first nonzero process; total cost retains every calculated process.'])
                cells={'B':get(row.inputs,'Customer No'),'C':get(row.inputs,'Desc'),'E':selected.get('name'),
                       'F':selected.get('quantity'),'G':selected.get('rate'),'H':get(row.values,'Process Cost/Pc')}
                sheet.merge_cells(start_row=index,start_column=3,end_row=index,end_column=4)
                for column,number in cells.items():
                    put(column+str(index),number);_style(sheet[column+str(index)],spec['styles'].get('nrml_frmt_pink',{}))
            sheet.merge_cells(start_row=total_row,start_column=2,end_row=total_row,end_column=7)
            put('B'+str(total_row),'Total Process Cost')
            process_cost=_weighted(processes,'Process Cost/Pc',variant,value)
            put('H'+str(total_row),process_cost)
            markup_start=first
            for index,(label,rate,field) in enumerate([('Rejection','REJ','REJ COST'),('Over Heads','OH','OH COST'),('Profit','PROFIT','PROFIT COST'),('ICC','ICC','ICC COST')],markup_start):
                put('I'+str(index),label)
                rate_values=[get(row.values,rate) for row in group if row.status=='success' and get(row.values,rate) is not None]
                put('J'+str(index),rate_values[0] if rate_values and all(item==rate_values[0] for item in rate_values) else None)
                sheet['J'+str(index)].number_format='0.00%'
                put('K'+str(index),_weighted(processes if key=='SAND_CASTING' else group,field,variant,value))
            if key!='SAND_CASTING':
                put('I'+str(first+4),'Handling')
                put('J'+str(first+4),get(base.values,'Handling') if base.status=='success' else None)
                sheet['J'+str(first+4)].number_format='0.00%'
                put('K'+str(first+4),_weighted(group,'Handling COST',variant,value))
            # The source repeats I19 for two labels. Keep the actual total at
            # its process-dependent coordinate and retain BOP handling below
            # instead of overwriting a heading/cost with a different meaning.
            for merged in list(sheet.merged_cells.ranges):
                if merged.min_col>=9 and merged.min_row==total_row and merged.max_row==total_row:
                    sheet.unmerge_cells(str(merged))
            sheet.merge_cells(start_row=total_row,start_column=9,end_row=total_row,end_column=10)
            put('I'+str(total_row),'Total Markups')
            put('K'+str(total_row),_weighted(processes if key=='SAND_CASTING' else group,'MARKUP/Pc',variant,value))
            if key!='SAND_CASTING':
                for index,(kind,label) in enumerate([('buy std','Handling_Buy Std'),('buy non std','Handling_Buy Non Std')],total_row+2):
                    put('I'+str(index),label)
                    put('K'+str(index),_weighted([row for row in owned if _kind(row)==kind],'MARKUP/Pc',variant,value))
            # No fixed blank cells are used as arithmetic operands. The exact
            # original final costing results remain the sole numeric producers.
            if key=='SAND_CASTING':
                put('J5',process_cost);put('J6',_weighted(processes,'MARKUP/Pc',variant,value))
                put('J8',_sum([_weighted([row for row in owned if _kind(row)==kind],'Basic Cost/Pc',variant,value) for kind in ('pkg','frt')]))
                put('J9',_final(owned,variant));denominator=sheet['J9'].value;last=9
            else:
                if key=='GDC':
                    # Original9869 reads the source rate row, not cost/weight.
                    # Retain the actual resolved rate even when casting weight=0.
                    put('D8',get(base.values.get('_master_rates',{}),'GDC Process cost'))
                else:put('D8',get(base.inputs,'PDC Machine Process'))
                put('J4',get(base.values,'RM Cost/Pc'))
                put('J5',get(base.values,'Process Cost/Pc'))
                put('J6',process_cost)
                # Source markups include optional BOP handling; each final owner
                # enters once and producer-only rows do not add economic cost.
                put('J7',_weighted(owned,'MARKUP/Pc',variant,value))
                put('J8',_sum([_weighted([row for row in owned if _kind(row)==kind],'Basic Cost/Pc',variant,value) for kind in ('buy std','buy non std')]))
                put('J9',_weighted([row for row in owned if _kind(row)=='pkg'],'Basic Cost/Pc',variant,value))
                put('J10',_weighted([row for row in owned if _kind(row)=='frt'],'Basic Cost/Pc',variant,value))
                put('J11',_final(owned,variant));denominator=sheet['J11'].value;last=11
                diag.append([sheet.title,'Source supplier-wide summary totals are scoped to this End Item to preserve final ownership; legacy numerical parity remains unverified.'])
            for row in range(4,last+1):
                numerator=_numeric(sheet['J'+str(row)].value);denom=_numeric(denominator)
                put('K'+str(row),numerator/denom if numerator is not None and denom else None)
                sheet['K'+str(row)].number_format='0.00%'
            if denominator is None:diag.append([sheet.title,'Final part summary is incomplete; no missing contribution was replaced with zero.'])
            status_row=max(total_row+4,24)
            write(sheet.cell(status_row,2),'Review estimate; original layout restored, legacy numerical parity unverified.')
            write(sheet.cell(status_row+1,2),'Source: '+spec['source']+':'+spec['lines'])
            sheet.print_area='B2:K'+str(status_row+1)


def forging_estimation_output(book,run,diag,write,value_of,new_name):
    """forg_estimation13538-13920, using existing final cost producers.

    The source's summary re-subtracts F11 although its own heading says scrap
    recovery is already factored into RM. Here the final same-run owner result
    remains authoritative; the recovery row is an informational disclosure.
    Set-of-two's separate template requires verified geometry and DB-backed
    normalising/density bindings, so it is explicitly disclosed rather than
    becoming a second formula path with hardcoded rates.
    """
    spec=PART_DETAIL_LAYOUTS['FORGING']
    rows=[row for row in [*getattr(run,'producer_rows',[]),*run.rows] if row.commodity=='FORGING' and _kind(row)!='end item'
          and (row.dependency_only or _kind(row)!='buy non std')]
    groups={}
    for row in rows:
        supplier=get(row.inputs,'Costing Supplier') or get(row.inputs,'Supplier')
        groups.setdefault((normalized(get(row.inputs,'End Item')),normalized(supplier)),[]).append(row)
    for (owner,_),group in groups.items():
        if 'setoftwo' in owner.replace(' ',''):
            set_of_two_output(book,run,group,diag,write,value_of,new_name)
            continue
        bases=[row for row in group if _kind(row)=='make']
        if len(bases)>1:
            diag.append(['FORGING:'+owner,'Per-part estimate withheld: multiple MAKE identities require an explicit original ownership decision.']);continue
        base=bases[0] if bases else group[0]
        sheet=_template(book,str(get(base.inputs,'Customer No') or base.record_id),spec,write,new_name)
        sheet.freeze_panes='A10'
        value=lambda row,field:value_of(row,field,template='FORGING')
        put=lambda address,number:write(sheet[address],number)
        for address,field in spec['fields'].items():
            put(address,value_of(base,field,source=None if base.status=='success' else 'result',template='FORGING') if not address.startswith('B') else value(base,field))
        if not bases:
            for address in ('B3','B4','B5','B6','B7','F2','F3','F4','F5','F7','F8'):put(address,'-')
        hardening=[]
        for row in group:
            for line in row.values.get('_hardening_costs',[]):
                if _numeric(line.get('quantity')) not in (None,ZERO) and line.get('name') not in hardening:hardening.append(line['name'])
        for offset,name in enumerate(hardening,10):
            put('A'+str(offset),name)
            rates=[line.get('rate') for row in group for line in row.values.get('_hardening_costs',[]) if line.get('name')==name]
            put('B'+str(offset),rates[0] if rates and all(rate==rates[0] for rate in rates) else None)
        new_pos=14 if len(hardening)<5 else 9+len(hardening)
        header_row=21 if len(hardening)<5 else 16+len(hardening)
        for row in range(10,header_row):
            address='C'+str(row)+':E'+str(row)
            if not any(sheet.cell(row,col).__class__ is MergedCell for col in (3,4,5)):
                sheet.merge_cells(address)
        for index,label in enumerate(['RM Cost / Pc','Scrap Recovery (factored in RM Cost/Pc)','Process Cost','BOP','BONP'],10):put('C'+str(index),label)
        for index,label in enumerate(['Total Markup','Tool Cost','Packaging','Freight','Total Cost/ part (Rs)'],new_pos+2):put('C'+str(index),label)
        for index,(label,field) in enumerate([('OH','OH'),('Profit','PROFIT'),('ICC','ICC'),('Rej','REJ')],new_pos+2):
            put('A'+str(index),label)
            rates=[get(row.values,field) for row in group if row.status=='success' and get(row.values,field) is not None]
            put('B'+str(index),rates[0] if rates and all(rate==rates[0] for rate in rates) else None)
            sheet['B'+str(index)].number_format='0.00%'
        owned=[row for row in run.rows if not row.dependency_only and normalized(get(row.inputs,'End Item'))==owner and _kind(row)!='end item']
        variants=list(dict.fromkeys(v for row in owned for v in row.inputs.get('_variants',{})));variant=variants[0] if variants else None
        if len(variants)>1:diag.append([sheet.title,'Original forging estimate projects first named variant '+str(variant)+'; detailed/End Item sheets retain all variants.'])
        for address,field in [('F10','RM Cost/Pc'),('F11','Scrap Recovery'),('F12','Process Cost/Pc')]:put(address,_weighted(group,field,variant,value))
        for address,kind in [('F13','buy std'),('F14','buy non std')]:
            selected=[row for row in owned if _kind(row)==kind]
            put(address,_weighted(selected,'Total Cost/Pc',variant,value) if selected else '-')
        for index,field in enumerate(['MARKUP/Pc','Tool Cost','Packing','Freight'],new_pos+2):put('F'+str(index),_weighted(group,field,variant,value))
        put('F'+str(new_pos+6),_final(owned,variant))
        diag.append([sheet.title,'Original summary expression SUM(F10:F...)-F11 is replaced by the existing final owner result: scrap recovery is already factored in RM and is not deducted twice. Legacy numerical parity remains unverified.'])
        headers=['Operation no.','Desc','Machine','CT(min)/uom','MHR','Process Cost (Rs)']
        for col,label in enumerate(headers,1):
            cell=sheet.cell(header_row,col);write(cell,label);_style(cell,spec['styles'].get('yellowcenterbrdrfrmt',{}))
        processes=[row for row in group if _kind(row)!='make']
        for row_number,row in enumerate(processes,header_row+1):
            lines=[line for line in row.values.get('_process_costs',[]) if _numeric(line.get('quantity')) not in (None,ZERO)]
            first=lines[0] if lines else {}
            values=[get(row.inputs,'Customer No'),get(row.inputs,'Desc'),first.get('name'),first.get('quantity'),first.get('rate'),get(row.values,'Process Cost/Pc')]
            for col,number in enumerate(values,1):
                cell=sheet.cell(row_number,col);write(cell,number);_style(cell,spec['styles'].get('centerbrdrfrmt',{}))
        end=header_row+1+len(processes)
        sheet.merge_cells(start_row=end,start_column=3,end_row=end,end_column=5);put('C'+str(end),'Sub Total')
        costs=[_numeric(value(row,'Process Cost/Pc')) if row.status=='success' else None for row in processes]
        put('F'+str(end),_sum(costs))
        write(sheet.cell(end+2,1),'Review estimate; source layout restored, legacy numerical parity unverified.')
        write(sheet.cell(end+3,1),'Source: forg_estimation13538-13920')
        sheet.print_area='A1:F'+str(end+3)


def set_of_two_output(book,run,group,diag,write,value_of,new_name):
    """Actual setoftwo_format12432-12730 layout and supported result links.

    The original geometry cells are empty editable positions, not input/master
    records. Without verified density, geometry-derived weights remain blank.
    Only a retained exact named Normalising DB rate may populate its cost;
    the original literal5 and steel density0.00785 are never fallback masters.
    """
    spec=PART_DETAIL_LAYOUTS['FORGING_SET_OF_TWO']
    base=next((row for row in group if _kind(row)!='process'),group[0])
    for issue in base.values.get('_estimate_issues',[]):
        diag.append([base.record_id+':'+str(issue.get('code')),issue.get('message','Estimate data is unresolved')])
    customer=str(get(base.inputs,'Customer No') or base.record_id).split('(')[0].strip()
    sheet=_template(book,customer,spec,write,new_name);sheet.freeze_panes='C29'
    value=lambda row,field:value_of(row,field,template='FORGING')
    def put(address,number):
        cell=sheet[address]
        if not isinstance(cell,MergedCell):write(cell,number)
    labels=spec['labels'];standard=spec['styles'].get('centerbrdrfrmt',{})
    for offset in range(7):
        index=offset+4
        for left,right,label in [(2,3,labels['firstlist'][offset]),(4,5,''),(6,7,labels['secondlist'][offset])]:
            address=get_column_letter(left)+str(index)+':'+get_column_letter(right)+str(index)
            if address not in sheet.merged_cells:sheet.merge_cells(address)
            put(get_column_letter(left)+str(index),label)
        put('I'+str(index),labels['list_1_new'][offset])
    # The label's old price is not a required current DB value.
    put('F8','Normalising cost')
    for address,field in spec['fields'].items():
        put(address,value_of(base,field,source=None if base.status=='success' else 'result',template='FORGING') if not address.startswith('D') else value(base,field))
    put('D6',get(base.inputs,'Date'));sheet['D6'].number_format='dd-mm-yyyy'
    hardening=base.values.get('_hardening_costs',[])
    forging_rate=next((line.get('rate') for line in hardening if any(token in str(line.get('name')).casefold() for token in ('forging','melting'))),None)
    put('H6',forging_rate)
    calculated_weight=_numeric(get(base.values,'Calculated Wt'))
    normalising_rate=_numeric(get(base.values.get('_master_rates',{}),'Normalising'))
    put('H8',normalising_rate*calculated_weight if normalising_rate is not None and calculated_weight is not None else None)
    put('H10',get(base.values,'Scrap Recovery'))
    put('H11',get(base.values,'RM Cost/Pc'))
    owner=normalized(get(base.inputs,'End Item'))
    owned=[row for row in run.rows if not row.dependency_only and normalized(get(row.inputs,'End Item'))==owner and _kind(row)!='end item']
    variants=list(dict.fromkeys(v for row in owned for v in row.inputs.get('_variants',{})));variant=variants[0] if variants else None
    for address,field in [('J5','RM Cost/Pc'),('J6','Process Cost/Pc'),('J7','MARKUP/Pc'),('J8','Tool Cost'),('J9','Packing')]:put(address,_weighted(group,field,variant,value))
    put('J11',_final(owned,variant))
    for index in range(5,12):
        numerator=_numeric(sheet['J'+str(index)].value);denominator=_numeric(sheet['J11'].value)
        put('K'+str(index),numerator/denominator if numerator is not None and denominator else None)
        sheet['K'+str(index)].number_format='0.00%'
    for offset,label in enumerate(labels['thirdlist'],2):put(get_column_letter(offset)+'14',label)
    for offset,label in enumerate(labels['fifthlist'],2):put(get_column_letter(offset)+'28',label)
    for offset,label in enumerate(labels['forthlist'],15):
        put('I'+str(offset),label)
        for column in range(2,12):_style(sheet.cell(offset,column),standard)
    for index,(rate,field) in [(16,('OH','OH COST')),(17,('PROFIT','PROFIT COST')),(18,('REJ','REJ COST')),(21,('ICC','ICC COST'))]:
        put('J'+str(index),get(base.values,rate) if base.status=='success' else None)
        sheet['J'+str(index)].number_format='0.00%'
        put('K'+str(index),_weighted(group,field,variant,value))
    put('K26',_weighted(group,'MARKUP/Pc',variant,value))
    current=29
    first=True
    for row in group:
        if _kind(row)!='process':
            if first:first=False;continue
            sheet.merge_cells(start_row=current,start_column=2,end_row=current,end_column=11)
            put('B'+str(current),'# '+str(get(row.inputs,'Desc') or '')+' '+str(get(row.inputs,'Customer No') or ''));current+=1
            continue
        lines=[line for line in row.values.get('_process_costs',[]) if _numeric(line.get('quantity')) not in (None,ZERO)]
        line=lines[0] if lines else {}
        sheet.merge_cells(start_row=current,start_column=3,end_row=current,end_column=4)
        sheet.merge_cells(start_row=current,start_column=5,end_row=current,end_column=6)
        for column,number in [('B',get(row.inputs,'Customer No')),('C',get(row.inputs,'Desc')),('E',line.get('name')),('I',line.get('quantity')),('J',line.get('rate')),('K',get(row.values,'Process Cost/Pc'))]:put(column+str(current),number)
        current+=1
    sheet.merge_cells(start_row=current,start_column=2,end_row=current,end_column=10)
    put('B'+str(current),'Total Process Cost')
    put('K'+str(current),_weighted([row for row in group if _kind(row)=='process'],'Process Cost/Pc',variant,value))
    missing=[]
    if calculated_weight is None:missing.append('same-run Calculated Wt producer')
    if normalising_rate is None:missing.append('retained exact process_master Normalising rate for selected location')
    from .formulas import editable_geometry_block
    geometry_ready=editable_geometry_block(sheet,get(base.values,'GSM/ Density'))
    if not geometry_ready:missing.append('positive verified material_master GSM/ Density for original editable geometry block')
    diag.append([sheet.title,'setoftwo_format12432-12730: original layout and available result links restored; unresolved: '+', '.join(missing)+'. No hardcoded normalising rate or density used.'])
    diag.append([sheet.title,'Final summary uses existing owner costs. Geometry is an editable output estimate, not an input Calculated Wt producer; blank dimensions cache zero only with verified DB density. Detailed display-formula parity remains unverified.'])
    for offset,text in enumerate(['Review estimate; supported source-backed values only.','Unresolved: '+', '.join(missing),'Source: setoftwo_format12432-12730'],current+2):write(sheet.cell(offset,2),text)
    sheet.print_area='B2:K'+str(current+4)



def casting_intermediate_output(book,run,diag,write,table):
    """PCM6788-7103: original recipe triplets and Sand_columns rate graph.

    This writer receives only same-run results and row-order provenance. It
    never opens source templates, queries masters, or calculates another cost.
    Missing template decoration and unresolved rates remain explicit.
    """
    from .layouts import SAND_RATE_FIELDS
    candidates=[row for row in [*getattr(run,'producer_rows',[]),*run.rows]
                if row.commodity=='SAND CASTING' and row.values.get('_recipe_output_rows')]
    if not candidates:return
    identity=['PF Code','Project','Part Name','BC / SBC / TKO / RoCM','Date',
              'Estimation Revision','0 / 1 / 2 / 3','Supplier','Function','End Item',
              'Supplier No','Customer No','Desc','2D DWG/CAD Revision No','Type',
              'Qty/Veh','Commodity','RM Base','Sector & Location','Material Code',
              'Material Grade','Material Type']
    conditions=['Molding Type','UOM','Shape Rating (Compactness)','Weight Rating',
                'Core Rating','Complexity/ Geometrical Factor','Complexity Group',
                'Moulding Box Length','Moulding Box Width','Moulding Box Height',
                'Part Wt','Gross Wt','Cavity']
    headers=identity+conditions+list(SAND_RATE_FIELDS)
    values=[]
    for row in candidates:
        source=dict(row.inputs)
        source['Supplier']=get(source,'Costing Supplier') or get(source,'Supplier')
        source['Material Type']=None  # Original6979-6980 explicitly blank.
        values.append([get(source,name) for name in identity+conditions]+
                      [get(row.values,name) if get(row.values,name) is not None
                       else get(source,name) if name.strip() in {'Type of Core','No. of Cores','Core Weight per Part','Box Yield (%)'}
                       else None for name in SAND_RATE_FIELDS])
        if row.status!='success':diag.append([row.record_id,'Casting rate graph is incomplete; available same-run intermediate values are exported and unresolved cells remain blank.'])
    rate_sheet=table(book,'Casting_Sand_RM rate Calulation',headers,values)
    for column in range(1,5):rate_sheet.column_dimensions[get_column_letter(column)].width=15
    for label,width in [('E',10.43),('F',7),('G',7),('H',8),('I',8),('J',20),('K',10),('L',12),('M',20)]:rate_sheet.column_dimensions[label].width=width
    for column in range(14,len(headers)+1):rate_sheet.column_dimensions[get_column_letter(column)].width=7.86
    for column in (11,12,20):
        for cell in rate_sheet.iter_cols(min_col=column,max_col=column,min_row=2):
            for item in cell:item.number_format='@'
    for row in range(2,rate_sheet.max_row+1):rate_sheet.cell(row,5).number_format='dd-mm-yyyy'
    groups={}
    for row in candidates:
        supplier=get(row.inputs,'Costing Supplier') or get(row.inputs,'Supplier')
        key=(str(supplier),str(get(row.inputs,'Material Grade')))
        prior=groups.get(key)
        if prior and (prior.values.get('_recipe_ingredients')!=row.values.get('_recipe_ingredients')
                      or prior.values.get('_recipe_rm_period')!=row.values.get('_recipe_rm_period')):
            diag.append(['Casting_Recipie_estimation','Conflicting same-supplier/grade recipe projections; ambiguous group withheld.'])
            groups[key]=None
        elif key not in groups:groups[key]=row
    groups={key:row for key,row in groups.items() if row is not None}
    if not groups:return
    first=next(iter(groups.values()));labels=first.values['_recipe_output_rows']
    if any(row.values['_recipe_output_rows']!=labels for row in groups.values()):
        diag.append(['Casting_Recipie_estimation','Recipe row-order projections disagree; no arbitrary ingredient order chosen.']);return
    sheet=book.create_sheet('Casting_Recipie_estimation');sheet.sheet_view.showGridLines=False
    sheet.freeze_panes='E10';sheet.merge_cells('B2:G3');write(sheet['B2'],'Casting Grade Raw Material Rate Calculation')
    _style(sheet['B2'],{'bold':True,'bg_color':'1F4E78','font_color':'FFFFFF','text_wrap':True})
    for address,label in [('D5','Sector & Location'),('D6','RM Base'),('D7','Supplier'),('D8','Casting Grade')]:write(sheet[address],label)
    write(sheet['E5'],get(first.inputs,'Sector & Location'));write(sheet['E6'],first.values.get('_recipe_rm_period'))
    for column,label in [(2,'Sr No'),(3,'Material Code'),(4,'Material Grade')]:write(sheet.cell(9,column),label)
    for index,(key,row) in enumerate(groups.items()):
        left=5+index*3
        sheet.merge_cells(start_row=7,start_column=left,end_row=7,end_column=left+2)
        sheet.merge_cells(start_row=8,start_column=left,end_row=8,end_column=left+2)
        write(sheet.cell(7,left),key[0]);write(sheet.cell(8,left),key[1]);write(sheet.cell(6,left+2),key[0]+key[1])
        for offset,label in enumerate(['Rate/UOM','Addition in kg','Cost']):write(sheet.cell(9,left+offset),label+('.'+str(index) if index else ''))
    serial=0
    for position,label in enumerate(labels,10):
        aggregate=label in {'Total','Sub Total','Grand Total'}
        if label=='Total':write(sheet.cell(position,2),'Other Additions')
        elif label=='Sub Total':write(sheet.cell(position,2),'Total RM cost Rs/Charge')
        else:
            serial+=1;write(sheet.cell(position,2),serial)
            write(sheet.cell(position,4),'Electricity Cost for melting' if label=='Grand Total' else label)
        for index,row in enumerate(groups.values()):
            left=5+index*3
            matches=[item for item in row.values.get('_recipe_ingredients',[]) if item['Material Grade']==label]
            if label=='Sub Total':write(sheet.cell(position,left+2),row.values.get('Total RM cost Rs/Charge'))
            elif label=='Grand Total':
                for offset,value in enumerate([row.values.get('Electricity Cost for melting'),row.values.get('Total Charge Kg'),row.values.get('Power cost')]):write(sheet.cell(position,left+offset),value)
            elif not aggregate and len(matches)==1:
                line=matches[0];write(sheet.cell(position,3),line['Material Code'])
                for offset,key in enumerate(['Rate/UOM','Addition in kg','Cost']):write(sheet.cell(position,left+offset),line[key])
            elif not aggregate and len(matches)>1:diag.append([row.record_id,'Recipe ingredient '+label+' is ambiguous; values withheld.'])
    for offset,(label,field) in enumerate([('Total Cost (Rs)','Sub Total Cost'),('Total Charge Wt. ( Kg )','Total Charge Kg')],10+len(labels)):
        write(sheet.cell(offset,2),label)
        for index,row in enumerate(groups.values()):
            left=5+index*3;write(sheet.cell(offset,left+2 if field=='Sub Total Cost' else left+1),row.values.get(field))
    # Reuse the existing table installer with the original header row9.
    sheet.auto_filter.ref='B9:'+get_column_letter(4+3*len(groups))+str(11+len(labels))
    sheet.column_dimensions['B'].width=23;sheet.column_dimensions['C'].width=22;sheet.column_dimensions['D'].width=35
    for column in range(5,5+3*len(groups)):sheet.column_dimensions[get_column_letter(column)].width=17
    for row in sheet.iter_rows(min_row=9):
        for cell in row:
            _style(cell,{'border':1,'bold':cell.row==9,'bg_color':'D9E1F2' if cell.row==9 else 'FFFFFF','text_wrap':True})
    diag.append([sheet.title,'Available Python labels, ingredient order and triplets restored from same-run DB values. Missing Casting_Recipie.xlsx/1.xlsx prevents claiming exact external-template decoration or Excel formula-representation parity.'])

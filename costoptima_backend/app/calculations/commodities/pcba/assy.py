"""PCM pcba_op13926-14305: eBom components and first-populated board quantity.

Rates come from the existing relational masters. Board quantities are input
fields, never master coordinates. Component costs and purchased-board markup
remain separate so final analysis can deduct PCBA basic from BNS exactly once.
"""
from decimal import Decimal
from contextlib import nullcontext
from ...common import required,get,number,CalculationError,normalized
from ...masters import FieldRef,MasterLookupError
from ..shared import markups,finish,checkpoint
SOURCE={'function':'pcba_op','lines':'13926-14305'}
ZERO=Decimal(0)


def calculate_component(record,context):
    key={'material_code':required(record,'Material Code')};period=required(record,'RM Base')
    fob=context.require_master('ebom',key,'fob');checkpoint(context,record,{'FOB':fob})
    landing=context.require_master('ebom',key,'landing_factor');checkpoint(context,record,{'Landing Factor':landing})
    currency=context.master.lookup('ebom',key,'currency')
    exchange=context.require_master('ebom',{'currency':currency},FieldRef('Rate/UOM',str(period)))
    rm=fob*exchange*landing
    checkpoint(context,record,{'Currency':currency,'EXCHANGE':exchange,'Landed Cost/Pc':rm,'RM Cost/Pc':rm})
    points=context.require_master('ebom',key,'solder_point');checkpoint(context,record,{'Solder Points':points})
    solder=context.require_master('process_master',{'process':'Soldering'},FieldRef('Rate/UOM','AD'))
    rm=fob*exchange*landing;process=points*solder
    metadata=context.master.record('ebom',key) if hasattr(context.master,'record') else None
    descriptions={field:metadata.value(field) for field in ('Material Family','Material Sub Family','Material Grade','RM Supplier','UOM')} if metadata else {}
    values=markups(context,record,{**descriptions,'Currency':currency,'FOB':fob,'EXCHANGE':exchange,'Exchange Rate':exchange,'Landing Factor':landing,
        'Landed Cost/Pc':rm,'Solder Points':points,'SOLDERING':solder,
        'RM Cost/Pc':rm,'Process Cost/Pc':process,'Process Cost':process,'Basic Cost/Pc':rm+process})
    result=finish(record,values)
    result.update({'MARKUP total':result['MARKUP/Pc'],'Total Cost':result['Total Cost/Pc'],
        'Grand Total Cost':result['Grand Total Cost/Pc'],'PnF':ZERO})
    return result


def calculate(record,context):
    components=record.get('_pcba_components')
    if not components:
        raise CalculationError('pcba_components_missing','PCBA board costing requires its original component rows; a BOM row is not one component')
    headers=record.get('_pcba_quantity_headers')
    if not headers:
        raise CalculationError('pcba_quantity_mapping_missing','PCBA quantity fields must come from the named input region after RM Supplier')
    board=required(record,'Customer No')
    for component in components:
        if normalized(get(component,'PCBA Part Number',board))!=normalized(board):
            raise CalculationError('pcba_component_ownership','A component belongs to another PCBA Part Number')
    # Original14203-14214 selects the first column with any nonzero quantity
    # for this board, not one quantity column for each individual component.
    selected=None
    for name in headers:
        if any(number(get(component,name) or 0,name)!=0 for component in components):
            selected=name;break
    if selected is None:
        raise CalculationError('pcba_population_empty','No PCBA quantity column is populated; original output is a dash, not a zero cost')
    # Original14003: the first original BOM row owns the exchange-rate period,
    # independently of each board/detail RM Base. No later-row fallback.
    first_bom=context.rows[0] if context.rows else record
    period=required(first_bom,'RM Base')
    context.provenance.append({'kind':'legacy_batch_selector','source':'pcba_op14003',
        'record_id':first_bom.get('_record_id'),'RM Base':period})
    fields=('RM Cost/Pc','Process Cost/Pc','ICC COST','REJ COST','OH COST','PROFIT COST','MARKUP/Pc','PnF/Pc')
    totals={name:ZERO for name in fields};details=[]
    failures=[]
    for index,component in enumerate(components):
        component_input={**record,**component,'RM Base':period,'Qty/Veh':1}
        values={};component_issues=[];quantity=None
        scope=context.intermediate_scope() if hasattr(context,'intermediate_scope') else nullcontext()
        with scope:
            try:
                values=calculate_component(component_input,context)
                quantity=number(get(component,selected) or 0,selected)
            except (CalculationError,MasterLookupError) as error:
                explicit=error.details.get('partial_values',{}) if isinstance(error,CalculationError) else {}
                values=context.partial_values(explicit) if hasattr(context,'partial_values') else explicit
                component_issues=[error.as_dict() if isinstance(error,CalculationError) else {'code':error.code,'message':str(error)}]
            except Exception as error:
                values=context.partial_values() if hasattr(context,'partial_values') else {}
                component_issues=[{'code':'calculation_error','message':str(error),'error_type':type(error).__name__}]
        if component_issues:failures.append({'component_index':index,'Material Code':get(component,'Material Code'),'issues':component_issues})
        else:
            for name in fields:totals[name]+=values[name]*quantity
        details.append({'Material Code':get(component,'Material Code'),'component_index':index,'quantity_field':selected,
            'quantity':quantity,'values':values,'source_sheet':component.get('_sheet'),'source_row':component.get('_row'),
            'calculation_status':'PARTIAL' if component_issues and values else 'BLOCKED' if component_issues else 'CALCULATED','issues':component_issues})
        completed=any(item['calculation_status']=='CALCULATED' for item in details)
        checkpoint(context,record,{'RM Base':period,'Selected Population':selected,'_pcba_component_results':details,
            **({'Completed component RM Cost/Pc':totals['RM Cost/Pc'],'Completed component Process Cost/Pc':totals['Process Cost/Pc']} if completed else {})})
    if failures:
        raise CalculationError('pcba_components_incomplete','One or more PCBA components could not calculate; board total is unavailable.',
            'PCBA components',components=failures,partial_values={'RM Base':period,'_pcba_component_results':details})
    basic=totals['RM Cost/Pc']+totals['Process Cost/Pc']+sum((totals[n+' COST'] for n in ('ICC','REJ','OH','PROFIT')),ZERO)
    totals.update({'RM Base':period,'PCBA Markup/Pc':totals['MARKUP/Pc'],'Basic Cost/Pc':basic,'Total Cost/Pc':basic,
        'Total':basic,'PCBA Basic Cost/Pc':basic,'Process / Assy Name':'Buy PCBA',
        'Selected Population':selected,'_pcba_board_aggregated':True,'_pcba_component_results':details})
    return finish(record,totals,pnf=totals['PnF/Pc'])

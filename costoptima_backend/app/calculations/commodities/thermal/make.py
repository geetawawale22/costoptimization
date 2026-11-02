"""PCM hvacpart115693-15758; make_hvac16070-16848; nonmake15760-16065.

Supplier slicing precedes Type calculation. Thermal consumes its own two named
consumption-rule sets, never SOFT's gross/yield/sheet pipeline. All rates and
allowances are read from the existing database; formulas are the original
static source vocabulary selected through verified named condition fields.
"""
from decimal import Decimal
from ...common import required,get,empty,normalized,number,evaluate_expression,CalculationError
from ...masters import FieldRef
from ...consumption import select_rule
from ..shared import checkpoint,material,markups,finish

SOURCE={'function':'hvac;make_cols;hvacpart1;make_hvac;hvac_part_nonmake','lines':'15502-16848'}
ZERO=Decimal(0)


def supplier_has_make(record,context):
    """Original15737-15754 tests *all Thermal detail Types* for this supplier."""
    supplier=normalized(required(record,'Supplier'))
    for owner in context.rows:
        if normalized(get(owner,'Supplier'))!=supplier:continue
        details=[row for row in owner.get('_details',[]) if normalized(row.get('_sheet'))=='thermal']
        candidates=details or [owner] if normalized(get(owner,'Commodity'))=='thermal' else details
        if any(normalized(get(row,'Type'))=='make' for row in candidates):return True
    return False


def _input_number(record,name):
    marker=object();value=get(record,name,marker)
    if value is marker:raise CalculationError('thermal_input_missing','Original Thermal input field is absent',name)
    return ZERO if empty(value) else number(value,name)


def _metadata(record,context):
    code=required(record,'Material Code');values={}
    for name in ('Material Family','Material Sub Family','Material Grade'):
        values[name]=context.master.lookup('material_master',{'material_code':code},name)
    return values


def _rule(record,context,master,output,conditions):
    expression=select_rule(context,master,output,record,conditions,commodity='Thermal')
    return number(evaluate_expression(expression,record,excel_blank_zero=True),output)


def make_geometry(record,context,metadata):
    """Original16271-16278,16409-16578,16597-16737 in dependency order."""
    values={**record,**metadata};code=required(record,'Material Code')
    for name in ('Blank Length Allowance','Blank Width Allowance'):
        # An existing blank allowance is a linked empty Excel numeric cell.
        value=context.master.lookup('material_master',{'material_code':code},name)
        values[name]=ZERO if empty(value) else number(value,name)
    flow=get(record,'Flowtype')
    if flow is None:flow=get(record,'Flow type')
    if flow is None:raise CalculationError('thermal_input_missing','Original Thermal Flow type header is absent','Flow type')
    values['Flowtype']=flow
    quantity=_input_number(record,'Qty/Veh')
    if quantity==0:raise CalculationError('division_by_zero','Thermal Tube Pitch divides by Qty/Veh','Qty/Veh')
    values['Tube Pitch']=_input_number(record,'L' if normalized(flow)=='crossflow' else 'W')/quantity
    # Only these named Thermal source rules apply. The source's blank rule
    # outputs are named Input Blank Length/Width in its calculation sheet.
    values['Input Blank Length']=_rule(values,context,'blank_consumption_base','Blank Length',[f'Con{i}' for i in range(1,11)])
    values['Input Blank Width']=_rule(values,context,'blank_consumption_base','Blank Width',[f'Con{i}' for i in range(1,11)])
    values['Height/100']=values['Input Blank Length']/100
    values['Fin Height']=values['Tube Pitch']-_input_number(record,'Tube Height (Post Forming)')
    values['Fin Qty']=values['Height/100']*_input_number(record,'FPDM')
    values['Fin Length']=values['Fin Height']*values['Fin Qty']
    gross=_rule(values,context,'net_consumption_base','Cal RM cons',[f'Con{i}' for i in range(1,10)])
    return {name:values[name] for name in ('Blank Length Allowance','Blank Width Allowance','Flowtype','Tube Pitch','Input Blank Length','Input Blank Width','Height/100','Fin Height','Fin Qty','Fin Length')},gross


def calculate(record,context):
    metadata=_metadata(record,context)
    make_path=supplier_has_make(record,context)
    if make_path:geometry,gross=make_geometry(record,context,metadata)
    else:geometry={};gross=_input_number(record,'Part Wt')*Decimal('1.2')
    rate=material(context,record);rm=rate*gross
    checkpoint(context,record,{**metadata,**geometry,'Gross Wt':gross,'Cal RM cons':gross,'Rate Kg':rate,
        'Rate/UOM':rate,'RM Cost':rm,'RM Cost/Pc':rm})
    # Original15920/16280 starts Process Master lookup at Process Code and
    # returns its AD Rate/UOM field. The named mapping was verified against
    # the actual original header and current SQL metadata, without runtime
    # master coordinates or the unrelated process UOM string.
    conversion=context.require_master('process_master',{'Process Code':required(record,'Material Code')},FieldRef('Rate/UOM','AD'),blank_zero=True)
    process=rm*conversion
    values={**metadata,**geometry,'Gross Wt':gross,'Cal RM cons':gross,'Rate Kg':rate,'Rate/UOM':rate,
        'RM Cost':rm,'RM Cost/Pc':rm,'Core Coversion Cost Rate':conversion,'Core Conversion Rate':conversion,
        'Conversion Cost':rm*15,'Process Cost':process,'Process Cost/Pc':process,'BASIC Cost/Pc':rm+process,'Basic Cost/Pc':rm+process}
    # These Thermal branches have no catch-to-zero for missing applicability.
    values=markups(context,record,values)
    values['MARKUP total']=values['MARKUP/Pc'];values['Total Cost']=values['Basic Cost/Pc']+values['MARKUP/Pc']
    # MAKE's PnF/grand-total writes are commented in the primary source;
    # non-MAKE explicitly adds 20% PnF at15974, with no invented master rate.
    pnf=ZERO if make_path else values['Total Cost']*Decimal('0.2')
    result=finish(record,values,pnf=pnf)
    result.update({'PnF':pnf,'Grand Total Cost':result['Grand Total Cost/Pc'],
        '_thermal_supplier_path':'make' if make_path else 'non_make',
        '_output_sheet_prefix':'make_' if make_path else 'Th_',
        '_thermal_source_lines':'make_hvac16256-16737' if make_path else 'hvac_part_nonmake15915-15975'})
    return result

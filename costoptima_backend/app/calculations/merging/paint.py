"""Original Paint Sr-block calculation and special-process ownership projection.

Primary paint598-654 and merge19362-19421,19504-19547. Physical
master positions are replaced by verified semantic headers and period labels.
No uploaded rates, make costs or markups are accepted as calculated producers.
"""
from decimal import Decimal
from ..common import CalculationError, get, empty, normalized, number, required
from ..masters import FieldRef
from ..commodities.shared import cost_base,checkpoint

ZERO=Decimal(0)
COSTS=('ICC','REJ ON INJ MOLDED PART','REJ ON MATERIAL & PROCESS','OVERALL REJECTION','OVERHEAD','PROFIT')


def _period_field(master,period):
    # Original Special RM's second header row carries APR#18/JUN#22 etc.
    # Step6 retains it as the unique data record without a Material Code.
    choices=[]
    for row in master.records('special_rm'):
        if not empty(row.value('Material Code')):continue
        for field in row.fields:
            if field.header and normalized(row.values.get(field.column))==normalized(period):
                choices.append(FieldRef(field.header,field.group))
    if len(choices)!=1:
        raise CalculationError('paint_period_mapping_missing','Paint RM Base must resolve to one verified Special RM period header','RM Base',period=str(period))
    return choices[0]


def calculate_paint_group(record,context):
    lines=record.get('_paint_components')
    if not isinstance(lines,list) or not lines:
        raise CalculationError('paint_group_missing','Paint requires the original parsed Sr group for this Customer No and End Item')
    anchor=lines[0]
    if empty(get(anchor,'Sr')):raise CalculationError('paint_group_anchor_missing','Paint group must start with its original Sr field')
    if any(not empty(get(line,'Sr')) for line in lines[1:]):raise CalculationError('paint_group_ambiguous','Several Sr anchors cannot share a Paint group')
    end=normalized(required(record,'End Item'));customer=normalized(required(record,'Customer No'))
    supplier=required(anchor,'Supplier')
    if any(normalized(required(line,'End Item'))!=end or normalized(required(line,'Customer No'))!=customer or normalized(required(line,'Supplier'))!=normalized(supplier) for line in lines):
        raise CalculationError('paint_owner_conflict','Paint group crosses Customer No, End Item or tier-2 supplier ownership')
    identity=str(required(anchor,'_record_id'))
    cache=getattr(context,'_paint_results',None)
    if cache is None:context._paint_results=cache={}
    if identity in cache:return dict(cache[identity])
    if any(normalized(get(line,'RM Base'))!=normalized(get(anchor,'RM Base')) for line in lines):
        raise CalculationError('paint_period_conflict','Original Paint uses one RM Base across the complete group')
    rm=ZERO;components=[]
    for line in lines:
        code=get(line,'Material Code');quantity=get(line,'Consumption (in Litre)')
        if empty(code):rate=ZERO
        else:
            period=required(line,'RM Base');field=_period_field(context.master,period)
            rate=context.require_master('special_rm',{'material_code':code},field,blank_zero=True)
        amount=rate*(ZERO if empty(quantity) else number(quantity,'Consumption (in Litre)'))
        rm+=amount
        data=dict(line)
        if not empty(code):data['Material']=context.master.lookup('special_rm',{'material_code':code},'Product Descrption')
        components.append({'inputs':data,'Rate / Litre':rate,'RM cost / Pc':amount,'Consumable / Masking Cost':ZERO})
        checkpoint(context,record,{'Completed Paint RM Cost':rm,'_paint_result':{'_paint_component_results':components,'Completed Paint RM Cost':rm}})
    rates_key={FieldRef('Supplier','Rates'):supplier}
    rates=context.master.record('special_norms',rates_key,missing='none')
    applicability=context.master.record('special_norms',{FieldRef('Supplier','Applicability'):supplier},missing='none')
    if applicability is None:
        # Primary uses a positional PnF fallback. No verified semantic default
        # is configured for missing suppliers; do not guess another supplier.
        raise CalculationError('paint_applicability_missing','Paint supplier has no named applicability/PnF record',supplier=str(supplier))
    hanger=ZERO if rates is None else number(rates.value(FieldRef('Hanger Cost','Rates')) or 0,'Hanger Cost')
    parts=number(required(anchor,'No of parts / Jig'),'No of parts / Jig')
    if parts<=0:raise CalculationError('division_by_zero','Paint No of parts / Jig must be positive')
    process=hanger/parts
    values={'Sr':get(anchor,'Sr'),'Surface Area (in SQM)':get(anchor,'Painting Area (in SQM)'),
        'Paint Supplier':supplier,'Paint RM Cost':rm,'RM Cost/Pc':rm,'Paint Process Cost':process,'Process Cost/Pc':process,
        'Consumable / Masking Cost':ZERO,'Hanger Cost':hanger,'Basic Cost':rm+process,'Basic Cost/Pc':rm+process}
    checkpoint(context,record,{**values,'_paint_result':{**values,'_paint_component_results':components}})
    make_part=get(anchor,'Make Part no')
    if not empty(make_part):values['Make Part Cost']=context.producer_by_customer(record,'HARD','MAKE','Basic Cost/Pc',make_part)
    total=ZERO
    for name in COSTS:
        rate=ZERO if rates is None else number(rates.value(FieldRef(name,'Rates')) or 0,name)
        # Named applicability is the meaning of the original numeric selector.
        # An unmapped/missing required base blocks, rather than accepting upload costs.
        amount=ZERO if rate==0 else rate*cost_base(applicability.value(FieldRef(name,'Applicability')),values)
        values[name]=rate;values[name+' Cost']=amount;total+=amount
        checkpoint(context,record,{**values,'_paint_result':{**values,'_paint_component_results':components}})
    pnf_factor=number(applicability.value(FieldRef('PnF','Applicability')) or 0,'Paint PnF')
    values.update({'Paint Markup':total,'Paint PnF':(rm+process+total)*pnf_factor,
        'MARKUP/Pc':total,'_paint_component_results':components})
    values['Paint Total Cost/Pc']=rm+process+total+values['Paint PnF']
    cache[identity]=dict(values)
    return values


paint_costs=calculate_paint_group

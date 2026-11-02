"""PCM make_new14588-15029; authoritative static six-policy formulas."""
from decimal import Decimal
from ...common import required,get,number,CalculationError,divide,normalized
from ..shared import material_details,material,markups,finish,value,process_rate,checkpoint
from ...masters import MasterLookupError
from . import shift_policy
SOURCE={'function':'make_new;merge','lines':'14588-15029;18501-18570'}
ZERO=Decimal(0)


def gross_weight(record,context,process):
    given=get(record,'Gross Wt')
    if process!='Rubber Molding' and given is not None and given!='':return number(given,'Gross Wt')
    part=value(record,'Part Wt')
    if process=='Rubber Molding':return part*Decimal('1.2')
    rules=context.master.records('norms_gross_wt')
    if str(get(record,'HRS Y or N','')).upper()=='Y':
        factors={row.decimal('hot_runner2') for row in rules if row.value('criteria') is not None}
        if len(factors)!=1:raise CalculationError('gross_weight_mapping_ambiguous','Hot-runner factors need an unambiguous semantic rule')
        return part*next(iter(factors))
    for row in rules:
        criteria=str(row.value('criteria') or '').strip().casefold()
        threshold=row.value('net_wt')
        if threshold is not None and part<=number(threshold):return part*row.decimal('hot_runner')
        if criteria=='rest all':return part*row.decimal('hot_runner')
    raise CalculationError('gross_weight_rule_missing','No gross-weight rule matches Part Wt')


def shift_field(record):
    category=str(get(record,'2D DWG/CAD Revision No','')).upper()
    if category in {'1K','2K'}:return 'c_'+category.casefold()
    ton=value(record,'m/c Tonnage')
    # These are actual named threshold columns, not positional lookup indices.
    return 'c_2001' if ton>=2001 else 'c_1000' if ton>=1000 else 'c_401' if ton>=401 else 'c_0'


def make_batch_selection(record,context):
    """Source14710-14725: first HARD MAKE detail selects RM period/location.

    This is the original named batch rule, not a missing-rate fallback. A blank
    first RM period is still an error; later nonblank rows are never guessed.
    Purchasing BNS has a separate source path and does not call this selector.
    """
    rows=[row for row in context.rows if normalized(get(row,'Commodity'))=='hard'
          and normalized(get(row,'Type'))=='make']
    positioned=[row for row in rows if isinstance(row.get('_detail_row'),int)]
    first=min(positioned,key=lambda row:row['_detail_row']) if positioned else next(iter(rows),record)
    period=required(first,'RM Base');location=get(first,'Sector & Location') or 'AD'
    if hasattr(context,'provenance'):
        context.provenance.append({'kind':'legacy_batch_selector','source':'make_new14710-14725',
            'record_id':first.get('_record_id'),'RM Base':period,'Sector & Location':location})
    return {**record,'RM Base':period,'Sector & Location':location}


def _material_stage(record,context,process,*,buy_non_std=False):
    rate=material(context,record)
    checkpoint(context,record,{'Material Rate':rate,'RM Base':get(record,'RM Base'),
        'Sector & Location':get(record,'Sector & Location')})
    gross_record={**record,'Gross Wt':None} if buy_non_std else record
    gross=gross_weight(gross_record,context,process)
    checkpoint(context,record,{'Gross Wt':gross,'RM Cost/Pc':rate*gross})
    return rate,gross


def calculate(record,context,*,buy_non_std=False):
    if not buy_non_std:record=make_batch_selection(record,context)
    code=required(record,'Material Code')
    requested=context.master.lookup('material_master',{'material_code':code},'Applicable Process',duplicate_policy='last',missing='default',default='Default')
    process=shift_policy.select(requested)
    checkpoint(context,record,{'Applicable Process':requested,'Shift Policy':process})
    try:tons=shift_policy.tonnage(record,requested,process) if process in shift_policy.NORM_POLICIES else None
    except CalculationError as error:
        # An incompatible machine/tonnage prevents conversion costing, but
        # independently verified RM arithmetic remains inspectable as partial.
        try:_material_stage(record,context,process,buy_non_std=buy_non_std)
        except (CalculationError,MasterLookupError) as partial_error:
            error.details['partial_issues']=[partial_error.as_dict() if isinstance(partial_error,CalculationError)
                else {'code':partial_error.code,'message':str(partial_error)}]
        raise
    rate,gross=_material_stage(record,context,process,buy_non_std=buy_non_std)
    efficiency=context.require_master('norms_make',{'supplier':required(record,'Supplier')},'efficiency')
    checkpoint(context,record,{'EFFICIENCY':efficiency})
    cycle=value(record,'Cycle Time');cavity=value(record,'Cavity')
    if cycle==0:raise CalculationError('division_by_zero','Cycle Time is zero','Cycle Time')
    pcs=Decimal(8)*3600*cavity*efficiency/cycle
    checkpoint(context,record,{'Pc per shift':pcs})
    if pcs==0:raise CalculationError('division_by_zero','Pc per shift is zero')
    if process in shift_policy.PROCESS_POLICIES:
        shift=ZERO;shift_rate=process_rate(context,record,required(record,'m/c Tonnage'),key='process')
        molding=divide(shift_rate,pcs,'Molding Cost');deflash=ZERO
    else:
        shift=context.require_master('norms_shift_factor',{'shift_factor':required(record,'Supplier')},shift_field(record))
        shift_rate=tons*shift
        # These are the actual PP/EP/Default formulas in the current reference,
        # even though inverse to the old initial formula at source14855.
        molding=divide(pcs,shift_rate,'Molding Cost')
        deflash=context.require_master('norms_shift_factor',{'shift_factor':'Deflashing'},'c_401' if tons>400 else 'c_0')
    material_fields=material_details(context,record)
    values={**material_fields,'Applicable Process':requested,'Shift Policy':process,'_shift_policy_reference':shift_policy.REFERENCE_SHA256,'RM Base':get(record,'RM Base'),'Sector & Location':get(record,'Sector & Location'),'Material Rate':rate,'Gross Wt':gross,'RM Cost/Pc':rate*gross,'Shift Factor':shift,'Shift Rate':shift_rate,
            'EFFICIENCY':efficiency,'Pc per shift':pcs,'Molding Cost':molding,'Deflashing cost':deflash,'Process Cost/Pc':molding+deflash}
    values['Basic Cost/Pc']=values['RM Cost/Pc']+values['Process Cost/Pc']
    return finish(record,markups(context,record,values,blank_applicability_zero=True))

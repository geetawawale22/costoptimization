"""PCM sand_casting_for_range:7616-8040 named-field arithmetic.

The PROCESS entry shares this graph: original7817-7824 still includes RM when
its Material Code is present. Only a blank code on that path leaves RM blank.
"""
from decimal import Decimal
from ...common import get,number,empty,CalculationError
from ..shared import material,process_lines,markups,finish,checkpoint
SOURCE={'function':'sand_casting_for_range','lines':'7616-8040'}
ZERO=Decimal(0)


def _linked_number(record,field):
    marker=object();raw=get(record,field,marker)
    if raw is marker:raise CalculationError('missing_input',f'Required input {field} is missing',field)
    return ZERO if empty(raw) else number(raw,field)


def calculate_range(record,context,*,allow_blank_material=False,derived_material_rate=None):
    code=get(record,'Material Code')
    # Only a same-run MAKE producer may supply this explicit internal argument.
    # Uploaded Rate/UOM or similarly named record fields are never read.
    rate=(number(derived_material_rate,'Derived Material Rate') if derived_material_rate is not None
          else ZERO if allow_blank_material and empty(code) else material(context,record))
    gross=_linked_number(record,'Gross Wt');part=_linked_number(record,'Part Wt')
    rm=rate*gross
    checkpoint(context,record,{'Rate/UOM':rate,'Gross Wt':gross,'Part Wt':part,'RM Cost/Pc':rm})
    process=process_lines(context,record);scrap_rate=rate/4
    recovery=(gross-part)*scrap_rate*Decimal('0.85')
    values=markups(context,record,{'Rate/UOM':rate,'Scrap Rate/Kg':scrap_rate,'Scrap Rate /Kg':scrap_rate,
        'Scrap Weight':gross-part,'Machining Scrap Recovery':recovery,
        'RM Cost/Pc':rm,'Process Cost/Pc':process,'Process Cost':process,'Basic Cost/Pc':rm+process})
    casting=values['Basic Cost/Pc']+values['MARKUP/Pc']
    # Source7939 excludes ancillary amounts from Casting Cost; source7945
    # includes them in the per-kg rate, before source7954 deducts recovery.
    ancillary=sum((number(get(record,name,0) or 0,name) for name in ('Tool Cost','Packing','Freight')),ZERO)
    before_recovery=casting+ancillary
    values.update({'Total Markup':values['MARKUP/Pc'],'Casting Cost':casting,'Ancillary Cost':ancillary,
        'Total Casting rate /Kg':before_recovery/gross if gross else ZERO,
        'Total':before_recovery-recovery,'Total Cost/Pc':before_recovery-recovery})
    if str(get(record,'Type') or '').strip().upper()=='PROCESS':values['Total Process Markup']=values['MARKUP/Pc']
    return finish(record,values)


def calculate(record,context,*,derived_material_rate=None):
    return calculate_range(record,context,derived_material_rate=derived_material_rate)

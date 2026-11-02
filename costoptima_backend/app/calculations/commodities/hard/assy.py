"""PCM assy 14313-14386 and merge 19005-19143, verified semantic blocks."""
from decimal import Decimal
from ...common import required, get, number
from ..shared import value, finish, checkpoint

SOURCE = {'function': 'assy; merge', 'lines': '14313-14386;19005-19143'}


def calculate(record, context):
    supplier = required(record, 'Supplier')
    operation = get(record, 'Operation Process')
    if operation in (None, ''):operation=required(record, 'Process / Assy Name')
    raw_ct = get(record, 'CT(sec)')
    if raw_ct is None:
        raw_ct = get(record, 'Cycle Time')
    # Explicit zero CT follows the original "derive" branch. Populated CT
    # bypasses both handling and point-time addition (merge 19041-19051).
    supplied = raw_ct is not None and raw_ct != '' and number(raw_ct, 'CT(sec)') != 0
    if supplied:
        ct = number(raw_ct, 'CT(sec)')
        handling = Decimal(0)
        point_time = Decimal(0)
    else:
        points = value(record, 'No of Points')
        per_point = Decimal(str(context.master.assy(supplier, operation, metric='CT')))
        handling = Decimal(str(context.master.assy(supplier, operation, metric='Handling')))
        point_time = points * per_point
        ct = point_time + handling
    checkpoint(context,record,{'ASSY Point Time':point_time,'Handling Time':handling,'CT(sec)':ct})
    mhr = Decimal(str(context.master.assy(supplier, operation, metric='MHR')))
    basic = ct * mhr / 3600
    values = {'ASSY Point Time': point_time, 'Handling Time': handling, 'CT(sec)': ct,
              'MHR': mhr, 'RM Cost/Pc': Decimal(0), 'Process Cost/Pc': basic, 'Basic Cost/Pc': basic}
    checkpoint(context,record,values)
    for name in ('ICC', 'REJ', 'OH', 'PROFIT'):
        rate = context.require_master('norms_assy', {'supplier': supplier}, name)
        values[name] = rate
        values[name + ' COST'] = basic * rate
        checkpoint(context,record,values)
    # Original overall markup is independent of the four displayed components.
    overall = context.require_master('norms_assy', {'supplier': supplier}, 'markup')
    values['MARKUP/Pc'] = basic * overall
    values['Markup'] = values['MARKUP/Pc']
    values['Total'] = basic + values['Markup']
    return finish(record, values)

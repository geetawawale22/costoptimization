"""Original SOFT Sr-group special-process projection, not an ASSY alias.

Source soft884, special_process6509-6558 and merge19508-19548. Raw SOFT
components calculate through the existing part1 port once as producer-only
results; their grouped costs belong to the final special-process owner.
"""
from decimal import Decimal

from ...common import CalculationError, get, normalized, required
from ...merging.special_process import calculate_soft_group
from ..shared import finish,checkpoint

SOURCE = {'function': 'soft;part1;special_process;merge',
          'lines': '877-1014;1548-3954;6509-6558;19508-19548'}


def calculate(record, context):
    mode = str(get(record, 'Special Process') or get(record, 'Process / Assy Name') or '').strip()
    if not mode:
        raise CalculationError('missing_special_process', 'SOFT special process requires its configured process name', 'Special Process')
    # dt comes from this actual master in primary213/6513. Its applicability
    # flags do not replace the explicit later MAKE PAINT/BUY PAINT branches.
    configured = context.master.record('norms_special_process_norms', {'special_process': mode}, missing='none')
    if configured is None:
        raise CalculationError('unsupported_special_process', 'This process is not present in the configured original special-process catalogue', process=mode)
    grouped = calculate_soft_group(record, context)
    basic = grouped['Basic cost']
    values = {**grouped, 'Process / Assy Name': mode,
              'RM Cost/Pc': grouped['RM Cost'], 'Process Cost/Pc': grouped['Process Cost'],
              'Basic Cost/Pc': basic, 'Tier 2 Markup+PnF': grouped['Tier 2 Markup+PnF']}
    checkpoint(context,record,values)
    if normalized(mode) == 'buy paint':
        supplier = required(record, 'Supplier')
        selected = context.master.record('norms_buy_non_std', {'supplier': supplier}, missing='none')
        if selected is None:
            # Original Norms fallback19539-19542 is semantically the Other
            # supplier, verified in both original and current output layouts.
            supplier = 'Other'
        for name, field in (('ICC', 'icc'), ('REJ', 'rej'), ('OH', 'handling'), ('PROFIT', 'profit')):
            rate = context.require_master('norms_buy_non_std', {'supplier': supplier}, field, blank_zero=True)
            values[name] = rate
            values[name + ' COST'] = basic * rate
            checkpoint(context,record,values)
    elif normalized(mode) == 'make paint':
        # The source copies Paint AI:AN amounts rather than applying SOFT
        # percentages to Q. Accept only calculated same-run provider results.
        try:
            from ...merging.paint import calculate_paint_group
        except ImportError as error:
            raise CalculationError('soft_make_paint_provider_missing', 'MAKE PAINT requires the original same-run Paint producer', partial_values=values) from error
        paint = calculate_paint_group(record, context)
        values['_paint_result']=paint
        values.update({'ICC':paint['ICC'],'REJ':sum(paint[name] for name in ('REJ ON INJ MOLDED PART','REJ ON MATERIAL & PROCESS','OVERALL REJECTION')),'OH':paint['OVERHEAD'],'PROFIT':paint['PROFIT']})
        values['ICC COST'] = required(paint, 'ICC Cost')
        values['REJ COST'] = sum((required(paint, name + ' Cost') for name in
                                  ('REJ ON INJ MOLDED PART', 'REJ ON MATERIAL & PROCESS', 'OVERALL REJECTION')), Decimal(0))
        values['OH COST'] = required(paint, 'OVERHEAD Cost')
        values['PROFIT COST'] = required(paint, 'PROFIT Cost')
    else:
        # Original other configured selectors leave V:Y blank; SUM(V:Y)=0.
        for name in ('ICC', 'REJ', 'OH', 'PROFIT'):
            values[name] = Decimal(0)
            values[name + ' COST'] = Decimal(0)
    values['MARKUP/Pc'] = sum((values[name + ' COST'] for name in ('ICC', 'REJ', 'OH', 'PROFIT')), Decimal(0))
    values['MARKUP'] = values['MARKUP/Pc']
    values['Total cost /PC'] = basic + values['MARKUP/Pc']
    values['Total Cost/Pc'] = values['Total cost /PC']
    # The original Master Output Matrix selects SOFT's Total label. It must
    # expose this final owner total, not omit it or reuse a component total.
    values['Total'] = values['Total Cost/Pc']
    values['_soft_special_process_grouped'] = True
    return finish(record, values)

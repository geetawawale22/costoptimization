"""PCM consign 10748-10786; merge 19246-19323.

The legacy driver deliberately initializes both actual and basic cost to zero.
Handling is a group-volume rule; it does not fabricate a consigned purchase cost.
"""
from decimal import Decimal
from ..common import CalculationError, get, normalized, required
from ..commodities.shared import finish

SOURCE = {'function': 'consign; merge', 'lines': '10748-10786;19246-19347'}


def calculate(record, context):
    volume = context.consign_volume(record)
    if volume > 500:
        # The original shared handling cell looks up the first CONSIGN
        # supplier, so all rows use the same selected supplier rate.
        first = next((row for row in context.rows if normalized(get(row, 'Type')) == 'consign'), None)
        if first is None:
            raise CalculationError('consign_rows_missing', 'CONSIGN handling requires a source row')
        supplier = required(first, 'Supplier')
        field = 'handling_500'
    else:
        # The original lower-volume fallback selects the first merged Norms
        # record. Preserve configured source/row precedence, then resolve the
        # returned rate through its actual semantic supplier key and field.
        default = next(iter(context.master.records('norms_consign')), None)
        if default is None:
            raise CalculationError('consign_default_missing', 'No configured CONSIGN handling default is available')
        supplier = default.value('supplier')
        if supplier is None or str(supplier).strip() == '':
            raise CalculationError('consign_default_unmapped', 'The first CONSIGN default has no semantic supplier key')
        field = 'handling_500_2'
    rate = context.require_master('norms_consign', {'supplier': supplier}, field, blank_zero=True)
    actual, basic = Decimal(0), Decimal(0)
    values = {'Actual Cost/Pc': actual, 'Handling': rate, 'RM Cost/Pc': basic,
              'Process Cost/Pc': Decimal(0), 'Basic Cost/Pc': basic, 'MARKUP/Pc': actual * rate}
    values['Total/Pc'] = basic + values['MARKUP/Pc']
    return finish(record, values)

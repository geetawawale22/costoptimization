"""PCM buynonstd 6617-6695; merge 18815-18970 producer selection."""
from decimal import Decimal
from ..common import required, get, CalculationError
from ..commodities.shared import finish,checkpoint
from ..context import costing_supplier

SOURCE = {'function': 'buynonstd; merge', 'lines': '6617-6695;18815-18970'}
PRODUCER_FIELDS = {'HARD': 'Grand Total Cost/Pc', 'SOFT': 'Total', 'STAMPING': 'Total',
                   'PCBA': 'Total Cost/Pc', 'FORGING': 'Total', 'SAND CASTING': 'Total',
                   'GDC': 'Total', 'PDC': 'Total', 'THERMAL': 'Total Cost/Veh'}


def calculate(record, context):
    commodity = str(get(record, 'Commodity') or '').strip().upper()
    if not commodity:
        cost = context.require_master('non_std_bop', {'customer_no': required(record, 'Customer No')}, 'cost_pc')
    elif commodity in PRODUCER_FIELDS:
        # Same-run named producer; a missing producer is a dependency error.
        cost = context.require_result(record, commodity, 'BUY NON STD PRODUCER', PRODUCER_FIELDS[commodity],
                                      supplier=costing_supplier(record), owner_record_id=record.get('_record_id'))
    else:
        raise CalculationError('producer_missing', f'No source-proven BUY NON STD producer exists for {commodity}.')
    values = {'RM Cost/Pc': cost, 'Process Cost/Pc': Decimal(0), 'Basic Cost/Pc': cost}
    checkpoint(context,record,values)
    supplier = context.end_item_supplier(record)
    values['End Item Supplier'] = supplier
    if commodity=='PCBA':values['PCBA Basic Cost/Pc']=cost
    for name, field in [('ICC', 'icc'), ('REJ', 'rej'), ('OH', 'handling'), ('PROFIT', 'profit')]:
        # Original merge18924-18933 VLOOKUP of an existing blank percentage
        # returns Excel numeric0. Missing supplier/field is still an error.
        rate = context.require_master('norms_buy_non_std', {'supplier': supplier}, field, blank_zero=True)
        values[name] = rate
        values[name + ' COST'] = cost * rate
        checkpoint(context,record,values)
    values['Purchasing Markup Rate'] = context.require_master('norms_buy_non_std', {'supplier': supplier}, 'markup', blank_zero=True)
    values['MARKUP/Pc'] = cost * values['Purchasing Markup Rate']
    values['Markup'] = values['MARKUP/Pc']
    values['Total/Pc'] = cost + values['Markup']
    return finish(record, values)

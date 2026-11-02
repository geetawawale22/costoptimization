"""PCM bystd 6569-6609; merge 18698-18791."""
from decimal import Decimal
from ..common import required
from ..commodities.shared import finish,checkpoint

SOURCE = {'function': 'bystd; merge', 'lines': '6569-6609;18698-18791'}


def calculate(record, context):
    cost = context.require_master('std_bop', {'customer_no': required(record, 'Customer No')}, 'cost_pc')
    # Legacy BUY STD uses the End Item supplier, not the component supplier.
    values = {'RM Cost/Pc': cost, 'Process Cost/Pc': Decimal(0), 'Basic Cost/Pc': cost}
    checkpoint(context,record,values)
    supplier = context.end_item_supplier(record)
    for name, field in [('ICC', 'icc'), ('REJ', 'rej'), ('OH', 'handling'), ('PROFIT', 'profit')]:
        rate = context.require_master('norms_buy_std', {'supplier': supplier}, field)
        values[name] = rate
        values[name + ' COST'] = cost * rate
        checkpoint(context,record,values)
    values['MARKUP/Pc'] = cost * context.require_master('norms_buy_std', {'supplier': supplier}, 'markup')
    values['Markup'] = values['MARKUP/Pc']
    values['Total/Pc'] = cost + values['Markup']
    return finish(record, values)

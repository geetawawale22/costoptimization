"""PCM FRT 6750-6782: the source intentionally writes zero Basic Cost."""
from decimal import Decimal
from ..commodities.shared import finish
SOURCE = {'function': 'FRT', 'lines': '6750-6782'}

def calculate(record, context):
    return finish(record, {'RM Cost/Pc': Decimal(0), 'Process Cost/Pc': Decimal(0),
                          'Basic Cost/Pc': Decimal(0), 'MARKUP/Pc': Decimal(0)})

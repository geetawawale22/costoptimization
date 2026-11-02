"""PCM end_item 10792-10863 and End_Analysis 12089-12427."""
from ..common import CalculationError
SOURCE = {'function': 'end_item; End_Analysis', 'lines': '10792-10863;12089-12427'}

def calculate(record, context):
    # End Item is an owner/aggregation stage, never a new cost producer.
    if not hasattr(context, 'end_item_rollup'):
        raise CalculationError('end_item_ownership_unresolved', 'End Item needs completed owned-component results before aggregation.')
    return context.end_item_rollup(record)

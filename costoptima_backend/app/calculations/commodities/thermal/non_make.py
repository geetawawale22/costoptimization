"""Explicit all-non-MAKE supplier entry; dispatch uses the original batch predicate."""
from .make import calculate as thermal
SOURCE={'function':'hvac_part_nonmake','lines':'15760-16065'}
def calculate(record,context):return thermal(record,context)

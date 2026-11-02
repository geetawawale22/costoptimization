"""Explicit GDC BUY_NON_STD entry into the source family calculation."""
from .make import calculate as family_calculate
SOURCE={"function":"gdc_casting","branch":"buy_non_std"}
def calculate(record,context):return family_calculate(record,context)

"""Explicit FORGING BUY_NON_STD entry into the source family calculation."""
from .make import calculate as family_calculate
SOURCE={"function":"forging_data","branch":"buy_non_std"}
def calculate(record,context):return family_calculate(record,context)

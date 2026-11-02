"""Explicit GDC PROCESS entry into the source family calculation."""
from .make import calculate as family_calculate
SOURCE={"function":"gdc_casting","branch":"process"}
def calculate(record,context):return family_calculate(record,context)

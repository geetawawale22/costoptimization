"""SOFT BNS producer; purchasing markup runs as a dependency consumer."""
from .make import calculate as part1
SOURCE={'function':'part1;buynonstd;merge','lines':'1548-3954;6617-6695;18815-18970'}
def calculate(record,context):return part1(record,context)

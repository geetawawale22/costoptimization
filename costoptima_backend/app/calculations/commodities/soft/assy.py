"""SOFT ASSY follows part1's generic Type branch, not HARD operation costing."""
from .make import calculate as part1
SOURCE={'function':'part1','lines':'1617-1644;1548-3954'}
def calculate(record,context):return part1(record,context)

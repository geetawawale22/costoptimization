"""Original PROCESS entry: include supplied material and final ancillary costs."""
from .buy_non_std import calculate_range
SOURCE={'function':'sand_casting_for_range','lines':'7817-7824;7928-7954'}


def calculate(record,context):
    return calculate_range(record,context,allow_blank_material=True)

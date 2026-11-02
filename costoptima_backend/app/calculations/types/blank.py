"""Explicit blank-commodity/blank-Type dispatch; no invented cost branch."""
from ..common import CalculationError
SOURCE = {'function': 'module dispatcher', 'lines': '19936-19988'}

def calculate(record, context):
    raise CalculationError('unsupported_blank_branch',
        'The original PCM has no standalone blank-Type/blank-commodity calculation; ownership must be identified.')

"""Static original formulas from the authoritative Shift_Factor_Rate contract.

Original make_new14860-14925 and merge18501-18570 choose a policy by
Applicable Process, otherwise Default. Rates remain named database lookups.
No runtime Excel access, embedded master rate or old seven-policy fallback.
"""
from ...common import CalculationError,number

# Current workbook was re-saved; all 147 typed policy/header positions match prior evidence.
REFERENCE_SHA256 = '4dc3d332d5616b529ad2521340817dd20f44b41ad00692fa0a836a7d6dfed47a'
REFERENCE_POLICY_SHA256 = '495ec440e4c4e188fe0e7a8390e30d4a74d98d0312f4df50306bda1400002226'
PROCESS_POLICIES=frozenset({'Blow Molding','Roto Molding','Compression Molding'})
NORM_POLICIES=frozenset({'PP','EP','Default'})


def select(process):
    requested=str(process or 'Default').strip()
    return requested if requested in PROCESS_POLICIES|NORM_POLICIES else 'Default'


def tonnage(record,requested,policy):
    """A process label cannot be guessed into an ordinary tonnage bucket."""
    try:return number(record.get('m/c Tonnage'),'m/c Tonnage')
    except CalculationError as error:
        raise CalculationError('shift_policy_input_incompatible',
            'The selected authoritative shift policy requires numeric m/c Tonnage; no process-name-to-tonnage guess is allowed.',
            'm/c Tonnage',requested_process=str(requested),selected_policy=policy,
            reference='Shift_Factor_Rate.xlsx',reference_sha256=REFERENCE_SHA256) from error

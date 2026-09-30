"""Condition-engine behaviour, pinned against the legacy oracle and the real master.

Two findings from tools/diff_condition_engines.py are locked in here. They point in
opposite directions, which is why both need a test: a future change that "restores
legacy parity" must not silently revert the first, and one that "tidies the engine" must
not silently adopt the second.

1. BLANK IS NOT ZERO - CostOptima was wrong, now fixed to match legacy.
   The translator used to emit n(d(field, 0)) == 0.0, defaulting a blank field to 0. That
   made Net Consumption rule 9 ("L / Unfold=0 and Net RM consumption=0") fire for every
   Thermal part, whose sheet-metal fields are blank because they do not apply, so a
   zero-consumption rule was chosen ahead of the Thermal-specific rule 11. Legacy compares
   str(None) = "None", so a blank never equals a number.

2. EARLY EXIT - legacy is wrong, CostOptima deliberately does NOT copy it.
   Legacy progressive narrowing returns the moment one candidate remains, without checking
   that rule's remaining conditions. For a Rod part it selects Blank Consumption rule 4,
   whose own Con14 is "Material Family!=Rod". CostOptima evaluates every condition and
   selects the Rod-specific rule 6. Needs Confirmation from the business before any change.
"""
from pathlib import Path

import pytest

from app.services import pcm_master_normalize as nm
from app.services.pcm_legacy_conditions import (
    NO_MATCH, condition_matches, findcond, legacy_eq, legacy_ne, select_rule,
)

ROOT = Path(__file__).resolve().parents[1]
MASTER = ROOT.parent / "Master_Rate_Data.xlsx"


# --- legacy comparison semantics -------------------------------------------------------

@pytest.mark.parametrize("value,expected,result", [
    (None, 0.0, False),   # the Thermal defect: blank must not equal zero
    (0, 0.0, True),
    ("0", 0.0, True),
    (0.0, 0.0, True),
    ("kg", "KG", True),   # legacy upper-cases both sides
])
def test_legacy_eq(value, expected, result):
    assert legacy_eq(value, expected) is result


def test_blank_is_not_equal_to_zero_under_ne():
    assert legacy_ne(None, 0.0) is True


def test_operator_precedence_quirk_is_reproduced():
    """'x<=5' contains '=', so legacy splits on '=' and looks for field 'x<'."""
    with pytest.raises(KeyError):
        condition_matches("x<=5", {"x": 3})


def test_greater_than_skips_non_numeric_rather_than_failing():
    assert condition_matches("L / Unfold>0", {"L / Unfold": "N/A"}) is None
    assert condition_matches("L / Unfold>0", {"L / Unfold": None}) is None
    assert condition_matches("L / Unfold>0", {"L / Unfold": -3}) is None  # isdigit quirk


def test_forgiving_findcond_hides_later_rules_after_a_missing_field():
    rules = [{"C": "Missing Field>0"}, {"C": "UOM=Kg"}]
    assert findcond(rules, {"UOM": "Kg"}, "C") == NO_MATCH


# --- translator emits the fixed form ----------------------------------------------------

def test_numeric_equality_translates_without_zero_default():
    expression = nm.convert_condition("L / Unfold=0", "Con1")
    assert expression.startswith("legacy_eq(")
    assert ", 0)" not in expression


def test_threshold_translation_unchanged():
    assert nm.convert_condition("Blank Length>0", "Con11") == 'n(d("Blank Length", 0)) > 0.0'


# --- the two findings, on the real master ----------------------------------------------

needs_master = pytest.mark.skipif(not MASTER.exists(), reason="Master_Rate_Data.xlsx not present")


@needs_master
def test_early_exit_divergence_is_documented_not_copied():
    """Legacy picks rule 4 for a Rod part although rule 4 excludes Rod."""
    import tools.diff_condition_engines as harness
    from openpyxl import load_workbook

    wb = load_workbook(MASTER, data_only=True)
    try:
        raw, con, flt = harness._raw_rules(wb["Blank Consumption base"], 41, list(range(25, 33)))
    finally:
        wb.close()
    target = next(r for r in raw if r["_row"] == 6)
    row = next(p for name, p in harness._probes(target, raw, con, flt) if name == "satisfy")

    legacy = select_rule(harness._legacy_candidates(raw, con, flt, row), row, con)
    assert legacy["_row"] == 4
    assert condition_matches(legacy["Con14"], row) is False  # rule 4 excludes this part

    translated = [r for r in nm.normalize_master_workbook(MASTER)["Blank Consumption base"]
                  if r["Output"] == "Cal Blank Cons"]
    assert harness._costoptima_select(translated, row) == ("RULE", 6)

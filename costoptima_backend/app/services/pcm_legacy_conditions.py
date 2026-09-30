"""Faithful port of the legacy PCM condition engine: ``findcond`` and its callers.

This module is a REFERENCE ORACLE. It reproduces legacy behaviour bug-for-bug so that
CostOptima's rule selection can be measured against it on real data. It is deliberately
not an improvement on legacy - where legacy is surprising, this is surprising in the same
way, and each such point is marked ``LEGACY QUIRK``.

Source: ``Sheet_Metal_Forging_with summary_chk_Chnages.py``
    findcond              lines 1078-1184   (reads live Excel cells via COM; forgiving)
    findcondpart1         lines 1188-1280   (reads a DataFrame; strict)
    progressive narrowing lines 1686-1760+  (the Con1 -> Con2 -> Con3 ... chain in part1)

What the index-formula functions are NOT
----------------------------------------
``create_indexformula``, ``create_indexformula1``, ``create_indexformula2``,
``create_indexformulacon`` and ``icc_check`` compute no business value. Each rewrites a
formula written in column names (``{Part Wt}*{Rate}``) into Excel cell references
(``H5*K5``) so that legacy could write a live formula into the output workbook for Excel
to evaluate. ``dynamiccol`` resolves a column letter, tolerating a ``/Pc`` suffix.

CostOptima evaluates the column-name formula directly against the row instead
(``pcm_master_normalize.convert_template`` + the runtime evaluator) and writes values, per
the output policy recorded in ``data/pcm_output_native.json``. So those functions are
superseded by design rather than ported. What IS business logic - and must match legacy -
is which rule gets selected. That is this module.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

NO_MATCH = "No matching Condition"


def _blank(value: Any) -> bool:
    """Legacy's ``pd.isnull`` test on a condition cell."""
    if value is None:
        return True
    try:
        return value != value  # NaN
    except Exception:
        return False


def _coerce(value: Any):
    """``float(str(v).strip())`` else ``str(v).strip().upper()``, exactly as legacy.

    LEGACY QUIRK: ``str(None)`` is ``"None"``, so a missing value coerces to ``"NONE"``
    rather than to an empty string. A condition ``Field!=X`` therefore MATCHES a row whose
    Field is missing.
    """
    text = str(value).strip()
    try:
        return float(text)
    except (TypeError, ValueError):
        return text.upper()


def legacy_eq(value: Any, expected: Any) -> bool:
    """Legacy '=' comparison. A blank value never equals a number."""
    return _coerce(value) == _coerce(expected)


def legacy_ne(value: Any, expected: Any) -> bool:
    """Legacy '!=' comparison. A blank value is not-equal to any number."""
    return _coerce(value) != _coerce(expected)


def _looks_numeric(value: Any) -> bool:
    """Legacy's numeric test for ``<`` and ``>``: ``str(v).strip().split('.')[0].isdigit()``.

    LEGACY QUIRK: this rejects negative numbers ("-3"), thousands separators ("1,200"),
    exponents and the missing value ("None"). A rejected value does not fail the
    condition - it SKIPS it, and the search moves on to the next rule row.
    """
    return str(value).strip().split(".")[0].isdigit()


def condition_matches(condition: str, row: Mapping[str, Any]) -> bool | None:
    """Evaluate one condition cell as legacy did.

    Returns True (match), False (evaluated, no match) or None (skipped - legacy prints
    "Go to next if" and moves on). Raises KeyError when the referenced field is absent,
    which the forgiving ``findcond`` turns into NO_MATCH for the entire search.

    LEGACY QUIRK - operator precedence is by substring, in this fixed order:
        '!='  then  '='  then  '<'  then  '>'
    So ``x<=5`` contains '=' and is split on '=' into field "x<" and value "5". Field
    "x<" does not exist, so the whole findcond search returns NO_MATCH. The supplied
    master uses no '<=', '>=' or '<' conditions, so this cannot occur with current data.
    """
    text = str(condition)
    if "!=" in text:
        field, _, expected = text.partition("!=")
        return _coerce(row[field.strip()]) != _coerce(expected)
    if "=" in text:
        field, expected = text.split("=")[0], text.split("=")[-1]
        return _coerce(row[field.strip()]) == _coerce(expected)
    if "<" in text:
        field, expected = text.split("<")[0], text.split("<")[-1]
        threshold = float(expected.strip())
        value = row[field.strip()]
        if not _looks_numeric(value):
            return None
        return not (float(str(value).strip()) >= threshold)
    if ">" in text:
        field, expected = text.split(">")[0], text.split(">")[-1]
        threshold = float(expected.strip())
        value = row[field.strip()]
        if not _looks_numeric(value):
            return None
        return not (float(str(value).strip()) <= threshold)
    # LEGACY QUIRK: a condition with no operator matches no branch and is never selected.
    return None


def findcond(rules: Sequence[Mapping[str, Any]], row: Mapping[str, Any], column: str) -> str:
    """Return the first rule's condition text in ``column`` that matches ``row``.

    Forgiving variant (legacy ``findcond``): blank condition cells are skipped, and ANY
    exception - a missing field, an unparseable threshold - returns NO_MATCH for the whole
    search rather than just skipping that one rule.

    LEGACY QUIRK: because the try wraps the whole loop, a bad field name in an EARLY rule
    row hides every later rule row, even ones that would have matched.
    """
    try:
        for rule in rules:
            condition = rule.get(column)
            if _blank(condition):
                continue
            if condition_matches(condition, row) is True:
                return condition
        return NO_MATCH
    except Exception:
        return NO_MATCH


def findcondpart1(rules: Sequence[Mapping[str, Any]], row: Mapping[str, Any], column: str) -> str:
    """Strict variant (legacy ``findcondpart1``): no blank skip, no outer try.

    A blank condition cell or a missing field RAISES instead of returning NO_MATCH. Kept
    for completeness; the progressive-narrowing chain below uses the forgiving variant.
    """
    for rule in rules:
        condition = rule.get(column)
        if condition_matches(condition, row) is True:
            return condition
    return NO_MATCH


def select_rule(rules: Sequence[Mapping[str, Any]], row: Mapping[str, Any],
                condition_columns: Iterable[str]) -> Mapping[str, Any] | None:
    """Legacy progressive narrowing (part1, lines 1686 onward).

    Start with every rule. For each condition column in order:
      1. findcond finds the FIRST rule whose condition in that column matches
      2. keep EVERY rule whose condition text in that column equals that string
      3. stop as soon as exactly one rule remains

    Returns the single surviving rule, or None.

    LEGACY QUIRK: step 2 compares condition TEXT, so NO_MATCH filters to rules whose cell
    literally reads "No matching Condition" - i.e. none. Once more than one rule survives
    and the next column yields no match, the candidate set becomes empty and no rule is
    selected, even if a surviving rule has a blank condition that reads naturally as
    "otherwise". CostOptima's row-wise AND selects that rule instead.
    """
    candidates = list(rules)
    if len(candidates) == 1:
        return candidates[0]
    for column in condition_columns:
        chosen = findcond(candidates, row, column)
        candidates = [rule for rule in candidates if rule.get(column) == chosen]
        if len(candidates) == 1:
            return candidates[0]
        if not candidates:
            return None
    return None

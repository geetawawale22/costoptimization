"""Differential test: legacy rule selection vs CostOptima rule selection, on real data.

    python tools/diff_condition_engines.py ["../Master_Rate_Data.xlsx"] [--json out.json]

For every output group in the real Net / Blank / Gross consumption tables, this generates
input rows that probe each rule's decision boundaries, then selects a rule two ways:

    legacy      app/services/pcm_legacy_conditions.select_rule  (progressive narrowing)
    costoptima  the runtime's own algorithm: AND each rule's translated conditions,
                keep every rule that is true, take the lowest Priority

CostOptima's side uses the runtime's real ``number`` and ``norm`` functions and the
translated Condition expressions exactly as the importer writes them, evaluated with
simpleeval as the runtime does. Nothing is reimplemented on that side.

Probe rows per rule:
    satisfy    every condition of this rule set to a value that meets it
    blank      each referenced numeric field set to None
    text       each referenced numeric field set to "N/A"
    boundary   each '>' threshold hit exactly (x == threshold)

A disagreement is reported with the rule each engine chose. Nothing is "fixed" here: this
tool measures, and the decision about which behaviour is intended is a business call.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

import simpleeval
from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services import pcm_master_normalize as nm  # noqa: E402
from app.services.pcm_legacy_conditions import legacy_eq, legacy_ne, select_rule  # noqa: E402
from app.services.pcm_runtime import PcmError, norm, number  # noqa: E402

# Same grouping the importer uses (pcm_master_normalize.normalize_master_workbook).
GROUPS = {
    "Net Consumption base": [("Cal RM cons", 20, range(1, 10))],
    "Blank Consumption base": [
        ("Blank Length", 15, range(1, 11)),
        ("Blank Width", 21, range(1, 11)),
        ("Cal Blank Cons", 41, range(25, 33)),
        ("Cal Parts Per Blank", 54, range(45, 50)),
    ],
    "Gross Consumption base": [
        ("Cal parts / Sheet", 48, range(31, 38)),
        ("Gross Consumption", 78, range(53, 58)),
        ("Total Surface Area", 109, range(84, 89)),
    ],
}
_OP = re.compile(r"^\s*(.+?)\s*(<>|!=|>=|<=|=|>|<)\s*(.*?)\s*$")


def _raw_rules(ws, formula_col, condition_cols):
    """Rule rows with raw legacy condition text.

    Returns (rules, con_keys, filter_keys). Legacy treats the two kinds of column in the
    importer's condition range differently, so they are separated here:

      con_keys     columns whose row-2 header starts with 'Con' - the chain legacy walks
                   with findcond('Con1'), findcond('Con2'), ... (lines 1690-1760)
      filter_keys  other headers, e.g. 'Commodity'. Legacy uses these to pick WHICH table
                   to run the chain on (line 1072: df[df['Commodity']=='Thermal']), never
                   as a link in the chain itself.

    Keys are the real header names so the probe rows use genuine field names.
    """
    headers = {col: str(ws.cell(2, col).value or "").strip() for col in condition_cols}
    con_keys = [headers[c] for c in condition_cols if headers[c].casefold().startswith("con")]
    filter_keys = [headers[c] for c in condition_cols
                   if headers[c] and not headers[c].casefold().startswith("con")]
    rules = []
    for r in range(3, ws.max_row + 1):
        if ws.cell(r, formula_col).value in nm._BLANK:
            continue
        rule = {"_row": r}
        for col in condition_cols:
            value = ws.cell(r, col).value
            rule[headers[col]] = None if value in nm._BLANK else str(value).strip()
        rules.append(rule)
    return rules, con_keys, filter_keys


def _parse(text):
    match = _OP.match(str(text))
    return match.groups() if match else None


def _probes(rule, all_rules, con_keys, filter_keys):
    """Generate input rows targeting this rule's decision boundaries."""
    fields = {}
    for other in all_rules:
        for key in con_keys:
            text = other.get(key)
            if text:
                parsed = _parse(text)
                if parsed:
                    fields.setdefault(parsed[0].strip(), None)
    base = dict(fields)
    # A part belongs to one commodity; realistic probes carry the target rule's value.
    for key in filter_keys:
        base[key] = rule.get(key)
    numeric_fields = set()
    for key in con_keys:
        text = rule.get(key)
        if not text:
            continue
        parsed = _parse(text)
        if not parsed:
            continue
        field, op, expected = (p.strip() for p in parsed)
        if op == "=":
            base[field] = expected
        elif op in ("!=", "<>"):
            base[field] = "ZZ_OTHER_VALUE"
        elif op in (">", ">="):
            base[field] = float(expected) + 1
            numeric_fields.add((field, float(expected)))
        elif op in ("<", "<="):
            base[field] = float(expected) - 1
            numeric_fields.add((field, float(expected)))
    yield "satisfy", dict(base)
    for field, threshold in sorted(numeric_fields):
        yield f"blank:{field}", {**base, field: None}
        yield f"text:{field}", {**base, field: "N/A"}
        yield f"boundary:{field}", {**base, field: threshold}


def _legacy_candidates(rules, con_keys, filter_keys, row):
    """Reproduce legacy's table preparation before the Con chain starts.

    1. dropna on the FIRST Con column (line 1065-1066: dropna(subset=['Con1'])).
    2. Commodity table selection (line 1072). A rule with a Commodity value is only in
       play for a part of that commodity; a blank Commodity rule is in the general table.
    """
    first = con_keys[0] if con_keys else None
    kept = [r for r in rules if first is None or r.get(first)]
    for key in filter_keys:
        target = row.get(key)
        kept = [r for r in kept if not r.get(key) or
                str(r.get(key)).strip().casefold() == str(target or "").strip().casefold()]
    return kept


def _costoptima_select(translated, row):
    """The runtime algorithm, using the runtime's own number/norm."""
    def d(key, *default):
        value = row.get(key)
        if value is not None:
            return value
        if default:
            return default[0]
        raise PcmError(f"Missing derived/input value {key!r}", "MISSING_INPUT")

    evaluator = simpleeval.EvalWithCompoundTypes(
        names={}, functions={"n": number, "text": norm, "d": d,
                                  "legacy_eq": legacy_eq, "legacy_ne": legacy_ne})
    matches = []
    for rule in translated:
        try:
            if evaluator.eval(rule["Condition"]):
                matches.append(rule)
        except PcmError as exc:
            # The runtime evaluates every candidate; one raising aborts the whole lookup.
            return ("ERROR", exc.code)
    if not matches:
        return ("NONE", None)
    matches.sort(key=lambda r: r["Priority"])
    return ("RULE", matches[0]["Priority"])


def run(master: Path) -> dict:
    translated_all = nm.normalize_master_workbook(master)
    wb = load_workbook(master, read_only=False, data_only=True)
    report = {"master": str(master), "groups": [], "totals": Counter()}
    try:
        for table, specs in GROUPS.items():
            ws = wb[table]
            for output, formula_col, condition_cols in specs:
                raw, con_keys, filter_keys = _raw_rules(ws, formula_col, list(condition_cols))
                translated = [r for r in translated_all[table] if r["Output"] == output]
                group = {"table": table, "output": output, "rules": len(raw),
                         "con_columns": con_keys, "filter_columns": filter_keys,
                         "probes": 0, "agree": 0, "disagreements": []}
                for rule in raw:
                    for probe, row in _probes(rule, raw, con_keys, filter_keys):
                        group["probes"] += 1
                        candidates = _legacy_candidates(raw, con_keys, filter_keys, row)
                        chosen = select_rule(candidates, row, con_keys)
                        legacy = ("RULE", chosen["_row"]) if chosen else ("NONE", None)
                        modern = _costoptima_select(translated, row)
                        if legacy == modern:
                            group["agree"] += 1
                        else:
                            group["disagreements"].append({
                                "target_rule_row": rule["_row"], "probe": probe,
                                "legacy": legacy, "costoptima": modern,
                                "row": {k: v for k, v in row.items() if v is not None}})
                report["totals"]["probes"] += group["probes"]
                report["totals"]["agree"] += group["agree"]
                report["totals"]["disagree"] += len(group["disagreements"])
                report["groups"].append(group)
    finally:
        wb.close()
    report["totals"] = dict(report["totals"])
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("master", nargs="?", default=str(ROOT.parent / "Master_Rate_Data.xlsx"))
    parser.add_argument("--json", help="write the full report here")
    args = parser.parse_args(argv)
    report = run(Path(args.master))
    for g in report["groups"]:
        n = len(g["disagreements"])
        print(f"{g['table']:<24} {g['output']:<20} rules={g['rules']:<3} "
              f"probes={g['probes']:<4} agree={g['agree']:<4} disagree={n}")
    t = report["totals"]
    print(f"\nTOTAL probes={t.get('probes',0)} agree={t.get('agree',0)} disagree={t.get('disagree',0)}")
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

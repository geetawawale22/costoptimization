"""Config-driven legacy-parity regression runner for the PCM engine.

Runs one or more parity cases declared in ``data/pcm_parity_cases.json``: each case
imports the real master workbook into a throwaway SQLite database, calculates the real
input workbook through the database-backed engine, exports the result, and only then
opens the legacy expected workbook to compare values.

This replaces the single hard-coded case that lived inside ``verify_pcm_reference.py``.
Adding a parity case for another commodity is now a JSON edit, not a code change, which
is what the remaining per-commodity parity work needs in order to proceed.

Usage
-----
    python tools/verify_pcm_parity.py                 # run every enabled case
    python tools/verify_pcm_parity.py --case s302_bso_cover
    python tools/verify_pcm_parity.py --list
    python tools/verify_pcm_parity.py --out docs/pcm/verification

Exit code is non-zero if any case reports a mismatch, a calculation error, or could not
run (for example because its fixture files are absent).
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

from openpyxl import load_workbook
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import models  # noqa: F401  (registers mappers)
from app.database import Base
from app.import_pcm_master import extract_master, install_master
from app.services.pcm_runtime import (
    Interpreter,
    read_inputs,
    snapshot_masters,
    write_snapshot_output,
    digest,
)

CASES_FILE = ROOT / "data" / "pcm_parity_cases.json"


def _resolve(path_text: str) -> Path:
    path = Path(path_text)
    return path if path.is_absolute() else (ROOT / path).resolve()


def _sheet_records(workbook, sheet, header_row=0, first_data_row=None, require_non_null_index=None):
    """Return the sheet as dicts keyed by the header row.

    ``header_row``/``first_data_row`` exist because some legacy sheets (SBC) carry a
    display-label row above the real header.
    """
    if sheet not in workbook.sheetnames:
        raise KeyError(f"sheet {sheet!r} not present; available: {workbook.sheetnames}")
    values = list(workbook[sheet].values)
    if not values:
        return []
    first_data_row = header_row + 1 if first_data_row is None else first_data_row
    headers = values[header_row]
    records = []
    for row in values[first_data_row:]:
        if require_non_null_index is not None and row[require_non_null_index] is None:
            continue
        records.append(dict(zip(headers, row)))
    return records


class Comparator:
    """Collects comparisons and mismatches for one case."""

    def __init__(self, tolerance: float):
        self.tolerance = tolerance
        self.comparisons = []
        self.mismatches = []
        self.skipped = []

    def compare(self, sheet, identity, field, actual, expected):
        # A blank cell in the legacy workbook is not an assertion about a value.
        if expected is None:
            return
        item = {"sheet": sheet, "identity": identity, "field": field,
                "actual": actual, "expected": expected}
        if not isinstance(expected, (int, float)) or isinstance(expected, bool):
            self.skipped.append({**item, "reason": "expected value is not numeric"})
            return
        self.comparisons.append(item)
        if actual is None or not isinstance(actual, (int, float)) or abs(actual - expected) > self.tolerance:
            self.mismatches.append(item)


def _actual_value(result, column):
    """Resolve a comparison column from a calculation result.

    Computed components live in ``result["values"]``; pass-through columns carried
    from the BOM (quantity per variant, Qty/Veh, identifiers) live in
    ``result["input"]``. Values take priority so a computed column can never be
    shadowed by a same-named input column.
    """
    values = result.get("values") or {}
    if column in values:
        return values[column]
    return (result.get("input") or {}).get(column)


def _match(records, criteria):
    for record in records:
        if all(record.get(key) == value for key, value in criteria.items()):
            return record
    return None


def run_case(case, out_dir: Path, return_details: bool = False):
    """Execute one parity case and return its report dict.

    With ``return_details`` the tuple ``(report, definition, masters, rows, results)``
    is returned instead, which is the signature the original
    ``verify_pcm_reference.reference_run`` exposed to tests.
    """
    name = case["name"]
    report = {"case": name, "description": case.get("description", ""), "status": "Completed"}

    input_path = _resolve(case["input"])
    master_path = _resolve(case["master"])
    expected_path = _resolve(case["expected"])
    missing = [str(p) for p in (input_path, master_path, expected_path) if not p.exists()]
    if missing:
        report.update(status="Fixture Missing", missing_files=missing,
                      note="Case skipped: supply these workbooks to verify this path.")
        return (report, None, None, None, None) if return_details else report

    definition = json.loads(_resolve(case["definition"]).read_text(encoding="utf-8"))
    layout = json.loads(_resolve(case["master_layout"]).read_text(encoding="utf-8"))
    tolerance = float(case.get("tolerance", 1e-8))

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as db:
            install_master(db, extract_master(master_path, layout))
            db.commit()
            masters = snapshot_masters(db, definition)
    finally:
        engine.dispose()

    rows = read_inputs(str(input_path), definition)
    results = Interpreter(definition, masters).run(rows)

    out_dir.mkdir(parents=True, exist_ok=True)
    calculated_path = out_dir / f"{name}_calculated.xlsx"
    snapshot = SimpleNamespace(
        definition=definition, masters=masters, results=results,
        input_sha256=hashlib.sha256(input_path.read_bytes()).hexdigest(),
        definition_sha256=digest(definition),
    )
    write_snapshot_output(snapshot, calculated_path)

    cmp = Comparator(tolerance)
    expected = load_workbook(expected_path, data_only=True)
    calculated = load_workbook(calculated_path, data_only=True)
    try:
        variants = definition["output"]["variants"]

        def columns_for(spec):
            if spec == "@variants":
                return {variant: variant for variant in variants}
            return spec

        # --- per-profile row comparisons -------------------------------------
        profile_sheets = case.get("profile_sheets", {})
        profile_details = case.get("profile_details", {})
        cache = {}
        for result in results:
            profile = result.get("profile")
            spec = profile_sheets.get(profile)
            if not spec:
                continue
            sheet = spec["sheet"]
            if sheet not in cache:
                cache[sheet] = _sheet_records(
                    expected,
                    sheet,
                    header_row=spec.get("expected_header_row", 0),
                    first_data_row=spec.get("expected_first_data_row"),
                    require_non_null_index=spec.get("expected_require_non_null_index"),
                )
            criteria = {key: result["input"].get(key) for key in spec.get("match_on", ["Customer No"])}
            identity = criteria.get("Customer No") or next(iter(criteria.values()), None)
            target = _match(cache[sheet], criteria)
            if target is None:
                cmp.mismatches.append({"sheet": sheet, "identity": identity, "field": "<row>",
                                       "actual": "calculated", "expected": "no matching legacy row"})
                continue
            for actual_col, expected_col in spec["columns"].items():
                cmp.compare(sheet, identity, expected_col,
                            _actual_value(result, actual_col), target.get(expected_col))

            detail = profile_details.get(profile)
            if detail:
                dsheet = detail["expected_sheet"]
                key = (dsheet, "detail")
                if key not in cache:
                    cache[key] = _sheet_records(
                        expected,
                        dsheet,
                        header_row=detail.get("expected_header_row", 0),
                        first_data_row=detail.get("expected_first_data_row"),
                        require_non_null_index=detail.get("expected_require_non_null_index"),
                    )
                require = detail.get("require_non_null_column")
                candidates = [r for r in cache[key]
                              if r.get(detail["match_expected_column"]) == identity
                              and (require is None or r.get(require) is not None)]
                if candidates:
                    for actual_col, expected_col in detail["columns"].items():
                        cmp.compare(dsheet, identity, expected_col,
                                    result["values"].get(actual_col), candidates[0].get(expected_col))

        # --- rollup sheets (End Item / Analysis / SBC) ------------------------
        for rollup in case.get("rollups", []):
            sheet, identity_col = rollup["sheet"], rollup["identity"]
            expected_rows = _sheet_records(
                expected, sheet,
                header_row=rollup.get("expected_header_row", 0),
                first_data_row=rollup.get("expected_first_data_row"),
                require_non_null_index=rollup.get("expected_require_non_null_index"),
            )
            calculated_rows = _sheet_records(calculated, sheet)
            cols = columns_for(rollup.get("columns", "@variants"))
            default = 0 if rollup.get("missing_actual_as_zero") else None
            for actual_row in calculated_rows:
                identity = actual_row.get(identity_col)
                target = _match(expected_rows, {identity_col: identity})
                if target is None:
                    continue
                for actual_col, expected_col in cols.items():
                    cmp.compare(sheet, identity, expected_col,
                                actual_row.get(actual_col, default), target.get(expected_col))

        # --- detail sheets (per-material Paint lines, etc.) -------------------
        for detail in case.get("detail_sheets", []):
            sheet = detail["sheet"]
            expected_rows = _sheet_records(
                expected,
                sheet,
                header_row=detail.get("expected_header_row", 0),
                first_data_row=detail.get("expected_first_data_row"),
                require_non_null_index=detail.get("expected_require_non_null_index"),
            )
            calculated_rows = _sheet_records(
                calculated,
                sheet,
                header_row=detail.get("actual_header_row", 0),
                first_data_row=detail.get("actual_first_data_row"),
                require_non_null_index=detail.get("actual_require_non_null_index"),
            )
            require = detail.get("require_non_null")
            for actual_row in calculated_rows:
                if require and actual_row.get(require) is None:
                    continue
                criteria = {key: actual_row.get(key) for key in detail["match_on"]}
                target = _match(expected_rows, criteria)
                if target is None:
                    continue
                identity = actual_row.get(require) or next(iter(criteria.values()), None)
                for actual_col, expected_col in detail["columns"].items():
                    cmp.compare(sheet, identity, expected_col,
                                actual_row.get(actual_col), target.get(expected_col))
    finally:
        expected.close()
        calculated.close()

    errors = [r.get("issues") for r in results if r.get("status") != "Completed"]
    report.update(
        input_sha256=snapshot.input_sha256,
        master_sha256=hashlib.sha256(master_path.read_bytes()).hexdigest(),
        expected_sha256=hashlib.sha256(expected_path.read_bytes()).hexdigest(),
        rows=len(results),
        errors=errors,
        comparisons=len(cmp.comparisons),
        mismatches=cmp.mismatches,
        non_numeric_skipped=len(cmp.skipped),
        max_absolute_difference=max(
            (abs(c["actual"] - c["expected"]) for c in cmp.comparisons
             if isinstance(c["actual"], (int, float))), default=0),
        calculated_workbook=str(calculated_path.relative_to(ROOT) if calculated_path.is_relative_to(ROOT) else calculated_path),
        scope=case.get("scope", ""),
        profiles_exercised=sorted({r.get("profile") for r in results if r.get("profile")}),
    )
    if errors or cmp.mismatches:
        report["status"] = "Mismatch"
    if return_details:
        return report, definition, masters, rows, results
    return report


def load_case(name, cases_file=CASES_FILE):
    """Return the declared case dict with the given name."""
    config = json.loads(Path(cases_file).read_text(encoding="utf-8"))
    for case in config.get("cases", []):
        if case["name"] == name:
            return case
    raise KeyError(f"no parity case named {name!r} in {cases_file}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", action="append", help="case name (repeatable); default is every enabled case")
    parser.add_argument("--list", action="store_true", help="list declared cases and exit")
    parser.add_argument("--out", default="docs/pcm/verification", help="output directory for reports")
    parser.add_argument("--cases-file", default=str(CASES_FILE))
    args = parser.parse_args(argv)

    config = json.loads(Path(args.cases_file).read_text(encoding="utf-8"))
    cases = config.get("cases", [])

    if args.list:
        for case in cases:
            state = "enabled" if case.get("enabled", True) else "disabled"
            print(f"{case['name']:<24} {state:<9} {case.get('description','')}")
        pending = config.get("_pending_cases", {}).get("profiles_without_parity_fixture", [])
        if pending:
            print("\nProfiles with no parity fixture supplied (unverified):")
            print("  " + ", ".join(pending))
        return 0

    selected = [c for c in cases
                if (args.case and c["name"] in args.case)
                or (not args.case and c.get("enabled", True))]
    if not selected:
        print("No matching cases.", file=sys.stderr)
        return 2

    out_dir = _resolve(args.out)
    reports = [run_case(case, out_dir) for case in selected]
    summary = {
        "cases": len(reports),
        "passed": sum(1 for r in reports if r["status"] == "Completed"),
        "mismatched": sum(1 for r in reports if r["status"] == "Mismatch"),
        "fixture_missing": sum(1 for r in reports if r["status"] == "Fixture Missing"),
        "unverified_profiles": config.get("_pending_cases", {}).get("profiles_without_parity_fixture", []),
        "reports": reports,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "parity_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 1 if (summary["mismatched"] or summary["fixture_missing"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())

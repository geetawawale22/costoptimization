"""Generate CostOptima's own Excel output.

Structure and styling come from ``data/pcm_output_native.json``, which is defined from the
application's data model. The legacy 2302C workbook is reference data for validating
numbers and is not consulted here for layout, column order, formatting or sheet names.

This replaces three earlier presentation paths that all derived their shape from that one
customer workbook (``apply_legacy_template_styles``, ``render_legacy_layout``,
``apply_legacy_formats``). Those remain in the codebase behind switches that default to
off, because ``apply_legacy_formats`` was transcribed from legacy source rather than from
the workbook and may be wanted if legacy visual conventions are independently required.

Values are never recomputed here. This module reads the calculation results and writes
them; it cannot change a number.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

SPEC_PATH = Path(__file__).resolve().parents[2] / "data" / "pcm_output_native.json"

# Identity/bookkeeping keys that must not be treated as computed cost components.
def _is_scalar(value) -> bool:
    """Cell-writable values only.

    Some profiles attach a nested detail table to a result (the special_process profile
    carries its per-material Paint lines this way). Those are real business data, so they
    are emitted as their own detail sheet rather than stringified into a cell or dropped.
    """
    return value is None or isinstance(value, (int, float, str, bool))


def _detail_tables(results) -> dict:
    """Collect nested list-of-dict details, keyed by the name they were attached under."""
    tables: dict[str, list] = {}
    for result in results:
        for key, value in (result.get("values") or {}).items():
            if isinstance(value, dict):
                for inner_key, inner in value.items():
                    if isinstance(inner, list) and inner and isinstance(inner[0], dict):
                        tables.setdefault(inner_key, []).extend(inner)
            elif isinstance(value, list) and value and isinstance(value[0], dict):
                tables.setdefault(key, []).extend(value)
    return tables


_NON_COMPONENT_KEYS = {
    "Customer No", "End Item", "Supplier", "Desc", "Type", "Commodity",
    "Parent Commodity", "Total", "Calculation Status", "Status", "Profile",
}


def load_native_spec(path: str | Path | None = None) -> dict | None:
    target = Path(path) if path else SPEC_PATH
    if not target.exists():
        return None
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


class _Styler:
    def __init__(self, spec: dict):
        style = spec.get("style", {})
        font = style.get("font", {})
        # 'Aptos Narrow' is the current Office default but is absent on most servers and on
        # LibreOffice; substituting silently changes row metrics, so fall back explicitly.
        self.family = font.get("fallback") or font.get("name") or "Calibri"
        self.size = font.get("size", 10)
        self.numfmt = style.get("number_formats", {})
        self._header = style.get("header", {})
        self._total = style.get("total_row", {})
        self._status = style.get("status", {})

    def base(self) -> Font:
        return Font(name=self.family, size=self.size)

    def header_font(self) -> Font:
        return Font(name=self.family, size=self.size,
                    bold=self._header.get("bold", True),
                    color=self._header.get("font_color"))

    def header_fill(self) -> PatternFill | None:
        rgb = self._header.get("bg_color")
        return PatternFill("solid", start_color=rgb, end_color=rgb) if rgb else None

    def total_font(self) -> Font:
        return Font(name=self.family, size=self.size, bold=self._total.get("bold", True))

    def total_fill(self) -> PatternFill | None:
        rgb = self._total.get("bg_color")
        return PatternFill("solid", start_color=rgb, end_color=rgb) if rgb else None

    def status_font(self, status: str) -> Font:
        cfg = self._status.get(status) or {}
        return Font(name=self.family, size=self.size,
                    bold=cfg.get("bold", False), color=cfg.get("font_color"))

    def status_fill(self, status: str) -> PatternFill | None:
        rgb = (self._status.get(status) or {}).get("bg_color")
        return PatternFill("solid", start_color=rgb, end_color=rgb) if rgb else None


def _write_header(ws, headers: Iterable[str], styler: _Styler, row: int = 1) -> None:
    fill = styler.header_fill()
    font = styler.header_font()
    for index, name in enumerate(headers, start=1):
        cell = ws.cell(row=row, column=index, value=name)
        cell.font = font
        if fill:
            cell.fill = fill
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)


def _autosize(ws, spec: dict, identity_columns: set[str]) -> None:
    workbook_cfg = spec.get("workbook", {})
    default = workbook_cfg.get("default_column_width", 14)
    wide = workbook_cfg.get("identity_column_width", 26)
    for index, cell in enumerate(ws[1], start=1):
        name = cell.value
        ws.column_dimensions[get_column_letter(index)].width = (
            wide if name in identity_columns else default)


def _key_value_sheet(ws, rows, styler: _Styler, context: dict) -> None:
    _write_header(ws, ["Item", "Value"], styler)
    cursor = 2
    for item in rows:
        label = item["label"]
        value = context.get(item["source"].lstrip("@")) if str(item.get("source", "")).startswith("@") else item.get("source")
        if isinstance(value, (list, tuple, set)):
            value = ", ".join(str(v) for v in value)
        ws.cell(row=cursor, column=1, value=label).font = styler.base()
        cell = ws.cell(row=cursor, column=2, value=value)
        cell.font = styler.base()
        if item.get("highlight_when_nonzero") and isinstance(value, int) and value:
            cell.font = styler.status_font("Needs Review")
            fill = styler.status_fill("Needs Review")
            if fill:
                cell.fill = fill
        cursor += 1
    ws.column_dimensions["A"].width = 30
    ws.column_dimensions["B"].width = 72


def _table_sheet(ws, headers, records, styler: _Styler, spec: dict,
                 identity_columns: set[str], total_columns: Iterable[str] = (),
                 number_format: str | None = None, total_row: bool = False) -> None:
    _write_header(ws, headers, styler)
    base = styler.base()
    numfmt = styler.numfmt.get(number_format) if number_format else None
    cursor = 2
    for record in records:
        for index, name in enumerate(headers, start=1):
            value = record.get(name)
            if not _is_scalar(value):
                # Should not happen - columns are filtered upstream - but a nested
                # structure reaching openpyxl raises and would lose the whole workbook.
                value = None
            cell = ws.cell(row=cursor, column=index, value=value)
            cell.font = base
            if name in ("Calculation Status", "Status") and isinstance(value, str):
                cell.font = styler.status_font(value)
                fill = styler.status_fill(value)
                if fill:
                    cell.fill = fill
            elif numfmt and isinstance(value, (int, float)) and name not in identity_columns:
                cell.number_format = numfmt
        cursor += 1

    if total_row and records:
        total_font = styler.total_font()
        total_fill = styler.total_fill()
        border = Border(top=Side(style="thin"))
        for index, name in enumerate(headers, start=1):
            cell = ws.cell(row=cursor, column=index)
            cell.font = total_font
            cell.border = border
            if total_fill:
                cell.fill = total_fill
            if index == 1:
                cell.value = "Total"
            elif name in total_columns:
                # Sum only rows that completed. Including a failed row's partial value
                # would present an understated total as if it were final.
                values = [r.get(name) for r in records
                          if isinstance(r.get(name), (int, float))
                          and r.get("Calculation Status", "Completed") == "Completed"]
                if values:
                    cell.value = sum(values)
                    if numfmt:
                        cell.number_format = numfmt

    workbook_cfg = spec.get("workbook", {})
    if workbook_cfg.get("freeze_header", True):
        ws.freeze_panes = "A2"
    if workbook_cfg.get("autofilter", True) and records:
        ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{cursor - 1}"
    _autosize(ws, spec, identity_columns)


def render_native_output(snapshot, target_path: str | Path, spec: dict | None = None) -> dict:
    """Write the CostOptima-native workbook from a calculation snapshot."""
    spec = spec or load_native_spec()
    if not spec:
        raise FileNotFoundError("data/pcm_output_native.json not found")

    styler = _Styler(spec)
    results = list(getattr(snapshot, "results", []) or [])
    definition = getattr(snapshot, "definition", {}) or {}
    variants = list((definition.get("output") or {}).get("variants") or [])
    if not variants:
        variants = _infer_variants(results)

    completed = [r for r in results if r.get("status") == "Completed"]
    needs_review = [r for r in results if r.get("status") != "Completed"]
    profiles = sorted({r.get("profile") for r in results if r.get("profile")})

    context = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "input_name": getattr(snapshot, "input_name", None),
        "input_sha256": getattr(snapshot, "input_sha256", None),
        "definition_sha256": getattr(snapshot, "definition_sha256", None),
        "master_sha256": getattr(snapshot, "master_sha256", None),
        "master_version": getattr(snapshot, "master_version", None),
        "engine_version": getattr(snapshot, "engine_version", "costoptima-pcm"),
        "rows_total": len(results),
        "rows_completed": len(completed),
        "rows_needs_review": len(needs_review),
        "profiles": profiles,
        "variants": variants,
    }

    wb = Workbook()
    wb.remove(wb.active)
    report: dict[str, Any] = {"sheets": [], "profiles": profiles,
                              "rows_needs_review": len(needs_review)}

    sheets = spec.get("sheets", {})

    summary_spec = sheets.get("Run Summary", {})
    ws = wb.create_sheet("Run Summary")
    _key_value_sheet(ws, summary_spec.get("rows", []), styler, context)
    if needs_review and summary_spec.get("warning_when_needs_review"):
        row = ws.max_row + 2
        cell = ws.cell(row=row, column=1, value=summary_spec["warning_when_needs_review"])
        cell.font = styler.status_font("Needs Review")
    report["sheets"].append("Run Summary")

    for name, records, identity, totals in _derived_tables(sheets, results, variants):
        if not records:
            continue
        ws = wb.create_sheet(name)
        headers = list(dict.fromkeys(identity + totals + (["Status"] if any("Status" in r for r in records) else [])))
        _table_sheet(ws, headers, records, styler, spec, set(identity),
                     total_columns=totals, number_format="currency", total_row=True)
        report["sheets"].append(name)

    for profile in profiles:
        rows = [r for r in results if r.get("profile") == profile]
        if not rows:
            continue
        name = _sheet_name(profile)
        ws = wb.create_sheet(name)
        headers, records = _commodity_table(rows)
        identity = {"Customer No", "End Item", "Supplier", "Desc"}
        _table_sheet(ws, headers, records, styler, spec, identity,
                     total_columns=[h for h in headers if h in ("Total",)],
                     number_format="currency", total_row=True)
        report["sheets"].append(name)

    for detail_name, detail_rows in _detail_tables(results).items():
        if not detail_rows:
            continue
        name = _sheet_name(f"{detail_name} Detail")
        ws = wb.create_sheet(name)
        headers = list(dict.fromkeys(k for r in detail_rows for k in r))
        scalar_rows = [{k: (v if _is_scalar(v) else str(v)) for k, v in r.items()}
                       for r in detail_rows]
        _table_sheet(ws, headers, scalar_rows, styler, spec,
                     {"Desc", "Material", "Material Code"}, number_format="currency")
        report["sheets"].append(name)

    ws = wb.create_sheet("Validation")
    v_spec = sheets.get("Validation", {})
    issues = _issue_rows(results)
    if issues:
        _table_sheet(ws, v_spec.get("columns", list(issues[0])), issues, styler, spec,
                     {"Message"}, number_format=None)
    else:
        ws.cell(row=1, column=1, value=v_spec.get("empty_message",
                "No validation issues were raised for this run.")).font = styler.base()
    report["sheets"].append("Validation")
    report["issues"] = len(issues)

    ws = wb.create_sheet("Provenance")
    _key_value_sheet(ws, sheets.get("Provenance", {}).get("rows", []), styler, context)
    report["sheets"].append("Provenance")

    Path(target_path).parent.mkdir(parents=True, exist_ok=True)
    wb.save(target_path)
    return report


def _sheet_name(profile: str) -> str:
    name = " ".join(part.capitalize() for part in str(profile).replace("_", " ").split())
    return name[:31] or "Sheet"


def _infer_variants(results) -> list:
    """Recover variant names from the results when the definition does not pin them."""
    for result in results:
        rollup = result.get("variant_values") or {}
        if rollup:
            return list(rollup)
    return []


def _derived_tables(sheets, results, variants):
    """Build the Cost Summary and Cost Breakdown tables from the calculation."""
    summary = {}
    for result in results:
        inputs = result.get("input") or {}
        key = inputs.get("End Item")
        if key is None:
            continue
        row = summary.setdefault(key, {"End Item": key,
                                       "Customer No": inputs.get("Customer No"),
                                       "Desc": inputs.get("Desc"),
                                       "Status": result.get("status", "Completed")})
        for variant in variants:
            value = (result.get("values") or {}).get(variant)
            if isinstance(value, (int, float)):
                row[variant] = row.get(variant, 0) + value
        if result.get("status") != "Completed":
            row["Status"] = "Needs Review"
    yield "Cost Summary", list(summary.values()), ["End Item", "Customer No", "Desc"], list(variants)

    drivers = {}
    for result in results:
        for component, value in (result.get("values") or {}).items():
            if component in _NON_COMPONENT_KEYS or component in variants:
                continue
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                continue
            row = drivers.setdefault(component, {"Cost Driver": component})
            row["Total"] = row.get("Total", 0) + value
    yield "Cost Breakdown", list(drivers.values()), ["Cost Driver"], ["Total"]


def _commodity_table(rows):
    """Identity, then inputs, then computed components, then result."""
    identity = ["Customer No", "End Item", "Supplier", "Desc", "Type", "Commodity"]
    input_keys, component_keys = [], []
    for row in rows:
        for key, value in (row.get("input") or {}).items():
            if key in identity or key in input_keys:
                continue
            if not _is_scalar(value):
                continue  # nested detail table - emitted as its own sheet
            input_keys.append(key)
        for key, value in (row.get("values") or {}).items():
            if key in _NON_COMPONENT_KEYS or key in component_keys:
                continue
            if not _is_scalar(value):
                continue  # nested detail tables are emitted as their own sheet
            component_keys.append(key)
    headers = ([h for h in identity] + input_keys + component_keys
               + ["Total", "Calculation Status"])
    records = []
    for row in rows:
        record = {}
        record.update({k: (row.get("input") or {}).get(k) for k in identity + input_keys})
        record.update({k: (row.get("values") or {}).get(k) for k in component_keys})
        record["Total"] = (row.get("values") or {}).get("Total")
        record["Calculation Status"] = row.get("status", "Completed")
        records.append(record)
    return headers, records


def _issue_rows(results) -> list:
    rows = []
    for result in results:
        for issue in (result.get("issues") or []):
            rows.append({
                "Line": issue.get("line"),
                "Customer No": issue.get("customer_no"),
                "Profile": issue.get("profile") or result.get("profile"),
                "Step": issue.get("step"),
                "Code": issue.get("code"),
                "Message": issue.get("message"),
                "Table": issue.get("table"),
                "Field": issue.get("field"),
                "Lookup Key": json.dumps(issue.get("lookup_key"), default=str)
                if issue.get("lookup_key") is not None else None,
            })
    return rows

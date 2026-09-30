"""Apply the legacy PCM output FORMAT contract to a generated workbook.

The spec lives in ``data/pcm_legacy_format.json`` and was transcribed from the legacy
source (``Sheet_Metal_Forging_with summary_chk_Chnages.py``), which builds output with
xlsxwriter and declares named formats via ``workbook.add_format(...)``. Those declarations
are the contract; the 2302C S302 workbook is one result of applying them.

This matters for the "everything dynamic" requirement. The earlier approach copied cell
formatting out of that one customer workbook, so formatting only existed where S302
happened to have cells. Driving formatting from the declared spec instead means any
input - any commodity, any variant set, any row count - is formatted correctly.

Values are never touched. This module only sets font, number format, fill and the
annotation blocks legacy writes.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

SPEC_PATH = Path(__file__).resolve().parents[2] / "data" / "pcm_legacy_format.json"


def load_format_spec(path: str | Path | None = None) -> dict | None:
    target = Path(path) if path else SPEC_PATH
    if not target.exists():
        return None
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def _resolve_number_format(spec: dict, value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    if value.startswith("@"):
        return spec.get("number_formats", {}).get(value[1:])
    return value


def _font_for(spec: dict, name: str) -> Font | None:
    fmt = spec.get("formats", {}).get(name)
    if not fmt:
        return None
    family = fmt.get("font_name")
    # 'Bierstadt Display' ships with Microsoft 365 and is absent on most servers and on
    # LibreOffice. Excel silently substitutes, which changes row metrics; using the
    # declared fallback keeps rendering predictable.
    if family and fmt.get("font_fallback") and family != "Calibri":
        family = fmt["font_fallback"]
    return Font(
        name=family,
        size=fmt.get("font_size"),
        bold=fmt.get("bold", False),
        color=fmt.get("font_color"),
    )


def _fill_for(spec: dict, name: str) -> PatternFill | None:
    fmt = spec.get("formats", {}).get(name) or {}
    rgb = fmt.get("bg_color")
    if not rgb:
        return None
    return PatternFill(fill_type="solid", start_color=rgb, end_color=rgb)


def _alignment_for(spec: dict, name: str) -> Alignment:
    fmt = spec.get("formats", {}).get(name) or {}
    return Alignment(
        horizontal=fmt.get("align"),
        vertical=fmt.get("valign", "").replace("vcenter", "center") or None,
        wrap_text=fmt.get("text_wrap", False),
    )


def apply_legacy_formats(workbook, spec: dict | None = None,
                         skip_sheets: set[str] | None = None) -> dict:
    """Format an open openpyxl workbook in place. Returns a report."""
    spec = spec or load_format_spec()
    if not spec:
        return {"applied": False, "reason": "no format spec found"}
    switches = spec.get("apply", {})
    skip_sheets = {s.casefold() for s in (skip_sheets or set())}

    base_font = _font_for(spec, "base")
    base_align = _alignment_for(spec, "base")
    base_numfmt = _resolve_number_format(
        spec, (spec.get("formats", {}).get("base") or {}).get("number_format"))
    total_fill = _fill_for(spec, "total_row")

    report: dict[str, Any] = {"applied": True, "sheets_formatted": [],
                              "annotations_written": [], "total_rows_filled": {}}

    for name in workbook.sheetnames:
        if name.casefold() in skip_sheets:
            continue
        ws = workbook[name]
        if switches.get("base_font", True) or switches.get("number_formats", True):
            for row in ws.iter_rows():
                for cell in row:
                    if cell.value is None:
                        continue
                    if switches.get("base_font", True) and base_font:
                        cell.font = base_font
                        cell.alignment = base_align
                    if switches.get("number_formats", True) and base_numfmt:
                        cell.number_format = base_numfmt
            report["sheets_formatted"].append(name)

        # Preserve any amber fill already present (the derived layout may have marked a
        # total row). Re-assert it here because the base font pass above rewrites styles.
        if switches.get("total_row_fill", True) and total_fill:
            filled = 0
            for row in ws.iter_rows():
                for cell in row:
                    existing = cell.fill
                    if existing and existing.fill_type == "solid" and \
                       str(existing.start_color.rgb).upper().endswith("FFC000"):
                        cell.fill = total_fill
                        filled += 1
            if filled:
                report["total_rows_filled"][name] = filled

        block = (spec.get("annotation_blocks", {}) or {}).get(name)
        if block and switches.get("annotation_blocks", True):
            _write_annotation(ws, block, spec)
            report["annotations_written"].append(name)

    return report


def _write_annotation(ws, block: dict, spec: dict) -> None:
    """Write one annotation block.

    Legacy writes some annotations at a fixed cell (Analysis A23/A25-A28) and one at a
    position computed from the sheet's last data column (End Item Analysis). Both forms
    are supported; the computed form keeps working when the variant count changes, which
    a fixed cell would not.
    """
    dynamic = block.get("dynamic")
    for index, item in enumerate(block.get("cells", [])):
        if "cell" in item:
            target = item["cell"]
        elif dynamic:
            column = ws.max_column + dynamic.get("column_offset_from_last", 1)
            target = f"{get_column_letter(column)}{dynamic.get('row', 15) + index}"
        else:
            continue
        cell = ws[target]
        cell.value = item["value"]
        font = _font_for(spec, item.get("format", "annotation"))
        if font:
            cell.font = font
        cell.alignment = _alignment_for(spec, item.get("format", "annotation"))

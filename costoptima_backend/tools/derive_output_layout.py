"""Derive the legacy PCM output layout from a real legacy output workbook.

The engine previously wrote each sheet with whatever keys happened to be present on the
row dictionaries, which produced a generic "input echo" column set: the Make sheet opened
with ``PF Code, Project, Part Name, ...`` where legacy opens with
``Supplier, End Item, Supplier No, Customer No, ...``. Values were correct; the table
shape was not.

This tool reads a legacy output workbook and records, per sheet: the exact sheet name,
its position in the workbook, the exact header row in order, column widths, header fill
and font, and freeze panes. The result is written to ``data/pcm_output_layout.json`` and
consumed at export time by ``app/services/pcm_output_layout.py``.

Each legacy column is bound to an engine field:

* exact name match against an engine value/input field, or
* an explicit mapping inverted from ``data/pcm_parity_cases.json`` (which already records
  engine-column -> legacy-column for every verified profile), or
* ``null`` - meaning the binding is **unknown**. Unknown columns are emitted blank and
  listed under ``unbound_columns`` in the output so they are visible rather than silently
  invented. Do not guess these; confirm them against the legacy source.

Re-run this whenever the legacy output template changes. It is a JSON regeneration, not a
code change.

Usage
-----
    python tools/derive_output_layout.py
    python tools/derive_output_layout.py --template "../<legacy output>.xlsx" --out data/pcm_output_layout.json
"""
import argparse
import json
import sys
from pathlib import Path

from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DEFAULT_TEMPLATE = ROOT.parent / "2302C S302 FENDER OUTER TRIMS BC 10 Sep 2026 09.52.04 AM_50002264.xlsx"
DEFAULT_OUT = ROOT / "data" / "pcm_output_layout.json"
PARITY_CASES = ROOT / "data" / "pcm_parity_cases.json"

# Sheets the modernized system adds for traceability. They have no legacy counterpart, so
# they are recorded separately and appended after the legacy sheets rather than being
# interleaved into the legacy sheet order.
DIAGNOSTIC_SHEETS = ["End Item Input", "Errors", "Provenance", "Formula Trace"]

# Some legacy sheets carry a display-label row above the real header row.
HEADER_ROW_OVERRIDES = {"SBC": 2}


def _rgb(colour):
    try:
        value = colour.rgb
    except AttributeError:
        return None
    if not isinstance(value, str) or value in ("00000000",):
        return None
    return value


def _inverted_parity_map():
    """legacy column header -> engine field, per profile sheet, from the parity cases."""
    if not PARITY_CASES.exists():
        return {}
    config = json.loads(PARITY_CASES.read_text(encoding="utf-8"))
    mapping = {}
    for case in config.get("cases", []):
        for spec in case.get("profile_sheets", {}).values():
            sheet = spec.get("sheet")
            if not sheet:
                continue
            table = mapping.setdefault(sheet, {})
            for engine_col, legacy_col in spec.get("columns", {}).items():
                # First declaration wins; cases are ordered most-verified first.
                table.setdefault(legacy_col, engine_col)
    return mapping


def derive(template_path: Path):
    wb = load_workbook(template_path)
    parity = _inverted_parity_map()
    sheets = {}
    unbound = {}

    for position, name in enumerate(wb.sheetnames):
        ws = wb[name]
        header_row = HEADER_ROW_OVERRIDES.get(name, 1)
        headers = [c.value for c in ws[header_row]]
        # Trim trailing empty header cells; legacy workbooks pad the used range.
        while headers and headers[-1] in (None, ""):
            headers.pop()

        # When a sheet carries a display-label row above its header (legacy SBC), the
        # label row holds the engine's own field name (MX 1, MX 2 ...) while the header
        # row holds the business display name (BASE, BASE 1 ...). Bind by the label so
        # the column resolves, and keep the legacy display name as the visible header.
        label_row = [c.value for c in ws[1]] if header_row > 1 else []

        columns = []
        for index, header in enumerate(headers, start=1):
            letter = ws.cell(row=header_row, column=index).column_letter
            dim = ws.column_dimensions.get(letter)
            label = None if header in (None, "") else str(header)
            source = None
            label_value = label_row[index - 1] if index - 1 < len(label_row) else None
            if label_value not in (None, ""):
                source = str(label_value)
            elif label is not None:
                source = parity.get(name, {}).get(label, label)
            columns.append({
                "header": label,
                "source": source,
                "width": round(dim.width, 2) if dim and dim.width else None,
            })
            if label is not None and source is None:
                unbound.setdefault(name, []).append(label)

        header_cell = ws.cell(row=header_row, column=1)
        sheets[name] = {
            "position": position,
            "header_row": header_row,
            "label_row": [c.value for c in ws[1]][:len(headers)] if header_row > 1 else None,
            "header_fill": _rgb(header_cell.fill.fgColor) if header_cell.fill else None,
            "header_bold": bool(header_cell.font and header_cell.font.bold),
            "freeze_panes": ws.freeze_panes,
            "tab_color": ws.sheet_properties.tabColor.rgb if ws.sheet_properties.tabColor else None,
            "columns": columns,
        }
    wb.close()

    return {
        "_comment": [
            "Legacy PCM output layout, derived from a real legacy output workbook by",
            "tools/derive_output_layout.py. Regenerate with that tool when the legacy",
            "template changes - this is a JSON regeneration, not a code change.",
            "",
            "'source' binds a legacy column to an engine field. null means the binding is",
            "UNKNOWN: the column is emitted blank and listed in 'unbound_columns'. Those",
            "are Needs Confirmation items - do not guess them.",
            "",
            "'emit_diagnostics' controls whether the traceability sheets (Errors,",
            "Provenance, Formula Trace, End Item Input) are appended. Legacy produced no",
            "such sheets, so set it false when an output must match legacy sheet-for-sheet.",
        ],
        "template": template_path.name,
        "sheet_order": list(sheets.keys()),
        "emit_diagnostics": True,
        "diagnostic_sheets": DIAGNOSTIC_SHEETS,
        "sheets": sheets,
        "unbound_columns": unbound,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", default=str(DEFAULT_TEMPLATE))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args(argv)

    template_path = Path(args.template)
    if not template_path.is_absolute():
        template_path = (ROOT / template_path).resolve()
    if not template_path.exists():
        print(f"Legacy output template not found: {template_path}", file=sys.stderr)
        return 2

    layout = derive(template_path)
    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = ROOT / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(layout, indent=2), encoding="utf-8")

    total_cols = sum(len(s["columns"]) for s in layout["sheets"].values())
    unbound = sum(len(v) for v in layout["unbound_columns"].values())
    print(f"template : {template_path.name}")
    print(f"sheets   : {len(layout['sheets'])}  ({', '.join(layout['sheet_order'])})")
    print(f"columns  : {total_cols} derived, {unbound} unbound (emitted blank, need confirmation)")
    for sheet, cols in layout["unbound_columns"].items():
        print(f"   {sheet}: {', '.join(cols[:8])}{' ...' if len(cols) > 8 else ''}")
    print(f"written  : {out_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

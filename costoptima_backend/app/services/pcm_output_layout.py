"""Render calculated PCM output into the legacy workbook's sheet and column layout.

The database runtime produces correct values but writes each sheet with whatever keys the
row dictionaries happen to carry, which gives a generic "input echo" table: the Make sheet
opens ``PF Code, Project, Part Name, ...`` where legacy opens
``Supplier, End Item, Supplier No, Customer No, ...``. It also names the packaging and
freight sheets ``PKG``/``FRT`` where legacy names them ``Pkg``/``Frt``, orders sheets
differently, and omits the legacy header fill.

This module re-projects an already-written snapshot workbook onto the layout derived from
a real legacy output workbook (``data/pcm_output_layout.json``, produced by
``tools/derive_output_layout.py``). It deliberately re-reads the snapshot rather than
re-deriving values, so the numbers are exactly the ones the verified calculation path
produced - this changes presentation only, never a result.

Columns whose legacy binding is unknown are written blank and reported, never guessed.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

LAYOUT_PATH = Path(__file__).resolve().parents[2] / "data" / "pcm_output_layout.json"


def load_layout(path: str | Path | None = None) -> dict | None:
    """Return the derived legacy layout, or None when it has not been generated."""
    target = Path(path) if path else LAYOUT_PATH
    if not target.exists():
        return None
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def _norm(value: Any) -> str:
    return " ".join(str(value or "").split()).casefold()


def _read_sheets(path: str | Path) -> dict[str, list[dict]]:
    """Read every sheet of a workbook into header-keyed row dictionaries."""
    wb = load_workbook(path, data_only=True, read_only=True)
    try:
        sheets: dict[str, list[dict]] = {}
        for name in wb.sheetnames:
            values = list(wb[name].values)
            if not values:
                sheets[name] = []
                continue
            headers = [h for h in values[0]]
            rows = []
            for row in values[1:]:
                record = {h: v for h, v in zip(headers, row) if h not in (None, "")}
                if any(v not in (None, "") for v in record.values()):
                    rows.append(record)
            sheets[name] = rows
        return sheets
    finally:
        wb.close()


def _resolve_source_sheet(legacy_name: str, available: Iterable[str]) -> str | None:
    """Match a legacy sheet name to the engine's sheet name.

    Legacy writes ``Pkg``/``Frt``; the engine writes ``PKG``/``FRT``. Case- and
    whitespace-insensitive matching covers that without a hard-coded rename table.
    """
    target = _norm(legacy_name)
    for name in available:
        if _norm(name) == target:
            return name
    return None



def template_variant_headers(layout: dict) -> list:
    """Return the variant names baked into the derived layout by its source workbook.

    ``tools/derive_output_layout.py`` reads a real legacy output, so the variant columns
    it records (``MX 1``...``ZENITH`` for the S302 workbook) are that project's variants,
    not a fixed vocabulary. They are treated as PLACEHOLDERS: at render time they are
    replaced by whatever variants the current calculation actually produced.
    """
    label_row = (layout.get("sheets", {}).get("SBC", {}) or {}).get("label_row") or []
    return [v for v in label_row if v not in (None, "")]


def _data_keys(rows) -> list:
    seen = {}
    for row in rows:
        for key in row:
            if key not in (None, "") and key not in seen:
                seen[key] = None
    return list(seen)


def _effective_columns(spec: dict, rows, template_variants) -> tuple:
    """Resolve a sheet's layout columns against the data actually calculated.

    The derived layout records the template project's variant names and, on
    'End Item Analysis', its end-item names as literal column headers. Those are data,
    not structure: applying them to a different input produces empty columns (U172 has
    four variants named 'SHEV - G12-DHT MX3'..., none of which match S302's nine).

    Any layout column that names a template variant, or that names nothing present in
    this run's data on a sheet whose columns are end-item names, is replaced by the
    corresponding column from the calculated data. Static legacy columns are untouched,
    preserving the legacy column ORDER while making the variable block follow the input.
    """
    columns = spec["columns"]
    keys = _data_keys(rows)
    if not keys:
        return columns, []

    template_set = {_norm(v) for v in template_variants}
    static_headers = {_norm(c.get("header")) for c in columns
                      if c.get("header") and _norm(c["header"]) not in template_set}
    # Columns in the data that the static layout does not already account for are this
    # run's variable block, in the order the engine emitted them.
    actual_variable = [k for k in keys if _norm(k) not in static_headers]

    placeholder_positions = [i for i, c in enumerate(columns)
                             if c.get("header") and _norm(c["header"]) in template_set]
    if not placeholder_positions:
        # 'End Item Analysis' style: every header is an end-item NAME from the template
        # project, so none of them bind to this run's data. Only treat the sheet as fully
        # dynamic when NOT ONE layout header matches the data - a sheet where some headers
        # bind (Make: Supplier, End Item, Customer No ...) has a real static legacy layout
        # that must be preserved, and replacing it would discard the legacy column order
        # this module exists to restore.
        key_set = {_norm(k) for k in keys}
        headers = [c for c in columns if c.get("header")]
        if headers and not any(_norm(c["header"]) in key_set for c in headers):
            placeholder_positions = [i for i, c in enumerate(columns) if c.get("header")]
    if not placeholder_positions or not actual_variable:
        return columns, []

    resolved = list(columns)
    substitutions = []
    insert_at = placeholder_positions[0]
    # Drop the template's placeholder slots, then splice this run's variable columns in
    # at the same position so surrounding legacy columns keep their order.
    for index in reversed(placeholder_positions):
        resolved.pop(index)
    block = []
    for name in actual_variable:
        template_like = next((columns[i] for i in placeholder_positions), {})
        block.append({"header": name, "source": name, "width": template_like.get("width")})
        substitutions.append(name)
    resolved[insert_at:insert_at] = block
    return resolved, substitutions


def render_legacy_output(snapshot_path: str | Path, target_path: str | Path,
                         layout: dict | None = None) -> dict:
    """Write a legacy-shaped workbook from an already-exported snapshot workbook.

    Returns a report describing what was rendered, which legacy sheets had no engine
    counterpart, and which legacy columns could not be bound to an engine field.
    """
    layout = layout or load_layout()
    if not layout:
        raise FileNotFoundError(
            "No output layout found. Generate it with: python tools/derive_output_layout.py"
        )

    source_sheets = _read_sheets(snapshot_path)
    template_variants = template_variant_headers(layout)
    wb = Workbook()
    wb.remove(wb.active)

    header_font = Font(bold=True)
    report: dict[str, Any] = {
        "template": layout.get("template"),
        "sheets_written": [],
        "legacy_sheets_without_data": [],
        "unbound_columns": {},
        "blank_columns": {},
    }

    for legacy_name in layout["sheet_order"]:
        spec = layout["sheets"][legacy_name]
        source_name = _resolve_source_sheet(legacy_name, source_sheets)
        rows = source_sheets.get(source_name, []) if source_name else []
        if source_name is None:
            report["legacy_sheets_without_data"].append(legacy_name)

        # A legacy sheet with no calculated rows for THIS input is not emitted. The
        # layout lists every sheet its template workbook happened to contain; S302 has
        # Buy Std/Assy/Paint/Special Process, U172 has none of them. Emitting them empty
        # would ship four blank sheets that misrepresent the calculation.
        if not rows and layout.get("omit_empty_legacy_sheets", True):
            report.setdefault("sheets_omitted_no_data", []).append(legacy_name)
            continue

        ws = wb.create_sheet(title=legacy_name)
        columns, substituted = _effective_columns(spec, rows, template_variants)
        if substituted:
            report.setdefault("dynamic_columns", {})[legacy_name] = substituted

        # Optional display-label row above the real header (legacy SBC has one).
        row_cursor = 1
        if spec.get("label_row"):
            # NEEDS CONFIRMATION. Legacy SBC carries two header rows: row 1 holds variant
            # CODES (MX 1 ... ZENITH) and row 2 holds the marketing names (BASE, BASE 1,
            # MID 1 ...). That code->name mapping is project data which is not present in
            # the input workbook or the master, so it cannot be derived for a different
            # project. The template's label row is written unchanged; for an input whose
            # variants differ from the template's it will therefore be WRONG.
            # Supply the code->name mapping (or confirm where legacy read it from) to fix.
            for index, value in enumerate(spec["label_row"], start=1):
                ws.cell(row=row_cursor, column=index, value=value)
            row_cursor += 1

        header_row_index = row_cursor
        fill = None
        if spec.get("header_fill"):
            rgb = spec["header_fill"]
            fill = PatternFill(fill_type="solid", start_color=rgb, end_color=rgb)

        for index, column in enumerate(columns, start=1):
            cell = ws.cell(row=header_row_index, column=index, value=column["header"])
            if spec.get("header_bold", True):
                cell.font = header_font
            if fill:
                cell.fill = fill
            if column.get("width"):
                ws.column_dimensions[get_column_letter(index)].width = column["width"]
        row_cursor += 1

        unresolved: set[str] = set()
        for record in rows:
            for index, column in enumerate(columns, start=1):
                source = column.get("source")
                if source is None:
                    if column["header"]:
                        unresolved.add(column["header"])
                    continue
                if source in record:
                    ws.cell(row=row_cursor, column=index, value=record[source])
                elif column["header"] in record:
                    ws.cell(row=row_cursor, column=index, value=record[column["header"]])
                elif column["header"]:
                    unresolved.add(column["header"])
            row_cursor += 1

        if spec.get("freeze_panes"):
            ws.freeze_panes = spec["freeze_panes"]
        if unresolved:
            report["blank_columns"][legacy_name] = sorted(unresolved)

    # Any engine sheet with no counterpart in the derived layout MUST still be written.
    # The layout is only as complete as the legacy workbook it was derived from: a layout
    # derived from an output with no Soft sheet would otherwise silently discard every
    # Soft row. Dropping calculated business rows to make the workbook match a template is
    # never acceptable, so these are appended with their native layout and reported.
    legacy_targets = {_norm(n) for n in layout["sheet_order"]}
    diagnostic_targets = {_norm(n) for n in layout.get("diagnostic_sheets", [])}
    carried_over = []
    for source_name, rows in source_sheets.items():
        key = _norm(source_name)
        if key in legacy_targets or key in diagnostic_targets:
            continue
        carried_over.append({"sheet": source_name, "rows": len(rows)})
        ws = wb.create_sheet(title=source_name)
        headers = list(dict.fromkeys(k for row in rows for k in row))
        for index, header in enumerate(headers, start=1):
            ws.cell(row=1, column=index, value=header).font = header_font
        for offset, record in enumerate(rows, start=2):
            for index, header in enumerate(headers, start=1):
                ws.cell(row=offset, column=index, value=record.get(header))
        ws.freeze_panes = "A2"
    report["sheets_without_legacy_layout"] = carried_over

    # Traceability sheets have no legacy counterpart. Append them after the legacy sheets
    # so the legacy portion of the workbook stays sheet-for-sheet comparable, or omit them
    # entirely when the output must match legacy exactly.
    if layout.get("emit_diagnostics", True):
        for name in layout.get("diagnostic_sheets", []):
            source_name = _resolve_source_sheet(name, source_sheets)
            if not source_name:
                continue
            rows = source_sheets[source_name]
            ws = wb.create_sheet(title=name)
            headers = list(dict.fromkeys(k for row in rows for k in row))
            for index, header in enumerate(headers, start=1):
                ws.cell(row=1, column=index, value=header).font = header_font
            for offset, record in enumerate(rows, start=2):
                for index, header in enumerate(headers, start=1):
                    ws.cell(row=offset, column=index, value=record.get(header))
            ws.freeze_panes = "A2"

    Path(target_path).parent.mkdir(parents=True, exist_ok=True)
    wb.save(target_path)
    wb.close()

    report["sheets_written"] = layout["sheet_order"] + (
        layout.get("diagnostic_sheets", []) if layout.get("emit_diagnostics", True) else []
    )
    report["unbound_columns"] = layout.get("unbound_columns", {})
    report["path"] = str(target_path)
    return report

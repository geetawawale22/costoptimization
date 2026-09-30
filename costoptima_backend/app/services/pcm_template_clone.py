"""Template-backed rendering for legacy PCM workbooks.

The old PCM output uses Excel Table objects, row heights, column widths, formulas,
tab order and cell styles as part of its visual contract. Rebuilding those sheets
from scratch loses details. This module instead clones the real legacy workbook
and overlays database-calculated values into the legacy tables.

openpyxl moves cells during row edits but does not update table references, so all
table refs are updated explicitly after row/column resizing.
"""
from __future__ import annotations

from copy import copy, deepcopy
import json
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter, range_boundaries
from openpyxl.worksheet.table import Table

LAYOUT_PATH = Path(__file__).resolve().parents[2] / "data" / "pcm_output_layout.json"
DEFAULT_LEGACY_TEMPLATE = (
    Path(__file__).resolve().parents[3]
    / "2302C S302 FENDER OUTER TRIMS BC 10 Sep 2026 09.52.04 AM_50002264.xlsx"
)
LEGACY_TEMPLATE_CANDIDATES = {
    "soft": (
        Path(__file__).resolve().parents[3]
        / "2302D M210 NVH INSULATIONS TKO 22 Sep 2026 01.04.03 PM_50002264.xlsx"
    ),
    "gdc": (
        Path(__file__).resolve().parents[3]
        / "604 Yuvn_t Thermostat Cover SBC 22 Sep 2026 01.10.47 PM_50002264.xlsx"
    ),
}


def _resolve_path(path: str | None) -> Path | None:
    if not path:
        return DEFAULT_LEGACY_TEMPLATE if DEFAULT_LEGACY_TEMPLATE.exists() else None
    target = Path(path)
    if target.is_absolute():
        return target
    backend_root = Path(__file__).resolve().parents[2]
    repo_root = backend_root.parent
    for base in (backend_root, repo_root):
        candidate = base / target
        if candidate.exists():
            return candidate
    return backend_root / target


def _auto_template_for_source(source_wb, configured_path: str | None) -> Path | None:
    """Pick the closest legacy workbook template for this calculation output.

    The old code produced different tab sets for Soft Trim, GDC, and hard-trim
    cases. Using the S302 template for every run drops or mis-shapes sheets like
    ``ST_Ukay Metal ST`` and ``GDC_Output_Pooja``. When the configured path is the
    default generic/S302 template, choose a more specific checked-in reference
    template based on the calculated profile sheets already present in the
    snapshot workbook.
    """
    configured = _resolve_path(configured_path)
    names = {_norm(name) for name in source_wb.sheetnames}
    if "gdc" in names:
        candidate = LEGACY_TEMPLATE_CANDIDATES["gdc"]
        if candidate.exists():
            return candidate
    if "soft" in names:
        candidate = LEGACY_TEMPLATE_CANDIDATES["soft"]
        if candidate.exists():
            return candidate
    return configured


def _copy_sheet(template_ws, target_wb, title: str):
    if title in target_wb.sheetnames:
        index = target_wb.sheetnames.index(title)
        del target_wb[title]
        ws = target_wb.create_sheet(title, index)
    else:
        ws = target_wb.create_sheet(title)

    for row in template_ws.iter_rows():
        for source in row:
            cell = ws.cell(source.row, source.column, source.value)
            if source.has_style:
                cell._style = copy(source._style)
            if source.number_format:
                cell.number_format = source.number_format
            if source.alignment:
                cell.alignment = copy(source.alignment)
            if source.protection:
                cell.protection = copy(source.protection)
            if source.comment:
                cell.comment = copy(source.comment)

    for key, dim in template_ws.column_dimensions.items():
        ws.column_dimensions[key] = copy(dim)
    for key, dim in template_ws.row_dimensions.items():
        ws.row_dimensions[key] = copy(dim)
    for merged in template_ws.merged_cells.ranges:
        ws.merge_cells(str(merged))
    ws.sheet_format = copy(template_ws.sheet_format)
    ws.sheet_properties = copy(template_ws.sheet_properties)
    ws.page_margins = copy(template_ws.page_margins)
    ws.page_setup = copy(template_ws.page_setup)
    ws.freeze_panes = template_ws.freeze_panes
    ws.sheet_view.showGridLines = template_ws.sheet_view.showGridLines
    if template_ws.sheet_view.pane:
        ws.sheet_view.pane = copy(template_ws.sheet_view.pane)
    for name in template_ws.tables.keys():
        ws.add_table(deepcopy(template_ws.tables[name]))
    return ws


def _norm(value) -> str:
    return " ".join(str(value if value is not None else "").replace("_", " ").split()).casefold()


def _source_sheet_name(legacy_name: str, available: list[str]) -> str | None:
    target = _norm(legacy_name)
    aliases = {
        "Pkg": ["Pkg", "PKG"],
        "Frt": ["Frt", "FRT"],
        "GDC_Casting_Input": ["GDC_Casting_Input", "GDC_Input", "GDC"],
        "GDC_Output_Pooja": ["GDC_Output_Pooja", "GDC"],
    }
    for candidate in aliases.get(legacy_name, [legacy_name]):
        for name in available:
            if _norm(name) == _norm(candidate):
                return name
    for name in available:
        if _norm(name) == target:
            return name
    return None


def _read_source_rows(wb, sheet_name: str) -> list[dict]:
    if sheet_name not in wb.sheetnames:
        return []
    ws = wb[sheet_name]
    if ws.tables:
        table = ws.tables["SBC"] if sheet_name == "SBC" and "SBC" in ws.tables else next(iter(ws.tables.values()))
        min_col, min_row, max_col, max_row = range_boundaries(table.ref)
        rows = [
            tuple(ws.cell(row, col).value for col in range(min_col, max_col + 1))
            for row in range(min_row, max_row + 1)
        ]
    else:
        rows = list(ws.values)
    if not rows:
        return []
    headers = [str(value) if value is not None else "" for value in rows[0]]
    records = []
    for values in rows[1:]:
        row = dict(zip(headers, values))
        if not any(value not in (None, "") for value in row.values()):
            continue
        records.append(row)
    return records


def _load_layout(path: str | None = None) -> dict:
    target = _resolve_path(path) if path else LAYOUT_PATH
    if not target or not target.exists():
        return {}
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _sheet_column_bindings(layout: dict, sheet_name: str) -> dict:
    spec = (layout.get("sheets") or {}).get(sheet_name) or {}
    result = {}
    for column in spec.get("columns") or []:
        header = column.get("header")
        source = column.get("source")
        if header:
            result[_norm(header)] = source or header
    return result


def _value_for_header(record: dict, header, bindings: dict):
    if header in (None, ""):
        return None
    candidates = [bindings.get(_norm(header)), header]
    aliases = {
        "O&H Cost": ["OH Cost", "O&H Cost"],
        "O&H": ["OH", "O&H"],
        "PROFIT Cost": ["Profit Cost", "PROFIT Cost"],
        "Deflashing cost": ["Deflashing Cost", "Deflashing cost"],
        "Markup": ["Total Markup", "Markup"],
        "MARKUP": ["Total Markup", "Markup"],
        "Total/Pc": ["Total", "Total/Pc"],
        "Total cost /PC": ["Total", "Total cost /PC"],
        "Total Cost/Pc": ["Total Cost/Pc", "Total"],
        "Basic cost/ pc": ["Basic Cost/Pc", "Basic cost/ pc"],
        "Basic cost": ["Basic Cost/Pc", "Basic cost"],
        "Basic Cost": ["Basic Cost", "Basic Cost/Pc"],
        "Pnf": ["Paint PnF", "Pnf"],
    }
    candidates.extend(aliases.get(str(header), []))
    for candidate in candidates:
        if candidate in record:
            return record[candidate]
        for key, value in record.items():
            if _norm(key) == _norm(candidate):
                return value
    return None


def _table_bounds(table: Table):
    return range_boundaries(table.ref)


def _set_table_ref(table: Table, min_col: int, min_row: int, max_col: int, max_row: int) -> None:
    table.ref = f"{get_column_letter(min_col)}{min_row}:{get_column_letter(max_col)}{max_row}"


def _copy_row_style(ws, source_row: int, target_row: int, max_col: int) -> None:
    ws.row_dimensions[target_row] = copy(ws.row_dimensions[source_row])
    for col in range(1, max_col + 1):
        source = ws.cell(source_row, col)
        target = ws.cell(target_row, col)
        if source.has_style:
            target._style = copy(source._style)
        target.number_format = source.number_format
        target.alignment = copy(source.alignment)
        target.protection = copy(source.protection)


def _resize_table_rows(ws, table_name: str, rows_needed: int) -> tuple[int, int, int, int, int]:
    table = ws.tables[table_name]
    min_col, min_row, max_col, max_row = _table_bounds(table)
    existing = max(0, max_row - min_row)
    wanted = max(1, rows_needed)
    delta = wanted - existing
    insert_at = max_row + 1
    if delta > 0:
        ws.insert_rows(insert_at, delta)
        for row in range(insert_at, insert_at + delta):
            _copy_row_style(ws, max_row, row, ws.max_column)
    elif delta < 0:
        ws.delete_rows(max_row + delta + 1, -delta)
    new_max_row = max_row + delta

    for name in ws.tables.keys():
        current = ws.tables[name]
        c1, r1, c2, r2 = _table_bounds(current)
        if name == table_name:
            _set_table_ref(current, c1, r1, c2, new_max_row)
        elif r1 > max_row:
            _set_table_ref(current, c1, r1 + delta, c2, r2 + delta)
    return min_col, min_row, max_col, new_max_row, delta


def _write_records_to_table(ws, table_name: str, records: list[dict],
                            bindings: dict | None = None) -> None:
    if table_name not in ws.tables:
        return
    bindings = bindings or {}
    min_col, min_row, max_col, max_row, _ = _resize_table_rows(ws, table_name, len(records))
    headers = [ws.cell(min_row, col).value for col in range(min_col, max_col + 1)]
    for row_index, record in enumerate(records, min_row + 1):
        for offset, header in enumerate(headers):
            if header in (None, ""):
                continue
            ws.cell(row_index, min_col + offset, _value_for_header(record, header, bindings))


def _clear_table_data(ws, table_name: str) -> None:
    if table_name not in ws.tables:
        return
    min_col, min_row, max_col, max_row = _table_bounds(ws.tables[table_name])
    for row in range(min_row + 1, max_row + 1):
        for col in range(min_col, max_col + 1):
            ws.cell(row, col).value = None


def _clear_all_tables(ws) -> None:
    for table_name in ws.tables.keys():
        _clear_table_data(ws, table_name)


def _write_sbc(ws, records: list[dict], variants: list[str]) -> None:
    if "SBC" not in ws.tables:
        return
    min_col, min_row, _, data_end, _ = _resize_table_rows(ws, "SBC", len(records))
    last_col = 3 + len(variants)
    current_max_col = range_boundaries(ws.tables["SBC"].ref)[2]
    if last_col < current_max_col:
        ws.delete_cols(last_col + 1, current_max_col - last_col)
    elif last_col > current_max_col:
        ws.insert_cols(current_max_col + 1, last_col - current_max_col)
        for col in range(current_max_col + 1, last_col + 1):
            for row in range(1, ws.max_row + 1):
                source = ws.cell(row, current_max_col)
                target = ws.cell(row, col)
                if source.has_style:
                    target._style = copy(source._style)
                target.number_format = source.number_format
                target.alignment = copy(source.alignment)
                target.protection = copy(source.protection)

    for col, variant in enumerate(variants, 4):
        ws.cell(1, col, variant)
        if ws.cell(2, col).value in (None, ""):
            ws.cell(2, col, variant)

    for row_index, record in enumerate(records, 3):
        ws.cell(row_index, 1, record.get("End Item"))
        ws.cell(row_index, 2, record.get("Customer No"))
        ws.cell(row_index, 3, record.get("Desc"))
        for col, variant in enumerate(variants, 4):
            ws.cell(row_index, col, record.get(variant, 0))

    data_end = 2 + len(records)
    total_row = data_end + 3
    for col in range(4, last_col + 1):
        letter = get_column_letter(col)
        ws.cell(total_row, col, f"=SUM({letter}3:{letter}{data_end})")

    ws.tables["SBC"].ref = f"A2:{get_column_letter(last_col)}{data_end}"
    if "File" in ws.tables:
        file_start = total_row + 4
        file_end = file_start + 18
        ws.tables["File"].ref = f"A{file_start}:D{file_end}"


def _ordered_unique(values) -> list:
    result = []
    seen = set()
    for value in values:
        if value in (None, ""):
            continue
        key = _norm(value)
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def _copy_column_style(ws, source_col: int, target_col: int, max_row: int) -> None:
    source_letter = get_column_letter(source_col)
    target_letter = get_column_letter(target_col)
    ws.column_dimensions[target_letter] = copy(ws.column_dimensions[source_letter])
    for row in range(1, max_row + 1):
        source = ws.cell(row, source_col)
        target = ws.cell(row, target_col)
        if source.has_style:
            target._style = copy(source._style)
        target.number_format = source.number_format
        target.alignment = copy(source.alignment)
        target.protection = copy(source.protection)


def _ensure_matrix_width(ws, end_item_count: int, variants: list[str]) -> None:
    if not end_item_count:
        return
    block_width = len(variants) + 2
    required_max_col = 1 + (end_item_count - 1) * block_width + len(variants)
    while ws.max_column < required_max_col:
        ws.insert_cols(ws.max_column + 1)
        _copy_column_style(ws, max(1, ws.max_column - 1), ws.max_column, ws.max_row)


def _write_end_item_analysis_matrix(ws, records: list[dict], variants: list[str]) -> None:
    """Write legacy merge()/End_Analysis style blocks from long-form runtime rows.

    Runtime rows are saved as:
      End Item / Supplier / Cost Driver / <variant values>
    Legacy output displays a matrix with one block per End Item:
      label column + variant columns + spacer column.
    """
    if not records or not variants:
        _clear_all_tables(ws)
        return

    end_items = _ordered_unique(record.get("End Item") for record in records)
    drivers = _ordered_unique(record.get("Cost Driver") for record in records)
    if not end_items or not drivers:
        return

    by_end_driver = {
        (_norm(record.get("End Item")), _norm(record.get("Cost Driver"))): record
        for record in records
    }
    _ensure_matrix_width(ws, len(end_items), variants)
    rows_needed = len(drivers) + 2
    if ws.max_row < rows_needed:
        ws.insert_rows(ws.max_row + 1, rows_needed - ws.max_row)
        for row in range(1, rows_needed + 1):
            _copy_row_style(ws, min(row, ws.max_row), row, ws.max_column)

    block_width = len(variants) + 2
    clear_max_col = 1 + (len(end_items) - 1) * block_width + len(variants)
    for row in range(1, rows_needed + 1):
        for col in range(1, clear_max_col + 1):
            ws.cell(row, col).value = None

    for block_index, end_item in enumerate(end_items):
        label_col = 1 + block_index * block_width
        first_variant_col = label_col + 1
        ws.cell(1, first_variant_col, end_item)
        ws.cell(2, label_col, "Cost Driver")
        for offset, variant in enumerate(variants):
            ws.cell(2, first_variant_col + offset, variant)
        for row_offset, driver in enumerate(drivers, 3):
            ws.cell(row_offset, label_col, driver)
            source = by_end_driver.get((_norm(end_item), _norm(driver)), {})
            for offset, variant in enumerate(variants):
                ws.cell(row_offset, first_variant_col + offset, source.get(variant, 0))


def _write_analysis_merge(ws, records: list[dict], variants: list[str],
                          bindings: dict | None = None) -> None:
    """Overlay runtime Analysis rows without moving legacy merge() sections.

    The legacy merge() output keeps the Analysis summary and markup-breakup
    blocks at fixed visual positions. Generic table resizing can shift those
    blocks and break formulas/table refs, so this writer updates matching
    cost-driver rows in place and leaves the template structure intact.
    """
    if not records:
        return
    bindings = bindings or {}
    by_driver = {
        _norm(record.get("Cost Driver")): record
        for record in records
        if record.get("Cost Driver") not in (None, "")
    }
    label_aliases = {
        "rm cost": "rm cost/pc",
        "process cost": "process cost/pc",
        "o&h cost": "oh cost",
        "profit cost": "profit cost",
    }
    if not by_driver:
        return

    def write_table(table_name: str) -> None:
        if table_name not in ws.tables:
            return
        min_col, min_row, max_col, max_row = _table_bounds(ws.tables[table_name])
        headers = [ws.cell(min_row, col).value for col in range(min_col, max_col + 1)]
        for row in range(min_row + 1, max_row + 1):
            label = ws.cell(row, min_col).value
            label_key = _norm(label)
            record = by_driver.get(label_key) or by_driver.get(label_aliases.get(label_key, ""))
            if not record:
                continue
            for offset, header in enumerate(headers):
                if offset == 0 or header in (None, ""):
                    continue
                value = _value_for_header(record, header, bindings)
                if value is not None:
                    ws.cell(row, min_col + offset, value)

    write_table("Analysis")
    write_table("Analysis_markupbreakup")

    # Some legacy templates keep tail markup labels outside the table object.
    # Update any visible matching rows as well, still without inserting/deleting.
    target_headers = set(variants)
    for row in range(1, ws.max_row + 1):
        label = ws.cell(row, 1).value
        label_key = _norm(label)
        record = by_driver.get(label_key) or by_driver.get(label_aliases.get(label_key, ""))
        if not record:
            continue
        for col in range(2, ws.max_column + 1):
            header = ws.cell(1, col).value
            if header not in target_headers:
                continue
            value = _value_for_header(record, header, bindings)
            if value is not None:
                ws.cell(row, col, value)


def render_legacy_workbook_from_template(workbook_path: str | Path, template_path: str | None,
                                         definition: dict) -> bool:
    """Render every legacy sheet from the template, populated from DB-calculated rows."""
    path = Path(workbook_path)
    source_wb = load_workbook(path, data_only=False)
    template = _auto_template_for_source(source_wb, template_path)
    if not template or not template.exists():
        source_wb.close()
        return False
    preserve_template_only_sheets = template in set(LEGACY_TEMPLATE_CANDIDATES.values())
    template_wb = load_workbook(template)
    output_wb = load_workbook(template)
    try:
        output_config = definition.get("output") or {}
        variants = list(output_config.get("variants") or [])
        layout = _load_layout(output_config.get("output_layout_path"))
        source_names = source_wb.sheetnames
        diagnostic_sheets = set(output_config.get(
            "diagnostic_sheets",
            ["Errors", "Provenance", "Formula Trace", "End Item Input"],
        ))
        table_for_sheet = {
            "End Item": "EndItem",
            "Master_Output": "Master_Output",
            "Analysis": "Analysis",
            "Make": "Make",
            "Buy Std": "Buy_Std",
            "Assy": "Assy",
            "Pkg": "Pkg",
            "Frt": "FRT",
            "Paint": "Paint",
            "Special Process": "Special_Process",
        }
        for sheet_name in list(output_wb.sheetnames):
            if sheet_name not in template_wb.sheetnames:
                continue
            template_ws = template_wb[sheet_name]
            output_ws = output_wb[sheet_name]
            if sheet_name == "SBC":
                rows = _read_source_rows(template_wb, "SBC") if template_path is None else _read_source_rows(source_wb, "SBC")
                if rows:
                    _write_sbc(output_ws, rows, variants)
                else:
                    _clear_all_tables(output_ws)
                continue
            source_name = _source_sheet_name(sheet_name, source_names)
            table_name = table_for_sheet.get(sheet_name)
            if not source_name:
                # This sheet exists only in this legacy template family (for example
                # ST_Ukay Metal ST or 006043145Y01_gdc). Until the DB runtime emits a
                # matching calculated sheet, keep the template's sheet intact so the
                # user-facing workbook still has the legacy tab, sections, formulas,
                # colors and table objects instead of a blank/deleted sheet.
                if preserve_template_only_sheets:
                    continue
                _clear_all_tables(output_ws)
                continue
            rows = _read_source_rows(source_wb, source_name)
            if sheet_name == "Analysis":
                _write_analysis_merge(output_ws, rows, variants,
                                      _sheet_column_bindings(layout, sheet_name))
                continue
            if sheet_name == "End Item Analysis":
                _write_end_item_analysis_matrix(output_ws, rows, variants)
                continue
            if not table_name and output_ws.tables:
                table_name = next(iter(output_ws.tables.keys()))
            if rows and table_name in output_ws.tables:
                _write_records_to_table(output_ws, table_name, rows,
                                        _sheet_column_bindings(layout, sheet_name))
            elif table_name:
                # No rows for this run: preserve the legacy table/formulas instead of
                # clearing a template-specific section that the current runtime does not
                # yet know how to regenerate.
                if not preserve_template_only_sheets:
                    _clear_table_data(output_ws, table_name)
                continue
            elif not rows:
                if not preserve_template_only_sheets:
                    _clear_all_tables(output_ws)
                continue

        if template_path:
            for source_name in source_names:
                if source_name in template_wb.sheetnames or source_name in diagnostic_sheets:
                    continue
                rows = _read_source_rows(source_wb, source_name)
                if rows:
                    _copy_sheet(source_wb[source_name], output_wb, source_name[:31])

        # Legacy exactness means no diagnostic sheets in the user-facing output.
        for name in list(output_wb.sheetnames):
            if name in diagnostic_sheets:
                del output_wb[name]
        output_wb.save(path)
        return True
    finally:
        source_wb.close()
        template_wb.close()
        output_wb.close()


def render_sbc_from_template(workbook_path: str | Path, template_path: str | None,
                             definition: dict) -> bool:
    """Backward-compatible wrapper for callers that requested only SBC rendering."""
    return render_legacy_workbook_from_template(workbook_path, template_path, definition)

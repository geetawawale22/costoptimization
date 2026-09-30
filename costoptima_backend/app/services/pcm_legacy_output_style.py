from __future__ import annotations

import os
from copy import copy
from pathlib import Path
from typing import Optional

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter


LEGACY_OUTPUT_TEMPLATE_NAME = "2302C S302 FENDER OUTER TRIMS BC 10 Sep 2026 09.52.04 AM_50002264.xlsx"


def _workspace_root() -> Path:
    # app/services -> costoptima_backend -> workspace root
    return Path(__file__).resolve().parents[3]


def find_legacy_output_template(configured_path: str | None = None) -> Optional[str]:
    """Return the legacy PCM output workbook used as the style/layout template.

    Values are still produced by the DB runtime. This workbook contributes the
    visual contract from the old PCM export: sheet ordering, tab colors, panes,
    dimensions, print/page setup, and cell formatting for matching sheets.
    """
    candidates: list[Path] = []
    if configured_path:
        candidates.append(Path(configured_path))
    env_path = os.getenv("PCM_OUTPUT_TEMPLATE_PATH")
    if env_path:
        candidates.append(Path(env_path))
    root = _workspace_root()
    candidates.extend([
        root / LEGACY_OUTPUT_TEMPLATE_NAME,
        root.parent / LEGACY_OUTPUT_TEMPLATE_NAME,
    ])
    seen: set[str] = set()
    for candidate in candidates:
        try:
            resolved = candidate.expanduser().resolve()
        except Exception:
            resolved = candidate
        key = str(resolved).lower()
        if key in seen:
            continue
        seen.add(key)
        if resolved.exists() and resolved.is_file():
            return str(resolved)
    return None


def _copy_cell_style(src, dst) -> None:
    if src.has_style:
        dst._style = copy(src._style)
    if src.number_format:
        dst.number_format = src.number_format
    dst.alignment = copy(src.alignment)
    dst.font = copy(src.font)
    dst.fill = copy(src.fill)
    dst.border = copy(src.border)
    dst.protection = copy(src.protection)


def _copy_sheet_layout(src_ws, dst_ws) -> None:
    dst_ws.sheet_properties = copy(src_ws.sheet_properties)
    dst_ws.sheet_format = copy(src_ws.sheet_format)
    dst_ws.page_margins = copy(src_ws.page_margins)
    dst_ws.page_setup = copy(src_ws.page_setup)
    dst_ws.print_options = copy(src_ws.print_options)
    dst_ws.freeze_panes = src_ws.freeze_panes
    dst_ws.sheet_view.showGridLines = src_ws.sheet_view.showGridLines
    dst_ws.sheet_view.zoomScale = src_ws.sheet_view.zoomScale
    dst_ws.sheet_view.zoomScaleNormal = src_ws.sheet_view.zoomScaleNormal
    if src_ws.auto_filter and src_ws.auto_filter.ref:
        dst_ws.auto_filter.ref = src_ws.auto_filter.ref
    if src_ws.print_title_rows:
        dst_ws.print_title_rows = src_ws.print_title_rows
    if src_ws.print_title_cols:
        dst_ws.print_title_cols = src_ws.print_title_cols
    try:
        dst_ws.sheet_properties.tabColor = copy(src_ws.sheet_properties.tabColor)
    except Exception:
        pass

    for key, dim in src_ws.column_dimensions.items():
        target = dst_ws.column_dimensions[key]
        target.width = dim.width
        target.hidden = dim.hidden
        target.outlineLevel = dim.outlineLevel
        target.collapsed = dim.collapsed
        target.bestFit = dim.bestFit
        target.min = dim.min
        target.max = dim.max

    for key, dim in src_ws.row_dimensions.items():
        target = dst_ws.row_dimensions[key]
        target.height = dim.height
        target.hidden = dim.hidden
        target.outlineLevel = dim.outlineLevel
        target.collapsed = dim.collapsed
        target.ht = dim.ht
        target.thickTop = dim.thickTop
        target.thickBot = dim.thickBot

    # Copy only header/top-section merges by default. Data-row merges from a
    # sample workbook can corrupt dynamic row values when an upload has a
    # different number of BOM lines.
    existing = {str(rng) for rng in dst_ws.merged_cells.ranges}
    for merged_range in src_ws.merged_cells.ranges:
        if merged_range.min_row <= 2 and str(merged_range) not in existing:
            try:
                dst_ws.merge_cells(str(merged_range))
            except Exception:
                continue


def _template_style_cell(src_ws, row_idx: int, col_idx: int):
    if row_idx <= src_ws.max_row and col_idx <= src_ws.max_column:
        return src_ws.cell(row_idx, col_idx)
    fallback_row = min(max(row_idx, 1), max(src_ws.max_row, 1))
    if row_idx > src_ws.max_row and src_ws.max_row >= 2:
        fallback_row = 2
    fallback_col = min(max(col_idx, 1), max(src_ws.max_column, 1))
    return src_ws.cell(fallback_row, fallback_col)


def apply_legacy_output_template_styles(output_path: str | os.PathLike, template_path: str | os.PathLike | None = None) -> Optional[str]:
    """Apply legacy PCM workbook styles/layout to an already generated output.

    The function intentionally preserves all output values and formulas from the
    generated workbook. It only copies workbook/sheet/cell presentation metadata
    for sheets that exist in both workbooks.
    """
    resolved_template = find_legacy_output_template(str(template_path) if template_path else None)
    if not resolved_template:
        return None

    output_path = str(output_path)
    template_wb = load_workbook(resolved_template, data_only=False)
    output_wb = load_workbook(output_path, data_only=False)
    try:
        common = [name for name in template_wb.sheetnames if name in output_wb.sheetnames]
        for name in common:
            src_ws = template_wb[name]
            dst_ws = output_wb[name]
            _copy_sheet_layout(src_ws, dst_ws)
            max_row = max(dst_ws.max_row, 1)
            max_col = max(dst_ws.max_column, 1)
            for row_idx in range(1, max_row + 1):
                for col_idx in range(1, max_col + 1):
                    src_cell = _template_style_cell(src_ws, row_idx, col_idx)
                    dst_cell = dst_ws.cell(row_idx, col_idx)
                    _copy_cell_style(src_cell, dst_cell)
            # Keep template widths for columns beyond current values so hidden or
            # preformatted right-side sections still open like the old workbook.
            for col_idx in range(max_col + 1, src_ws.max_column + 1):
                letter = get_column_letter(col_idx)
                if letter in src_ws.column_dimensions:
                    dim = src_ws.column_dimensions[letter]
                    target = dst_ws.column_dimensions[letter]
                    target.width = dim.width
                    target.hidden = dim.hidden
                    target.outlineLevel = dim.outlineLevel
                    target.collapsed = dim.collapsed
                    target.bestFit = dim.bestFit
                    target.min = dim.min
                    target.max = dim.max

        # Reorder generated sheets to match the old workbook where names match,
        # then leave DB-only diagnostic sheets at the end.
        ordered_names = common + [name for name in output_wb.sheetnames if name not in common]
        output_wb._sheets = [output_wb[name] for name in ordered_names]
        output_wb.save(output_path)
        return resolved_template
    finally:
        template_wb.close()
        output_wb.close()


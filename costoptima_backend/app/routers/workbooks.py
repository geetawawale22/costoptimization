import openpyxl
import difflib
import io
import json
import math
import os
from pathlib import Path
import tempfile
import uuid
from datetime import datetime, date
from typing import List, Optional, Dict

import numpy as np
import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from fastapi.responses import StreamingResponse, JSONResponse
from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from app.database import get_db
from app.config import settings
from app.core.deps import get_current_user, require_permission, require_any_permission
from app.core.pagination import PaginatedResponse, paginate_query, paginate_list, DEFAULT_PAGE_SIZE
from app.models.mixins import stamp_create, stamp_update
from app.models.workbook import (
    WorkbookType, WorkbookTab, WorkbookSection, WorkbookTabColumn, WorkbookRow,
    WorkbookImportVersion, WorkbookMergedRange, HEADER_TYPES,
)
from app.schemas.workbook import (
    WorkbookTypeOut, WorkbookTypeIn, WorkbookTypeUpdate,
    WorkbookTabOut, WorkbookTabIn, WorkbookTabUpdate, WorkbookTabSummaryOut,
    WorkbookSectionOut, WorkbookSectionIn, WorkbookSectionUpdate,
    WorkbookTabColumnOut, WorkbookTabColumnIn, WorkbookTabColumnUpdate, WorkbookTabColumnBulkIn,
    DetectedColumn, ColumnDetectionResult, DetectedSection, SectionDetectionResult,
    BulkConfigQuery, BulkConfigResult, WorkbookMergedRangeOut,
    WorkbookSchemaOut, WorkbookRowIn, WorkbookRowOut,
    ImportResult, ImportResultTab, WorkbookVersionOut,
    ImportPreviewResult, ImportPreviewTab, ImportPreviewSection, ImportHeaderMapping,
    ImportCommitRequest,
)

router = APIRouter(prefix="/workbook-types", tags=["Masters & Commodity Types — Workbook Engine"])

# Temporary on-disk cache for uploaded files between /import/preview and /import/commit.
# No background job runs against this directory; it is purged lazily (best-effort) each
# time a new preview is created, and any file used by /import/commit is removed right
# after a successful commit.
IMPORT_CACHE_DIR = os.path.join(tempfile.gettempdir(), "costoptima_import_cache")
os.makedirs(IMPORT_CACHE_DIR, exist_ok=True)
IMPORT_CACHE_MAX_AGE_SECONDS = 3600


def _purge_expired_import_cache(max_age_seconds: int = IMPORT_CACHE_MAX_AGE_SECONDS):
    now = datetime.utcnow()
    try:
        for fname in os.listdir(IMPORT_CACHE_DIR):
            fpath = os.path.join(IMPORT_CACHE_DIR, fname)
            try:
                mtime = datetime.utcfromtimestamp(os.path.getmtime(fpath))
                if (now - mtime).total_seconds() > max_age_seconds:
                    os.remove(fpath)
            except OSError:
                continue
    except FileNotFoundError:
        pass


def _cache_paths(preview_id: str):
    return (
        os.path.join(IMPORT_CACHE_DIR, f"{preview_id}.xlsx"),
        os.path.join(IMPORT_CACHE_DIR, f"{preview_id}.meta.json"),
    )


# ---------------------------------------------------------------------------
# JSON-safety: pandas' DataFrame.where(cond, None) does NOT reliably turn NaN into
# Python None on numeric (float64) columns — numpy can't hold None in a float array,
# so pandas silently keeps NaN there. That NaN then reaches MySQL's JSON column as a
# literal `NaN` token (Python's json.dumps allows it by default; MySQL's strict JSON
# validator rejects it): "Invalid JSON text: 'Invalid value.'". Fixed by sanitizing
# every value explicitly rather than relying on DataFrame-level replacement.
# ---------------------------------------------------------------------------
def _json_safe_value(v):
    if v is None:
        return None
    if isinstance(v, np.generic):
        v = v.item()
    if isinstance(v, (pd.Timestamp, datetime, date)):
        return v.isoformat()
    if isinstance(v, pd.Timedelta):
        return str(v)
    if isinstance(v, dict):
        return {str(k): _json_safe_value(val) for k, val in v.items()}
    if isinstance(v, (list, tuple)):
        return [_json_safe_value(x) for x in v]
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    return v


def _json_safe_records(df: pd.DataFrame) -> List[dict]:
    records = df.to_dict(orient="records")
    return [{k: _json_safe_value(v) for k, v in rec.items()} for rec in records]

def _norm_header(value) -> str:
    if value is None:
        return ""
    return " ".join(str(value).replace("\n", " ").split()).strip().casefold()


def _is_unnamed_header(value) -> bool:
    text = "" if value is None else str(value).strip()
    return not text or text.casefold().startswith("unnamed:")


def _formula_payload(formula, cached_value, number_format=None):
    return {
        "formula": formula,
        "cached_value": _json_safe_value(cached_value),
        "number_format": number_format,
    }


def _header_rows_for(tab: WorkbookTab, section: Optional[WorkbookSection] = None) -> List[int]:
    configured = (getattr(section, "header_rows", None) if section is not None else None) or getattr(tab, "header_rows", None)
    if configured:
        return sorted({int(r) for r in configured if int(r) >= 0})
    base = section.header_row if section is not None else tab.header_row_index
    if (section.header_type if section is not None else tab.header_type) == "typeMerge":
        return [base, base + 1]
    if (section.header_type if section is not None else tab.header_type) == "typeSingle":
        return []
    return [base]


def _sheet_matrix(content: bytes, sheet_name, data_only: bool = False):
    wb = openpyxl.load_workbook(io.BytesIO(content), data_only=data_only, read_only=False)
    ws = wb[sheet_name] if isinstance(sheet_name, str) else wb.worksheets[sheet_name]
    return ws, [[cell.value for cell in row] for row in ws.iter_rows()]


def _header_candidates(matrix, header_rows: List[int], col_idx: int) -> List[str]:
    vals = []
    for r in header_rows:
        if 0 <= r < len(matrix) and col_idx < len(matrix[r]):
            v = matrix[r][col_idx]
            if v not in (None, ""):
                vals.append(str(v).strip())
    return vals


def _combined_dynamic_header(matrix, header_rows: List[int], col_idx: int) -> str:
    vals = _header_candidates(matrix, header_rows, col_idx)
    return " ".join(dict.fromkeys(vals))



def _unique_source_header(base: str, existing: set, pos: int) -> str:
    header = " ".join(str(base or "").replace("\n", " ").split()).strip()
    if not header or _is_unnamed_header(header):
        return ""
    candidate = header
    if candidate in existing:
        candidate = f"{header}_{openpyxl.utils.get_column_letter(pos + 1)}"
    counter = 2
    while candidate in existing:
        candidate = f"{header}_{openpyxl.utils.get_column_letter(pos + 1)}_{counter}"
        counter += 1
    existing.add(candidate)
    return candidate

def _dense_stacked_header(matrix, row: int) -> bool:
    """Recognize repeated grade labels below supplier labels, never numeric data."""
    if row < 0 or row + 1 >= len(matrix):
        return False
    pairs = [(a, b) for a, b in zip(matrix[row], matrix[row + 1])
             if a not in (None, "") and b not in (None, "")]
    if len(pairs) < 3 or not all(isinstance(a, str) and isinstance(b, str) for a, b in pairs):
        return False
    lower = [_norm_header(b) for _, b in pairs]
    combined = {_norm_header(a) + " " + _norm_header(b) for a, b in pairs}
    return len(set(lower)) < len(lower) and len(combined) > len(set(lower))


def _is_percent_format(fmt: Optional[str]) -> bool:
    return bool(fmt and "%" in fmt and "[" not in fmt[:1])


def _percent_decimal_places(fmt: Optional[str]) -> Optional[int]:
    if not _is_percent_format(fmt):
        return None
    fmt = str(fmt).split(";")[0]
    before_percent = fmt.split("%", 1)[0]
    if "." not in before_percent:
        return 0
    decimals = []
    for ch in before_percent.rsplit(".", 1)[1]:
        if ch in ("0", "#", "?"):
            decimals.append(ch)
        else:
            break
    return len(decimals) if decimals else 0


def _format_percent_value(value, cell_format: Optional[str]) -> str:
    percent = float(value) * 100.0
    decimals = _percent_decimal_places(cell_format)
    if decimals is not None:
        text = f"{percent:.{decimals}f}"
    else:
        text = f"{percent:.10f}".rstrip("0").rstrip(".")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return f"{text}%"


def _parse_import_value(value, cell_format: Optional[str], is_percentage: bool = False):
    if value in (None, ""):
        return None
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("="):
            return text
        if is_percentage or _is_percent_format(cell_format):
            try:
                return float(text[:-1].replace(",", "")) / 100 if text.endswith("%") else float(text.replace(",", ""))
            except ValueError:
                return value
        return value
    if is_percentage or _is_percent_format(cell_format):
        try:
            return float(value)
        except (TypeError, ValueError):
            return value
    return value


def _non_empty_count(row) -> int:
    return sum(1 for value in row if value not in (None, ""))


def _maybe_skip_title_header(matrix, header_rows: List[int]) -> List[int]:
    if len(header_rows) != 1:
        return header_rows
    row_idx = header_rows[0]
    next_idx = row_idx + 1
    if row_idx < 0 or next_idx >= len(matrix):
        return header_rows
    if _non_empty_count(matrix[row_idx]) <= 1 and _non_empty_count(matrix[next_idx]) >= 2:
        return [next_idx]
    return header_rows


def _build_dynamic_records(tab: WorkbookTab, section: Optional[WorkbookSection], content: bytes,
                           sheet_name) -> tuple[pd.DataFrame, dict]:
    """Read Excel with openpyxl so formulas, number formats and blank configured
    columns survive. Header matching is configuration-first and position-aware."""
    ws, matrix = _sheet_matrix(content, sheet_name, data_only=False)
    _, cached = _sheet_matrix(content, sheet_name, data_only=True)
    header_rows = _header_rows_for(tab, section)
    header_rows = _maybe_skip_title_header(matrix, header_rows)
    explicit_rows = (getattr(section, "header_rows", None) if section is not None else None) or tab.header_rows
    if len(header_rows) == 1 and not explicit_rows:
        base = header_rows[0]
        if _dense_stacked_header(matrix, base):
            header_rows = [base, base + 1]
        elif _dense_stacked_header(matrix, base - 1):
            header_rows = [base - 1, base]
    # Expand only actual Excel merges, and only in the header rows.
    for merged in ws.merged_cells.ranges:
        for r in header_rows:
            if merged.min_row <= r + 1 <= merged.max_row:
                for c in range(merged.min_col - 1, merged.max_col):
                    matrix[r][c] = ws.cell(merged.min_row, merged.min_col).value
    cols = sorted(_tab_columns_for_section(tab, section), key=lambda c: c.position)
    start_row = (section.data_start_row if section is not None else None)
    if start_row is None:
        start_row = (max(header_rows) + 1) if header_rows else 0
    end_row = section.data_end_row if section is not None else None
    start_col = (section.column_start if section is not None else None)
    end_col = (section.column_end if section is not None else None)
    source_positions = list(range(start_col or 0, end_col if end_col is not None else ws.max_column))

    # Header -> source column candidates. Explicit column_position always wins.
    by_header = {}
    for idx in source_positions:
        combined = _norm_header(_combined_dynamic_header(matrix, header_rows, idx))
        singles = {_norm_header(v) for v in _header_candidates(matrix, header_rows, idx)}
        for h in {combined, *singles} - {""}:
            by_header.setdefault(h, []).append(idx)

    mapped_positions = {}
    used_positions = set()
    for col in cols:
        pos = col.column_position
        if pos is None:
            matches = by_header.get(_norm_header(col.effective_header()), [])
            pos = next((x for x in matches if x not in used_positions), None)
        if pos is not None and pos in source_positions:
            mapped_positions[col.column_name] = pos
            used_positions.add(pos)

    # Preserve every named source column, even if the admin configuration did not
    # already contain it. Blank/Unnamed columns are still skipped. This is critical
    # for PCM master files where different commodities contribute different columns
    # that must appear in the final Master Rate Data union.
    existing_output_names = {c.column_name for c in cols}
    extra_positions = {}
    for pos in source_positions:
        if pos in used_positions:
            continue
        header = _combined_dynamic_header(matrix, header_rows, pos)
        if not header:
            continue
        if _is_unnamed_header(header):
            continue
        unique = _unique_source_header(header, existing_output_names, pos)
        if unique:
            extra_positions[unique] = pos

    records = []
    formula_cells = {}
    column_formats = {}
    header_groups = {}
    for r in range(start_row, min(end_row if end_row is not None else ws.max_row, ws.max_row)):
        row_data = {}
        row_formulas = {}
        unwanted = {}
        for col in cols:
            pos = mapped_positions.get(col.column_name)
            if pos is None:
                row_data[col.column_name] = None
                continue
            value = matrix[r][pos] if pos < len(matrix[r]) else None
            cached_value = cached[r][pos] if r < len(cached) and pos < len(cached[r]) else None
            cell = ws.cell(row=r + 1, column=pos + 1)
            if col.column_name not in header_groups and len(header_rows) > 1:
                top_values = _header_candidates(matrix, [header_rows[0]], pos)
                if top_values and top_values[0] and top_values[0] != col.effective_header():
                    header_groups[col.column_name] = top_values[0]
            if col.column_name not in column_formats and cell.number_format:
                column_formats[col.column_name] = cell.number_format
            parsed = _parse_import_value(value, cell.number_format,
                                          bool(col.is_percentage) or _is_percent_format(cell.number_format))
            if isinstance(value, str) and value.startswith("="):
                row_formulas[col.column_name] = _formula_payload(value, cached_value, cell.number_format)
                formula_cells.setdefault(col.column_name, []).append({
                    "row": r + 1, "formula": value, "cached_value": _json_safe_value(cached_value),
                    "number_format": cell.number_format,
                })
                parsed = _parse_import_value(cached_value, cell.number_format,
                                             bool(col.is_percentage) or _is_percent_format(cell.number_format))
            row_data[col.column_name] = _json_safe_value(parsed)
        remarks_name = getattr(tab, "remarks_column_name", None)
        if section is not None:
            remarks_name = remarks_name or getattr(section, "remarks_column_name", None)
        for extra_name, pos in extra_positions.items():
            value = matrix[r][pos] if pos < len(matrix[r]) else None
            cached_value = cached[r][pos] if r < len(cached) and pos < len(cached[r]) else None
            cell = ws.cell(row=r + 1, column=pos + 1)
            if extra_name not in column_formats and cell.number_format:
                column_formats[extra_name] = cell.number_format
            parsed = _parse_import_value(value, cell.number_format, _is_percent_format(cell.number_format))
            if isinstance(value, str) and value.startswith("="):
                row_formulas[extra_name] = _formula_payload(value, cached_value, cell.number_format)
                formula_cells.setdefault(extra_name, []).append({
                    "row": r + 1, "formula": value, "cached_value": _json_safe_value(cached_value),
                    "number_format": cell.number_format,
                })
                parsed = _parse_import_value(cached_value, cell.number_format, _is_percent_format(cell.number_format))
            if remarks_name:
                unwanted[extra_name] = _json_safe_value(parsed)
            else:
                row_data[extra_name] = _json_safe_value(parsed)
        if row_formulas:
            row_data["_formula_data"] = row_formulas
        if unwanted:
            if remarks_name:
                row_data[remarks_name] = unwanted
            else:
                row_data["_unwanted_data"] = unwanted
        if any(v is not None for v in row_data.values()):
            records.append(row_data)

    output_columns = [c.column_name for c in cols if c.column_name in mapped_positions]
    if not getattr(tab, "remarks_column_name", None) and not (section is not None and getattr(section, "remarks_column_name", None)):
        output_columns += list(extra_positions.keys())
    remarks_name = getattr(tab, "remarks_column_name", None)
    if remarks_name and remarks_name not in output_columns:
        output_columns.append(remarks_name)
    if records and any("_unwanted_data" in x for x in records):
        output_columns.append("_unwanted_data")
    if records and any("_formula_data" in x for x in records):
        output_columns.append("_formula_data")
    df = pd.DataFrame(records, columns=output_columns)
    df.attrs["formula_meta"] = formula_cells
    df.attrs["column_formats"] = column_formats
    df.attrs["header_groups"] = header_groups
    df.attrs["header_rows"] = header_rows
    df.attrs["canonical_columns"] = True
    return df, {"formula_cells": formula_cells, "column_formats": column_formats, "header_groups": header_groups, "header_rows": header_rows}


def _record_key(row_data: dict, columns: List[WorkbookTabColumn]):
    keys = [c.column_name for c in columns if c.is_unique_key]
    if not keys:
        return None
    vals = []
    for k in keys:
        v = row_data.get(k)
        if v is None or str(v).strip() == "":
            return None
        vals.append(_norm_header(v))
    return tuple(vals)


def _rows_equal(a: dict, b: dict) -> bool:
    return a == b


def _upsert_rows(db, tab, section, records, user, formula_meta=None):
    cols = _tab_columns_for_section(tab, section)
    q = db.query(WorkbookRow).filter(WorkbookRow.tab_id == tab.id, WorkbookRow.is_deleted.is_(False))
    if section is not None:
        q = q.filter(WorkbookRow.section_id == section.id)
    else:
        q = q.filter(WorkbookRow.section_id.is_(None))
    existing = q.all()
    by_key = {}
    for row in existing:
        key = _record_key(row.row_data or {}, cols)
        if key is not None and key not in by_key:
            by_key[key] = row
    inserted = updated = unchanged = 0
    now = datetime.utcnow()
    seen_keys = set()
    for rec in records:
        key = _record_key(rec, cols)
        target = by_key.get(key) if key is not None else None
        if target is not None:
            if _rows_equal(target.row_data or {}, rec):
                unchanged += 1
                continue
            target.row_data = rec
            target.version = int(target.version or 1) + 1
            stamp_update(target, user.id)
            updated += 1
            seen_keys.add(key)
        else:
            row = WorkbookRow(tab_id=tab.id, section_id=section.id if section else None,
                              row_data=rec, version=1)
            stamp_create(row, user.id)
            db.add(row)
            if key is not None:
                by_key[key] = row
                seen_keys.add(key)
            inserted += 1
    return inserted, updated, unchanged


def _sanitize_for_json(obj):
    """Recursively sanitizes an already-built nested dict/list response payload right
    before handing it to JSONResponse. Used by the preview/commit/merge endpoints,
    which return a Response object directly instead of relying on FastAPI's Pydantic
    response_model serialization — that serializer has repeatedly raised
    "TypeError: 'float' object cannot be interpreted as an integer" whenever a
    pandas-derived float reached a field declared `int` somewhere in the response tree,
    and chasing individual fields hasn't reliably closed every path it can happen
    through. Stdlib json.dumps (which JSONResponse uses) has no such int/float type
    distinction, so returning a Response object directly makes this crash class
    structurally impossible on these routes, regardless of what the source data
    contains."""
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_for_json(v) for v in obj]
    return _json_safe_value(obj)
# ---------------------------------------------------------------------------
# Lookups with structured, recoverable errors
# ---------------------------------------------------------------------------
def _get_workbook_type(db: Session, code: str) -> WorkbookType:
    wt = db.query(WorkbookType).filter(WorkbookType.code == code, WorkbookType.is_deleted.is_(False)).first()
    if not wt:
        available = [w.code for w in db.query(WorkbookType).filter(WorkbookType.is_deleted.is_(False)).all()]
        raise HTTPException(status_code=404, detail={
            "error_code": "unknown_workbook_type",
            "message": f"Unknown workbook type '{code}'. It may have been renamed or deleted.",
            "available": available,
        })
    return wt


def _get_tab(db: Session, wt: WorkbookType, tab_name: str) -> WorkbookTab:
    tab = next((t for t in wt.tabs if t.tab_name == tab_name and not t.is_deleted), None)
    if not tab:
        available = [t.tab_name for t in wt.tabs if not t.is_deleted]
        raise HTTPException(status_code=404, detail={
            "error_code": "unknown_tab",
            "message": f"Unknown tab '{tab_name}' in workbook '{wt.code}'. "
                       f"It may have been renamed or deleted — pick a current tab below.",
            "available": available,
        })
    return tab


def _get_section(db: Session, tab: WorkbookTab, section_id: int) -> WorkbookSection:
    section = next((s for s in tab.sections if s.id == section_id and not s.is_deleted), None)
    if not section:
        available = [s.section_name for s in tab.sections if not s.is_deleted]
        raise HTTPException(status_code=404, detail={
            "error_code": "unknown_section",
            "message": f"Unknown section id {section_id} in tab '{tab.tab_name}' of workbook '{tab.workbook_type.code}'.",
            "available": available,
        })
    return section


def _derive_header_type(structure: Optional[str], explicit_header_type: Optional[str], header_row_index: int) -> str:
    """Turns the user-facing `structure` choice into a real HEADER_TYPES value.
    "single" doesn't need the user to separately pick "type0" vs "type1" — both are
    parsed identically (always at header_row_index); the type0/type1 label is kept
    only for internal bookkeeping/back-compat, chosen automatically here. `structure`
    takes priority over `explicit_header_type` when both are given."""
    if structure == "none":
        return "typeSingle"
    if structure == "merge":
        return "typeMerge"
    if structure == "single":
        return "type0" if header_row_index == 0 else "type1"
    if explicit_header_type:
        if explicit_header_type not in HEADER_TYPES:
            raise HTTPException(status_code=400, detail=f"header_type must be one of {HEADER_TYPES}")
        return explicit_header_type
    return "type0" if header_row_index == 0 else "type1"


# ---------------------------------------------------------------------------
# Read-only browse endpoints (used by every Masters/Commodity Types page)
# ---------------------------------------------------------------------------
@router.get("", response_model=None)
def list_workbook_types(page: Optional[int] = None, page_size: int = DEFAULT_PAGE_SIZE, q: str = "",
                         db: Session = Depends(get_db),
                         _=Depends(require_any_permission(("masters", "view"), ("workbook_config", "view")))):
    query = db.query(WorkbookType).filter(WorkbookType.is_deleted.is_(False))
    if q:
        like = f"%{q}%"
        query = query.filter((WorkbookType.name.ilike(like)) | (WorkbookType.code.ilike(like)))
    query = query.order_by(WorkbookType.name)
    if page is None:
        return [WorkbookTypeOut.model_validate(item).model_dump(mode="json") for item in query.all()]
    items, total, page, page_size, total_pages = paginate_query(query, page, page_size)
    items = [WorkbookTypeOut.model_validate(item).model_dump(mode="json") for item in items]
    return PaginatedResponse(items=items, total=total, page=page, page_size=page_size, total_pages=total_pages)


@router.get("/{type_code}/schema", response_model=WorkbookSchemaOut)
def get_schema(type_code: str, db: Session = Depends(get_db), _=Depends(require_permission("masters", "view"))):
    """This is the endpoint WorkbookTable.jsx (every Masters/Commodity Types data
    table in the app) builds its column headers from, so it must NEVER leak a
    soft-deleted tab/section/column into the response — unlike list_tabs/
    list_sections/list_columns above, WorkbookTabOut.model_validate(tab) would
    otherwise serialize the RAW `tab.sections` / `tab.columns` relationships as-is,
    including rows with is_deleted=True (e.g. a column removed because it no longer
    exists in the current Excel). That was the root cause of deleted/obsolete
    columns reappearing in the frontend table. Sections/columns are also explicitly
    sorted by `position` here rather than trusting relationship load order alone —
    belt-and-suspenders alongside the order_by on the ORM relationships themselves."""
    wt = _get_workbook_type(db, type_code)
    tabs_out = []
    for t in wt.tabs:
        if t.is_deleted:
            continue
        tab_out = WorkbookTabOut.model_validate(t)
        tab_out.sections = [
            WorkbookSectionOut.model_validate(s)
            for s in sorted((s for s in t.sections if not s.is_deleted), key=lambda s: s.position)
        ]
        tab_out.columns = [
            WorkbookTabColumnOut.model_validate(c)
            for c in sorted((c for c in t.columns if not c.is_deleted), key=lambda c: c.position)
        ]
        tabs_out.append(tab_out)
    return WorkbookSchemaOut(workbook_type=wt, tabs=tabs_out)


@router.get("/{type_code}/tabs", response_model=PaginatedResponse[WorkbookTabSummaryOut])
def list_tabs(type_code: str, page: int = 1, page_size: int = DEFAULT_PAGE_SIZE,
              db: Session = Depends(get_db), _=Depends(require_permission("masters", "view"))):
    wt = _get_workbook_type(db, type_code)
    tabs = [t for t in wt.tabs if not t.is_deleted]
    row_counts = dict(
        db.query(WorkbookRow.tab_id, func.count(WorkbookRow.id))
          .filter(WorkbookRow.tab_id.in_([t.id for t in tabs] or [0]), WorkbookRow.is_deleted.is_(False))
          .group_by(WorkbookRow.tab_id)
          .all()
    )
    summaries = [
        WorkbookTabSummaryOut(
            id=t.id, tab_name=t.tab_name, row_count=int(row_counts.get(t.id, 0)),
            read_only=t.read_only, header_type=t.header_type, header_row_index=t.header_row_index,
            has_multiple_sections=t.has_multiple_sections,
        )
        for t in tabs
    ]
    items, total, page, page_size, total_pages = paginate_list(summaries, page, page_size)
    return PaginatedResponse(items=items, total=total, page=page, page_size=page_size, total_pages=total_pages)


@router.get("/{type_code}/tabs/{tab_name}/sections", response_model=PaginatedResponse[WorkbookSectionOut])
def list_sections(type_code: str, tab_name: str, page: int = 1, page_size: int = DEFAULT_PAGE_SIZE,
                   db: Session = Depends(get_db), _=Depends(require_permission("masters", "view"))):
    wt = _get_workbook_type(db, type_code)
    tab = _get_tab(db, wt, tab_name)
    sections = sorted([s for s in tab.sections if not s.is_deleted], key=lambda s: s.position)
    items, total, page, page_size, total_pages = paginate_list(sections, page, page_size)
    return PaginatedResponse(items=items, total=total, page=page, page_size=page_size, total_pages=total_pages)


@router.get("/{type_code}/tabs/{tab_name}/merged-ranges", response_model=PaginatedResponse[WorkbookMergedRangeOut])
def list_merged_ranges(type_code: str, tab_name: str, page: int = 1, page_size: int = DEFAULT_PAGE_SIZE,
                        db: Session = Depends(get_db), _=Depends(require_permission("masters", "view"))):
    """Raw Layer-1 merged-cell metadata captured at the last import of this tab —
    independent of how (or whether) header/section detection used any given merge.
    Not filtered by section, since a merge is a sheet-level structural fact."""
    wt = _get_workbook_type(db, type_code)
    tab = _get_tab(db, wt, tab_name)
    ranges = (db.query(WorkbookMergedRange)
                .filter(WorkbookMergedRange.tab_id == tab.id, WorkbookMergedRange.is_deleted.is_(False))
                .order_by(WorkbookMergedRange.start_row, WorkbookMergedRange.start_column).all())
    items, total, page, page_size, total_pages = paginate_list(ranges, page, page_size)
    return PaginatedResponse(items=items, total=total, page=page, page_size=page_size, total_pages=total_pages)

@router.get("/{type_code}/tabs/{tab_name}/columns", response_model=PaginatedResponse[WorkbookTabColumnOut])
def list_columns(type_code: str, tab_name: str, section_id: Optional[int] = None,
                  page: int = 1, page_size: int = DEFAULT_PAGE_SIZE,
                  db: Session = Depends(get_db), _=Depends(require_permission("masters", "view"))):
    wt = _get_workbook_type(db, type_code)
    tab = _get_tab(db, wt, tab_name)
    cols = [c for c in tab.columns if not c.is_deleted]
    if section_id is not None:
        cols = [c for c in cols if c.section_id == section_id]
    cols = sorted(cols, key=lambda c: c.position)
    items, total, page, page_size, total_pages = paginate_list(cols, page, page_size)
    return PaginatedResponse(items=items, total=total, page=page, page_size=page_size, total_pages=total_pages)


@router.get("/{type_code}/tabs/{tab_name}/rows", response_model=None)
def list_rows(type_code: str, tab_name: str, q: str = "", section_id: Optional[int] = None,
              page: Optional[int] = None, page_size: int = DEFAULT_PAGE_SIZE,
              db: Session = Depends(get_db), _=Depends(require_permission("masters", "view"))):
    wt = _get_workbook_type(db, type_code)
    tab = _get_tab(db, wt, tab_name)
    rows = [r for r in tab.rows if not r.is_deleted]
    if section_id is not None:
        rows = [r for r in rows if r.section_id == section_id]
    if q:
        q_lower = q.lower()
        rows = [r for r in rows if q_lower in str(r.row_data).lower()]
    rows = [WorkbookRowOut.model_validate(r).model_dump(mode="json") for r in rows]
    if page is None:
        return rows
    items, total, page, page_size, total_pages = paginate_list(rows, page, page_size)
    return PaginatedResponse(items=items, total=total, page=page, page_size=page_size, total_pages=total_pages)


@router.post("/{type_code}/tabs/{tab_name}/rows", response_model=WorkbookRowOut)
def add_row(type_code: str, tab_name: str, payload: WorkbookRowIn, db: Session = Depends(get_db),
            user=Depends(require_permission("masters", "create"))):
    wt = _get_workbook_type(db, type_code)
    tab = _get_tab(db, wt, tab_name)
    if tab.read_only:
        raise HTTPException(status_code=400, detail="This tab is system-generated and read-only")
    if payload.section_id is not None:
        _get_section(db, tab, payload.section_id)
    row_data = {k: _json_safe_value(v) for k, v in payload.row_data.items()}
    row = WorkbookRow(tab_id=tab.id, section_id=payload.section_id, row_data=row_data)
    stamp_create(row, user.id)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.put("/{type_code}/tabs/{tab_name}/rows/{row_id}", response_model=WorkbookRowOut)
def update_row(type_code: str, tab_name: str, row_id: int, payload: WorkbookRowIn,
               db: Session = Depends(get_db), user=Depends(require_permission("masters", "update"))):
    wt = _get_workbook_type(db, type_code)
    tab = _get_tab(db, wt, tab_name)
    row = db.query(WorkbookRow).filter(WorkbookRow.id == row_id, WorkbookRow.tab_id == tab.id,
                                        WorkbookRow.is_deleted.is_(False)).first()
    if not row:
        raise HTTPException(status_code=404, detail="Row not found")
    row.row_data = {k: _json_safe_value(v) for k, v in payload.row_data.items()}
    if payload.section_id is not None:
        _get_section(db, tab, payload.section_id)
        row.section_id = payload.section_id
    stamp_update(row, user.id)
    db.commit()
    db.refresh(row)
    return row


@router.delete("/{type_code}/tabs/{tab_name}/rows/{row_id}")
def delete_row(type_code: str, tab_name: str, row_id: int, db: Session = Depends(get_db),
               user=Depends(require_permission("masters", "delete"))):
    wt = _get_workbook_type(db, type_code)
    tab = _get_tab(db, wt, tab_name)
    row = db.query(WorkbookRow).filter(WorkbookRow.id == row_id, WorkbookRow.tab_id == tab.id,
                                        WorkbookRow.is_deleted.is_(False)).first()
    if not row:
        raise HTTPException(status_code=404, detail="Row not found")
    row.is_deleted = True
    stamp_update(row, user.id)
    db.commit()
    return {"message": "Row deleted", "row_id": row_id}


@router.delete("/{type_code}/tabs/{tab_name}/rows")
def delete_all_rows(type_code: str, tab_name: str, section_id: Optional[int] = None,
                    db: Session = Depends(get_db), user=Depends(require_permission("masters", "delete"))):
    wt = _get_workbook_type(db, type_code)
    tab = _get_tab(db, wt, tab_name)
    if section_id is not None:
        _get_section(db, tab, section_id)
    rows = db.query(WorkbookRow).filter(WorkbookRow.tab_id == tab.id, WorkbookRow.is_deleted.is_(False))
    if section_id is not None:
        rows = rows.filter(WorkbookRow.section_id == section_id)
    count = 0
    for row in rows.all():
        row.is_deleted = True
        stamp_update(row, user.id)
        count += 1
    db.commit()
    return {"message": "Rows deleted", "rows_deleted": count}



def _sheet_max_column(content: bytes, sheet_name) -> int:
    """Real column count of the sheet, via openpyxl — used to resolve an open-ended
    (column_end=None) section's column range to an actual usecols list."""
    wb_xl = openpyxl.load_workbook(io.BytesIO(content), data_only=True)
    ws = wb_xl[sheet_name] if isinstance(sheet_name, str) else wb_xl.worksheets[sheet_name]
    return ws.max_column


def _section_usecols(section: "WorkbookSection", total_cols: Optional[int]) -> Optional[List[int]]:
    """0-based positional column indices this section is scoped to, or None for the
    whole sheet width. Positional indices work identically regardless of whether the
    caller later reads with header=None or header=<row>, so this list can be reused
    for both the typeMerge and single-header-row read paths below."""
    if section.column_start is None and section.column_end is None:
        return None
    start = section.column_start or 0
    end = section.column_end if section.column_end is not None else total_cols
    return list(range(start, end))


# ---------------------------------------------------------------------------
# Shared read/validate helpers used by import (direct), preview and commit.
# ---------------------------------------------------------------------------
def _read_dataframe_for_tab(tab: WorkbookTab, content: bytes, sheet_name=0,
                             header_override: Optional[int] = None):
    """Configuration-driven Excel reader. It keeps the public DataFrame interface
    used by preview/import, but delegates cell extraction to openpyxl so formula text,
    percentage semantics, configured blank columns and arbitrary header-row counts are
    not lost."""
    if header_override is not None and tab.header_rows is None and tab.header_type not in ("typeMerge", "typeSingle"):
        original = tab.header_row_index
        tab.header_row_index = header_override
        try:
            return [(None, _build_dynamic_records(tab, None, content, sheet_name)[0])]
        finally:
            tab.header_row_index = original

    if tab.has_multiple_sections and tab.sections:
        results = []
        for section in sorted((s for s in tab.sections if not s.is_deleted), key=lambda s: s.position):
            df, _meta = _build_dynamic_records(tab, section, content, sheet_name)
            results.append((section, df))
        return results

    df, _meta = _build_dynamic_records(tab, None, content, sheet_name)
    return [(None, df)]

def _tab_columns_for_section(tab: WorkbookTab, section: Optional[WorkbookSection]) -> List[WorkbookTabColumn]:
    return [c for c in tab.columns if not c.is_deleted and (c.section_id == section.id if section else c.section_id is None)]


def _column_identity_taken(tab: WorkbookTab, section_id: Optional[int], column_name: str,
                           header_name: Optional[str], exclude_id: Optional[int] = None) -> bool:
    wanted = {_norm_header(column_name), _norm_header(header_name)}
    wanted.discard("")
    if not wanted:
        return False
    for col in tab.columns:
        if col.is_deleted or col.section_id != section_id or (exclude_id is not None and col.id == exclude_id):
            continue
        existing = {_norm_header(col.column_name), _norm_header(col.header_name)}
        existing.discard("")
        if wanted & existing:
            return True
    return False


def _should_skip_config_column(tab: WorkbookTab, section_id: Optional[int], column_name: str,
                               header_name: Optional[str]) -> bool:
    return (_is_unnamed_header(column_name)
            or _is_unnamed_header(header_name or column_name)
            or _column_identity_taken(tab, section_id, column_name, header_name))


def _apply_header_mapping(tab: WorkbookTab, section: Optional[WorkbookSection], df: pd.DataFrame) -> pd.DataFrame:
    """Renames source columns (their literal header text, i.e. effective_header()) to
    the canonical column_name used in row_data / the rest of the app."""
    if df.attrs.get("canonical_columns"):
        return df
    cols = _tab_columns_for_section(tab, section)
    rename_map = {}
    normalized = {_norm_header(c): c for c in df.columns}
    for col in cols:
        header = col.effective_header()
        source = header if header in df.columns else normalized.get(_norm_header(header))
        if source is not None and source != col.column_name:
            rename_map[source] = col.column_name
    return df.rename(columns=rename_map) if rename_map else df


def _validate_dataframe(tab: WorkbookTab, section: Optional[WorkbookSection], df: pd.DataFrame,
                         ignored_columns: Optional[set] = None) -> List[str]:
    """Strict by default: EVERY configured column must be present in the file, not
    just required ones — a configured column absent from the file is a 'missing'
    error (which blocks the import) unless its name is in `ignored_columns` (an
    explicit, per-import acknowledgment that this column doesn't apply here, supplied
    via ImportCommitOverride.ignored_columns — see commit_import)."""
    ignored_columns = ignored_columns or set()
    errors = []
    cols = _tab_columns_for_section(tab, section)
    for col in cols:
        if col.column_name in ignored_columns:
            continue
        if col.column_name not in df.columns:
            errors.append(f"Required column '{col.column_name}' is missing")
            continue
        if col.is_required and df[col.column_name].isnull().any():
            bad_rows = df[df[col.column_name].isnull()].index.tolist()
            errors.append(f"Column '{col.column_name}' has blank values in row(s): {bad_rows[:5]}")
    unique_cols = [c.column_name for c in cols if c.is_unique_key and c.column_name not in ignored_columns]
    for col in unique_cols:
        if col in df.columns and df[col].duplicated().any():
            errors.append(f"Column '{col}' has duplicate values and must be unique")
    return errors

def _build_header_mapping_preview(tab: WorkbookTab, section: Optional[WorkbookSection],
                                   df_columns: List[str], canonical: bool = False) -> List[dict]:
    """Compares configured columns against what's actually in the uploaded file and
    reports, per configured column: matched / unmatched (+ a fuzzy-match suggestion),
    plus any header present in the file with no configured column at all ("extra")."""
    cols = _tab_columns_for_section(tab, section)
    df_columns = [str(c) for c in df_columns]
    used_source = set()
    mappings = []
    for col in cols:
        header = col.effective_header()
        source = col.column_name if canonical else header
        if source in df_columns:
            mappings.append({"source_header": header, "matched_column": col.column_name,
                              "suggested_column": None, "status": "matched"})
            used_source.add(source)
        else:
            candidates = [c for c in df_columns if c not in used_source]
            close = difflib.get_close_matches(header, candidates, n=1, cutoff=0.6)
            mappings.append({"source_header": header, "matched_column": None,
                              "suggested_column": close[0] if close else None, "status": "unmatched"})
    configured_headers = {c.effective_header() for c in cols}
    for h in df_columns:
        if h not in configured_headers and h not in used_source:
            mappings.append({"source_header": h, "matched_column": None,
                              "suggested_column": None, "status": "extra"})
    return mappings

def _suggest_header_row(content: bytes, sheet_name, configured_headers: List[str],
                         current_header_row: int, max_scan: int = 15) -> Optional[int]:
    """Scans the first `max_scan` rows of the raw sheet (no header applied) and scores
    each one by how many of the tab's configured column headers it contains verbatim
    (case-insensitive). Returns the best-scoring row (0-based) only if it clearly beats
    the row currently configured — this is what powers the "header might actually be on
    row X" hint when a preview comes back with almost every configured column
    'unmatched', which is the common symptom of a blank spacer row sitting above the
    real header (every detected column shows as "Unnamed: N" and the real header text
    shows up as the first data row instead)."""
    if not configured_headers:
        return None
    try:
        raw = pd.read_excel(io.BytesIO(content), sheet_name=sheet_name, header=None, nrows=max_scan)
    except Exception:
        return None

    wanted = {h.strip().lower() for h in configured_headers if h}
    best_row, best_score = None, 0
    for row_idx in range(len(raw)):
        cells = {str(v).strip().lower() for v in raw.iloc[row_idx].tolist() if pd.notna(v)}
        score = len(wanted & cells)
        if score > best_score:
            best_score, best_row = score, row_idx

    current_cells = set()
    if 0 <= current_header_row < len(raw):
        current_cells = {str(v).strip().lower() for v in raw.iloc[current_header_row].tolist() if pd.notna(v)}
    current_score = len(wanted & current_cells)

    if best_row is not None and best_score > current_score and best_score >= max(2, len(wanted) // 3):
        return best_row
    return None


def _read_merged_header_rows(content: bytes, sheet_name, row_indices: List[int]) -> List[List[str]]:
    """Reads specific 0-based rows as header text, forward-filling merged cells so
    every column in a merge range sees the merge's top-left value. pandas.read_excel
    does NOT expose this — for a merged header cell it leaves every cell but the
    top-left one blank, which pandas then auto-names "Unnamed: N", silently shifting
    which file column a configured column actually matches. openpyxl exposes
    ws.merged_cells.ranges directly, so this reads the header rows through openpyxl
    instead of pandas."""
    wb_xl = openpyxl.load_workbook(io.BytesIO(content), data_only=True)
    ws = wb_xl[sheet_name] if isinstance(sheet_name, str) else wb_xl.worksheets[sheet_name]
    max_col = ws.max_column

    merge_lookup = {}  # (row, col) 1-based -> top-left (row, col) of its merge range
    for merged_range in ws.merged_cells.ranges:
        top_left = (merged_range.min_row, merged_range.min_col)
        for r in range(merged_range.min_row, merged_range.max_row + 1):
            for c in range(merged_range.min_col, merged_range.max_col + 1):
                merge_lookup[(r, c)] = top_left

    rows = []
    for row_idx_0based in row_indices:
        excel_row = row_idx_0based + 1
        cells = []
        for col in range(1, max_col + 1):
            src_row, src_col = merge_lookup.get((excel_row, col), (excel_row, col))
            val = ws.cell(row=src_row, column=src_col).value
            cells.append("" if val is None else str(val).strip())
        rows.append(cells)
    return rows


def _combine_header_cells(row1_cells: List[str], row2_cells: List[str]) -> List[str]:
    """Combines two header rows into one label per column:
      - both rows have text -> join them (covers a shared group label spanning
        several columns AND fully independent per-column text on each row, e.g.
        "Landed Price/Ltr (April18)" over "APR#18")
      - only one row has text -> use that row alone (e.g. "Sr", "Paint Supplier",
        "SAP P.Code" where the second row is blank)
      - neither has text -> blank (becomes "Unnamed: N" downstream)
    Used identically by real import, preview, and column auto-detection so they can
    never disagree about what a given column's header actually is."""
    n = max(len(row1_cells), len(row2_cells))
    combined = []
    for i in range(n):
        top = row1_cells[i].strip() if i < len(row1_cells) else ""
        bottom = row2_cells[i].strip() if i < len(row2_cells) else ""
        if top and bottom:
            combined.append(top if top == bottom else f"{top} {bottom}")
        else:
            combined.append(top or bottom)
    return combined


def _read_raw_header_rows(content: bytes, sheet_name, row_indices: List[int]) -> List[List[str]]:
    """Kept as a thin alias — preview_import calls this name; the merge-aware reader
    above does the real work, including forward-filling merged cells."""
    return _read_merged_header_rows(content, sheet_name, row_indices)


def _capture_merged_ranges(db: Session, tab: WorkbookTab, content: bytes, sheet_name, user_id: int):
    """Raw preservation (Layer 1) of every merged range on the sheet actually being
    imported — independent of whatever _detect_sections/_looks_like_stacked_header
    made of it for header interpretation (Layer 2). Best-effort: a failure here must
    never fail the actual data import, since this is auxiliary structural metadata,
    not row data. Replaces the tab's previous snapshot wholesale — this mirrors how
    columns/sections already represent "the current structure", not row-level
    history (WorkbookImportVersion already covers row history separately)."""
    try:
        wb_xl = openpyxl.load_workbook(io.BytesIO(content), data_only=True)
        ws = wb_xl[sheet_name] if isinstance(sheet_name, str) else wb_xl.worksheets[sheet_name]
        merges = list(ws.merged_cells.ranges)
    except Exception:
        return
    db.query(WorkbookMergedRange).filter(WorkbookMergedRange.tab_id == tab.id).delete()
    now = datetime.utcnow()
    mappings = []
    for m in merges:
        try:
            top_left_value = ws.cell(row=m.min_row, column=m.min_col).value
        except Exception:
            top_left_value = None
        mappings.append({
            "tab_id": tab.id, "worksheet_name": str(ws.title),
            "merged_range": str(m), "top_left_cell": f"{openpyxl.utils.get_column_letter(m.min_col)}{m.min_row}",
            "start_row": m.min_row, "end_row": m.max_row,
            "start_column": m.min_col, "end_column": m.max_col,
            "value": None if top_left_value is None else str(top_left_value),
            "created_by": user_id, "created_at": now, "modified_by": user_id, "modified_at": now,
            "is_deleted": False,
        })
    if mappings:
        db.bulk_insert_mappings(WorkbookMergedRange, mappings)

def _infer_data_type(series: pd.Series) -> str:
    """Best-effort guess at a column's data_type from its actual values, so an admin
    doesn't have to specify it by hand for every column — used by the
    '.../columns/detect' endpoint that pre-fills the Bulk Add Columns grid."""
    non_null = series.dropna()
    if non_null.empty:
        return "string"
    if pd.api.types.is_bool_dtype(non_null):
        return "boolean"
    if pd.api.types.is_datetime64_any_dtype(non_null):
        return "date"
    if pd.api.types.is_numeric_dtype(non_null):
        return "number"

    sample = non_null.astype(str).str.strip()
    lowered = sample.str.lower()
    if set(lowered.unique()) <= {"true", "false", "yes", "no", "y", "n"}:
        return "boolean"
    try:
        pd.to_datetime(sample.head(25), errors="raise")
        return "date"
    except Exception:
        pass
    try:
        pd.to_numeric(sample.head(25), errors="raise")
        return "number"
    except Exception:
        pass
    return "string"

def _import_tab_dataframe(db, tab, section, df, user, ignored_columns=None):
    df = _apply_header_mapping(tab, section, df)
    errors = _validate_dataframe(tab, section, df, ignored_columns=ignored_columns)
    status_str = "Invalid" if any("missing" in e for e in errors) else ("Needs Review" if errors else "Valid")
    if status_str == "Invalid":
        return status_str, len(df), errors, None

    records = _json_safe_records(df)
    remarks_name = getattr(tab, "remarks_column_name", None)
    if remarks_name and not any(c.column_name == remarks_name and not c.is_deleted and
                                 c.section_id == (section.id if section else None) for c in tab.columns):
        col = WorkbookTabColumn(tab_id=tab.id, section_id=section.id if section else None,
                                column_name=remarks_name, header_name=remarks_name,
                                data_type="string", position=len(tab.columns))
        stamp_create(col, user.id)
        db.add(col)
        db.flush()
    # Formula definitions are configuration, not merely calculated values. Capture
    # the source formula and number format on the configured column for reuse on
    # future exports/calculation templates.
    formula_meta = df.attrs.get("formula_meta", {})
    column_formats = df.attrs.get("column_formats", {})
    header_groups = df.attrs.get("header_groups", {})
    configured = {c.column_name: c for c in _tab_columns_for_section(tab, section)}
    # If the uploaded Excel contains named columns that were not previously
    # configured, add them now so workbook_rows and future Master Rate Data exports
    # remain lossless. Blank/Unnamed/internal columns are intentionally skipped.
    next_position = (max((c.position or 0 for c in configured.values()), default=-1) + 1)
    next_column_position = (max(((c.column_position if c.column_position is not None else c.position or 0) for c in configured.values()), default=-1) + 1)
    for df_col in df.columns:
        name = str(df_col)
        if name in configured or name.startswith("_") or _is_unnamed_header(name):
            continue
        col = WorkbookTabColumn(tab_id=tab.id, section_id=section.id if section else None,
                                column_name=name, header_name=name, data_type="string",
                                position=next_position, column_position=next_column_position,
                                is_required=False)
        stamp_create(col, user.id)
        db.add(col)
        db.flush()
        configured[name] = col
        next_position += 1
        next_column_position += 1
    for name, group in header_groups.items():
        col = configured.get(name)
        if col and group:
            col.header_group = group
    for name, fmt in column_formats.items():
        col = configured.get(name)
        if col and fmt:
            col.number_format = col.number_format or fmt
            col.is_percentage = bool(col.is_percentage or _is_percent_format(fmt))
    for name, formulas in formula_meta.items():
        col = configured.get(name)
        if not col or not formulas:
            continue
        sample = formulas[0]
        col.formula_config = {"mode": "source", "formula": sample.get("formula"),
                              "cached_value": sample.get("cached_value"),
                              "cells": formulas[:100]}
        col.number_format = sample.get("number_format") or col.number_format
        if _is_percent_format(col.number_format):
            col.is_percentage = True

    inserted, updated, unchanged = _upsert_rows(db, tab, section, records, user)
    snapshot_rows = [{"row_data": r.row_data, "version": int(r.version or 1)} for r in (
        db.query(WorkbookRow).filter(WorkbookRow.tab_id == tab.id, WorkbookRow.is_deleted.is_(False),
                                      WorkbookRow.section_id == (section.id if section else None)).all()
    )]
    return status_str, inserted + updated + unchanged, errors, snapshot_rows


@router.post("/{type_code}/import", response_model=ImportResult)
def import_workbook(type_code: str, file: UploadFile = File(...), db: Session = Depends(get_db),
                     user=Depends(require_permission("masters", "create"))):
    """Upload a full multi-tab Excel file directly (no preview step). Every tab is
    validated before anything is committed. Prefer /import/preview + /import/commit
    when you want the user to review/fix header mappings first."""
    wt = _get_workbook_type(db, type_code)
    content = file.file.read()
    try:
        xl = pd.ExcelFile(io.BytesIO(content))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not read Excel file: {exc}")

    results: List[ImportResultTab] = []
    snapshot = {}
    active_tabs = [t for t in wt.tabs if not t.is_deleted]

    for tab in active_tabs:
        if tab.tab_name not in xl.sheet_names:
            results.append(ImportResultTab(tab_name=tab.tab_name, status="Invalid", rows_imported=0,
                                            errors=[f"Sheet '{tab.tab_name}' not found in uploaded file"]))
            continue
        try:
            section_frames = _read_dataframe_for_tab(tab, content, sheet_name=tab.tab_name)
        except Exception as exc:
            results.append(ImportResultTab(tab_name=tab.tab_name, status="Invalid", rows_imported=0,
                                            errors=[f"Could not parse sheet '{tab.tab_name}': {exc}"]))
            continue
        _capture_merged_ranges(db, tab, content, tab.tab_name, user.id)

        tab_errors, tab_rows, tab_snapshot, worst_status = [], 0, {}, "Valid"
        for section, df in section_frames:
            status_str, n_rows, errors, snap = _import_tab_dataframe(db, tab, section, df, user)
            tab_errors.extend([f"[{section.section_name}] {e}" if section else e for e in errors])
            tab_rows += n_rows
            if snap is not None:
                tab_snapshot[section.section_name if section else "_default"] = snap
            if status_str == "Invalid":
                worst_status = "Invalid"
            elif status_str == "Needs Review" and worst_status != "Invalid":
                worst_status = "Needs Review"
        results.append(ImportResultTab(tab_name=tab.tab_name, status=worst_status, rows_imported=tab_rows,
                                        errors=tab_errors))
        if tab_snapshot:
            snapshot[tab.tab_name] = tab_snapshot

    version_num = (db.query(WorkbookImportVersion)
                     .filter(WorkbookImportVersion.workbook_type_id == wt.id).count()) + 1
    version = WorkbookImportVersion(workbook_type_id=wt.id, version=version_num, snapshot=snapshot)
    stamp_create(version, user.id)
    db.add(version)
    db.commit()

    return ImportResult(workbook_type=wt.code, version=version_num, tabs=results)


@router.post("/{type_code}/tabs/{tab_name}/import", response_model=ImportResultTab)
def import_single_tab(type_code: str, tab_name: str, file: UploadFile = File(...),
                       db: Session = Depends(get_db), user=Depends(require_permission("masters", "create"))):
    """Imports just one file/tab on its own — e.g. re-upload only 'Net Consumption Base'
    without touching any other sheet in its parent workbook. Reads the first sheet of
    the uploaded file regardless of its name. Prefer /import/preview + /import/commit
    with tab_name set when you want the user to review/fix header mappings first."""
    wt = _get_workbook_type(db, type_code)
    tab = _get_tab(db, wt, tab_name)
    if tab.read_only:
        raise HTTPException(status_code=400, detail="This tab is system-generated and read-only")

    content = file.file.read()
    try:
        section_frames = _read_dataframe_for_tab(tab, content, sheet_name=0)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not read Excel file: {exc}")
    _capture_merged_ranges(db, tab, content, 0, user.id)

    tab_errors, tab_rows, tab_snapshot, worst_status = [], 0, {}, "Valid"
    for section, df in section_frames:
        status_str, n_rows, errors, snap = _import_tab_dataframe(db, tab, section, df, user)
        tab_errors.extend([f"[{section.section_name}] {e}" if section else e for e in errors])
        tab_rows += n_rows
        if snap is not None:
            tab_snapshot[section.section_name if section else "_default"] = snap
        if status_str == "Invalid":
            worst_status = "Invalid"
        elif status_str == "Needs Review" and worst_status != "Invalid":
            worst_status = "Needs Review"

    if tab_snapshot:
        version_num = (db.query(WorkbookImportVersion)
                         .filter(WorkbookImportVersion.workbook_type_id == wt.id).count()) + 1
        version = WorkbookImportVersion(workbook_type_id=wt.id, version=version_num,
                                         tab_name=tab_name, snapshot={tab_name: tab_snapshot})
        stamp_create(version, user.id)
        db.add(version)

    db.commit()
    return ImportResultTab(tab_name=tab_name, status=worst_status, rows_imported=tab_rows, errors=tab_errors)


# ---------------------------------------------------------------------------
# Import PREVIEW -> COMMIT — shows the user exactly what an import will do before
# any row is written, and lets header-mapping mismatches be resolved in place
# ("Uploaded Header: Customer Name" -> map to configured "Customer_Name") instead of
# restarting the whole upload.
# ---------------------------------------------------------------------------
@router.post("/{type_code}/import/preview", response_model=ImportPreviewResult)
def preview_import(type_code: str, file: UploadFile = File(...), tab_name: Optional[str] = Query(
        None, description="Set to preview a single tab only; omit to preview the whole workbook."),
        header_row_override: Optional[int] = Query(
            None, description="1-based; overrides the header row for this preview only "
                               "(single-header tabs). Use the 'suggested_header_row' from a prior "
                               "preview response to correct a misconfigured header row."),
        db: Session = Depends(get_db), user=Depends(require_permission("masters", "create"))):
    """Returns a JSONResponse built from plain dicts (see module docstring on
    _sanitize_for_json) rather than relying on response_model=ImportPreviewResult for
    the actual serialization — response_model is kept on the decorator purely so this
    endpoint still shows up correctly in the OpenAPI docs."""
    wt = _get_workbook_type(db, type_code)
    content = file.file.read()
    try:
        xl = pd.ExcelFile(io.BytesIO(content))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not read Excel file: {exc}")

    _purge_expired_import_cache()
    preview_id = uuid.uuid4().hex
    cache_path, meta_path = _cache_paths(preview_id)
    with open(cache_path, "wb") as f:
        f.write(content)
    with open(meta_path, "w") as f:
        json.dump({"type_code": type_code, "tab_name": tab_name, "file_name": file.filename,
                    "created_at": datetime.utcnow().isoformat()}, f)

    active_tabs = [_get_tab(db, wt, tab_name)] if tab_name else [t for t in wt.tabs if not t.is_deleted]

    tab_previews = []
    for tab in active_tabs:
        sheet_name_to_use = xl.sheet_names[0] if tab_name else tab.tab_name
        found = True if tab_name else sheet_name_to_use in xl.sheet_names
        if not found:
            tab_previews.append({"tab_name": tab.tab_name, "found_in_file": False, "sheet_used": None,
                                  "sections": [], "status": "Invalid", "suggested_header_row": None})
            continue

        is_single_header = tab.header_type in ("type0", "type1") and not (tab.has_multiple_sections and tab.sections)
        header_override_0based = (header_row_override - 1) if (header_row_override is not None and is_single_header) else None

        try:
            section_frames = _read_dataframe_for_tab(tab, content, sheet_name=sheet_name_to_use,
                                                      header_override=header_override_0based)
        except Exception as exc:
            tab_previews.append({
                "tab_name": tab.tab_name, "found_in_file": True, "sheet_used": sheet_name_to_use, "status": "Invalid",
                "suggested_header_row": None,
                "sections": [{"section_name": None, "header_row": tab.header_row_index, "columns_detected": [],
                               "header_mappings": [], "missing_required": [], "sample_rows": [], "total_rows": 0,
                               "errors": [f"Could not parse sheet '{sheet_name_to_use}': {exc}"], "warnings": [],
                               "status": "Invalid"}],
            })
            continue
        header_rows_preview = None
        if tab.header_type == "typeMerge" and not (tab.has_multiple_sections and tab.sections):
            header_rows_preview = _read_raw_header_rows(content, sheet_name_to_use, [0, 1])
        section_previews = []
        worst = "Valid"
        for section, df in section_frames:
            df_cols = [str(c) for c in df.columns]
            mappings = _build_header_mapping_preview(tab, section, df_cols,
                                                     canonical=df.attrs.get("canonical_columns", False))
            mapped_df = _apply_header_mapping(tab, section, df)
            validation_errors = _validate_dataframe(tab, section, mapped_df)
            required_headers = {c.effective_header() for c in _tab_columns_for_section(tab, section) if c.is_required}
            missing_required = [m["source_header"] for m in mappings
                                 if m["status"] == "unmatched" and m["source_header"] in required_headers]
            has_unmatched = any(m["status"] == "unmatched" for m in mappings)
            status_str = "Invalid" if any("missing" in e for e in validation_errors) else (
                "Needs Review" if (validation_errors or missing_required or has_unmatched) else "Valid")
            if status_str == "Invalid":
                worst = "Invalid"
            elif status_str == "Needs Review" and worst != "Invalid":
                worst = "Needs Review"

            sample = _json_safe_records(df.head(20))
            effective_header_row = section.header_row if section else (
                header_override_0based if header_override_0based is not None else tab.header_row_index)
            section_previews.append({
                "section_name": section.section_name if section else None,
                "header_row": int(effective_header_row),
                "columns_detected": df_cols,
                "header_mappings": mappings,
                "missing_required": missing_required, "sample_rows": sample, "total_rows": int(len(df)),
                "errors": validation_errors, "warnings": [], "status": status_str,
                "header_rows": header_rows_preview,
            })

        suggested_header_row = None
        if header_row_override is None and worst != "Valid" and is_single_header:
            configured_headers = [c.effective_header() for c in _tab_columns_for_section(tab, None)]
            suggestion = _suggest_header_row(content, sheet_name_to_use, configured_headers, tab.header_row_index)
            if suggestion is not None:
                suggested_header_row = int(suggestion) + 1

        tab_previews.append({
            "tab_name": tab.tab_name, "found_in_file": True, "sheet_used": sheet_name_to_use,
            "sections": section_previews, "status": worst, "suggested_header_row": suggested_header_row,
        })

    payload = {"preview_id": preview_id, "workbook_type": wt.code, "file_name": file.filename, "tabs": tab_previews}
    return JSONResponse(content=_sanitize_for_json(payload))


@router.post("/{type_code}/import/commit", response_model=ImportResult)
def commit_import(type_code: str, payload: ImportCommitRequest, db: Session = Depends(get_db),
                   user=Depends(require_permission("masters", "create"))):
    wt = _get_workbook_type(db, type_code)
    cache_path, meta_path = _cache_paths(payload.preview_id)
    if not os.path.exists(cache_path):
        raise HTTPException(status_code=400, detail="This import preview has expired or was already committed. "
                                                      "Please re-upload the file.")
    with open(cache_path, "rb") as f:
        content = f.read()
    meta = {}
    if os.path.exists(meta_path):
        with open(meta_path) as f:
            meta = json.load(f)
    single_tab_name = meta.get("tab_name")

    try:
        xl = pd.ExcelFile(io.BytesIO(content))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not read Excel file: {exc}")

    active_tabs = [_get_tab(db, wt, single_tab_name)] if single_tab_name else [t for t in wt.tabs if not t.is_deleted]
    if payload.tab_names:
        active_tabs = [t for t in active_tabs if t.tab_name in payload.tab_names]

    results = []
    snapshot = {}

    for tab in active_tabs:
        if tab.read_only:
            results.append({"tab_name": tab.tab_name, "status": "Invalid", "rows_imported": 0,
                             "errors": ["This tab is system-generated and read-only"]})
            continue
        sheet_name_to_use = xl.sheet_names[0] if single_tab_name else tab.tab_name
        if not single_tab_name and sheet_name_to_use not in xl.sheet_names:
            results.append({"tab_name": tab.tab_name, "status": "Invalid", "rows_imported": 0,
                             "errors": [f"Sheet '{tab.tab_name}' not found in uploaded file"]})
            continue

        is_single_header = tab.header_type in ("type0", "type1") and not (tab.has_multiple_sections and tab.sections)
        header_override_0based = (payload.header_row_override - 1) if (payload.header_row_override is not None and is_single_header) else None

        try:
            section_frames = _read_dataframe_for_tab(tab, content, sheet_name=sheet_name_to_use,
                                                      header_override=header_override_0based)
        except Exception as exc:
            results.append({"tab_name": tab.tab_name, "status": "Invalid", "rows_imported": 0,
                             "errors": [f"Could not parse sheet '{sheet_name_to_use}': {exc}"]})
            continue
        _capture_merged_ranges(db, tab, content, sheet_name_to_use, user.id)

        tab_errors, tab_rows, tab_snapshot, worst_status = [], 0, {}, "Valid"
        for section, df in section_frames:
            key = f"{tab.tab_name}::{section.section_name}" if section else tab.tab_name
            override = payload.overrides.get(key) or payload.overrides.get(tab.tab_name)
            if override and override.header_mapping:
                col_by_name = {c.column_name: c for c in _tab_columns_for_section(tab, section)}
                rename_map = {
                    source_header: col_name
                    for col_name, source_header in override.header_mapping.items()
                    if col_name in col_by_name and source_header in df.columns
                }
                if rename_map:
                    df = df.rename(columns=rename_map)
            ignored_columns = set(override.ignored_columns) if override else set()
            status_str, n_rows, errors, snap = _import_tab_dataframe(db, tab, section, df, user, ignored_columns=ignored_columns)
            tab_errors.extend([f"[{section.section_name}] {e}" if section else e for e in errors])
            tab_rows += n_rows
            if snap is not None:
                tab_snapshot[section.section_name if section else "_default"] = snap
            if status_str == "Invalid":
                worst_status = "Invalid"
            elif status_str == "Needs Review" and worst_status != "Invalid":
                worst_status = "Needs Review"

        results.append({"tab_name": tab.tab_name, "status": worst_status, "rows_imported": int(tab_rows),
                         "errors": tab_errors})
        if tab_snapshot:
            snapshot[tab.tab_name] = tab_snapshot

    version_num = (db.query(WorkbookImportVersion)
                     .filter(WorkbookImportVersion.workbook_type_id == wt.id).count()) + 1
    version = WorkbookImportVersion(workbook_type_id=wt.id, version=version_num,
                                     tab_name=single_tab_name, snapshot=snapshot)
    stamp_create(version, user.id)
    db.add(version)
    db.commit()

    for p in (cache_path, meta_path):
        try:
            os.remove(p)
        except OSError:
            pass

    payload_out = {"workbook_type": wt.code, "version": int(version_num), "tabs": results}
    return JSONResponse(content=_sanitize_for_json(payload_out))

def _excel_write_value(cell, value, number_format=None):
    if isinstance(value, (dict, list, tuple)):
        value = json.dumps(value, ensure_ascii=False, default=str)
    cell.value = value
    if number_format:
        cell.number_format = number_format


def _merge_header_groups(ws, cols: List[WorkbookTabColumn], positions: dict, row_number: int):
    ordered = sorted(cols, key=lambda c: positions[id(c)])
    start = 0
    while start < len(ordered):
        group = ordered[start].header_group
        end = start
        while (group and end + 1 < len(ordered)
               and ordered[end + 1].header_group == group
               and positions[id(ordered[end + 1])] == positions[id(ordered[end])] + 1):
            end += 1
        if group and end > start:
            start_col = positions[id(ordered[start])] + 1
            end_col = positions[id(ordered[end])] + 1
            if start_col < end_col:
                ws.merge_cells(start_row=row_number, start_column=start_col,
                               end_row=row_number, end_column=end_col)
        start = end + 1


def _export_tab_to_workbook(wb, tab: WorkbookTab):
    ws = wb.create_sheet(title=tab.tab_name[:31])
    cols = sorted([c for c in tab.columns if not c.is_deleted], key=lambda c: c.position)
    header_rows = _header_rows_for(tab, None)
    row_start = (max(header_rows) + 1) if header_rows else 0
    positions = {id(c): (c.column_position if c.column_position is not None else i) for i, c in enumerate(cols)}

    if header_rows:
        top_row = min(header_rows)
        bottom_row = max(header_rows)
        for col in cols:
            cidx = positions[id(col)] + 1
            header = col.effective_header()
            if col.header_group and len(header_rows) > 1:
                ws.cell(top_row + 1, cidx, col.header_group)
            ws.cell(bottom_row + 1, cidx, header)
        if len(header_rows) > 1:
            _merge_header_groups(ws, cols, positions, top_row + 1)

    for ridx, row in enumerate([r for r in tab.rows if not r.is_deleted], start=row_start + 1):
        data = row.row_data or {}
        formula_data = data.get("_formula_data") if isinstance(data.get("_formula_data"), dict) else {}
        for col in cols:
            value = data.get(col.column_name)
            formula = formula_data.get(col.column_name) if isinstance(formula_data, dict) else None
            if isinstance(formula, dict) and formula.get("formula"):
                value = formula.get("formula")
            if value is None and col.column_name.lower() in {"version", "row version"}:
                value = row.version
            if value is None and col.column_name == getattr(tab, "remarks_column_name", None):
                value = data.get("_unwanted_data")
            _excel_write_value(ws.cell(ridx, positions[id(col)] + 1), value, col.number_format)

    for mr in sorted((m for m in tab.merged_ranges if not m.is_deleted), key=lambda x: (x.start_row, x.start_column)):
        try:
            ws.merge_cells(mr.merged_range)
            if mr.value is not None:
                ws.cell(mr.start_row, mr.start_column).value = mr.value
        except Exception:
            pass
    return ws


def _master_rate_tab_name(tab_name: str) -> str:
    """Match the old Master Rate Data workbook sheet names.

    Some configured commodity master tabs are stored with safe identifier-style names
    such as Norms_Make. The generated/live Master Rate Data workbook must expose the
    old Excel tab names, e.g. Norms Make, so formulas and exports match PCM output.
    """
    name = " ".join(str(tab_name or "").strip().replace("_", " ").replace("-", " ").split())
    norm = name.casefold()
    canonical = {
        "process master": "Process Master",
        "material master": "Material Master",
        "non std bop": "Buy NonStd",
        "std bop": "BOP",
        "master data file": "Master Data file",
        "net consumption base": "Net Consumption base",
        "blank consumption base": "Blank Consumption base",
        "gross consumption base": "Gross Consumption base",
        "consumable": "Consumable",
        "norms make": "Norms",
        "norms make applicability": "Norms",
        "norms buy std": "Norms",
        "norms buy non std": "Norms",
        "norms consign": "Norms",
        "norms pnf": "Norms",
        "norms pn f": "Norms",
        "norms casting": "Norms",
        "norms burning loss": "Norms",
        "norms gross wt": "Norms",
        "norms assy": "Norms",
        "norms special process norms": "Norms",
        "norms shift factor": "Norms",
        "special norms": "Special norms",
        "special rm": "Special RM",
        "scrap stamping": "Scrap Stamping",
        "seat output matrix": "Seat_Output_Matrix",
        "casting complexity master": "Casting Complexity Master",
        "casting recipe master": "Casting Recipie Master",
        "ebom": "eBom",
        "assy": "Assy",
    }
    return canonical.get(norm, name or str(tab_name or "Sheet"))


def _source_tab_groups(db: Session) -> Dict[str, List[tuple]]:
    groups: Dict[str, List[tuple]] = {}
    for wt in _mergeable_source_types(db):
        commodity_label = _source_commodity_label(wt.code)
        for tab in wt.tabs:
            if not tab.is_deleted:
                groups.setdefault(_master_rate_tab_name(tab.tab_name), []).append((wt, tab, commodity_label))
    return groups


def _ordered_source_columns(sources: List[tuple]) -> List[WorkbookTabColumn]:
    columns, seen = [], set()
    for _, tab, _ in sources:
        for col in sorted((c for c in tab.columns if not c.is_deleted), key=lambda c: (c.section_id or 0, c.position)):
            if _is_unnamed_header(col.column_name) or _is_unnamed_header(col.effective_header()):
                continue
            key = _norm_header(col.column_name)
            if key in seen:
                continue
            seen.add(key)
            columns.append(col)
    return sorted(columns, key=lambda c: (c.column_position if c.column_position is not None else c.position, c.position))


def _export_merged_source_tab_to_workbook(wb, tab_name: str, sources: List[tuple]):
    ws = wb.create_sheet(title=tab_name[:31])
    cols = _ordered_source_columns(sources)
    if not cols:
        return ws
    first_source = sources[0][1]
    header_rows = list(getattr(first_source, "header_rows", None) or _header_rows_for(first_source, None))
    row_start = (max(header_rows) + 1) if header_rows else 0
    positions = {id(c): (c.column_position if c.column_position is not None else i) for i, c in enumerate(cols)}

    if header_rows:
        top_row = min(header_rows)
        bottom_row = max(header_rows)
        for col in cols:
            cidx = positions[id(col)] + 1
            if col.header_group and len(header_rows) > 1:
                ws.cell(top_row + 1, cidx, col.header_group)
            ws.cell(bottom_row + 1, cidx, col.effective_header())
        if len(header_rows) > 1:
            _merge_header_groups(ws, cols, positions, top_row + 1)

    excel_row = row_start + 1
    for _, src_tab, _ in sources:
        for row in sorted((r for r in src_tab.rows if not r.is_deleted), key=lambda r: r.id):
            data = row.row_data or {}
            formula_data = data.get("_formula_data") if isinstance(data.get("_formula_data"), dict) else {}
            for col in cols:
                value = data.get(col.column_name)
                formula = formula_data.get(col.column_name) if isinstance(formula_data, dict) else None
                if isinstance(formula, dict) and formula.get("formula"):
                    value = formula.get("formula")
                _excel_write_value(ws.cell(excel_row, positions[id(col)] + 1), value, col.number_format)
            excel_row += 1
    return ws



def _authority_matrix_path() -> Path:
    return Path(__file__).resolve().parents[2].parent / "PCM" / "Authority_Matrix.xlsx"


def _authority_master_headers() -> List[dict]:
    path = _authority_matrix_path()
    if not path.exists():
        return []
    df = pd.read_excel(path, sheet_name="Master_headers", dtype=object)
    result = []
    for _, row in df.iterrows():
        colname = row.get("Colname")
        sheet_name = row.get("SheetName")
        if pd.isna(colname) or pd.isna(sheet_name):
            continue
        position = row.get("Position")
        try:
            position = None if pd.isna(position) else int(position)
        except Exception:
            position = None
        result.append({
            "colname": str(colname).strip(),
            "header": str(row.get("Header") or "0").strip(),
            "sheet_name": str(sheet_name).strip(),
            "position": position,
        })
    return result


def _raw_source_tab_index(db: Session) -> Dict[str, List[tuple]]:
    index: Dict[str, List[tuple]] = {}
    for wt in _mergeable_source_types(db):
        commodity_label = _source_commodity_label(wt.code)
        for tab in wt.tabs:
            if not tab.is_deleted:
                index.setdefault(_norm_header(tab.tab_name), []).append((wt, tab, commodity_label))
    return index


def _tab_export_columns(tab: WorkbookTab) -> List[WorkbookTabColumn]:
    cols = [c for c in tab.columns if not c.is_deleted and not _is_unnamed_header(c.column_name) and not _is_unnamed_header(c.effective_header())]
    return sorted(cols, key=lambda c: (c.column_position if c.column_position is not None else c.position, c.position, c.id))


def _write_sources_at(ws, sources: List[tuple], start_row: int, start_col: int, header_mode: str):
    """Write DB-imported source tabs using the old merge semantics.

    Multiple commodity workbooks can contribute the same logical source tab. The old
    pandas concat used an outer/union column set, so columns that appear only in one
    commodity still exist in the merged Master Rate Data sheet. The previous DB path
    wrote only each tab's local configured columns and only the first tab's header,
    causing narrow sheets such as Buy NonStd 13 cols instead of 27.
    """
    excel_row = start_row + 1
    max_row = excel_row

    union_cols: List[WorkbookTabColumn] = []
    union_keys = set()
    source_cols: Dict[int, List[WorkbookTabColumn]] = {}
    source_key_to_col: Dict[int, Dict[str, WorkbookTabColumn]] = {}
    for _, src_tab, _ in sources:
        cols = _tab_export_columns(src_tab)
        source_cols[src_tab.id] = cols
        key_map = {}
        for col in cols:
            key = _norm_header(col.effective_header() or col.column_name)
            if not key:
                key = _norm_header(col.column_name)
            # Preserve duplicate visible headers where the import config already
            # assigned distinct column names such as Rate/UOM1, Scrap Rate1, etc.
            unique_key = key
            if unique_key in key_map:
                unique_key = _norm_header(col.column_name) or f"{key}_{col.id}"
            key_map[unique_key] = col
            if unique_key not in union_keys:
                union_cols.append(col)
                union_keys.add(unique_key)
        source_key_to_col[src_tab.id] = key_map

    if not union_cols:
        return max_row, start_col

    positions = {idx: start_col + idx for idx in range(len(union_cols))}
    if header_mode != "Single":
        if header_mode == "0,1":
            for idx, col in enumerate(union_cols):
                cidx = positions[idx] + 1
                if col.header_group:
                    ws.cell(1, cidx, col.header_group)
                ws.cell(2, cidx, col.effective_header())
            _merge_header_groups(ws, union_cols, {id(c): positions[i] for i, c in enumerate(union_cols)}, 1)
            excel_row = max(excel_row, 3)
        elif header_mode == "1":
            for idx, col in enumerate(union_cols):
                ws.cell(2, positions[idx] + 1, col.effective_header())
            excel_row = max(excel_row, 3)
        else:
            for idx, col in enumerate(union_cols):
                ws.cell(1, positions[idx] + 1, col.effective_header())
            excel_row = max(excel_row, 2)

    union_keys_order = []
    seen = set()
    for col in union_cols:
        key = _norm_header(col.effective_header() or col.column_name)
        if key in seen:
            key = _norm_header(col.column_name) or f"{key}_{col.id}"
        seen.add(key)
        union_keys_order.append(key)

    for _, src_tab, _ in sources:
        key_map = source_key_to_col.get(src_tab.id, {})
        for row in sorted((r for r in src_tab.rows if not r.is_deleted), key=lambda r: r.id):
            data = row.row_data or {}
            formula_data = data.get("_formula_data") if isinstance(data.get("_formula_data"), dict) else {}
            for idx, key in enumerate(union_keys_order):
                col = key_map.get(key)
                if not col:
                    continue
                value = data.get(col.column_name)
                formula = formula_data.get(col.column_name) if isinstance(formula_data, dict) else None
                if isinstance(formula, dict) and formula.get("formula"):
                    value = formula.get("formula")
                _excel_write_value(ws.cell(excel_row, positions[idx] + 1), value, col.number_format)
            max_row = max(max_row, excel_row)
            excel_row += 1
    return max_row, start_col + len(union_cols)



def _pcm_master_root() -> Path:
    return Path(__file__).resolve().parents[2].parent / "PCM_master"


def _local_master_file(folder_structure: str, colname: str) -> Optional[Path]:
    if not folder_structure or pd.isna(folder_structure):
        return None
    rel = str(folder_structure).replace("D:\\PCM_Sharepoint\\PCM_Master", "").strip("\\/")
    parts = ["EE" if part == "E&E" else part for part in Path(rel).parts]
    path = _pcm_master_root().joinpath(*parts) / f"{colname}.xlsx"
    return path if path.exists() else None



def _dedupe_dataframe_columns(df: pd.DataFrame) -> pd.DataFrame:
    seen = {}
    columns = []
    for col in df.columns:
        name = str(col)
        count = seen.get(name, 0)
        seen[name] = count + 1
        columns.append(name if count == 0 else f"{name}.__dup{count}")
    df = df.copy()
    df.columns = columns
    return df


def _visible_column_name(col) -> str:
    return str(col).split('.__dup', 1)[0]

def _write_dataframe_to_ws(ws, df: pd.DataFrame, start_row: int = 1, start_col: int = 1, header: bool = True):
    row_offset = start_row
    if header:
        for c, col in enumerate(df.columns, start_col):
            visible = _visible_column_name(col)
            ws.cell(row_offset, c, None if visible.startswith("Unnamed") else visible)
        row_offset += 1
    for r_idx, row in enumerate(df.itertuples(index=False), row_offset):
        for c_idx, value in enumerate(row, start_col):
            if pd.isna(value):
                value = None
            ws.cell(r_idx, c_idx, value)


def _read_local_master_df(path: Path, header_mode: str, col_mer: Optional[pd.DataFrame] = None, colname: str = "") -> pd.DataFrame:
    if header_mode == "0":
        return pd.read_excel(path, header=0, dtype=object)
    if header_mode == "1":
        return pd.read_excel(path, header=1, dtype=object)
    if header_mode == "Single":
        return pd.read_excel(path, header=None, dtype=object)
    if header_mode == "0,1":
        top = pd.read_excel(path, header=0, dtype=object)
        df = pd.read_excel(path, header=1, dtype=object)
        merge_tokens = []
        if col_mer is not None and colname in col_mer.columns:
            merge_tokens = [str(v) for v in col_mer[colname].dropna().tolist()]
        new_header = []
        for idx, val1 in enumerate(df.columns):
            val1 = str(val1)
            val2 = str(top.columns[idx]) if idx < len(top.columns) else ""
            matched = next((token for token in merge_tokens if token in val1), None)
            if matched:
                new_header.append(f"{matched}_{val2.split('.')[0]}")
            else:
                new_header.append(val1)
        df.columns = new_header
        return df
    return pd.read_excel(path, header=0, dtype=object)


def _export_master_rate_data_from_local_files(db: Session | None = None) -> Optional[openpyxl.Workbook]:
    authority = _authority_matrix_path()
    root = _pcm_master_root()
    if not authority.exists() or not root.exists():
        return None
    mas_head = pd.read_excel(authority, sheet_name="Master_headers", dtype=object)
    mas_spl = pd.read_excel(authority, sheet_name="Master_Split", dtype=object)
    col_mer = pd.read_excel(authority, sheet_name="Column_Merging", dtype=object)
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    sheets: Dict[str, Any] = {}
    raw_index = _raw_source_tab_index(db) if db is not None else {}
    for _, row in mas_head.iterrows():
        colname = row.get("Colname")
        sheet_name = row.get("SheetName")
        header_mode = str(row.get("Header") or "0").strip()
        if pd.isna(colname) or pd.isna(sheet_name) or colname not in mas_spl.columns:
            continue
        colname = str(colname).strip()
        sheet_name = str(sheet_name).strip()
        try:
            start_col = 1 if pd.isna(row.get("Position")) else int(row.get("Position")) + 1
        except Exception:
            start_col = 1
        ws = sheets.get(sheet_name)
        if ws is None:
            ws = wb.create_sheet(title=sheet_name[:31])
            sheets[sheet_name] = ws
        frames = []
        for _, split_row in mas_spl[mas_spl[colname].notna()].iterrows():
            path = _local_master_file(split_row.get("Folder Structure"), colname)
            if not path:
                continue
            try:
                frames.append(_dedupe_dataframe_columns(_read_local_master_df(path, header_mode, col_mer, colname)))
            except Exception:
                continue
        if frames:
            df = pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]
            if header_mode == "Single":
                _write_dataframe_to_ws(ws, df, start_row=1, start_col=start_col, header=False)
            elif header_mode == "1":
                _write_dataframe_to_ws(ws, df, start_row=2, start_col=start_col, header=True)
            elif header_mode == "0,1":
                _write_dataframe_to_ws(ws, df, start_row=2, start_col=start_col, header=True)
                for c_idx, col in enumerate(df.columns, start_col):
                    col = str(col)
                    if "_" in col:
                        top, bottom = col.split("_", 1)
                        ws.cell(1, c_idx, bottom)
                        ws.cell(2, c_idx, top)
            else:
                _write_dataframe_to_ws(ws, df, start_row=1, start_col=start_col, header=True)
            continue
        # If a local PCM_master source is absent, fall back to the same configured DB tab.
        # This keeps the modern database manageable from UI while still honoring Authority_Matrix layout.
        db_sources = raw_index.get(_norm_header(colname), [])
        if db_sources:
            start_row = 0 if header_mode in {"0", "Single"} else 1
            _write_sources_at(ws, db_sources, start_row, start_col - 1, header_mode)
    return wb



MASTER_RATE_REFERENCE_SHAPES = {
    "Buy NonStd": (5685, 27),
    "BOP": (2061, 14),
    "Assy": (12, 170),
    "Special RM": (205, 19),
    "Special norms": (19, 25),
    "Process Master": (1156, 16),
    "Material Master": (1789, 105),
    "Casting Complexity Master": (28, 26),
    "Casting Recipie Master": (51, 71),
    "eBom": (7699, 43),
    "Scrap Stamping": (74, 14),
    "Net Consumption base": (14, 20),
    "Blank Consumption base": (38, 54),
    "Gross Consumption base": (14, 109),
    "Norms": (359, 124),
    "Consumable": (22, 21),
    "Seat_Output_Matrix": (12, 3),
}


def _master_rate_shape_diagnostics(wb) -> List[dict]:
    diagnostics = []
    for ws in wb.worksheets:
        expected = MASTER_RATE_REFERENCE_SHAPES.get(ws.title)
        if not expected:
            continue
        actual = (ws.max_row, ws.max_column)
        if actual != expected:
            diagnostics.append({
                "sheet": ws.title,
                "actual_rows": actual[0],
                "actual_columns": actual[1],
                "expected_rows": expected[0],
                "expected_columns": expected[1],
                "message": "Imported DB workbook data does not match the old source workbook shape. Re-import this sheet/workbook with the correct header/merge configuration."
            })
    return diagnostics


def _export_master_rate_data_authority_live(db: Session):
    plan = _authority_master_headers()
    if not plan:
        return _export_master_rate_data_live(db)
    raw_index = _raw_source_tab_index(db)
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    sheets: Dict[str, Any] = {}
    raw_index = _raw_source_tab_index(db) if db is not None else {}
    for item in plan:
        sheet_name = item["sheet_name"]
        ws = sheets.get(sheet_name)
        if ws is None:
            ws = wb.create_sheet(title=sheet_name[:31])
            sheets[sheet_name] = ws
        sources = raw_index.get(_norm_header(item["colname"]), [])
        if not sources:
            continue
        start_col = int(item["position"] or 0)
        header = item["header"]
        start_row = 0 if header in {"0", "Single"} else 1
        _write_sources_at(ws, sources, start_row, start_col, header)
    return wb

def _export_master_rate_data_live(db: Session):
    groups = _source_tab_groups(db)
    if not groups:
        raise HTTPException(status_code=400, detail="No commodity workbooks found to export.")
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for tab_name in sorted(groups):
        _export_merged_source_tab_to_workbook(wb, tab_name, groups[tab_name])
    return wb


@router.get("/{type_code}/export")
def export_workbook(type_code: str, db: Session = Depends(get_db), _=Depends(require_permission("masters", "view"))):
    if type_code == MERGE_TARGET_CODE:
        wb = _export_master_rate_data_authority_live(db) or _export_master_rate_data_from_local_files(db)
        buffer = io.BytesIO()
        wb.save(buffer)
        buffer.seek(0)
        return StreamingResponse(buffer, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                 headers={"Content-Disposition": "attachment; filename=Master_Rate_Data.xlsx"})
    wt = _get_workbook_type(db, type_code)
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for tab in wt.tabs:
        if not tab.is_deleted:
            _export_tab_to_workbook(wb, tab)
    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return StreamingResponse(buffer, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             headers={"Content-Disposition": f"attachment; filename={wt.name.replace(' ', '_')}.xlsx"})


@router.get("/{type_code}/tabs/{tab_name}/export")
def export_tab(type_code: str, tab_name: str, db: Session = Depends(get_db),
               _=Depends(require_permission("masters", "view"))):
    wt = _get_workbook_type(db, type_code)
    tab = _get_tab(db, wt, tab_name)
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    _export_tab_to_workbook(wb, tab)
    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return StreamingResponse(buffer, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             headers={"Content-Disposition": f"attachment; filename={tab_name}.xlsx"})


@router.get("/{type_code}/versions", response_model=List[WorkbookVersionOut])
def list_versions(type_code: str, db: Session = Depends(get_db), _=Depends(require_permission("masters", "view"))):
    wt = _get_workbook_type(db, type_code)
    return (db.query(WorkbookImportVersion)
              .filter(WorkbookImportVersion.workbook_type_id == wt.id)
              .order_by(WorkbookImportVersion.version.desc()).all())


@router.post("/{type_code}/rollback/{version}")
def rollback(type_code: str, version: int, db: Session = Depends(get_db),
             user=Depends(require_permission("masters", "update"))):
    wt = _get_workbook_type(db, type_code)
    ver = (db.query(WorkbookImportVersion)
             .filter(WorkbookImportVersion.workbook_type_id == wt.id, WorkbookImportVersion.version == version)
             .first())
    if not ver:
        raise HTTPException(status_code=404, detail="Version not found")
    for tab in wt.tabs:
        if tab.is_deleted or tab.tab_name not in ver.snapshot:
            continue
        db.query(WorkbookRow).filter(WorkbookRow.tab_id == tab.id).update({"is_deleted": True})
        tab_snapshot = ver.snapshot[tab.tab_name]
        # Snapshots taken before the section-aware format was introduced are a flat
        # list of row dicts; newer ones are {section_name_or_"_default": [rows...]}.
        if isinstance(tab_snapshot, list):
            for item in tab_snapshot:
                if isinstance(item, dict) and "row_data" in item:
                    row_data, row_version = item.get("row_data", {}), int(item.get("version", 1) or 1)
                else:  # legacy snapshots
                    row_data, row_version = item, 1
                row = WorkbookRow(tab_id=tab.id, row_data=row_data, version=row_version)
                stamp_create(row, user.id)
                db.add(row)
        else:
            section_by_name = {s.section_name: s for s in tab.sections if not s.is_deleted}
            for key, rows in tab_snapshot.items():
                section = section_by_name.get(key)
                for item in rows:
                    if isinstance(item, dict) and "row_data" in item:
                        row_data, row_version = item.get("row_data", {}), int(item.get("version", 1) or 1)
                    else:
                        row_data, row_version = item, 1
                    row = WorkbookRow(tab_id=tab.id, section_id=section.id if section else None,
                                       row_data=row_data, version=row_version)
                    stamp_create(row, user.id)
                    db.add(row)
    db.commit()
    return {"message": f"Rolled back '{type_code}' to version {version}"}


# ---------------------------------------------------------------------------
# Admin CRUD — dynamic workbook/tab/section/column management, gated behind the
# 'workbook_config' permission (separate from 'masters', which is about row data).
# Super Admin/Admin get this by default (see app/seed.py).
# ---------------------------------------------------------------------------
@router.post("", response_model=WorkbookTypeOut)
def create_workbook_type(payload: WorkbookTypeIn, db: Session = Depends(get_db),
                          user=Depends(require_permission("workbook_config", "create"))):
    if db.query(WorkbookType).filter(WorkbookType.code == payload.code).first():
        raise HTTPException(status_code=400, detail=f"Workbook type code '{payload.code}' already exists")
    wt = WorkbookType(**payload.model_dump())
    stamp_create(wt, user.id)
    db.add(wt)
    db.commit()
    db.refresh(wt)
    return wt


@router.put("/{type_code}", response_model=WorkbookTypeOut)
def update_workbook_type(type_code: str, payload: WorkbookTypeUpdate, db: Session = Depends(get_db),
                          user=Depends(require_permission("workbook_config", "update"))):
    wt = _get_workbook_type(db, type_code)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(wt, field, value)
    stamp_update(wt, user.id)
    db.commit()
    db.refresh(wt)
    return wt


@router.delete("")
def delete_all_workbook_types(db: Session = Depends(get_db),
                              user=Depends(require_permission("workbook_config", "delete"))):
    items = db.query(WorkbookType).filter(WorkbookType.is_deleted.is_(False)).all()
    for wt in items:
        wt.is_deleted = True
        stamp_update(wt, user.id)
    db.commit()
    return {"message": "Workbook types deleted", "workbook_types_deleted": len(items)}


@router.delete("/{type_code}")
def delete_workbook_type(type_code: str, db: Session = Depends(get_db),
                          user=Depends(require_permission("workbook_config", "delete"))):
    wt = _get_workbook_type(db, type_code)
    wt.is_deleted = True
    stamp_update(wt, user.id)
    db.commit()
    return {"message": "Workbook type deleted", "code": type_code}


@router.post("/{type_code}/tabs/import-new", response_model=ImportResultTab)
def import_new_tab(type_code: str, tab_name: str = Query(...), file: UploadFile = File(...),
                   db: Session = Depends(get_db),
                   user=Depends(require_permission("workbook_config", "create"))):
    """Create a tab, infer its columns, and import its first worksheet atomically."""
    wt = _get_workbook_type(db, type_code)
    if any(t.tab_name == tab_name and not t.is_deleted for t in wt.tabs):
        raise HTTPException(status_code=400, detail=f"Tab '{tab_name}' already exists on this workbook")
    content = file.file.read()
    try:
        frame = pd.read_excel(io.BytesIO(content), sheet_name=0, dtype=object)
        frame = frame.dropna(axis=1, how="all")
        frame.columns = [str(value).strip() for value in frame.columns]
        frame = frame.where(pd.notnull(frame), None)
        if not len(frame.columns) or any(_is_unnamed_header(name) for name in frame.columns):
            raise ValueError("The first worksheet must have named columns")
        tab = WorkbookTab(workbook_type_id=wt.id, tab_name=tab_name,
                          header_row_index=0, header_type="type0")
        stamp_create(tab, user.id)
        db.add(tab)
        db.flush()
        for position, name in enumerate(frame.columns):
            col = WorkbookTabColumn(tab_id=tab.id, column_name=name, header_name=name,
                                    column_position=position, position=position,
                                    data_type=_infer_data_type(frame[name]))
            stamp_create(col, user.id)
            db.add(col)
        db.flush()
        status, count, errors, _ = _import_tab_dataframe(db, tab, None, frame, user)
        if status == "Invalid":
            raise ValueError("; ".join(errors))
        db.commit()
        return ImportResultTab(tab_name=tab_name, status=status, rows_imported=count, errors=errors)
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"Could not import new tab: {exc}") from exc


@router.post("/{type_code}/tabs", response_model=WorkbookTabOut)
def create_tab(type_code: str, payload: WorkbookTabIn, db: Session = Depends(get_db),
               user=Depends(require_permission("workbook_config", "create"))):
    wt = _get_workbook_type(db, type_code)
    if any(t.tab_name == payload.tab_name and not t.is_deleted for t in wt.tabs):
        raise HTTPException(status_code=400, detail=f"Tab '{payload.tab_name}' already exists on this workbook")
    column_specs = payload.columns
    data = payload.model_dump(exclude={"structure", "columns"})
    data["header_type"] = _derive_header_type(payload.structure, payload.header_type, payload.header_row_index)
    if data.get("header_row_index", 0) < 0:
        raise HTTPException(status_code=400, detail="Header Row must be 1 or greater")
    tab = WorkbookTab(workbook_type_id=wt.id, **data)
    stamp_create(tab, user.id)
    db.add(tab)
    db.flush()
    for position, spec in enumerate(column_specs):
        values = spec.model_dump()
        if values.get("position") is None:
            values["position"] = position
        col = WorkbookTabColumn(tab_id=tab.id, **values)
        stamp_create(col, user.id)
        db.add(col)
    db.commit()
    db.refresh(tab)
    return tab


@router.put("/{type_code}/tabs/{tab_name}", response_model=WorkbookTabOut)
def update_tab(type_code: str, tab_name: str, payload: WorkbookTabUpdate, db: Session = Depends(get_db),
               user=Depends(require_permission("workbook_config", "update"))):
    """Renaming a tab (tab_name) is safe: rows/sections/columns reference the tab by
    tab_id (a stable foreign key), never by tab_name, so a rename never orphans them —
    this is the structural fix for stale-tab-reference bugs at the data-model level."""
    wt = _get_workbook_type(db, type_code)
    tab = _get_tab(db, wt, tab_name)
    data = payload.model_dump(exclude_unset=True, exclude={"structure"})
    if "tab_name" in data and data["tab_name"] != tab.tab_name:
        if any(t.tab_name == data["tab_name"] and not t.is_deleted and t.id != tab.id for t in wt.tabs):
            raise HTTPException(status_code=400, detail=f"Tab '{data['tab_name']}' already exists on this workbook")

    if payload.structure is not None:
        header_row_index = data.get("header_row_index", tab.header_row_index)
        data["header_type"] = _derive_header_type(payload.structure, None, header_row_index)
    elif "header_type" in data:
        if data["header_type"] not in HEADER_TYPES:
            raise HTTPException(status_code=400, detail=f"header_type must be one of {HEADER_TYPES}")

    if data.get("header_row_index", 0) < 0:
        raise HTTPException(status_code=400, detail="Header Row must be 1 or greater")

    for field, value in data.items():
        setattr(tab, field, value)
    stamp_update(tab, user.id)
    db.commit()
    db.refresh(tab)
    return tab


@router.delete("/{type_code}/tabs")
def delete_all_tabs(type_code: str, db: Session = Depends(get_db),
                    user=Depends(require_permission("workbook_config", "delete"))):
    wt = _get_workbook_type(db, type_code)
    tabs = [t for t in wt.tabs if not t.is_deleted]
    for tab in tabs:
        tab.is_deleted = True
        stamp_update(tab, user.id)
    db.commit()
    return {"message": "Tabs deleted", "tabs_deleted": len(tabs), "code": type_code}


@router.delete("/{type_code}/tabs/{tab_name}")
def delete_tab(type_code: str, tab_name: str, db: Session = Depends(get_db),
               user=Depends(require_permission("workbook_config", "delete"))):
    """Soft-deletes the tab. Its sections/columns/rows stay attached (via tab_id) but
    are excluded from every read path because _get_tab / the schema/list endpoints all
    filter is_deleted — so a deleted tab can never again be "found" by name, and no
    request can be routed to it (the class of bug behind 'Unknown tab ... in ...')."""
    wt = _get_workbook_type(db, type_code)
    tab = _get_tab(db, wt, tab_name)
    tab.is_deleted = True
    stamp_update(tab, user.id)
    db.commit()
    return {"message": "Tab deleted", "tab_name": tab_name}


@router.post("/{type_code}/tabs/{tab_name}/sections", response_model=WorkbookSectionOut)
def create_section(type_code: str, tab_name: str, payload: WorkbookSectionIn, db: Session = Depends(get_db),
                    user=Depends(require_permission("workbook_config", "create"))):
    wt = _get_workbook_type(db, type_code)
    tab = _get_tab(db, wt, tab_name)
    section = WorkbookSection(tab_id=tab.id, **payload.model_dump())
    stamp_create(section, user.id)
    db.add(section)
    tab.has_multiple_sections = True
    stamp_update(tab, user.id)
    db.commit()
    db.refresh(section)
    return section


@router.put("/{type_code}/tabs/{tab_name}/sections/{section_id}", response_model=WorkbookSectionOut)
def update_section(type_code: str, tab_name: str, section_id: int, payload: WorkbookSectionUpdate,
                    db: Session = Depends(get_db), user=Depends(require_permission("workbook_config", "update"))):
    wt = _get_workbook_type(db, type_code)
    tab = _get_tab(db, wt, tab_name)
    section = _get_section(db, tab, section_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(section, field, value)
    stamp_update(section, user.id)
    db.commit()
    db.refresh(section)
    return section


@router.delete("/{type_code}/tabs/{tab_name}/sections")
def delete_all_sections(type_code: str, tab_name: str, db: Session = Depends(get_db),
                        user=Depends(require_permission("workbook_config", "delete"))):
    wt = _get_workbook_type(db, type_code)
    tab = _get_tab(db, wt, tab_name)
    sections = [s for s in tab.sections if not s.is_deleted]
    for section in sections:
        section.is_deleted = True
        stamp_update(section, user.id)
    tab.has_multiple_sections = False
    stamp_update(tab, user.id)
    db.commit()
    return {"message": "Sections deleted", "sections_deleted": len(sections), "tab_name": tab_name}


@router.delete("/{type_code}/tabs/{tab_name}/sections/{section_id}")
def delete_section(type_code: str, tab_name: str, section_id: int, db: Session = Depends(get_db),
                    user=Depends(require_permission("workbook_config", "delete"))):
    wt = _get_workbook_type(db, type_code)
    tab = _get_tab(db, wt, tab_name)
    section = _get_section(db, tab, section_id)
    section.is_deleted = True
    stamp_update(section, user.id)
    db.commit()
    return {"message": "Section deleted", "section_id": section_id}


@router.post("/{type_code}/tabs/{tab_name}/columns", response_model=WorkbookTabColumnOut)
def create_column(type_code: str, tab_name: str, payload: WorkbookTabColumnIn, db: Session = Depends(get_db),
                   user=Depends(require_permission("workbook_config", "create"))):
    wt = _get_workbook_type(db, type_code)
    tab = _get_tab(db, wt, tab_name)
    if payload.section_id is not None:
        _get_section(db, tab, payload.section_id)
    elif tab.has_multiple_sections:
        raise HTTPException(status_code=400,
                             detail="This tab has multiple sections — section_id is required for new columns")
    if _is_unnamed_header(payload.column_name) or _is_unnamed_header(payload.header_name or payload.column_name):
        raise HTTPException(status_code=400, detail="Blank or Unnamed columns are skipped and cannot be configured")
    if _column_identity_taken(tab, payload.section_id, payload.column_name, payload.header_name):
        raise HTTPException(status_code=400, detail="A column with this name or header is already configured")
    col = WorkbookTabColumn(tab_id=tab.id, **payload.model_dump())
    stamp_create(col, user.id)
    db.add(col)
    db.commit()
    db.refresh(col)
    return col

@router.post("/{type_code}/tabs/{tab_name}/columns/detect", response_model=ColumnDetectionResult)
def detect_columns(type_code: str, tab_name: str, file: UploadFile = File(...),
                    header_row: Optional[int] = Query(
                        None, description="1-based; the header row (or, if second_header_row is set, the GROUP-label row)"),
                    second_header_row: Optional[int] = Query(
                        None, description="1-based; if set, treats the sheet as a 2-row merged header — "
                                           "header_row is the group-label row, this is the actual column-name row — "
                                           "and auto-fills each detected column's header_group from the group row."),
                    section_id: Optional[int] = None,
                    db: Session = Depends(get_db), user=Depends(require_permission("workbook_config", "create"))):
    """Reads the uploaded file's header (one row, or two for a merged header) plus a
    sample of the data and returns a suggested column definition per header — name,
    an auto-detected data_type, and (for a 2-row header) the group label — meant to
    pre-fill the Bulk Add Columns grid. Nothing is written; the admin reviews first."""
    wt = _get_workbook_type(db, type_code)
    tab = _get_tab(db, wt, tab_name)
    section = _get_section(db, tab, section_id) if section_id is not None else None
    existing_names = {c.column_name for c in _tab_columns_for_section(tab, section)}
    content = file.file.read()
    total_cols = _sheet_max_column(content, 0) if section is not None else None
    usecols = _section_usecols(section, total_cols) if section is not None else None
    default_header_row_0 = section.header_row if section is not None else tab.header_row_index

    auto_detected = False
    if second_header_row is None:
        candidate_row = (header_row - 1) if header_row is not None else default_header_row_0
        _, matrix = _sheet_matrix(content, 0, data_only=True)
        if _dense_stacked_header(matrix, candidate_row - 1):
            candidate_row -= 1
        try:
            probe = pd.read_excel(io.BytesIO(content), sheet_name=0, header=None,
                       skiprows=candidate_row, nrows=2, usecols=usecols)
            if probe.shape[0] == 2:
                notna = probe.notna().values
                if (_looks_like_stacked_header(notna, 0, 1, 0, probe.shape[1], 2)
                        or _dense_stacked_header(matrix, candidate_row)):
                    second_header_row = candidate_row + 2  # 1-based name row
                    header_row = candidate_row + 1          # 1-based group row
                    auto_detected = True
        except Exception:
            pass

    if second_header_row is not None:
        group_row_0 = (header_row - 1) if header_row is not None else default_header_row_0
        name_row_0 = second_header_row - 1

        row1_cells, row2_cells = _read_merged_header_rows(content, 0, [group_row_0, name_row_0])
        if usecols is not None:
            row1_cells = [row1_cells[i] for i in usecols if i < len(row1_cells)]
            row2_cells = [row2_cells[i] for i in usecols if i < len(row2_cells)]
        combined = _combine_header_cells(row1_cells, row2_cells)

        try:
            sample_df = pd.read_excel(io.BytesIO(content), sheet_name=0, header=None,
                                    skiprows=max(group_row_0, name_row_0) + 1, nrows=50, usecols=usecols)
        except Exception:
            sample_df = None

        suggestions = []
        detected_names = set()
        for i in range(len(combined)):
            actual_col = usecols[i] if usecols is not None else i
            top = row1_cells[i].strip() if i < len(row1_cells) else ""
            bottom = row2_cells[i].strip() if i < len(row2_cells) else ""
            if _is_unnamed_header(combined[i]) or (not combined[i] and (
                    sample_df is None or i >= sample_df.shape[1] or sample_df.iloc[:, i].isna().all())):
                continue
            col_name = combined[i] or openpyxl.utils.get_column_letter(actual_col + 1)
            if col_name in detected_names:
                col_name = f"{col_name} ({openpyxl.utils.get_column_letter(actual_col + 1)})"
            detected_names.add(col_name)
            if col_name in existing_names or _column_identity_taken(tab, section.id if section else None, col_name, combined[i]):
                continue
            dtype = "string"
            if sample_df is not None and i < sample_df.shape[1]:
                dtype = _infer_data_type(sample_df.iloc[:, i])
            suggestions.append(DetectedColumn(
                column_name=col_name,
                header_name=combined[i] if combined[i] else f"Unnamed: {actual_col}",
                header_group=(top if (top and bottom) else None),
                data_type=dtype, is_required=False, is_unique_key=False,
                position=actual_col, column_position=actual_col,
                excel_column=openpyxl.utils.get_column_letter(actual_col + 1),
                header_row=name_row_0,
            ))
        return ColumnDetectionResult(columns=suggestions, auto_detected_two_row_header=auto_detected)

    effective_header_row = (header_row - 1) if header_row is not None else default_header_row_0
    try:
        df = pd.read_excel(io.BytesIO(content), sheet_name=0, header=effective_header_row, usecols=usecols)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not read Excel file: {exc}")

    suggestions = []
    for i, col in enumerate(df.columns):
        actual_col = usecols[i] if usecols is not None else i
        raw_header = str(col).strip()
        col_name = raw_header
        if _is_unnamed_header(col_name):
            continue
        if col_name in existing_names or _column_identity_taken(tab, section.id if section else None, col_name, raw_header):
            continue
        suggestions.append(DetectedColumn(
            column_name=col_name, header_name=raw_header, header_group=None,
            data_type=_infer_data_type(df[col]), is_required=False, is_unique_key=False,
            position=i, column_position=actual_col, excel_column=openpyxl.utils.get_column_letter(actual_col + 1),
            header_row=effective_header_row,
        ))
    return ColumnDetectionResult(columns=suggestions)

@router.post("/{type_code}/tabs/{tab_name}/columns/bulk", response_model=List[WorkbookTabColumnOut])
def bulk_create_columns(type_code: str, tab_name: str, payload: WorkbookTabColumnBulkIn,
                         db: Session = Depends(get_db), user=Depends(require_permission("workbook_config", "create"))):
    """Creates every column in `payload.columns` in one request/transaction — lets an
    admin fill in a 10-20+ column sheet at once instead of repeating the single-column
    form that many times. Every column is validated (section exists, section_id
    required when the tab has sections) before any is written."""
    wt = _get_workbook_type(db, type_code)
    tab = _get_tab(db, wt, tab_name)
    if not payload.columns:
        raise HTTPException(status_code=400, detail="No columns supplied")

    accepted = []
    seen = set()
    for col_in in payload.columns:
        if not col_in.column_name.strip():
            continue
        if col_in.section_id is not None:
            _get_section(db, tab, col_in.section_id)
        elif tab.has_multiple_sections:
            raise HTTPException(status_code=400,
                                 detail=f"Column '{col_in.column_name}' needs a section — this tab has multiple sections")

        identity = (col_in.section_id, _norm_header(col_in.column_name), _norm_header(col_in.header_name or col_in.column_name))
        if identity in seen or _should_skip_config_column(tab, col_in.section_id, col_in.column_name, col_in.header_name):
            continue
        seen.add(identity)
        accepted.append(col_in)
    if not accepted:
        return []

    created = []
    for col_in in accepted:
        col = WorkbookTabColumn(tab_id=tab.id, **col_in.model_dump())
        stamp_create(col, user.id)
        db.add(col)
        created.append(col)
    db.commit()
    for col in created:
        db.refresh(col)
    return created


@router.put("/{type_code}/tabs/{tab_name}/columns/{column_id}", response_model=WorkbookTabColumnOut)
def update_column(type_code: str, tab_name: str, column_id: int, payload: WorkbookTabColumnUpdate,
                   db: Session = Depends(get_db), user=Depends(require_permission("workbook_config", "update"))):
    wt = _get_workbook_type(db, type_code)
    tab = _get_tab(db, wt, tab_name)
    col = db.query(WorkbookTabColumn).filter(WorkbookTabColumn.id == column_id, WorkbookTabColumn.tab_id == tab.id,
                                              WorkbookTabColumn.is_deleted.is_(False)).first()
    if not col:
        raise HTTPException(status_code=404, detail="Column not found")
    data = payload.model_dump(exclude_unset=True)
    if "section_id" in data and data["section_id"] is not None:
        _get_section(db, tab, data["section_id"])
    next_section_id = data.get("section_id", col.section_id)
    next_name = data.get("column_name", col.column_name)
    next_header = data.get("header_name", col.header_name)
    if _is_unnamed_header(next_name) or _is_unnamed_header(next_header or next_name):
        raise HTTPException(status_code=400, detail="Blank or Unnamed columns are skipped and cannot be configured")
    if _column_identity_taken(tab, next_section_id, next_name, next_header, exclude_id=col.id):
        raise HTTPException(status_code=409, detail="A column with this name or header is already configured")
    for field, value in data.items():
        setattr(col, field, value)
    stamp_update(col, user.id)
    db.commit()
    db.refresh(col)
    return col


@router.delete("/{type_code}/tabs/{tab_name}/columns")
def delete_all_columns(type_code: str, tab_name: str, section_id: Optional[int] = None,
                       db: Session = Depends(get_db),
                       user=Depends(require_permission("workbook_config", "delete"))):
    wt = _get_workbook_type(db, type_code)
    tab = _get_tab(db, wt, tab_name)
    query = db.query(WorkbookTabColumn).filter(WorkbookTabColumn.tab_id == tab.id,
                                               WorkbookTabColumn.is_deleted.is_(False))
    if section_id is not None:
        query = query.filter(WorkbookTabColumn.section_id == section_id)
    columns = query.all()
    for col in columns:
        col.is_deleted = True
        stamp_update(col, user.id)
    db.commit()
    return {"message": "Columns deleted", "columns_deleted": len(columns), "tab_name": tab_name}


@router.delete("/{type_code}/tabs/{tab_name}/columns/{column_id}")
def delete_column(type_code: str, tab_name: str, column_id: int, db: Session = Depends(get_db),
                   user=Depends(require_permission("workbook_config", "delete"))):
    wt = _get_workbook_type(db, type_code)
    tab = _get_tab(db, wt, tab_name)
    col = db.query(WorkbookTabColumn).filter(WorkbookTabColumn.id == column_id, WorkbookTabColumn.tab_id == tab.id,
                                              WorkbookTabColumn.is_deleted.is_(False)).first()
    if not col:
        raise HTTPException(status_code=404, detail="Column not found")
    col.is_deleted = True
    stamp_update(col, user.id)
    db.commit()
    return {"message": "Column deleted", "column_id": column_id}

# ---------------------------------------------------------------------------
# Master Rate Data — merges every commodity's identically-named master files
# (Process Master, Material Master, Non Std BOP, Std BOP — the WorkbookTypes seeded
# per (commodity, category) as f"{commodity_code}_{category}") into one combined
# dataset, mirroring the legacy Authority_Matrix-driven RPA script's per-file
# cross-commodity concatenation. Unlike that script, this is driven entirely by the
# app's own dynamic WorkbookType/WorkbookTab/WorkbookRow data — no external
# spreadsheet, no hardcoded commodity list — so a newly added commodity or column is
# picked up automatically the next time "Start Merge" runs.
# ---------------------------------------------------------------------------
MERGE_TARGET_CODE = "master_rate_data"
MERGE_TARGET_NAME = "Master Rate Data"


def _mergeable_source_types(db: Session) -> List[WorkbookType]:
    """All commodity-owned workbooks are eligible sources. Load related tabs,
    columns, rows, and merged ranges up front to avoid thousands of lazy queries
    during live Master Rate Data export/merge."""
    return (db.query(WorkbookType)
              .options(
                  selectinload(WorkbookType.tabs).selectinload(WorkbookTab.columns),
                  selectinload(WorkbookType.tabs).selectinload(WorkbookTab.rows),
                  selectinload(WorkbookType.tabs).selectinload(WorkbookTab.merged_ranges),
              )
              .filter(WorkbookType.is_deleted.is_(False),
                      WorkbookType.code != MERGE_TARGET_CODE,
                      ((WorkbookType.code == "pcm_master") |
                       (WorkbookType.code.endswith("_self")) |
                       (WorkbookType.code.endswith("_master_user")) |
                       (WorkbookType.code.endswith("_pce"))))
              .all())


def _source_commodity_label(code: str) -> str:
    for suffix in ("_master_user", "_self", "_pce"):
        if code.endswith(suffix):
            return code[:-len(suffix)]
    return code


def _get_or_create_merge_target(db: Session, user_id: int) -> WorkbookType:
    wt = db.query(WorkbookType).filter(WorkbookType.code == MERGE_TARGET_CODE).first()
    if wt:
        if wt.is_deleted:
            wt.is_deleted = False
            stamp_update(wt, user_id)
            db.commit()
        return wt
    wt = WorkbookType(code=MERGE_TARGET_CODE, name=MERGE_TARGET_NAME,
                       description="Auto-generated: every commodity's Process Master, Material Master, "
                                   "Non Std BOP and Std BOP combined into one company-wide master, refreshed "
                                   "each time Start Merge is run.")
    stamp_create(wt, user_id)
    db.add(wt)
    db.commit()
    db.refresh(wt)
    return wt


def _get_or_create_merge_tab(db: Session, target: WorkbookType, tab_name: str,
                              source_columns: List[WorkbookTabColumn], user_id: int) -> WorkbookTab:
    tab = next((t for t in target.tabs if t.tab_name == tab_name and not t.is_deleted), None)
    if not tab:
        tab = WorkbookTab(workbook_type_id=target.id, tab_name=tab_name,
                          header_row_index=0, header_type="type0", read_only=True)
        stamp_create(tab, user_id)
        db.add(tab)
        db.commit()
        db.refresh(tab)

    existing = {c.column_name: c for c in tab.columns if not c.is_deleted}
    for src_col in source_columns:
        if src_col.column_name in existing:
            continue
        col = WorkbookTabColumn(
            tab_id=tab.id, column_name=src_col.column_name, header_name=src_col.header_name,
            header_group=src_col.header_group, header_row=src_col.header_row,
            column_position=src_col.column_position, data_type=src_col.data_type,
            is_unique_key=src_col.is_unique_key, is_required=False, position=len(existing),
            validation_rules=src_col.validation_rules, number_format=src_col.number_format,
            is_percentage=src_col.is_percentage, formula_config=src_col.formula_config,
        )
        stamp_create(col, user_id)
        db.add(col)
        existing[src_col.column_name] = col
    db.commit()
    db.refresh(tab)
    return tab


@router.post("/merge/run")
def run_merge(db: Session = Depends(get_db), user=Depends(require_permission("merge", "create"))):
    """Prepare Master Rate Data using the same legacy-compatible live export plan.

    The UI action should mean: verify/build the workbook that Export All will
    download. We still avoid storing duplicate merged row data in DB, but we create
    the target workbook metadata and version from the actual generated workbook
    sheets so the screen no longer reports an empty/no-op merge.
    """
    wb = _export_master_rate_data_authority_live(db) or _export_master_rate_data_from_local_files(db)
    if not wb or not wb.sheetnames:
        raise HTTPException(status_code=400, detail="No Master Rate Data sources found. Import master/commodity files first.")

    target = _get_or_create_merge_target(db, user.id)
    diagnostics = _master_rate_shape_diagnostics(wb)
    sheet_results = []
    for ws in wb.worksheets:
        tab = next((t for t in target.tabs if t.tab_name == ws.title and not t.is_deleted), None)
        if not tab:
            tab = WorkbookTab(workbook_type_id=target.id, tab_name=ws.title,
                              header_row_index=0, header_type="type0", read_only=True)
            stamp_create(tab, user.id)
            db.add(tab)
            db.flush()
        tab.read_only = True
        tab.header_row_index = 0
        tab.header_type = "type0"
        tab.header_rows = [0]
        stamp_update(tab, user.id)

        existing = {c.position: c for c in tab.columns if not c.is_deleted}
        max_col = ws.max_column or 1
        for idx in range(1, max_col + 1):
            raw_name = ws.cell(1, idx).value
            col_name = _visible_column_name(raw_name) or f"Column {idx}"
            if _is_unnamed_header(col_name):
                col_name = f"Column {idx}"
            col = existing.get(idx - 1)
            if not col:
                col = WorkbookTabColumn(tab_id=tab.id, column_name=col_name, header_name=col_name,
                                        position=idx - 1, column_position=idx - 1, data_type="text")
                stamp_create(col, user.id)
                db.add(col)
            else:
                col.column_name = col_name
                col.header_name = col_name
                stamp_update(col, user.id)

        sheet_results.append({"sheet": ws.title, "rows": max(0, (ws.max_row or 1) - 1),
                              "columns": max_col, "commodities_merged": None})

    version_num = (db.query(WorkbookImportVersion)
                     .filter(WorkbookImportVersion.workbook_type_id == target.id).count()) + 1
    snapshot = {r["sheet"]: {"rows": r["rows"], "columns": r["columns"]} for r in sheet_results}
    output_dir = Path(settings.UPLOAD_DIR) / "outputs"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "Master_Rate_Data.xlsx"
    wb.save(output_path)

    version = WorkbookImportVersion(workbook_type_id=target.id, version=version_num, snapshot=snapshot)
    stamp_create(version, user.id)
    db.add(version)
    db.commit()
    return JSONResponse(content=_sanitize_for_json({"workbook_type": target.code,
                                                      "version": int(version_num),
                                                      "sheets": sheet_results,
                                                      "live": True,
                                                      "source": "database_workbook_rows",
                                                      "export_filename": "Master_Rate_Data.xlsx",
                                                      "output_path": str(output_path),
                                                      "diagnostics": diagnostics}))


def _col_bands(notna: "np.ndarray", r0: int, r1: int, c0: int, c1: int) -> List[tuple]:
    """0-based [start, end) column ranges within notna[r0:r1, c0:c1] that contain any
    data, split wherever a column is fully blank across that row range. A single fully
    blank column is enough to split — see _xy_cut_sections for why min_gap=1 (not 2)
    is the right default here."""
    col_has = notna[r0:r1, c0:c1].any(axis=0)
    bands, start = [], None
    for i, has in enumerate(col_has):
        if has:
            if start is None:
                start = i
        elif start is not None:
            bands.append((c0 + start, c0 + i))
            start = None
    if start is not None:
        bands.append((c0 + start, c1))
    return bands


def _row_blocks(notna: "np.ndarray", r0: int, r1: int, c0: int, c1: int) -> List[tuple]:
    """0-based [start, end) row ranges within notna[r0:r1, c0:c1] that contain any
    data, split wherever a row is fully blank across that column range."""
    row_has = notna[r0:r1, c0:c1].any(axis=1)
    blocks, start = [], None
    for i, has in enumerate(row_has):
        if has:
            if start is None:
                start = i
        elif start is not None:
            blocks.append((r0 + start, r0 + i))
            start = None
    if start is not None:
        blocks.append((r0 + start, r1))
    return blocks


def _xy_cut_sections(notna: "np.ndarray", r0: int, r1: int, c0: int, c1: int, out: List[tuple]):
    """Recursive rectangle decomposition (an "XY-cut", the standard technique for
    segmenting a page/sheet into its independent blocks): repeatedly try to split the
    current rectangle by fully-blank columns, then by fully-blank rows, and recurse
    into whatever sub-rectangles result. A rectangle that can no longer be split
    either way is one leaf table.

    This replaces an earlier version that only band-split columns ONCE for the whole
    sheet and then row-split within each band — which fails as soon as one wide
    column-band actually contains several unrelated narrower tables at different row
    ranges (verified against Casting Complexity Master.xlsx: columns B:K there hold
    four independent small lookup tables — "Rejection Category", "Core", "Weight",
    "Shape (Compactness)" — plus a fifth, "ARPA/HPML" complexity-factor table, that a
    single-pass split merged into one or two oversized, wrong sections). Recursing
    re-evaluates blank columns/rows *within* each candidate rectangle instead of once
    globally, so a table's true extent no longer depends on what else shares its rows
    or columns elsewhere in the sheet.

    A single fully-blank row or column is treated as a split point (not 2+, as the
    single-pass version used for columns) — deliberately erring toward *more*
    candidate sections rather than fewer: under-splitting silently merges two
    unrelated tables into one (wrong columns, wrong data), which is worse than
    over-splitting a table that uses an intentional blank spacer column, since this
    is only ever a proposal the admin reviews before any section is created."""
    if r0 >= r1 or c0 >= c1:
        return
    cb = _col_bands(notna, r0, r1, c0, c1)
    if len(cb) != 1 or cb[0] != (c0, c1):
        for cs, ce in cb:
            _xy_cut_sections(notna, r0, r1, cs, ce, out)
        return
    rb = _row_blocks(notna, r0, r1, c0, c1)
    if len(rb) != 1 or rb[0] != (r0, r1):
        for rs, re_ in rb:
            _xy_cut_sections(notna, rs, re_, c0, c1, out)
        return
    out.append((r0, r1, c0, c1))


def _looks_like_stacked_header(notna: "np.ndarray", header_row: int, next_row: int,
                                col_start: int, col_end: int, row_end: int) -> bool:
    """True when the row right below `header_row` is very likely a second header row
    rather than the first data row — even though NO Excel cell is actually merged.

    This matters because a real merged cell is not the only way spreadsheets express
    a "group label over several sub-columns": it's just as common to see one plain
    text label sitting in a single cell above several individually-labelled columns,
    with no merge at all (confirmed directly against Master Rate Data.xlsx — the
    consolidated file every commodity master ultimately gets imported from: pandas/
    xlsxwriter does not preserve merged cells when re-writing a sheet, so every
    "Process Master"/"Material Master"/"eBom"-style two-row header in that file has
    zero real merges left, even though the header is still visibly two rows). Relying
    on merged cells alone means these headers are silently read as single-row,
    picking up only one of the two rows and — just as importantly — starting the data
    range one row too early, since the real second header row gets read as data.

    The tell is structural rather than about merges specifically: a genuine group-
    label row is usually only partially populated (it only labels some columns, or
    repeats sparse labels), while the real, complete header sits immediately below it
    and is populated across nearly the whole width. A normal single header row does
    not have this shape — it's already fully populated on its own row — so this
    check only fires when there's an actual step up in density from one row to the
    next, and never fires on the section's last row (nothing to compare against)."""
    if next_row >= row_end:
        return False
    width = col_end - col_start
    if width <= 0:
        return False
    header_ratio = notna[header_row, col_start:col_end].sum() / width
    next_ratio = notna[next_row, col_start:col_end].sum() / width
    # header_ratio must be > 0, not just < 0.7: a *completely* blank row means there is
    # no group label at all — it's just that the real single header starts one row
    # down (e.g. a tab written starting at row 2 with row 1 left empty), which is a
    # header_row_index/_suggest_header_row problem, not a 2-row-header one. Only a
    # row with *some* real content but not full coverage looks like a genuine partial
    # group label sitting above a complete header row.
    return 0 < header_ratio < 0.7 and next_ratio >= 0.85 and (next_ratio - header_ratio) >= 0.2


def _detect_sections(content: bytes, sheet_name=0, max_scan: int = 500) -> List[dict]:
    """Structural detection of every independent table on a worksheet, stacked
    vertically and/or placed side by side, via the recursive XY-cut in
    _xy_cut_sections. For each resulting rectangle, the first row is the header
    unless a merged cell spanning more than one column starts on that row within the
    rectangle — in that case the header is treated as two rows (group label +
    sub-label), combined the same way tab.header_type=="typeMerge" is. Still a
    heuristic — the result is a proposal for review, not an automatic import."""
    try:
        raw = pd.read_excel(io.BytesIO(content), sheet_name=sheet_name, header=None, nrows=max_scan)
    except Exception:
        return []
    if raw.empty:
        return []
    n_rows, n_cols = raw.shape
    notna = raw.notna().values

    rects: List[tuple] = []
    _xy_cut_sections(notna, 0, n_rows, 0, n_cols, rects)
    if not rects:
        return []

    try:
        wb_xl = openpyxl.load_workbook(io.BytesIO(content), data_only=True)
        ws = wb_xl[sheet_name] if isinstance(sheet_name, str) else wb_xl.worksheets[sheet_name]
        merges = list(ws.merged_cells.ranges)
    except Exception:
        merges = []

    # Reading order matches how a person scans the sheet: top-to-bottom, then
    # left-to-right for anything sharing a row range — not the arbitrary order the
    # recursive split happened to produce.
    rects.sort(key=lambda rect: (rect[0], rect[2]))

    sections = []
    for row_start, row_end, col_start, col_end in rects:
        header_row = row_start
        header_cells = raw.iloc[header_row, col_start:col_end]
        if header_cells.isna().all():
            continue
        is_merge = any(
            (m.min_row - 1) == header_row and m.max_col - m.min_col > 0
            and (m.min_col - 1) < col_end and (m.max_col - 1) >= col_start
            for m in merges
        )
        if not is_merge:
            is_merge = _looks_like_stacked_header(notna, header_row, header_row + 1, col_start, col_end, row_end)
        data_start = header_row + (2 if is_merge else 1)
        section_name = next((str(v).strip() for v in header_cells.tolist() if pd.notna(v) and str(v).strip()),
                             f"Section row {header_row + 1} col {col_start + 1}")
        sections.append({
            "header_row": header_row, "data_start_row": data_start, "data_end_row": row_end,
            "section_name": section_name[:255], "column_start": col_start, "column_end": col_end,
            "header_type": "typeMerge" if is_merge else None,
        })
    return sections


@router.post("/{type_code}/tabs/{tab_name}/detect-sections", response_model=SectionDetectionResult)
def detect_sections(type_code: str, tab_name: str, file: UploadFile = File(...),
                     db: Session = Depends(get_db), user=Depends(require_permission("workbook_config", "create"))):
    """Structure-only detection for a sheet that may contain multiple tables — stacked
    (blank-row separated) or side-by-side (blank-column separated) — proposes one
    Section per detected table, with its columns, for the admin to review before
    applying via the normal create_section/bulk_create_columns endpoints. No row data
    is read into the app by this endpoint."""
    wt = _get_workbook_type(db, type_code)
    tab = _get_tab(db, wt, tab_name)
    content = file.file.read()
    raw_sections = _detect_sections(content)

    if len(raw_sections) <= 1:
        return SectionDetectionResult(tab_name=tab.tab_name, has_multiple_sections=False, sections=[])

    detected = []
    for sec in raw_sections:
        usecols = list(range(sec["column_start"], sec["column_end"]))
        try:
            if sec["header_type"] == "typeMerge":
                row1_cells, row2_cells = _read_merged_header_rows(content, 0, [sec["header_row"], sec["header_row"] + 1])
                row1_cells = [row1_cells[i] for i in usecols if i < len(row1_cells)]
                row2_cells = [row2_cells[i] for i in usecols if i < len(row2_cells)]
                combined_headers = _combine_header_cells(row1_cells, row2_cells)
                df = pd.read_excel(io.BytesIO(content), sheet_name=0, header=None,
                                    skiprows=sec["header_row"] + 2, usecols=usecols)
                df = df.iloc[:max(0, sec["data_end_row"] - sec["data_start_row"])]
                seen, final_headers = {}, []
                for i, h in enumerate(combined_headers):
                    name = h if h else f"Unnamed: {i}"
                    if name in seen:
                        seen[name] += 1
                        name = f"{name}.{seen[name]}"
                    else:
                        seen[name] = 0
                    final_headers.append(name)
                df.columns = final_headers[:df.shape[1]]
            else:
                df = pd.read_excel(io.BytesIO(content), sheet_name=0, header=sec["header_row"], usecols=usecols)
                df = df.iloc[:max(0, sec["data_end_row"] - sec["data_start_row"])]
        except Exception:
            df = pd.DataFrame()
        cols = []
        for i, col in enumerate(df.columns):
            raw_header = str(col).strip()
            col_name = raw_header
            if _is_unnamed_header(col_name):
                continue
            cols.append(DetectedColumn(column_name=col_name, header_name=raw_header, header_group=None,
                                    data_type=_infer_data_type(df[col]) if not df.empty else "string",
                                    is_required=False, is_unique_key=False, position=i,
                                    column_position=sec["column_start"] + i,
                                    excel_column=openpyxl.utils.get_column_letter(sec["column_start"] + i + 1),
                                    header_row=sec["header_row"]))
        detected.append(DetectedSection(section_name=sec["section_name"], header_row=sec["header_row"] + 1,
                                         data_start_row=sec["data_start_row"] + 1, columns=cols,
                                         column_start=sec["column_start"] + 1, column_end=sec["column_end"],
                                         header_type=sec["header_type"]))

    return SectionDetectionResult(tab_name=tab.tab_name, has_multiple_sections=True, sections=detected)


@router.post("/config/apply", response_model=BulkConfigResult)
def apply_config_query(payload: BulkConfigQuery, db: Session = Depends(get_db),
                        user=Depends(require_permission("workbook_config", "create"))):
    """Declarative bulk create-or-update for workbook/tab/section/column config from a
    JSON 'configuration query' — see the format shown in the Workbook Configuration
    page. Matches existing records by code/tab_name/section_name/column_name (creates
    what's missing, updates what exists); never writes row data."""
    wt = db.query(WorkbookType).filter(WorkbookType.code == payload.workbook_type.code).first()
    if not wt:
        wt = WorkbookType(code=payload.workbook_type.code, name=payload.workbook_type.name,
                           description=payload.workbook_type.description)
        stamp_create(wt, user.id)
        db.add(wt)
        db.commit()
        db.refresh(wt)
    elif wt.is_deleted:
        wt.is_deleted = False
        stamp_update(wt, user.id)
        db.commit()

    tabs_created = tabs_updated = sections_created = columns_created = columns_updated = 0

    for tab_spec in payload.tabs:
        tab = next((t for t in wt.tabs if t.tab_name == tab_spec.tab_name and not t.is_deleted), None)
        header_row_0 = max(0, tab_spec.header_row - 1)
        header_type = _derive_header_type(tab_spec.structure, None, header_row_0)
        header_rows_0 = [max(0, int(x) - 1) for x in (tab_spec.header_rows or [tab_spec.header_row])] if tab_spec.structure != "none" else []
        if not tab:
            tab = WorkbookTab(workbook_type_id=wt.id, tab_name=tab_spec.tab_name, header_row_index=header_row_0,
                               header_type=header_type, read_only=tab_spec.read_only,
                               has_multiple_sections=tab_spec.has_multiple_sections,
                               header_rows=header_rows_0, remarks_column_name=tab_spec.remarks_column_name)
            stamp_create(tab, user.id)
            db.add(tab)
            db.commit()
            db.refresh(tab)
            tabs_created += 1
        else:
            tab.header_row_index = header_row_0
            tab.header_type = header_type
            tab.read_only = tab_spec.read_only
            tab.has_multiple_sections = tab_spec.has_multiple_sections
            tab.header_rows = header_rows_0
            tab.remarks_column_name = tab_spec.remarks_column_name
            stamp_update(tab, user.id)
            db.commit()
            tabs_updated += 1

        section_by_name = {}
        for sec_spec in tab_spec.sections:
            section = next((s for s in tab.sections if s.section_name == sec_spec.section_name and not s.is_deleted), None)
            if not section:
                section = WorkbookSection(
                    tab_id=tab.id, section_name=sec_spec.section_name, header_row=max(0, sec_spec.header_row - 1),
                    header_rows=[max(0, int(x) - 1) for x in (sec_spec.header_rows or [sec_spec.header_row])],
                    data_start_row=(sec_spec.data_start_row - 1) if sec_spec.data_start_row else None,
                    data_end_row=(sec_spec.data_end_row - 1) if sec_spec.data_end_row else None,
                    column_start=(sec_spec.column_start - 1) if sec_spec.column_start else None,
                    column_end=sec_spec.column_end, header_type=sec_spec.header_type,
                    position=sec_spec.position, description=sec_spec.description)
                stamp_create(section, user.id)
                db.add(section)
                db.commit()
                db.refresh(section)
                sections_created += 1
            else:
                section.header_row = max(0, sec_spec.header_row - 1)
                section.header_rows = [max(0, int(x) - 1) for x in (sec_spec.header_rows or [sec_spec.header_row])]
                section.data_start_row = (sec_spec.data_start_row - 1) if sec_spec.data_start_row else None
                section.data_end_row = (sec_spec.data_end_row - 1) if sec_spec.data_end_row else None
                section.column_start = (sec_spec.column_start - 1) if sec_spec.column_start else None
                section.column_end = sec_spec.column_end
                section.header_type = sec_spec.header_type
                section.position = sec_spec.position
                section.description = sec_spec.description
                stamp_update(section, user.id)
                db.commit()
            section_by_name[sec_spec.section_name] = section

        for col_spec in tab_spec.columns:
            if col_spec.section_name and col_spec.section_name not in section_by_name:
                raise HTTPException(status_code=400, detail=f"Unknown section '{col_spec.section_name}' for tab '{tab_spec.tab_name}'")
            section_id = section_by_name[col_spec.section_name].id if col_spec.section_name else None
            if _should_skip_config_column(tab, section_id, col_spec.column_name, col_spec.header_name):
                continue
            existing = next((c for c in tab.columns if not c.is_deleted and c.column_name == col_spec.column_name
                              and c.section_id == section_id), None)
            header_row_col = (col_spec.header_row - 1) if col_spec.header_row else None
            column_position_col = (col_spec.column_position - 1) if col_spec.column_position else None
            if not existing:
                col = WorkbookTabColumn(tab_id=tab.id, section_id=section_id, column_name=col_spec.column_name,
                                         header_name=col_spec.header_name, header_group=col_spec.header_group,
                                         header_row=header_row_col, column_position=column_position_col,
                                         data_type=col_spec.data_type, is_required=col_spec.required,
                                         is_unique_key=col_spec.unique, position=col_spec.position,
                                         validation_rules=col_spec.validation_rules, number_format=col_spec.number_format,
                                         is_percentage=col_spec.is_percentage, formula_config=col_spec.formula_config)
                stamp_create(col, user.id)
                db.add(col)
                columns_created += 1
            else:
                existing.header_name = col_spec.header_name
                existing.header_group = col_spec.header_group
                existing.header_row = header_row_col
                existing.column_position = column_position_col
                existing.data_type = col_spec.data_type
                existing.is_required = col_spec.required
                existing.is_unique_key = col_spec.unique
                existing.position = col_spec.position
                existing.validation_rules = col_spec.validation_rules
                existing.number_format = col_spec.number_format
                existing.is_percentage = col_spec.is_percentage
                existing.formula_config = col_spec.formula_config
                stamp_update(existing, user.id)
                columns_updated += 1
        db.commit()

    return BulkConfigResult(workbook_type=wt.code, tabs_created=tabs_created, tabs_updated=tabs_updated,
                             sections_created=sections_created, columns_created=columns_created,
                             columns_updated=columns_updated)

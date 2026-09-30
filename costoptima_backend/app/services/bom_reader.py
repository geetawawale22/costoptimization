"""Workbook sheet reading and commodity resolution, independent of the web layer.

These helpers previously lived in ``app/routers/boms.py``. ``app/services/pcm_runtime.py``
imported ``_read_bom_sheet`` from that router, which made the calculation engine depend
on FastAPI: the engine could not be imported, unit-tested or run offline without the
whole HTTP stack, and ``tools/verify_pcm_reference.py`` inherited that dependency.

Nothing about the reading behaviour is changed here - the functions are moved verbatim
so that legacy parity is unaffected. ``app/routers/boms.py`` re-exports them under their
original private names so existing imports and tests keep working.

The one behavioural addition is that commodity aliases are now resolved from database
configuration when available (see ``resolve_commodity_code``), with the original
hard-coded table retained as the default. Legacy PCM_5 hard-coded these names, so the
default preserves legacy behaviour exactly when no configuration row exists.
"""
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import pandas as pd
from openpyxl import load_workbook
from sqlalchemy.orm import Session

from app.models.commodity import CommodityType


def norm(value) -> str:
    return " ".join(str(value or "").split()).casefold()


# Legacy PCM_5 treated "Hard" (the input file's 'Hard' sheet, and 'HARD' as a
# Commodity/Parent Commodity cell value) as a first-class commodity distinct
# from any CommodityType.name string - see PCM/Sheet_name.xlsx rows 'Hard Make',
# 'Hard Assy', 'Hard Buy Non Std', 'Hard Special Process', 'Hard Paint', all of
# which map to the same master data as 'Trim' (PCM_master/Trim/*, seeded here as
# the "trim" CommodityType / trim_self / trim_master_user / trim_pce workbooks,
# which is exactly what app/services/pcm_hard.py::HardLookup reads from).
# resolve_commodity_code below only matches an exact CommodityType.code/name,
# so without this alias table 'Hard' silently resolved to no commodity at all
# and every Hard-sheet BOM line calculated to Total=0 / "Not Configured".
#
# This remains the DEFAULT rather than the only source: an administrator can
# override or extend it through app_config group ``PCM_ENGINE`` key
# ``commodity_aliases`` (a JSON object of {alias: commodity_code}) without a
# source-code change. An empty/absent config falls back to exactly this table,
# so behaviour is unchanged for existing installations.
DEFAULT_COMMODITY_ALIASES = {
    "hard": "trim",
    "hard make": "trim",
    "hard assy": "trim",
    "hard buy non std": "trim",
    "hard buy std": "trim",
    "hard special process": "trim",
    "hard paint": "trim",
    "soft": "soft_trim",
}

# Backwards-compatible name used by existing imports.
COMMODITY_ALIASES = DEFAULT_COMMODITY_ALIASES


def load_commodity_aliases(db: Session | None = None) -> dict:
    """Return the effective alias table: configured overrides merged over the default.

    Configuration is read through the existing app_config service so that it shares
    the project's caching/soft-delete behaviour. Any failure to read configuration
    falls back to the hard-coded default rather than resolving no commodity at all,
    because returning an empty table would silently zero every Hard-sheet BOM line.
    """
    aliases = dict(DEFAULT_COMMODITY_ALIASES)
    if db is None:
        return aliases
    try:
        from app.services.app_config_service import get_config_value

        configured = get_config_value(db, "PCM_ENGINE", "commodity_aliases", None)
    except Exception:
        return aliases
    if isinstance(configured, dict):
        for alias, code in configured.items():
            if isinstance(alias, str) and isinstance(code, str) and alias.strip() and code.strip():
                aliases[norm(alias)] = code.strip()
    return aliases


def json_safe_value(value: Any):
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if hasattr(value, "item"):
        try:
            return json_safe_value(value.item())
        except Exception:
            pass
    if isinstance(value, dict):
        return {str(k): json_safe_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe_value(v) for v in value]
    return value


def json_safe_row(row: dict) -> dict:
    return {str(k): json_safe_value(v) for k, v in (row or {}).items() if str(k).strip()}


def resolve_commodity_code(db: Session, sheet_name: str, row: dict, commodities=None) -> str | None:
    commodities = (
        commodities
        if commodities is not None
        else db.query(CommodityType).filter(CommodityType.is_deleted.is_(False)).all()
    )
    by_code = {norm(c.code): c.code for c in commodities}
    by_name = {norm(c.name): c.code for c in commodities}
    aliases = load_commodity_aliases(db)

    def _lookup(value) -> str | None:
        norm_value = norm(value)
        return (
            by_code.get(norm_value)
            or by_name.get(norm_value)
            or by_code.get(norm(aliases.get(norm_value)))
        )

    for key in ("Commodity", "commodity", "Parent Commodity"):
        value = row.get(key)
        if value not in (None, ""):
            resolved = _lookup(value)
            if resolved:
                return resolved
    return _lookup(sheet_name)


def read_bom_sheet(path: str, sheet_name: str):
    """Read a worksheet in streaming mode and return JSON-safe row dictionaries.

    The earlier implementation materialized every Excel cell before detecting the
    header. That made small uploads feel slow when workbooks carried formatting or
    validation metadata over a large range. This version keeps openpyxl in
    read-only streaming mode and only stores the resulting logical data rows.
    """
    wb = load_workbook(path, data_only=False, read_only=True)
    try:
        ws = wb[sheet_name]
        iterator = ws.iter_rows(values_only=False)
        scanned = []
        header_idx = None
        header_row = None
        for idx, row in enumerate(iterator):
            if idx < 15:
                scanned.append(row)
                values = {str(c.value).strip() for c in row if c.value not in (None, "")}
                looks_like_pcm_header = bool({"PF Code", "Project", "Type"} & values) and (
                    "Type" in values or "Customer No" in values or "Material Code" in values
                )
                if looks_like_pcm_header:
                    header_idx = idx
                    header_row = row
                    break
                if header_idx is None and sum(c.value not in (None, "") for c in row) >= 2:
                    header_idx = idx
                    header_row = row
            else:
                break
        if header_row is None:
            if not scanned:
                return []
            header_idx = 0
            header_row = scanned[0]
            iterator = iter(scanned[1:])
        else:
            iterator = iterator

        headers = []
        seen = {}
        for pos, cell in enumerate(header_row):
            name = str(cell.value).strip() if cell.value not in (None, "") else ""
            if not name or name.lower().startswith("unnamed"):
                continue
            base = name
            n = seen.get(base, 0)
            if n:
                name = f"{base}.{n}"
            seen[base] = n + 1
            headers.append((pos, name))

        result = []
        for row in iterator:
            data = {}
            for pos, name in headers:
                value = row[pos].value if pos < len(row) else None
                if isinstance(value, str):
                    value = value.strip() if value.strip() else None
                data[name] = json_safe_value(value)
            if any(v not in (None, "") for v in data.values()):
                result.append(json_safe_row(data))
        return result
    finally:
        wb.close()

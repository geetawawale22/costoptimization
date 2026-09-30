import os
import re
import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Any, List

import pandas as pd
from openpyxl import load_workbook
from app.models.workbook import WorkbookType
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.orm import Session

from app.database import get_db
from app.config import settings
from app.core.deps import require_permission
from app.models.bom import Bom, BomTab, BomLine
from app.models.commodity import CommodityType
from app.models.mixins import stamp_create, stamp_update
from app.schemas.bom import BomOut, BomTabOut, BomLinePreview, ValidationResult, ValidationIssue



# These helpers moved to app/services/bom_reader.py so that the calculation engine
# (app/services/pcm_runtime.py) no longer has to import this router - a service must
# not depend on the HTTP layer. They are re-exported under their original private
# names so existing imports and tests continue to work unchanged.
from app.services.bom_reader import (  # noqa: F401
    COMMODITY_ALIASES,
    DEFAULT_COMMODITY_ALIASES,
    load_commodity_aliases,
    norm as _norm,
    json_safe_value as _json_safe_value,
    json_safe_row as _json_safe_row,
    resolve_commodity_code as _resolve_commodity_code,
    read_bom_sheet as _read_bom_sheet,
)


def _safe_upload_filename(filename: str | None, default: str = "input.xlsx") -> str:
    raw_name = os.path.basename(filename or default)
    stem, ext = os.path.splitext(raw_name)
    stem = re.sub(r"[^A-Za-z0-9._ -]+", "_", stem).strip(" ._") or "input"
    ext = ext if ext.lower() in {".xlsx", ".xlsm", ".xls"} else ".xlsx"
    return f"{stem}{ext}"


def _versioned_upload_path(filename: str | None, default: str = "input.xlsx") -> str:
    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
    safe_name = _safe_upload_filename(filename, default)
    stem, ext = os.path.splitext(safe_name)
    candidate = os.path.join(settings.UPLOAD_DIR, safe_name)
    if not os.path.exists(candidate):
        return candidate
    version = 2
    while True:
        candidate = os.path.join(settings.UPLOAD_DIR, f"{stem}_V{version}{ext}")
        if not os.path.exists(candidate):
            return candidate
        version += 1


from app.pcm.engine import load_definition, PcmError
from app.services.pcm_runtime import resolve_sheet_name


def _configured_input_sheets(definition, sheet_names):
    config = definition["input"]
    aliases = config.get("sheet_aliases", {})
    # Match the runtime: only the driver sheet is mandatory.
    driver = config["bom_sheet"]
    missing = [] if driver in sheet_names else [driver]
    known = {driver} & sheet_names
    specs = [{"sheet": name} for name in config.get("headers", {})]
    specs += config.get("enrichment", []) + config.get("relations", [])
    if definition.get("output", {}).get("sbc_sheet"):
        specs.append({"sheet": definition["output"]["sbc_sheet"]})
    for spec in specs:
        resolved = resolve_sheet_name(spec["sheet"], spec.get("aliases") or aliases.get(spec["sheet"]), sheet_names)
        if resolved:
            known.add(resolved)
    return missing, known


router = APIRouter(prefix="/boms", tags=["BOM Intake"])
logger = logging.getLogger("costoptima.boms")


def _sheet_columns_preview(path: str, sheet: str, limit: int = 2):
    try:
        rows = _read_bom_sheet(path, sheet)
        columns = list(rows[0].keys()) if rows else []
        return columns, rows[:limit], len(rows)
    except Exception as exc:
        return [], [{"read_error": str(exc)}], 0


def _identify_sheet_type(sheet: str, columns: list[str], commodity_code: str | None) -> tuple[str, str]:
    names = {str(c).strip().casefold() for c in columns}
    sheet_key = str(sheet or "").strip().casefold()
    if sheet_key in {"bom", "sbc", "pf", "hard", "soft", "paint", "gdc_input"}:
        return "PCM Input Data", "Sheet name is part of the PCM input workbook flow"
    if {"type", "commodity", "customer no"} & names and ("type" in names or "customer no" in names):
        return "Commodity/Calculation Data", "Columns include PCM row identifiers such as Type, Commodity, or Customer No"
    if {"material code", "process", "rate", "supplier", "output"} & names:
        return "Master-like Data", "Columns look like master/configuration fields used by lookup rules"
    if commodity_code:
        return "Commodity Data", f"Sheet/row resolved to configured commodity code {commodity_code!r}"
    return "Other/Unmapped Data", "No PCM input/master/commodity identification rule matched"


@router.post("/upload", response_model=BomOut)
def upload_bom(file: UploadFile = File(...), project_name: str = Form(None),
               customer: str = Form(None), db: Session = Depends(get_db),
               user=Depends(require_permission("calculations", "create"))):
    logger.info("UPLOAD_START filename=%s project=%s customer=%s", file.filename, project_name, customer)
    file_path = _versioned_upload_path(file.filename)
    content = file.file.read()
    logger.info("UPLOAD_READ filename=%s bytes=%s", file.filename, len(content))
    with open(file_path, "wb") as f:
        f.write(content)
    logger.info("UPLOAD_SAVED original_filename=%s saved_path=%s", file.filename, os.path.abspath(file_path))


    bom = Bom(project_name=project_name, customer=customer, file_path=file_path, status="Uploaded")
    stamp_create(bom, user.id)
    db.add(bom)
    db.commit()
    db.refresh(bom)

    try:
        # Keep upload fast: inspect only workbook metadata here. Full row parsing is
        # deferred to preview/validation/calculation, where the rows are actually needed.
        commodities = db.query(CommodityType).filter(CommodityType.is_deleted.is_(False)).all()
        wb = load_workbook(file_path, read_only=True, data_only=False)
        logger.info("WORKBOOK_OPEN path=%s sheets=%s", os.path.abspath(file_path), wb.sheetnames)
        for sheet in wb.sheetnames:
            commodity_code = _resolve_commodity_code(db, sheet, {}, commodities)
            columns, preview, row_count = _sheet_columns_preview(file_path, sheet)
            data_type, reason = _identify_sheet_type(sheet, columns, commodity_code)
            logger.info("DATA_IDENTIFICATION file=%s sheet=%s identified_as=%s reason=%s rows=%s columns=%s sample=%s",
                        os.path.basename(file_path), sheet, data_type, reason, row_count, columns, preview)
            tab = BomTab(bom_id=bom.id, commodity_code=commodity_code, detected_sheet_name=sheet)
            stamp_create(tab, user.id)
            db.add(tab)
        db.commit()
    except Exception:
        pass  # not a valid Excel file â€” leave tabs empty, validate() will surface the problem

    return bom


@router.get("", response_model=List[BomOut])
def list_boms(db: Session = Depends(get_db), _=Depends(require_permission("calculations", "view"))):
    return db.query(Bom).filter(Bom.is_deleted.is_(False)).order_by(Bom.created_at.desc()).all()


@router.get("/{bom_id}", response_model=BomOut)
def get_bom(bom_id: int, db: Session = Depends(get_db), _=Depends(require_permission("calculations", "view"))):
    bom = db.query(Bom).filter(Bom.id == bom_id, Bom.is_deleted.is_(False)).first()
    if not bom:
        raise HTTPException(status_code=404, detail="BOM not found")
    return bom


@router.get("/{bom_id}/tabs", response_model=List[BomTabOut])
def get_bom_tabs(bom_id: int, db: Session = Depends(get_db), _=Depends(require_permission("calculations", "view"))):
    exists = db.query(Bom.id).filter(Bom.id == bom_id, Bom.is_deleted.is_(False)).first()
    if not exists:
        raise HTTPException(status_code=404, detail="BOM not found")
    return (db.query(BomTab)
              .filter(BomTab.bom_id == bom_id, BomTab.is_deleted.is_(False))
              .order_by(BomTab.id)
              .all())


@router.get("/{bom_id}/preview", response_model=List[BomLinePreview])
def preview_bom(bom_id: int, limit: int = 20, db: Session = Depends(get_db),
                user=Depends(require_permission("calculations", "view"))):
    bom = db.query(Bom).filter(Bom.id == bom_id, Bom.is_deleted.is_(False)).first()
    if not bom:
        raise HTTPException(status_code=404, detail="BOM not found")
    if not bom.lines:
        try:
            xl = pd.ExcelFile(bom.file_path)
            for tab in bom.tabs[:1]:
                df = pd.read_excel(xl, sheet_name=tab.detected_sheet_name)
                df = df.where(pd.notnull(df), None)
                for _, record in df.head(limit).iterrows():
                    row_data = _json_safe_row(record.to_dict())
                    line = BomLine(bom_id=bom.id, commodity_code=tab.commodity_code, row_data=row_data)
                    stamp_create(line, user.id)
                    db.add(line)
            db.commit()
        except Exception:
            db.rollback()
            pass
    return (db.query(BomLine)
              .filter(BomLine.bom_id == bom_id, BomLine.is_deleted.is_(False))
              .limit(limit).all())


@router.post("/{bom_id}/validate", response_model=ValidationResult)
def validate_bom(bom_id: int, db: Session = Depends(get_db), user=Depends(require_permission("calculations", "view"))):
    bom = db.query(Bom).filter(Bom.id == bom_id, Bom.is_deleted.is_(False)).first()
    if not bom:
        raise HTTPException(status_code=404, detail="BOM not found")

    issues: List[ValidationIssue] = []
    sheet_names = {t.detected_sheet_name for t in bom.tabs if not t.is_deleted}
    known = set()
    missing = []
    try:
        definition = load_definition(db)
        if definition is None:
            raise PcmError("PCM_ENGINE.definition is not configured in the database")
        missing, known = _configured_input_sheets(definition, sheet_names)
    except (PcmError, KeyError, TypeError) as exc:
        logger.exception("VALIDATION_CONFIG_ERROR bom_id=%s", bom_id)
        issues.append(ValidationIssue(check="PCM configuration", status="Error", message=str(exc)))
    found = sorted(known)
    unknown = sorted(sheet_names - known)
    logger.info("INPUT_VALIDATION bom_id=%s source=database_PCM_ENGINE.definition sheets=%s recognized=%s missing=%s unconfigured=%s",
                bom_id, sorted(sheet_names), found, missing, unknown)

    if missing:
        issues.append(ValidationIssue(check="PCM required input sheets", status="Error",
                                       message=f"Missing required sheet(s): {', '.join(missing)}"))
    elif not issues:
        issues.append(ValidationIssue(check="PCM required input sheets", status="Passed",
                                       message=f"Found configured input sheets: {', '.join(found)}"))

    if unknown:
        issues.append(ValidationIssue(check="Additional sheets", status="Warning",
                                       message=f"These sheets are not configured for the PCM calculation flow: {', '.join(unknown)}"))
    else:
        issues.append(ValidationIssue(check="Additional sheets", status="Passed"))

    # A sheet can be present by name yet still fail to resolve to a CommodityType
    # (e.g. no CommodityType.code/name or COMMODITY_ALIASES entry matches its
    # Commodity value). Without this check that was invisible: validate_bom only
    # looked at sheet names, so a BOM could show "Validated" while every row on
    # an unmapped sheet silently calculated to Total=0 / "Not Configured".
    unmapped_tabs = sorted({t.detected_sheet_name for t in bom.tabs
                             if not t.is_deleted and t.commodity_code is None
                             and t.detected_sheet_name not in known})
    if unmapped_tabs:
        issues.append(ValidationIssue(
            check="Commodity <-> Sheet Match", status="Warning",
            message=(f"Sheet(s) {', '.join(unmapped_tabs)} did not resolve to any configured "
                     f"Commodity Type or PCM input mapping. Review the database PCM_ENGINE.definition "
                     f"input mappings and configured commodity aliases.")))
    else:
        issues.append(ValidationIssue(check="Commodity <-> Sheet Match", status="Passed"))

    issues.append(ValidationIssue(check="Master Rate Data", status="Info",
                                   message="Master Rate Data is fetched live from configured master tables during calculation."))
    issues.append(ValidationIssue(check="Email notification", status="Info",
                                   message="Email is not configured yet; calculation will not send mail."))

    bom.status = "Failed" if any(i.status == "Error" for i in issues) else "Validated"
    stamp_update(bom, user.id)
    db.commit()
    logger.info("VALIDATION_DONE bom_id=%s status=%s issues=%s", bom_id, bom.status,
                [issue.model_dump() for issue in issues])
    return ValidationResult(bom_id=bom_id, issues=issues)


@router.delete("/{bom_id}")
def delete_bom(bom_id: int, db: Session = Depends(get_db), user=Depends(require_permission("calculations", "delete"))):
    bom = db.query(Bom).filter(Bom.id == bom_id, Bom.is_deleted.is_(False)).first()
    if not bom:
        raise HTTPException(status_code=404, detail="BOM not found")
    bom.is_deleted = True
    stamp_update(bom, user.id)
    db.commit()
    return {"message": "BOM deleted", "bom_id": bom_id}


@router.delete("")
def delete_all_boms(db: Session = Depends(get_db), user=Depends(require_permission("calculations", "delete"))):
    boms = db.query(Bom).filter(Bom.is_deleted.is_(False)).all()
    for bom in boms:
        bom.is_deleted = True
        stamp_update(bom, user.id)
    db.commit()
    return {"message": "BOMs deleted", "boms_deleted": len(boms)}


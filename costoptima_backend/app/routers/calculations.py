"""
Calculations â€” runs synchronously (on the fly) inside POST /boms/{id}/calculate.
Replaces the earlier async Jobs pattern entirely: no Queued/Running polling loop.
"""
import io
import os
import openpyxl
import time
import logging
from datetime import datetime
from types import SimpleNamespace
from typing import List

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.core.deps import get_current_user, require_permission
from app.models.bom import Bom, BomLine
from app.routers.boms import _json_safe_row, _read_bom_sheet, _resolve_commodity_code
from app.models.commodity import CommodityType
from app.models.workbook import WorkbookType, WorkbookTab
from app.config import settings
from openpyxl import load_workbook
from app.models.calculation import Calculation, CalculationCommodityResult, CalculationLog, CalculationResult
from app.models.mixins import stamp_create
from app.schemas.calculation import CalculationOut
from app.calc_engine import run_synchronous_calculation
from app.services.pcm_sheet_metal import (
    build_pcm_output_workbook, write_pcm_output_file, _master_lookup as _pcm_sheet_metal_master_lookup,
)
from app.services.diagnostics import DiagnosticTrace

router = APIRouter(tags=["Calculations"])


def _ensure_bom_lines_parsed(db: Session, bom: Bom, user) -> List[BomLine]:
    existing = db.query(BomLine).filter(BomLine.bom_id == bom.id, BomLine.is_deleted.is_(False)).all()
    if existing:
        return existing
    try:
        for tab in bom.tabs:
            if tab.is_deleted:
                continue
            rows = _read_bom_sheet(bom.file_path, tab.detected_sheet_name)
            for row_data in rows:
                row_data = _json_safe_row(row_data)
                code = tab.commodity_code or _resolve_commodity_code(db, tab.detected_sheet_name, row_data)
                if not code:
                    continue
                line = BomLine(bom_id=bom.id, commodity_code=code, row_data=row_data)
                stamp_create(line, user.id)
                db.add(line)
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"Could not parse calculation input: {exc}")
    return db.query(BomLine).filter(BomLine.bom_id == bom.id, BomLine.is_deleted.is_(False)).all()


def _calculation_lines_in_memory(db: Session, bom: Bom):
    """Build transient calculation rows without inserting BomLine records.

    PCM's old flow calculates from the BOM sheet and uses the other sheets as
    source/reference sheets for output generation. Persisting every parsed row in
    bom_lines made upload/calculate slow and also exposed JSON serialization edge
    cases. The calculation engine only needs commodity_code and row_data, so a
    light SimpleNamespace keeps the hot path in memory.
    """
    try:
        tabs = [t for t in bom.tabs if not t.is_deleted]
        bom_tab = next((t for t in tabs if str(t.detected_sheet_name).strip().casefold() == "bom"), None)
        selected_tabs = [bom_tab] if bom_tab else [t for t in tabs if t.commodity_code]
        lines = []
        for tab in selected_tabs:
            if not tab:
                continue
            rows = _read_bom_sheet(bom.file_path, tab.detected_sheet_name)
            for row_data in rows:
                row_data = _json_safe_row(row_data)
                code = tab.commodity_code or _resolve_commodity_code(db, tab.detected_sheet_name, row_data)
                if not code:
                    row_type = str(row_data.get("Type") or "").strip().upper()
                    commodity = str(row_data.get("Commodity") or "").strip().upper()
                    code = commodity.casefold() if commodity else (row_type.replace(" ", "_").casefold() if row_type else "pcm")
                lines.append(SimpleNamespace(commodity_code=code, row_data=row_data))
        return lines
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not parse calculation input: {exc}")


def _append_extra_input_lines(db: Session, bom: Bom, path: str, user):
    try:
        xl = pd.ExcelFile(path)
        for sheet in xl.sheet_names:
            rows = _read_bom_sheet(path, sheet)
            for row_data in rows:
                row_data = _json_safe_row(row_data)
                row_data["_source_file"] = "extra_input"
                code = _resolve_commodity_code(db, sheet, row_data)
                line = BomLine(bom_id=bom.id, commodity_code=code, row_data=row_data)
                stamp_create(line, user.id)
                db.add(line)
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"Could not parse second input file: {exc}")


def _is_merge_source(code: str) -> bool:
    return (code == "pcm_master" or code == "shift_factor_rate"
            or code.endswith("_self") or code.endswith("_master_user") or code.endswith("_pce"))


def _source_commodity_label(code: str) -> str:
    for suffix in ("_master_user", "_self", "_pce"):
        if code.endswith(suffix):
            return code[:-len(suffix)]
    return code


def _master_lookup(db: Session):
    """Build calculation lookups from current commodity master rows every time.
    Master Rate Data is treated as a live view, not a stored workbook dependency.

    This used to be a second, independently-maintained copy of
    app.services.pcm_sheet_metal._master_lookup — the two had already drifted
    (this one was missing 'shift_factor_rate' from _is_merge_source and the
    PCM_HARD config block until patched here). Delegating to the single
    implementation in pcm_sheet_metal.py removes that drift risk entirely
    instead of just re-fixing both copies by hand.
    """
    return _pcm_sheet_metal_master_lookup(db)


def _rules_by_commodity(db: Session):
    result = {}
    for commodity in db.query(CommodityType).filter(CommodityType.is_deleted.is_(False)).all():
        result[commodity.code] = [r for r in commodity.rules if not r.is_deleted]
    return result


def _is_pcm_bom(bom: Bom) -> bool:
    names = {str(tab.detected_sheet_name).strip() for tab in bom.tabs if not tab.is_deleted}
    # BOM is the PCM dispatch sheet. Treat even an incomplete PCM workbook as
    # PCM so it receives a configuration/input error instead of another engine.
    return "BOM" in names


def _run_standard_calculation(db: Session, bom: Bom, user):
    """Keep the ordinary configurable CostOptima flow separate from PCM."""
    started = datetime.utcnow()
    lines = _ensure_bom_lines_parsed(db, bom, user)
    calculated = run_synchronous_calculation(
        lines, master_lookup=_master_lookup(db), rules_by_commodity=_rules_by_commodity(db))
    calc = Calculation(bom_id=bom.id, status="Completed", started_at=started,
                       completed_at=datetime.utcnow())
    stamp_create(calc, user.id)
    db.add(calc)
    db.flush()
    total = 0.0
    by_code = {code: iter(bucket["lines"]) for code, bucket in calculated.items()}
    for line in lines:
        line._calc_result = next(by_code[line.commodity_code or "unclassified"])
    for code, bucket in calculated.items():
        amount = float(bucket["total_cost"])
        total += amount
        item = CalculationCommodityResult(calculation_id=calc.id, commodity_code=code,
                                           status="Completed", records_found=bucket["records"],
                                           total_cost=amount)
        stamp_create(item, user.id)
        db.add(item)
    output_path = _write_calculation_output(calc.id, lines)
    result = CalculationResult(calculation_id=calc.id, total_cost=total,
                               savings_opportunity=0, confidence_pct=0,
                               breakdown={code: bucket["total_cost"] for code, bucket in calculated.items()},
                               output_file_path=os.path.relpath(output_path, settings.UPLOAD_DIR).replace(os.sep, "/"),
                               email_sent="Yes")
    stamp_create(result, user.id)
    db.add(result)
    log = CalculationLog(calculation_id=calc.id, level="Success", process="Standard Calculation",
                         message=f"{len(lines)} input rows calculated synchronously")
    stamp_create(log, user.id)
    db.add(log)
    bom.status = "Calculated"
    db.commit()
    db.refresh(calc)
    return calc


def _write_calculation_output(calc_id: int, lines):
    output_dir = os.path.join(settings.UPLOAD_DIR, "outputs")
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, f"calc_{calc_id}_output.xlsx")
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    by_code = {}
    for line in lines:
        by_code.setdefault(line.commodity_code or "unclassified", []).append(line)
    for code, code_lines in by_code.items():
        ws = wb.create_sheet(code[:31])
        headers = []
        for line in code_lines:
            for k in (line.row_data or {}).keys():
                if k not in headers and not str(k).startswith("_"):
                    headers.append(k)
            for k in (line._calc_result if hasattr(line, "_calc_result") else {}).keys():
                if k not in headers:
                    headers.append(k)
        for c, h in enumerate(headers, 1):
            ws.cell(1, c, h)
        for r, line in enumerate(code_lines, 2):
            data = dict(line.row_data or {})
            data.update(getattr(line, "_calc_result", {}) or {})
            for c, h in enumerate(headers, 1):
                ws.cell(r, c, data.get(h))
    wb.save(path)
    return path




def _copy_sheet_values(src_ws, dst_ws):
    for row in src_ws.iter_rows():
        for cell in row:
            dst_ws.cell(cell.row, cell.column, cell.value)


def _input_dataframe(path: str, sheet: str, header: int = 0):
    try:
        df = pd.read_excel(path, sheet_name=sheet, header=header, dtype=object)
        df = df.where(pd.notnull(df), None)
        df.columns = [str(c).strip() if c is not None else "" for c in df.columns]
        return df
    except Exception:
        return pd.DataFrame()


def _write_df(ws, df: pd.DataFrame, start_row: int = 1, start_col: int = 1):
    for c, col in enumerate(df.columns, start_col):
        ws.cell(start_row, c, col)
    for r_idx, row in enumerate(df.itertuples(index=False), start_row + 1):
        for c_idx, value in enumerate(row, start_col):
            ws.cell(r_idx, c_idx, value)


def _legacy_variant_columns(df: pd.DataFrame):
    base = {"PF Code", "Project", "Part Name", "BC / SBC / TKO / RoCM", "Date", "Estimation Revision", "0 / 1 / 2 / 3",
            "Supplier", "End Item", "Function", "Supplier No", "Customer No", "Desc", "2D DWG/CAD Revision No", "Type",
            "Qty/Veh", "Commodity", "Special Process", "Material Code", "Material", "Grade", "Color", "L", "W", "H", "thk",
            "Part Wt", "Gross Wt", "m/c Tonnage", "Cycle Time", "Cavity", "HRS Y or N"}
    return [c for c in df.columns if c and c not in base]


def _filter_type(df: pd.DataFrame, type_name: str):
    if df.empty or "Type" not in df.columns:
        return pd.DataFrame()
    return df[df["Type"].astype(str).str.strip().str.upper() == type_name].copy()


def _legacy_pcm_workbook_from_input(input_path: str):
    """Generate the old PCM-style multi-sheet output from the uploaded workbook."""
    src_wb = load_workbook(input_path, data_only=False)
    hard = _input_dataframe(input_path, "Hard", header=1)
    variants = _legacy_variant_columns(hard)

    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name in ["SBC", "End Item", "Master_Output", "Analysis", "End Item Analysis", "Make", "Buy Std", "Assy", "Pkg", "Frt", "Paint", "Special Process"]:
        wb.create_sheet(name)

    if "SBC" in src_wb.sheetnames:
        _copy_sheet_values(src_wb["SBC"], wb["SBC"])
    if "Paint" in src_wb.sheetnames:
        _copy_sheet_values(src_wb["Paint"], wb["Paint"])

    end_df = _filter_type(hard, "END ITEM")
    if end_df.empty and "End Item" in hard.columns:
        end_df = hard[[c for c in ["End Item", "Supplier", "Customer No", "Desc", "Type", "Qty/Veh"] if c in hard.columns]].drop_duplicates().copy()
        if "Type" not in end_df.columns:
            end_df["Type"] = "END ITEM"
    end_cols = [c for c in ["End Item", "Supplier", "Customer No", "Desc", "Type", "Qty/Veh"] + variants if c in end_df.columns]
    if not end_df.empty:
        _write_df(wb["End Item"], end_df[end_cols].drop_duplicates())

    make_df = _filter_type(hard, "MAKE")
    make_cols = [c for c in ["Supplier", "End Item", "Supplier No", "Customer No", "Desc", "2D DWG/CAD Revision No", "Type", "Qty/Veh", "Material Code", "Material", "Grade", "Color", "L", "W", "H", "thk", "Part Wt", "Gross Wt", "m/c Tonnage", "Cycle Time", "Cavity", "HRS Y or N"] + variants if c in make_df.columns]
    if not make_df.empty:
        _write_df(wb["Make"], make_df[make_cols])
        for r in range(2, wb["Make"].max_row + 1):
            if wb["Make"].max_column >= 10:
                wb["Make"].cell(r, 10, f'=IFERROR(VLOOKUP($I{r},\'[1]Material Master\'!$A:$SF,3,0),"")')
            if wb["Make"].max_column >= 11:
                wb["Make"].cell(r, 11, f'=IFERROR(VLOOKUP($I{r},\'[1]Material Master\'!$A:$SF,6,0),"")')

    buy_df = _filter_type(hard, "BUY STD")
    buy_cols = [c for c in ["Supplier", "End Item", "Supplier No", "Customer No", "Desc", "2D DWG/CAD Revision No", "Type", "Qty/Veh"] + variants if c in buy_df.columns]
    if not buy_df.empty:
        _write_df(wb["Buy Std"], buy_df[buy_cols])
        ws = wb["Buy Std"]
        extra = ["Basic Cost/Pc", "ICC", "REJ", "O&H", "Profit", "Total/Pc"]
        start = ws.max_column + 1
        for i, h in enumerate(extra, start):
            ws.cell(1, i, h)
        for r in range(2, ws.max_row + 1):
            ws.cell(r, start, f'=IFERROR(VLOOKUP($D{r},\'[1]BOP\'!$D:$G,4,0),0)')
            ws.cell(r, start + 5, f'=SUM({openpyxl.utils.get_column_letter(start)}{r}:{openpyxl.utils.get_column_letter(start+4)}{r})')

    assy_df = _filter_type(hard, "ASSY")
    assy_cols = [c for c in ["Supplier", "End Item", "Supplier No", "Customer No", "Desc", "Operation Process", "No of Points", "Category", "Qty/Veh"] + variants if c in assy_df.columns]
    if not assy_df.empty:
        _write_df(wb["Assy"], assy_df[assy_cols])

    pkg_rows, frt_rows = [], []
    end_source = end_df if not end_df.empty else (hard.drop_duplicates(subset=["End Item"]) if "End Item" in hard.columns else pd.DataFrame())
    for idx, (_, row) in enumerate(end_source.iterrows(), 1):
        end_item = row.get("End Item")
        if not end_item:
            continue
        supplier_no = row.get("Supplier No") or idx
        pkg_rows.append({"End Item": end_item, "Supplier No": supplier_no, "Customer No": f"PKGXX{idx:02d}", "Desc": f"PKG FOR {end_item}", "Qty/Veh": 1, "Basic Cost": 0, **{v: row.get(v, 1) for v in variants}})
        frt_rows.append({"End Item": end_item, "Supplier No": supplier_no, "Customer No": f"FRTXX{idx:02d}", "Desc": f"FRT FOR {end_item}", "Qty/Veh": 1, "Basic Cost": 0, **{v: row.get(v, 1) for v in variants}})
    if pkg_rows:
        _write_df(wb["Pkg"], pd.DataFrame(pkg_rows))
    if frt_rows:
        _write_df(wb["Frt"], pd.DataFrame(frt_rows))

    sp = _filter_type(hard, "SPECIAL PROCESS")
    process_col = "Process / Assy Name" if "Process / Assy Name" in hard.columns else ("Special Process" if "Special Process" in hard.columns else None)
    if sp.empty and process_col:
        sp = hard[hard[process_col].notna() & (hard[process_col].astype(str).str.strip() != "")].copy()
    sp_cols = [c for c in ["Supplier", "End Item", "Supplier No", "Customer No", "Desc", "Qty/Veh", "Commodity", process_col, "Finish"] + variants if c and c in sp.columns]
    if not sp.empty:
        sp_out = sp[sp_cols].copy()
        if process_col and process_col != "Process / Assy Name":
            sp_out = sp_out.rename(columns={process_col: "Process / Assy Name"})
        if "Commodity" not in sp_out.columns:
            sp_out.insert(min(7, len(sp_out.columns)), "Commodity", "HARD")
        _write_df(wb["Special Process"], sp_out)

    master = hard.copy()
    if not master.empty:
        if "Sector & Location" not in master.columns:
            master.insert(min(3, len(master.columns)), "Sector & Location", "AD")
        _write_df(wb["Master_Output"], master)

    drivers = ["Cost Driver", "RM cost", "Process cost", "Buy Std", "Buy Non Std", "Assy", "Special Process", "Markup", "PnF", "Total"]
    analysis = wb["Analysis"]
    for r, label in enumerate(drivers, 1):
        analysis.cell(r, 1, label)
    for c, v in enumerate(variants, 2):
        analysis.cell(1, c, v)
        for r in range(2, len(drivers) + 1):
            col = openpyxl.utils.get_column_letter(c)
            analysis.cell(r, c, f'=IFERROR(SUM(Master_Output!{col}:{col}),0)')

    ei_analysis = wb["End Item Analysis"]
    unique_ei = [x for x in hard["End Item"].dropna().drop_duplicates().tolist()] if "End Item" in hard.columns else []
    for block, ei in enumerate(unique_ei):
        start_col = 1 + block * (len(variants) + 3)
        ei_analysis.cell(1, start_col + 1, ei)
        for r, label in enumerate(drivers, 2):
            ei_analysis.cell(r, start_col, label)
        for c, v in enumerate(variants, start_col + 1):
            ei_analysis.cell(2, c, v)
            for r in range(3, len(drivers) + 2):
                col = openpyxl.utils.get_column_letter(c - start_col + 1)
                ei_analysis.cell(r, c, f'=IFERROR(SUMPRODUCT((Master_Output!$J:$J={openpyxl.utils.quote_sheetname(ei_analysis.title)}!{openpyxl.utils.get_column_letter(start_col+1)}$1),Master_Output!{col}:{col}),0)')
    return wb


def _write_legacy_pcm_output(calc_id: int, bom: Bom):
    output_dir = os.path.join(settings.UPLOAD_DIR, "outputs")
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, f"calc_{calc_id}_output.xlsx")
    wb = _legacy_pcm_workbook_from_input(bom.file_path)
    wb.save(path)
    return path


@router.post("/boms/{bom_id}/calculate", response_model=CalculationOut)
def calculate(bom_id: int, verbose: bool | None = None, db: Session = Depends(get_db),
              user=Depends(require_permission("calculations", "create"))):
    """Runs the whole cost calculation immediately and returns the finished result â€”
    no job id to poll, no background worker; by the time this responds, it's done."""
    bom = db.query(Bom).filter(Bom.id == bom_id, Bom.is_deleted.is_(False)).first()
    if not bom:
        raise HTTPException(status_code=404, detail="BOM not found")

    if not _is_pcm_bom(bom):
        return _run_standard_calculation(db, bom, user)

    from app.pcm.engine import load_definition, PcmError
    from app.services.pcm_calculation import run_calculation
    try:
        definition = load_definition(db)
        if definition is not None:
            # Auditability: record which engine actually served this calculation.
            # Previously this was only inferable indirectly (presence/absence of
            # a pcm_run_snapshots row), which is fine for the PCM engine path but
            # gives no equivalent signal on the compatibility path below -- there
            # was no single log line saying "the old hardcoded engine ran" for a
            # request where a reviewer might otherwise assume the PCM engine
            # (since app_config PCM_ENGINE.definition exists) had handled it.
            logging.getLogger("costoptima.calculations").info(
                "bom_id=%s engine=pcm_database_runtime definition_revision=%s",
                bom_id, definition.get("revision"),
            )
            verbose_enabled = settings.PCM_VERBOSE_LOGS if verbose is None else verbose
            trace = DiagnosticTrace(enabled=verbose_enabled, prefix=f"PCM CALC {bom_id}") if verbose_enabled else None
            return run_calculation(db, bom, user, definition, trace=trace)
    except (PcmError, ValueError, KeyError) as exc:
        db.rollback()
        logging.getLogger("costoptima.calculations").exception("CALCULATION_FAILED bom_id=%s", bom_id)
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    logging.getLogger("costoptima.calculations").info(
        "bom_id=%s engine=compatibility_calc_engine reason=%s",
        bom_id, "no_active_PCM_ENGINE_definition" if definition is None else "unexpected",
    )

    raise HTTPException(status_code=422, detail={
        "code": "CONFIG_NOT_AVAILABLE",
        "message": "PCM_ENGINE.definition is missing or inactive. Install the database formula definition and master data before calculating.",
    })


@router.get("/calculations", response_model=List[CalculationOut])
def list_calculations(db: Session = Depends(get_db), _=Depends(require_permission("calculations", "view"))):
    return (db.query(Calculation)
              .filter(Calculation.is_deleted.is_(False))
              .order_by(Calculation.id.desc()).all())


@router.get("/calculations/{calc_id}", response_model=CalculationOut)
def get_calculation(calc_id: int, db: Session = Depends(get_db), _=Depends(require_permission("calculations", "view"))):
    calc = db.query(Calculation).filter(Calculation.id == calc_id, Calculation.is_deleted.is_(False)).first()
    if not calc:
        raise HTTPException(status_code=404, detail="Calculation not found")
    return calc


@router.get("/calculations/{calc_id}/export")
def export_calculation(calc_id: int, db: Session = Depends(get_db), user=Depends(require_permission("calculations", "view"))):
    calc = db.query(Calculation).filter(Calculation.id == calc_id, Calculation.is_deleted.is_(False)).first()
    if not calc or not calc.result:
        raise HTTPException(status_code=404, detail="No completed result to export")
    output_path = os.path.join(settings.UPLOAD_DIR, calc.result.output_file_path or "")
    from app.models.pcm_run import PcmRunSnapshot
    from app.pcm.engine import write_snapshot_output
    snapshot = db.query(PcmRunSnapshot).filter(PcmRunSnapshot.calculation_id == calc_id).first()
    if snapshot and (not calc.result.output_file_path or not os.path.exists(output_path)):
        output_path = os.path.join(settings.UPLOAD_DIR, "outputs", f"calc_{calc_id}_pcm.xlsx")
        write_snapshot_output(snapshot, output_path)
        calc.result.output_file_path = os.path.relpath(output_path, settings.UPLOAD_DIR).replace(os.sep, "/")
        db.commit()
    if not calc.result.output_file_path or not os.path.exists(output_path):
        raise HTTPException(status_code=409, detail={
            "code": "OUTPUT_NOT_AVAILABLE",
            "message": "The stored output is missing and this historical calculation has no PCM snapshot. Run a new calculation; historical results will not be recreated using current rates.",
        })
    with open(output_path, "rb") as f:
        buffer = io.BytesIO(f.read())
    buffer.seek(0)
    filename = os.path.basename(output_path) or f"calc_{calc_id}_output.xlsx"
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/calculations/{calc_id}/pcm-snapshot")
def get_pcm_snapshot(calc_id: int, db: Session = Depends(get_db), _=Depends(require_permission("calculations", "view"))):
    from app.models.pcm_run import PcmRunSnapshot
    calc = db.query(Calculation).filter(Calculation.id == calc_id, Calculation.is_deleted.is_(False)).first()
    if not calc:
        raise HTTPException(status_code=404, detail="Calculation not found")
    snapshot = db.query(PcmRunSnapshot).filter(PcmRunSnapshot.calculation_id == calc_id).first()
    if not snapshot:
        raise HTTPException(status_code=404, detail="This calculation predates the PCM database runtime")
    return {"calculation_id": calc_id, "input_sha256": snapshot.input_sha256,
            "definition_sha256": snapshot.definition_sha256, "definition": snapshot.definition,
            "results": snapshot.results}

@router.delete("/calculations/{calc_id}")
def delete_calculation(calc_id: int, db: Session = Depends(get_db), user=Depends(require_permission("calculations", "delete"))):
    calc = db.query(Calculation).filter(Calculation.id == calc_id, Calculation.is_deleted.is_(False)).first()
    if not calc:
        raise HTTPException(status_code=404, detail="Calculation not found")
    calc.is_deleted = True
    calc.modified_by = user.id
    db.commit()
    return {"message": "Calculation deleted", "calculation_id": calc_id}


@router.delete("/calculations")
def delete_all_calculations(db: Session = Depends(get_db), user=Depends(require_permission("calculations", "delete"))):
    calculations = db.query(Calculation).filter(Calculation.is_deleted.is_(False)).all()
    for calc in calculations:
        calc.is_deleted = True
        calc.modified_by = user.id
    db.commit()
    return {"message": "Calculations deleted", "calculations_deleted": len(calculations)}

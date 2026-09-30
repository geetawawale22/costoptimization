import os
import json
import re
import shutil
from copy import deepcopy, copy
from pathlib import Path
from typing import Dict, Any, List, Optional

import openpyxl
import pandas as pd
import xlsxwriter
from openpyxl import load_workbook
from openpyxl.formula.translate import Translator
from sqlalchemy.orm import Session

from app.config import settings
from app.models.bom import Bom
from app.models.calculation import OutputTemplate
from app.models.mixins import stamp_create
from app.models.workbook import WorkbookType
from app.services.pcm_hard import calculate_hard_line, calculate_paint_rows, calculate_special_process_from_paint
from app.services.app_config_service import get_config_value

# Admin-editable defaults surfaced under Settings > App Config, group "PCM_HARD".
# These are the SAME values pcm_hard.py previously had as bare Python literals —
# moving them here (and reading them through get_config_value, same mechanism
# every other configurable value in this app uses) means a revised shift rate,
# rate period, or allowance factor is a Settings-page edit, not a code change.
# discover_config_keys() in app_config_service.py auto-detects the
# get_config_value(...) call in _pcm_hard_config() below, so these show up in
# the Settings UI's key list automatically.
PCM_HARD_CONFIG_DEFAULTS: Dict[str, Any] = {
    "hrs_allowance_factor": 1.03,
    "default_shift_factor": 10.0,
    "default_shift_rate": 8500.0,
    "default_efficiency": 0.9,
    "shift_seconds": 8 * 3600.0,
    "deflashing_tonnage_threshold": 400.0,
    "deflashing_flat_cost": 0.5,
    "assy_default_ct_per_point": 6.0,
    "assy_default_handling_time": 10.0,
    "paint_default_process_cost": 125.0,
    # Blank ("") means "auto-detect the latest MMM#YY column in the data" —
    # see pcm_hard._latest_period_column. Set either of these to pin a
    # specific published rate period instead of always using the newest one.
    "active_rm_rate_period": "",
    "active_paint_rate_period": "",
}


def _pcm_hard_config(db: Session) -> Dict[str, Any]:
    return {key: get_config_value(db, "PCM_HARD", key, default=default)
            for key, default in PCM_HARD_CONFIG_DEFAULTS.items()}


TEMPLATE_NAME = "pcm_sheet_metal_forging"


def _seed_formula_repository() -> Dict[str, str]:
    seed_path = Path(__file__).resolve().parents[1] / "data" / "pcm_output_formula_repository.json"
    try:
        with seed_path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return {}
    return {str(key): str(value) for key, value in (data or {}).items()}


def _formula_repository(layout: Dict[str, Any]) -> Dict[str, str]:
    repo = layout.get("formula_repository") or {}
    return {str(key): str(value) for key, value in repo.items()}


def _formula_template(layout: Dict[str, Any], key: str | None, fallback: str = "") -> str:
    if not key:
        return fallback
    return _formula_repository(layout).get(str(key), fallback)


def _render_formula(template: str, **values: Any) -> str:
    formula = str(template or "")
    for key, value in values.items():
        formula = formula.replace("${" + key + "}", str(value))
    return formula
DEFAULT_TEMPLATE: Dict[str, Any] = {
    "description": "Config driven PCM Sheet Metal/Forging output migrated from Sheet_Metal_Forging_with summary_chk_Chnages.py",
    "formula_repository": _seed_formula_repository(),
    "required_input_sheets": ["BOM", "Hard", "SBC", "PF"],
    "optional_input_sheets": ["Paint"],
    "sheet_order": ["SBC", "End Item", "Master_Output", "Analysis", "End Item Analysis", "Make", "Buy Std", "Assy", "Pkg", "Frt", "Paint", "Special Process"],
    "variant_exclude_columns": ["PF Code", "Project", "Part Name", "BC / SBC / TKO / RoCM", "Date", "Estimation Revision", "0 / 1 / 2 / 3", "Supplier", "End Item", "Function", "Supplier No", "Customer No", "Desc", "2D DWG/CAD Revision No", "Type", "Qty/Veh", "Commodity", "Special Process", "Process / Assy Name", "Operation Process", "No of Points", "Category", "Material Code", "Material", "Grade", "Color", "Finish", "RM Base", "Sector & Location", "Surface Area (in SQM)", "L", "W", "H", "thk", "Part Wt", "Gross Wt", "m/c Tonnage", "Cycle Time", "Cavity", "HRS Y or N", "Process Signoff Ref no", "PnF Signoff Ref no"],
    "type_filters": {
        "Make": {"source_sheet": "Hard", "type": "MAKE"},
        "Buy Std": {"source_sheet": "BOM", "type": "BUY STD"},
        "Assy": {"source_sheet": "Hard", "type": "ASSY"},
        "Special Process": {"source_sheet": "BOM", "type": "SPECIAL PROCESS"}
    },
    "columns": {
        "End Item": ["End Item", "Supplier", "Customer No", "Desc", "Type", "Qty/Veh"],
        "Make": ["Supplier", "End Item", "Supplier No", "Customer No", "Desc", "2D DWG/CAD Revision No", "Type", "Qty/Veh", "Material Code", "Material Family", "Material Grade", "Color", "L", "W", "H", "thk", "Part Wt", "Gross Wt", "m/c Tonnage", "Cycle Time", "Cavity", "HRS Y or N", "RM Base", "Rate Kg", "RM Cost/Pc", "Shift Factor", "Shift Rate", "EFFICIENCY", "Pc per shift", "Molding Cost", "Deflashing cost", "Process Cost/Pc", "Basic Cost/Pc", "ICC", "REJ", "OH", "PROFIT", "ICC COST", "REJ COST", "OH COST", "PROFIT COST", "MARKUP/Pc", "Total Cost/Pc", "PnF/Pc", "Grand Total Cost/Pc", "RM Cost/Veh", "Process Cost/Veh", "BASIC Cost/Veh", "MARKUP/Veh", "Total Cost/Veh", "PnF/Veh", "Grand Total Cost/Veh"],
        "Buy Std": ["Supplier", "End Item", "Supplier No", "Customer No", "Desc", "2D DWG/CAD Revision No", "Type", "Qty/Veh", "Basic Cost/Pc", "ICC", "REJ", "O&H", "PROFIT", "ICC Cost", "REJ Cost", "O&H Cost", "PROFIT Cost", "Markup", "Total/Pc"],
        "Assy": ["Supplier", "End Item", "Supplier No", "Customer No", "Desc", "Operation Process", "No of Points", "Category", "Qty/Veh", "CT Per Point", "Handling Time", "MHR", "CT(sec)", "Basic cost/ pc", "ICC", "REJ", "O&H", "PROFIT", "ICC Cost", "REJ Cost", "O&H Cost", "PROFIT Cost", "Markup", "Total"],
        "Special Process": ["Supplier", "End Item", "Supplier No", "Customer No", "Desc", "Sr", "Qty/Veh", "Commodity", "Surface Area (in SQM)", "Process / Assy Name", "Finish", "Total RM Cost", "Consumable / Masking Cost", "RM Cost", "Process Cost", "Tier 2 Markup+PnF", "Basic cost", "ICC", "REJ", "O&H", "PROFIT", "ICC Cost", "REJ Cost", "O&H Cost", "PROFIT Cost", "MARKUP", "Total cost /PC"],
        "Paint": ["Sr", "Supplier", "End Item", "Make Part no", "Customer No", "Desc", "Qty/Veh", "L", "W", "H", "thk", "Finish", "Painting Area (in SQM)", "Paint System", "Material Code", "Material", "Consumption (in Litre)", "No of parts / Jig", "Make Part Cost", "RM Base", "Rate / Litre", "RM cost / Pc", "Consumable / Masking Cost", "Hanger Cost", "Process Cost/Pc", "Paint RM Cost", "Paint Process Cost", "Basic Cost", "ICC", "REJ ON INJ MOLDED PART", "REJ ON MATERIAL & PROCESS", "OVERALL REJECTION", "OVERHEAD", "PROFIT", "ICC Cost", "REJ ON INJ MOLDED PART Cost", "REJ ON MATERIAL & PROCESS Cost", "OVERALL REJECTION Cost", "OVERHEAD Cost", "PROFIT Cost", "Paint Markup", "Pnf", "Total Cost/Pc"],
        "Pkg": ["End Item", "Supplier No", "Customer No", "Desc", "Qty/Veh", "Basic Cost"],
        "Frt": ["End Item", "Supplier No", "Customer No", "Desc", "Qty/Veh", "Basic Cost"]
    },
    "bom_type_sheets": {
        "Buy Std": "BUY STD",
        "Pkg": "PKG",
        "Frt": "FRT"
    },
    "formula_columns": {
        "Make": [
            {"column": "Material Family", "formula_key": "material_master_lookup_family"},
            {"column": "Material Grade", "formula_key": "material_master_lookup_grade"}
        ],
        "Buy Std": [
            {"column": "Basic Cost/Pc", "formula_key": "buy_std_basic_cost"},
            {"column": "Total/Pc", "formula_key": "buy_std_total_pc"}
        ]
    },
    "analysis_drivers": ["Cost Driver", "RM cost", "Process cost", "Buy Std", "Buy Non Std", "Assy", "Special Process", "Markup", "PnF", "Total"],
    "master_output_columns": ["PF Code", "Project", "Part Name", "Sector & Location", "BC / SBC / TKO / RoCM", "Date", "Estimation Revision", "0 / 1 / 2 / 3", "Supplier", "End Item", "Function", "Supplier No", "Customer No", "Desc", "2D DWG/CAD Revision No", "Type", "Qty/Veh", "Commodity", "Operation Process", "No of Points", "Category", "Special Process", "Material Code", "Material", "Grade", "Material Family", "Material Sub Family", "Material Grade", "RM Base", "Color", "Surface Area (in SQM)", "L", "W", "H", "thk", "Part Wt", "Gross Wt", "m/c Tonnage", "Cycle Time", "Cavity", "HRS Y or N", "Process / Assy Name", "Finish", "Process Signoff Ref no", "RM Rate/UOM", "RM Cost", "Process Cost", "Basic Cost/Pc", "ICC Cost", "REJ Cost", "O&H Cost", "PROFIT Cost", "Markup Cost/Pc", "Total Cost/Pc", "Assy CT per Point", "Assy Handling Time", "Assy MHR", "Assy CT(in sec)", "Special RM Cost", "Special Process Cost", "Special Markup"],
    "master_output_source": "BOM",
    "minimum_rows": {
        "SBC": 21,
        "End Item": 6,
        "Master_Output": 15,
        "Analysis": 28,
        "End Item Analysis": 20,
        "Make": 19,
        "Buy Std": 18,
        "Assy": 18,
        "Pkg": 6,
        "Frt": 6,
        "Paint": 29,
        "Special Process": 17
    },
    "minimum_columns": {
        "SBC": 12,
        "End Item": 15,
        "Master_Output": 70,
        "Analysis": 10,
        "End Item Analysis": 21,
        "Make": 62,
        "Buy Std": 28,
        "Assy": 33,
        "Pkg": 16,
        "Frt": 16,
        "Paint": 52,
        "Special Process": 36
    },
    "footer_rules": {
        "End Item": {"type": "variant_sum", "formula_key": "variant_sum_footer", "label_column": 1},
        "Pkg": {"type": "pnf_sumproduct", "formula_key": "pkg_frt_pnf_footer", "label": "Pnf", "label_column": 7, "qty_column": 5, "basic_cost_column": 6},
        "Frt": {"type": "pnf_sumproduct", "formula_key": "pkg_frt_pnf_footer", "label": "Pnf", "label_column": 7, "qty_column": 5, "basic_cost_column": 6}
    },
    "legacy_pcm5_porting": {
        "source_file": "PCM_5/Sheet_Metal_Forging_with summary_chk_Chnages.py",
        "status": "configurable_port_in_progress",
        "commodity_functions": {
            "paint": {"lines": "598-660", "status": "pending_full_formula_port"},
            "stamping": {"lines": "661-876", "status": "calc_profile_active_legacy_template_style_active"},
            "soft": {"lines": "877-1077", "status": "calc_profile_active_legacy_template_style_active"},
            "special_process": {"lines": "6509-6568", "status": "partial_output_port"},
            "bystd": {"lines": "6569-6616", "status": "partial_output_port"},
            "buynonstd": {"lines": "6617-6708", "status": "pending_full_formula_port"},
            "PKG": {"lines": "6709-6749", "status": "partial_footer_port"},
            "FRT": {"lines": "6750-6787", "status": "partial_footer_port"},
            "sand_casting_receipe": {"lines": "6788-7107", "status": "calc_profile_active_recipe_details_partial"},
            "sand_casting": {"lines": "7108-7615", "status": "calc_profile_active_legacy_template_style_active"},
            "gdc_casting": {"lines": "8718-9289", "status": "calc_profile_active_legacy_template_style_active"},
            "pdc": {"lines": "16853-17465", "status": "calc_profile_active_legacy_template_style_active"},
            "consign": {"lines": "10748-10791", "status": "pending_full_formula_port"},
            "end_item": {"lines": "10792-10866", "status": "partial_output_port"},
            "make": {"lines": "10867-10948", "status": "partial_output_port"},
            "forging": {"lines": "12735-12874", "status": "calc_profile_active_legacy_template_style_active"},
            "forging_data": {"lines": "12875-13537", "status": "calc_profile_active_legacy_template_style_active"},
            "pcba_op": {"lines": "13926-14312", "status": "calc_profile_active_legacy_template_style_active"},
            "assy": {"lines": "14313-14391", "status": "partial_output_port"},
            "seat": {"lines": "14511-14587", "status": "minimal_calc_profile_active_legacy_template_style_active"},
            "hvac": {"lines": "15502-15591", "status": "calc_profile_active_legacy_template_style_active"}
        },
        "summary_functions": {
            "Analysis": {"lines": "11178-12001", "status": "partial_layout_port_legacy_template_style_active"},
            "End_Analysis": {"lines": "12089-12431", "status": "partial_layout_port_legacy_template_style_active"},
            "master_output": {"lines": "15215-15501", "status": "partial_export_active_legacy_template_style_active_formula_cells_pending"},
            "merge": {"lines": "17466-19600", "status": "partial_export_active_legacy_template_style_active_formula_cells_pending"}
        }
    },
    "performance_plan": {
        "calculation": "in_memory_rows",
        "final_output_writer": "xlsxwriter_constant_memory",
        "disabled_intermediate_files": ["MA.xlsx", "BOP.xlsx", "Part1*.xlsx", "*_formula_*.xlsx"],
        "next": ["bulk_db_upsert_for_import", "streaming_master_import", "master_lookup_request_cache"]
    }
}



def _deep_merge_defaults(defaults: Dict[str, Any], current: Dict[str, Any]) -> Dict[str, Any]:
    merged = deepcopy(defaults)
    for key, value in (current or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge_defaults(merged[key], value)
        else:
            merged[key] = value
    return merged


def _normalize_layout(layout: Dict[str, Any]) -> Dict[str, Any]:
    layout = _deep_merge_defaults(DEFAULT_TEMPLATE, layout or {})
    default_excludes = set(DEFAULT_TEMPLATE.get("variant_exclude_columns") or [])
    configured_excludes = {
        value for value in (layout.get("variant_exclude_columns") or [])
        if not str(value).strip().upper().startswith(("MX ", "AX ")) and str(value).strip().upper() not in {"AX7L", "ZENITH"}
    }
    layout["variant_exclude_columns"] = sorted(default_excludes | configured_excludes)
    for sheet_name, default_columns in (DEFAULT_TEMPLATE.get("columns") or {}).items():
        configured = list((layout.get("columns") or {}).get(sheet_name) or [])
        merged = list(dict.fromkeys(configured + list(default_columns)))
        layout.setdefault("columns", {})[sheet_name] = merged
    return layout

def get_or_create_pcm_template(db: Session, user_id: int | None = None) -> OutputTemplate:
    template = db.query(OutputTemplate).filter(OutputTemplate.name == TEMPLATE_NAME, OutputTemplate.is_deleted.is_(False)).first()
    if template:
        return template
    template = OutputTemplate(name=TEMPLATE_NAME, format_type="xlsx", layout=deepcopy(DEFAULT_TEMPLATE))
    if user_id:
        stamp_create(template, user_id)
    db.add(template)
    db.commit()
    db.refresh(template)
    return template


def _read_df(path: str, sheet: str, header: int = 0) -> pd.DataFrame:
    try:
        df = pd.read_excel(path, sheet_name=sheet, header=header, dtype=object)
        df = df.where(pd.notnull(df), None)
        df.columns = [str(c).strip() if c is not None else "" for c in df.columns]
        return df
    except Exception:
        return pd.DataFrame()


def _copy_sheet(src_wb, source: str, dst_wb, target: str):
    if source not in src_wb.sheetnames or target not in dst_wb.sheetnames:
        return
    src, dst = src_wb[source], dst_wb[target]
    for row in src.iter_rows():
        for cell in row:
            dst.cell(cell.row, cell.column, cell.value)


def _write_df(ws, df: pd.DataFrame):
    for c, col in enumerate(df.columns, 1):
        ws.cell(1, c, col)
    for r, row in enumerate(df.itertuples(index=False), 2):
        for c, value in enumerate(row, 1):
            ws.cell(r, c, value)


def _filter_type(df: pd.DataFrame, type_name: str) -> pd.DataFrame:
    if df.empty or "Type" not in df.columns:
        return pd.DataFrame()
    return df[df["Type"].astype(str).str.strip().str.upper() == type_name].copy()


def _variants(df: pd.DataFrame, layout: Dict[str, Any]) -> List[str]:
    exclude = set(layout.get("variant_exclude_columns") or [])
    return [c for c in df.columns if c and c not in exclude]


def _pcm_variants(bom_df: pd.DataFrame, hard: pd.DataFrame, layout: Dict[str, Any]) -> List[str]:
    variants = _variants(hard, layout)
    if variants:
        return variants
    return _variants(bom_df, layout)


def _select_columns(df: pd.DataFrame, configured: List[str], variants: List[str]) -> pd.DataFrame:
    cols = [c for c in configured + variants if c in df.columns]
    cols = list(dict.fromkeys(cols))
    return df[cols].copy() if cols else pd.DataFrame()


def _is_merge_source(code: str) -> bool:
    return (code == "pcm_master" or code == "shift_factor_rate"
            or code.endswith("_self") or code.endswith("_master_user") or code.endswith("_pce"))


def _source_commodity_label(code: str) -> str:
    for suffix in ("_master_user", "_self", "_pce"):
        if code.endswith(suffix):
            return code[:-len(suffix)]
    return code


def _master_lookup(db: Session):
    lookup = {}
    for wt in db.query(WorkbookType).filter(WorkbookType.is_deleted.is_(False)).all():
        if wt.code == "master_rate_data" or not _is_merge_source(wt.code):
            continue
        commodity = _source_commodity_label(wt.code)
        for tab in wt.tabs:
            if tab.is_deleted:
                continue
            cols = sorted([c for c in tab.columns if not c.is_deleted], key=lambda c: (c.section_id or 0, c.position))
            unique = [c.column_name for c in cols if c.is_unique_key]
            scoped = lookup.setdefault("scoped", {}).setdefault(wt.code, {}).setdefault(tab.tab_name, {})
            index = lookup.setdefault(tab.tab_name, {})
            for row in sorted((r for r in tab.rows if not r.is_deleted), key=lambda r: r.id):
                data = {k: v for k, v in (row.row_data or {}).items() if not str(k).startswith("_")}
                data.setdefault("Commodity", commodity)
                key = tuple(str(data.get(k)).strip() for k in unique) if unique else None
                if key and all(key):
                    lookup_key = key[0] if len(key) == 1 else key
                    scoped[lookup_key] = data
                    index.setdefault(lookup_key, data)
                for fallback_key in ("Material Code", "Customer No", "Supplier", "Process", "Material"):
                    value = str(data.get(fallback_key) or "").strip()
                    if value:
                        scoped.setdefault(value, data)
                        index.setdefault(value, data)
    for name, index in list(lookup.items()):
        if name == "scoped":
            continue
        lookup.setdefault(name.replace(" ", "_").lower(), index)
    if "Material Master" in lookup:
        lookup["material_master"] = lookup["Material Master"]
    if "Norms" in lookup:
        lookup["norms"] = lookup["Norms"]
    lookup["config"] = _pcm_hard_config(db)
    return lookup


def _with_hard_calculations(df: pd.DataFrame, db: Session) -> pd.DataFrame:
    if df.empty:
        return df
    lookup = _master_lookup(db)
    rows = []
    for record in df.to_dict("records"):
        calculated = calculate_hard_line(record, lookup)
        merged = dict(record)
        for key, value in calculated.items():
            if key == "Total" and "Total" in merged:
                continue
            if value not in (None, ""):
                merged[key] = value
        rows.append(merged)
    return pd.DataFrame(rows)


def _make_costs_from_hard(hard: pd.DataFrame, db: Session) -> Dict[str, float]:
    make = _with_hard_calculations(_filter_type(hard, "MAKE"), db)
    costs = {}
    for row in make.to_dict("records"):
        key = str(row.get("Customer No") or "").strip()
        if key:
            costs[key] = float(row.get("Basic Cost/Pc") or row.get("Total Cost/Pc") or 0)
    return costs


def _calculated_paint_df(path: str, hard: pd.DataFrame, db: Session) -> pd.DataFrame:
    paint = _read_df(path, "Paint", header=1)
    if paint.empty:
        return paint
    rows = calculate_paint_rows(paint.to_dict("records"), _master_lookup(db), _make_costs_from_hard(hard, db))
    return pd.DataFrame(rows)


def _paint_by_customer(paint_df: pd.DataFrame) -> Dict[str, Dict[str, Any]]:
    result = {}
    if paint_df.empty:
        return result
    for row in paint_df.to_dict("records"):
        try:
            has_sr = pd.notna(row.get("Sr")) and row.get("Sr") != ""
        except Exception:
            has_sr = row.get("Sr") not in (None, "")
        if has_sr:
            key = str(row.get("Customer No") or "").strip()
            if key:
                result[key] = row
    return result


def _with_special_process_calculations(df: pd.DataFrame, paint_df: pd.DataFrame, db: Session) -> pd.DataFrame:
    if df.empty:
        return df
    lookup = _master_lookup(db)
    paint_index = _paint_by_customer(paint_df)
    rows = []
    for record in df.to_dict("records"):
        merged = dict(record)
        merged.update(calculate_special_process_from_paint(record, paint_index, lookup))
        rows.append(merged)
    return pd.DataFrame(rows)


def _fmt_pcm_date(value):
    try:
        ts = pd.Timestamp(value)
        if pd.notna(ts):
            return ts.strftime("%d.%b.%Y")
    except Exception:
        pass
    return value


def _build_master_output_df(source: pd.DataFrame, paint_df: pd.DataFrame, variants: List[str], db: Session, layout: Dict[str, Any]) -> pd.DataFrame:
    if source.empty:
        return source
    lookup = _master_lookup(db)
    paint_index = _paint_by_customer(paint_df)
    rows = []
    for record in source.to_dict("records"):
        merged = dict(record)
        row_type = str(record.get("Type") or "").strip().upper()
        if "Sector & Location" not in merged or merged.get("Sector & Location") in (None, ""):
            merged["Sector & Location"] = "AD"
        merged["Date"] = _fmt_pcm_date(merged.get("Date"))
        if str(merged.get("Commodity") or "").strip().casefold() == "hard":
            merged["Commodity"] = "HARD"
        if row_type in {"MAKE", "BUY STD", "ASSY"}:
            calc = calculate_hard_line(record, lookup)
            if row_type == "MAKE":
                merged.update({
                    "Material Family": calc.get("Material Family"),
                    "Material Grade": calc.get("Material Grade"),
                    "RM Base": calc.get("RM Base"),
                    "Gross Wt": calc.get("RM Cost/Pc") / calc.get("Rate Kg") if calc.get("Rate Kg") else None,
                    "RM Rate/UOM": calc.get("Rate Kg"),
                    "RM Cost": calc.get("RM Cost/Pc"),
                    "Process Cost": calc.get("Process Cost/Pc"),
                    "Basic Cost/Pc": calc.get("Basic Cost/Pc"),
                    "ICC Cost": calc.get("ICC COST"),
                    "REJ Cost": calc.get("REJ COST"),
                    "O&H Cost": calc.get("OH COST"),
                    "PROFIT Cost": calc.get("PROFIT COST"),
                    "Markup Cost/Pc": calc.get("MARKUP/Pc"),
                    "Total Cost/Pc": calc.get("Total Cost/Pc"),
                })
            elif row_type == "BUY STD":
                merged.update({
                    "Basic Cost/Pc": calc.get("Basic Cost/Pc"),
                    "ICC Cost": calc.get("ICC Cost"),
                    "REJ Cost": calc.get("REJ Cost"),
                    "O&H Cost": calc.get("O&H Cost"),
                    "PROFIT Cost": calc.get("PROFIT Cost"),
                    "Markup Cost/Pc": calc.get("Markup"),
                    "Total Cost/Pc": calc.get("Total/Pc"),
                })
            elif row_type == "ASSY":
                merged.update({
                    "Operation Process": record.get("Operation Process"),
                    "Category": record.get("Category") or "Manual",
                    "Basic Cost/Pc": calc.get("Basic cost/ pc"),
                    "ICC Cost": calc.get("ICC Cost"),
                    "REJ Cost": calc.get("REJ Cost"),
                    "O&H Cost": calc.get("O&H Cost"),
                    "PROFIT Cost": calc.get("PROFIT Cost"),
                    "Markup Cost/Pc": calc.get("Markup"),
                    "Total Cost/Pc": calc.get("Total"),
                    "Assy CT per Point": calc.get("CT Per Point"),
                    "Assy Handling Time": calc.get("Handling Time"),
                    "Assy MHR": calc.get("MHR"),
                    "Assy CT(in sec)": calc.get("CT(sec)"),
                })
        elif row_type == "SPECIAL PROCESS":
            calc = calculate_special_process_from_paint(record, paint_index, lookup)
            merged.update({
                "Process / Assy Name": calc.get("Process / Assy Name"),
                "Surface Area (in SQM)": calc.get("Surface Area (in SQM)"),
                "Basic Cost/Pc": calc.get("Basic cost"),
                "ICC Cost": calc.get("ICC Cost"),
                "REJ Cost": calc.get("REJ Cost"),
                "O&H Cost": calc.get("O&H Cost"),
                "PROFIT Cost": calc.get("PROFIT Cost"),
                "Markup Cost/Pc": calc.get("MARKUP"),
                "Total Cost/Pc": calc.get("Total cost /PC"),
                "Special RM Cost": calc.get("RM Cost"),
                "Special Process Cost": calc.get("Process Cost"),
                "Special Markup": calc.get("Tier 2 Markup+PnF"),
            })
        elif row_type in {"PKG", "FRT"}:
            merged.update({"Basic Cost/Pc": 0, "Markup Cost/Pc": 0, "Total Cost/Pc": 0})
        rows.append(merged)
    columns = list(dict.fromkeys((layout.get("master_output_columns") or DEFAULT_TEMPLATE["master_output_columns"]) + variants))
    return pd.DataFrame(rows).reindex(columns=[c for c in columns if c in set(columns)])


def _master_source_df(bom_df: pd.DataFrame, hard: pd.DataFrame) -> pd.DataFrame:
    if bom_df.empty:
        return hard
    if hard.empty or "Customer No" not in hard.columns:
        return bom_df
    hard_by_customer = {
        str(row.get("Customer No") or "").strip(): row
        for row in hard.to_dict("records")
        if str(row.get("Customer No") or "").strip()
    }
    variant_like = {
        c for c in bom_df.columns
        if str(c).strip().upper().startswith(("MX ", "AX ")) or str(c).strip().upper() in {"AX7L", "ZENITH"}
    }
    rows = []
    for record in bom_df.to_dict("records"):
        merged = dict(record)
        detail = hard_by_customer.get(str(record.get("Customer No") or "").strip())
        if detail:
            for key, value in detail.items():
                if key in variant_like:
                    continue
                if key not in merged or pd.isna(merged.get(key)) or merged.get(key) == "":
                    merged[key] = value
        rows.append(merged)
    return pd.DataFrame(rows)


def _analysis_df(master: pd.DataFrame, variants: List[str]) -> pd.DataFrame:
    def n(value):
        try:
            number = float(value)
            return 0.0 if pd.isna(number) else number
        except Exception:
            return 0.0

    rows = []
    drivers = ["RM cost", "Process cost", "Buy Std", "Buy Non Std", "Assy", "Special Process", "Markup", "PnF", "Total"]
    for driver in drivers:
        row = {"Cost Driver": driver}
        for variant in variants:
            total = 0.0
            for _, record in master.iterrows():
                factor = n(record.get(variant))
                qty = n(record.get("Qty/Veh"))
                typ = str(record.get("Type") or "").strip().upper()
                if driver == "RM cost" and typ == "MAKE":
                    total += factor * qty * n(record.get("RM Cost"))
                elif driver == "Process cost" and typ == "MAKE":
                    total += factor * qty * n(record.get("Process Cost"))
                elif driver == "Buy Std" and typ == "BUY STD":
                    total += factor * qty * n(record.get("Basic Cost/Pc"))
                elif driver == "Assy" and typ == "ASSY":
                    total += factor * qty * n(record.get("Basic Cost/Pc"))
                elif driver == "Special Process" and typ == "SPECIAL PROCESS":
                    total += factor * qty * n(record.get("Basic Cost/Pc"))
                elif driver == "Markup":
                    total += factor * qty * n(record.get("Markup Cost/Pc"))
                elif driver == "PnF":
                    total += 0.0
                elif driver == "Total":
                    total += factor * qty * n(record.get("Total Cost/Pc"))
            row[variant] = 0.0 if driver == "Special Process" and not total else (total if total else None)
        rows.append(row)
    return pd.DataFrame(rows, columns=["Cost Driver"] + variants)


def _end_item_output_df(end_df: pd.DataFrame, master: pd.DataFrame, variants: List[str], layout: Dict[str, Any]) -> pd.DataFrame:
    if end_df.empty or master.empty:
        return end_df

    def n(value):
        try:
            number = float(value)
            return 0.0 if pd.isna(number) else number
        except Exception:
            return 0.0

    rows = []
    for record in end_df.drop_duplicates(subset=["End Item"]).to_dict("records"):
        merged = dict(record)
        end_item = str(record.get("End Item") or "").strip()
        members = master[
            (master["End Item"].astype(str).str.strip() == end_item)
            & (master["Type"].astype(str).str.strip().str.upper() != "END ITEM")
        ]
        for variant in variants:
            merged[variant] = sum(n(row.get(variant)) * n(row.get("Qty/Veh")) * n(row.get("Total Cost/Pc")) for _, row in members.iterrows())
        rows.append(merged)
    return pd.DataFrame(rows)


def _apply_formula_columns(ws, layout: Dict[str, Any], sheet_name: str):
    formula_specs = (layout.get("formula_columns") or {}).get(sheet_name) or []
    if not formula_specs or ws.max_row < 2:
        return
    headers = {str(ws.cell(1, c).value): c for c in range(1, ws.max_column + 1) if ws.cell(1, c).value}
    for spec in formula_specs:
        col_name = spec.get("column")
        if not col_name:
            continue
        formula_template = _formula_template(layout, spec.get("formula_key"))
        if not formula_template:
            continue
        col_idx = headers.get(col_name)
        if not col_idx:
            col_idx = ws.max_column + 1
            ws.cell(1, col_idx, col_name)
            headers[col_name] = col_idx
        for r in range(2, ws.max_row + 1):
            formula = str(formula_template).replace("{row}", str(r))
            for header, idx in headers.items():
                formula = formula.replace("{" + header + "}", openpyxl.utils.get_column_letter(idx))
            ws.cell(r, col_idx, formula)



def _data_last_row(ws) -> int:
    last = 1
    for row in range(2, ws.max_row + 1):
        if any(ws.cell(row, col).value not in (None, "") for col in range(1, ws.max_column + 1)):
            last = row
    return last


def _pad_min_rows(ws, minimum: Optional[int]):
    if not minimum or ws.max_row >= int(minimum):
        return
    target_row = int(minimum)
    ws.cell(target_row, 1, ws.cell(target_row, 1).value)


def _pad_min_columns(ws, minimum: Optional[int]):
    if not minimum:
        return
    if ws.max_column < minimum:
        ws.cell(1, minimum, ws.cell(1, minimum).value)


def _write_variant_sum_footer(ws, layout: Dict[str, Any], rule: Dict[str, Any], minimum: Optional[int] = None, variant_start_col: int = 7):
    if ws.max_column < variant_start_col:
        _pad_min_rows(ws, minimum)
        return
    formula_template = _formula_template(layout, (rule or {}).get("formula_key"))
    if not formula_template:
        return
    data_last = max(1, _data_last_row(ws))
    footer_row = max(minimum or 0, data_last + 3)
    _pad_min_rows(ws, footer_row)
    if data_last < 2:
        return
    for col in range(variant_start_col, ws.max_column + 1):
        letter = openpyxl.utils.get_column_letter(col)
        ws.cell(footer_row, col, _render_formula(formula_template, variant_col=letter, data_last=data_last))


def _write_pnf_footer(ws, layout: Dict[str, Any], rule: Dict[str, Any], minimum: Optional[int] = None, variant_start_col: int = 8):
    formula_template = _formula_template(layout, (rule or {}).get("formula_key"))
    if not formula_template:
        return
    data_last = max(1, _data_last_row(ws))
    footer_row = max(minimum or 0, data_last + 3)
    _pad_min_rows(ws, footer_row)
    label_col = int(rule.get("label_column") or 7)
    qty_col = int(rule.get("qty_column") or 5)
    basic_col = int(rule.get("basic_cost_column") or 6)
    ws.cell(footer_row, label_col, rule.get("label") or "Pnf")
    if data_last < 2:
        return
    qty_letter = openpyxl.utils.get_column_letter(qty_col)
    basic_letter = openpyxl.utils.get_column_letter(basic_col)
    for col in range(variant_start_col, ws.max_column + 1):
        letter = openpyxl.utils.get_column_letter(col)
        ws.cell(footer_row, col, _render_formula(formula_template, variant_col=letter, qty_col=qty_letter, basic_cost_col=basic_letter, data_last=data_last))


def _apply_expansion_rules(wb, layout: Dict[str, Any]):
    minimum_rows = layout.get("minimum_rows") or {}
    minimum_columns = layout.get("minimum_columns") or {}
    footer_rules = layout.get("footer_rules") or {}
    for sheet_name, minimum in minimum_rows.items():
        if sheet_name in wb.sheetnames:
            _pad_min_rows(wb[sheet_name], int(minimum) if minimum else None)
    for sheet_name, minimum in minimum_columns.items():
        if sheet_name in wb.sheetnames:
            _pad_min_columns(wb[sheet_name], int(minimum) if minimum else None)
    for sheet_name, rule in footer_rules.items():
        if sheet_name not in wb.sheetnames:
            continue
        ws = wb[sheet_name]
        rule_type = (rule or {}).get("type")
        minimum = int(minimum_rows.get(sheet_name) or 0) or None
        if rule_type == "variant_sum":
            _write_variant_sum_footer(ws, layout, rule or {}, minimum=minimum, variant_start_col=7)
        elif rule_type == "pnf_sumproduct":
            _write_pnf_footer(ws, layout, rule or {}, minimum=minimum, variant_start_col=8)

def build_pcm_output_workbook(db: Session, bom: Bom, user_id: int | None = None):
    template = get_or_create_pcm_template(db, user_id)
    layout = _normalize_layout(template.layout or {})
    src_wb = load_workbook(bom.file_path, data_only=False)
    bom_df = _read_df(bom.file_path, "BOM", header=0)
    hard = _read_df(bom.file_path, "Hard", header=1)
    paint_df = _calculated_paint_df(bom.file_path, hard, db)
    variants = _pcm_variants(bom_df, hard, layout)
    master_source = _master_source_df(bom_df, hard)
    master_output = _build_master_output_df(master_source, paint_df, variants, db, layout) if not master_source.empty else pd.DataFrame()

    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for sheet in layout.get("sheet_order") or DEFAULT_TEMPLATE["sheet_order"]:
        wb.create_sheet(sheet)

    _copy_sheet(src_wb, "SBC", wb, "SBC")
    if "Paint" in wb.sheetnames and not paint_df.empty:
        _write_df(wb["Paint"], _select_columns(paint_df, layout["columns"].get("Paint", []), variants))

    end_df = _filter_type(bom_df, "END ITEM")
    if end_df.empty:
        end_df = _filter_type(hard, "END ITEM")
    if end_df.empty and "End Item" in bom_df.columns:
        end_df = bom_df.drop_duplicates(subset=["End Item"]).copy()
        if "Type" not in end_df.columns:
            end_df["Type"] = "END ITEM"
    if not end_df.empty:
        _write_df(wb["End Item"], _select_columns(end_df, layout["columns"].get("End Item", []), variants).drop_duplicates())

    for sheet_name, cfg in (layout.get("type_filters") or {}).items():
        if sheet_name not in wb.sheetnames:
            continue
        source_name = (cfg.get("source_sheet") or "Hard").strip().lower()
        source_df = bom_df if source_name == "bom" else hard
        df = _filter_type(source_df, cfg.get("type", ""))
        if sheet_name == "Special Process":
            process_col = "Process / Assy Name" if "Process / Assy Name" in source_df.columns else ("Special Process" if "Special Process" in source_df.columns else None)
            if df.empty and process_col:
                df = source_df[source_df[process_col].notna() & (source_df[process_col].astype(str).str.strip() != "")].copy()
            if not df.empty and "Commodity" not in df.columns:
                df.insert(min(7, len(df.columns)), "Commodity", "HARD")
            df = _with_special_process_calculations(df, paint_df, db)
        elif sheet_name in {"Make", "Buy Std", "Assy"}:
            df = _with_hard_calculations(df, db)
        out = _select_columns(df, layout["columns"].get(sheet_name, []), variants)
        if not out.empty:
            _write_df(wb[sheet_name], out)
            _apply_formula_columns(wb[sheet_name], layout, sheet_name)

    for sheet_name, type_name in (layout.get("bom_type_sheets") or {}).items():
        if sheet_name not in wb.sheetnames:
            continue
        df = _filter_type(bom_df, type_name)
        if sheet_name in {"Buy Std", "Pkg", "Frt"}:
            df = _with_hard_calculations(df, db)
        out = _select_columns(df, layout["columns"].get(sheet_name, []), variants)
        if not out.empty:
            _write_df(wb[sheet_name], out)
            _apply_formula_columns(wb[sheet_name], layout, sheet_name)

    master_output = pd.DataFrame()
    if "Master_Output" in wb.sheetnames:
        master_source = _master_source_df(bom_df, hard)
        if not master_source.empty:
            master_output = _build_master_output_df(master_source, paint_df, variants, db, layout)
            _write_df(wb["Master_Output"], master_output)

    drivers = layout.get("analysis_drivers") or DEFAULT_TEMPLATE["analysis_drivers"]
    if "Analysis" in wb.sheetnames:
        ws = wb["Analysis"]
        for r, label in enumerate(drivers, 1):
            ws.cell(r, 1, label)
        analysis_df = _analysis_df(master_output, variants) if not master_output.empty else pd.DataFrame()
        if not analysis_df.empty:
            _write_df(ws, analysis_df)

    if "End Item Analysis" in wb.sheetnames and "End Item" in hard.columns:
        ws = wb["End Item Analysis"]
        for block, end_item in enumerate(hard["End Item"].dropna().drop_duplicates().tolist()):
            start_col = 1 + block * (len(variants) + 2)
            ws.cell(1, start_col + 1, end_item)
            for r, label in enumerate(drivers, 2):
                ws.cell(r, start_col, label)
            for c, variant in enumerate(variants, start_col + 1):
                ws.cell(2, c, variant)

    _apply_expansion_rules(wb, layout)
    return wb



def _safe_excel_value(value):
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def legacy_pcm_output_filename(bom: Bom, user_id: int | None = None, when=None) -> str:
    hard = _read_df(bom.file_path, "Hard", header=1)
    source = hard if not hard.empty else _read_df(bom.file_path, "BOM", header=0)
    row = source.iloc[0].to_dict() if not source.empty else {}
    timestamp = (when or pd.Timestamp.now()).strftime("%d %b %Y %I.%M.%S %p")
    parts = [
        row.get("PF Code"),
        row.get("Project") or bom.project_name,
        row.get("Part Name"),
        row.get("BC / SBC / TKO / RoCM"),
        timestamp,
        user_id,
    ]
    stem = " ".join(str(p).strip() for p in parts if p not in (None, ""))
    stem = re.sub(r"[^A-Za-z0-9._ -]+", "_", stem).strip(" ._") or "pcm_output"
    return f"{stem}.xlsx"


def _sheet_rows_fast(path: str, sheet: str, max_empty_tail: int = 50):
    try:
        wb = load_workbook(path, data_only=False, read_only=True)
    except Exception:
        return []
    try:
        if sheet not in wb.sheetnames:
            return []
        ws = wb[sheet]
        rows = []
        empty_tail = 0
        for row in ws.iter_rows(values_only=True):
            values = [_safe_excel_value(v) for v in row]
            if any(v not in (None, "") for v in values):
                rows.append(values)
                empty_tail = 0
            elif rows:
                empty_tail += 1
                if empty_tail >= max_empty_tail:
                    break
        return rows
    finally:
        wb.close()


def _write_matrix_xlsxwriter(ws, rows):
    for r_idx, row in enumerate(rows):
        for c_idx, value in enumerate(row):
            if value not in (None, ""):
                ws.write(r_idx, c_idx, value)


def _write_df_xlsxwriter(ws, df: pd.DataFrame, start_row: int = 0, start_col: int = 0):
    for c_idx, col in enumerate(df.columns, start_col):
        ws.write(start_row, c_idx, col)
    for r_offset, row in enumerate(df.itertuples(index=False), start_row + 1):
        for c_idx, value in enumerate(row, start_col):
            value = _safe_excel_value(value)
            if value not in (None, ""):
                ws.write(r_offset, c_idx, value)


def _worksheet_write_formula(ws, row: int, col: int, formula: str):
    ws.write_formula(row - 1, col - 1, formula)


def _worksheet_write_value(ws, row: int, col: int, value):
    if value not in (None, ""):
        ws.write(row - 1, col - 1, value)


def _write_fast_footer_rules(sheets: Dict[str, Any], sheet_sizes: Dict[str, tuple], layout: Dict[str, Any]):
    minimum_rows = layout.get("minimum_rows") or {}
    footer_rules = layout.get("footer_rules") or {}
    for sheet_name, rule in footer_rules.items():
        if sheet_name not in sheets:
            continue
        ws = sheets[sheet_name]
        max_row, max_col = sheet_sizes.get(sheet_name, (1, 1))
        minimum = int(minimum_rows.get(sheet_name) or 0)
        data_last = max(1, min(max_row, minimum - 3) if minimum >= 5 else max_row)
        footer_row = max(minimum, data_last + 3)
        if (rule or {}).get("type") == "variant_sum" and max_col >= 7 and data_last >= 2:
            formula_template = _formula_template(layout, (rule or {}).get("formula_key"))
            if formula_template:
                for col in range(7, max_col + 1):
                    letter = openpyxl.utils.get_column_letter(col)
                    _worksheet_write_formula(ws, footer_row, col, _render_formula(formula_template, variant_col=letter, data_last=data_last))
        elif (rule or {}).get("type") == "pnf_sumproduct":
            formula_template = _formula_template(layout, (rule or {}).get("formula_key"))
            label_col = int(rule.get("label_column") or 7)
            qty_col = int(rule.get("qty_column") or 5)
            basic_col = int(rule.get("basic_cost_column") or 6)
            _worksheet_write_value(ws, footer_row, label_col, rule.get("label") or "Pnf")
            if formula_template and data_last >= 2:
                qty_letter = openpyxl.utils.get_column_letter(qty_col)
                basic_letter = openpyxl.utils.get_column_letter(basic_col)
                for col in range(8, max_col + 1):
                    letter = openpyxl.utils.get_column_letter(col)
                    _worksheet_write_formula(ws, footer_row, col, _render_formula(formula_template, variant_col=letter, qty_col=qty_letter, basic_cost_col=basic_letter, data_last=data_last))
        sheet_sizes[sheet_name] = (max(max_row, footer_row), max_col)




def _legacy_output_template_path() -> Optional[str]:
    """Find the legacy PCM output workbook supplied with the project.

    The old script writes a heavily formatted workbook with formulas spread across
    SBC, Master_Output, Analysis, End Item Analysis and commodity sheets. Until every
    one of those formulas is fully externalized into OutputTemplate JSON, this file
    is the compatibility layout/template so CostOptima does not emit a thin
    placeholder workbook.
    """
    configured = os.getenv("PCM_OUTPUT_TEMPLATE_PATH")
    candidates = []
    if configured:
        candidates.append(configured)
    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            return candidate
    return None


def _copy_cell_format(src, dst):
    if src.has_style:
        dst._style = copy(src._style)
    if src.number_format:
        dst.number_format = src.number_format
    if src.alignment:
        dst.alignment = copy(src.alignment)
    if src.font:
        dst.font = copy(src.font)
    if src.fill:
        dst.fill = copy(src.fill)
    if src.border:
        dst.border = copy(src.border)
    if src.protection:
        dst.protection = copy(src.protection)


def _clear_sheet_values(ws, start_row: int = 2):
    for row in ws.iter_rows(min_row=start_row):
        for cell in row:
            cell.value = None


def _formula_for_row(formula: str, origin: str, target: str) -> str:
    if not isinstance(formula, str) or not formula.startswith("="):
        return formula
    try:
        return Translator(formula, origin=origin).translate_formula(target)
    except Exception:
        return formula


def _write_template_df(ws, df: pd.DataFrame, data_start_row: int = 2, formula_template_row: int = 2, preserve_headers: bool = True, formula_wins: bool = False):
    """Write input-driven rows into a legacy-template sheet.

    Columns whose template row contains formulas keep translated formulas. Columns
    with matching headers receive uploaded-file data. This keeps the old PCM formula
    logic visible in the workbook while making the workbook respond to each upload.
    """
    if df is None:
        df = pd.DataFrame()
    header_map = {str(ws.cell(1, c).value).strip(): c for c in range(1, ws.max_column + 1) if ws.cell(1, c).value not in (None, "")}
    df_cols = {str(c).strip(): c for c in df.columns}
    data_rows = list(df.to_dict("records"))
    target_rows = max(len(data_rows), 1)
    template_cells = []
    for c in range(1, ws.max_column + 1):
        cell = ws.cell(formula_template_row, c)
        template_cells.append({
            "value": cell.value,
            "style": copy(cell._style) if cell.has_style else None,
            "number_format": cell.number_format,
            "alignment": copy(cell.alignment),
            "font": copy(cell.font),
            "fill": copy(cell.fill),
            "border": copy(cell.border),
            "protection": copy(cell.protection),
        })
    current_capacity = max(1, ws.max_row - data_start_row + 1)
    if target_rows > current_capacity:
        insert_at = data_start_row + current_capacity
        ws.insert_rows(insert_at, target_rows - current_capacity)
    _clear_sheet_values(ws, data_start_row)
    for r_offset in range(target_rows):
        row_num = data_start_row + r_offset
        for c in range(1, ws.max_column + 1):
            dst_cell = ws.cell(row_num, c)
            tpl = template_cells[c - 1]
            if tpl["style"] is not None:
                dst_cell._style = copy(tpl["style"])
            dst_cell.number_format = tpl["number_format"]
            dst_cell.alignment = copy(tpl["alignment"])
            dst_cell.font = copy(tpl["font"])
            dst_cell.fill = copy(tpl["fill"])
            dst_cell.border = copy(tpl["border"])
            dst_cell.protection = copy(tpl["protection"])
            template_value = tpl["value"]
            header = str(ws.cell(1, c).value).strip() if ws.cell(1, c).value not in (None, "") else ""
            if isinstance(template_value, str) and template_value.startswith("=") and formula_wins:
                origin = f"{openpyxl.utils.get_column_letter(c)}{formula_template_row}"
                target = f"{openpyxl.utils.get_column_letter(c)}{row_num}"
                dst_cell.value = _formula_for_row(template_value, origin, target)
            elif r_offset < len(data_rows) and header in df_cols:
                dst_cell.value = _safe_excel_value(data_rows[r_offset].get(df_cols[header]))
            elif isinstance(template_value, str) and template_value.startswith("="):
                origin = f"{openpyxl.utils.get_column_letter(c)}{formula_template_row}"
                target = f"{openpyxl.utils.get_column_letter(c)}{row_num}"
                dst_cell.value = _formula_for_row(template_value, origin, target)
            else:
                dst_cell.value = None




def _fill_sbc_from_input(wb, src_wb, end_df: pd.DataFrame, variants: List[str]):
    if "SBC" not in wb.sheetnames:
        return
    ws = wb["SBC"]
    # Preserve template style/formulas, but use the uploaded SBC label row and BOM variants.
    if "SBC" in src_wb.sheetnames:
        src = src_wb["SBC"]
        for c in range(1, min(3, ws.max_column) + 1):
            ws.cell(2, c).value = src.cell(2, c).value
        for idx, variant in enumerate(variants, 4):
            if idx <= ws.max_column:
                ws.cell(1, idx).value = variant
                ws.cell(2, idx).value = src.cell(1, idx).value
    if end_df is None or end_df.empty:
        return
    # Rows 3+ are one per END ITEM, with old template formulas translated across variants.
    data = end_df.drop_duplicates(subset=["End Item"]).to_dict("records")
    for r_offset, row in enumerate(data, 3):
        template_row = 3
        for c in range(1, ws.max_column + 1):
            _copy_cell_format(ws.cell(template_row, c), ws.cell(r_offset, c))
        ws.cell(r_offset, 1).value = row.get("End Item")
        ws.cell(r_offset, 2).value = row.get("Customer No")
        ws.cell(r_offset, 3).value = row.get("Desc")
        for c in range(4, ws.max_column + 1):
            formula = ws.cell(template_row, c).value
            if isinstance(formula, str) and formula.startswith("="):
                origin = f"{openpyxl.utils.get_column_letter(c)}{template_row}"
                target = f"{openpyxl.utils.get_column_letter(c)}{r_offset}"
                ws.cell(r_offset, c).value = _formula_for_row(formula, origin, target)
    for r in range(3 + len(data), ws.max_row + 1):
        for c in range(1, ws.max_column + 1):
            ws.cell(r, c).value = None


def _build_legacy_compatible_workbook(db: Session, bom: Bom, calc_id: int, user_id: int | None = None) -> Optional[str]:
    template_path = _legacy_output_template_path()
    if not template_path:
        return None
    template = get_or_create_pcm_template(db, user_id)
    layout = _normalize_layout(template.layout or {})
    output_dir = os.path.join(settings.UPLOAD_DIR, "outputs")
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, legacy_pcm_output_filename(bom, user_id))

    wb = load_workbook(template_path, data_only=False)
    src_wb = load_workbook(bom.file_path, data_only=False)
    bom_df = _read_df(bom.file_path, "BOM", header=0)
    hard = _read_df(bom.file_path, "Hard", header=1)
    paint_df = _calculated_paint_df(bom.file_path, hard, db)
    variants = _pcm_variants(bom_df, hard, layout)

    if "Paint" in wb.sheetnames and not paint_df.empty:
        _write_template_df(wb["Paint"], _select_columns(paint_df, layout["columns"].get("Paint", []), variants), data_start_row=2)

    end_df = _filter_type(bom_df, "END ITEM")
    if end_df.empty:
        end_df = _filter_type(hard, "END ITEM")
    if end_df.empty and "End Item" in bom_df.columns:
        end_df = bom_df.drop_duplicates(subset=["End Item"]).copy()
        if "Type" not in end_df.columns:
            end_df["Type"] = "END ITEM"
    if not end_df.empty and "End Item" in wb.sheetnames:
        _write_template_df(wb["End Item"], _select_columns(end_df, layout["columns"].get("End Item", []), variants).drop_duplicates(), formula_wins=True)
    _fill_sbc_from_input(wb, src_wb, end_df, variants)

    for sheet_name, cfg in (layout.get("type_filters") or {}).items():
        if sheet_name not in wb.sheetnames:
            continue
        source_name = (cfg.get("source_sheet") or "Hard").strip().lower()
        source_df = bom_df if source_name == "bom" else hard
        df = _filter_type(source_df, cfg.get("type", ""))
        if sheet_name == "Special Process":
            process_col = "Process / Assy Name" if "Process / Assy Name" in source_df.columns else ("Special Process" if "Special Process" in source_df.columns else None)
            if df.empty and process_col:
                df = source_df[source_df[process_col].notna() & (source_df[process_col].astype(str).str.strip() != "")].copy()
            if not df.empty and "Commodity" not in df.columns:
                df.insert(min(7, len(df.columns)), "Commodity", "HARD")
            df = _with_special_process_calculations(df, paint_df, db)
        elif sheet_name in {"Make", "Buy Std", "Assy"}:
            df = _with_hard_calculations(df, db)
        out = _select_columns(df, layout["columns"].get(sheet_name, []), variants)
        _write_template_df(wb[sheet_name], out)

    for sheet_name, type_name in (layout.get("bom_type_sheets") or {}).items():
        if sheet_name not in wb.sheetnames:
            continue
        df = _filter_type(bom_df, type_name)
        if sheet_name in {"Buy Std", "Pkg", "Frt"}:
            df = _with_hard_calculations(df, db)
        out = _select_columns(df, layout["columns"].get(sheet_name, []), variants)
        _write_template_df(wb[sheet_name], out)

    master_output = pd.DataFrame()
    if "Master_Output" in wb.sheetnames:
        master_source = _master_source_df(bom_df, hard)
        if not master_source.empty:
            master_output = _build_master_output_df(master_source, paint_df, variants, db, layout)
            _write_template_df(wb["Master_Output"], master_output)

    wb.save(path)
    return path


def _write_pcm_output_from_legacy_template(calc_id: int) -> Optional[str]:
    # Kept only for compatibility with older imports; dynamic generation uses
    # _build_legacy_compatible_workbook so different uploads produce different output.
    template_path = _legacy_output_template_path()
    if not template_path:
        return None
    output_dir = os.path.join(settings.UPLOAD_DIR, "outputs")
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, legacy_pcm_output_filename(bom, user_id))
    shutil.copyfile(template_path, path)
    return path


def write_pcm_output_file_fast(db: Session, calc_id: int, bom: Bom, user_id: int | None = None) -> str:
    """Fast final-output writer used by calculation.

    This creates the stored workbook directly with xlsxwriter and avoids the slow
    openpyxl workbook-copy/save cycle. It does not create MA/BOP/intermediate
    files; only the final calc output is written.
    """
    template = get_or_create_pcm_template(db, user_id)
    layout = _normalize_layout(template.layout or {})
    output_dir = os.path.join(settings.UPLOAD_DIR, "outputs")
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, legacy_pcm_output_filename(bom, user_id))

    bom_df = _read_df(bom.file_path, "BOM", header=0)
    hard = _read_df(bom.file_path, "Hard", header=1)
    paint_df = _calculated_paint_df(bom.file_path, hard, db)
    variants = _pcm_variants(bom_df, hard, layout)
    master_source = _master_source_df(bom_df, hard)
    master_output = _build_master_output_df(master_source, paint_df, variants, db, layout) if not master_source.empty else pd.DataFrame()

    workbook = xlsxwriter.Workbook(path, {"constant_memory": True, "strings_to_formulas": False})
    blank_format = workbook.add_format({})
    sheets = {}
    sheet_sizes = {}
    for sheet_name in layout.get("sheet_order") or DEFAULT_TEMPLATE["sheet_order"]:
        sheets[sheet_name] = workbook.add_worksheet(str(sheet_name)[:31])
        sheet_sizes[sheet_name] = (1, 1)

    for source_sheet in ("SBC",):
        rows = _sheet_rows_fast(bom.file_path, source_sheet)
        if rows and source_sheet in sheets:
            _write_matrix_xlsxwriter(sheets[source_sheet], rows)
            sheet_sizes[source_sheet] = (len(rows), max((len(r) for r in rows), default=1))

    def write_named_df(sheet_name: str, df: pd.DataFrame):
        if sheet_name in sheets and not df.empty:
            _write_df_xlsxwriter(sheets[sheet_name], df)
            sheet_sizes[sheet_name] = (len(df) + 1, len(df.columns))

    end_df = _filter_type(bom_df, "END ITEM")
    if end_df.empty:
        end_df = _filter_type(hard, "END ITEM")
    if end_df.empty and "End Item" in bom_df.columns:
        end_df = bom_df.drop_duplicates(subset=["End Item"]).copy()
        if "Type" not in end_df.columns:
            end_df["Type"] = "END ITEM"
    if not end_df.empty:
        end_out = _end_item_output_df(end_df, master_output, variants, layout)
        write_named_df("End Item", _select_columns(end_out, layout["columns"].get("End Item", []), variants).drop_duplicates())
    if not paint_df.empty:
        write_named_df("Paint", _select_columns(paint_df, layout["columns"].get("Paint", []), variants))

    for sheet_name, cfg in (layout.get("type_filters") or {}).items():
        source_name = (cfg.get("source_sheet") or "Hard").strip().lower()
        source_df = bom_df if source_name == "bom" else hard
        df = _filter_type(source_df, cfg.get("type", ""))
        if sheet_name == "Special Process":
            process_col = "Process / Assy Name" if "Process / Assy Name" in source_df.columns else ("Special Process" if "Special Process" in source_df.columns else None)
            if df.empty and process_col:
                df = source_df[source_df[process_col].notna() & (source_df[process_col].astype(str).str.strip() != "")].copy()
            if not df.empty and "Commodity" not in df.columns:
                df.insert(min(7, len(df.columns)), "Commodity", "HARD")
            df = _with_special_process_calculations(df, paint_df, db)
        elif sheet_name in {"Make", "Buy Std", "Assy"}:
            df = _with_hard_calculations(df, db)
        out = _select_columns(df, layout["columns"].get(sheet_name, []), variants)
        write_named_df(sheet_name, out)

    for sheet_name, type_name in (layout.get("bom_type_sheets") or {}).items():
        df = _filter_type(bom_df, type_name)
        if sheet_name in {"Buy Std", "Pkg", "Frt"}:
            df = _with_hard_calculations(df, db)
        out = _select_columns(df, layout["columns"].get(sheet_name, []), variants)
        write_named_df(sheet_name, out)

    if "Master_Output" in sheets:
        if not master_source.empty:
            write_named_df("Master_Output", master_output)

    drivers = layout.get("analysis_drivers") or DEFAULT_TEMPLATE["analysis_drivers"]
    if "Analysis" in sheets:
        ws = sheets["Analysis"]
        analysis_df = _analysis_df(master_output, variants) if not master_output.empty else pd.DataFrame()
        if not analysis_df.empty:
            _write_df_xlsxwriter(ws, analysis_df)
            sheet_sizes["Analysis"] = (len(analysis_df) + 1, len(analysis_df.columns))

    end_item_analysis_source = bom_df if not bom_df.empty and "End Item" in bom_df.columns else hard
    if "End Item Analysis" in sheets and "End Item" in end_item_analysis_source.columns:
        ws = sheets["End Item Analysis"]
        max_col = 1
        for block, end_item in enumerate(end_item_analysis_source["End Item"].dropna().drop_duplicates().tolist()):
            start_col = 1 + block * (len(variants) + 2)
            _worksheet_write_value(ws, 1, start_col + 1, end_item)
            for r, label in enumerate(drivers, 2):
                _worksheet_write_value(ws, r, start_col, label)
            for c, variant in enumerate(variants, start_col + 1):
                _worksheet_write_value(ws, 2, c, variant)
                max_col = max(max_col, c)
        sheet_sizes["End Item Analysis"] = (len(drivers) + 1, max_col)

    for sheet_name, minimum in (layout.get("minimum_rows") or {}).items():
        if sheet_name in sheet_sizes:
            rows, cols = sheet_sizes[sheet_name]
            sheet_sizes[sheet_name] = (max(rows, int(minimum or 0)), cols)
    for sheet_name, minimum in (layout.get("minimum_columns") or {}).items():
        if sheet_name in sheet_sizes:
            rows, cols = sheet_sizes[sheet_name]
            target_cols = max(cols, int(minimum or 0))
            sheet_sizes[sheet_name] = (rows, target_cols)
            # xlsxwriter only materializes written cells. Write a harmless blank to the last
            # configured column so Excel/openpyxl report the same output width as the legacy file.
            if target_cols > 0:
                sheets[sheet_name].write_blank(0, target_cols - 1, None, blank_format)
    _write_fast_footer_rules(sheets, sheet_sizes, layout)
    for sheet_name, (rows, cols) in sheet_sizes.items():
        if rows > 0 and cols > 0:
            sheets[sheet_name].write_blank(rows - 1, cols - 1, None, blank_format)
    workbook.close()
    return path

def write_pcm_output_file(db: Session, calc_id: int, bom: Bom, user_id: int | None = None) -> str:
    legacy_path = _build_legacy_compatible_workbook(db, bom, calc_id, user_id)
    if legacy_path:
        return legacy_path
    return write_pcm_output_file_fast(db, calc_id, bom, user_id)






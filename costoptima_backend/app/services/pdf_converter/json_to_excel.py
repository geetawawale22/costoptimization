# 27 - 2 4:23 
# -*- coding: utf-8 -*-

import os
import sys
import re
import io
import json
import time
import gc
import tempfile
import openpyxl
from pathlib import Path
from PIL import Image as PILImage
from openpyxl.drawing.image import Image as OpenpyxlImage
from openpyxl.utils import get_column_letter, column_index_from_string
from openpyxl.utils.cell import coordinate_from_string
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.styles import Alignment, Border, Side
from argparse import ArgumentParser
from difflib import SequenceMatcher
from copy import copy


# ---------------- FILE PATHS (edit these defaults) ----------------

#TEMPLATE_FILE = r"D:\PCM\Shreyas\pdf\new\New folder\to_mail\to_mail\updated code\MSS_FILLED.xlsx"
#JSON_FOLDER   = r"D:\PCM\Shreyas\pdf\new\New folder\to_mail\to_mail\updated code"
#OUTPUT_FILE   = r"D:\PCM\Shreyas\pdf\new\New folder\to_mail\to_mail\updated code\MSS.xlsx"

TEMPLATE_FILE = r"D:\PCM\Shreyas\pdf\new\pdf Files\Template.xlsx"
JSON_FOLDER   = r"D:\PCM\Shreyas\pdf\new\pdf Files"
OUTPUT_FILE   = r"D:\PCM\Shreyas\pdf\new\pdf Files\MSS.xlsx"

#os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)


# ---------------- SAFE GET ----------------
def safe_get(data, section, key_path):
    """
    If 'section' not present, look in root (your JSONs are mostly flat).
    key_path supports 'a/b/c' traversal.
    """
    section_data = data.get(section, data)
    for k in key_path.split("/"):
        if isinstance(section_data, dict):
            section_data = section_data.get(k, "")
        else:
            return ""
    if section_data is None:
        return ""
    if isinstance(section_data, (dict, list)):
        return json.dumps(section_data, ensure_ascii=False)
    return section_data


# ---------------- VALUE SANITIZATION HELPERS ----------------
def _is_numeric_like(s: str) -> bool:
    """
    Returns True if the given string looks like a number:
    - allows leading/trailing spaces
    - allows comma separators
    - supports integers/floats (e.g., '  1,200.50 ')
    """
    if s is None or not isinstance(s, str):
        return False
    try:
        float(s.replace(",", "").strip())
        return True
    except Exception:
        return False

def strip_if_numeric(value):
    """
    If value is a string representing a number, strip() it.
    Otherwise return as-is.
    """
    if isinstance(value, str) and _is_numeric_like(value):
        return value.strip()
    return value


# ---------------- MERGE-AWARE WRITE HELPERS ----------------
def top_left_of_merge(ws, cell):
    """Return top-left cell for a merged range containing 'cell', else 'cell'."""
    for rng in ws.merged_cells.ranges:
        if cell.coordinate in rng:
            min_col, min_row, _, _ = rng.bounds
            return ws.cell(row=min_row, column=min_col)
    return cell

def safe_write_addr(ws, address, value):
    """Write to an address; if merged, write to merge's top-left. Center-align."""
    # Sanitize numeric-like strings by stripping whitespace
    value = strip_if_numeric(value)
    cell = ws[address]
    tl = top_left_of_merge(ws, cell)
    tl.value = value
    tl.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    tl.border = Border(left=Side(style='thin'), right=Side(style='thin'),
                       top=Side(style='thin'), bottom=Side(style='thin'))

def safe_write(ws, row, col, value):
    """Write to row/col; if merged, write to merge's top-left. Center-align."""
    # Sanitize numeric-like strings by stripping whitespace
    value = strip_if_numeric(value)
    cell = ws.cell(row=row, column=col)
    tl = top_left_of_merge(ws, cell)
    tl.value = value
    tl.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    tl.border = Border(left=Side(style='thin'), right=Side(style='thin'),
                       top=Side(style='thin'), bottom=Side(style='thin'))


# ---------------- IMAGE HELPERS ----------------
def _approx_box_pixels(ws, start_col, start_row, width_cells, height_cells):
    """Approximate width/height in pixels for a W x H cell box (Excel heuristics)."""
    total_width = 0
    for c in range(start_col, start_col + width_cells):
        cd = ws.column_dimensions.get(get_column_letter(c))
        w = (cd.width if cd and cd.width else 10) * 7         # ≈7 px per column unit
        total_width += w
    total_height = 0
    for r in range(start_row, start_row + height_cells):
        rd = ws.row_dimensions.get(r)
        h = (rd.height if rd and rd.height else 15) * 1.33    # ≈1.33 px per row unit
        total_height += h
    return int(total_width), int(total_height)

def insert_image_fit_box(ws, image_path, anchor_cell, width_cells=4, height_cells=4):
    """Insert an image at anchor_cell, scaled to fit width_cells x height_cells."""
    if not image_path or not os.path.exists(image_path):
        return False
    img = PILImage.open(image_path)
    orig_w, orig_h = img.size

    col_letter, start_row = coordinate_from_string(anchor_cell)
    start_col = column_index_from_string(col_letter)
    box_w, box_h = _approx_box_pixels(ws, start_col, start_row, width_cells, height_cells)

    if box_w <= 0 or box_h <= 0:
        return False

    scale = min(box_w / float(orig_w), box_h / float(orig_h))
    if scale <= 0:
        return False

    img = img.resize((int(orig_w * scale), int(orig_h * scale)), PILImage.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)

    ws.add_image(OpenpyxlImage(buf), anchor_cell)
    return True

def materialize_base64_to_file(b64, out_dir: Path, filename_hint: str) -> str:
    """Write base64 PNG bytes to disk for Excel insertion; return the new path."""
    try:
        import base64
        out_dir.mkdir(parents=True, exist_ok=True)
        p = out_dir / f"{filename_hint}.png"
        with open(p, "wb") as f:
            f.write(base64.b64decode(b64))
        return str(p)
    except Exception:
        return ""
#------------------------------for formatting EXCLUSIVE BOP--------------#
def merge_and_copy_style(
    ws,
    row,
    start_col,
    end_col,
    align="left"
):
    """
    Merge cells in a single row and copy style from the starting cell.

    :param ws: worksheet object
    :param row: row number (e.g. 35)
    :param start_col: starting column number (e.g. 2 for B)
    :param end_col: ending column number (e.g. 11 for K)
    :param align: 'left', 'center', or 'right'
    """

    src_cell = ws.cell(row=row, column=start_col)
    value = src_cell.value

    ws.merge_cells(
        start_row=row,
        start_column=start_col,
        end_row=row,
        end_column=end_col
    )

    merged_cell = ws.cell(row=row, column=start_col)
    merged_cell.value = value

    # Copy styles
    merged_cell.font = copy(src_cell.font)
    merged_cell.border = copy(src_cell.border)
    merged_cell.fill = copy(src_cell.fill)
    merged_cell.number_format = copy(src_cell.number_format)
    merged_cell.protection = copy(src_cell.protection)

    # Alignment (preserve vertical, override horizontal)
    merged_cell.alignment = Alignment(
        horizontal=align,
        vertical=src_cell.alignment.vertical or "center",
        wrap_text=src_cell.alignment.wrap_text,
    )
# ---------------- SAFE SAVE (avoid PermissionError) ----------------
def safe_save_workbook(wb, final_path: str, retries: int = 6, delay_sec: float = 1.5):
    """
    Save workbook safely:
      - write to temp file
      - atomically replace destination
      - retry on PermissionError (file temporarily locked by Excel/AV)
    """
    final_path = str(final_path)
    folder = os.path.dirname(final_path) or "."
    os.makedirs(folder, exist_ok=True)

    for attempt in range(1, retries + 1):
        tmp_name = None
        try:
            with tempfile.NamedTemporaryFile(prefix="~tmp_", suffix=".xlsx", dir=folder, delete=False) as tmp:
                tmp_name = tmp.name
            wb.save(tmp_name)
            os.replace(tmp_name, final_path)
            return True
        except PermissionError:
            if tmp_name and os.path.exists(tmp_name):
                try: os.remove(tmp_name)
                except Exception: pass
            if attempt == retries:
                raise
            time.sleep(delay_sec)
        except Exception:
            if tmp_name and os.path.exists(tmp_name):
                try: os.remove(tmp_name)
                except Exception: pass
            raise
    return False


# ---------------- MAPPING (value cells only; do NOT touch labels) ----------------
# Template.xlsx layout: labels are in col B, values go in col C (and labels in
# col F have values in col G, labels in col J have values in col K, etc.).
# Compare with old MSS_FILLED.xlsx where labels were in col A and values in col B.
mapping = [
    ("C2",  "PROJECT_DETAILS", "Project Name"),
    ("G2",  "PROJECT_DETAILS", "Estimation Type"),
    ("K2",  "PROJECT_DETAILS", "Date"),

    ("C3",  "PROJECT_DETAILS", "SOR/BTP/CMTCP No."),
    ("G3",  "PROJECT_DETAILS", "Assembly Name"),

    ("C5",  "PART_DETAILS",    "Part Name"),
    ("C6",  "PART_DETAILS",    "Part Finish"),
    ("C7",  "PART_DETAILS",    "Part Accuracy"),
    ("C8",  "PART_DETAILS",    "Part Material (Grade)"),
    ("C9",  "PART_DETAILS",    "Part Weight (KG)"),
    ("C10", "PART_DETAILS",    "Tonnage"),   # Machine Tonnage (T)

    ("C12", "MOLD_DETAILS",    "Mold Mfg Location"),
    ("G12", "MOLD_DETAILS",    "Supplier Class"),
    ("K12", "MOLD_DETAILS",    "No. of Trials"),

    ("C13", "MOLD_DETAILS",    "Mold weight (KG)"),
    ("G13", "MOLD_DETAILS",    "No of Cavity"),
    ("K13", "MOLD_DETAILS",    "No of Parts"),

    ("C14", "MOLD_DETAILS",    "Mold Type"),
    ("G14", "MOLD_DETAILS",    "Cavity Orientation"),
    ("K14", "MOLD_DETAILS",    "Cost of Spares"),

    ("C15", "MOLD_DETAILS",    "RS Type"),
    ("G15", "MOLD_DETAILS",    "Ejection Type"),
    ("K15", "MOLD_DETAILS",    "No of Eye Bolt"),

    ("C16", "MOLD_DETAILS",    "No of Drops"),
    ("E16", "MOLD_DETAILS",    "ZPL"),
    ("G16", "MOLD_DETAILS",    "No.of Hydraulic Cylinder for Ejection"),
    ("K16", "MOLD_DETAILS",    "Type Of Eye Bolt"),

    ("C17", "MOLD_DETAILS",    "Parting line Requirement"),
    ("E17", "MOLD_DETAILS",    "Cavity Ejection/ Floating Insert"),
    ("G17", "MOLD_DETAILS",    "Gas Spring"),
    ("K17", "MOLD_DETAILS",    "No of Cylinders For Sliders"),
]


# ---------------- CLI ----------------
def make_cli():
    p = ArgumentParser(description="Push JSON to Excel with multi-image grid insertion.")
    p.add_argument("--template", default=TEMPLATE_FILE)
    p.add_argument("--json-dir", default=JSON_FOLDER)
    p.add_argument("--out",      default=OUTPUT_FILE)
    p.add_argument("--debug",    action="store_true")

    # image grid controls
    p.add_argument("--image-cell", default="E5",
                   help='Top-left cell to start placing images (default: "E5")')
    p.add_argument("--image-span", nargs=2, type=int, metavar=("W_CELLS", "H_CELLS"),
                   default=[4, 4], help="Each image fits into W x H cells (default: 4 4)")
    p.add_argument("--grid-cols", type=int, default=2,
                   help="Number of image boxes per row (default: 2)")
    p.add_argument("--max-images", type=int, default=4,
                   help="Max images to place per sheet (default: 4)")
    p.add_argument("--no-prefer-left", action="store_true",
                   help="Do not prefer the left-of-label image; keep input order")
    p.add_argument("--cache-dir", default="images_cache",
                   help="Folder to write base64 images when 'path' is missing (default: images_cache)")
    return p


# ---------------- NEW: sheet name helper (sanitize, 31-char, unique) ----------------
def make_unique_sheet_name(wb, base_name: str) -> str:
    """
    Returns a valid, unique Excel sheet name based on 'base_name':
      - remove invalid chars \ / : * ? [ ]
      - trim to 31 chars
      - ensure uniqueness in the workbook by appending _2, _3, ...
    """
    for ch in '\\/:*?[]':
        base_name = base_name.replace(ch, "_")
    base_name = base_name.strip() or "Sheet"

    base_name = base_name[:31]

    if base_name not in wb.sheetnames:
        return base_name

    for i in range(2, 1000):
        suffix = f"_{i}"
        allowed = 31 - len(suffix)
        candidate = (base_name[:allowed] + suffix)
        if candidate not in wb.sheetnames:
            return candidate

    raise ValueError("Unable to create a unique sheet name.")

#----------- for cavity rating ------------
def find_text(ws, text):
    text = text.strip().lower()
    for row in ws.iter_rows():
        for cell in row:
            if isinstance(cell.value, str) and cell.value.strip().lower() == text:
                return cell.row, cell.column
    return None, None

#------------------helper for material grade ------------
def material_grade_parts(text,height=1):
    MaterialGradeList = ['P20 1.2738','P20 1.2738HH','P20 1.2738TSHH','1.2738 HHHG','P20 1.2311','P20 1.2711','P20 1.2344','C 45','Stavax','Cu','P20 1.2343','NAK 80','Diamond HHH','Graphite','AL Casting (with Pattern)','DPX1']
    #print('text: ',text)
    if not text or len(str(text).strip()) == 0:
        return "C 45"
    text = str(text).strip()
    if 'TSHH' in text:
        text = 'TSHH'
    for material in MaterialGradeList:
        material1 = re.sub(r"[ .]","",material)
        if re.sub(r"[ .]","",text) in material1:
            if  material == 'P20 1.2738TSHH':
                height = height or 0
                if int(height)>500:
                    return "P20 1.2738TSHH (>500 Size)"
                else:
                    return "P20 1.2738TSHH (<500 Size)"
            return material
    # If no exact/substring match found, try fuzzy match against dropdown values
    matched = fuzzy_match_dropdown(text, DROPDOWN_MATERIAL_GRADES, threshold=0.45)
    if matched:
        return matched
    # No match at all -> return blank (per user requirement)
    return ""


# ---------- DROPDOWN LISTS (from DROP_DOWN_TAB) ----------
DROPDOWN_MATERIAL_GRADES = [
    'P20 1.2738', 'P20 1.2738TSHH (<500 Size)', 'P20 1.2738TSHH (>500 Size)',
    '1.2738 HHHG', 'P20 1.2311', 'P20 1.2711', 'P20 1.2344', 'C 45',
    'Stavax', 'Cu', 'P20 1.2343', 'NAK 80', 'Diamond HHH', 'Graphite',
    'AL Casting (with Pattern)', 'DPX1'
]


def fuzzy_match_dropdown(value, dropdown_list, threshold=0.4):
    """
    Match a value against a dropdown list using fuzzy matching.
    Returns the best match if ratio >= threshold, else returns ''.
    """
    if not value or not isinstance(value, str) or not value.strip():
        return ""
    value_clean = value.strip()
    # Exact match first
    for item in dropdown_list:
        if item.lower() == value_clean.lower():
            return item
    # Substring match
    val_norm = re.sub(r"[ .]", "", value_clean.lower())
    for item in dropdown_list:
        item_norm = re.sub(r"[ .]", "", item.lower())
        if val_norm in item_norm or item_norm in val_norm:
            return item
    # Fuzzy ratio match
    best_match = ""
    best_ratio = 0.0
    for item in dropdown_list:
        ratio = SequenceMatcher(None, value_clean.lower(), item.lower()).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_match = item
    if best_ratio >= threshold:
        return best_match
    return ""


def add_data_validations(ws):
    """
    Add Excel data validation (dropdown) lists to relevant cells,
    referencing the DROP_DOWN_TAB sheet for choices.
    """
    # Material Grade validation for table column D (rows 19 onwards)
    dv_material = DataValidation(
        type="list",
        formula1="=DROP_DOWN_TAB!$B$83:$B$99",
        allow_blank=True
    )
    dv_material.error = "Please select a valid Material Grade"
    dv_material.errorTitle = "Invalid Material Grade"
    dv_material.prompt = "Select a Material Grade"
    dv_material.promptTitle = "Material Grade"
    # Apply to D19:D50 to cover all possible table rows
    dv_material.add("D19:D50")
    ws.add_data_validation(dv_material)

    # Estimation Type
    dv_est = DataValidation(type="list", formula1="=DROP_DOWN_TAB!$B$3:$B$5", allow_blank=True)
    dv_est.add("G2")
    ws.add_data_validation(dv_est)

    # Part Finish
    dv_finish = DataValidation(type="list", formula1="=DROP_DOWN_TAB!$B$10:$B$14", allow_blank=True)
    dv_finish.add("C6")
    ws.add_data_validation(dv_finish)

    # Part Accuracy
    dv_accuracy = DataValidation(type="list", formula1="=DROP_DOWN_TAB!$B$19:$B$23", allow_blank=True)
    dv_accuracy.add("C7")
    ws.add_data_validation(dv_accuracy)

    # Part Material (Grade)
    dv_part_mat = DataValidation(type="list", formula1="=DROP_DOWN_TAB!$B$27:$B$36", allow_blank=True)
    dv_part_mat.add("C8")
    ws.add_data_validation(dv_part_mat)

    # Mold Mfg Location
    dv_location = DataValidation(type="list", formula1="=DROP_DOWN_TAB!$B$40:$B$43", allow_blank=True)
    dv_location.add("C12")
    ws.add_data_validation(dv_location)

    # Supplier Class
    dv_supplier = DataValidation(type="list", formula1="=DROP_DOWN_TAB!$B$106:$B$108", allow_blank=True)
    dv_supplier.add("G12")
    ws.add_data_validation(dv_supplier)

    # Mold Type (CRS/HRS)
    dv_mold_type = DataValidation(type="list", formula1="=DROP_DOWN_TAB!$B$46:$B$47", allow_blank=True)
    dv_mold_type.add("C14")
    ws.add_data_validation(dv_mold_type)

    # RS TYPE
    dv_rs = DataValidation(type="list", formula1="=DROP_DOWN_TAB!$B$50:$B$58", allow_blank=True)
    dv_rs.add("C15")
    ws.add_data_validation(dv_rs)

    # Cavity Orientation
    dv_cav_orient = DataValidation(type="list", formula1="=DROP_DOWN_TAB!$B$65:$B$67", allow_blank=True)
    dv_cav_orient.add("G14")
    ws.add_data_validation(dv_cav_orient)

    # Ejection Type
    dv_eject = DataValidation(type="list", formula1="=DROP_DOWN_TAB!$B$70:$B$71", allow_blank=True)
    dv_eject.add("G15")
    ws.add_data_validation(dv_eject)

    # Gas Spring
    dv_gas = DataValidation(type="list", formula1="=DROP_DOWN_TAB!$B$74:$B$75", allow_blank=True)
    dv_gas.add("G17")
    ws.add_data_validation(dv_gas)

    # Type Of Eye Bolt
    dv_eye = DataValidation(type="list", formula1="=DROP_DOWN_TAB!$B$78:$B$80", allow_blank=True)
    dv_eye.add("K16")
    ws.add_data_validation(dv_eye)

    # Parting line Requirement
    dv_parting = DataValidation(type="list", formula1="=DROP_DOWN_TAB!$B$61:$B$62", allow_blank=True)
    dv_parting.add("C17")
    ws.add_data_validation(dv_parting)

    # Latches
    dv_latch = DataValidation(type="list", formula1="=DROP_DOWN_TAB!$B$102:$B$103", allow_blank=True)
    dv_latch.add("C32")
    ws.add_data_validation(dv_latch)

    # ZPL
    dv_Zpl = DataValidation(type="list", formula1="=DROP_DOWN_TAB!$B$74:$B$75", allow_blank=True)
    dv_Zpl.add("E16")
    ws.add_data_validation(dv_Zpl)

    # Cavity Ejection/ Floating Insert
    dv_CEFI = DataValidation(type="list", formula1="=DROP_DOWN_TAB!$B$74:$B$75", allow_blank=True)
    dv_CEFI.add("E17")
    ws.add_data_validation(dv_CEFI)

    # Part Section View
    dv_Psv = DataValidation(type="list", formula1="=DROP_DOWN_TAB!$B$112:$B$114", allow_blank=True)
    dv_Psv.add("J26")
    ws.add_data_validation(dv_Psv)

    # Housing and Base Material
    dv_HBM = DataValidation(type="list", formula1="==DROP_DOWN_TAB!$B$83:$B$99", allow_blank=True)
    dv_HBM.add("K20")
    ws.add_data_validation(dv_HBM)

# ---------------- MAIN ----------------
def main():
    args = make_cli().parse_args()

    wb_template = openpyxl.load_workbook(args.template)
    # The Template.xlsx has two sheets: DROP_DOWN_TAB (dropdown data) and
    # the actual data sheet.  We must use the DATA sheet (the second one),
    # NOT wb_template.active which gives us the dropdown tab.
    data_sheet_names = [s for s in wb_template.sheetnames if s != 'DROP_DOWN_TAB']
    if data_sheet_names:
        template_ws = wb_template[data_sheet_names[0]]
    else:
        template_ws = wb_template.active

    json_files = sorted([p for p in Path(args.json_dir).glob("*.json")])
    if not json_files:
        print(f"❌ No JSON files in: {args.json_dir}")
        return

    total_files = len(json_files)
    for idx, json_path in enumerate(json_files, 1):
        print(f"📊 Processing {idx}/{total_files}: {json_path.stem}...", file=sys.stderr)
        data = json.loads(Path(json_path).read_text(encoding="utf-8"))

        # -------- Sheet name from Part Name (fallback to filename) --------
        part_name = str(safe_get(data, "PART_DETAILS", "Part Name")).strip()
        sheet_base = part_name if part_name else json_path.stem
        sheet_name = make_unique_sheet_name(wb_template, sheet_base)

        # Duplicate template
        ws = wb_template.copy_worksheet(template_ws)
        ws.title = sheet_name

        # Add data validation dropdowns referencing DROP_DOWN_TAB
        add_data_validations(ws)

        # -------- Fill high-level fields (value cells only) --------
        for value_cell, section, json_key in mapping:
            val = safe_get(data, section, json_key)

            # --- Business Logic Translations from logic 2.xlsx & Copy of Logic.xlsx ---
            if json_key == "Project Name" and val:
                import re
                val = str(val)#re.sub(r'[^A-Za-z0-9]', '', str(val))
            elif json_key == "Parting line Requirement" and not val:
                val = "Core + Cavity"
            elif json_key == "ZPL":
                val = "No"
            elif json_key == "Cavity Ejection/ Floating Insert":
                val = "No"
            elif json_key == "Mold Mfg Location" and not val:
                val = "INDIA"
            elif json_key == "Part Accuracy" and not val:
                val = "Moderate Precision"

            # Part Finish Rules
            if json_key == "Part Finish":
                raw_finish = str(safe_get(data, "", "Part Finish Raw")).lower()
                val_str = str(val).lower() if val else ""
                combined_check = raw_finish + " " + val_str
                if "grain" in combined_check or "mic" in combined_check or "yes" in combined_check:
                    val = "Painted Parts"  # Updated as per Logic Painted Parts
                elif "paint" in combined_check:
                    val = "Painted Parts"
                elif "chrome" in combined_check or "no" in combined_check:
                    val = "Mirror Finish / High Gloss"

            # Mold Type Rules (CRS vs HRS)
            if json_key == "Mold Type":
                val_str = str(val).lower() if val else ""
                if "cold" in val_str or "crs" in val_str:
                    val = "Cold Sprue"
                elif "svg" in val_str or "hot" in val_str or "open" in val_str or "manifold" in val_str:
                    val = "Hot Sprue"  # Default HRS fallback, RS Type defines details

            # RS Type Rules
            if json_key == "RS Type":
                val_str = str(val).lower() if val else ""
                mold_type_str = str(safe_get(data, "MOLD_DETAILS", "Mold Type")).lower()
                no_of_drops = safe_get(data, "MOLD_DETAILS", "No of Drops")
                
                try:
                    num_drops = int(str(no_of_drops).strip())
                except:
                    num_drops = 0

                if "crs" in mold_type_str or "cold" in mold_type_str:
                    val = "Cold Sprue"
                elif num_drops > 1:
                    val = "Multiple Drops Open Type"
                elif "svg" in val_str:
                    val = "Seq Moulds/Per Drop"
                elif "open" in val_str:
                    val = "Multiple Drops Open Type"
                elif "manifold" in val_str:
                    val = "Single Drop L Manifold"
                elif "hot" in val_str:
                    val = "Hot Sprue"

            # Ejection Type Rules
            if json_key == "Ejection Type":
                val_str = str(val).lower() if val else ""
                if "double" in val_str:
                    val = "Double"
                else:
                    val = "Single"

            # Gas Spring Rules
            if json_key == "Gas Spring":
                val_str = str(val).lower() if val else ""
                if "gas spring" in val_str and "no" not in val_str:
                    val = "Yes"
                else:
                    val = "No"

            #No.of Hydraulic Cylinder for Ejection
            if json_key == "No.of Hydraulic Cylinder for Ejection":
                val = safe_get(data, "", "No.of Hydraulic Cylinder for Ejection")
                safe_write_addr(ws, value_cell, val)

            # Mold Weight rules
            if json_key == "Mold weight (KG)":
                val = safe_get(data, "", "Mold weight (KG)")
                safe_write_addr(ws, value_cell, val)
                '''
                mold_size_str = safe_get(data, "", "Mold Size")
                if mold_size_str:
                    import re
                    # Look for L x W x H
                    dims = re.findall(r'\d+(?:\.\d+)?', mold_size_str)
                    if len(dims) >= 3:
                        try:
                            l, w, h = float(dims[0]), float(dims[1]), float(dims[2])
                            calculated_weight = l * w * h * 7.8e-6 * 0.75
                            val = round(calculated_weight / 10) * 10
                            safe_write_addr(ws, value_cell, val)
                            continue
                        except:
                            pass'''

            # Type Of Eye Bolt Rules
            if json_key == "Type Of Eye Bolt":
                if not val:
                    tonnage_val = str(safe_get(data, "PART_DETAILS", "Tonnage")).strip()
                    try:
                        tonnage = float(tonnage_val.replace(",", ""))
                        if tonnage < 500:
                            val = "LOCAL"
                        elif tonnage <= 1500:
                            val = "SWIVEL"
                        else:
                            val = "SWIVEL_LARGE"
                    except:
                        val = "SWIVEL" # fallback

            # Strip if numeric-like string
            # Skip strip_if_numeric for Mold weight so we don't clobber the calculated value if it's an int/float
            if json_key != "Mold weight (KG)":
                val = strip_if_numeric(val)

            # Special handling for C10 (Tonnage) if numeric-like: coerce to int/float
            if value_cell == "C10" and val != "":
                try:
                    num = float(str(val).replace(",", "").strip())
                    val = int(num) if num.is_integer() else num
                except Exception:
                    # leave as-is if conversion fails
                    pass

            safe_write_addr(ws, value_cell, val)

            # Also write B10 value (Tonnage) by address since the key check above
            # used value_cell == "B10" which is now "C10"
        #------------------material garde values--------------    
        Core_material_grade = data.get("Core_material_grade","")
        Cavity_material_grade = data.get("Cavity_material_grade","")
        Sliders_material_grade = data.get("Sliders_material_grade","")
        Lifters_material_grade = data.get("Lifters_material_grade","")

        # -------- Housing and Base Material (K20) --------
        # Default to "C 45" if no value extracted; if a value exists,
        # fuzzy-match it against the dropdown Material Grade list.
        housing_base_raw = str(data.get("Housing_base_material", "") or "").strip()
        if not housing_base_raw:
            housing_base_val = "C 45"
        else:
            matched = fuzzy_match_dropdown(housing_base_raw, DROPDOWN_MATERIAL_GRADES, threshold=0.4)
            housing_base_val = matched if matched else "C 45"
        safe_write_addr(ws, "K20", housing_base_val)
        
        # ---------------- TABLE DATA ----------------
        # Template already has headers at row 18 (B18..H18). Do NOT overwrite them.
        # Table columns in new template: B=Sr.No, C=Details, D=Material Grade,
        # E=Size L, F=Size W, G=Size H, H=Quantity
        current_row = 19   # data starts at row 19
        sr = 1

        # Build table sequence
        cc = data.get("Core/Cavity Sizes", {}) or {}

        # --- normalize cavity/core insert defaults to 0x0x0 and qty 0 ---
        ci_value = cc.get("Cavity Insert", "")
        ci_qty   = cc.get("Cavity Insert Quantity", None)
        if not ci_value or str(ci_value).strip().lower() in ("1x1x1",):
            ci_value = "0x0x0"
            ci_qty = 0
        elif ci_qty is None:
            ci_qty = 0  # if value provided but qty missing, still default to 0

        corei_value = cc.get("Core Insert", "")
        corei_qty   = cc.get("Core Insert Quantity", None)
        if not corei_value or str(corei_value).strip().lower() in ("1x1x1",):
            corei_value = "0x0x0"
            corei_qty = 0
        elif corei_qty is None:
            corei_qty = 0

        fixed_sequence = [
            ("Part Size",     data.get("Part Size", ""), ""),
            ("Mold Size",     data.get("Mold Size", ""), ""),
            ("Cavity Block Size",  cc.get("Cavity Block", ""), cc.get("Cavity Quantity", 1)),
            ("Core Block Size",    cc.get("Core Block",   ""), cc.get("Core Quantity",   1)),
            ("Cavity Insert Size", ci_value, ci_qty),
            ("Core Insert Size",   corei_value, corei_qty),
        ]
        
        # ---------------- Lifters/Sliders ----------------
        lifter_slots = []
        slider_slots = []

        # Robust regex pattern for dimension parsing - handles many formats:
        # "700x550x500, 2nos", "150x100x80-6 nos", "300x200x200-qty2", "500x50x100-2no"
        import re
        dim_qty_pattern = re.compile(
            r'(\d+(?:\.\d+)?)\s*[xX×*]\s*(\d+(?:\.\d+)?)\s*[xX×*]\s*(\d+(?:\.\d+)?)'
            r'\s*(?:mm)?\s*[-–—,\s]*(?:qty\.?\s*)?(\d+)\s*(?:nos?|no\.?)?',
            re.IGNORECASE
        )

        # Parse lifter/slider sizes from the JSON data
        lifter_sizes_raw = data.get("Lifter Sizes", None)
        if isinstance(lifter_sizes_raw, str):
            try:
                lifter_sizes_raw = json.loads(lifter_sizes_raw)
            except Exception:
                lifter_sizes_raw = {}
        if not isinstance(lifter_sizes_raw, dict):
            lifter_sizes_raw = {}

        # Get the total counts from the JSON
        try:
            no_of_lifters_total = int(data.get("No of Lifters") or 0)
        except Exception:
            no_of_lifters_total = 0

        try:
            no_of_sliders_total = int(data.get("No of Sliders") or 0)
        except Exception:
            no_of_sliders_total = 0


        for k, value in lifter_sizes_raw.items():
            k_lower = k.lower()
            val_str = str(value)
            # Split on comma to handle multiple entries
            entries = [v.strip() for v in val_str.split(",")]
            c=0
            for entry in entries:
                
                m = dim_qty_pattern.search(entry)
                if not m:
                    continue
                L, W, H, Qty = m.group(1), m.group(2), m.group(3), m.group(4)
                # Determine if slider or lifter - handle spelling variations
                # "slider", "lider", "slder" -> slider
                # "lifter", "ifter", "lfter", "cap lifter", "wing lifter" -> lifter
                combined = (k_lower + " " + entry.lower()).strip()
                is_slider = bool(re.search(r's?l[iy]?d[eé]?r|slder', combined, re.I))
                '''if is_slider and no_of_sliders_total != 0:
                    slider_slots.append({"L": L, "W": W, "H": H, "Qty": Qty}) #no_of_lifters_total #no_of_sliders_total
                else:
                    if no_of_lifters_total != 0:
                        lifter_slots.append({"L": L, "W": W, "H": H, "Qty": Qty}) #no_of_sliders_total'''
                #c+=1
                #if c>0:
                #    break    
                
                if is_slider:
                    slider_slots.append({"L": L, "W": W, "H": H, "Qty": Qty})
                else:
                    lifter_slots.append({"L": L, "W": W, "H": H, "Qty": Qty})

        # Get the total counts from the JSON
        try:
            no_of_lifters_total = int(data.get("No of Lifters") or 0)
        except Exception:
            no_of_lifters_total = 0

        try:
            no_of_sliders_total = int(data.get("No of Sliders") or 0)
        except Exception:
            no_of_sliders_total = 0

        if no_of_lifters_total == 0:
            lifter_slots.append({"L": "", "W": "", "H": "", "Qty": ""})
        if  no_of_sliders_total == 0:
            slider_slots.append({"L": "", "W": "", "H": "", "Qty": ""})   

        # Step 4: Balance logic
        # Sum up parsed quantities
        try:
            parsed_lifter_qty = sum(int(slot["Qty"]) for slot in lifter_slots)
            parsed_slider_qty = sum(int(slot["Qty"]) for slot in slider_slots)
        except:
            parsed_lifter_qty = 0
            parsed_slider_qty = 0   
        
        # If total count > parsed qty, add ONE default row with the balance
        lifter_balance = no_of_lifters_total - parsed_lifter_qty
        slider_balance = no_of_sliders_total - parsed_slider_qty

        if lifter_balance > 0:
            lifter_slots.append({"L": "50", "W": "50", "H": "50", "Qty": str(lifter_balance)})

        if slider_balance > 0:
            slider_slots.append({"L": "50", "W": "50", "H": "50", "Qty": str(slider_balance)})

        # Ensure at least 3 rows each (template default)
        while len(lifter_slots) < 3:
            lifter_slots.append(None)
        while len(slider_slots) < 3:
            slider_slots.append(None)

        total_lifter_rows = len(lifter_slots)
        total_slider_rows = len(slider_slots)

        # Calculate extra rows needed beyond the 3 default rows in the template
        extra_lifters = max(0, total_lifter_rows - 3)
        extra_sliders = max(0, total_slider_rows - 3)
        total_extra_rows = extra_lifters + extra_sliders

        if total_extra_rows > 0:
            # Unmerge any merged cells in the insertion zone to prevent corruption
            insert_point = 31  # row 31 is EXCLUSIVE BOP in the template
            merged_to_remove = []
            for merged_range in ws.merged_cells.ranges:
                if merged_range.min_row <= insert_point + total_extra_rows and merged_range.max_row >= insert_point:
                    merged_to_remove.append(str(merged_range))
            for mr in merged_to_remove:
                try:
                    ws.unmerge_cells(mr)
                except Exception:
                    pass
            ws.insert_rows(insert_point, amount=total_extra_rows)

        # Build the lifter/slider sequence for the table
        for i in range(total_lifter_rows):
            fixed_sequence.append((f"Lifter  {i+1}", None, None))
        if total_lifter_rows == 0:
            for i in range(3):
                fixed_sequence.append(("", None, None))

        for i in range(total_slider_rows):
            fixed_sequence.append((f"Slider {i+1}", None, None))
        if total_slider_rows == 0:
            for i in range(3):
                fixed_sequence.append(("", None, None))
        #print(total_slider_rows,total_lifter_rows)
        print(find_text(ws, "EXCLUSIVE BOP"))

        if total_slider_rows>3 or total_lifter_rows>3:
            Erow,Estart_col = find_text(ws, "EXCLUSIVE BOP")
            merge_and_copy_style(ws,Erow,Estart_col,Estart_col+9,align="left")
            
            thin = Side(style="thin")
            border = Border(left=thin, right=thin, top=thin, bottom=thin)

            for row in range(19, Erow):      # Rows 19 to 34
                for col in range(2, 9):    # Columns B(2) to H(8)
                    ws.cell(row=row, column=col).border = border


        #---filling cavity rating/core rating value
        core_values = [0.75, 0.35, 0.3, '']
        cavity_values = [0.75, 0.25, 0.3, "L"]
        row, col = find_text(ws, "Cavity Rating")
        if row is not None and col is not None:
            for i in range(4):
                safe_write(ws, row + i + 1, col, cavity_values[i])
                safe_write(ws, row + i + 1, col + 1, core_values[i])

        # -------- Fill the table --------
        for label, value, qty in fixed_sequence:
            
            if label == "":
                # NA / Blank overriding the template defaults
                for col_idx in range(2, 12):
                    safe_write(ws, current_row, col_idx, "")
                current_row += 1
                continue

            safe_write(ws, current_row, 2, sr)     # B = Sr. No.
            safe_write(ws, current_row, 3, label)   # C = Details

            if value:
                # Strip numeric-like parts when writing sizes
                parts = re.split(r'[xX]', str(value).replace("mm", "").strip())
                for i, part in enumerate(parts[:3]):
                    safe_write(ws, current_row, 5 + i, part)  # E, F, G = Size L, W, H
                if qty is not None and qty != "":
                    safe_write(ws, current_row, 8, qty)  # H = Quantity
                if label.lower().startswith("cavity"):
                    cheight = int(str(parts[-1]).strip()) 
                    Cavity_material_grade_assign = material_grade_parts(Cavity_material_grade,cheight)
                    safe_write(ws, current_row, 4, Cavity_material_grade_assign)  # D = Material Grade
                elif label.lower().startswith("core"):
                    crheight =  int(str(parts[-1]).strip()) 
                    Core_material_grade_assign = material_grade_parts(Core_material_grade,crheight)
                    safe_write(ws, current_row, 4, Core_material_grade_assign)    # D = Material Grade
                        

            elif re.search(r'(?:l[iy]?f?t[eé]?r|ifter|lfter)', label, re.I):
                # This is a Lifter row
                slot_idx = int(re.search(r'\d+', label).group()) - 1
                slot = lifter_slots[slot_idx] if 0 <= slot_idx < len(lifter_slots) else None

                if slot:
                    safe_write(ws, current_row, 5, slot["L"])   # E
                    safe_write(ws, current_row, 6, slot["W"])   # F
                    safe_write(ws, current_row, 7, slot["H"])   # G
                    safe_write(ws, current_row, 8, slot["Qty"]) # H
                    heigh = slot.get("H", 1)
                else:
                    # NA or empty slot: leave Size L/W/H and Qty blank
                    heigh = 1
                grade = material_grade_parts(Lifters_material_grade, heigh)
                # If material grade resolves to "C 45", leave blank for lifters
                if grade == "C 45":
                    grade = ""
                safe_write(ws, current_row, 4, grade)  # D = Material Grade

            elif re.search(r'(?:s?l[iy]?d[eé]?r|slder)', label, re.I):
                # This is a Slider row
                slot_idx = int(re.search(r'\d+', label).group()) - 1
                slot = slider_slots[slot_idx] if 0 <= slot_idx < len(slider_slots) else None

                if slot:
                    safe_write(ws, current_row, 5, slot["L"])   # E
                    safe_write(ws, current_row, 6, slot["W"])   # F
                    safe_write(ws, current_row, 7, slot["H"])   # G
                    safe_write(ws, current_row, 8, slot["Qty"]) # H
                    sheigh = slot.get("H", 1)
                else:
                    # NA or empty slot: leave Size L/W/H and Qty blank
                    sheigh = 1
                
                grade = material_grade_parts(Sliders_material_grade, sheigh)
                # If material grade resolves to "C 45", leave blank for sliders
                if grade == "C 45":
                    grade = ""
                safe_write(ws, current_row, 4, grade)  # D = Material Grade

            current_row += 1
            sr += 1

        # -------- MULTI-IMAGE GRID --------
        mould_images = data.get("Mould Image", []) or []
        images_ordered = []

        if mould_images:
            # Prefer left-of-label first (optional)
            if args.no_prefer_left:
                images_ordered = mould_images[:]
            else:
                left = [i for i in mould_images if i.get("note") == "extracted_left_of_label"]
                rest = [i for i in mould_images if i.get("note") != "extracted_left_of_label"]
                images_ordered = left + rest

            # Deduplicate by path/base64 to avoid repeats
            seen = set(); deduped = []
            for rec in images_ordered:
                key = (rec.get("path") or "") or (rec.get("base64") or "")
                if not key:
                    key = id(rec)  # worst case
                if key in seen:
                    continue
                seen.add(key)
                deduped.append(rec)
            images_ordered = deduped

        # Layout definitions
        start_col_letter, start_row = coordinate_from_string(args.image_cell)
        start_col = column_index_from_string(start_col_letter)
        W, H = max(1, args.image_span[0]), max(1, args.image_span[1])
        grid_cols = max(1, args.grid_cols)
        max_images = max(1, args.max_images)

        # Images are now stored as file paths only (base64 removed for memory efficiency)
        cache_dir = Path(args.cache_dir)
        chosen = images_ordered[:max_images]

        # Insert images in grid
        for img_idx, rec in enumerate(chosen):
            p = rec.get("path", "")
            r = img_idx // grid_cols
            c = img_idx % grid_cols
            anchor_col = start_col + c * W
            anchor_row = start_row + r * H
            anchor_cell = f"{get_column_letter(anchor_col)}{anchor_row}"
            inserted = insert_image_fit_box(ws, p, anchor_cell, width_cells=W, height_cells=H)
            if args.debug:
                print(f"[DEBUG] {ws.title}: image {img_idx+1}/{len(chosen)} -> {anchor_cell} "
                      f"{'(ok)' if inserted else '(skip)'} path={p}")

        # Clear memory after processing this JSON file
        gc.collect()
        print(f"✅ Completed {idx}/{total_files}: {json_path.stem}", file=sys.stderr)

    # Remove the original data template sheet AFTER all part sheets are created
    wb_template.remove(template_ws)

    # KEEP the DROP_DOWN_TAB sheet so dropdowns work in the output!
    # (Previously this was being removed, which broke dropdown references)

    # Save with retry to avoid PermissionError
    safe_save_workbook(wb_template, args.out)
    print(f"\n✅ Combined Excel report generated:\n{args.out}")


if __name__ == "__main__":
    import sys
    try:
        main()
    except SystemExit:
        raise
    except KeyboardInterrupt:
        print("\n⚠️ Processing interrupted by user", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        import traceback
        print(f"❌ CRITICAL ERROR in json_to_excel: {str(e)}", file=sys.stderr)
        print(traceback.format_exc(), file=sys.stderr)
        sys.exit(1)
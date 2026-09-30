"""
CostOptima seed script — populates:
  - the Permission catalogue (one row per page/model x action)
  - 4 roles (Super Admin, Admin, HOD, Masters Update) with a sensible default
    permission matrix assigned via RolePermission
  - one starter user per role, for testing session login as each role
  - the Masters workbook types/tabs/columns (Column Position, Sheet Name,
    Shift Factor Rate, Master Output Matrix, Sand Columns, SBC Format, plus
    Authority Matrix retained but unlinked from the sidebar)
  - the commodity types + their Master User/PCE/Self workbook files
  - the commodity config engine's starter field/formula rows

Run with:  python -m app.seed
"""
from pathlib import Path

from app.database import Base, engine, SessionLocal
from app.models.user import Role, Permission, RolePermission, User
from app.models.workbook import WorkbookType, WorkbookTab, WorkbookTabColumn
from app.pcm.master_data.static_importer import import_support_workbook
from app.models.commodity import CommodityType, FieldDefinition, CalcRule
from app.models.mixins import stamp_create
from app.core.security import hash_password

Base.metadata.create_all(bind=engine)
db = SessionLocal()

# ---------------------------------------------------------------------------
# Permission catalogue — one row per (resource, action). 'resource' covers both
# pages (calculations, masters, commodity_config, outputs, history, settings,
# users, roles) and, within the Workbook Engine, the underlying model.
# ---------------------------------------------------------------------------
RESOURCES = ["calculations", "masters", "commodity_config", "outputs", "history", "settings", "users", "roles",
             "permissions", "workbook_config", "merge"]
ACTIONS = ["create", "view", "update", "delete"]

permission_by_key = {}
for resource in RESOURCES:
    for action in ACTIONS:
        perm = db.query(Permission).filter(Permission.resource == resource, Permission.action == action).first()
        if not perm:
            perm = Permission(resource=resource, action=action,
                               description=f"Can {action} {resource.replace('_', ' ')}")
            db.add(perm)
            db.commit()
            db.refresh(perm)
        permission_by_key[(resource, action)] = perm

# ---------------------------------------------------------------------------
# Roles + their default permission matrix
# ---------------------------------------------------------------------------
ROLE_MATRIX = {
    "Super Admin": {r: ACTIONS for r in RESOURCES},  # full access everywhere
    "Admin": {
        "calculations": ACTIONS, "masters": ACTIONS, "commodity_config": ACTIONS,
        "outputs": ["view"], "history": ["view"], "settings": ["view", "update"],
        "users": ["view", "create", "update"], "roles": ["view"],
        "permissions": ["view"], "workbook_config": ACTIONS,
        "merge": ["view", "create"],
    },
    "HOD": {
        "calculations": ["view", "create"], "masters": ["view"], "commodity_config": ["view"],
        "outputs": ["view"], "history": ["view"], "settings": ["view"],
        "users": ["view"], "roles": ["view"], "permissions": ["view"], "workbook_config": ["view"],
        "merge": ["view"],

    },
    "Masters Update": {
        "calculations": ["view"], "masters": ["create", "view", "update"],
        "commodity_config": ["create", "view", "update"],
        "outputs": ["view"], "history": ["view"], "settings": ["view"],
        "workbook_config": ["view"],
    },
}

role_by_name = {}
for role_name, matrix in ROLE_MATRIX.items():
    role = db.query(Role).filter(Role.name == role_name).first()
    if not role:
        role = Role(name=role_name, description=f"{role_name} role")
        db.add(role)
        db.commit()
        db.refresh(role)
    role_by_name[role_name] = role

    for resource, actions in matrix.items():
        for action in actions:
            perm = permission_by_key[(resource, action)]
            exists = db.query(RolePermission).filter(
                RolePermission.role_id == role.id, RolePermission.permission_id == perm.id
            ).first()
            if not exists:
                rp = RolePermission(role_id=role.id, permission_id=perm.id)
                db.add(rp)
    db.commit()

# ---------------------------------------------------------------------------
# One starter user per role (session-login credentials for testing each role)
# ---------------------------------------------------------------------------
STARTER_USERS = [
    ("Super Admin User", "superadmin@costoptima.com", "superadmin123", "Super Admin", "Platform Owner"),
    ("Admin User", "admin@costoptima.com", "admin123", "Admin", "Platform Admin"),
    ("HOD User", "hod@costoptima.com", "hod123", "HOD", "Head of Department"),
    ("Masters Update User", "masters@costoptima.com", "masters123", "Masters Update", "Master Data Analyst"),
]
for name, email, pwd, role_name, title in STARTER_USERS:
    if not db.query(User).filter(User.email == email).first():
        u = User(name=name, email=email, hashed_password=hash_password(pwd),
                 job_title=title, department="Operations", role_id=role_by_name[role_name].id, status="Active")
        db.add(u)
        db.commit()
        db.refresh(u)
        stamp_create(u, u.id)  # seeded users are their own creator for audit purposes
        db.commit()
        print(f"Seeded {role_name} user: {email} / {pwd}")


# Excel layout/format metadata recovered from the legacy master workbooks. These are
# configuration values, not business logic: changing a workbook layout only changes
# this table (or the equivalent /config/app endpoint).
WORKBOOK_LAYOUT_CONFIG = {
    "Process Master": {"header_rows": [0, 1]},
    "Material Master": {"header_rows": [0, 1]},
    "Special RM": {"header_rows": [0, 1]},
    "Special norms": {"header_rows": [1]},
    "Net Consumption base": {"header_rows": [1]},
    "Blank Consumption base": {"header_rows": [0, 1]},
    "Gross Consumption base": {"header_rows": [0, 1]},
    "Consumable": {"header_rows": [0, 1]},
    "Casting Complexity Master": {"header_rows": [1, 2]},
    "Casting Recipe Master": {"header_rows": [0, 1]},
}

COLUMN_FORMAT_CONFIG = {
    "ICC": {"is_percentage": True, "number_format": "0.00%"},
    "REJ": {"is_percentage": True, "number_format": "0.00%"},
    "OH": {"is_percentage": True, "number_format": "0.00%"},
    "PROFIT": {"is_percentage": True, "number_format": "0.00%"},
    "EFFICIENCY": {"is_percentage": True, "number_format": "0.00%"},
    "Royalty": {"is_percentage": True, "number_format": "0.00%"},
    "Rejection %": {"is_percentage": True, "number_format": "0.00%"},
    "% wastage": {"is_percentage": True, "number_format": "0.00%"},
    "OVERALL REJECTION": {"is_percentage": True, "number_format": "0.00%"},
    "OVERHEAD": {"is_percentage": True, "number_format": "0.00%"},
    "REJ ON INJ MOLDED PART": {"is_percentage": True, "number_format": "0.00%"},
    "REJ ON MATERIAL & PROCESS": {"is_percentage": True, "number_format": "0.00%"},
}


def get_or_create_workbook(code, name, description, tabs):
    """tabs: list of (tab_name, header_row_index, read_only, columns)
       columns: list of (name, data_type, is_unique, is_required)
       header_type is auto-derived from header_row_index (0 -> type0, 1 -> type1, per
       the PCM Master Merge reference doc); pass header_row_index=-1 for typeSingle."""
    wt = db.query(WorkbookType).filter(WorkbookType.code == code).first()
    if wt:
        # Fill only missing metadata so existing admin configuration remains authoritative.
        for tab in wt.tabs:
            layout = WORKBOOK_LAYOUT_CONFIG.get(tab.tab_name, {})
            if getattr(tab, "header_rows", None) is None and layout.get("header_rows") is not None:
                tab.header_rows = layout["header_rows"]
            for col in tab.columns:
                fmt = COLUMN_FORMAT_CONFIG.get(col.column_name, {})
                if col.number_format is None and fmt.get("number_format"):
                    col.number_format = fmt["number_format"]
                if not col.is_percentage and fmt.get("is_percentage"):
                    col.is_percentage = True
        db.commit()
        return wt
    wt = WorkbookType(code=code, name=name, description=description)
    db.add(wt)
    db.commit()
    db.refresh(wt)
    for tab_name, header_row, read_only, columns in tabs:
        header_type = "typeSingle" if header_row < 0 else ("type1" if header_row == 1 else "type0")
        layout = WORKBOOK_LAYOUT_CONFIG.get(tab_name, {})
        tab = WorkbookTab(workbook_type_id=wt.id, tab_name=tab_name,
                           header_row_index=max(header_row, 0), header_type=header_type, read_only=read_only,
                           header_rows=layout.get("header_rows"))
        db.add(tab)
        db.commit()
        db.refresh(tab)
        for i, (col_name, dtype, uniq, req) in enumerate(columns):
            fmt = COLUMN_FORMAT_CONFIG.get(col_name, {})
            db.add(WorkbookTabColumn(tab_id=tab.id, column_name=col_name, header_name=col_name,
                                      data_type=dtype, is_unique_key=uniq, is_required=req, position=i,
                                      number_format=fmt.get("number_format"), is_percentage=fmt.get("is_percentage", False)))
    db.commit()
    return wt


# Authority Matrix — 8 tabs, real columns from the source workbook
get_or_create_workbook("authority_matrix", "Authority Matrix",
    "Folder access, master-file mapping, column merge rules, headers, users, notification routing.",
    tabs=[
        ("Folder Authority", 0, False, [
            ("Folder Path", "string", False, True), ("Access Required", "string", False, True),
            ("Type", "string", False, False), ("Link", "string", False, False)]),
        ("Master Split", 0, False, [
            ("Folder Structure", "string", False, True), ("FileName", "string", False, True)]),
        ("Column Merging", 0, False, [
            ("Process Master", "string", False, False), ("Material Master", "string", False, False),
            ("eBom", "string", False, False)]),
        ("Master Header", 0, False, [
            ("Colname", "string", False, True), ("Header", "string", False, True),
            ("Unique", "string", False, False), ("SheetName", "string", False, True),
            ("Position", "number", False, False)]),
        ("User Data", 0, False, [
            ("Name", "string", False, True), ("ID", "string", True, True),
            ("Commodity", "string", False, False), ("User Status", "string", False, False)]),
        ("Persons Left", 0, False, [
            ("Name", "string", False, True), ("ID", "string", True, True),
            ("Commodity", "string", False, False), ("User Status", "string", False, False),
            ("Reason", "string", False, True)]),
        ("Duplicate Entry", 0, False, [
            ("Commodity", "string", True, True), ("To", "email-list", False, False),
            ("CC", "email-list", False, False)]),
        ("Sheet", 0, False, [("Commodity Group", "string", True, True)]),
    ])

get_or_create_workbook("sheet_name", "Sheet Name", "Maps each commodity to its expected input sheet.",
    tabs=[("Sheet Name", 0, False, [
        ("Commodity", "string", False, True), ("Sheet Name", "string", False, True),
        ("Header", "number", False, False), ("Temp File", "string", False, False),
        ("Add", "number", False, False)])])

get_or_create_workbook("shift_factor_rate", "Shift Factor Rate",
    "Formula templates driving shift-based costing.",
    tabs=[("Shift Factor Rate", 0, False, [
        ("Applicable Process", "string", True, True), ("Shift Factor", "number", False, False),
        ("Shift Rate", "number", False, False), ("Molding Cost", "string", False, False),
        ("Deflashing Cost", "string", False, False)])])

get_or_create_workbook("master_output_matrix", "Master Output Matrix",
    "Tracks completion status across BOM, Freight, Packaging, Consign and every commodity.",
    tabs=[("Master output Final", 1, True, [
        ("Final Out Put Sheet", "string", True, True), ("BOM", "string", False, False),
        ("Frt", "string", False, False), ("Pkg", "string", False, False),
        ("Consign", "string", False, False), ("Remarks", "string", False, False)])])

pcm_dir = Path(__file__).resolve().parents[2] / "PCM"
master_output_matrix_path = pcm_dir / "Master Output Matrix.xlsx"
if master_output_matrix_path.exists():
    import_support_workbook(
        db,
        code="master_output_matrix",
        name="Master Output Matrix",
        description="Legacy PCM master-output column applicability matrix imported from PCM/Master Output Matrix.xlsx.",
        path=master_output_matrix_path,
        sheet_name="Master output Final",
        tab_name="Master output Final",
        header_row=1,
        replace=False,
    )

get_or_create_workbook("column_position", "Column Position",
    "Legacy Excel-column-letter mapping, retained for migration reference.",
    tabs=[("Column Position", 0, False, [
        ("Index", "number", True, True), ("Position", "string", False, True)])])

get_or_create_workbook("sand_columns", "Sand Columns", "Output column configuration for Sand Casting.",
    tabs=[("Sand Columns", 0, False, [("Columns Val", "string", True, True)])])

get_or_create_workbook("sbc_format", "SBC Format",
    "Supplier Bench Commercial/Technical report layout template.",
    tabs=[("SBC Format", 0, False, [
        ("Field Name", "string", True, True), ("Value", "string", False, False),
        ("Group", "string", False, False)])])


# Commodity types (subset shown; add the rest the same way — pure data, no code change)
def get_or_create_commodity(code, name, fields, formula=None):
    c = db.query(CommodityType).filter(CommodityType.code == code).first()
    if c:
        return c
    c = CommodityType(code=code, name=name)
    db.add(c)
    db.commit()
    db.refresh(c)
    for i, (fname, ftype, source) in enumerate(fields):
        db.add(FieldDefinition(commodity_type_id=c.id, field_name=fname, data_type=ftype,
                                source_type=source, position=i))
    if formula:
        formulas = formula if isinstance(formula, list) else [formula]
        for rule in formulas:
            db.add(CalcRule(commodity_type_id=c.id, **rule))
    db.commit()
    return c


get_or_create_commodity("sheet_metal", "Sheet Metal",
    fields=[("Material Code", "string", "input"), ("Rate/UOM", "number", "master_lookup"),
            ("Scrap Rate", "number", "master_lookup"), ("Basic Cost/Pc", "number", "calculated")],
    formula={"formula_expr": "material_weight * rate_per_uom", "output_field": "Basic Cost/Pc", "priority": 1})

get_or_create_commodity("forging", "Casting, Forging & Machining",
    fields=[("Material Code", "string", "input"), ("Part Wt", "number", "input"),
            ("Input Wt", "number", "input"), ("Rate/UOM", "number", "master_lookup"),
            ("Scrap Rate", "number", "master_lookup"), ("Process Cost", "number", "input"),
            ("ICC", "number", "master_lookup"), ("REJ", "number", "master_lookup"),
            ("OH", "number", "master_lookup"), ("Profit", "number", "master_lookup"),
            ("Total", "number", "calculated")],
    formula=[
        {"formula_expr": "part_wt / input_wt if input_wt else 0", "output_field": "Yield (%)", "priority": 10},
        {"formula_expr": "(input_wt - part_wt) * 0.8", "output_field": "Scrap Wt", "priority": 20},
        {"formula_expr": "scrap_rate * scrap_wt", "output_field": "Scrap Recovery", "priority": 30},
        {"condition_expr": "type.upper() == 'MAKE'", "formula_expr": "rate_uom * input_wt + heat_treatment_cost - scrap_recovery", "output_field": "RM Cost/Pc", "priority": 40},
        {"condition_expr": "type.upper() != 'MAKE'", "formula_expr": "rate_uom * input_wt - scrap_recovery", "output_field": "RM Cost/Pc", "priority": 40},
        {"formula_expr": "process_cost", "output_field": "Process Cost", "priority": 50},
        {"formula_expr": "rm_cost_pc + process_cost", "output_field": "Basic Cost/Pc", "priority": 60},
        {"formula_expr": "icc + rej + oh + profit", "output_field": "Total Markup", "priority": 70},
        {"formula_expr": "basic_cost_pc + tool_cost + packing + freight + total_markup", "output_field": "Total", "priority": 80},
    ])

get_or_create_commodity("pcba", "PCBA",
    fields=[("Component Code", "string", "input"), ("Unit Cost", "number", "master_lookup")])

get_or_create_commodity("injection_molding", "Injection Molding",
    fields=[("Shift Rate", "number", "master_lookup"), ("Pc per shift", "number", "input"),
            ("Molding Cost", "number", "calculated")],
    formula={"formula_expr": "shift_rate / pc_per_shift", "output_field": "Molding Cost", "priority": 1})

# The remaining commodities (registered for navigation; add fields/formulas the same way
# as above once their calculation rules are migrated in)
for code, name in [
    ("dac_hvac_other", "DAC, HVAC & Other"),
    ("ee", "E&E"),
    ("seat_mechanism", "Seat Mechanism"),
    ("trim", "Trim"),
    ("soft_trim", "Soft Trim"),
    ("gdc_pdc", "GDC / PDC"),
    ("special_process", "Special Process"),
]:
    get_or_create_commodity(code, name, fields=[])


# ---------------------------------------------------------------------------
# Commodity MASTER-FILE workbooks — every file shown under Commodity Types in the
# UI (Non Std BOP, Process Master, Norms Make, etc.) is registered here as a
# WorkbookTab, exactly like the Masters section. One WorkbookType per
# (commodity, category) — its tabs are the individual master files, rendered in
# the frontend as a sheet carousel. This is what makes Commodity Types pages
# read/write real data through the same generic Workbook Engine endpoints used
# by Masters, instead of needing bespoke routes.
# ---------------------------------------------------------------------------

COLUMN_PRESETS = {
    "Master Data file": [("Part No", "string", True, True), ("Description", "string", False, False),
                         ("Commodity", "string", False, False), ("Process", "string", False, False),
                         ("Material Code", "string", False, False), ("UOM", "string", False, False),
                         ("Rate/UOM", "number", False, False), ("Remarks", "string", False, False)],
    "RM": [("Material Code", "string", False, True), ("Description", "string", False, False),
           ("Grade", "string", False, False), ("Supplier", "string", False, False),
           ("Rate/UOM", "number", False, False), ("UOM", "string", False, False)],
    "Non Std BOP": [("Sr", "number", False, False), ("Supplier", "string", False, True),
                    ("Customer No", "string", True, True), ("Description", "string", False, False),
                    ("Commodity", "string", False, False), ("Cost/Pc", "number", False, False),
                    ("UOM", "string", False, False)],
    "Std BOP": [("Sr", "number", False, False), ("Supplier", "string", False, True),
                ("Customer No", "string", True, True), ("Description", "string", False, False),
                ("Commodity", "string", False, False), ("Cost/Pc", "number", False, False),
                ("Delivery Terms", "string", False, False)],
    "Process Master": [("Category", "string", False, False), ("Process Code", "string", True, True),
                       ("Process", "string", False, True), ("M/C Specs", "string", False, False),
                       ("UOM", "string", False, False), ("Rate/UOM", "number", False, False)],
    "Material Master": [("Material Code", "string", True, True), ("Commodity", "string", False, False),
                        ("Material Family", "string", False, False), ("Material Grade", "string", False, False),
                        ("RM Supplier", "string", False, False), ("UOM", "string", False, False),
                        ("Rate/UOM", "number", False, False), ("Scrap Rate", "number", False, False)],
    "Casting Complexity Master": [("Rejection Category", "string", False, True), ("Type", "string", False, False),
                                  ("Rejection %", "number", False, False), ("ARPA", "string", False, False),
                                  ("HPML", "string", False, False)],
    "Casting Recipe Master": [("Material Code", "string", True, True), ("Addition in kg", "number", False, False),
                              ("FG 200", "string", False, False), ("FG 220", "string", False, False),
                              ("FG 260", "string", False, False), ("FG 300", "string", False, False)],
    "Net Consumption Base": [("Con1", "string", False, False), ("Con2", "string", False, False),
                             ("Con3", "string", False, False), ("Head1", "string", False, False),
                             ("Head2", "string", False, False), ("Cal RM Cons", "string", False, False)],
    "Blank Consumption Base": [("Conditions", "string", False, False), ("Blank Length", "number", False, False),
                               ("Blank Width", "number", False, False), ("Blank Pipe Dimension", "string", False, False)],
    "Gross Consumption Base": [("Conditions", "string", False, False), ("Gross Length", "number", False, False),
                               ("Gross Width", "number", False, False), ("Gross Pipe Dimension", "string", False, False)],
    "Consumable": [("Material Code", "string", True, True), ("Base Part", "string", False, False),
                   ("Consumable", "string", False, False), ("Fixed Cost", "number", False, False),
                   ("Output", "string", False, False)],
    "eBom": [("Part No", "string", True, True), ("Description", "string", False, False),
             ("Revision", "string", False, False), ("Effective Date", "string", False, False)],
    "Scrap Stamping": [("Track", "string", True, True), ("Scrap Value 1", "number", False, False),
                       ("Scrap Value 2", "number", False, False), ("Scrap Value 3", "number", False, False)],
    "Seat Output Matrix": [("Base", "string", True, True), ("Type", "string", False, False),
                           ("Selection Priority", "number", False, False)],
    "Assy": [("Process", "string", True, True), ("Fix", "string", False, False),
             ("Snap", "string", False, False), ("Screw", "string", False, False),
             ("Inspection", "string", False, False), ("JPH - Manual", "number", False, False)],
    "Special Norms": [("Supplier", "string", False, True), ("Hanger Cost", "number", False, False),
                      ("ICC", "number", False, False), ("Rej on Molded Part", "number", False, False),
                      ("Overhead", "number", False, False), ("Profit", "number", False, False),
                      ("PnF", "number", False, False)],
    "Special RM": [("Material", "string", True, True), ("Apr#18", "number", False, False),
                   ("Oct#18", "number", False, False), ("Apr#19", "number", False, False),
                   ("Oct#19", "number", False, False)],
}
NORMS_DEFAULT = [("Supplier", "string", False, True), ("ICC", "number", False, False),
                 ("REJ", "number", False, False), ("OH", "number", False, False),
                 ("Profit", "number", False, False), ("Commodity", "string", False, False)]
GENERIC_DEFAULT = [("Field 1", "string", False, False), ("Field 2", "string", False, False),
                    ("Field 3", "string", False, False), ("Field 4", "string", False, False)]


def columns_for(file_name):
    if file_name in COLUMN_PRESETS:
        return COLUMN_PRESETS[file_name]
    if file_name.startswith("Norms"):
        return NORMS_DEFAULT
    return GENERIC_DEFAULT


def seed_commodity_workbook(commodity_code, category, files):
    """One WorkbookType per (commodity, category); each file becomes a tab.
       `files` may be an empty list (e.g. Trim Soft / Special Process have no 'self'
       files) — the workbook is still created with zero tabs so the frontend gets a
       clean empty-state response instead of a 404."""
    code = f"{commodity_code}_{category}"
    tabs = [(f, 0, False, columns_for(f)) for f in files]
    get_or_create_workbook(code, f"{commodity_code} — {category}",
                           f"Master files for '{commodity_code}', category '{category}'.", tabs)


# Matches pcm_v5.html's COMMODITY_STRUCTURE exactly: every commodity has a 'self'
# category (its primary/root-level sheets, shown under the commodity's own name in
# the UI rather than a generic label) plus 'master_user' and, where applicable, 'pce'.
COMMODITY_STRUCTURE = {
    "forging": {
        "name": "Casting, Forging & Machining", "categories": ["self", "master_user", "pce"],
        "files": {
            "self": ["Casting Complexity Master", "Casting Recipe Master", "Norms Make",
                     "Norms Make Applicability", "Norms Buy Std", "Norms Buy Non Std", "Norms Consign",
                     "Norms PnF", "Norms Casting", "Norms Burning Loss"],
            "master_user": ["Process Master", "Material Master"],
            "pce": ["Non Std BOP", "Std BOP"],
        }},
    "dac_hvac_other": {
        "name": "DAC, HVAC & Other", "categories": ["self", "master_user", "pce"],
        "files": {
            "self": ["Norms Make", "Norms Make Applicability", "Norms Buy Std", "Norms Buy Non Std",
                     "Norms Consign", "Norms PnF"],
            "master_user": ["Process Master", "Material Master"],
            "pce": ["Non Std BOP", "Std BOP"],
        }},
    "ee": {
        "name": "E&E", "categories": ["self", "master_user", "pce"],
        "files": {
            "self": ["Norms Make", "Norms Make Applicability", "Norms Buy Std", "Norms Buy Non Std",
                     "Norms Consign", "Norms PnF", "eBom"],
            "master_user": ["Process Master", "Material Master"],
            "pce": ["Non Std BOP", "Std BOP"],
        }},
    "seat_mechanism": {
        "name": "Seat Mechanism", "categories": ["self", "master_user", "pce"],
        "files": {
            "self": ["Norms Make", "Norms Make Applicability", "Norms Buy Std", "Norms Buy Non Std",
                     "Norms Consign", "Norms PnF", "Seat Output Matrix"],
            "master_user": ["Process Master", "Material Master"],
            "pce": ["Non Std BOP", "Std BOP"],
        }},
    "sheet_metal": {
        "name": "Sheet Metal", "categories": ["self", "master_user", "pce"],
        "files": {
            "self": ["Norms Make", "Norms Make Applicability", "Norms Buy Std", "Norms Buy Non Std",
                     "Norms Consign", "Norms PnF", "Scrap Stamping"],
            "master_user": ["Process Master", "Material Master"],
            "pce": ["Non Std BOP", "Std BOP"],
        }},
    "trim": {
        "name": "Trim", "categories": ["self", "master_user", "pce"],
        "files": {
            "self": ["Assy", "Special Norms", "Norms Make", "Norms Make Applicability", "Norms Gross Wt",
                     "Norms Assy", "Norms Buy Std", "Norms Buy Non Std", "Norms Consign",
                     "Norms Special Process Norms", "Norms PnF", "Norms Shift Factor"],
            "master_user": ["Process Master", "Material Master"],
            "pce": ["Non Std BOP", "Std BOP"],
        }},
    "soft_trim": {
        "name": "Soft Trim", "categories": ["self", "master_user"],
        "files": {
            "self": [],
            "master_user": ["Process Master", "Material Master"],
        }},
    "gdc_pdc": {
        "name": "GDC / PDC", "categories": ["self", "master_user"],
        "files": {
            "self": ["Norms Make", "Norms Make Applicability", "Norms Buy Std", "Norms Buy Non Std",
                     "Norms Consign", "Norms PnF"],
            "master_user": ["Process Master", "Material Master"],
        }},
    "special_process": {
        "name": "Special Process", "categories": ["self", "master_user"],
        "files": {
            "self": [],
            "master_user": ["Special RM", "Process Master"],
        }},
}

for commodity_code, spec in COMMODITY_STRUCTURE.items():
    for category in spec["categories"]:
        seed_commodity_workbook(commodity_code, category, spec["files"][category])

# The shared/default master sheets shown under "PCM Master" — tab names match
# pcm_v5.html's PCM_SHEETS exactly.
get_or_create_workbook("pcm_master", "PCM Master", "Shared master sheets used across multiple commodities.",
    tabs=[
        ("Master Data file", 0, False, columns_for("Master Data file")),
        ("Net Consumption base", 0, False, columns_for("Net Consumption Base")),
        ("Blank Consumption base", 0, False, columns_for("Blank Consumption Base")),
        ("Gross Consumption base", 0, False, columns_for("Gross Consumption Base")),
        ("Consumable", 0, False, columns_for("Consumable")),
    ])

db.close()
print("Seed complete.")

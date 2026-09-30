from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime
from app.core.pagination import SafeInt, SafeOptionalInt


class WorkbookTypeOut(BaseModel):
    id: SafeInt
    code: str
    name: str
    description: Optional[str] = None
    model_config = {"from_attributes": True}


class WorkbookTypeIn(BaseModel):
    code: str
    name: str
    description: Optional[str] = None


class WorkbookTypeUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None


class WorkbookMergedRangeOut(BaseModel):
    id: SafeInt
    tab_id: SafeInt
    worksheet_name: str
    merged_range: str
    top_left_cell: str
    start_row: SafeInt
    end_row: SafeInt
    start_column: SafeInt
    end_column: SafeInt
    value: Optional[str] = None
    model_config = {"from_attributes": True}


class WorkbookSectionOut(BaseModel):
    id: SafeInt
    tab_id: SafeInt
    section_name: str
    header_row: SafeInt
    data_start_row: SafeOptionalInt = None
    data_end_row: SafeOptionalInt = None
    column_start: SafeOptionalInt = None
    column_end: SafeOptionalInt = None
    header_type: Optional[str] = None
    header_rows: Optional[List[int]] = None
    position: SafeInt
    description: Optional[str] = None
    model_config = {"from_attributes": True}


class WorkbookSectionIn(BaseModel):
    section_name: str
    header_row: int = 0
    data_start_row: Optional[int] = None
    data_end_row: Optional[int] = None
    column_start: Optional[int] = None
    column_end: Optional[int] = None
    header_type: Optional[str] = None
    header_rows: Optional[List[int]] = None
    position: int = 0
    description: Optional[str] = None


class WorkbookSectionUpdate(BaseModel):
    section_name: Optional[str] = None
    header_row: Optional[int] = None
    data_start_row: Optional[int] = None
    data_end_row: Optional[int] = None
    column_start: Optional[int] = None
    column_end: Optional[int] = None
    header_type: Optional[str] = None
    header_rows: Optional[List[int]] = None
    position: Optional[int] = None
    description: Optional[str] = None


class WorkbookTabColumnOut(BaseModel):
    id: SafeInt
    tab_id: SafeInt
    section_id: SafeOptionalInt = None
    column_name: str
    header_name: Optional[str] = None
    header_row: SafeOptionalInt = None
    column_position: SafeOptionalInt = None
    excel_column: Optional[str] = None
    data_type: str
    is_unique_key: bool
    is_required: bool
    position: SafeInt
    validation_rules: Optional[Dict[str, Any]] = None
    number_format: Optional[str] = None
    is_percentage: bool = False
    formula_config: Optional[Dict[str, Any]] = None
    model_config = {"from_attributes": True}


class WorkbookTabColumnIn(BaseModel):
    section_id: Optional[int] = None
    column_name: str
    header_name: Optional[str] = None
    header_group: Optional[str] = None
    header_row: Optional[int] = None
    column_position: Optional[int] = None
    data_type: str = "string"
    is_unique_key: bool = False
    is_required: bool = False
    position: int = 0
    validation_rules: Optional[Dict[str, Any]] = None
    number_format: Optional[str] = None
    is_percentage: bool = False
    formula_config: Optional[Dict[str, Any]] = None


class WorkbookTabColumnUpdate(BaseModel):
    section_id: Optional[int] = None
    column_name: Optional[str] = None
    header_name: Optional[str] = None
    header_group: Optional[str] = None
    header_row: Optional[int] = None
    column_position: Optional[int] = None
    data_type: Optional[str] = None
    is_unique_key: Optional[bool] = None
    is_required: Optional[bool] = None
    position: Optional[int] = None
    validation_rules: Optional[Dict[str, Any]] = None
    number_format: Optional[str] = None
    is_percentage: Optional[bool] = None
    formula_config: Optional[Dict[str, Any]] = None


class WorkbookTabColumnBulkIn(BaseModel):
    columns: List[WorkbookTabColumnIn]


class DetectedColumn(BaseModel):
    column_name: str
    header_name: str
    header_group: Optional[str] = None
    data_type: str
    is_required: bool = False
    is_unique_key: bool = False
    position: SafeInt
    column_position: SafeOptionalInt = None
    excel_column: Optional[str] = None
    header_row: SafeOptionalInt = None


class ColumnDetectionResult(BaseModel):
    columns: List[DetectedColumn]
    auto_detected_two_row_header: bool = False


class WorkbookTabOut(BaseModel):
    id: SafeInt
    workbook_type_id: SafeInt
    tab_name: str
    header_row_index: SafeInt
    header_type: str
    read_only: bool
    has_multiple_sections: bool
    header_rows: Optional[List[int]] = None
    remarks_column_name: Optional[str] = None
    sections: List[WorkbookSectionOut] = []
    columns: List[WorkbookTabColumnOut] = []
    model_config = {"from_attributes": True}


class WorkbookTabSummaryOut(BaseModel):
    """Lightweight tab row used by paginated tab lists (admin config UI) — avoids
    shipping every column/section definition just to render a list row."""
    id: SafeInt
    tab_name: str
    row_count: SafeInt
    read_only: bool
    header_type: str
    header_row_index: SafeInt
    has_multiple_sections: bool
    header_rows: Optional[List[int]] = None
    remarks_column_name: Optional[str] = None


class WorkbookTabIn(BaseModel):
    tab_name: str
    header_row_index: int = 0
    # Raw/advanced field — kept for backward compatibility with existing callers
    # (e.g. app/seed.py) that already know exactly which of the 4 HEADER_TYPES they
    # want. The UI no longer asks the user to choose between "type0" and "type1"
    # directly (see `structure` below); it only matters as a fallback when
    # `structure` is omitted.
    header_type: str = Field(default="type0", description="type0 | type1 | typeSingle | typeMerge")
    read_only: bool = False
    has_multiple_sections: bool = False
    header_rows: Optional[List[int]] = None
    remarks_column_name: Optional[str] = None
    # User-facing, 3-option version of header_type. When provided, the backend
    # derives the real header_type instead of asking the user to pick "type0" vs
    # "type1" — that distinction is pure bookkeeping (both are parsed identically,
    # at header_row_index) and doesn't need to be a second decision:
    #   "single" -> "type0" if header_row_index == 0 else "type1"
    #   "none"   -> "typeSingle" (no header row at all; raw data passthrough)
    #   "merge"  -> "typeMerge"  (two header rows, at rows 0 and 1, combined)
    structure: Optional[str] = Field(default=None, description="single | none | merge")
    columns: List[WorkbookTabColumnIn] = []


class WorkbookTabUpdate(BaseModel):
    tab_name: Optional[str] = None
    header_row_index: Optional[int] = None
    header_type: Optional[str] = None
    read_only: Optional[bool] = None
    has_multiple_sections: Optional[bool] = None
    header_rows: Optional[List[int]] = None
    remarks_column_name: Optional[str] = None
    structure: Optional[str] = Field(default=None, description="single | none | merge")


class WorkbookSchemaOut(BaseModel):
    workbook_type: WorkbookTypeOut
    tabs: List[WorkbookTabOut]


class WorkbookRowIn(BaseModel):
    row_data: Dict[str, Any]
    section_id: Optional[int] = None


class WorkbookRowOut(BaseModel):
    id: SafeInt
    section_id: SafeOptionalInt = None
    row_data: Dict[str, Any]
    version: SafeInt
    created_by: SafeOptionalInt = None
    created_at: datetime
    modified_by: SafeOptionalInt = None
    modified_at: datetime
    model_config = {"from_attributes": True}


class ImportResultTab(BaseModel):
    tab_name: str
    status: str  # Valid / Needs Review / Invalid
    rows_imported: SafeInt
    errors: List[str] = []


class ImportResult(BaseModel):
    workbook_type: str
    version: SafeInt
    tabs: List[ImportResultTab]


class WorkbookVersionOut(BaseModel):
    id: SafeInt
    version: SafeInt
    tab_name: Optional[str] = None
    created_by: SafeOptionalInt = None
    created_at: datetime
    model_config = {"from_attributes": True}


class WorkbookErrorDetail(BaseModel):
    """Structured error payload for config/lookup failures (unknown workbook type, unknown
    tab, etc). The frontend uses `available` to offer the user a working alternative
    instead of dead-ending on a raw error string — this is what fixes the class of bug
    where a renamed/deleted tab left an invalid reference (e.g. the historical
    "Unknown tab 'Master output Final' in 'column_position'" error)."""
    error_code: str
    message: str
    available: List[str] = []


# ---------------------------------------------------------------------------
# Import PREVIEW -> resolve -> COMMIT schemas.
# Preview never writes to workbook_rows; it parses/validates the uploaded file and
# returns a structured, per-section report (detected columns, header-mapping status,
# a small sample of rows, and errors) so the UI can show it before anything is
# imported. The user can then supply header-mapping overrides and call /import/commit,
# which re-reads the same cached upload (by preview_id) and performs the real import.
# ---------------------------------------------------------------------------
class ImportHeaderMapping(BaseModel):
    source_header: str
    matched_column: Optional[str] = None
    suggested_column: Optional[str] = None
    # "matched"   -> file header already matches a configured column, used as-is
    # "unmatched" -> a configured (often required) column has no matching header in the file
    # "extra"     -> a header exists in the file with no matching configured column
    status: str


class ImportPreviewSection(BaseModel):
    section_name: Optional[str] = None
    header_row: SafeInt
    columns_detected: List[str] = []
    header_mappings: List[ImportHeaderMapping] = []
    missing_required: List[str] = []
    sample_rows: List[Dict[str, Any]] = []
    total_rows: SafeInt
    errors: List[str] = []
    warnings: List[str] = []
    status: str
    header_rows: Optional[List[List[str]]] = None  # raw 2-row header, only for typeMerge tabs

class ImportPreviewTab(BaseModel):
    tab_name: str
    found_in_file: bool
    sheet_used: Optional[str] = None
    sections: List[ImportPreviewSection] = []
    status: str
    # 1-based. Set only when the configured header row scored poorly (most/all
    # configured columns unmatched) and a different row in the first ~15 rows of the
    # sheet scored clearly better — the classic symptom of a blank spacer row sitting
    # above the real header, which otherwise shows up as every detected column being
    # "Unnamed: N" with the real header text appearing as the first data row instead.
    suggested_header_row: SafeOptionalInt = None

class ImportPreviewResult(BaseModel):
    preview_id: str
    workbook_type: str
    file_name: str
    tabs: List[ImportPreviewTab]



class ImportCommitOverride(BaseModel):
    header_mapping: Dict[str, str] = {}
    # Column names the admin explicitly chose to skip for this section (they don't
    # apply to this file) — distinct from simply not resolving them. Without this,
    # any configured column missing from the file blocks the whole import.
    ignored_columns: List[str] = []


class ImportCommitRequest(BaseModel):
    preview_id: str
    tab_names: Optional[List[str]] = None
    overrides: Dict[str, ImportCommitOverride] = {}
    # 1-based. Carries forward a header-row correction picked during preview (via
    # "Try row X") into the actual commit, for single-header (type0/type1) tabs only.
    header_row_override: Optional[int] = None


class DetectedSection(BaseModel):
    section_name: str
    header_row: SafeInt        # 1-based
    data_start_row: SafeInt    # 1-based
    column_start: SafeOptionalInt = None  # 1-based
    column_end: SafeOptionalInt = None    # 1-based, inclusive
    header_type: Optional[str] = None     # "typeMerge" when a merged group header was detected
    columns: List[DetectedColumn] = []


class SectionDetectionResult(BaseModel):
    tab_name: str
    has_multiple_sections: bool
    sections: List[DetectedSection]


class ConfigSectionSpec(BaseModel):
    section_name: str
    header_row: int = 1
    header_rows: Optional[List[int]] = None
    data_start_row: Optional[int] = None
    data_end_row: Optional[int] = None
    column_start: Optional[int] = None
    column_end: Optional[int] = None
    header_type: Optional[str] = None
    position: int = 0
    description: Optional[str] = None


class ConfigColumnSpec(BaseModel):
    column_name: str
    section_name: Optional[str] = None
    header_name: Optional[str] = None
    header_group: Optional[str] = None
    header_row: Optional[int] = None
    column_position: Optional[int] = None
    data_type: str = "string"
    required: bool = False
    unique: bool = False
    position: int = 0
    validation_rules: Optional[Dict[str, Any]] = None
    number_format: Optional[str] = None
    is_percentage: bool = False
    formula_config: Optional[Dict[str, Any]] = None


class ConfigTabSpec(BaseModel):
    tab_name: str
    structure: str = "single"
    header_row: int = 1
    header_rows: Optional[List[int]] = None
    remarks_column_name: Optional[str] = None
    read_only: bool = False
    has_multiple_sections: bool = False
    sections: List[ConfigSectionSpec] = []
    columns: List[ConfigColumnSpec] = []


class ConfigWorkbookTypeSpec(BaseModel):
    code: str
    name: str
    description: Optional[str] = None


class BulkConfigQuery(BaseModel):
    workbook_type: ConfigWorkbookTypeSpec
    tabs: List[ConfigTabSpec] = []


class BulkConfigResult(BaseModel):
    workbook_type: str
    tabs_created: SafeInt
    tabs_updated: SafeInt
    sections_created: SafeInt
    columns_created: SafeInt
    columns_updated: SafeInt

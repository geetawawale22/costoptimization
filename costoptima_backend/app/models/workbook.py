from sqlalchemy import Column, Integer, String, ForeignKey, JSON, Boolean, Text
from sqlalchemy.orm import relationship
from app.database import Base
from app.models.mixins import AuditMixin
from typing import Optional
import openpyxl.utils
# Matches the PCM Master Merge Process reference document exactly:
#   type0      - single header row, starts at row 1 (0-indexed: header_row_index=0)
#   type1      - header on row 2 (0-indexed: header_row_index=1), row 1 is a group label
#   typeSingle - no header at all; data copied through as-is
#   typeMerge  - two header rows (0 and 1) combined into one logical column name
HEADER_TYPES = ("type0", "type1", "typeSingle", "typeMerge")


class WorkbookType(Base, AuditMixin):
    __tablename__ = "workbook_types"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(255), unique=True, nullable=False)
    name = Column(String(255), nullable=False)
    description = Column(String(500), nullable=True)

    tabs = relationship("WorkbookTab", back_populates="workbook_type", cascade="all, delete-orphan")
    versions = relationship("WorkbookImportVersion", back_populates="workbook_type", cascade="all, delete-orphan")


class WorkbookTab(Base, AuditMixin):
    __tablename__ = "workbook_tabs"

    id = Column(Integer, primary_key=True, index=True)
    workbook_type_id = Column(Integer, ForeignKey("workbook_types.id"), nullable=False)
    tab_name = Column(String(255), nullable=False)
    header_row_index = Column(Integer, default=0)
    # One of HEADER_TYPES. Defaults to "type0" for backward compatibility with tabs
    # created before this field existed.
    header_type = Column(String(20), default="type0", nullable=False)
    read_only = Column(Boolean, default=False)
    # If True, this tab's source sheet may contain more than one table/header block;
    # columns must then be attached to a WorkbookSection rather than directly to the tab.
    has_multiple_sections = Column(Boolean, default=False, nullable=False)
    # 0-based header rows; supports any number of header/group rows.
    header_rows = Column(JSON, nullable=True)
    # Configured destination for source headers that are not otherwise mapped.
    remarks_column_name = Column(String(255), nullable=True)

    workbook_type = relationship("WorkbookType", back_populates="tabs")
    sections = relationship("WorkbookSection", back_populates="tab", cascade="all, delete-orphan",
                             order_by="WorkbookSection.position")
    columns = relationship("WorkbookTabColumn", back_populates="tab", cascade="all, delete-orphan",
                            order_by="WorkbookTabColumn.position")
    rows = relationship("WorkbookRow", back_populates="tab", cascade="all, delete-orphan")
    merged_ranges = relationship("WorkbookMergedRange", back_populates="tab", cascade="all, delete-orphan")


class WorkbookSection(Base, AuditMixin):
    __tablename__ = "workbook_sections"

    id = Column(Integer, primary_key=True, index=True)
    tab_id = Column(Integer, ForeignKey("workbook_tabs.id"), nullable=False)
    section_name = Column(String(255), nullable=False)
    header_row = Column(Integer, default=0, nullable=False)
    header_rows = Column(JSON, nullable=True)
    data_start_row = Column(Integer, nullable=True)
    # Row index (0-based, exclusive) where this section's data ends, OR null meaning
    # "read until the next section's header_row / a fully blank row, whichever comes first".
    data_end_row = Column(Integer, nullable=True)
    # Column scoping (0-based). Both null = whole sheet width, matching the original
    # row-only behavior. Needed because a worksheet can have tables placed side by
    # side (not just stacked) sharing the same row range — e.g. a rejection-rate table
    # in columns B:K and an unrelated index table in columns N:U on the same rows —
    # which row-only sections cannot distinguish.
    column_start = Column(Integer, nullable=True)
    column_end = Column(Integer, nullable=True)
    # Optional override of HEADER_TYPES scoped to just this section. Null means "single
    # header row at header_row" (the original behavior). "typeMerge" combines header_row
    # and header_row+1 into one label per column, same rule as WorkbookTab.header_type
    # — a single sheet can mix a plain single-row-header table with a grouped/merged-
    # header table (e.g. a merged cell spanning several columns as a group label with
    # per-column sub-labels on the row below).
    header_type = Column(String(20), nullable=True)
    position = Column(Integer, default=0)
    description = Column(String(500), nullable=True)

    tab = relationship("WorkbookTab", back_populates="sections")
    columns = relationship("WorkbookTabColumn", back_populates="section", cascade="all, delete-orphan",
                            order_by="WorkbookTabColumn.position")


class WorkbookTabColumn(Base, AuditMixin):
    __tablename__ = "workbook_tab_columns"

    id = Column(Integer, primary_key=True, index=True)
    tab_id = Column(Integer, ForeignKey("workbook_tabs.id"), nullable=False)
    # Nullable: only set when the parent tab has_multiple_sections=True.
    section_id = Column(Integer, ForeignKey("workbook_sections.id"), nullable=True)

    # Canonical/internal name used everywhere in the app (row_data keys, UI headers).
    column_name = Column(String(255), nullable=False)
    # The literal header text expected in the uploaded source file. Defaults to
    # column_name when not explicitly set. This is what the import-preview mapping
    # screen edits when a file's header doesn't match ("Customer Name" vs "Customer_Name").
    header_name = Column(String(255), nullable=True)
    # Optional group/parent label for a 2-row Excel-style header (e.g. "Conditions"
    # over several columns, "{Blank Consumption} Calculation" over others) — purely for
    # display: rendered as a merged top row above the normal column-name row in both
    # the data table and the import preview. Unrelated to header_type="typeMerge",
    # which is about how the file is *parsed*; this is about how it's *displayed*.
    header_group = Column(String(255), nullable=True)
    # Optional per-column override of the section/tab header row.
    header_row = Column(Integer, nullable=True)
    # Optional explicit 0-based column index in the source sheet (replaces the old
    # unlinked "Column Position.xlsx" master file with real configuration).
    column_position = Column(Integer, nullable=True)

    data_type = Column(String(255), default="string")
    is_unique_key = Column(Boolean, default=False)
    is_required = Column(Boolean, default=False)
    position = Column(Integer, default=0)
    validation_rules = Column(JSON, nullable=True)
    # Excel display semantics and source formula definition.
    number_format = Column(String(255), nullable=True)
    is_percentage = Column(Boolean, default=False, nullable=False)
    formula_config = Column(JSON, nullable=True)

    tab = relationship("WorkbookTab", back_populates="columns")
    section = relationship("WorkbookSection", back_populates="columns")

    def effective_header(self) -> str:
        return self.header_name or self.column_name

    @property
    def excel_column(self) -> Optional[str]:
        if self.column_position is None:
            return None
        return openpyxl.utils.get_column_letter(self.column_position + 1)


class WorkbookRow(Base, AuditMixin):
    __tablename__ = "workbook_rows"

    id = Column(Integer, primary_key=True, index=True)
    tab_id = Column(Integer, ForeignKey("workbook_tabs.id"), nullable=False)
    # Nullable: only set for rows imported into a tab that has_multiple_sections=True.
    section_id = Column(Integer, ForeignKey("workbook_sections.id"), nullable=True)
    row_data = Column(JSON, nullable=False, default=dict)
    version = Column(Integer, nullable=False, default=1)

    tab = relationship("WorkbookTab", back_populates="rows")


class WorkbookImportVersion(Base, AuditMixin):
    __tablename__ = "workbook_import_versions"

    id = Column(Integer, primary_key=True, index=True)
    workbook_type_id = Column(Integer, ForeignKey("workbook_types.id"), nullable=False)
    version = Column(Integer, nullable=False)
    tab_name = Column(String(255), nullable=True)
    snapshot = Column(JSON, nullable=True)

    workbook_type = relationship("WorkbookType", back_populates="versions")


class WorkbookMergedRange(Base, AuditMixin):
    __tablename__ = "workbook_merged_ranges"

    id = Column(Integer, primary_key=True, index=True)
    tab_id = Column(Integer, ForeignKey("workbook_tabs.id"), nullable=False)
    worksheet_name = Column(String(255), nullable=False)
    merged_range = Column(String(64), nullable=False)   # e.g. "D2:F2"
    top_left_cell = Column(String(16), nullable=False)  # e.g. "D2"
    start_row = Column(Integer, nullable=False)     # 1-based, inclusive, matches Excel
    end_row = Column(Integer, nullable=False)
    start_column = Column(Integer, nullable=False)  # 1-based, inclusive, matches Excel
    end_column = Column(Integer, nullable=False)
    value = Column(Text, nullable=True)  # top-left cell's raw value at capture time, stringified

    tab = relationship("WorkbookTab", back_populates="merged_ranges")
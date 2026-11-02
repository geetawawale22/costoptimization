"""Shared PCM input reader; uploaded cells never supply master data or formulas.

Legacy reference: PCM_5/Sheet_Metal_Forging_with summary_chk_Chnages.py,
make_new:14588+, part1:1548+, gdc_casting:8718+, and dispatcher:19821+.
Those functions read differently positioned BOM/detail headers. This parser detects
named headers and joins detail records by business identity rather than row order.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import re
from typing import Any
from zipfile import BadZipFile, ZipFile

from openpyxl import load_workbook
from openpyxl.utils.cell import column_index_from_string
from xml.etree.ElementTree import iterparse

MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_EXPANDED_BYTES = 256 * 1024 * 1024
MAX_ROWS = 100_000
MAX_COLUMNS = 2_048
MAX_SHEETS = 128
CORE_HEADERS = {"Customer No", "Type", "Supplier", "End Item", "Qty/Veh", "Commodity"}
IDENTIFIER_HEADERS = {"Customer No", "Supplier No", "PF Code", "Material Code", "PCBA Part Number", "Sr", "Make Part no"}
KEY_HEADERS = ("Customer No", "Supplier", "Type", "End Item")
IDENTITY_HEADERS = set(KEY_HEADERS) | {"Commodity"}
_ALIASES = {
    "customerno": "Customer No", "customernumber": "Customer No",
    "supplierno": "Supplier No", "suppliernumber": "Supplier No",
    "supplier": "Supplier", "suppliername": "Supplier",
    "enditem": "End Item", "type": "Type", "commodity": "Commodity",
    "qtyveh": "Qty/Veh", "qtyvehicle": "Qty/Veh", "qtypervehicle": "Qty/Veh",
    "description": "Desc", "desc": "Desc", "pfcode": "PF Code",
    "materialcode": "Material Code", "rmcode": "Material Code",
    "partname": "Part Name", "specialprocess": "Special Process",
}
_DETAIL_FAMILIES = {
    "hard": "HARD", "soft": "SOFT", "stamping": "STAMPING",
    "sandcasting": "SAND CASTING", "sandinput": "SAND CASTING", "gdc": "GDC", "gdcinput": "GDC",
    "pdc": "PDC", "pdcinput": "PDC", "forging": "FORGING",
    "thermal": "THERMAL", "thermalinput": "THERMAL", "pcba": "PCBA",
    "pcbainput": "PCBA",
}


class InputValidationError(ValueError):
    """An unreadable, unsupported or structurally unsafe workbook."""


@dataclass
class ParsedWorkbook:
    records: list[dict[str, Any]] = field(default_factory=list)
    issues: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not any(item.get("severity") == "error" for item in self.issues)


def _token(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())


def normalize_header(value: Any) -> str:
    """Normalize actual header spelling, retaining unknown business field names."""
    if value is None:
        return ""
    clean = re.sub(r"\s+", " ", str(value)).strip()
    return _ALIASES.get(_token(clean), clean)


def _key_value(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value if value is not None else "")).strip().casefold()


def business_key(record: dict[str, Any]) -> tuple[str, str, str, str]:
    return tuple(_key_value(record.get(name)) for name in KEY_HEADERS)


def _detail_context_conflicts(owner: dict[str, Any], detail: dict[str, Any], family: str) -> list[str]:
    """Keep named ownership boundaries consistent for every detail join.

    PCM separates family input tables (make_new14602-14617, part11548+).
    Master Output owns BOM labels (15153-15184), while its result lookup uses
    Customer No in the selected supplier/family output (15393-15455). Project
    and PF Code disagreements remain review warnings; they are not a proven
    extra join key and cannot disambiguate otherwise duplicate details.
    """
    conflicts = []
    commodity = _key_value(owner.get("Commodity"))
    if family and _key_value(family) != commodity:
        conflicts.append("Source family")
    if detail.get("Commodity") not in (None, "") and _key_value(detail["Commodity"]) != commodity:
        conflicts.append("Commodity")
    return conflicts


def _input_value(cell: Any, header: str) -> Any:
    value = cell.value
    if value is None or header not in IDENTIFIER_HEADERS:
        return value
    if isinstance(value, bool) or isinstance(value, (date, datetime)):
        return value
    if isinstance(value, (int, float)):
        if int(value) == value:
            # Excel stores an identifier's leading zeros in the number format.
            # Apply only a verified simple zero mask on explicitly named ID fields.
            fmt = str(cell.number_format or "")
            if re.fullmatch(r"0+", fmt):
                return str(int(value)).zfill(len(fmt))
            return str(int(value))
        return str(value)
    return value


def _read_bytes(source: bytes | bytearray | str | Path) -> tuple[bytes, str | None]:
    filename = None
    if isinstance(source, (str, Path)):
        path = Path(source)
        if path.suffix.casefold() not in {".xlsx", ".xlsm"}:
            raise InputValidationError("Only .xlsx and .xlsm input workbooks are supported.")
        if path.stat().st_size > MAX_FILE_BYTES:
            raise InputValidationError("Input workbook exceeds the 64 MiB limit.")
        data, filename = path.read_bytes(), path.name
    elif isinstance(source, (bytes, bytearray)):
        data = bytes(source)
    else:
        raise TypeError("source must be workbook bytes or a filesystem path")
    if not data or len(data) > MAX_FILE_BYTES:
        raise InputValidationError("Input workbook is empty or exceeds the 64 MiB limit.")
    try:
        with ZipFile(BytesIO(data)) as archive:
            names = set(archive.namelist())
            if "[Content_Types].xml" not in names or "xl/workbook.xml" not in names:
                raise InputValidationError("The file is not an Excel OOXML workbook.")
            if len(names) > 10_000 or sum(item.file_size for item in archive.infolist()) > MAX_EXPANDED_BYTES:
                raise InputValidationError("Expanded workbook exceeds the safe parsing limit.")
            if any(item.flag_bits & 1 for item in archive.infolist()):
                raise InputValidationError("Encrypted workbooks are not supported.")
    except BadZipFile as exc:
        raise InputValidationError("The file is not a valid .xlsx/.xlsm workbook.") from exc
    return data, filename


def _header_row(sheet: Any) -> tuple[int, list[str]] | None:
    """Find the strongest named-header signature; no fixed business row number."""
    candidate = None
    for physical_row, row in enumerate(sheet.iter_rows(), 1):
        if not row:
            continue
        values = [normalize_header(cell.value) for cell in row]
        signature = set(values)
        score = len(CORE_HEADERS & signature)
        if {"Customer No", "Type"}.issubset(signature) and score >= 3:
            if candidate is None or score > candidate[0]:
                candidate = (score, physical_row, values)
            if score == len(CORE_HEADERS):
                break
    return None if candidate is None else (candidate[1], candidate[2])


def _issue(issues: list[dict[str, Any]], code: str, sheet: str, row: int | None,
           message: str, severity: str = "warning", **details: Any) -> None:
    issues.append({"code": code, "severity": severity, "sheet": sheet,
                   "row": row, "message": message, "blocking": severity == "error", **details})


def _paint_records(sheet, formula_sheet, workbook_id, issues):
    """PCM paint598-654: two-row Paint header and Sr-started groups."""
    signature={'Sr','Supplier','End Item','Make Part no','Customer No','Material Code','Consumption (in Litre)','No of parts / Jig'}
    detected=None;previous=None;period=None
    for physical_row, cells in enumerate(formula_sheet.iter_rows(), 1):
        names=[normalize_header(cell.value) for cell in cells]
        if signature.issubset(set(names)):
            detected=(physical_row,names)
            break
        if 'RM Base' in names:
            index=names.index('RM Base')
            period=next((cell.value for cell in cells[index+1:] if cell.value not in (None,'')),None)
    if detected is None:
        _issue(issues,'missing_paint_headers',sheet.title,None,'Paint lacks its original named group/header signature.','error')
        return [],{'sheet':sheet.title,'kind':'paint','header_row':None,'records':0}
    row_number,names=detected
    duplicates=[name for name,count in Counter(names).items() if name and count>1]
    if duplicates:
        _issue(issues,'duplicate_headers',sheet.title,row_number,'Paint duplicate headers are ambiguous.','error',headers=duplicates)
    rows=[];anchor=None
    data_rows=sheet.iter_rows(min_row=row_number+1)
    formula_rows=formula_sheet.iter_rows(min_row=row_number+1)
    for physical_row,(cells,formula_cells) in enumerate(zip(data_rows,formula_rows),row_number+1):
        values={name:_input_value(cell,name) for name,cell in zip(names,cells) if name}
        for name,cell,formula_cell in zip(names,cells,formula_cells):
            if name and formula_cell.data_type=='f' and cell.value is None:
                _issue(issues,'uncached_input_formula',sheet.title,physical_row,
                       'Input formula has no cached result; it is never executed by the parser.',
                       'error',field=name)
        if not any(value not in (None,'') for value in values.values()):continue
        identity=f'{workbook_id}:{sheet.title}:{physical_row}'
        values.update({'_record_id':identity,'_sheet':sheet.title,'_row':physical_row,'_input_headers':names,'RM Base':period})
        if values.get('Sr') not in (None,''):
            anchor=values
        elif anchor is None:
            _issue(issues,'paint_group_anchor_missing',sheet.title,physical_row,'Paint continuation precedes its Sr anchor.','error')
            continue
        if anchor is not values:
            # Only ownership grouping propagates; never copy paint quantities or costs.
            if values.get('End Item') in (None,''):values['End Item']=anchor.get('End Item')
        values['_paint_anchor_id']=anchor['_record_id']
        rows.append(values)
    return rows,{'sheet':sheet.title,'kind':'paint','header_row':row_number,'records':len(rows)}



def _preheader_period(sheet, formulas, header_row, issues):
    """Original part1/part1_stamp grouped RM Base and HARD named fallback."""
    candidates=[]
    for row, expression_row in zip(sheet.iter_rows(max_row=header_row-1), formulas.iter_rows(max_row=header_row-1)):
        for index,cell in enumerate(row[:-1]):
            if normalize_header(cell.value) != 'RM Base':continue
            value=row[index+1].value
            if expression_row[index+1].data_type=='f' and value is None:
                _issue(issues,'uncached_period_formula',sheet.title,cell.row,'Grouped RM Base has no cached value.','error',field='RM Base')
            candidates.append(value)
    if not candidates:return None
    if len(candidates)!=1 or candidates[0] in (None,''):
        _issue(issues,'ambiguous_grouped_period',sheet.title,header_row,'Grouped RM Base must have one nonblank adjacent period.','error',field='RM Base')
        return None
    return candidates[0]


def _unsupported_input_projection(data, values_book, formulas_book, parsed_sheets, issues, filename, workbook_id):
    """Preserve a safe readable file, without inventing no-BOM costing owners."""
    records=[];copies=[];budget=0
    reason='No unique BOM ownership contract is available; readable data is preserved without costing.'
    _issue(issues,'unsupported_bom_structure',None,None,reason,'error')
    for rows,info in parsed_sheets:
        for source in rows:
            item=dict(source);item['_dependency_blocked']=True;item['_input_blocked_reason']=reason
            item.setdefault('_variants',{});records.append(item)
        sheet=values_book[info['sheet']];formulas=formulas_book[info['sheet']]
        last_row,last_column=_actual_dimensions(data,sheet)
        if not last_row or not last_column:continue
        if last_row*last_column>MAX_INPUT_COPY_RECTANGLE or last_column>MAX_COLUMNS or last_row>MAX_ROWS:
            _issue(issues,'input_copy_limit',sheet.title,None,'Input sheet exceeds safe output projection bounds.','warning');continue
        cells=[];overflow=False
        for number,(row,formula_row) in enumerate(zip(sheet.iter_rows(max_row=last_row,max_col=last_column),formulas.iter_rows(max_row=last_row,max_col=last_column)),1):
            for col,(cell,expression) in enumerate(zip(row,formula_row),1):
                value=cell.value if cell.value is not None else expression.value if expression.data_type=='f' else None
                if value is None:continue
                if budget+len(cells)>=MAX_INPUT_COPY_CELLS:overflow=True;break
                cells.append([number,col,value,str(cell.number_format or 'General')])
            if overflow:break
        if overflow:
            _issue(issues,'input_copy_limit',sheet.title,None,'Input sheet exceeds safe stored-cell output budget.','warning');continue
        budget+=len(cells)
        copies.append({'family':'UNASSIGNED','title':('Input_'+sheet.title)[:31],'source_sheet':sheet.title,'always_include':True,
            'source_header_row':info.get('header_row') or 1,'export_header_row':info.get('header_row') or 1,'rows':last_row,'columns':last_column,'cells':cells})
    return ParsedWorkbook(records,issues,{'filename':filename,'sha256':workbook_id,'sheets':[info for _,info in parsed_sheets],
        'record_count':len(records),'detail_record_count':0,'branch_counts':[],'variant_headers':[],
        'input_sheet_copies':copies,'unsupported_bom_structure':True,'unassigned_details':[]})


def _sheet_records(sheet: Any, formula_sheet: Any, workbook_id: str,
                   issues: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if _token(sheet.title)=="paint":return _paint_records(sheet,formula_sheet,workbook_id,issues)
    detected = _header_row(formula_sheet)
    pcba_signature={"PCBA Part Number", "Material Code", "Supplier", "End Item", "RM Supplier"}
    if _token(sheet.title)=="pcba":
        for physical_row, cells in enumerate(formula_sheet.iter_rows(), 1):
            names=[normalize_header(cell.value) for cell in cells]
            if pcba_signature.issubset(set(names)):
                detected=(physical_row,names)
                break
    pcba_components=bool(detected and _token(sheet.title)=="pcba"
                         and pcba_signature.issubset(set(detected[1])))
    if not detected:
        # Original make_new/assy falls back to BOM if the Hard Type header
        # cannot be selected, not just when a worksheet is absent. A malformed
        # declared Type header must not borrow unrelated BOM rows silently.
        hard_type_header = False
        if _token(sheet.title) == "hard":
            for cells in formula_sheet.iter_rows():
                names = {normalize_header(cell.value) for cell in cells}
                if "Type" in names and len(names & CORE_HEADERS) >= 2:
                    hard_type_header = True
                    break
        return [], {"sheet": sheet.title, "kind": "supporting", "header_row": None, "records": 0,
                    "family": _DETAIL_FAMILIES.get(_token(sheet.title), ""),
                    "declared_type_header_present": hard_type_header}
    header_row, raw_headers = detected
    duplicates = [name for name, count in Counter(raw_headers).items() if name and count > 1]
    if duplicates:
        _issue(issues, "duplicate_headers", sheet.title, header_row,
               "Duplicate input headers require review; values are retained under distinct names.",
               "error", headers=duplicates)
    seen: Counter[str] = Counter()
    headers = []
    for column, name in enumerate(raw_headers, 1):
        if not name:
            headers.append(f"__unnamed_{column}")
            continue
        seen[name] += 1
        headers.append(name if seen[name] == 1 else f"{name}__duplicate_{seen[name]}")
    family = _DETAIL_FAMILIES.get(_token(sheet.title), '')
    period = _preheader_period(sheet,formula_sheet,header_row,issues) if family in {'HARD','SOFT','STAMPING'} and header_row>1 else None
    records = []
    formula_rows = formula_sheet.iter_rows(min_row=header_row + 1)
    for physical_row, (row, formula_row) in enumerate(zip(sheet.iter_rows(min_row=header_row + 1), formula_rows), header_row + 1):
        if all(cell.value is None for cell in row) and all(cell.value is None for cell in formula_row):
            continue
        values = {}
        for column, (cell, formula_cell, header) in enumerate(zip(row, formula_row, headers), 1):
            value = _input_value(cell, header)
            if formula_cell.data_type == "f" and value is None:
                _issue(issues, "uncached_input_formula", sheet.title, physical_row,
                       "Input formula has no cached result; it is never executed by the parser.",
                       "error", field=header)
            if header.startswith("__unnamed_") and value is not None:
                _issue(issues, "unnamed_data_column", sheet.title, physical_row,
                       "A populated column has no business header; preserved as an unassigned annotation and never used as a calculation dependency.", "warning", column=column)
            if not header.startswith("__unnamed_") or value is not None:
                values[header] = value
        # Supporting footers and instructions are not calculation rows.
        if not any(values.get(name) is not None for name in ("Customer No", "Type", "Supplier", "End Item")):
            continue
        original_values = dict(values)
        if period is not None:
            values['_preheader_period']=period
            if family in {'SOFT','STAMPING'}:values['RM Base']=period
        if pcba_components:
            # Original pcba_op:13946-13995 joins component rows through this
            # named board identifier; component rows are not separate BOM rows.
            values["Customer No"]=values.get("PCBA Part Number")
            values["_pcba_component"]=True
            values["_pcba_quantity_headers"]=[name for name in headers[headers.index("RM Supplier")+1:]
                                              if not name.startswith("__")]
        values["Commodity"] = str(values.get("Commodity") or "").strip().upper()
        values["Type"] = str(values.get("Type") or "").strip().upper()
        values["_original_values"] = original_values
        values["_sheet"] = sheet.title
        values["_row"] = physical_row
        values["_record_id"] = f"{workbook_id}:{sheet.title}:{physical_row}"
        values["_variants"] = _variant_values(values, headers)
        values["_business_key"] = list(business_key(values))
        values["_input_headers"] = headers
        _attach_process_lines(values, headers, _DETAIL_FAMILIES.get(_token(sheet.title), ""))
        records.append(values)
        if values.get("Customer No") in (None, ""):
            _issue(issues, "missing_customer_number", sheet.title, physical_row,
                   "Calculation row has no Customer No.", "error")
    family = _DETAIL_FAMILIES.get(_token(sheet.title), "")
    return records, {"sheet": sheet.title, "kind": "pcba_components" if pcba_components else "bom" if _token(sheet.title) == "bom" else "detail",
                     "family": family, "header_row": header_row, "headers": headers, "records": len(records)}


def _variant_values(values: dict[str, Any], headers: list[str]) -> dict[str, Any]:
    """Variants occupy the named region after BOM level and before Supplier.

    The boundary is detected by header identity, never by fixed Excel coordinates.
    This preserves Base/Mid/High, W1-W3 and Pack 1-Pack 4 from supplied files.
    """
    level_names = {"0123", "level", "bomlevel"}
    level_index = next((i for i, name in enumerate(headers) if _token(name) in level_names), None)
    supplier_index = next((i for i, name in enumerate(headers) if name == "Supplier"), None)
    if level_index is None or supplier_index is None or supplier_index <= level_index:
        return {}
    return {name: values.get(name) for name in headers[level_index + 1:supplier_index]
            if not name.startswith("__")}


def _attach_process_lines(values: dict[str, Any], headers: list[str], family: str) -> None:
    # Verified original header boundaries, not master column positions:
    # part1 3390/3472, part1_stamp equivalent, forging12735-12867,
    # gdc_casting8752-8775, pdc16904+, sand_casting_for_range.
    boundary = {
        "SOFT": "Actual Gross Consumption", "STAMPING": "Actual Gross Consumption",
        "GDC": "Process", "PDC": "Process", "SAND CASTING": "Process",
        "FORGING": "Hardening & Tempering",
    }.get(family)
    def lines(names: list[str]) -> list[dict[str, Any]]:
        return [{"name": name, "quantity": values.get(name), "_field": name,
                 "_sheet": values["_sheet"], "_row": values["_row"]}
                for name in names if not name.startswith("__") and values.get(name) not in (None, "")]
    if family == "STAMPING" and boundary not in headers and "Yield (%)" in headers:
        # Original preprocessing stamping567-584 uses Yield when the optional
        # Actual Gross Consumption header is absent, never a fixed index.
        boundary = "Yield (%)"
    if boundary in headers:
        values["_process_lines"] = lines(headers[headers.index(boundary) + 1:])
        values["Process Lines"] = values["_process_lines"]
    if family == "PDC" and "Cavity" in headers and "Process" in headers:
        candidates=[name for name in headers[headers.index("Cavity")+1:headers.index("Process")] if not name.startswith("__")]
        if candidates:
            # Original pdc17040 uses the first named input machine column in
            # this detected region. This is input parsing, not a master index.
            values["PDC Machine Process"]=candidates[0]
            values["PDC Machine Minutes"]=values.get(candidates[0])
    if family == "FORGING" and "Yield (%)" in headers and boundary in headers:
        values["_hardening_lines"] = lines(headers[headers.index("Yield (%)") + 1:headers.index(boundary) + 1])



def _sbc_layout(workbook, variant_names, issues):
    """Read original SBC's semantic variant keys and separate display labels.

    PCM end_item:10815-10862 and output:17562-17619 use the second header
    row as BOM variant keys and the preceding row as presentation labels.
    This reads input layout metadata only; it never uses cached costing cells.
    """
    names = [name for name in workbook.sheetnames if _token(name) == "sbc"]
    if not names:
        return None
    if len(names) != 1:
        _issue(issues, "ambiguous_sbc_layout", "SBC", None,
               "Several worksheets match SBC; no arbitrary layout was selected.", "error")
        return None
    sheet = workbook[names[0]]
    previous = None
    for cells in sheet.iter_rows():
        headers = [normalize_header(cell.value) for cell in cells]
        if headers[:3] == ["End Item", "Customer No", "Desc"]:
            variants = []
            seen = set()
            for index, key in enumerate(headers[3:], 3):
                if not key:
                    continue
                if key in seen or key not in variant_names:
                    _issue(issues, "invalid_sbc_variant", sheet.title,
                           cells[0].row, "SBC variant must uniquely match a named BOM variant.",
                           "error", variant=key)
                    continue
                seen.add(key)
                raw_label = previous[index] if previous is not None and index < len(previous) else None
                variants.append({"key": key, "label": key if raw_label is None else str(raw_label)})
            return {"identity_headers": headers[:3], "variants": variants,
                    "source_sheet": sheet.title, "header_row": cells[0].row}
        previous = [cell.value for cell in cells]
    _issue(issues, "missing_sbc_headers", sheet.title, None,
           "SBC lacks its original End Item, Customer No and Desc header signature.", "error")
    return None



def _actual_dimensions(data,sheet):
    """Ignore trailing style-only cells, not data, when Excel dimensions inflate.

    Bound real stored values/formulas before openpyxl expands rectangular rows.
    Coordinates belong solely to file parsing; they never identify master fields.
    Existing ZIP expansion and meaningful row/column limits remain enforced.
    """
    max_row=max_column=0
    with ZipFile(BytesIO(data)) as archive:
        with archive.open(sheet._worksheet_path.lstrip('/')) as xml:
            for _,element in iterparse(xml,events=('end',)):
                kind=element.tag.rsplit('}',1)[-1]
                if kind=='c':
                    meaningful=any(child.tag.rsplit('}',1)[-1] in {'v','f','t'} and child.text not in (None,'') for child in element.iter())
                    if meaningful:
                        match=re.fullmatch(r'([A-Z]+)([1-9][0-9]*)',element.get('r',''))
                        if not match:raise InputValidationError('Worksheet cell coordinate is invalid.')
                        column=column_index_from_string(match[1]);row=int(match[2])
                        if row>MAX_ROWS or column>MAX_COLUMNS:
                            raise InputValidationError(f'Worksheet {sheet.title!r} contains data beyond the safe parsing dimensions.')
                        max_row=max(max_row,row);max_column=max(max_column,column)
                    element.clear()
                elif kind=='row':element.clear()
    return max_row or 1,max_column or 1


# Original final merge copies only these commodity input projections. They are
# output evidence, never a master provider or another calculation-input system.
_INPUT_COPY_TARGETS = {
    'soft': ('SOFT', 'Soft', False),
    'stamping': ('STAMPING', 'Stamping', False),
    'forging': ('FORGING', 'Forging', False),
    'thermal': ('THERMAL', 'Thermal', False),
    'thermalinput': ('THERMAL', 'Thermal', False),
    'gdc': ('GDC', 'GDC_Casting_Input', True),
    'gdcinput': ('GDC', 'GDC_Casting_Input', True),
    'gdccastinginput': ('GDC', 'GDC_Casting_Input', True),
    'pdc': ('PDC', 'PDC_Input', True),
    'pdcinput': ('PDC', 'PDC_Input', True),
    'sandcasting': ('SAND CASTING', 'Sand_Casting_Input', True),
    'sandinput': ('SAND CASTING', 'Sand_Casting_Input', True),
    'sandcastinginput': ('SAND CASTING', 'Sand_Casting_Input', True),
}
MAX_INPUT_COPY_CELLS = 250_000
MAX_INPUT_COPY_RECTANGLE = 1_000_000


def _input_sheet_copies(data, values_book, formulas_book, sheet_metadata, issues):
    """Bounded, inert input-sheet evidence for the one final workbook writer.

    Primary PCM1559/4009/12887/8758/16890/7164/15682 produces these source
    sheets; final merge17741-18250 copies their family workbooks conditionally.
    Casting DataFrame projections move the detected source header to output1;
    Soft/Stamping keep their original grouped/header rows. Cached input formula
    values are copied as values only. An uncached expression stays literal text,
    never a formula, hyperlink, macro, external link or calculation dependency.
    """
    result=[];count=0
    for info in sheet_metadata:
        target=_INPUT_COPY_TARGETS.get(_token(info['sheet']))
        if not target or info.get('kind')!='detail' or not info.get('header_row'):
            continue
        family,title,project_header=target
        sheet=values_book[info['sheet']];formula_sheet=formulas_book[info['sheet']]
        last_row,last_column=_actual_dimensions(data,sheet)
        source_header=info['header_row'];first_row=source_header if project_header else 1
        if (last_row-first_row+1)*last_column>MAX_INPUT_COPY_RECTANGLE:
            _issue(issues,'input_copy_limit',sheet.title,None,
                   'Input reference copy omitted: bounded output evidence rectangle exceeded.','warning')
            continue
        cells=[];overflow=False
        values=sheet.iter_rows(min_row=first_row,max_row=last_row,max_col=last_column)
        formulas=formula_sheet.iter_rows(min_row=first_row,max_row=last_row,max_col=last_column)
        headers=info.get('headers',[])
        for row_number,(row,formula_row) in enumerate(zip(values,formulas),first_row):
            for column,(cell,formula_cell) in enumerate(zip(row,formula_row),1):
                header=headers[column-1] if column<=len(headers) and row_number>source_header else ''
                value=_input_value(cell,header)
                if formula_cell.data_type=='f' and value is None:
                    value=formula_cell.value
                if value is None:continue
                if count+len(cells)>=MAX_INPUT_COPY_CELLS:
                    overflow=True;break
                cells.append([row_number-first_row+1,column,value,str(cell.number_format or 'General')])
            if overflow:break
        if overflow:
            _issue(issues,'input_copy_limit',sheet.title,None,
                   'Input reference copy omitted: bounded stored-cell budget exceeded.','warning')
            continue
        count+=len(cells)
        result.append({'source_sheet':sheet.title,'family':family,'title':title,
                       'source_header_row':source_header,'export_header_row':source_header-first_row+1,
                       'rows':last_row-first_row+1,'columns':last_column,'cells':cells,
                       'formula_policy':'cached value or inert expression text; never execute'})
    return result


def parse_workbook(source: bytes | bytearray | str | Path, *, partial_output=False) -> ParsedWorkbook:
    """Read once into normalized BOM records plus matched commodity details.

    BOM identity, quantity, supplier, commodity, type, ownership and variant flags
    remain authoritative. Detail values enrich calculation inputs and are also
    retained verbatim in _details. Differences are explicit issues, never hidden
    master data or guessed formulas. No database, macro or Excel execution occurs.
    """
    data, filename = _read_bytes(source)
    workbook_id = sha256(data).hexdigest()[:16]
    issues: list[dict[str, Any]] = []
    try:
        values_book = load_workbook(BytesIO(data), read_only=True, data_only=True, keep_links=False)
        formulas_book = load_workbook(BytesIO(data), read_only=True, data_only=False, keep_links=False)
    except Exception as exc:
        raise InputValidationError("The Excel workbook cannot be parsed.") from exc
    try:
        if len(values_book.worksheets) > MAX_SHEETS:
            raise InputValidationError("Input workbook has too many worksheets.")
        parsed_sheets = []
        sheet_metadata = []
        for sheet in values_book:
            if (sheet.max_row or 0)>MAX_ROWS or (sheet.max_column or 0)>MAX_COLUMNS:
                last_row,last_column=_actual_dimensions(data,sheet)
                # Read-only worksheet bounds are internal reader state only.
                sheet._max_row=last_row;sheet._max_column=last_column
                formulas_book[sheet.title]._max_row=last_row
                formulas_book[sheet.title]._max_column=last_column
                _issue(issues,'trimmed_empty_formatting',sheet.title,None,'Trailing style-only cells were excluded from parsing; all stored values/formulas remain bounded and retained.','info')
            rows, info = _sheet_records(sheet, formulas_book[sheet.title], workbook_id, issues)
            parsed_sheets.append((rows, info))
            sheet_metadata.append(info)
        bom_sets = [(rows, info) for rows, info in parsed_sheets if info["kind"] == "bom"]
        if len(bom_sets)!=1 and partial_output:
            return _unsupported_input_projection(data,values_book,formulas_book,parsed_sheets,issues,filename,workbook_id)
        if not bom_sets:
            raise InputValidationError(
                "A BOM worksheet with Customer No and Type headers is required. "
                "The external no-BOM/injection-molding workflow is not reconstructed.")
        if len(bom_sets) != 1:
            raise InputValidationError("Exactly one BOM worksheet is required.")
        bom_rows = bom_sets[0][0]
        detail_families_present = {_DETAIL_FAMILIES[_token(sheet.title)]
                                   for sheet in values_book if _token(sheet.title) in _DETAIL_FAMILIES}
        hard_detail_usable = any(info.get("family") == "HARD" and
                                 (info["kind"] == "detail" or info.get("declared_type_header_present"))
                                 for _, info in parsed_sheets)
        for row in bom_rows:
            if row["Commodity"] in {"STAMPING", "FORGING"} and row["Commodity"] not in detail_families_present:
                # Original PCM_Phase_3_generate_input.stamping499-597 and
                # forging_ip297-370 project BOM fields only when that family
                # sheet is missing (generate_input695-738). An existing family
                # sheet with unmatched/invalid details does not authorize a
                # second, silently competing owner/calculation input.
                _attach_process_lines(row, row["_input_headers"], row["Commodity"])
                row["_bom_family_projection"] = row["Commodity"]
        detail_index: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
        detail_families = {}
        paint_rows=[row for rows,info in parsed_sheets if info["kind"]=="paint" for row in rows]
        soft_rows=[row for rows,info in parsed_sheets if info.get("family")=="SOFT" for row in rows]
        pcba_rows = [row for rows, info in parsed_sheets if info["kind"]=="pcba_components" for row in rows]
        pcba_index = defaultdict(list)
        for component in pcba_rows:
            pcba_index[_key_value(component.get("PCBA Part Number"))].append(component)
        used_pcba = set()
        for rows, info in parsed_sheets:
            if info["kind"] != "detail":
                continue
            # PF/SBC/output tables are not commodity detail inputs.
            if not info.get("family") and not any(row.get("Commodity") for row in rows):
                continue
            for row in rows:
                detail_index[business_key(row)].append(row)
                detail_families[row["_record_id"]] = info.get("family", "")
        # Original SOFT part1 branches on detail Type; BOM Type owns final output.
        # A unique identity match may therefore legitimately have different Types.
        detail_owner_index: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
        for detail_rows in detail_index.values():
            for detail in detail_rows:
                key = business_key(detail)
                detail_owner_index[(key[0], key[1], key[3])].append(detail)
        used_details = set()
        records = []
        owner_fields = IDENTITY_HEADERS | {"Qty/Veh", "PF Code", "Project", "Part Name"}
        counts = Counter(business_key(row) for row in bom_rows)
        # Primary bystd6571-6590, buynonstd6619-6626, PKG/FRT6709-6782
        # and consign10752-10786 preserve every purchasing occurrence and its
        # quantity/variant fields. Their repeated customer key is a shared DB
        # price key, not a duplicate calculation node. Never apply this rule
        # to a manufacturing producer, mixed-commodity group or END ITEM.
        occurrence_types = {"BUY STD", "BUY NON STD", "CONSIGN", "PKG", "FRT"}
        purchasing_occurrences = {business_key(row) for row in bom_rows
                                  if row["Commodity"] == "" and row["Type"] in occurrence_types}
        purchasing_occurrences -= {business_key(row) for row in bom_rows if row["Commodity"]}
        pcba_owners = defaultdict(list)
        for owner in bom_rows:
            if owner["Commodity"] == "PCBA":
                pcba_owners[_key_value(owner.get("Customer No"))].append(owner)
        for row in bom_rows:
            record = dict(row)
            # Server-owned identity: an uploaded private/public column cannot
            # choose the supplier used for rate resolution.
            record["_costing_supplier"] = row.get("Supplier")
            record["_bom_values"] = dict(row["_original_values"])
            record["_bom_type"] = record["_ownership_type"] = row["Type"]
            record["_bom_sheet"], record["_bom_row"] = row["_sheet"], row["_row"]
            record["_owner"] = {name: row.get(name) for name in ("PF Code", "Project", "Supplier", "End Item")}
            same_key_details = detail_index.get(business_key(row), [])
            matches = [item for item in same_key_details
                       if not _detail_context_conflicts(row, item, detail_families[item["_record_id"]])]
            rejected_details = [{"detail_record": item["_record_id"],
                                 "fields": _detail_context_conflicts(row, item, detail_families[item["_record_id"]])}
                                for item in same_key_details
                                if _detail_context_conflicts(row, item, detail_families[item["_record_id"]])]
            if rejected_details:
                _issue(issues, "incompatible_detail_identity", row["_sheet"], row["_row"],
                       "Detail rows with a different named commodity/source family were not borrowed; valid BOM-only inputs remain available.",
                       rejected_details=rejected_details)
            # HARD detail Supplier owns manufacturing rates; BOM Supplier owns
            # the purchased/final component (PCM make_bns10952+,merge18815+).
            # Relax only this one dimension after proving a unique named detail
            # and unique BOM owner. Never borrow another Type/End Item/family.
            if not matches and row["Commodity"] == "HARD" and row["Type"] in {"MAKE", "BUY NON STD", "ASSY"}:
                # Primary make_new14602-14617 / assy14313-14386 calculate
                # Hard detail rows; master_output15451 then locates Customer
                # No in that output. A detail's descriptive End Item is not a
                # new BOM owner. Require exact customer/supplier/type/family
                # and a unique owner across End Items before rebinding it.
                key = business_key(row)
                candidates = [item for rows in detail_index.values() for item in rows
                              if detail_families[item["_record_id"]] == "HARD"
                              and business_key(item)[:3] == key[:3]
                              and not _detail_context_conflicts(row, item, "HARD")]
                owners = [owner for owner in bom_rows if owner["Commodity"] == "HARD"
                          and business_key(owner)[:3] == key[:3]]
                if key[0] and key[1] and len(candidates) == len(owners) == 1:
                    matches = candidates
                    _issue(issues, "hard_detail_end_item_differs", row["_sheet"], row["_row"],
                           "Unique HARD customer, supplier and Type identify the detail; BOM End Item remains final ownership.",
                           "info", detail_record=candidates[0]["_record_id"],
                           bom_end_item=row.get("End Item"), detail_end_item=candidates[0].get("End Item"),
                           source="PCM5 make_new14602-14617;assy14313-14386;master_output15451")
                elif candidates:
                    record["_hard_detail_owner_ambiguous"] = True
                    _issue(issues, "ambiguous_hard_detail_owner", row["_sheet"], row["_row"],
                           "HARD descriptive-label matching does not identify one detail and one BOM owner.",
                           "error", detail_records=[item["_record_id"] for item in candidates],
                           owner_records=[owner["_record_id"] for owner in owners])
            if not matches and not record.get("_hard_detail_owner_ambiguous") and row["Commodity"] == "HARD" and row["Type"] in {"MAKE", "BUY NON STD"}:
                key = business_key(row)
                candidates = [item for rows in detail_index.values() for item in rows
                              if detail_families[item["_record_id"]] == "HARD"
                              and (business_key(item)[0], business_key(item)[2], business_key(item)[3]) == (key[0], key[2], key[3])
                              and all(not row.get(field) or not item.get(field)
                                      or _key_value(row[field]) == _key_value(item[field])
                                      for field in ("PF Code", "Project"))]
                owners = [item for item in bom_rows if item["Commodity"] == "HARD"
                          and (business_key(item)[0], business_key(item)[2], business_key(item)[3]) == (key[0], key[2], key[3])]
                if (len(candidates) == 1 and len(owners) == 1
                        and _key_value(candidates[0].get("Supplier"))
                        and candidates[0].get("Commodity") in (None, "", "HARD")):
                    matches = candidates
                    _issue(issues, "separate_costing_supplier", row["_sheet"], row["_row"],
                           "Unique HARD detail supplies manufacturing rates; BOM supplier remains final purchasing owner.",
                           purchasing_supplier=row.get("Supplier"), costing_supplier=candidates[0].get("Supplier"),
                           detail_record=candidates[0]["_record_id"])
                elif candidates:
                    _issue(issues, "ambiguous_costing_supplier", row["_sheet"], row["_row"],
                           "HARD sourcing identity is ambiguous, has no costing supplier, or declares another commodity; no detail was selected.", "error",
                           detail_records=[item["_record_id"] for item in candidates], owner_count=len(owners))
            if not matches and row["Commodity"] == "SOFT":
                key = business_key(row)
                soft_candidates = [item for item in detail_owner_index.get((key[0], key[1], key[3]), [])
                                   if detail_families[item["_record_id"]] == "SOFT"
                                   and not _detail_context_conflicts(row, item, "SOFT")]
                if not soft_candidates and key[0] and key[1]:
                    # Original soft879-883 groups by Supplier and the final
                    # master_output15451 locates Customer No in that family
                    # result. A missing detail End Item is not a new owner.
                    # Bind it only when that named customer/supplier identifies
                    # one detail and one BOM owner across Types/End Items.
                    blank_candidates = [item for rows in detail_index.values() for item in rows
                                        if detail_families[item["_record_id"]] == "SOFT"
                                        and business_key(item)[:2] == key[:2]
                                        and not business_key(item)[3]
                                        and not _detail_context_conflicts(row, item, "SOFT")]
                    possible_owners = [owner for owner in bom_rows if business_key(owner)[:2] == key[:2]]
                    if len(blank_candidates) == 1 and len(possible_owners) == 1:
                        soft_candidates = blank_candidates
                        _issue(issues, "soft_detail_end_item_omitted", row["_sheet"], row["_row"],
                               "Unique SOFT customer and supplier identify a detail with no End Item; BOM remains final ownership.",
                               detail_record=blank_candidates[0]["_record_id"], bom_end_item=row.get("End Item"),
                               source="PCM_5/Sheet_Metal_Forging_with summary_chk_Chnages.py:879-883;15451")
                    elif blank_candidates:
                        _issue(issues, "ambiguous_soft_detail_owner", row["_sheet"], row["_row"],
                               "SOFT detail omits End Item but customer/supplier does not identify one detail and one BOM owner.",
                               "error", detail_records=[item["_record_id"] for item in blank_candidates],
                               owner_records=[owner["_record_id"] for owner in possible_owners])
                if len(soft_candidates) == 1:
                    matches = soft_candidates
                    record["Type"] = matches[0]["Type"]
                    _issue(issues, "detail_type_differs_from_bom", row["_sheet"], row["_row"],
                           "Original SOFT part1 uses detail Type for calculation; BOM Type remains output ownership.",
                           bom_type=row["Type"], calculation_type=record["Type"],
                           source="PCM_5/Sheet_Metal_Forging_with summary_chk_Chnages.py:1614-1644")
                elif len(soft_candidates) > 1:
                    _issue(issues, "ambiguous_soft_detail_owner", row["_sheet"], row["_row"],
                           "Different SOFT Types share this owner; no arbitrary detail was selected.", "error")
            if row['Type']=='SPECIAL PROCESS' and row['Commodity']=='SOFT' and matches:
                matches=matches[:1]  # original first Customer No anchor; Sr group retains all rows below
            record["_details"] = [dict(item) for item in matches]
            if counts[business_key(row)] > 1 and business_key(row) not in purchasing_occurrences:
                _issue(issues, "duplicate_bom_business_key", row["_sheet"], row["_row"],
                       "Duplicate BOM identity is retained as separate records.", "error",
                       business_key=list(business_key(row)))
            if counts[business_key(row)] > 1 and business_key(row) in purchasing_occurrences:
                record["_purchasing_occurrence"] = True
                _issue(issues, "repeated_purchasing_occurrence", row["_sheet"], row["_row"],
                       "Repeated purchasing line is retained with its own quantity and variants; each physical occurrence contributes once.",
                       severity="info", occurrence_record=row["_record_id"], business_key=list(business_key(row)))
            if len(matches) == 1:
                detail = matches[0]
                used_details.add(detail["_record_id"])
                if (detail.get('_preheader_period') is not None and row['Commodity']=='HARD'
                        and 'RM Base' not in row.get('_input_headers',[]) and 'RM Base' not in detail.get('_input_headers',[])):
                    record['RM Base']=detail['_preheader_period']
                disagreements = {}
                for name, value in detail.items():
                    if name.startswith("_"):
                        continue
                    if name in owner_fields:
                        if value not in (None, "") and row.get(name) not in (None, "") and _key_value(value) != _key_value(row[name]):
                            disagreements[name] = {"bom": row[name], "detail": value}
                        continue
                    # A detected detail header with an empty cell is a known
                    # Excel blank, distinct from an unknown formula operand.
                    if value is not None or name not in record:
                        record[name] = value
                record["_detail_sheet"], record["_detail_row"] = detail["_sheet"], detail["_row"]
                for internal_name in ("_process_lines", "_hardening_lines", "_input_headers"):
                    if internal_name in detail:
                        record[internal_name] = detail[internal_name]
                if "_process_lines" in detail:
                    record["Process Lines"] = detail["_process_lines"]
                record["_costing_supplier"] = detail.get("Supplier") or row.get("Supplier")
                # Detail/header/formula errors belong to this calculation owner.
                header_row = next(info["header_row"] for _, info in parsed_sheets if info["sheet"] == detail["_sheet"])
                if any(item.get("severity") == "error" and item.get("sheet") == detail["_sheet"]
                       and item.get("row") in (None, header_row, detail["_row"]) for item in list(issues)):
                    _issue(issues, "detail_validation_failed", row["_sheet"], row["_row"],
                           "A matched detail/header validation error blocks its owning BOM record.", "error",
                           detail_record=detail["_record_id"])
                if disagreements:
                    _issue(issues, "bom_detail_disagreement", row["_sheet"], row["_row"],
                           "BOM ownership/quantity remains authoritative; conflicting detail values are retained for review.",
                           fields=disagreements)
            elif len(matches) > 1:
                for item in matches:
                    used_details.add(item["_record_id"])
                _issue(issues, "ambiguous_detail_match", row["_sheet"], row["_row"],
                       "Several detail rows match this BOM identity; no arbitrary row was selected.",
                       "error", detail_records=[item["_record_id"] for item in matches])
            elif row["Commodity"] and row["Type"] != "END ITEM" and row["Commodity"]!="PCBA":
                commodity, type_ = row["Commodity"], row["Type"]
                # Primary make_new14602-14617 and make_bns_prev11041-11169
                # explicitly project BOM inputs when HARD detail is absent.
                # assy14313-14386 uses that same Hard-Type fallback; SP uses BOM.
                # generate_input641-743 generates STAMPING/FORGING/THERMAL
                # sheets from BOM; those normalized fields need no fake join.
                direct_bom = (commodity == "HARD" and
                              (type_ == "SPECIAL PROCESS" or
                               type_ in {"MAKE", "BUY NON STD", "ASSY"} and not hard_detail_usable))
                projected_bom = (commodity in {"STAMPING", "FORGING", "THERMAL"}
                                 and commodity not in detail_families_present)
                if direct_bom or projected_bom:
                    record["_calculation_input_source"] = "BOM"
                    _issue(issues, "bom_calculation_input", row["_sheet"], row["_row"],
                           "Original calculation uses this BOM record directly; no separate commodity detail row is required.",
                           "info", commodity=commodity, type=type_,
                           source="PCM5 make_new14602-14617;make_bns_prev11041-11169;assy14313-14386;generate_input641-743")
                else:
                    supported = commodity in {"SOFT", "STAMPING", "FORGING", "THERMAL", "GDC", "PDC", "SAND CASTING"} or commodity == "HARD" and type_ in {"MAKE", "BUY NON STD", "ASSY"}
                    _issue(issues, "missing_detail_match", row["_sheet"], row["_row"],
                           "No commodity detail row matches the complete business key.",
                           "error" if supported else "warning", commodity=commodity, type=type_,
                           detail_family_present=commodity in detail_families_present,
                           business_key=list(business_key(row)))
            # Both identities survive in final records and output/provenance.
            # These assignments override any uploaded similarly named columns.
            record["_purchasing_supplier"] = row.get("Supplier")
            record.setdefault("_costing_supplier", row.get("Supplier"))
            record["Purchasing Supplier"] = record["_purchasing_supplier"]
            record["Costing Supplier"] = record["_costing_supplier"]
            if row["Commodity"]=="PCBA":
                components = pcba_index.get(_key_value(row.get("Customer No")), [])
                record["_pcba_components"]=[]
                quantity_headers=[]
                if not components:
                    _issue(issues,"missing_pcba_components",row["_sheet"],row["_row"],
                           "No PCBA component rows match this board's Customer No.","error")
                # pcba_op13946-13959 selects BOM owners by PCBA Part Number
                # against Customer No;14203-14225 aggregates by that board ID.
                # Component End Item is a descriptive detail label and need not
                # equal the BOM ownership label. Relax this dimension only for
                # one board owner, with the exact nonblank supplier and compatible
                # project identifiers. A repeated board cannot borrow/duplicate
                # another owner's components.
                owners = pcba_owners[_key_value(row.get("Customer No"))]
                if len(owners) != 1:
                    _issue(issues,"ambiguous_pcba_board_owner",row["_sheet"],row["_row"],
                           "PCBA Part Number identifies several BOM owners; component ownership is ambiguous.",
                           "error",owner_records=[owner["_record_id"] for owner in owners])
                eligible_components = components if len(owners) == 1 else []
                record["_pcba_detail_end_items"] = []
                for component in eligible_components:
                    conflicts = []
                    if (not _key_value(row.get("Supplier")) or not _key_value(component.get("Supplier"))
                            or _key_value(component.get("Supplier")) != _key_value(row.get("Supplier"))):
                        conflicts.append("Supplier")
                    conflicts.extend(name for name in ("PF Code","Project")
                                     if row.get(name) not in (None, "") and component.get(name) not in (None, "")
                                     and _key_value(component[name]) != _key_value(row[name]))
                    if component.get("Commodity") not in (None, "", "PCBA"):
                        conflicts.append("Commodity")
                    if conflicts:
                        _issue(issues,"pcba_component_owner_conflict",row["_sheet"],row["_row"],
                               "PCBA board identifier matches but supplier/project ownership differs; no cross-owner aggregation.",
                               "error",fields=conflicts,component_row=component["_row"])
                        continue
                    used_pcba.add(component["_record_id"])
                    retained = dict(component)
                    retained["_pcba_owner_record_id"] = row["_record_id"]
                    retained["_pcba_owner_end_item"] = row.get("End Item")
                    retained["_pcba_detail_end_item"] = component.get("End Item")
                    record["_pcba_components"].append(retained)
                    detail_end_item = component.get("End Item")
                    if detail_end_item not in record["_pcba_detail_end_items"]:
                        record["_pcba_detail_end_items"].append(detail_end_item)
                    for header in component["_pcba_quantity_headers"]:
                        if header not in quantity_headers:quantity_headers.append(header)
                    if any(i.get("severity")=="error" and i.get("sheet")==component["_sheet"]
                           and i.get("row") in (component["_row"], next(info["header_row"] for rows,info in parsed_sheets if info["sheet"]==component["_sheet"]))
                           for i in issues):
                        _issue(issues,"pcba_component_validation_failed",row["_sheet"],row["_row"],
                               "A component input validation error affects this board.","error",component_row=component["_row"])
                if any(_key_value(value) != _key_value(row.get("End Item"))
                       for value in record["_pcba_detail_end_items"]):
                    _issue(issues,"pcba_detail_end_item_differs",row["_sheet"],row["_row"],
                           "Unique board ID and exact supplier identify the components; BOM End Item remains final ownership.", "info",
                           bom_end_item=row.get("End Item"),detail_end_items=record["_pcba_detail_end_items"],
                           source="PCM_5/Sheet_Metal_Forging_with summary_chk_Chnages.py:13946-13959;14203-14225")
                record["_pcba_quantity_headers"]=quantity_headers
                if eligible_components and not quantity_headers:
                    _issue(issues,"missing_pcba_quantities",row["_sheet"],row["_row"],
                           "PCBA component input has no named quantity fields after RM Supplier.","error")
            if row['Type']=='SPECIAL PROCESS':
                if row['Commodity']=='SOFT':
                    record['_soft_components']=[dict(item) for item in soft_rows if _key_value(item.get('Supplier'))==_key_value(row.get('Supplier'))]
                    anchors=[item for item in record['_soft_components'] if _key_value(item.get('Customer No'))==_key_value(row.get('Customer No'))]
                    if anchors:
                        group=[item for item in record['_soft_components'] if _key_value(item.get('Sr'))==_key_value(anchors[0].get('Sr'))]
                        used_details.update(item['_record_id'] for item in group)
                        for item in group:
                            if any(i.get('severity')=='error' and i.get('sheet')==item['_sheet'] and i.get('row') in (None,item['_row'],next(info['header_row'] for _,info in parsed_sheets if info['sheet']==item['_sheet'])) for i in issues):
                                _issue(issues,'soft_component_validation_failed',row['_sheet'],row['_row'],'SOFT special-process component input is invalid.','error')
                anchors=[item for item in paint_rows if item['_record_id']==item['_paint_anchor_id'] and _key_value(item.get('Customer No'))==_key_value(row.get('Customer No')) and _key_value(item.get('End Item'))==_key_value(row.get('End Item'))]
                if len(anchors)==1:
                    record['_paint_components']=[dict(item) for item in paint_rows if item['_paint_anchor_id']==anchors[0]['_record_id']]
                    component_rows = {item['_row'] for item in record['_paint_components']}
                    paint_header = next(info['header_row'] for _, info in parsed_sheets if info['sheet']==anchors[0]['_sheet'])
                    if any(i.get('severity')=='error' and i.get('sheet')==anchors[0]['_sheet']
                           and i.get('row') in component_rows | {None, paint_header} for i in issues):
                        _issue(issues,'paint_component_validation_failed',row['_sheet'],row['_row'],'Paint component input is invalid.','error')
                elif len(anchors)>1:
                    _issue(issues,'paint_anchor_ambiguous',row['_sheet'],row['_row'],'Several Paint groups match this Customer No and End Item.','error')
            records.append(record)
        for component in pcba_rows:
            if component["_record_id"] not in used_pcba:
                _issue(issues,"unmatched_pcba_component",component["_sheet"],component["_row"],
                       "PCBA component has no matching board and ownership in the BOM.","error")
        for rows in detail_index.values():
            for row in rows:
                if row["_record_id"] not in used_details:
                    _issue(issues, "unmatched_detail_row", row["_sheet"], row["_row"],
                           "Detail input has no matching BOM owner.", "error",
                           business_key=list(business_key(row)))
        variant_names = list(dict.fromkeys(name for row in records for name in row["_variants"]))
        sbc_layout = _sbc_layout(values_book, variant_names, issues)
        input_copies = _input_sheet_copies(data, values_book, formulas_book, sheet_metadata, issues)
        return ParsedWorkbook(records, issues, {
            "filename": filename, "sha256": sha256(data).hexdigest(), "sheets": sheet_metadata,
            "record_count": len(records), "detail_record_count": sum(len(v) for v in detail_index.values())+len(pcba_rows),
            "branch_counts": [{"commodity": key[0], "type": key[1], "count": count}
                              for key, count in sorted(Counter((r["Commodity"], r["Type"]) for r in records).items())],
            "variant_headers": variant_names,
            "sbc_layout": sbc_layout,
            "input_sheet_copies": input_copies,
            'unassigned_details': [dict(row) for rows,info in parsed_sheets for row in rows
                if (info.get('kind')=='detail' and info.get('family') and row['_record_id'] not in used_details)
                or (info.get('kind')=='pcba_components' and row['_record_id'] not in used_pcba)],
            "type_authority": "BOM owns output; a unique SOFT detail owns calculation Type (original part1:1614-1644).",
        })
    finally:
        values_book.close()
        formulas_book.close()

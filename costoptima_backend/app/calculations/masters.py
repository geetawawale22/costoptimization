"""Semantic, read-only access to the existing Step 6 relational masters.

Business code supplies verified headers or real semantic SQL column names. The
metadata owns physical storage translation; coordinate-shaped names are rejected
at the public boundary. No Excel file, private master copy, DDL or writes occur.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Any, Mapping
import re
import unicodedata

from sqlalchemy import MetaData, Table, select
from app.masters.metadata import masters, sources, columns, families


class MasterLookupError(ValueError):
    """A required master value cannot safely be resolved."""
    code = "master_lookup_error"

    def __init__(self, message, **details):
        super().__init__(message)
        self.details = details

    def as_dict(self):
        return {"code": self.code, "message": str(self), **self.details}


class MasterMappingError(MasterLookupError):
    code = "master_mapping_blocked"


class MissingMasterError(MasterLookupError):
    code = "missing_master"


class DuplicateMasterError(MasterLookupError):
    code = "duplicate_master"


class InvalidMasterValue(MasterLookupError):
    code = "invalid_master_value"


def _label(value: Any) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value)).split()).casefold()


def _identifier(name: str) -> str:
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", name or ""):
        raise MasterMappingError("Invalid configured master SQL identifier")
    return name


def _semantic(name: str) -> str:
    value = str(name).strip()
    # Exact header labels ICC, OH, REJ etc. are genuine business semantics. A
    # coordinate has either an explicit physical col_ prefix or a row suffix.
    # Bare column letters cannot be distinguished from acronyms, so an exact
    # verified header match is mandatory below rather than a positional lookup.
    # ConN are original named condition fields, despite resembling cell addresses;
    # they must still resolve against the actual verified master header metadata.
    if (re.fullmatch(r"col_[a-z]+|[A-Za-z]{1,3}[1-9][0-9]*", value, re.I)
            and not re.fullmatch(r"Con[1-9][0-9]*", value, re.I)):
        raise MasterMappingError("Excel coordinates are not semantic master fields")
    if not value:
        raise MasterMappingError("An explicit semantic master field is required")
    return _label(value)


@dataclass(frozen=True)
class FieldRef:
    """Disambiguates repeated semantic labels such as Rate/UOM by period/group."""
    name: str
    group: str | None = None


@dataclass(frozen=True)
class MasterField:
    column: str
    header: str | None
    group: str | None
    sql_type: str
    verified_raw: bool = False


@dataclass(frozen=True)
class MasterRecord:
    master_code: str
    source_table: str
    source_family: str
    source_row_id: int
    values: Mapping[str, Any]
    fields: tuple[MasterField, ...] = field(repr=False)

    def value(self, ref: str | FieldRef) -> Any:
        return self.values.get(resolve_field(self.fields, ref).column)

    def decimal(self, ref: str | FieldRef) -> Decimal:
        return as_decimal(self.value(ref), field_name=str(ref))


@lru_cache(maxsize=4096)
def resolve_field(fields: tuple[MasterField, ...], ref: str | FieldRef) -> MasterField:
    """Resolve immutable actual field metadata; the bounded cache holds no rates.

    Its complete field tuple and period/ref are the key, so changed metadata or
    another source cannot borrow a previous mapping. No record value is cached
    here. This avoids rescanning hundreds of fields for every eBom component.
    """
    request = ref if isinstance(ref, FieldRef) else FieldRef(str(ref))
    name = _semantic(request.name)
    group = _label(request.group) if request.group is not None else None
    candidates = []
    for item in fields:
        # Raw grids require their own verified semantic adapter (Assy below).
        if re.fullmatch(r"col_[a-z]+", item.column, re.I) and not item.verified_raw:
            continue
        aliases = {_label(item.header or "")} if item.verified_raw else {_label(item.column), _label(item.header or "")}
        if name in aliases:
            if group is None or _label(item.group or "") == group:
                candidates.append(item)
    if not candidates:
        raise MasterMappingError(f"No verified field mapping for {request.name!r}" +
                                 (f" in group {request.group!r}" if group is not None else ""))
    if len(candidates) != 1:
        raise MasterMappingError(f"Ambiguous master field {request.name!r}; specify its configured group or named SQL field")
    return candidates[0]


def as_decimal(value: Any, *, field_name: str = "master value") -> Decimal:
    """Never infer a text identifier's type; convert only an explicitly requested rate."""
    if value is None or isinstance(value, bool) or str(value).strip() == "":
        raise InvalidMasterValue(f"{field_name} is not an available numeric master value")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value).strip())
    except (InvalidOperation, ValueError, TypeError):
        raise InvalidMasterValue(f"{field_name} is not a numeric master value") from None
    if not result.is_finite():
        raise InvalidMasterValue(f"{field_name} must be finite")
    return result


def _equal(actual: Any, expected: Any, case_sensitive: bool) -> bool:
    # Do not coerce 00123 to 123; leading zeros are part of business identifiers.
    if actual is None or expected is None:
        return actual is expected
    a, b = str(actual).strip(), str(expected).strip()
    return a == b if case_sensitive else a.casefold() == b.casefold()


class SemanticMasterResolver:
    """Uses the request's existing SQLAlchemy session and Step 6 definitions.

    A resolver is scoped to one calculation execution. Its read cache gives the
    execution consistent values already read without mutating any master. Source
    order then source row ID implement legacy first-match precedence. Callers
    needing a transaction-wide snapshot must set isolation on their own session;
    this class never changes the application's transaction configuration.
    """
    def __init__(self, db):
        self.db = db
        self.trace = []
        self._masters: dict[str, Mapping[str, Any]] = {}
        self._source_cache: dict[str, tuple[dict[str, Any], ...]] = {}
        self._rows: dict[str, tuple[Mapping[str, Any], ...]] = {}
        self._tables: dict[str, Table] = {}
        self._assy: dict[str, Any] | None = None
        self._semantic_rows: dict[str, tuple[MasterRecord, ...]] = {}
        self._record_cache: dict[tuple[str, str | None], tuple[MasterRecord, ...]] = {}
        self._match_columns: dict[tuple[int, tuple], tuple[str, ...] | None] = {}

    def definition(self, master_code: str) -> Mapping[str, Any]:
        if master_code not in self._masters:
            row = self.db.execute(select(masters).where(masters.c.master_code == master_code)).mappings().first()
            if row is None:
                raise MasterMappingError(f"Unknown configured master {master_code!r}")
            self._masters[master_code] = MappingProxyType(dict(row))
        return self._masters[master_code]

    def _sources(self, master_code: str, family: str | None = None):
        if master_code not in self._source_cache:
            definition = self.definition(master_code)
            stmt = (select(sources, families.c.folder_path)
                    .join(families, sources.c.family_id == families.c.family_id)
                    .where(sources.c.master_id == definition["master_id"])
                    .order_by(sources.c.merge_order, sources.c.source_id))
            result = []
            for row in self.db.execute(stmt).mappings():
                source = dict(row)
                fs = self.db.execute(select(columns).where(columns.c.source_id == source["source_id"])
                                     .order_by(columns.c.position)).mappings()
                source["fields"] = tuple(MasterField(f["column_key"], f["header_text"], f["group_text"], f["sql_type"])
                                         for f in fs if f["column_key"] != "row_id")
                result.append(source)
            self._source_cache[master_code] = tuple(result)
        chosen = tuple(s for s in self._source_cache[master_code]
                       if family is None or s["folder_path"] == family or s["data_table_name"] == family)
        if not chosen:
            raise MasterMappingError(f"No configured source for {master_code!r}" +
                                     (f" and source family {family!r}" if family else ""))
        return chosen

    def _read(self, source):
        name = _identifier(source["data_table_name"])
        if name not in self._rows:
            if name not in self._tables:
                try:
                    self._tables[name] = Table(name, MetaData(), autoload_with=self.db.connection())
                except Exception as exc:
                    # Do not disclose connection strings or driver messages.
                    raise MasterMappingError(f"Configured source {name!r} is unavailable") from None
            table = self._tables[name]
            expected = {f.column for f in source["fields"]} | {"row_id"}
            if not expected.issubset(set(table.c.keys())):
                raise MasterMappingError(f"Configured source fields differ from SQL table {name!r}")
            stmt = select(table).order_by(table.c.row_id)
            self._rows[name] = tuple(MappingProxyType(dict(r)) for r in self.db.execute(stmt).mappings())
        return self._rows[name]

    def fields(self, master_code: str, *, family: str | None = None) -> tuple[MasterField, ...]:
        items = []
        for source in self._sources(master_code, family):
            for item in source["fields"]:
                if item not in items:
                    items.append(item)
        return tuple(items)

    def _casting_recipe_records(self, source) -> tuple[MasterRecord, ...]:
        """Original sand_casting_receipe:6791-6816 two-level supplier/grade header.

        Physical columns are discovered from actual labels. Public callers use
        Addition in kg as the row key and FieldRef(material_grade,supplier).
        Neither source coordinates nor copied recipe values enter business code.
        """
        name=source["data_table_name"]
        if name in self._semantic_rows:return self._semantic_rows[name]
        rows=self._read(source)
        candidates=[(i,row) for i,row in enumerate(rows)
                    if {"addition in kg","material code"}.issubset(
                        {_label(v) for key,v in row.items() if key!="row_id" and v is not None})]
        if len(candidates)!=1 or candidates[0][0]==0:
            raise MasterMappingError("Verified supplier/grade recipe headers are missing or ambiguous")
        index,header=candidates[0];suppliers=rows[index-1]
        semantic_fields=[]
        for physical in source["fields"]:
            label=header.get(physical.column)
            if label is None or not str(label).strip():continue
            is_key=_label(label) in {"addition in kg","material code"}
            supplier=suppliers.get(physical.column)
            if not is_key and (supplier is None or not str(supplier).strip()):continue
            semantic_fields.append(MasterField(physical.column,str(label).strip(),
                                   None if is_key else str(supplier).strip(),physical.sql_type,True))
        fields_for_grid=tuple(semantic_fields)
        ingredient=resolve_field(fields_for_grid,"Addition in kg").column
        records=tuple(MasterRecord("casting_recipie_master",name,source["folder_path"],int(row["row_id"]),row,fields_for_grid)
                      for row in rows[index+1:] if row.get(ingredient) is not None and str(row.get(ingredient)).strip())
        self._semantic_rows[name]=records
        return records

    def _casting_complexity_records(self, source) -> tuple[MasterRecord, ...]:
        """Named pre/post-machining rejection matrix used at PCM7019/7078.

        This exposes only the original Rejection Category/Type/rate grid.
        Other side-by-side shape/core rating grids remain explicitly unmapped.
        """
        name=source["data_table_name"]
        if name in self._semantic_rows:return self._semantic_rows[name]
        rows=self._read(source)
        candidates=[(i,row) for i,row in enumerate(rows)
                    if {"rejection category","type","rejection %"}.issubset(
                       {_label(v) for key,v in row.items() if key!="row_id" and v is not None})]
        if len(candidates)!=1 or candidates[0][0]+1>=len(rows):
            raise MasterMappingError("Verified casting rejection matrix headers are missing or ambiguous")
        index,header=candidates[0];groups=rows[index+1]
        semantic_fields=[];in_rates=False;rate_labels=[]
        for physical in source["fields"]:
            label=header.get(physical.column)
            if label is not None and _label(label) in {"rejection category","type"}:
                semantic_fields.append(MasterField(physical.column,str(label).strip(),None,physical.sql_type,True))
            if label is not None and _label(label)=="rejection %":in_rates=True
            if in_rates:
                rate_label=str(groups.get(physical.column) or "").strip()
                if rate_label not in {"A","B","C"}:
                    in_rates=False
                else:
                    rate_labels.append(rate_label)
                    semantic_fields.append(MasterField(physical.column,rate_label,"Rejection %",physical.sql_type,True))
        if rate_labels!=["A","B","C"]:
            raise MasterMappingError("Casting rejection rate group must contain the verified A/B/C labels")
        fields_for_grid=tuple(semantic_fields)
        category_field=resolve_field(fields_for_grid,"Rejection Category").column
        type_field=resolve_field(fields_for_grid,"Type").column
        for label in ("A","B","C"):resolve_field(fields_for_grid,FieldRef(label,"Rejection %"))
        category=None;records=[]
        for raw in rows[index+2:]:
            if raw.get(category_field) is not None and str(raw.get(category_field)).strip():
                category=str(raw[category_field]).strip()
            if category is None or not _label(category).startswith("casting ("):continue
            if raw.get(type_field) is None or not str(raw.get(type_field)).strip():continue
            values=dict(raw);values[category_field]=category
            records.append(MasterRecord("casting_complexity_master",name,source["folder_path"],int(raw["row_id"]),
                                        MappingProxyType(values),fields_for_grid))
        if not records:raise MasterMappingError("Casting rejection matrix has no verified data rows")
        result=tuple(records);self._semantic_rows[name]=result
        return result

    def _raw_records(self, master_code: str, source) -> tuple[MasterRecord, ...]:
        """Translate verified raw header labels, never expose addresses as fields.

        These anchors are schema semantics observed in the original source and
        Step 6 raw tables, not copied master values. Header detection scans rows.
        A changed/missing/ambiguous header blocks the affected master explicitly.
        """
        if master_code=="casting_recipie_master":return self._casting_recipe_records(source)
        if master_code=="casting_complexity_master":return self._casting_complexity_records(source)
        anchors = {
            "net_consumption_base": {"Con1", "Con9", "Cal RM cons"},
            "blank_consumption_base": {"Con1", "Con23", "Blank Length", "Cal Blank Cons", "Cal Parts Per Blank"},
            "gross_consumption_base": {"Con1", "Con21", "L", "W", "Cal Gross Consumption", "Total Surface Area"},
            "consumable": {"Material Code", "Base Part", "Consumable", "Consumables cost calculations"},
            "seat_output_matrix": {"Base", "Type", "Selection Priority"},
            "special_norms": {"Supplier", "Hanger Cost", "ICC Cost", "PnF"},
        }
        if master_code not in anchors:
            raise MasterMappingError(f"Raw master {master_code!r} requires a verified semantic grid adapter")
        name = source["data_table_name"]
        if name in self._semantic_rows:
            return self._semantic_rows[name]
        rows = self._read(source)
        wanted = {_label(v) for v in anchors[master_code]}
        headers = [r for r in rows if wanted.issubset({_label(v) for k, v in r.items() if k != "row_id" and v is not None})]
        if len(headers) != 1:
            raise MasterMappingError(f"Verified semantic header is missing or ambiguous for {master_code!r}")
        header = headers[0]
        present = [(f, str(header[f.column]).strip()) for f in source["fields"]
                   if header.get(f.column) is not None and str(header[f.column]).strip()]
        terminals = {"Cal Gross Consumption", "Total Surface Area", "Cal parts / Sheet", "L", "W"}
        semantic_fields = []
        supplier_count = 0
        for i, (physical, label) in enumerate(present):
            group = None
            if master_code == "gross_consumption_base":
                group = next((text for _, text in present[i:] if text in terminals), None)
            if master_code == "special_norms":
                if _label(label) == "supplier":
                    supplier_count += 1
                group = "Rates" if supplier_count <= 1 else "Applicability"
            semantic_fields.append(MasterField(physical.column, label, group, physical.sql_type, True))
        fields_for_grid = tuple(semantic_fields)
        # Source row IDs only preserve order after the detected header. They are
        # provenance, not a fixed-row business lookup or a formula dependency.
        records = tuple(MasterRecord(master_code, name, source["folder_path"], int(row["row_id"]), row, fields_for_grid)
                        for row in rows if row["row_id"] > header["row_id"] and
                        any(row.get(f.column) is not None and str(row.get(f.column)).strip() for f in fields_for_grid))
        self._semantic_rows[name] = records
        return records

    def records(self, master_code: str, *, family: str | None = None) -> tuple[MasterRecord, ...]:
        # This cache lives only inside the current resolver/read-only transaction.
        # No mutable rate value is shared between executions or snapshots.
        key=(master_code,family)
        if key not in self._record_cache:
            if self.definition(master_code)["header_kind"] == "Single":
                result=tuple(record for source in self._sources(master_code, family)
                             for record in self._raw_records(master_code, source))
            else:
                result=tuple(MasterRecord(master_code,s["data_table_name"],s["folder_path"],int(row["row_id"]),row,s["fields"])
                             for s in self._sources(master_code,family) for row in self._read(s))
            self._record_cache[key]=result
        return self._record_cache[key]

    def matches(self, master_code: str, keys: Mapping[str | FieldRef, Any], *,
                family: str | None = None, case_sensitive: bool = False) -> tuple[MasterRecord, ...]:
        if not keys:
            raise MasterMappingError("Master lookup requires explicit business keys")
        for key in keys:
            _semantic(key.name if isinstance(key, FieldRef) else str(key))
        found, mapped = [], False
        refs=tuple(keys)
        expected=tuple(keys.values())
        for record in self.records(master_code, family=family):
            # resolve_field's shared immutable cache previously re-hashed a large
            # dataclass tuple for every row/key. Resolve once per actual source
            # schema and selector within this transaction, keeping all original
            # ambiguity validation and source precedence.
            selector=(id(record.fields),refs)
            if selector not in self._match_columns:
                try:self._match_columns[selector]=tuple(resolve_field(record.fields,ref).column for ref in refs)
                except MasterMappingError:self._match_columns[selector]=None
            matched_columns=self._match_columns[selector]
            if matched_columns is None:continue
            mapped=True
            if all(_equal(record.values.get(column),value,case_sensitive) for column,value in zip(matched_columns,expected)):
                found.append(record)
        # Mapping must be validated even for an empty source table.
        if not mapped:
            for source in self._sources(master_code, family):
                try:
                    for ref in keys:
                        resolve_field(source["fields"], ref)
                    mapped = True
                    break
                except MasterMappingError:
                    continue
        if not mapped:
            raise MasterMappingError(f"No source in {master_code!r} has all requested semantic keys", master_failure="unmapped_key", master=master_code, lookup_keys={str(k):str(v) for k,v in keys.items()})
        return tuple(found)

    def record(self, master_code: str, keys: Mapping[str | FieldRef, Any], *,
               family: str | None = None, duplicate_policy: str = "first", missing: str = "error",
               case_sensitive: bool = False) -> MasterRecord | None:
        if duplicate_policy not in {"first", "last", "error"}:
            raise ValueError("duplicate_policy must be first, last or error")
        if missing not in {"error", "none"}:
            raise ValueError("missing must be error or none")
        found = self.matches(master_code, keys, family=family, case_sensitive=case_sensitive)
        if not found:
            if missing == "none":
                return None
            raise MissingMasterError(f"No matching {master_code!r} master record", master_failure="no_record", master=master_code, lookup_keys={(k.name if isinstance(k,FieldRef) else str(k)):str(v) if v is not None else None for k,v in keys.items()})
        if len(found) > 1 and duplicate_policy == "error":
            raise DuplicateMasterError(f"Multiple {master_code!r} master records match the business keys", master_failure="duplicate_record", master=master_code, matching_records=[{"source_table":r.source_table,"source_row_id":r.source_row_id} for r in found])
        return found[-1] if duplicate_policy == "last" else found[0]

    def lookup(self, master_code: str, keys: Mapping[str | FieldRef, Any], field: str | FieldRef, *,
               family: str | None = None, duplicate_policy: str = "first", missing: str = "error",
               default: Any = None, decimal: bool = False, case_sensitive: bool = False, blank_zero: bool = False) -> Any:
        if missing not in {"error", "none", "default"}:
            raise ValueError("missing must be error, none or default")
        # Resolve return metadata before a fallback, preventing a typo from being
        # hidden by a default. Ambiguous selectors are always blocked.
        candidates = []
        if self.definition(master_code)["header_kind"] == "Single":
            available_fields = [record.fields for record in self.records(master_code, family=family)]
        else:
            available_fields = [source["fields"] for source in self._sources(master_code, family)]
        for fields_for_source in available_fields:
            try:
                candidates.append(resolve_field(fields_for_source, field))
            except MasterMappingError:
                continue
        if not candidates:
            raise MasterMappingError(f"No verified return field {field!r} for {master_code!r}", master_failure="unmapped_field", master=master_code, master_field=field.name if isinstance(field,FieldRef) else field, master_group=field.group if isinstance(field,FieldRef) else None)
        record = self.record(master_code, keys, family=family, duplicate_policy=duplicate_policy,
                             missing="error" if missing == "error" else "none", case_sensitive=case_sensitive)
        if record is None:
            return default if missing == "default" else None
        try:resolved = resolve_field(record.fields,field)
        except MasterMappingError as exc:
            raise MasterMappingError(str(exc), master_failure="unmapped_field", master=master_code,
                source_table=record.source_table, source_row_id=record.source_row_id,
                master_field=field.name if isinstance(field,FieldRef) else field,
                master_group=field.group if isinstance(field,FieldRef) else None) from exc
        value = record.values.get(resolved.column)
        self.trace.append({"master":master_code,"source_table":record.source_table,"source_family":record.source_family,
                           "source_row_id":record.source_row_id,"field":field.name if isinstance(field,FieldRef) else field,
                           "database_column":resolved.column,"header":resolved.header,"group":resolved.group,
                           "blank_numeric_zero":bool(blank_zero and decimal and (value is None or isinstance(value,str) and not value.strip()))})
        if value is None or (isinstance(value, str) and not value.strip()):
            if blank_zero and decimal:
                return Decimal(0)  # Existing selected blank Excel numeric cell; never a missing record.
            if missing == "error":
                raise MissingMasterError(f"Matched {master_code!r} record has no value for {field!r}", master_failure="blank_value", master=master_code, source_table=record.source_table, source_row_id=record.source_row_id, database_column=resolved.column, master_field=resolved.header, master_group=resolved.group)
            return default if missing == "default" else None
        if not decimal:return value
        try:return as_decimal(value, field_name=str(field))
        except InvalidMasterValue as exc:
            raise InvalidMasterValue(str(exc), master_failure="invalid_numeric_value", master=master_code,
                source_table=record.source_table, source_row_id=record.source_row_id,
                database_column=resolved.column, master_field=resolved.header, master_group=resolved.group) from exc

    def decimal(self, master_code: str, keys: Mapping[str | FieldRef, Any], field: str | FieldRef, **kwargs) -> Decimal:
        return self.lookup(master_code, keys, field, decimal=True, **kwargs)

    def material(self, material_code: str, field: str | FieldRef, **kwargs):
        return self.lookup("material_master", {"material_code": material_code}, field, **kwargs)

    def process(self, process: str, field: str | FieldRef, **kwargs):
        return self.lookup("process_master", {"process": process}, field, **kwargs)

    def supplier(self, master_code: str, supplier: str, field: str | FieldRef, **kwargs):
        return self.lookup(master_code, {"supplier": supplier}, field, **kwargs)

    def assy(self, supplier: str, operation: str, metric: str = "MHR", *,
             duplicate_policy: str = "first", decimal: bool = True):
        """Assy is the Step 6 verified three-block semantic adapter.

        Metric/operation labels resolve through the existing master repository's
        header mapping. Neither supplier nor rate lookup uses an Excel address.
        Legacy CT/Handling erroneous supplier references are not reproduced.
        Unknown/blank supplier fallback was unverified in Step 7 and is blocked.
        """
        if not str(supplier or "").strip():
            raise MissingMasterError("Assy requires an explicit supplier; blank-supplier fallback is unverified", master="assy", master_failure="missing_input", master_field=str(operation), lookup_keys={"supplier":supplier}, master_group=metric)
        if duplicate_policy not in {"first", "last", "error"}:
            raise ValueError("duplicate_policy must be first, last or error")
        if self._assy is None:
            from app.masters.repository import assy_lookup_mapping
            self._assy = assy_lookup_mapping(self.db, self.definition("assy"))
        metric = {"ct": "CT per Point", "handling": "Handling Time"}.get(_label(metric), metric)
        blocks = self._assy["blocks"]
        matches = [v for k, v in blocks.items() if _label(k) == _label(metric)]
        if len(matches) != 1:
            raise MasterMappingError(f"Unknown Assy metric {metric!r}", master="assy", master_failure="unmapped_field", master_group=metric, master_field=str(operation), lookup_keys={"supplier":supplier})
        block = matches[0]
        operation_columns = [v for k, v in block["operation_fields"].items() if _label(k) == _label(operation)]
        if len(operation_columns) != 1:
            raise MasterMappingError(f"Unknown or ambiguous Assy operation {operation!r}", master="assy", master_failure="unmapped_field", master_group=metric, master_field=str(operation), lookup_keys={"supplier":supplier}, source_table=self._assy["source_table"])
        sources_for_assy = [s for s in self._sources("assy") if s["data_table_name"] == self._assy["source_table"]]
        if len(sources_for_assy) != 1:
            raise MasterMappingError("Assy source definition is ambiguous")
        rows = [row for row in self._read(sources_for_assy[0])
                if _equal(row.get(block["supplier_field"]), supplier, False)]
        if not rows:
            raise MissingMasterError("Assy supplier has no matching verified operation rate", master="assy", master_failure="no_record", master_group=metric, master_field=str(operation), lookup_keys={"supplier":supplier}, source_table=self._assy["source_table"])
        if len(rows) > 1 and duplicate_policy == "error":
            raise DuplicateMasterError("Assy supplier has duplicate operation rates", master="assy", master_failure="duplicate_record", master_group=metric, master_field=str(operation), lookup_keys={"supplier":supplier}, source_table=self._assy["source_table"], matching_records=[{"source_row_id":row["row_id"]} for row in rows])
        chosen = rows[-1] if duplicate_policy == "last" else rows[0]
        result = chosen.get(operation_columns[0])
        self.trace.append({"master":"assy","source_table":self._assy["source_table"],
            "source_family":sources_for_assy[0]["folder_path"],"source_row_id":chosen.get("row_id"),
            "field":str(operation),"group":str(metric),"header":str(operation),
            "database_column":operation_columns[0],"supplier_field":block["supplier_field"]})
        if result is None or (isinstance(result, str) and not result.strip()):
            raise MissingMasterError("Assy operation rate is blank", master="assy", master_failure="blank_value", master_group=metric, master_field=str(operation), lookup_keys={"supplier":supplier}, source_table=self._assy["source_table"], source_row_id=chosen["row_id"], database_column=operation_columns[0])
        return as_decimal(result, field_name=f"Assy {metric}/{operation}") if decimal else result

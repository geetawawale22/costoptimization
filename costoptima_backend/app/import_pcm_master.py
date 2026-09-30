"""One-time migration of a published PCM master to database rows; never generates Excel."""
import argparse
import hashlib
import json
from pathlib import Path
from openpyxl import load_workbook
from openpyxl.utils import column_index_from_string


def extract_master(path, layout):
    wb = load_workbook(path, read_only=True, data_only=True)
    tables = {}
    try:
        normalized = None
        for spec in layout["tables"]:
            if spec.get("normalizer") == "consumption_rules":
                if normalized is None:
                    from app.pcm.master_data.normalize import normalize_master_workbook, NOT_YET_CONVERTED
                    normalized = normalize_master_workbook(path)
                    if NOT_YET_CONVERTED:
                        raise ValueError(f"Consumption master contains untranslatable rules: {NOT_YET_CONVERTED}")
                tables[spec["table"]] = normalized[spec["table"]]
                continue
            sheet = wb[spec["sheet"]]
            matrix = list(sheet.values)
            def cell(r, c):
                c = column_index_from_string(c) if isinstance(c, str) else c
                return matrix[r - 1][c - 1] if r <= len(matrix) and c <= len(matrix[r - 1]) else None
            columns = spec.get("columns")
            if not columns:
                columns = {}
                for c, value in enumerate(matrix[spec["header_row"] - 1], 1):
                    if value is None or str(value).startswith("Unnamed"):
                        continue
                    label = str(value).strip()
                    if spec.get("group_row") and label in spec.get("merge_columns", []):
                        group = cell(spec["group_row"], c)
                        label += "_" + str(group).split(".")[0].strip()
                    if label in columns:
                        label += f"__column_{c}"
                    columns[label] = c
            rows = []
            for r in range(spec["data_start"], min(spec.get("data_end", len(matrix)), len(matrix)) + 1):
                data = {name: cell(r, c) for name, c in columns.items()}
                if not all(data.get(key) not in (None, "") for key in spec.get("required_keys", [])):
                    continue
                if not any(value is not None for value in data.values()):
                    continue
                for name in spec.get("blank_as_zero", []):
                    if data.get(name) is None:
                        data[name] = 0
                data.update({"_source_sheet": spec["sheet"], "_source_row": r})
                if spec.get("unpivot"):
                    for pivot in spec["unpivot"]:
                        value = cell(r, pivot["column"])
                        if value is not None:
                            rows.append({**data, **pivot["fields"], spec["value_field"]: value})
                else:
                    rows.append(data)
            tables[spec["table"]] = rows
        for spec in layout.get("cell_tables", []):
            values = {key: wb[spec["sheet"]][address].value for key, address in spec["cells"].items()}
            tables.setdefault(spec["table"], []).append({**spec.get("keys", {}), **values})
    finally:
        wb.close()
    return tables


def install_master(db, tables, code="pcm_published", replace=False):
    from app.models.workbook import WorkbookType, WorkbookTab, WorkbookRow, WorkbookTabColumn, WorkbookImportVersion
    book = db.query(WorkbookType).filter(WorkbookType.code == code).first()
    if book and not replace:
        raise ValueError(f"{code} already exists; use --replace to publish a new version")
    if not book:
        book = WorkbookType(code=code, name="Published PCM Master", description="Database source imported from published legacy master")
        db.add(book)
        db.flush()
    if book.is_deleted:
        raise ValueError("Cannot replace a deleted master workbook")
    previous = [version.version for version in book.versions]
    version = max(previous, default=0) + 1
    for tab in book.tabs:
        for row in tab.rows:
            row.is_deleted = True
    for name, records in tables.items():
        tab = next((t for t in book.tabs if t.tab_name == name and not t.is_deleted), None)
        if not tab:
            tab = WorkbookTab(workbook_type=book, tab_name=name)
            db.add(tab)
            db.flush()
        names = {c.column_name for c in tab.columns}
        headers = list(dict.fromkeys(k for row in records for k in row if not k.startswith("_")))
        for position, header in enumerate(headers):
            if header not in names:
                db.add(WorkbookTabColumn(tab_id=tab.id, column_name=header, position=position))
        db.add_all([WorkbookRow(tab_id=tab.id, row_data=row, version=version) for row in records])
    db.add(WorkbookImportVersion(workbook_type_id=book.id, version=version, snapshot=tables))
    db.flush()
    return version


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workbook", type=Path)
    parser.add_argument("--layout", type=Path, default=Path(__file__).parents[1] / "data/pcm_master_import.json")
    parser.add_argument("--output", type=Path, help="Optional reviewable JSON; no database connection")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--replace", action="store_true")
    parser.add_argument("--with-definition", action="store_true", help="Install the compatible database calculation definition in the same transaction")
    parser.add_argument("--definition", type=Path, default=Path(__file__).parents[1] / "data/pcm_reference_definition.json")
    args = parser.parse_args()
    layout = json.loads(args.layout.read_text(encoding="utf-8"))
    tables = extract_master(args.workbook, layout)
    report = {"source_sha256": hashlib.sha256(args.workbook.read_bytes()).hexdigest(), "tables": tables}
    if args.output:
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    if args.apply:
        from app.database import SessionLocal
        from app.models.app_config import AppConfig
        from app.pcm.engine import validate_definition
        with SessionLocal() as db:
            if args.with_definition:
                definition = json.loads(args.definition.read_text(encoding="utf-8"))
                validate_definition(definition)
                config = db.query(AppConfig).filter(AppConfig.group == "PCM_ENGINE", AppConfig.key == "definition").first()
                if config and not args.replace:
                    raise ValueError("PCM_ENGINE.definition exists; --replace is required to publish the new definition")
                if not config:
                    config = AppConfig(group="PCM_ENGINE", key="definition", type="json")
                    db.add(config)
                config.value = json.dumps(definition)
                config.type = "json"
                config.is_active = True
                config.is_deleted = False
                config.is_secret = False
            version = install_master(db, tables, layout["workbook"], args.replace)
            db.commit()
            print(f"Published database master version {version}")
    print(json.dumps({table: len(rows) for table, rows in tables.items()}, indent=2))


if __name__ == "__main__":
    main()

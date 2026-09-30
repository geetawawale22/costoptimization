"""Apply legacy Master_Header/Master_Split semantics to normalized database rows.

Headers are normalized on import. Position remains block metadata, not an Excel
file offset. Rows are concatenated in source order; duplicates are never erased.
"""
from sqlalchemy.orm import selectinload
from app.models.workbook import WorkbookType, WorkbookTab


def materialize_views(db, specs):
    codes = {s["workbook"] for spec in specs for s in spec["sources"]}
    books = {b.code: b for b in db.query(WorkbookType).options(
        selectinload(WorkbookType.tabs).selectinload(WorkbookTab.rows)
    ).filter(WorkbookType.code.in_(codes), WorkbookType.is_deleted.is_(False)).all()}
    output = []
    for spec in specs:
        mode = str(spec["header_mode"])
        if mode not in {"0", "1", "Single", "0,1"}:
            raise ValueError(f"Unsupported master header mode: {mode}")
        sources = spec["sources"][:1] if mode == "Single" else spec["sources"]
        for source in sources:
            book = books.get(source["workbook"])
            if not book:
                continue  # Referencing this unavailable table fails in the interpreter.
            tabs = [t for t in book.tabs if not t.is_deleted and t.tab_name == source["table"]]
            if len(tabs) > 1:
                raise ValueError(f"Ambiguous master source {source}")
            for tab in tabs:
                seen = set()
                for row in sorted(tab.rows, key=lambda r: r.id):
                    if row.is_deleted or ("section_id" in source and row.section_id != source["section_id"]):
                        continue
                    data = dict(row.row_data or {})
                    key = tuple(str(data.get(k, "")).strip().casefold() for k in spec.get("unique", []))
                    duplicate = mode != "Single" and bool(key) and key in seen
                    seen.add(key)
                    output.append({"workbook": spec.get("workbook", "pcm_merged"), "table": spec["table"],
                                   "row_id": row.id, "version": row.version, "section_id": row.section_id,
                                   "source_workbook": book.code, "source_table": tab.tab_name,
                                   "position": spec.get("position", 0), "duplicate_in_source": duplicate,
                                   "data": data})
    return output

import io

from openpyxl import Workbook, load_workbook

from app.models.workbook import WorkbookRow, WorkbookTab, WorkbookTabColumn, WorkbookType
from app.routers.workbooks import _build_dynamic_records, _export_tab_to_workbook, _upsert_rows
from app.routers.workbooks import _build_header_mapping_preview, _dense_stacked_header


class _User:
    id = 1


def _xlsx(rows, merges=()):
    wb = Workbook()
    ws = wb.active
    ws.title = "T"
    for row in rows:
        ws.append(row)
    for rng in merges:
        ws.merge_cells(rng)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def test_dynamic_headers_percent_formula_and_unwanted_data():
    content = _xlsx([
        ["Rates", "Rates", "", "Extra"],
        ["Code", "Rate", "Pct", ""],
        ["A", "=1+2", "10%", "keep"],
    ], merges=("A1:B1",))
    tab = WorkbookTab(workbook_type=WorkbookType(code="x", name="x"), tab_name="T",
                      header_type="type0", header_rows=[0, 1], remarks_column_name="Remarks")
    tab.columns = [
        WorkbookTabColumn(column_name="Code", header_name="Code", position=0, column_position=0, is_unique_key=True),
        WorkbookTabColumn(column_name="Rate", header_name="Rate", position=1, column_position=1),
        WorkbookTabColumn(column_name="Pct", header_name="Pct", position=2, column_position=2, is_percentage=True),
        WorkbookTabColumn(column_name="Remarks", header_name="Remarks", position=3),
    ]
    df, meta = _build_dynamic_records(tab, None, content, "T")
    assert df.iloc[0]["Rate"] is None
    assert df.iloc[0]["_formula_data"]["Rate"]["formula"] == "=1+2"
    assert df.iloc[0]["Pct"] == 0.10
    assert df.iloc[0]["Remarks"] == {"Extra": "keep"}
    assert meta["formula_cells"]["Rate"][0]["formula"] == "=1+2"
    assert meta["header_rows"] == [0, 1]


def test_upsert_increments_version_only_when_data_changes(db_session):
    tab = WorkbookTab(workbook_type_id=1, tab_name="T")
    col = WorkbookTabColumn(tab=tab, column_name="Code", position=0, is_unique_key=True)
    db_session.add_all([tab, col])
    db_session.commit()
    inserted, updated, unchanged = _upsert_rows(db_session, tab, None, [{"Code": "A", "Rate": 10}], _User())
    db_session.commit()
    row = db_session.query(WorkbookRow).one()
    assert (inserted, updated, unchanged) == (1, 0, 0)
    assert row.version == 1
    _upsert_rows(db_session, tab, None, [{"Code": "A", "Rate": 20}], _User())
    db_session.commit()
    assert row.version == 2
    _upsert_rows(db_session, tab, None, [{"Code": "A", "Rate": 20}], _User())
    db_session.commit()
    assert row.version == 2


def test_empty_template_export_keeps_all_configured_headers():
    tab = WorkbookTab(workbook_type=WorkbookType(code="x", name="x"), tab_name="T",
                      header_type="type0", header_rows=[0, 1])
    tab.columns = [
        WorkbookTabColumn(column_name="A", header_name="A", header_group="G", position=0),
        WorkbookTabColumn(column_name="B", header_name="B", header_group="G", position=1),
    ]
    wb = Workbook()
    wb.remove(wb.active)
    _export_tab_to_workbook(wb, tab)
    out = io.BytesIO()
    wb.save(out)
    check = load_workbook(io.BytesIO(out.getvalue()), data_only=False)["T"]
    assert check["A1"].value == "G"
    assert check["A2"].value == "A"
    assert check["B2"].value == "B"
    assert str(list(check.merged_cells.ranges)[0]) == "A1:B1"


def test_casting_headers_keep_supplier_columns_and_skip_header_data():
    content = _xlsx([
        [None, None, "General Casting", "KFIL", "Seaco"],
        ["Addition in kg", None, "FG 220", "FG 220", "FG 220"],
        ["Pig Iron", None, 175, 200, 225],
    ])
    tab = WorkbookTab(tab_name="T", header_type="type0", header_row_index=1)
    tab.columns = [
        WorkbookTabColumn(column_name="Material", header_name="Addition in kg", position=0),
        *[WorkbookTabColumn(column_name=name, header_name=f"{name} FG 220", position=i + 1)
          for i, name in enumerate(["General Casting", "KFIL", "Seaco"])],
    ]
    df, meta = _build_dynamic_records(tab, None, content, "T")
    assert df.to_dict("records") == [{"Material": "Pig Iron", "General Casting": 175,
                                       "KFIL": 200, "Seaco": 225}]
    assert meta["header_rows"] == [0, 1]
    assert all(m["status"] == "matched" for m in
               _build_header_mapping_preview(tab, None, list(df.columns), canonical=True))


def test_missing_header_is_not_reported_as_a_matched_blank_column():
    tab = WorkbookTab(tab_name="T", header_type="type0", header_row_index=0)
    tab.columns = [WorkbookTabColumn(column_name="Missing", position=0)]
    df, _ = _build_dynamic_records(tab, None, _xlsx([["Actual"], [12]]), "T")
    assert "Missing" not in df.columns


def test_numeric_data_is_not_a_second_header():
    assert not _dense_stacked_header([["A", "B", "C"], [1, 1, 2]], 0)


def test_casting_column_detection_uses_unique_full_headers(admin_client):
    admin_client.post("/workbook-types", json={"code": "casting_detect", "name": "Casting"})
    admin_client.post("/workbook-types/casting_detect/tabs", json={"tab_name": "T", "columns": []})
    content = _xlsx([
        [None, None, "General Casting", "KFIL", "Seaco"],
        ["Addition in kg", None, "FG 220", "FG 220", "FG 220"],
        ["Pig Iron", None, 175, 200, 225],
    ])
    response = admin_client.post("/workbook-types/casting_detect/tabs/T/columns/detect",
                                 params={"header_row": 2}, files={"file": ("casting.xlsx", content)})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["auto_detected_two_row_header"] is True
    assert [c["column_name"] for c in result["columns"]] == [
        "Addition in kg", "General Casting FG 220", "KFIL FG 220", "Seaco FG 220"]
    assert [c["column_position"] for c in result["columns"]] == [0, 2, 3, 4]

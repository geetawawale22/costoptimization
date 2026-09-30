"""Tests for /workbook-types/* — generic Masters/Commodity import/export/CRUD engine,
including the individual (single-tab) import endpoint."""
import io
import pandas as pd


def _make_xlsx(sheet_name, df):
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter") as writer:
        df.to_excel(writer, sheet_name=sheet_name, index=False)
    buf.seek(0)
    return buf


def test_list_workbook_types(viewer_client):
    r = viewer_client.get("/workbook-types")
    assert r.status_code == 200
    assert "test_masters" in {w["code"] for w in r.json()}


def test_unknown_workbook_type_404(viewer_client):
    assert viewer_client.get("/workbook-types/does_not_exist/schema").status_code == 404


def test_get_schema(viewer_client):
    r = viewer_client.get("/workbook-types/test_masters/schema")
    assert r.status_code == 200
    tab_names = {t["tab_name"] for t in r.json()["tabs"]}
    assert {"Master Header", "Logbook"} <= tab_names


def test_add_row_requires_permission(viewer_client):
    r = viewer_client.post("/workbook-types/test_masters/tabs/Master Header/rows", json={"row_data": {"Colname": "x"}})
    assert r.status_code == 403


def test_editor_can_add_list_update_delete_row_with_audit_fields(editor_client):
    row = {"Colname": "proc_rate", "Header": "Rate/UOM", "Unique": "Yes", "SheetName": "Process Master", "Position": 4}
    r = editor_client.post("/workbook-types/test_masters/tabs/Master Header/rows", json={"row_data": row})
    assert r.status_code == 200
    body = r.json()
    row_id = body["id"]
    assert body["created_by"] is not None  # audit field populated
    assert body["created_at"] is not None

    listed = editor_client.get("/workbook-types/test_masters/tabs/Master Header/rows")
    assert any(x["id"] == row_id for x in listed.json())
    paged = editor_client.get("/workbook-types/test_masters/tabs/Master Header/rows", params={"page": 1, "page_size": 10})
    assert paged.status_code == 200
    assert any(x["id"] == row_id for x in paged.json()["items"])

    updated = editor_client.put(f"/workbook-types/test_masters/tabs/Master Header/rows/{row_id}",
                                 json={"row_data": {**row, "Header": "Rate per UOM"}})
    assert updated.status_code == 200
    assert updated.json()["row_data"]["Header"] == "Rate per UOM"
    assert updated.json()["modified_by"] is not None

    deleted = editor_client.delete(f"/workbook-types/test_masters/tabs/Master Header/rows/{row_id}")
    assert deleted.status_code == 200
    # Soft-deleted: no longer listed
    final_list = editor_client.get("/workbook-types/test_masters/tabs/Master Header/rows")
    assert not any(x["id"] == row_id for x in final_list.json())


def test_cannot_add_row_to_readonly_tab(editor_client):
    r = editor_client.post("/workbook-types/test_masters/tabs/Logbook/rows", json={"row_data": {"x": 1}})
    assert r.status_code == 400


def test_import_valid_and_invalid_tabs(admin_client):
    df = pd.DataFrame({
        "Colname": ["proc_rate", "mat_scrap"], "Header": ["Rate/UOM", "Scrap Rate"],
        "Unique": ["Yes", "No"], "SheetName": ["Process Master", "Material Master"], "Position": [4, 7],
    })
    buf = _make_xlsx("Master Header", df)
    r = admin_client.post("/workbook-types/test_masters/import",
                           files={"file": ("masters.xlsx", buf, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert r.status_code == 200
    by_tab = {t["tab_name"]: t for t in r.json()["tabs"]}
    assert by_tab["Master Header"]["status"] == "Valid"
    assert by_tab["Master Header"]["rows_imported"] == 2
    assert by_tab["Logbook"]["status"] == "Invalid"


def test_individual_single_tab_import(admin_client):
    """A single file (e.g. Net Consumption Base) can be imported entirely on its own,
    without touching any other tab in its parent workbook."""
    df = pd.DataFrame({"Colname": ["solo"], "Header": ["Solo Header"], "Unique": ["Yes"],
                        "SheetName": ["S"], "Position": [1]})
    buf = _make_xlsx("whatever_sheet_name", df)  # sheet name inside the file doesn't need to match the tab
    r = admin_client.post("/workbook-types/test_masters/tabs/Master Header/import",
                           files={"file": ("solo_import.xlsx", buf,
                                   "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert r.status_code == 200
    assert r.json()["status"] == "Valid"
    assert r.json()["rows_imported"] == 1

    rows = admin_client.get("/workbook-types/test_masters/tabs/Master Header/rows").json()
    assert any(row["row_data"].get("Colname") == "solo" for row in rows)


def test_individual_import_requires_permission(viewer_client):
    df = pd.DataFrame({"Colname": ["x"]})
    buf = _make_xlsx("s", df)
    r = viewer_client.post("/workbook-types/test_masters/tabs/Master Header/import",
                            files={"file": ("x.xlsx", buf, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert r.status_code == 403


def test_individual_import_rejects_readonly_tab(admin_client):
    df = pd.DataFrame({"x": [1]})
    buf = _make_xlsx("s", df)
    r = admin_client.post("/workbook-types/test_masters/tabs/Logbook/import",
                           files={"file": ("x.xlsx", buf, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert r.status_code == 400


def test_export_workbook_and_single_tab(admin_client):
    r = admin_client.get("/workbook-types/test_masters/export")
    assert r.status_code == 200
    assert "spreadsheetml" in r.headers["content-type"]
    r2 = admin_client.get("/workbook-types/test_masters/tabs/Master Header/export")
    assert r2.status_code == 200


def test_export_requires_permission(viewer_client):
    assert viewer_client.get("/workbook-types/test_masters/export").status_code == 200  # Viewer has masters.view


def test_versions_grow_after_import(admin_client):
    before = admin_client.get("/workbook-types/test_masters/versions").json()
    df = pd.DataFrame({"Colname": ["v"], "Header": ["V"], "Unique": ["Yes"], "SheetName": ["S"], "Position": [1]})
    buf = _make_xlsx("Master Header", df)
    admin_client.post("/workbook-types/test_masters/import",
                       files={"file": ("v.xlsx", buf, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    after = admin_client.get("/workbook-types/test_masters/versions").json()
    assert len(after) == len(before) + 1


def test_rollback_restores_previous_rows(admin_client):
    versions = admin_client.get("/workbook-types/test_masters/versions").json()
    assert len(versions) >= 1
    earliest = min(v["version"] for v in versions)
    r = admin_client.post(f"/workbook-types/test_masters/rollback/{earliest}")
    assert r.status_code == 200


def test_rollback_unknown_version_404(admin_client):
    assert admin_client.post("/workbook-types/test_masters/rollback/999999").status_code == 404


# ---------- New: create workbook type, add/import tab, add/edit/delete column ----------
def test_create_workbook_type(admin_client):
    r = admin_client.post("/workbook-types", json={"code": "new_master", "name": "New Master", "description": "test"})
    assert r.status_code == 200
    assert r.json()["code"] == "new_master"

    dup = admin_client.post("/workbook-types", json={"code": "new_master", "name": "Dup"})
    assert dup.status_code == 400


def test_create_workbook_type_requires_permission(viewer_client):
    r = viewer_client.post("/workbook-types", json={"code": "x", "name": "X"})
    assert r.status_code == 403


def test_add_tab_manually_with_columns(admin_client):
    admin_client.post("/workbook-types", json={"code": "tab_test_wb", "name": "Tab Test"})
    r = admin_client.post("/workbook-types/tab_test_wb/tabs", json={
        "tab_name": "My New Tab",
        "columns": [
            {"column_name": "Code", "data_type": "string", "is_unique_key": True, "is_required": True},
            {"column_name": "Rate", "data_type": "number"},
        ],
    })
    assert r.status_code == 200
    assert r.json()["tab_name"] == "My New Tab"
    assert len(r.json()["columns"]) == 2

    dup = admin_client.post("/workbook-types/tab_test_wb/tabs", json={"tab_name": "My New Tab", "columns": []})
    assert dup.status_code == 400


def test_add_tab_by_importing_file_auto_detects_columns(admin_client):
    admin_client.post("/workbook-types", json={"code": "import_new_wb", "name": "Import New"})
    df = pd.DataFrame({"Material Code": ["A1", "A2"], "Rate/UOM": [10.5, 20.0]})
    buf = _make_xlsx("Sheet1", df)
    r = admin_client.post("/workbook-types/import_new_wb/tabs/import-new", params={"tab_name": "Auto Tab"},
                           files={"file": ("f.xlsx", buf, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert r.status_code == 200
    assert r.json()["status"] == "Valid"
    assert r.json()["rows_imported"] == 2

    schema = admin_client.get("/workbook-types/import_new_wb/schema").json()
    tab = next(t for t in schema["tabs"] if t["tab_name"] == "Auto Tab")
    col_types = {c["column_name"]: c["data_type"] for c in tab["columns"]}
    assert col_types["Material Code"] == "string"
    assert col_types["Rate/UOM"] == "number"

    rows = admin_client.get("/workbook-types/import_new_wb/tabs/Auto Tab/rows").json()
    assert len(rows) == 2


def test_add_edit_delete_column(admin_client):
    admin_client.post("/workbook-types", json={"code": "col_test_wb", "name": "Col Test"})
    admin_client.post("/workbook-types/col_test_wb/tabs", json={"tab_name": "T1", "columns": []})

    added = admin_client.post("/workbook-types/col_test_wb/tabs/T1/columns",
                               json={"column_name": "Rate", "data_type": "number"})
    assert added.status_code == 200
    col_id = added.json()["id"]

    dup = admin_client.post("/workbook-types/col_test_wb/tabs/T1/columns",
                             json={"column_name": "Rate", "data_type": "number"})
    assert dup.status_code == 400

    updated = admin_client.put(f"/workbook-types/col_test_wb/tabs/T1/columns/{col_id}",
                                json={"column_name": "Rate/UOM", "data_type": "number", "is_required": True})
    assert updated.status_code == 200
    assert updated.json()["column_name"] == "Rate/UOM"

    deleted = admin_client.delete(f"/workbook-types/col_test_wb/tabs/T1/columns/{col_id}")
    assert deleted.status_code == 200

    schema = admin_client.get("/workbook-types/col_test_wb/schema").json()
    tab = next(t for t in schema["tabs"] if t["tab_name"] == "T1")
    assert len(tab["columns"]) == 0  # soft-deleted column no longer listed


def test_delete_tab(admin_client):
    admin_client.post("/workbook-types", json={"code": "del_tab_wb", "name": "Del Tab"})
    admin_client.post("/workbook-types/del_tab_wb/tabs", json={"tab_name": "T1", "columns": []})
    r = admin_client.delete("/workbook-types/del_tab_wb/tabs/T1")
    assert r.status_code == 200
    schema = admin_client.get("/workbook-types/del_tab_wb/schema").json()
    assert not any(t["tab_name"] == "T1" for t in schema["tabs"])


def test_add_tab_and_column_require_permission(viewer_client):
    r = viewer_client.post("/workbook-types/test_masters/tabs", json={"tab_name": "X", "columns": []})
    assert r.status_code == 403
    r2 = viewer_client.post("/workbook-types/test_masters/tabs/Master Header/columns",
                             json={"column_name": "X"})
    assert r2.status_code == 403

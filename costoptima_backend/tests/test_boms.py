"""Tests for /boms/* — upload, detection, preview, validation."""
import io
import pandas as pd
import json
from pathlib import Path


def test_pcm_validation_uses_runtime_configuration(engineer_client, monkeypatch):
    from app.routers import boms
    definition = json.loads((Path(__file__).parents[1] / "data/pcm_reference_definition.json").read_text(encoding="utf-8"))
    monkeypatch.setattr(boms, "load_definition", lambda db: definition)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter") as writer:
        for sheet in ["BOM", "GDC_Input", "SBC"]:
            pd.DataFrame({"Customer No": ["part"]}).to_excel(writer, sheet_name=sheet, index=False)
    buf.seek(0)
    upload = engineer_client.post("/boms/upload", files={"file": ("original.xlsx", buf)})
    assert upload.status_code == 200
    response = engineer_client.post(f"/boms/{upload.json()['id']}/validate")
    assert response.status_code == 200
    issues = {item["check"]: item for item in response.json()["issues"]}
    for check in ["PCM required input sheets", "Additional sheets", "Commodity <-> Sheet Match"]:
        assert issues[check]["status"] == "Passed", issues
    # Configuration changes must affect validation without commodity-specific code.
    definition["input"]["bom_sheet"] = "New Driver"
    response = engineer_client.post(f"/boms/{upload.json()['id']}/validate")
    issue = next(item for item in response.json()["issues"] if item["check"] == "PCM required input sheets")
    assert issue["status"] == "Error"
    assert "New Driver" in issue["message"]


def test_pcm_validation_reports_absent_configuration(engineer_client, monkeypatch):
    from app.routers import boms
    monkeypatch.setattr(boms, "load_definition", lambda db: None)
    upload = engineer_client.post("/boms/upload", files={"file": ("unconfigured.xlsx", _bom_file())})
    response = engineer_client.post(f"/boms/{upload.json()['id']}/validate")
    assert response.status_code == 200
    assert any(item["check"] == "PCM configuration" and item["status"] == "Error"
               for item in response.json()["issues"])


def _bom_file():
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter") as writer:
        pd.DataFrame({"Part": ["A", "B"], "Wt": [1.2, 3.4]}).to_excel(writer, sheet_name="Test Sheet Metal", index=False)
        pd.DataFrame({"Part": ["C"]}).to_excel(writer, sheet_name="Totally Unknown Sheet", index=False)
    buf.seek(0)
    return buf


def test_upload_requires_permission(editor_client):
    r = editor_client.post("/boms/upload", files={"file": ("b.xlsx", _bom_file(),
                            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert r.status_code == 403


def test_upload_bom_detects_known_and_unknown_sheets(engineer_client):
    r = engineer_client.post("/boms/upload", data={"project_name": "Test Project"},
                              files={"file": ("bom.xlsx", _bom_file(),
                                      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert r.status_code == 200
    bom_id = r.json()["id"]
    tabs = engineer_client.get(f"/boms/{bom_id}/tabs").json()
    by_sheet = {t["detected_sheet_name"]: t for t in tabs}
    assert by_sheet["Test Sheet Metal"]["commodity_code"] == "test_sheet_metal"
    assert by_sheet["Totally Unknown Sheet"]["commodity_code"] is None


def test_list_and_get_bom(engineer_client):
    upload = engineer_client.post("/boms/upload", files={"file": ("bom2.xlsx", _bom_file(),
                                   "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    bom_id = upload.json()["id"]
    assert any(b["id"] == bom_id for b in engineer_client.get("/boms").json())
    assert engineer_client.get(f"/boms/{bom_id}").status_code == 200


def test_get_nonexistent_bom_404(engineer_client):
    assert engineer_client.get("/boms/999999").status_code == 404


def test_preview_bom_lines(engineer_client):
    upload = engineer_client.post("/boms/upload", files={"file": ("bom3.xlsx", _bom_file(),
                                   "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    bom_id = upload.json()["id"]
    r = engineer_client.get(f"/boms/{bom_id}/preview")
    assert r.status_code == 200 and len(r.json()) >= 1


def test_validate_bom_flags_unmatched_sheet(engineer_client):
    upload = engineer_client.post("/boms/upload", files={"file": ("bom4.xlsx", _bom_file(),
                                   "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    bom_id = upload.json()["id"]
    r = engineer_client.post(f"/boms/{bom_id}/validate")
    assert r.status_code == 200
    issues = {i["check"]: i for i in r.json()["issues"]}
    assert issues["Commodity <-> Sheet Match"]["status"] == "Warning"


def test_delete_bom_soft_deletes(engineer_client):
    upload = engineer_client.post("/boms/upload", files={"file": ("bom5.xlsx", _bom_file(),
                                   "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    bom_id = upload.json()["id"]
    assert engineer_client.delete(f"/boms/{bom_id}").status_code == 200
    assert engineer_client.get(f"/boms/{bom_id}").status_code == 404

import copy
import io
import json
from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook

from app.database import SessionLocal
from app.models.app_config import AppConfig
from app.models.workbook import WorkbookType, WorkbookTab, WorkbookRow
from app.config import settings
from tests.test_pcm_database_runtime_unittest import DEFINITION, forging_masters, forging_row


@pytest.fixture
def pcm_definition():
    definition = copy.deepcopy(DEFINITION)
    definition["sources"]["forging"] = ["pcm_api_test"]
    with SessionLocal() as db:
        config = AppConfig(group="PCM_ENGINE", key="definition", type="json", value=json.dumps(definition))
        wb = WorkbookType(code="pcm_api_test", name="PCM API test masters")
        tabs = {}
        for source in forging_masters():
            if source["table"] not in tabs:
                tabs[source["table"]] = WorkbookTab(tab_name=source["table"], workbook_type=wb)
            tabs[source["table"]].rows.append(WorkbookRow(row_data=source["data"]))
        db.add_all([config, wb])
        db.commit()
        config_id, wb_id = config.id, wb.id
    try:
        yield definition
    finally:
        with SessionLocal() as db:
            db.delete(db.get(AppConfig, config_id))
            db.delete(db.get(WorkbookType, wb_id))
            db.commit()


def upload(client, row):
    wb = Workbook()
    ws = wb.active
    ws.title = "BOM"
    ws.append(list(row))
    ws.append(list(row.values()))
    stream = io.BytesIO()
    wb.save(stream)
    response = client.post("/boms/upload", files={"file": ("pcm.xlsx", stream.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert response.status_code == 200, response.text
    return response.json()["id"]


def test_enabled_pcm_route_snapshot_and_historical_export(engineer_client, pcm_definition):
    bom_id = upload(engineer_client, forging_row())
    response = engineer_client.post(f"/boms/{bom_id}/calculate")
    assert response.status_code == 200, response.text
    calc = response.json()
    assert calc["status"] == "Completed"
    assert calc["result"]["total_cost"] == pytest.approx(2587.52)
    snapshot = engineer_client.get(f"/calculations/{calc['id']}/pcm-snapshot")
    assert snapshot.status_code == 200
    assert snapshot.json()["results"][0]["values"]["RM Cost/Pc"] == 1148
    output = Path(settings.UPLOAD_DIR) / calc["result"]["output_file_path"]
    output.unlink()  # Force recovery of this test-generated artifact.
    with SessionLocal() as db:
        config = db.query(AppConfig).filter(AppConfig.group == "PCM_ENGINE").one()
        config.value = '{}'  # Historical export must not consult current configuration.
        db.commit()
    response = engineer_client.get(f"/calculations/{calc['id']}/export")
    assert response.status_code == 200
    wb = load_workbook(io.BytesIO(response.content), data_only=False)
    rows = list(wb["Master_Output"].values)
    values = dict(zip(rows[0], rows[1]))
    assert values["RM Cost/Pc"] == 1148
    assert all(cell.data_type != "f" for sheet in wb for row in sheet for cell in row)
    wb.close()


def test_missing_master_marks_run_and_commodity_for_review(engineer_client, pcm_definition):
    bom_id = upload(engineer_client, {**forging_row(), "Material Code": "MISSING"})
    response = engineer_client.post(f"/boms/{bom_id}/calculate")
    assert response.status_code == 200, response.text
    calc = response.json()
    assert calc["status"] == "Needs Review"
    assert calc["commodity_results"][0]["status"] == "Needs Review"
    assert calc["issues"][0]["code"] == "MISSING_MASTER"
    assert calc["issues"][0]["customer_no"] == "C"
    assert any(log["level"] == "Error" for log in calc["logs"])
    row = engineer_client.get(f"/calculations/{calc['id']}/pcm-snapshot").json()["results"][0]
    assert "expected one match" in row["error"]
    assert "Total" not in row["values"]


@pytest.mark.parametrize("profile_fields", [{}, {"profile": None}, {"profile": ""}])
def test_skipped_row_without_line_does_not_abort_output(engineer_client, pcm_definition, monkeypatch, profile_fields):
    from app.services.pcm_calculation import Interpreter
    original_run = Interpreter.run

    def with_skipped_row(self, rows):
        return original_run(self, rows) + [{
            **profile_fields,
            "input": {"Customer No": "OP 10", "Type": "Process"},
            "values": {}, "status": "Skipped",
            "issues": [{"code": "NOT_A_COSTED_ROW", "customer_no": "OP 10",
                        "message": "Operation requires parent contribution verification"}],
        }]

    monkeypatch.setattr(Interpreter, "run", with_skipped_row)
    bom_id = upload(engineer_client, forging_row())
    response = engineer_client.post(f"/boms/{bom_id}/calculate")
    assert response.status_code == 200, response.text
    calc = response.json()
    assert calc["status"] == "Needs Review"
    assert calc["result"]["output_file_path"]
    assert calc["result"]["total_cost"] == pytest.approx(2587.52)
    assert any("Line 2 / OP 10" in log["message"] for log in calc["logs"])
    groups = {group["commodity_code"]: group for group in calc["commodity_results"]}
    assert groups["unconfigured"]["records_found"] == 1
    assert groups["unconfigured"]["status"] == "Needs Review"
    assert all(groups)


def test_snapshot_requires_existing_calculation_and_permission(editor_client, engineer_client, pcm_definition):
    bom_id = upload(engineer_client, forging_row())
    calc = engineer_client.post(f"/boms/{bom_id}/calculate").json()
    # Existing Editor can view calculations, but cannot create estimates.
    assert editor_client.post(f"/boms/{bom_id}/calculate").status_code == 403
    assert engineer_client.get("/calculations/999999/pcm-snapshot").status_code == 404
    assert engineer_client.delete(f"/calculations/{calc['id']}").status_code == 200
    assert engineer_client.get(f"/calculations/{calc['id']}/pcm-snapshot").status_code == 404


def test_no_configuration_does_not_run_legacy_output_template(engineer_client):
    bom_id = upload(engineer_client, forging_row())
    response = engineer_client.post(f"/boms/{bom_id}/calculate")
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "CONFIG_NOT_AVAILABLE"

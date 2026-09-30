"""Tests for /commodity-types/* — commodity/field/formula config engine."""
from app.models.app_config import AppConfig


def test_list_commodities(viewer_client):
    r = viewer_client.get("/commodity-types")
    assert r.status_code == 200
    assert "test_sheet_metal" in {c["code"] for c in r.json()}


def test_create_commodity_requires_permission(engineer_client):
    r = engineer_client.post("/commodity-types", json={"code": "new_commodity", "name": "New Commodity"})
    assert r.status_code == 403


def test_admin_can_create_get_update_delete_commodity(admin_client):
    created = admin_client.post("/commodity-types", json={"code": "lifecycle_test", "name": "Lifecycle"})
    assert created.status_code == 200
    cid = created.json()["id"]

    assert admin_client.get(f"/commodity-types/{cid}").status_code == 200

    updated = admin_client.put(f"/commodity-types/{cid}",
                                json={"code": "lifecycle_test", "name": "Renamed", "is_active": False})
    assert updated.status_code == 200 and updated.json()["is_active"] is False

    assert admin_client.delete(f"/commodity-types/{cid}").status_code == 200
    assert admin_client.get(f"/commodity-types/{cid}").status_code == 404


def test_create_commodity_duplicate_code_rejected(admin_client):
    admin_client.post("/commodity-types", json={"code": "dup_code", "name": "First"})
    r = admin_client.post("/commodity-types", json={"code": "dup_code", "name": "Second"})
    assert r.status_code == 400


def test_field_crud(admin_client):
    commodities = admin_client.get("/commodity-types").json()
    cid = next(c["id"] for c in commodities if c["code"] == "test_sheet_metal")

    added = admin_client.post(f"/commodity-types/{cid}/fields",
                               json={"field_name": "Scrap Rate", "data_type": "number", "source_type": "master_lookup"})
    assert added.status_code == 200
    fid = added.json()["id"]

    updated = admin_client.put(f"/commodity-types/fields/{fid}",
                                json={"field_name": "Scrap Rate %", "data_type": "number", "source_type": "master_lookup"})
    assert updated.status_code == 200 and updated.json()["field_name"] == "Scrap Rate %"

    assert admin_client.delete(f"/commodity-types/fields/{fid}").status_code == 200


def test_formula_crud_and_test_endpoint(admin_client):
    commodities = admin_client.get("/commodity-types").json()
    cid = next(c["id"] for c in commodities if c["code"] == "test_sheet_metal")

    added = admin_client.post(f"/commodity-types/{cid}/formulas", json={"formula_expr": "rate * qty", "output_field": "Total Cost"})
    assert added.status_code == 200
    rid = added.json()["id"]

    updated = admin_client.put(f"/commodity-types/formulas/{rid}", json={"formula_expr": "rate * qty * 1.1", "output_field": "Total Cost"})
    assert updated.status_code == 200

    test_result = admin_client.post(f"/commodity-types/formulas/{rid}/test", json={"sample_row": {"rate": 10, "qty": 5}})
    assert test_result.status_code == 200
    assert abs(test_result.json()["result"] - 55.0) < 1e-9

    assert admin_client.delete(f"/commodity-types/formulas/{rid}").status_code == 200


def test_formula_test_sandbox_rejects_code_injection(admin_client):
    commodities = admin_client.get("/commodity-types").json()
    cid = next(c["id"] for c in commodities if c["code"] == "test_sheet_metal")
    added = admin_client.post(f"/commodity-types/{cid}/formulas",
                               json={"formula_expr": "__import__('os').system('echo pwned')", "output_field": "Malicious"})
    rid = added.json()["id"]
    r = admin_client.post(f"/commodity-types/formulas/{rid}/test", json={"sample_row": {}})
    assert r.status_code == 200
    assert r.json()["error"] is not None


def test_pcm_formula_repository_uses_runtime_definition(admin_client, db_session):
    seed = admin_client.post("/commodity-types/pcm-formulas/seed-default", params={"replace": True})
    assert seed.status_code == 200
    assert seed.json()["profiles"] >= 1
    assert seed.json()["formulas"] >= 80

    listed = admin_client.get("/commodity-types/pcm-formulas")
    assert listed.status_code == 200
    formulas = listed.json()
    target = next(item for item in formulas if item["profile"] == "hard" and item["step_name"] == "total")
    updated = admin_client.put(f"/commodity-types/pcm-formulas/{target['id']}",
                               json={"step_name": target["step_name"],
                                     "formula_expr": target["formula_expr"] + " + 0",
                                     "output_field": target["output_field"]})
    assert updated.status_code == 200
    assert updated.json()["formula_expr"].endswith("+ 0")
    db_session.query(AppConfig).filter(AppConfig.group == "PCM_ENGINE", AppConfig.key == "definition").delete()
    db_session.commit()

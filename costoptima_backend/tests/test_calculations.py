"""Tests for POST /boms/{id}/calculate and /calculations/* — the synchronous,
on-the-fly calculation engine (no job queue, no polling)."""
import io
import os
import pandas as pd
import pytest


def _upload_bom(client, sheet_name="Test Sheet Metal", extra_cols=None):
    buf = io.BytesIO()
    data = {"Part": ["A", "B"]}
    if extra_cols:
        data.update(extra_cols)
    with pd.ExcelWriter(buf, engine="xlsxwriter") as writer:
        pd.DataFrame(data).to_excel(writer, sheet_name=sheet_name, index=False)
    buf.seek(0)
    r = client.post("/boms/upload", files={"file": ("calc_bom.xlsx", buf,
                     "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert r.status_code == 200
    return r.json()["id"]


def test_calculate_requires_permission(editor_client, engineer_client):
    bom_id = _upload_bom(engineer_client)
    r = editor_client.post(f"/boms/{bom_id}/calculate")
    assert r.status_code == 403


def test_calculate_unknown_bom_404(engineer_client):
    assert engineer_client.post("/boms/999999/calculate").status_code == 404


def test_calculate_runs_synchronously_and_returns_complete_result(engineer_client):
    """The whole point of removing the job queue: the response IS the final result,
    with no separate status to poll for."""
    bom_id = _upload_bom(engineer_client)
    r = engineer_client.post(f"/boms/{bom_id}/calculate")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "Completed"
    assert body["completed_at"] is not None
    assert len(body["commodity_results"]) >= 1
    assert all(c["status"] == "Completed" for c in body["commodity_results"])
    assert body["result"]["email_sent"] == "Yes"
    assert body["result"]["total_cost"] >= 0


def test_calculate_is_self_sufficient_without_a_prior_preview_call(engineer_client):
    """Calling /calculate directly after /upload (skipping /preview entirely) must
    still produce real per-commodity results — it should not silently return zero."""
    bom_id = _upload_bom(engineer_client)
    r = engineer_client.post(f"/boms/{bom_id}/calculate")
    assert r.status_code == 200
    assert r.json()["commodity_results"][0]["records_found"] >= 1


def test_calculate_runs_the_real_documented_forging_formula():
    """Verifies the concrete worked-example formula from the reference document end
    to end: Yield%, Scrap Recovery, RM Cost/Pc, Basic Cost/Pc, Total Markup, Total."""
    from app.calc_engine import calculate_forging_line
    row = {"Type": "MAKE", "Material Code": "MC1", "Input Wt": 10.0, "Part Wt": 7.0,
           "Rate/UOM": 120, "Scrap Rate": 30, "ICC": 2, "REJ": 1, "OH": 3, "Profit": 5}
    result = calculate_forging_line(row, {})
    assert result["Yield (%)"] == 0.7
    assert result["Scrap Wt"] == 2.4
    assert result["Scrap Recovery"] == 72.0
    assert result["RM Cost/Pc"] == 1128.0  # (120*10) - 72
    assert result["Total Markup"] == 11.0  # 2+1+3+5
    assert result["Total"] == 1139.0


def test_s302_hard_sheet_uses_second_physical_row_as_header():
    from app.routers.boms import _read_bom_sheet

    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    path = os.path.join(root, "S302 BSO COVER_Sprint Input_Rev 2_09.09.2026 (1).xlsx")
    rows = _read_bom_sheet(path, "Hard")

    assert rows
    assert rows[0]["PF Code"] == "2302C"
    assert rows[0]["Project"] == "S302"
    assert rows[0]["Type"] == "MAKE"
    assert rows[0]["Material Code"] == "PPE291"


def test_hard_make_buy_std_and_assy_calculations_match_s302_reference_values():
    from app.calc_engine import run_synchronous_calculation
    from types import SimpleNamespace

    lookup = {
        "scoped": {
            "trim_master_user": {"Material Master": {"PPE291": {"Material Code": "PPE291", "Material Family": "PP", "Material Grade": "ABP 0730 MM P2B Y0031", "JAN#25": 154.87}}},
            "trim_pce": {"Std BOP": {"SF5306026": {"Customer No": "SF5306026", "Cost/Pc": 2.52}}},
            "trim_self": {
                "Assy": {"Abhijeet": {"Supplier": "Abhijeet", "Fix": 110, "CT per Point Fix": 6, "Handling Time Fix": 10}},
                "Norms_Make": {"Abhijeet": {"Supplier": "Abhijeet", "ICC": 0.01, "REJ": 0.015, "OH": 0.10, "PROFIT": 0.09, "EFFICIENCY": 0.9}},
                "Norms_Buy Std": {"Abhijeet": {"Supplier": "Abhijeet", "ICC": 0.01, "REJ": 0.015, "Handling": 0.05, "PROFIT": 0}},
                "Norms_Assy": {"Abhijeet": {"Supplier": "Abhijeet", "ICC": 0, "REJ": 0.015, "OH": 0.10, "PROFIT": 0.09}},
            },
        }
    }
    lines = [
        SimpleNamespace(commodity_code="hard", row_data={"Supplier": "Abhijeet", "Type": "MAKE", "Material Code": "PPE291", "Gross Wt": 0.1545, "RM Base": "JAN#25", "m/c Tonnage": 850, "Cycle Time": 70, "Cavity": 2}),
        SimpleNamespace(commodity_code="hard", row_data={"Supplier": "Abhijeet", "Type": "BUY STD", "Customer No": "SF5306026"}),
        SimpleNamespace(commodity_code="hard", row_data={"Supplier": "Abhijeet", "Type": "ASSY", "Operation Process": "Fix", "No of Points": 1}),
    ]

    result = run_synchronous_calculation(lines, master_lookup=lookup)
    make, buy, assy = result["hard"]["lines"]
    assert make["Total Cost/Pc"] == pytest.approx(41.1121039904)
    assert buy["Total/Pc"] == pytest.approx(2.709)
    assert assy["Total"] == pytest.approx(0.5891111111)


def test_list_and_get_calculation(engineer_client):
    bom_id = _upload_bom(engineer_client)
    calc = engineer_client.post(f"/boms/{bom_id}/calculate").json()
    listed = engineer_client.get("/calculations")
    assert listed.status_code == 200
    assert any(c["id"] == calc["id"] for c in listed.json())
    got = engineer_client.get(f"/calculations/{calc['id']}")
    assert got.status_code == 200


def test_get_nonexistent_calculation_404(engineer_client):
    assert engineer_client.get("/calculations/999999").status_code == 404


def test_export_calculation(engineer_client):
    bom_id = _upload_bom(engineer_client)
    calc = engineer_client.post(f"/boms/{bom_id}/calculate").json()
    r = engineer_client.get(f"/calculations/{calc['id']}/export")
    assert r.status_code == 200
    assert "spreadsheetml" in r.headers["content-type"]


def test_delete_calculation_requires_permission(editor_client, engineer_client):
    bom_id = _upload_bom(engineer_client)
    calc = engineer_client.post(f"/boms/{bom_id}/calculate").json()
    assert editor_client.post(f"/boms/{bom_id}/calculate").status_code == 403
    r = engineer_client.delete(f"/calculations/{calc['id']}")
    assert r.status_code == 200
    assert engineer_client.get(f"/calculations/{calc['id']}").status_code == 404

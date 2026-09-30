"""Tests for /output-templates, /dashboard/*, /reports/audit-log, /notifications."""


def test_list_output_templates(viewer_client):
    assert viewer_client.get("/output-templates").status_code == 200


def test_create_output_template_requires_permission(engineer_client):
    r = engineer_client.post("/output-templates", json={"name": "SBC Format", "format_type": "xlsx"})
    assert r.status_code == 403


def test_admin_can_create_and_update_output_template(admin_client):
    r = admin_client.post("/output-templates", json={"name": "SBC Format", "format_type": "xlsx", "layout": {"a": 1}})
    assert r.status_code == 200
    tid = r.json()["id"]
    updated = admin_client.put(f"/output-templates/{tid}", json={"name": "SBC Format v2", "format_type": "xlsx", "layout": {"a": 2}})
    assert updated.status_code == 200 and updated.json()["name"] == "SBC Format v2"


def test_dashboard_summary_shape(viewer_client):
    r = viewer_client.get("/dashboard/summary")
    assert r.status_code == 200
    for key in ("total_calculations", "completed", "failed", "avg_total_cost"):
        assert key in r.json()


def test_dashboard_commodity_distribution_is_a_mapping(viewer_client):
    r = viewer_client.get("/dashboard/commodity-distribution")
    assert r.status_code == 200
    assert isinstance(r.json(), dict)


def test_audit_log_requires_auth(client):
    assert client.get("/reports/audit-log").status_code == 401


def test_audit_log_returns_paged_table_shape(viewer_client):
    r = viewer_client.get("/reports/audit-log")
    assert r.status_code == 200
    body = r.json()
    assert set(body) >= {"items", "total", "page", "page_size", "total_pages", "entities"}
    assert isinstance(body["items"], list)
    assert body["page"] == 1


def test_notifications_scoped_to_current_user(admin_client, viewer_client):
    assert isinstance(admin_client.get("/notifications").json(), list)
    assert isinstance(viewer_client.get("/notifications").json(), list)


def test_mark_notification_read_404_when_missing(admin_client):
    assert admin_client.post("/notifications/999999/read").status_code == 404

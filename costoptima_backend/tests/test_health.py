"""Test for the root health-check endpoint."""


def test_root_health_check(client):
    r = client.get("/")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["service"] == "CostOptima API"

"""Tests for /auth/* — session-cookie login, logout, me."""


def test_login_success_sets_session_cookie(client):
    r = client.post("/auth/login", data={"username": "admin@costoptima.com", "password": "admin123"})
    assert r.status_code == 200
    assert r.json()["email"] == "admin@costoptima.com"
    assert "session_id" in r.cookies


def test_login_wrong_password(client):
    r = client.post("/auth/login", data={"username": "admin@costoptima.com", "password": "wrong"})
    assert r.status_code == 401


def test_login_unknown_user(client):
    r = client.post("/auth/login", data={"username": "nobody@costoptima.com", "password": "whatever"})
    assert r.status_code == 401


def test_me_requires_session_cookie(client):
    r = client.get("/auth/me")
    assert r.status_code == 401


def test_me_returns_current_user(admin_client):
    r = admin_client.get("/auth/me")
    assert r.status_code == 200
    assert r.json()["email"] == "admin@costoptima.com"
    assert r.json()["role"]["name"] == "Admin"


def test_me_rejects_garbage_cookie(client):
    client.cookies.set("session_id", "not-a-real-session-token")
    r = client.get("/auth/me")
    assert r.status_code == 401


def test_logout_clears_session(admin_client):
    assert admin_client.get("/auth/me").status_code == 200
    r = admin_client.post("/auth/logout")
    assert r.status_code == 200
    r2 = admin_client.get("/auth/me")
    assert r2.status_code == 401


def test_deactivated_user_cannot_login(admin_client):
    created = admin_client.post("/users", json={"name": "Temp", "email": "temp@costoptima.com", "password": "pw12345"})
    assert created.status_code == 200
    admin_client.delete(f"/users/{created.json()['id']}")
    r = admin_client.post("/auth/login", data={"username": "temp@costoptima.com", "password": "pw12345"})
    assert r.status_code == 403

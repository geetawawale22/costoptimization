"""Tests for /users, /roles, /roles/{id}/permissions, /permissions, /notification-rules."""
import uuid


def unique_email():
    return f"user_{uuid.uuid4().hex[:8]}@costoptima.com"


def test_list_users_requires_auth(client):
    assert client.get("/users").status_code == 401


def test_viewer_can_read_users_but_not_create(viewer_client):
    assert viewer_client.get("/users").status_code == 200
    r = viewer_client.post("/users", json={"name": "X", "email": unique_email(), "password": "pw12345"})
    assert r.status_code == 403


def test_admin_can_create_get_update_deactivate_user(admin_client):
    email = unique_email()
    created = admin_client.post("/users", json={"name": "New Person", "email": email, "password": "pw12345"})
    assert created.status_code == 200
    uid = created.json()["id"]

    got = admin_client.get(f"/users/{uid}")
    assert got.status_code == 200 and got.json()["email"] == email

    updated = admin_client.put(f"/users/{uid}", json={"job_title": "Cost Analyst"})
    assert updated.status_code == 200 and updated.json()["job_title"] == "Cost Analyst"

    deactivated = admin_client.delete(f"/users/{uid}", params={"reason": "Left the company"})
    assert deactivated.status_code == 200
    assert admin_client.get(f"/users/{uid}").json()["status"] == "Inactive"


def test_create_user_duplicate_email_rejected(admin_client):
    email = unique_email()
    r1 = admin_client.post("/users", json={"name": "First", "email": email, "password": "pw12345"})
    assert r1.status_code == 200
    r2 = admin_client.post("/users", json={"name": "Second", "email": email, "password": "pw67890"})
    assert r2.status_code == 400


def test_get_nonexistent_user_404(admin_client):
    assert admin_client.get("/users/999999").status_code == 404


def test_list_roles(viewer_client):
    r = viewer_client.get("/roles")
    assert r.status_code == 200
    names = {role["name"] for role in r.json()}
    assert {"Admin", "Editor", "Engineer", "Viewer"} <= names


def test_create_role_requires_permission(editor_client):
    assert editor_client.post("/roles", json={"name": "Temp Role"}).status_code == 403


def test_admin_can_create_and_update_role(admin_client):
    r = admin_client.post("/roles", json={"name": "Auditor"})
    assert r.status_code == 200
    role_id = r.json()["id"]
    r2 = admin_client.put(f"/roles/{role_id}", json={"name": "Auditor", "description": "Reviews things"})
    assert r2.status_code == 200
    assert r2.json()["description"] == "Reviews things"


def test_permissions_catalogue_lists_all_seeded_permissions(admin_client):
    r = admin_client.get("/permissions")
    assert r.status_code == 200
    resources = {p["resource"] for p in r.json()}
    assert "masters" in resources and "calculations" in resources


def test_create_update_delete_permission(admin_client):
    created = admin_client.post("/permissions", json={"resource": "reports", "action": "view", "description": "View reports"})
    assert created.status_code == 200
    pid = created.json()["id"]

    dup = admin_client.post("/permissions", json={"resource": "reports", "action": "view"})
    assert dup.status_code == 400

    updated = admin_client.put(f"/permissions/{pid}", json={"resource": "reports", "action": "view", "description": "Updated desc"})
    assert updated.status_code == 200
    assert updated.json()["description"] == "Updated desc"

    deleted = admin_client.delete(f"/permissions/{pid}")
    assert deleted.status_code == 200
    remaining = admin_client.get("/permissions").json()
    assert not any(p["id"] == pid for p in remaining)


def test_create_permission_requires_permission(viewer_client):
    r = viewer_client.post("/permissions", json={"resource": "x", "action": "view"})
    assert r.status_code == 403


def test_set_and_get_role_permissions(admin_client):
    role = admin_client.post("/roles", json={"name": "Custom Role"}).json()
    all_perms = admin_client.get("/permissions").json()
    masters_view = next(p["id"] for p in all_perms if p["resource"] == "masters" and p["action"] == "view")

    r = admin_client.put(f"/roles/{role['id']}/permissions", json={"permission_ids": [masters_view]})
    assert r.status_code == 200
    assert len(r.json()["permissions"]) == 1
    assert r.json()["permissions"][0]["resource"] == "masters"

    fetched = admin_client.get(f"/roles/{role['id']}/permissions")
    assert fetched.status_code == 200
    assert len(fetched.json()["permissions"]) == 1

    # Replacing again with an empty list should clear it
    r2 = admin_client.put(f"/roles/{role['id']}/permissions", json={"permission_ids": []})
    assert r2.status_code == 200
    assert r2.json()["permissions"] == []


def test_notification_rules_upsert(admin_client):
    payload = {"commodity": "Sheet Metal", "to_emails": ["a@x.com"], "cc_emails": ["b@x.com"]}
    r1 = admin_client.put("/notification-rules", json=payload)
    assert r1.status_code == 200
    payload["to_emails"] = ["c@x.com"]
    r2 = admin_client.put("/notification-rules", json=payload)
    assert r2.status_code == 200
    assert r2.json()["to_emails"] == ["c@x.com"]
    all_rules = admin_client.get("/notification-rules").json()
    assert len([x for x in all_rules if x["commodity"] == "Sheet Metal"]) == 1
"""Authentication, CSRF, roles, headers, lockout."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.models import Role
from tests.conftest import PASSWORD, Staff, create_user


def test_login_sets_secure_httponly_cookie():
    create_user("alice", Role.admin)
    c = TestClient(app, base_url="https://testserver")
    r = c.post("/api/auth/login", json={"username": "Alice", "password": PASSWORD})
    assert r.status_code == 200
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "secure" in cookie and "samesite=strict" in cookie
    assert "csrf_token" in r.json()


def test_wrong_password_and_unknown_user_same_message():
    create_user("bob", Role.admin)
    c = TestClient(app, base_url="https://testserver")
    a = c.post("/api/auth/login", json={"username": "bob", "password": "nope"})
    b = c.post("/api/auth/login", json={"username": "nobody", "password": "nope"})
    assert a.status_code == b.status_code == 401
    assert a.json() == b.json()


def test_lockout_after_repeated_failures():
    create_user("carol", Role.admin)
    c = TestClient(app, base_url="https://testserver")
    for _ in range(5):
        assert c.post("/api/auth/login", json={"username": "carol", "password": "bad"}).status_code == 401
    r = c.post("/api/auth/login", json={"username": "carol", "password": PASSWORD})
    assert r.status_code == 429


def test_csrf_required_for_state_changes(admin):
    r = admin.client.post("/api/admin/expressions", json={"expression_code": "GON-1", "english": "x"})
    assert r.status_code == 403
    r = admin.client.post("/api/admin/expressions", json={"expression_code": "GON-1", "english": "x"},
                          headers={"X-CSRF-Token": "forged"})
    assert r.status_code == 403
    assert admin.post("/api/admin/expressions", json={"expression_code": "GON-1", "english": "x"}).status_code == 201


def test_role_permissions(admin, contributor, reviewer):
    # Only admins manage users
    body = {"username": "newuser", "role": "contributor", "password": "Another-Pass-77"}
    assert contributor.post("/api/admin/users", json=body).status_code == 403
    assert reviewer.post("/api/admin/users", json=body).status_code == 403
    assert admin.post("/api/admin/users", json=body).status_code == 201
    # Reviewers cannot create content or import
    assert reviewer.post("/api/admin/expressions", json={"expression_code": "R-1", "english": "x"}).status_code == 403
    assert reviewer.get("/api/admin/imports").status_code == 403
    # Only admins see the audit log and backups
    assert contributor.get("/api/admin/audit").status_code == 403
    assert reviewer.post("/api/admin/backups").status_code == 403


def test_weak_password_rejected(admin):
    r = admin.post("/api/admin/users", json={"username": "weak", "role": "reviewer", "password": "password"})
    assert r.status_code == 422


def test_disabling_user_ends_their_sessions(admin):
    victim = Staff("dave", Role.contributor)
    assert victim.get("/api/auth/me").status_code == 200
    users = admin.get("/api/admin/users").json()
    uid = next(u["id"] for u in users if u["username"] == "dave")
    assert admin.patch(f"/api/admin/users/{uid}", json={"active": False}).status_code == 200
    assert victim.get("/api/auth/me").status_code == 401


def test_last_admin_cannot_be_demoted(admin):
    me = admin.get("/api/auth/me").json()
    r = admin.patch(f"/api/admin/users/{me['id']}", json={"role": "reviewer"})
    assert r.status_code == 409


def test_logout_invalidates_session(admin):
    assert admin.post("/api/auth/logout").status_code == 204
    assert admin.get("/api/auth/me").status_code == 401


def test_security_headers(public):
    r = public.get("/")
    assert r.status_code == 200
    csp = r.headers["content-security-policy"]
    assert "default-src 'self'" in csp and "script-src 'self'" in csp
    assert r.headers["x-content-type-options"] == "nosniff"
    a = public.get("/admin")
    assert "frame-ancestors 'none'" in a.headers["content-security-policy"]
    assert a.headers["x-frame-options"] == "DENY"


def test_storage_not_publicly_served(public):
    for path in ("/storage/gondi_vaani.db", "/storage/audio/x.mp3", "/audio/x.mp3", "/api/public/audio/abc"):
        assert public.get(path).status_code in (404, 422)


def test_api_docs_disabled_outside_development(public):
    assert public.get("/api/docs").status_code == 404

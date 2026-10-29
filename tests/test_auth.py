"""Sign-in, sessions, role-based access and user administration (service and HTTP level)."""

from __future__ import annotations

import http.client
import json
import threading
from http.server import ThreadingHTTPServer

import pytest

from backend import dashboard_server
from backend.auth import passwords, service
from backend.database import auth_repository, init_db
from backend.database.db import transaction

PASSWORD = "Plantation2026"


@pytest.fixture
def auth_db(db):
    init_db.apply_schema()  # the db fixture truncates every table, including the seeded roles
    dashboard_server._failed_logins.clear()
    return db


def make_user(email, role, *, password=PASSWORD, actor=None):
    return service.create_user(actor, {"email": email, "fullName": email.split("@")[0].replace(".", " ").title(),
                                       "role": role, "password": password})


# --- passwords --------------------------------------------------------------

def test_password_hash_round_trip_and_salting():
    first, second = passwords.hash_password(PASSWORD), passwords.hash_password(PASSWORD)
    assert first != second and first.startswith("scrypt$")
    assert passwords.verify_password(PASSWORD, first)
    assert not passwords.verify_password("Plantation2027", first)
    assert not passwords.verify_password(PASSWORD, "not-a-hash")


@pytest.mark.parametrize("candidate, reason", [
    ("short1", "at least"), ("onlyletterspassword", "letters and numbers"),
    ("123456789012345", "letters and numbers"), ("Siti.Rahma2026x", "email name"),
])
def test_password_policy_rejects(candidate, reason):
    assert reason in passwords.policy_problem(candidate, email="siti.rahma@example.com")


# --- sign-in and lockout ----------------------------------------------------

def test_login_success_creates_session_and_audit(auth_db):
    make_user("siti.rahma@example.com", "plantation")
    token, user = service.login("  Siti.Rahma@EXAMPLE.com ", PASSWORD, ip_address="10.0.0.5", user_agent="pytest")
    assert service.user_for_token(token)["email"] == "siti.rahma@example.com"
    assert set(user["sections"]) == {"all_regions", "inhouse"}
    with transaction() as cursor:
        cursor.execute("SELECT count(*) FROM app_session WHERE token_hash = %s", (service.token_hash(token),))
        assert cursor.fetchone()[0] == 1
        cursor.execute("SELECT count(*) FROM app_session WHERE token_hash = %s", (token,))
        assert cursor.fetchone()[0] == 0  # the raw token is never stored
    assert auth_repository.recent_audit()[0]["action"] == "login_succeeded"


def test_wrong_password_and_unknown_user_give_the_same_message(auth_db):
    make_user("siti.rahma@example.com", "regional")
    with pytest.raises(service.AuthError) as wrong:
        service.login("siti.rahma@example.com", "Wrong-password-1", ip_address=None, user_agent=None)
    with pytest.raises(service.AuthError) as unknown:
        service.login("nobody@example.com", "Wrong-password-1", ip_address=None, user_agent=None)
    assert str(wrong.value) == str(unknown.value) == "Incorrect email or password."


def test_account_locks_after_repeated_failures(auth_db, monkeypatch):
    monkeypatch.setattr(service.config, "LOGIN_MAX_FAILED_ATTEMPTS", 3)
    make_user("siti.rahma@example.com", "regional")
    for _ in range(3):
        with pytest.raises(service.AuthError):
            service.login("siti.rahma@example.com", "Wrong-password-1", ip_address=None, user_agent=None)
    with pytest.raises(service.AuthError, match="Too many failed attempts"):
        service.login("siti.rahma@example.com", PASSWORD, ip_address=None, user_agent=None)  # correct password, still locked
    user_id = auth_repository.get_login("SITI.RAHMA@example.com")[0]["user_id"]
    admin = make_user("admin.one@example.com", "admin")
    service.update_user(admin, user_id, {"unlock": True})
    assert service.login("siti.rahma@example.com", PASSWORD, ip_address=None, user_agent=None)[0]


def test_disabled_user_cannot_sign_in_and_loses_sessions(auth_db):
    admin = make_user("admin.one@example.com", "admin")
    user = make_user("siti.rahma@example.com", "regional")
    token, _ = service.login("siti.rahma@example.com", PASSWORD, ip_address=None, user_agent=None)
    service.update_user(admin, user["user_id"], {"isActive": False})
    assert service.user_for_token(token) is None
    with pytest.raises(service.AuthError, match="disabled"):
        service.login("siti.rahma@example.com", PASSWORD, ip_address=None, user_agent=None)


def test_expired_session_is_rejected(auth_db):
    make_user("siti.rahma@example.com", "regional")
    token, _ = service.login("siti.rahma@example.com", PASSWORD, ip_address=None, user_agent=None)
    with transaction() as cursor:
        cursor.execute("UPDATE app_session SET expires_at = now() - interval '1 minute'")
    assert service.user_for_token(token) is None


# --- user administration rules ----------------------------------------------

def test_only_admins_can_manage_users(auth_db):
    plantation = make_user("siti.rahma@example.com", "plantation")
    with pytest.raises(service.AccessDenied):
        service.create_user(plantation, {"email": "new.user@example.com", "fullName": "New User", "role": "regional",
                                         "password": PASSWORD})
    with pytest.raises(service.AccessDenied):
        service.reset_password(plantation, plantation["user_id"], "Another-pass-2026")


def test_create_user_validation(auth_db):
    admin = make_user("admin.one@example.com", "admin")
    base = {"email": "new.user@example.com", "fullName": "New User", "role": "regional", "password": PASSWORD}
    for change, message in [({"email": "not-an-email"}, "valid email"), ({"email": ""}, "valid email"),
                            ({"role": "superuser"}, "valid role"), ({"fullName": "X"}, "Full name"),
                            ({"password": "short"}, "at least")]:
        with pytest.raises(service.AuthError, match=message):
            service.create_user(admin, {**base, **change})
    created = service.create_user(admin, {**base, "email": " New.User@Example.COM "})
    assert created["email"] == "new.user@example.com"  # stored normalised
    with pytest.raises(service.AuthError, match="already exists"):
        service.create_user(admin, {**base, "email": "NEW.USER@example.com"})


def test_admin_cannot_lock_themselves_out_and_last_admin_is_kept(auth_db):
    admin = make_user("admin.one@example.com", "admin")
    with pytest.raises(service.AuthError, match="your own"):
        service.update_user(admin, admin["user_id"], {"isActive": False})
    with pytest.raises(service.AuthError, match="your own"):
        service.update_user(admin, admin["user_id"], {"role": "regional"})
    second = make_user("admin.two@example.com", "admin")
    service.update_user(admin, second["user_id"], {"role": "regional"})  # allowed: admin.one remains
    assert auth_repository.count_active_admins() == 1


def test_password_reset_ends_sessions(auth_db):
    admin = make_user("admin.one@example.com", "admin")
    user = make_user("siti.rahma@example.com", "regional")
    token, _ = service.login("siti.rahma@example.com", PASSWORD, ip_address=None, user_agent=None)
    service.reset_password(admin, user["user_id"], "Fresh-password-2026")
    assert service.user_for_token(token) is None
    assert service.login("siti.rahma@example.com", "Fresh-password-2026", ip_address=None, user_agent=None)[0]


def test_change_own_password_keeps_current_session(auth_db):
    make_user("siti.rahma@example.com", "regional")
    first, user = service.login("siti.rahma@example.com", PASSWORD, ip_address=None, user_agent=None)
    other, _ = service.login("siti.rahma@example.com", PASSWORD, ip_address=None, user_agent=None)
    with pytest.raises(service.AuthError, match="current password"):
        service.change_own_password(user, first, "wrong", "Changed-password-2026", ip_address=None)
    service.change_own_password(user, first, PASSWORD, "Changed-password-2026", ip_address=None)
    assert service.user_for_token(first) and service.user_for_token(other) is None


# --- HTTP access control ----------------------------------------------------

@pytest.fixture
def server(auth_db):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), dashboard_server.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()


class Client:
    def __init__(self, port):
        self.port, self.cookie = port, None

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        all_headers = {"X-Requested-With": "fetch", "Content-Type": "application/json", **(headers or {})}
        if self.cookie:
            all_headers["Cookie"] = self.cookie
        connection.request(method, path, body=None if body is None else json.dumps(body), headers=all_headers)
        response = connection.getresponse()
        data = response.read()
        set_cookie = response.getheader("Set-Cookie")
        if set_cookie:
            self.cookie = set_cookie.split(";")[0]
        connection.close()
        return response.status, response, data

    def login(self, email, password=PASSWORD):
        return self.request("POST", "/api/login", {"email": email, "password": password})


def test_anonymous_is_sent_to_sign_in(server):
    anonymous = Client(server)
    status, response, _ = anonymous.request("GET", "/")
    assert status == 302 and response.getheader("Location") == "/login.html"
    status, response, _ = anonymous.request("GET", "/weekly-seasonality.html")
    assert status == 302 and "next=%2Fweekly-seasonality.html" in response.getheader("Location")
    assert anonymous.request("GET", "/api/data")[0] == 401
    assert anonymous.request("GET", "/inhouse/data/plantations.json")[0] == 401
    assert anonymous.request("GET", "/login.html")[0] == 200
    assert anonymous.request("GET", "/css/login.css")[0] == 200
    assert anonymous.request("GET", "/css/dashboard.css")[0] == 302
    assert anonymous.request("GET", "/api/health")[0] == 200


def test_session_cookie_flags(server):
    make_user("siti.rahma@example.com", "regional")
    client = Client(server)
    status, response, _ = client.login("siti.rahma@example.com")
    assert status == 200
    cookie = response.getheader("Set-Cookie")
    for flag in ("HttpOnly", "SameSite=Strict", "Path=/", "Max-Age="):
        assert flag in cookie


def test_regional_viewer_sees_only_all_regions(server):
    make_user("viewer.one@example.com", "regional")
    client = Client(server)
    assert client.login("viewer.one@example.com")[0] == 200
    assert client.request("GET", "/weekly-seasonality.html")[0] == 200
    status, response, _ = client.request("GET", "/inhouse/")
    assert status == 302 and response.getheader("Location").startswith("/")
    assert client.request("GET", "/inhouse/data/plantations.json")[0] == 403
    assert client.request("GET", "/admin/users.html")[0] == 302
    assert client.request("GET", "/api/admin/users")[0] == 403
    me = json.loads(client.request("GET", "/api/me")[2])
    assert me["sections"] == ["all_regions"] and me["home"] == "/"


def test_plantation_user_cannot_open_admin_page_by_path_tricks(server):
    make_user("siti.rahma@example.com", "plantation")
    client = Client(server)
    client.login("siti.rahma@example.com")
    for trick in ("/admin/users.html", "/js/../admin/users.html", "/%61dmin/users.html", "/ADMIN/users.html",
                  "/admin/", "/admin"):
        status, _, body = client.request("GET", trick)
        assert status in (302, 404) and b"User management" not in body, trick
    assert client.request("GET", "/inhouse/index.html")[0] == 200


def test_admin_registers_user_who_can_then_sign_in(server):
    make_user("admin.one@example.com", "admin")
    admin = Client(server)
    admin.login("admin.one@example.com")
    assert admin.request("GET", "/admin/users.html")[0] == 200
    status, _, body = admin.request("POST", "/api/admin/users", {
        "email": "new.viewer@example.com", "fullName": "New Viewer", "role": "regional", "password": "Viewer-pass-2026"})
    assert status == 200 and json.loads(body)["roleName"] == "Regional viewer"
    listing = json.loads(admin.request("GET", "/api/admin/users")[2])
    assert {user["email"] for user in listing["users"]} == {"admin.one@example.com", "new.viewer@example.com"}
    assert "password_hash" not in json.dumps(listing)
    assert Client(server).login("new.viewer@example.com", "Viewer-pass-2026")[0] == 200


def test_post_requires_dashboard_headers_and_same_origin(server):
    make_user("admin.one@example.com", "admin")
    admin = Client(server)
    admin.login("admin.one@example.com")
    payload = {"email": "x.user@example.com", "fullName": "X User", "role": "regional", "password": PASSWORD}
    assert admin.request("POST", "/api/admin/users", payload, headers={"X-Requested-With": ""})[0] == 400
    assert admin.request("POST", "/api/admin/users", payload, headers={"Origin": "http://evil.example"})[0] == 403


def test_logout_ends_session(server):
    make_user("siti.rahma@example.com", "regional")
    client = Client(server)
    client.login("siti.rahma@example.com")
    session_cookie = client.cookie
    assert client.request("POST", "/api/logout", {})[0] == 200
    client.cookie = session_cookie  # replaying the old cookie must not work
    assert client.request("GET", "/api/me")[0] == 401


def test_failed_logins_are_throttled_per_ip(server, monkeypatch):
    monkeypatch.setattr(dashboard_server, "FAILED_LOGINS_PER_IP", 3)
    client = Client(server)
    for _ in range(3):
        assert client.login("nobody@example.com", "Wrong-password-1")[0] == 401
    assert client.login("nobody@example.com", "Wrong-password-1")[0] == 429


def test_admin_can_change_a_users_email(auth_db):
    admin = make_user("admin.one@example.com", "admin")
    user = make_user("siti.rahma@example.com", "regional")
    make_user("other.person@example.com", "regional")
    with pytest.raises(service.AuthError, match="already exists"):
        service.update_user(admin, user["user_id"], {"email": "Other.Person@example.com"})
    service.update_user(admin, user["user_id"], {"email": "siti.r@example.com"})
    with pytest.raises(service.AuthError):
        service.login("siti.rahma@example.com", PASSWORD, ip_address=None, user_agent=None)
    assert service.login("siti.r@example.com", PASSWORD, ip_address=None, user_agent=None)[0]


@pytest.mark.parametrize("header", [
    "rainfall_session=abc123",
    'prefs={"theme":"dark"}; rainfall_session=abc123',
    "a b=1; rainfall_session=abc123; other=x",
    "XSRF-TOKEN=eyJpdiI6Ik1=; rainfall_session=abc123",
])
def test_session_cookie_is_found_among_other_localhost_cookies(header):
    # Other local apps' cookies (JSON values, spaces) must not hide the session cookie.
    assert dashboard_server.session_token_from(header) == "abc123"


def test_signed_in_navigation_works_with_messy_cookies(server):
    make_user("siti.rahma@example.com", "regional")
    client = Client(server)
    assert client.login("siti.rahma@example.com")[0] == 200
    client.cookie = 'prefs={"theme":"dark"}; ' + client.cookie + "; a b=1"
    assert client.request("GET", "/")[0] == 200
    assert client.request("GET", "/api/me")[0] == 200


# --- separate frontend (nginx asks the backend before serving a page) ---------

def auth_check(client, uri):
    status, response, _ = client.request("GET", "/api/auth/check", headers={"X-Original-URI": uri})
    return status, response.getheader("X-Auth-Redirect")


def test_auth_check_for_anonymous_visitor(server):
    anonymous = Client(server)
    assert auth_check(anonymous, "/login.html") == (204, None)
    assert auth_check(anonymous, "/css/login.css") == (204, None)
    assert auth_check(anonymous, "/") == (401, "/login.html")
    assert auth_check(anonymous, "/inhouse/?page=seasonality") == (401, "/login.html?next=%2Finhouse%2F%3Fpage%3Dseasonality")
    assert anonymous.request("GET", "/api/auth/check")[0] == 400  # header required


def test_auth_check_applies_roles(server):
    make_user("viewer.one@example.com", "regional")
    viewer = Client(server)
    viewer.login("viewer.one@example.com")
    assert auth_check(viewer, "/weekly-seasonality.html") == (204, None)
    assert auth_check(viewer, "/css/dashboard.css") == (204, None)
    assert auth_check(viewer, "/inhouse/index.html") == (403, "/?denied=1")
    assert auth_check(viewer, "/admin/users.html") == (403, "/?denied=1")
    assert auth_check(viewer, "/js/../admin/users.html") == (403, "/?denied=1")
    assert auth_check(viewer, "/login.html") == (403, "/")  # already signed in: go to the dashboard


def test_auth_check_inhouse_only_role_lands_on_inhouse(server, monkeypatch):
    make_user("siti.rahma@example.com", "plantation")
    with transaction() as cursor:  # a role with only the in-house section
        cursor.execute("DELETE FROM app_role_section WHERE role_code = 'plantation' AND section = 'all_regions'")
    client = Client(server)
    client.login("siti.rahma@example.com")
    assert auth_check(client, "/") == (403, "/inhouse/?denied=1")
    assert auth_check(client, "/inhouse/") == (204, None)


def test_proxy_address_is_used_only_when_trusted(server, monkeypatch):
    client = Client(server)
    client.login("nobody@example.com", "Wrong-password-1")
    assert auth_repository.recent_audit()[0]["ip_address"] == "127.0.0.1"
    monkeypatch.setattr(dashboard_server.config, "TRUST_PROXY_HEADERS", True)
    client.request("POST", "/api/login", {"email": "nobody@example.com", "password": "Wrong-password-1"},
                   headers={"X-Real-IP": "10.20.30.40"})
    assert auth_repository.recent_audit()[0]["ip_address"] == "10.20.30.40"


def test_frontend_build_files_are_not_served(server):
    make_user("siti.rahma@example.com", "regional")
    client = Client(server)
    client.login("siti.rahma@example.com")
    assert client.request("GET", "/Dockerfile")[0] == 404
    assert client.request("GET", "/nginx/default.conf.template")[0] == 404


def test_health_reports_missing_schema_and_unreachable_database(server, monkeypatch):
    client = Client(server)
    status, _, body = client.request("GET", "/api/health")
    assert status == 200 and json.loads(body)["status"] == "ok"
    with transaction() as cursor:
        cursor.execute("ALTER TABLE refresh_run RENAME TO refresh_run_hidden")
    try:
        status, _, body = client.request("GET", "/api/health")
        assert status == 503 and "schema missing" in json.loads(body)["problem"]
    finally:
        with transaction() as cursor:
            cursor.execute("ALTER TABLE refresh_run_hidden RENAME TO refresh_run")
    monkeypatch.setattr(dashboard_server.config, "DATABASE_PORT", 1)
    status, _, body = client.request("GET", "/api/health")
    assert status == 503 and "unreachable" in json.loads(body)["problem"]
    assert "127.0.0.1" not in body.decode()  # no connection details leak to callers

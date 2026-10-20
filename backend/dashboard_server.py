"""Zero-dependency LAN server for the palm-oil weather dashboard.

Static files are served only from the frontend/ folder; all data comes from
PostgreSQL through the /api/* and /inhouse/data/* endpoints.

Every page and endpoint except the sign-in page requires a signed-in user, and
each one belongs to a section (all_regions, inhouse, user_admin) that the
user's role must grant. Sessions are server-side; the browser holds only an
HttpOnly, SameSite=Strict cookie with a random token.
"""

from __future__ import annotations

import html
import json
import posixpath
import re
import socket
import threading
import time
from collections import defaultdict, deque
from datetime import date
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse

import psycopg2
import psycopg2.errors

from backend import config
from backend.all_regions.cumulative_rainfall_data import build_cumulative_rainfall
from backend.all_regions.dashboard_payload import payload_bytes
from backend.all_regions.forecast_changes import build_forecast_changes, build_last_week_accuracy
from backend.auth import service as auth
from backend.auth import sso
from backend.database import auth_repository, repository


# In-house dashboard data files, kept at their original URLs so app.js is unchanged.
INHOUSE_DOCUMENTS = {
    "/inhouse/data/rainfall_daily.json": repository.rainfall_document,
    "/inhouse/data/plantations.json": repository.plantations_document,
    "/inhouse/data/forecast_14day.json": repository.forecast_document,
}

PUBLIC_PATHS = {"/login.html", "/css/login.css", "/js/login.js", "/api/login", "/api/health", "/api/auth/check",
                "/api/sso/config", "/api/sso/login", "/api/sso/callback"}
ANY_SIGNED_IN = "signed_in"
ALL_REGIONS_APIS = {"/api/cumulative-rainfall", "/api/forecast-changes", "/api/last-week-forecast-accuracy"}
ADMIN_USER_PATH = re.compile(r"^/api/admin/users/(\d+)(/password)?$")
MAX_BODY_BYTES = 16 * 1024

# Per-IP throttle on failed sign-ins, on top of the per-account lockout.
FAILED_LOGIN_WINDOW_SECONDS = 600
FAILED_LOGINS_PER_IP = 20
_failed_logins: dict[str, deque] = defaultdict(deque)
_failed_logins_lock = threading.Lock()


def session_token_from(cookie_header: str) -> str | None:
    """Find our session cookie in a Cookie header.

    Browsers send every localhost cookie to every port, so the header often holds
    cookies from other local apps. http.cookies.SimpleCookie drops all cookies
    after one it cannot parse (JSON values, spaces), so read ours directly.
    """
    for part in cookie_header.split(";"):
        name, _, value = part.strip().partition("=")
        if name == auth.SESSION_COOKIE and value:
            return value.strip().strip('"')
    return None


def canonical_path(raw_path: str) -> str:
    """Decode and normalise a URL path the way the file server will, lower-cased.

    Access is decided on this form so that "/js/../admin/x", "%61dmin" or "/ADMIN/"
    (Windows file names ignore case) cannot reach a page under a different rule.
    """
    path = posixpath.normpath(unquote(raw_path).replace("\\", "/"))
    if raw_path.endswith("/") and path != "/":
        path += "/"
    return path.lower()


def required_sections(path: str) -> tuple[str, ...] | None:
    """Sections that grant access to a path (any one is enough); None means public."""
    path = canonical_path(path)
    if path in PUBLIC_PATHS:
        return None
    if path.startswith("/admin/") or path == "/admin" or path.startswith("/api/admin/"):
        return ("user_admin",)
    if path.startswith("/inhouse/") or path == "/inhouse":
        return ("inhouse",)
    if path == "/api/data":  # the in-house seasonality page also compares with All Regions data
        return ("all_regions", "inhouse")
    if path in ALL_REGIONS_APIS:
        return ("all_regions",)
    if path == "/" or (path.endswith(".html") and path.count("/") == 1):
        return ("all_regions",)
    return (ANY_SIGNED_IN,)  # shared CSS/JS, /api/me, /api/logout


def allowed_for(user: dict, sections: tuple[str, ...]) -> bool:
    return ANY_SIGNED_IN in sections or any(auth.can(user, section) for section in sections)


def page_redirect(request_uri: str, user: dict | None) -> str | None:
    """Where to send a browser that asks for a page, or None if it may see it.

    Shared by this server and the separate frontend (nginx asks /api/auth/check).
    """
    path = urlparse(request_uri).path
    sections = required_sections(path)
    if sections is None:
        # Signed-in users who open the sign-in page go straight to their dashboard.
        return auth.home_path(user) if user and canonical_path(path) == "/login.html" else None
    if user is None:
        target = request_uri if canonical_path(path) not in ("/", "/dashboard.html") else ""
        return "/login.html" + (f"?next={quote(target, safe='')}" if target else "")
    if allowed_for(user, sections):
        return None
    return auth.home_path(user) + "?denied=1"


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(config.FRONTEND_DIR), **kwargs)

    # ---- response helpers -------------------------------------------------
    def end_headers(self):
        path = urlparse(self.path).path
        if Path(path).suffix.lower() in {".png", ".jpg", ".jpeg", ".svg", ".woff", ".woff2"}:
            self.send_header("Cache-Control", "public, max-age=3600")
        else:
            self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "same-origin")
        super().end_headers()

    def send_json(self, status: int, body: bytes | dict, *, gzip_body: bytes | None = None,
                  cookie: str | None = None) -> None:
        if isinstance(body, dict):
            body = json.dumps(body, separators=(",", ":")).encode()
        use_gzip = gzip_body is not None and "gzip" in self.headers.get("Accept-Encoding", "").lower()
        if use_gzip:
            body = gzip_body
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        if gzip_body is not None:
            self.send_header("Vary", "Accept-Encoding")
        if use_gzip:
            self.send_header("Content-Encoding", "gzip")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def redirect(self, location: str) -> None:
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def send_document(self, build) -> None:
        try:
            self.send_json(200, json.dumps(build(), separators=(",", ":"), default=str).encode())
        except auth.AccessDenied as exc:
            self.send_json(403, {"error": str(exc)})
        except auth.AuthError as exc:
            self.send_json(400, {"error": str(exc)})
        except (ValueError, KeyError) as exc:
            self.send_json(400, {"error": str(exc)})
        except LookupError as exc:
            self.send_json(404, {"error": str(exc)})
        except Exception as exc:
            self.log_error("Request failed: %s", exc)
            self.send_json(500, {"error": "The request could not be completed."})

    def list_directory(self, path):  # never list folder contents
        self.send_error(404, "File not found")
        return None

    # ---- session helpers --------------------------------------------------
    def session_token(self) -> str | None:
        return session_token_from(self.headers.get("Cookie", ""))

    def session_cookie(self, token: str, max_age: int) -> str:
        secure = "; Secure" if config.SESSION_COOKIE_SECURE else ""
        return f"{auth.SESSION_COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={max_age}{secure}"

    def client_ip(self) -> str:
        # Behind the frontend proxy every request comes from the proxy, so use the
        # address it reports. Only enabled when the backend is reachable solely via the proxy.
        if config.TRUST_PROXY_HEADERS:
            forwarded = (self.headers.get("X-Real-IP") or "").strip()
            if forwarded:
                return forwarded[:64]
        return self.client_address[0]

    def authorize(self, path: str) -> tuple[bool, dict | None]:
        """Check sign-in and role for a path. Sends the refusal itself and returns (False, None)."""
        sections = required_sections(path)
        user = auth.user_for_token(self.session_token())
        if sections is None:
            return True, user
        is_api = path.startswith("/api/") or path.startswith("/inhouse/data/")
        if is_api:
            if user is None:
                self.send_json(401, {"error": "Please sign in."})
                return False, None
            if allowed_for(user, sections):
                return True, user
            self.send_json(403, {"error": "Your role does not allow access to this data."})
            return False, None
        redirect = page_redirect(self.path, user)
        if redirect and canonical_path(path) != "/login.html":
            self.redirect(redirect)
            return False, None
        return True, user

    def send_health(self) -> None:
        """Liveness plus database readiness, with a short reason when not ready.

        Details (host names, driver messages) go to the log only, not to the caller.
        """
        try:
            version = repository.plantation_signature()
        except psycopg2.errors.UndefinedTable as exc:
            self.log_error("Health: schema missing: %s", exc)
            return self.send_json(503, {"status": "error", "problem":
                                        "Database schema missing: run python -m backend.database.init_db"})
        except psycopg2.OperationalError as exc:
            self.log_error("Health: database unreachable: %s", str(exc).strip())
            return self.send_json(503, {"status": "error", "problem":
                                        "Database unreachable: check DATABASE_* settings, whitelist and SSL mode "
                                        "(details in the backend log)"})
        except Exception as exc:
            self.log_error("Health check failed: %s", exc)
            return self.send_json(503, {"status": "error", "problem": "Backend error (details in the backend log)"})
        return self.send_json(200, {"status": "ok", "plantationVersion": version})

    def send_auth_check(self) -> None:
        """Page access decision for the frontend proxy (nginx auth_request).

        204 = serve the page; 401/403 = redirect the browser to X-Auth-Redirect.
        """
        uri = self.headers.get("X-Original-URI") or ""
        if not uri.startswith("/"):
            return self.send_json(400, {"error": "X-Original-URI is required."})
        user = auth.user_for_token(self.session_token())
        redirect = page_redirect(uri, user)
        if redirect is None:
            self.send_response(204)
            self.send_header("Content-Length", "0")
            return self.end_headers()
        self.send_response(401 if user is None else 403)
        self.send_header("X-Auth-Redirect", redirect)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def read_json(self) -> dict | None:
        """Read a JSON body from a same-origin fetch request, or send an error and return None."""
        if self.headers.get("X-Requested-With") != "fetch" or \
                not self.headers.get("Content-Type", "").startswith("application/json"):
            self.send_json(400, {"error": "Requests must be sent by the dashboard."})
            return None
        origin = self.headers.get("Origin")
        if origin and urlparse(origin).netloc != self.headers.get("Host"):
            self.send_json(403, {"error": "Cross-site request refused."})
            return None
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY_BYTES:
            self.send_json(413, {"error": "Request too large."})
            return None
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self.send_json(400, {"error": "Invalid JSON."})
            return None
        if not isinstance(data, dict):
            self.send_json(400, {"error": "Invalid request."})
            return None
        return data

    # ---- GET / HEAD -------------------------------------------------------
    def do_HEAD(self):
        allowed, _ = self.authorize(urlparse(self.path).path)
        if allowed:
            super().do_HEAD()

    def do_GET(self):
        url = urlparse(self.path)
        path = url.path
        allowed, user = self.authorize(path)
        if not allowed:
            return None
        if path == "/login.html" and user:
            return self.redirect(auth.home_path(user))
        if path in ("/admin", "/admin/"):
            return self.redirect("/admin/users.html")
        if path in INHOUSE_DOCUMENTS:
            return self.send_document(INHOUSE_DOCUMENTS[path])
        if path == "/api/auth/check":
            return self.send_auth_check()
        if path == "/api/sso/config":
            return self.send_json(200, {"ssoEnabled": config.SSO_ENABLED, "ssoLabel": config.SSO_LABEL,
                                        "localLoginEnabled": config.LOCAL_LOGIN_ENABLED})
        if path == "/api/sso/login":
            return self.handle_sso_login(parse_qs(url.query))
        if path == "/api/sso/callback":
            return self.handle_sso_callback(parse_qs(url.query))
        if path == "/api/health":
            return self.send_health()
        if path == "/api/me":
            return self.send_document(lambda: {**auth.public_user(user), "home": auth.home_path(user)})
        if path == "/api/admin/users":
            return self.send_document(lambda: {
                "users": [auth.public_user(item) for item in auth_repository.list_users()],
                "roles": auth_repository.list_roles(),
                "passwordMinLength": config.PASSWORD_MIN_LENGTH,
                "ssoEnabled": config.SSO_ENABLED,
            })
        if path == "/api/admin/audit":
            return self.send_document(lambda: {"events": auth_repository.recent_audit(50)})
        if path == "/api/cumulative-rainfall":
            query = parse_qs(url.query)
            return self.send_document(lambda: build_cumulative_rainfall(
                query.get("region", [""])[0],
                int(query.get("startMonth", ["7"])[0]),
                int(query.get("from", ["2015"])[0]),
                int(query.get("to", [str(date.today().year - 1)])[0]),
            ))
        if path == "/api/last-week-forecast-accuracy":
            return self.send_document(build_last_week_accuracy)
        if path == "/api/forecast-changes":
            return self.send_document(build_forecast_changes)
        if path == "/api/data":
            try:
                body, compressed = payload_bytes()
                return self.send_json(200, body, gzip_body=compressed)
            except Exception as exc:
                self.log_error("Request failed: %s", exc)
                return self.send_json(500, {"error": "Dashboard data could not be built."})
        if path.startswith("/api/"):
            return self.send_json(404, {"error": "Unknown API endpoint"})
        if canonical_path(path).startswith("/nginx/") or canonical_path(path) == "/dockerfile":
            return self.send_error(404, "File not found")  # frontend build files, not part of the site
        if path == "/":
            if not auth.can(user, "all_regions"):
                return self.redirect(auth.home_path(user))
            self.path = "/dashboard.html"
        return super().do_GET()

    # ---- POST -------------------------------------------------------------
    def do_POST(self):
        path = urlparse(self.path).path
        allowed, user = self.authorize(path)
        if not allowed:
            return None
        data = self.read_json()
        if data is None:
            return None
        ip = self.client_ip()
        if path == "/api/login":
            return self.handle_login(data, ip)
        if path == "/api/logout":
            auth.logout(self.session_token(), user, ip_address=ip)
            return self.send_json(200, {"ok": True}, cookie=self.session_cookie("", 0))
        if path == "/api/me/password":
            return self.send_document(lambda: auth.change_own_password(
                user, self.session_token(), data.get("currentPassword"), data.get("newPassword"),
                ip_address=ip) or {"ok": True})
        if path == "/api/admin/users":
            return self.send_document(lambda: auth.public_user(auth.create_user(user, data, ip_address=ip)))
        match = ADMIN_USER_PATH.match(path)
        if match and match.group(2):
            return self.send_document(lambda: auth.reset_password(
                user, int(match.group(1)), data.get("password"), ip_address=ip) or {"ok": True})
        if match:
            return self.send_document(lambda: auth.public_user(
                auth.update_user(user, int(match.group(1)), data, ip_address=ip)))
        return self.send_json(404, {"error": "Unknown API endpoint"})

    # ---- single sign-on (DWS Hub) ------------------------------------------
    def sso_failed(self, message: str) -> None:
        self.redirect("/login.html?" + urlencode({"sso_error": message[:300]}))

    def handle_sso_login(self, query: dict) -> None:
        if not config.SSO_ENABLED:
            return self.send_json(404, {"error": "DWS Hub sign-in is not enabled."})
        try:
            return self.redirect(sso.start_login(query.get("next", ["/"])[0]))
        except auth.AuthError as exc:
            return self.sso_failed(str(exc))
        except Exception as exc:
            self.log_error("SSO start failed: %s", exc)
            return self.sso_failed("DWS Hub sign-in is unavailable. Try again shortly.")

    def handle_sso_callback(self, query: dict) -> None:
        if not config.SSO_ENABLED:
            return self.send_json(404, {"error": "DWS Hub sign-in is not enabled."})
        if query.get("error"):
            reason = query.get("error_description", query["error"])[0]
            return self.sso_failed(f"DWS Hub did not sign you in: {reason}")
        try:
            token, _, next_path = sso.complete_login(
                query.get("code", [""])[0], query.get("state", [""])[0],
                ip_address=self.client_ip(), user_agent=self.headers.get("User-Agent"))
        except auth.AuthError as exc:
            return self.sso_failed(str(exc))
        except Exception as exc:
            self.log_error("SSO callback failed: %s", exc)
            return self.sso_failed("DWS Hub sign-in failed. Try again shortly.")
        # The Strict session cookie is not sent on a redirect chain that started at the
        # Hub, so continue with a page-initiated (same-site) navigation instead of a 302.
        target = html.escape(next_path, quote=True)
        body = (f'<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0;url={target}">'
                f'<title>Signing in</title><p style="font-family:sans-serif">Signing you in… '
                f'<a href="{target}">Continue</a></p>').encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Set-Cookie", self.session_cookie(token, config.SESSION_HOURS * 3600))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def handle_login(self, data: dict, ip: str) -> None:
        if not config.LOCAL_LOGIN_ENABLED:
            return self.send_json(403, {"error": "Sign in with DWS Hub."})
        now = time.monotonic()
        with _failed_logins_lock:
            attempts = _failed_logins[ip]
            while attempts and now - attempts[0] > FAILED_LOGIN_WINDOW_SECONDS:
                attempts.popleft()
            if len(attempts) >= FAILED_LOGINS_PER_IP:
                return self.send_json(429, {"error": "Too many failed sign-in attempts. Wait a few minutes."})
        try:
            token, user = auth.login(str(data.get("email") or ""), str(data.get("password") or ""),
                                     ip_address=ip, user_agent=self.headers.get("User-Agent"))
        except auth.AuthError as exc:
            with _failed_logins_lock:
                _failed_logins[ip].append(now)
            return self.send_json(401, {"error": str(exc)})
        except Exception as exc:
            self.log_error("Sign-in failed: %s", exc)
            return self.send_json(500, {"error": "Sign-in is unavailable. Try again shortly."})
        with _failed_logins_lock:
            _failed_logins.pop(ip, None)
        self.send_json(200, {"ok": True, "home": auth.home_path(user), "user": auth.public_user(user)},
                       cookie=self.session_cookie(token, config.SESSION_HOURS * 3600))


def main() -> None:
    socket.setdefaulttimeout(30)
    server = ThreadingHTTPServer((config.DASHBOARD_HOST, config.DASHBOARD_PORT), Handler)
    print(f"Dashboard ready: http://localhost:{config.DASHBOARD_PORT}")
    print(f"Network address: {config.DASHBOARD_PUBLIC_URL}")
    print("Keep this window open. Press Ctrl+C to stop.")
    server.serve_forever()


if __name__ == "__main__":
    main()

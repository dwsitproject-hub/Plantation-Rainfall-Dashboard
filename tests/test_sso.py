"""DWS Hub single sign-on (OIDC code + PKCE) against a fake Hub with real RSA-signed tokens."""

from __future__ import annotations

import base64
import hashlib
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from backend.auth import sso
from backend.database import auth_repository
from tests.test_auth import Client, PASSWORD, auth_db, make_user, server  # noqa: F401  (fixtures)

CLIENT_ID = "plantation-rainfall-staging"


class FakeHub:
    """Discovery, JWKS and token endpoint of a strict-mode DWS Hub."""

    def __init__(self):
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.kid = "test-key-1"
        self.codes: dict[str, dict] = {}
        self.overrides: dict = {}
        hub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, status, body):
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if self.path == "/api/sso/.well-known/openid-configuration":
                    return self.reply(200, {"issuer": hub.issuer, "authorization_endpoint": f"{hub.issuer}/api/sso/authorize",
                                            "token_endpoint": f"{hub.issuer}/api/sso/token", "jwks_uri": f"{hub.issuer}/api/sso/jwks"})
                if self.path == "/api/sso/jwks":
                    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(hub.key.public_key()))
                    return self.reply(200, {"keys": [{**jwk, "kid": hub.kid, "alg": "RS256", "use": "sig"}]})
                self.reply(404, {"error": "not_found"})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                grant = hub.codes.pop(body.get("code"), None)
                challenge = base64.urlsafe_b64encode(hashlib.sha256(body.get("code_verifier", "").encode()).digest()).rstrip(b"=").decode()
                if (grant is None or body.get("client_id") != CLIENT_ID or body.get("redirect_uri") != grant["redirect_uri"]
                        or challenge != grant["code_challenge"]):
                    return self.reply(400, {"error": "invalid_grant"})
                self.reply(200, {"token_type": "Bearer", "expires_in": 60, "id_token": hub.id_token(grant["claims"]),
                                 "scope": "openid profile email"})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.issuer = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def id_token(self, claims: dict) -> str:
        now = int(time.time())
        payload = {"iss": self.issuer, "aud": CLIENT_ID, "iat": now, "exp": now + 60, **claims, **self.overrides}
        payload = {key: value for key, value in payload.items() if value is not None}
        signer = self.overrides.pop("_signer", self.key)
        payload.pop("_signer", None)
        return jwt.encode(payload, signer, algorithm="RS256", headers={"kid": self.kid})

    def approve(self, authorize_url: str, *, sub: str, email: str, name: str = "Hub User", **extra) -> tuple[str, str]:
        """Act as the Hub after the user signs in: issue a code for this authorize request."""
        query = {k: v[0] for k, v in parse_qs(urlparse(authorize_url).query).items()}
        assert query["response_type"] == "code" and query["code_challenge_method"] == "S256"
        assert query["client_id"] == CLIENT_ID and query["scope"] == "openid profile email"
        code = f"code-{len(self.codes)}-{time.time_ns()}"
        self.codes[code] = {"code_challenge": query["code_challenge"], "redirect_uri": query["redirect_uri"],
                            "claims": {"sub": sub, "user_id": sub, "email": email, "name": name,
                                       "nonce": query["nonce"], **extra}}
        return code, query["state"]


@pytest.fixture
def hub(server, monkeypatch):
    fake = FakeHub()
    monkeypatch.setattr(sso.config, "SSO_ENABLED", True)
    monkeypatch.setenv("SSO_DISCOVERY_URL", f"{fake.issuer}/api/sso/.well-known/openid-configuration")
    monkeypatch.setenv("SSO_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("SSO_REDIRECT_URI", f"http://127.0.0.1:{server}/api/sso/callback")
    sso.reset_caches()
    yield fake
    fake.httpd.shutdown()
    sso.reset_caches()


def start(client, next_path="/weekly-seasonality.html"):
    status, response, _ = client.request("GET", f"/api/sso/login?next={next_path}")
    assert status == 302
    return response.getheader("Location")


def finish(client, code, state):
    return client.request("GET", f"/api/sso/callback?code={code}&state={state}")


def test_sso_sign_in_links_registered_user_and_opens_session(server, hub):
    make_user("siti.rahma@example.com", "regional")
    client = Client(server)
    authorize = start(client)
    assert authorize.startswith(f"{hub.issuer}/api/sso/authorize?")
    code, state = hub.approve(authorize, sub="hub-uuid-1", email="Siti.Rahma@example.com")
    status, response, body = finish(client, code, state)
    assert status == 200 and b'url=/weekly-seasonality.html' in body
    cookie = response.getheader("Set-Cookie")
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie
    assert client.request("GET", "/weekly-seasonality.html")[0] == 200
    me = json.loads(client.request("GET", "/api/me")[2])
    assert me["email"] == "siti.rahma@example.com" and me["ssoLinked"] is True
    assert auth_repository.recent_audit()[0]["action"] == "sso_login_succeeded"
    # Next time the Hub user is found by sub, even if their Hub email changed.
    authorize = start(Client(server))
    code, state = hub.approve(authorize, sub="hub-uuid-1", email="new.address@example.com")
    assert finish(Client(server), code, state)[0] == 200


def test_state_is_single_use_and_unknown_state_is_refused(server, hub):
    make_user("siti.rahma@example.com", "regional")
    client = Client(server)
    code, state = hub.approve(start(client), sub="hub-uuid-1", email="siti.rahma@example.com")
    assert finish(client, code, state)[0] == 200
    status, response, _ = finish(Client(server), code, state)  # replay
    assert status == 302 and "sso_error=" in response.getheader("Location")
    status, response, _ = finish(Client(server), "x", "forged-state")
    assert status == 302 and "expired" in response.getheader("Location")


def test_unregistered_hub_user_is_refused_unless_auto_provisioning(server, hub, monkeypatch):
    client = Client(server)
    code, state = hub.approve(start(client), sub="hub-uuid-9", email="new.person@example.com", name="New Person")
    status, response, _ = finish(client, code, state)
    assert status == 302 and "no+access" in response.getheader("Location")
    monkeypatch.setattr(sso.config, "SSO_AUTO_PROVISION_ROLE", "regional")
    client = Client(server)
    code, state = hub.approve(start(client), sub="hub-uuid-9", email="new.person@example.com", name="New Person")
    assert finish(client, code, state)[0] == 200
    me = json.loads(client.request("GET", "/api/me")[2])
    assert me["role"] == "regional" and me["fullName"] == "New Person"


@pytest.mark.parametrize("override, reason", [
    ({"aud": "some-other-app"}, "rejected"),
    ({"iss": "http://evil.example"}, "rejected"),
    ({"exp": int(time.time()) - 3600}, "rejected"),
    ({"nonce": "not-the-one-we-sent"}, "nonce"),
    ({"sub": None}, "rejected"),
])
def test_invalid_id_tokens_are_rejected(server, hub, override, reason):
    make_user("siti.rahma@example.com", "regional")
    client = Client(server)
    code, state = hub.approve(start(client), sub="hub-uuid-1", email="siti.rahma@example.com")
    hub.overrides = dict(override)
    status, response, _ = finish(client, code, state)
    assert status == 302 and "sso_error=" in response.getheader("Location") and reason in response.getheader("Location")
    assert client.request("GET", "/api/me")[0] == 401


def test_token_signed_by_another_key_is_rejected(server, hub):
    make_user("siti.rahma@example.com", "regional")
    client = Client(server)
    code, state = hub.approve(start(client), sub="hub-uuid-1", email="siti.rahma@example.com")
    hub.overrides = {"_signer": rsa.generate_private_key(public_exponent=65537, key_size=2048)}
    status, response, _ = finish(client, code, state)
    assert status == 302 and "rejected" in response.getheader("Location")


def test_hub_rejecting_the_code_is_reported(server, hub):
    client = Client(server)
    start(client)
    status, response, _ = finish(client, "code-the-hub-never-issued", "unknown")
    assert status == 302 and "sso_error=" in response.getheader("Location")


def test_disabled_and_hub_only_accounts(server, hub, monkeypatch):
    admin = make_user("admin.one@example.com", "admin")
    from backend.auth import service
    hub_only = service.create_user(admin, {"email": "hub.only@example.com", "fullName": "Hub Only", "role": "regional"})
    assert Client(server).login("hub.only@example.com", "")[0] == 401  # no local password
    service.update_user(admin, hub_only["user_id"], {"isActive": False})
    client = Client(server)
    code, state = hub.approve(start(client), sub="hub-uuid-2", email="hub.only@example.com")
    status, response, _ = finish(client, code, state)
    assert status == 302 and "disabled" in response.getheader("Location")


def test_local_login_can_be_switched_off(server, hub, monkeypatch):
    make_user("siti.rahma@example.com", "regional")
    monkeypatch.setattr(sso.config, "LOCAL_LOGIN_ENABLED", False)
    assert Client(server).login("siti.rahma@example.com")[0] == 403
    config_doc = json.loads(Client(server).request("GET", "/api/sso/config")[2])
    assert config_doc == {"ssoEnabled": True, "ssoLabel": "Sign in with DWS Hub", "localLoginEnabled": False}


def test_sso_endpoints_are_off_by_default(server, monkeypatch):
    monkeypatch.setattr(sso.config, "SSO_ENABLED", False)
    assert Client(server).request("GET", "/api/sso/login")[0] == 404
    assert Client(server).request("GET", "/api/sso/callback?code=x&state=y")[0] == 404

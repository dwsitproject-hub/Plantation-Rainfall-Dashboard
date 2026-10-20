"""Single sign-on with DWS Hub: OIDC authorization code flow with PKCE (strict mode).

1. /api/sso/login    -> store state + PKCE verifier + nonce, redirect the browser to the Hub.
2. Hub               -> user signs in, Hub redirects to SSO_REDIRECT_URI with ?code&state.
3. /api/sso/callback -> exchange the code at the Hub token endpoint (server to server),
                        verify the ID token against the Hub JWKS (signature, iss, aud,
                        exp, sub, nonce), match or link the dashboard user, open a session.

The Hub "sub" claim is the canonical user key; email and name are profile data only.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import jwt

from backend import config
from backend.auth import passwords, service
from backend.database import auth_repository as repo


ALGORITHMS = ["RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512", "EdDSA"]
DISCOVERY_TTL_SECONDS = 3600
CLOCK_SKEW_SECONDS = 60

_lock = threading.Lock()
_discovery: tuple[float, dict] | None = None
_jwks_clients: dict[str, jwt.PyJWKClient] = {}


class SsoError(service.AuthError):
    """A sign-in problem that is safe to show to the user."""


class UnknownSsoState(SsoError):
    """The callback carries a state this dashboard did not issue (or already used).

    Happens when a sign-in is started from the DWS Hub tile instead of from this
    dashboard: the Hub's code cannot be redeemed without our PKCE verifier.
    """


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _http_json(url: str, *, body: dict | None = None) -> dict:
    data = None if body is None else json.dumps(body).encode()
    headers = {"Accept": "application/json", "User-Agent": config.HTTP_USER_AGENT}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = Request(url, data=data, headers=headers, method="POST" if data is not None else "GET")
    try:
        with urlopen(request, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise SsoError(f"DWS Hub returned HTTP {exc.code}: {detail}") from exc
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise SsoError(f"DWS Hub could not be reached: {exc}") from exc


def discovery() -> dict:
    """The Hub's OpenID configuration, cached for an hour."""
    global _discovery
    with _lock:
        if _discovery and time.monotonic() - _discovery[0] < DISCOVERY_TTL_SECONDS:
            return _discovery[1]
    document = _http_json(config.sso()["discovery_url"])
    for key in ("issuer", "authorization_endpoint", "token_endpoint", "jwks_uri"):
        if not document.get(key):
            raise SsoError(f"DWS Hub discovery document has no {key}.")
    with _lock:
        _discovery = (time.monotonic(), document)
    return document


def _jwks_client(jwks_uri: str) -> jwt.PyJWKClient:
    with _lock:
        if jwks_uri not in _jwks_clients:
            _jwks_clients[jwks_uri] = jwt.PyJWKClient(jwks_uri, cache_keys=True, lifespan=600,
                                                      headers={"User-Agent": config.HTTP_USER_AGENT})
        return _jwks_clients[jwks_uri]


def safe_next(path: str | None) -> str:
    """Only return to a page on this site."""
    if path and path.startswith("/") and not path.startswith(("//", "/\\")) and not path.startswith("/login"):
        return path[:500]
    return "/"


# ---------------------------------------------------------------------------
# Flow
# ---------------------------------------------------------------------------

def start_login(next_path: str | None) -> str:
    """Create a pending sign-in and return the Hub authorize URL to redirect the browser to."""
    settings = config.sso()
    document = discovery()
    state, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(64)  # 86 characters, within the 43-128 PKCE range
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    repo.save_sso_request(service.token_hash(state), code_verifier=verifier, nonce=nonce,
                          next_path=safe_next(next_path))
    query = urlencode({
        "response_type": "code", "client_id": settings["client_id"], "redirect_uri": settings["redirect_uri"],
        "scope": config.SSO_SCOPES, "state": state, "nonce": nonce,
        "code_challenge": challenge, "code_challenge_method": "S256",
    })
    base = settings["authorization_url"] or document["authorization_endpoint"]
    return f"{base}{'&' if '?' in base else '?'}{query}"


def verify_id_token(id_token: str, *, nonce: str) -> dict:
    settings, document = config.sso(), discovery()
    try:
        signing_key = _jwks_client(document["jwks_uri"]).get_signing_key_from_jwt(id_token)
        claims = jwt.decode(
            id_token, signing_key.key, algorithms=ALGORITHMS,
            audience=settings["client_id"], issuer=document["issuer"], leeway=CLOCK_SKEW_SECONDS,
            options={"require": ["exp", "iss", "aud", "sub"]},
        )
    except jwt.PyJWKClientError as exc:
        raise SsoError(f"DWS Hub signing keys could not be used: {exc}") from exc
    except jwt.InvalidTokenError as exc:
        raise SsoError(f"The sign-in token from DWS Hub was rejected: {exc}") from exc
    if not str(claims.get("sub") or "").strip():
        raise SsoError("The sign-in token from DWS Hub has no user id (sub).")
    if "nonce" in claims and claims["nonce"] != nonce:
        raise SsoError("The sign-in token from DWS Hub does not belong to this sign-in (nonce).")
    return claims


def exchange_code(code: str, code_verifier: str) -> str:
    settings, document = config.sso(), discovery()
    body = {"grant_type": "authorization_code", "code": code, "redirect_uri": settings["redirect_uri"],
            "client_id": settings["client_id"], "code_verifier": code_verifier}
    if settings["client_secret"]:
        body["client_secret"] = settings["client_secret"]
    result = _http_json(document["token_endpoint"], body=body)
    if not result.get("id_token"):
        raise SsoError(f"DWS Hub did not return an ID token: {result.get('error') or result}")
    return result["id_token"]


def _dashboard_user(claims: dict, *, ip_address: str | None) -> dict:
    """Find the dashboard user for Hub claims: by sub, then by email (and link), or provision."""
    subject = str(claims["sub"])
    user = repo.get_user_by_sso_subject(subject)
    if user:
        return user
    email = service.normalise_email(claims.get("email"))
    found = repo.get_login(email) if email else None
    if found:
        user = found[0]
        if user["sso_subject"] and user["sso_subject"] != subject:
            raise SsoError("This email is already linked to a different DWS Hub account. Contact an administrator.")
        repo.link_sso_subject(user["user_id"], subject)
        repo.audit("sso_linked", target_user_id=user["user_id"], email=email, ip_address=ip_address,
                   detail={"sub": subject})
        return repo.get_user(user["user_id"])
    role = config.SSO_AUTO_PROVISION_ROLE
    if not role or not email:
        repo.audit("login_failed", email=email or None, ip_address=ip_address,
                   detail={"reason": "sso user not registered", "sub": subject})
        raise SsoError(f"{email or 'Your DWS Hub account'} has no access to this dashboard yet. "
                       "Ask an administrator to register you.")
    user_id = repo.create_user(email=email, full_name=(claims.get("name") or email.split("@")[0])[:100],
                               role_code=role, password_hash=passwords.NO_PASSWORD, created_by=None)
    repo.link_sso_subject(user_id, subject)
    repo.audit("user_created", target_user_id=user_id, email=email, ip_address=ip_address,
               detail={"role": role, "via": "sso auto-provision"})
    return repo.get_user(user_id)


def complete_login(code: str, state: str, *, ip_address: str | None,
                   user_agent: str | None) -> tuple[str, dict, str]:
    """Finish a Hub sign-in. Returns (session token, user, page to open)."""
    if not code or not state:
        raise SsoError("The DWS Hub sign-in response was incomplete. Please try again.")
    pending = repo.take_sso_request(service.token_hash(state))
    if pending is None:
        raise UnknownSsoState("This sign-in link has expired or was already used. Please sign in again.")
    claims = verify_id_token(exchange_code(code, pending["code_verifier"]), nonce=pending["nonce"])
    user = _dashboard_user(claims, ip_address=ip_address)
    if not user["is_active"]:
        repo.audit("login_failed", target_user_id=user["user_id"], email=user["email"], ip_address=ip_address,
                   detail={"reason": "inactive", "via": "sso"})
        raise SsoError("This account is disabled. Contact an administrator.")
    token = service.open_session(user, ip_address=ip_address, user_agent=user_agent,
                                 action="sso_login_succeeded", detail={"sub": str(claims["sub"])})
    return token, user, pending["next_path"]


def reset_caches() -> None:
    """Forget the cached discovery document and keys (tests, key rotation)."""
    global _discovery
    with _lock:
        _discovery = None
        _jwks_clients.clear()

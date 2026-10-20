"""Password hashing with scrypt (Python standard library) and the password policy."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

from backend import config


N, R, P, KEY_LENGTH = 2 ** 14, 8, 1, 32

# Stored for accounts that sign in only through DWS Hub; never matches any password.
NO_PASSWORD = "!sso-only"


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=N, r=R, p=P, dklen=KEY_LENGTH)
    return f"scrypt${N}${R}${P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(digest)
        actual = hashlib.scrypt(password.encode("utf-8"), salt=base64.b64decode(salt),
                                n=int(n), r=int(r), p=int(p), dklen=len(expected))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


# Hash of a random value, verified when the email is not registered so that
# response time does not reveal which emails have accounts.
DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


def policy_problem(password: str, *, email: str = "") -> str | None:
    """Return why a password is not acceptable, or None if it is."""
    if len(password) < config.PASSWORD_MIN_LENGTH:
        return f"Password must be at least {config.PASSWORD_MIN_LENGTH} characters."
    if len(password) > 128:
        return "Password must be at most 128 characters."
    if not any(c.isalpha() for c in password) or not any(c.isdigit() for c in password):
        return "Password must contain both letters and numbers."
    local_part = email.split("@", 1)[0].lower()
    if len(local_part) >= 3 and local_part in password.lower():
        return "Password must not contain your email name."
    return None

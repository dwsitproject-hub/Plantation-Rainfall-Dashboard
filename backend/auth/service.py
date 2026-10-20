"""Sign-in, sessions, role-based access and user administration rules.

Users sign in with their email address and password.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from datetime import datetime, timezone

from backend import config
from backend.auth import passwords
from backend.database import auth_repository as repo


SESSION_COOKIE = "rainfall_session"
SECTIONS = ("all_regions", "inhouse", "user_admin")
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AuthError(Exception):
    """A user-facing sign-in or validation problem (safe to show)."""


class AccessDenied(Exception):
    pass


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def normalise_email(email: str | None) -> str:
    return (email or "").strip().lower()


def public_user(user: dict) -> dict:
    """The fields of a user that are safe to send to the browser."""
    locked = bool(user.get("locked_until") and user["locked_until"] > datetime.now(timezone.utc))
    iso = lambda value: value.isoformat(timespec="seconds") if value else None
    return {
        "userId": user["user_id"], "email": user["email"], "fullName": user["full_name"],
        "role": user["role_code"], "roleName": user["role_name"],
        "sections": list(user["sections"]), "isActive": user["is_active"], "isLocked": locked,
        "ssoLinked": bool(user.get("sso_subject")),
        "lastLoginAt": iso(user["last_login_at"]), "createdAt": iso(user["created_at"]),
    }


# ---------------------------------------------------------------------------
# Sign-in and sessions
# ---------------------------------------------------------------------------

def login(email: str, password: str, *, ip_address: str | None, user_agent: str | None) -> tuple[str, dict]:
    """Verify an email and password and open a session. Returns (cookie token, user)."""
    email = normalise_email(email)
    found = repo.get_login(email) if email else None
    if found is None:
        passwords.verify_password(password or "", passwords.DUMMY_HASH)  # equal timing
        repo.audit("login_failed", email=email, ip_address=ip_address, detail={"reason": "unknown email"})
        raise AuthError("Incorrect email or password.")
    user, stored_hash = found
    now = datetime.now(timezone.utc)
    if user["locked_until"] and user["locked_until"] > now:
        repo.audit("login_blocked", target_user_id=user["user_id"], email=email, ip_address=ip_address)
        minutes = max(1, int((user["locked_until"] - now).total_seconds() // 60) + 1)
        raise AuthError(f"Too many failed attempts. Try again in {minutes} minute{'s' if minutes > 1 else ''}.")
    if not passwords.verify_password(password or "", stored_hash):
        locked = repo.record_failed_login(user["user_id"], max_attempts=config.LOGIN_MAX_FAILED_ATTEMPTS,
                                          lockout_minutes=config.LOGIN_LOCKOUT_MINUTES)
        repo.audit("login_failed", target_user_id=user["user_id"], email=email, ip_address=ip_address,
                   detail={"reason": "wrong password", "locked": locked})
        raise AuthError("Incorrect email or password.")
    if not user["is_active"]:
        repo.audit("login_failed", target_user_id=user["user_id"], email=email, ip_address=ip_address,
                   detail={"reason": "inactive"})
        raise AuthError("This account is disabled. Contact an administrator.")
    return open_session(user, ip_address=ip_address, user_agent=user_agent, action="login_succeeded"), user


def open_session(user: dict, *, ip_address: str | None, user_agent: str | None, action: str,
                 detail: dict | None = None) -> str:
    """Start a session for a verified user (password or SSO) and return the cookie token."""
    if not user["sections"]:
        raise AuthError("Your role does not give access to any dashboard. Contact an administrator.")
    token = secrets.token_urlsafe(32)
    repo.create_session(token_hash(token), user["user_id"], hours=config.SESSION_HOURS,
                        ip_address=ip_address, user_agent=user_agent)
    repo.record_successful_login(user["user_id"])
    repo.audit(action, actor_user_id=user["user_id"], target_user_id=user["user_id"],
               email=user["email"], ip_address=ip_address, detail=detail)
    return token


def user_for_token(token: str | None) -> dict | None:
    if not token or len(token) > 100:
        return None
    return repo.session_user(token_hash(token))


def logout(token: str | None, user: dict | None, *, ip_address: str | None) -> None:
    if token:
        repo.delete_session(token_hash(token))
    if user:
        repo.audit("logout", actor_user_id=user["user_id"], target_user_id=user["user_id"],
                   email=user["email"], ip_address=ip_address)


def change_own_password(user: dict, token: str, current: str, new: str, *, ip_address: str | None) -> None:
    found = repo.get_login(user["email"])
    if not found or not passwords.verify_password(current or "", found[1]):
        raise AuthError("Your current password is incorrect.")
    problem = passwords.policy_problem(new or "", email=user["email"])
    if problem:
        raise AuthError(problem)
    repo.set_password(user["user_id"], passwords.hash_password(new), keep_token_hash=token_hash(token))
    repo.audit("password_changed", actor_user_id=user["user_id"], target_user_id=user["user_id"],
               ip_address=ip_address)


# ---------------------------------------------------------------------------
# Role-based access
# ---------------------------------------------------------------------------

def can(user: dict | None, section: str) -> bool:
    return bool(user) and section in user["sections"]


def home_path(user: dict) -> str:
    """Where a signed-in user lands: the first section their role allows."""
    if can(user, "all_regions"):
        return "/"
    if can(user, "inhouse"):
        return "/inhouse/"
    if can(user, "user_admin"):
        return "/admin/users.html"
    return "/login.html"


# ---------------------------------------------------------------------------
# User administration (admin only)
# ---------------------------------------------------------------------------

def _require_admin(actor: dict) -> None:
    if not can(actor, "user_admin"):
        raise AccessDenied("Only administrators can manage users.")


def _clean_email(email: str | None, *, current_user_id: int | None = None) -> str:
    email = normalise_email(email)
    if not email or len(email) > 200 or not EMAIL_PATTERN.match(email):
        raise AuthError("Enter a valid email address.")
    existing = repo.get_login(email)
    if existing and existing[0]["user_id"] != current_user_id:
        raise AuthError("A user with that email address already exists.")
    return email


def _clean_name(full_name: str | None) -> str:
    full_name = " ".join((full_name or "").split())
    if not 2 <= len(full_name) <= 100:
        raise AuthError("Full name must be 2 to 100 characters.")
    return full_name


def _check_role(role_code: str) -> None:
    if not role_code or not repo.role_exists(role_code):
        raise AuthError("Choose a valid role.")


def create_user(actor: dict | None, data: dict, *, ip_address: str | None = None) -> dict:
    """Create a user. actor=None is only used by the command-line bootstrap of the first admin."""
    if actor is not None:
        _require_admin(actor)
    email = _clean_email(data.get("email"))
    full_name = _clean_name(data.get("fullName"))
    role_code = data.get("role") or ""
    _check_role(role_code)
    password = data.get("password") or ""
    if not password and config.SSO_ENABLED and actor is not None:
        stored_hash = passwords.NO_PASSWORD  # signs in with DWS Hub only
    else:
        problem = passwords.policy_problem(password, email=email)
        if problem:
            raise AuthError(problem)
        stored_hash = passwords.hash_password(password)
    user_id = repo.create_user(email=email, full_name=full_name, role_code=role_code,
                               password_hash=stored_hash,
                               created_by=actor["user_id"] if actor else None)
    repo.audit("user_created", actor_user_id=actor["user_id"] if actor else None, target_user_id=user_id,
               email=email, ip_address=ip_address, detail={"role": role_code})
    return repo.get_user(user_id)


def update_user(actor: dict, user_id: int, data: dict, *, ip_address: str | None = None) -> dict:
    _require_admin(actor)
    target = repo.get_user(user_id)
    if target is None:
        raise LookupError("User not found.")
    changes: dict = {}
    if "fullName" in data:
        full_name = _clean_name(data["fullName"])
        if full_name != target["full_name"]:
            changes["full_name"] = full_name
    if "email" in data and normalise_email(data["email"]) != target["email"]:
        changes["email"] = _clean_email(data["email"], current_user_id=user_id)
    if "role" in data and data["role"] != target["role_code"]:
        _check_role(data["role"])
        changes["role_code"] = data["role"]
    if "isActive" in data and bool(data["isActive"]) != target["is_active"]:
        changes["is_active"] = bool(data["isActive"])
    if user_id == actor["user_id"] and (changes.get("is_active") is False or
                                         ("role_code" in changes and not repo.role_has_section(changes["role_code"], "user_admin"))):
        raise AuthError("You cannot disable your own account or remove your own administrator role.")
    removes_admin = can(target, "user_admin") and target["is_active"] and (
        changes.get("is_active") is False or
        ("role_code" in changes and not repo.role_has_section(changes["role_code"], "user_admin")))
    if removes_admin and repo.count_active_admins(excluding_user_id=user_id) == 0:
        raise AuthError("At least one active administrator must remain.")
    if changes:
        repo.update_user(user_id, **changes)
        repo.audit("user_updated", actor_user_id=actor["user_id"], target_user_id=user_id, ip_address=ip_address,
                   detail={key: value for key, value in changes.items()})
    if data.get("unlock"):
        repo.unlock_user(user_id)
        repo.audit("user_unlocked", actor_user_id=actor["user_id"], target_user_id=user_id, ip_address=ip_address)
    return repo.get_user(user_id)


def reset_password(actor: dict, user_id: int, new_password: str, *, ip_address: str | None = None) -> None:
    _require_admin(actor)
    target = repo.get_user(user_id)
    if target is None:
        raise LookupError("User not found.")
    problem = passwords.policy_problem(new_password or "", email=target["email"])
    if problem:
        raise AuthError(problem)
    repo.set_password(user_id, passwords.hash_password(new_password))
    repo.audit("password_reset", actor_user_id=actor["user_id"], target_user_id=user_id, ip_address=ip_address)

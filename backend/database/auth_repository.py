"""SQL for users, roles, sessions and the authentication audit trail."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from backend.database.db import transaction


USER_COLUMNS = """
    u.user_id, u.email, u.full_name, u.role_code, r.role_name, u.is_active,
    u.failed_login_count, u.locked_until, u.last_login_at, u.created_at, u.password_changed_at,
    u.sso_subject,
    COALESCE((SELECT array_agg(s.section ORDER BY s.section) FROM app_role_section s
              WHERE s.role_code = u.role_code), '{}') AS sections
"""


def _user(row) -> dict | None:
    if row is None:
        return None
    keys = ("user_id", "email", "full_name", "role_code", "role_name", "is_active",
            "failed_login_count", "locked_until", "last_login_at", "created_at", "password_changed_at",
            "sso_subject", "sections")
    return dict(zip(keys, row))


# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------

def list_roles() -> list[dict]:
    with transaction() as cursor:
        cursor.execute("""
            SELECT r.role_code, r.role_name, r.description,
                   COALESCE(array_agg(s.section ORDER BY s.section) FILTER (WHERE s.section IS NOT NULL), '{}')
            FROM app_role r LEFT JOIN app_role_section s USING (role_code)
            GROUP BY r.role_code, r.role_name, r.description
            ORDER BY count(s.section) DESC, r.role_code
        """)
        return [{"role_code": code, "role_name": name, "description": description, "sections": sections}
                for code, name, description, sections in cursor.fetchall()]


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

def get_user(user_id: int) -> dict | None:
    with transaction() as cursor:
        cursor.execute(f"SELECT {USER_COLUMNS} FROM app_user u JOIN app_role r USING (role_code) WHERE u.user_id = %s",
                       (user_id,))
        return _user(cursor.fetchone())


def get_login(email: str) -> tuple[dict, str] | None:
    """Return (user, password_hash) for a sign-in email, case-insensitively."""
    with transaction() as cursor:
        cursor.execute(f"""
            SELECT {USER_COLUMNS}, u.password_hash FROM app_user u JOIN app_role r USING (role_code)
            WHERE lower(u.email) = lower(%s)
        """, (email,))
        row = cursor.fetchone()
    return (_user(row[:-1]), row[-1]) if row else None


def list_users() -> list[dict]:
    with transaction() as cursor:
        cursor.execute(f"SELECT {USER_COLUMNS} FROM app_user u JOIN app_role r USING (role_code) "
                       "ORDER BY u.is_active DESC, lower(u.full_name), lower(u.email)")
        return [_user(row) for row in cursor.fetchall()]


def create_user(*, email: str, full_name: str, role_code: str, password_hash: str,
                created_by: int | None) -> int:
    with transaction() as cursor:
        cursor.execute("""
            INSERT INTO app_user (email, full_name, role_code, password_hash, created_by)
            VALUES (%s, %s, %s, %s, %s) RETURNING user_id
        """, (email, full_name, role_code, password_hash, created_by))
        return cursor.fetchone()[0]


def update_user(user_id: int, *, full_name: str | None = None, email: str | None = None,
                role_code: str | None = None, is_active: bool | None = None) -> None:
    changes = {key: value for key, value in
               (("full_name", full_name), ("email", email), ("role_code", role_code), ("is_active", is_active))
               if value is not None}
    if not changes:
        return
    assignments = ", ".join(f"{column} = %s" for column in changes)
    with transaction() as cursor:
        cursor.execute(f"UPDATE app_user SET {assignments}, updated_at = now() WHERE user_id = %s",
                       (*changes.values(), user_id))
        if is_active is False:
            cursor.execute("DELETE FROM app_session WHERE user_id = %s", (user_id,))


def set_password(user_id: int, password_hash: str, *, end_sessions: bool = True,
                 keep_token_hash: str | None = None) -> None:
    with transaction() as cursor:
        cursor.execute("""
            UPDATE app_user SET password_hash = %s, password_changed_at = now(), failed_login_count = 0,
                   locked_until = NULL, updated_at = now() WHERE user_id = %s
        """, (password_hash, user_id))
        if end_sessions:
            cursor.execute("DELETE FROM app_session WHERE user_id = %s AND token_hash IS DISTINCT FROM %s",
                           (user_id, keep_token_hash))


def unlock_user(user_id: int) -> None:
    with transaction() as cursor:
        cursor.execute("UPDATE app_user SET failed_login_count = 0, locked_until = NULL, updated_at = now() "
                       "WHERE user_id = %s", (user_id,))


def count_active_admins(excluding_user_id: int | None = None) -> int:
    with transaction() as cursor:
        cursor.execute("""
            SELECT count(*) FROM app_user u JOIN app_role_section s USING (role_code)
            WHERE u.is_active AND s.section = 'user_admin' AND u.user_id IS DISTINCT FROM %s
        """, (excluding_user_id,))
        return cursor.fetchone()[0]


def role_has_section(role_code: str, section: str) -> bool:
    with transaction() as cursor:
        cursor.execute("SELECT 1 FROM app_role_section WHERE role_code = %s AND section = %s",
                       (role_code, section))
        return cursor.fetchone() is not None


def role_exists(role_code: str) -> bool:
    with transaction() as cursor:
        cursor.execute("SELECT 1 FROM app_role WHERE role_code = %s", (role_code,))
        return cursor.fetchone() is not None


def record_failed_login(user_id: int, *, max_attempts: int, lockout_minutes: int) -> bool:
    """Count a failed password; lock the account when the limit is reached. Returns True if now locked."""
    with transaction() as cursor:
        cursor.execute("""
            UPDATE app_user SET failed_login_count = failed_login_count + 1,
                   locked_until = CASE WHEN failed_login_count + 1 >= %s
                                       THEN now() + make_interval(mins => %s) ELSE locked_until END
            WHERE user_id = %s RETURNING locked_until IS NOT NULL AND locked_until > now()
        """, (max_attempts, lockout_minutes, user_id))
        row = cursor.fetchone()
    return bool(row and row[0])


def record_successful_login(user_id: int) -> None:
    with transaction() as cursor:
        cursor.execute("UPDATE app_user SET failed_login_count = 0, locked_until = NULL, last_login_at = now() "
                       "WHERE user_id = %s", (user_id,))


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

def create_session(token_hash: str, user_id: int, *, hours: int, ip_address: str | None,
                   user_agent: str | None) -> datetime:
    expires = datetime.now(timezone.utc) + timedelta(hours=hours)
    with transaction() as cursor:
        cursor.execute("DELETE FROM app_session WHERE expires_at < now()")
        cursor.execute("""
            INSERT INTO app_session (token_hash, user_id, expires_at, ip_address, user_agent)
            VALUES (%s, %s, %s, %s, %s)
        """, (token_hash, user_id, expires, ip_address, (user_agent or "")[:300]))
    return expires


def session_user(token_hash: str) -> dict | None:
    """Return the active user for a live session and refresh last_seen_at (at most once a minute)."""
    with transaction() as cursor:
        cursor.execute(f"""
            SELECT {USER_COLUMNS} FROM app_session s
            JOIN app_user u USING (user_id) JOIN app_role r ON r.role_code = u.role_code
            WHERE s.token_hash = %s AND s.expires_at > now() AND u.is_active
        """, (token_hash,))
        user = _user(cursor.fetchone())
        if user:
            cursor.execute("UPDATE app_session SET last_seen_at = now() "
                           "WHERE token_hash = %s AND last_seen_at < now() - interval '1 minute'", (token_hash,))
    return user


def delete_session(token_hash: str) -> None:
    with transaction() as cursor:
        cursor.execute("DELETE FROM app_session WHERE token_hash = %s", (token_hash,))


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------

def audit(action: str, *, actor_user_id: int | None = None, target_user_id: int | None = None,
          email: str | None = None, ip_address: str | None = None, detail: dict | None = None) -> None:
    with transaction() as cursor:
        cursor.execute("""
            INSERT INTO auth_audit (action, actor_user_id, target_user_id, email, ip_address, detail)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (action, actor_user_id, target_user_id, (email or "")[:200] or None, ip_address,
              json.dumps(detail or {})))


def recent_audit(limit: int = 50) -> list[dict]:
    with transaction() as cursor:
        cursor.execute("""
            SELECT a.occurred_at, a.action, actor.email, target.email, a.email, a.ip_address, a.detail
            FROM auth_audit a
            LEFT JOIN app_user actor ON actor.user_id = a.actor_user_id
            LEFT JOIN app_user target ON target.user_id = a.target_user_id
            ORDER BY a.audit_id DESC LIMIT %s
        """, (limit,))
        return [{"occurred_at": occurred, "action": action, "actor": actor, "target": target,
                 "email": typed, "ip_address": ip, "detail": detail}
                for occurred, action, actor, target, typed, ip, detail in cursor.fetchall()]


# ---------------------------------------------------------------------------
# Single sign-on (DWS Hub)
# ---------------------------------------------------------------------------

def get_user_by_sso_subject(subject: str) -> dict | None:
    with transaction() as cursor:
        cursor.execute(f"SELECT {USER_COLUMNS} FROM app_user u JOIN app_role r USING (role_code) "
                       "WHERE u.sso_subject = %s", (subject,))
        return _user(cursor.fetchone())


def link_sso_subject(user_id: int, subject: str) -> None:
    with transaction() as cursor:
        cursor.execute("UPDATE app_user SET sso_subject = %s, updated_at = now() WHERE user_id = %s",
                       (subject, user_id))


def save_sso_request(state_hash: str, *, code_verifier: str, nonce: str, next_path: str,
                     minutes: int = 10) -> None:
    with transaction() as cursor:
        cursor.execute("DELETE FROM sso_login_request WHERE expires_at < now()")
        cursor.execute("""
            INSERT INTO sso_login_request (state_hash, code_verifier, nonce, next_path, expires_at)
            VALUES (%s, %s, %s, %s, now() + make_interval(mins => %s))
        """, (state_hash, code_verifier, nonce, next_path, minutes))


def take_sso_request(state_hash: str) -> dict | None:
    """Return and delete a pending sign-in (single use); None if unknown or expired."""
    with transaction() as cursor:
        cursor.execute("""
            DELETE FROM sso_login_request WHERE state_hash = %s
            RETURNING code_verifier, nonce, next_path, expires_at > now()
        """, (state_hash,))
        row = cursor.fetchone()
    if row is None or not row[3]:
        return None
    return {"code_verifier": row[0], "nonce": row[1], "next_path": row[2]}

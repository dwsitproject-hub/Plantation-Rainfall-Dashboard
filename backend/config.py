"""Central configuration, loaded from the project .env file.

Real environment variables always take precedence over values in .env, so a
server or scheduler can inject secrets without editing the file.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env", override=False)


class ConfigError(RuntimeError):
    pass


def _get(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if value is None or value.strip() in ("", "REPLACE_ME"):
        raise ConfigError(f"{name} is not set. Add it to {PROJECT_ROOT / '.env'} (see .env.example).")
    return value.strip()


def _int(name: str, default: int) -> int:
    return int(_get(name, str(default)))


def _float(name: str, default: float) -> float:
    return float(_get(name, str(default)))


def _path(name: str, default: str) -> Path:
    path = Path(_get(name, default))
    return path if path.is_absolute() else PROJECT_ROOT / path


# --- Folders --------------------------------------------------------------
FRONTEND_DIR = PROJECT_ROOT / "frontend"
WORK_DIR = _path("WORK_DIR", "data/work")          # temporary staging files only
ESTATE_WORKBOOK = _path("ESTATE_WORKBOOK", "backend/reference_data/koordinat_estate_update.xlsx")

# --- Database -------------------------------------------------------------
DATABASE_HOST = _get("DATABASE_HOST", "localhost")
DATABASE_PORT = _int("DATABASE_PORT", 5432)
DATABASE_NAME = _get("DATABASE_NAME", "plantation_rainfall")
# disable | prefer | require | verify-full (use require or stricter for Apsara RDS).
DATABASE_SSLMODE = _get("DATABASE_SSLMODE", "prefer")


def database_user() -> str:
    return _get("DATABASE_USER")


def database_password() -> str:
    return _get("DATABASE_PASSWORD")


# --- Dashboard web server -------------------------------------------------
DASHBOARD_HOST = _get("DASHBOARD_HOST", "0.0.0.0")
DASHBOARD_PORT = _int("DASHBOARD_PORT", 8080)
DASHBOARD_PUBLIC_URL = _get("DASHBOARD_PUBLIC_URL", f"http://localhost:{DASHBOARD_PORT}/")

# --- Sign-in and sessions ---------------------------------------------------
SESSION_HOURS = _int("SESSION_HOURS", 12)
# Set to true when the dashboard is served over HTTPS (e.g. behind a TLS reverse proxy).
SESSION_COOKIE_SECURE = _get("SESSION_COOKIE_SECURE", "false").lower() in ("1", "true", "yes")
LOGIN_MAX_FAILED_ATTEMPTS = _int("LOGIN_MAX_FAILED_ATTEMPTS", 5)
LOGIN_LOCKOUT_MINUTES = _int("LOGIN_LOCKOUT_MINUTES", 15)
PASSWORD_MIN_LENGTH = _int("PASSWORD_MIN_LENGTH", 12)
# true only when the backend is reachable solely through the frontend proxy, which
# then reports each visitor's address in X-Real-IP (used for audit and sign-in throttling).
TRUST_PROXY_HEADERS = _get("TRUST_PROXY_HEADERS", "false").lower() in ("1", "true", "yes")
# Email + password sign-in on the dashboard itself (keep on for a break-glass admin).
LOCAL_LOGIN_ENABLED = _get("LOCAL_LOGIN_ENABLED", "true").lower() in ("1", "true", "yes")

# --- Single sign-on: DWS Hub (OIDC authorization code + PKCE) --------------
SSO_ENABLED = _get("SSO_ENABLED", "false").lower() in ("1", "true", "yes")
SSO_LABEL = _get("SSO_LABEL", "Sign in with DWS Hub")
SSO_SCOPES = _get("SSO_SCOPES", "openid profile email")
# Role given to a Hub user who has no dashboard account yet; empty = only pre-registered users.
SSO_AUTO_PROVISION_ROLE = os.getenv("SSO_AUTO_PROVISION_ROLE", "").strip()


def sso() -> dict[str, str]:
    """Hub settings, read only when SSO is enabled."""
    return {
        # Server-side address of the Hub discovery document (reachable from the backend).
        "discovery_url": _get("SSO_DISCOVERY_URL"),
        "client_id": _get("SSO_CLIENT_ID"),
        "client_secret": os.getenv("SSO_CLIENT_SECRET", "").strip(),
        # Callback registered in the Hub: <frontend address>/api/sso/callback.
        "redirect_uri": _get("SSO_REDIRECT_URI"),
        # Optional: browser-facing authorize URL when it differs from the discovery document.
        "authorization_url": os.getenv("SSO_AUTHORIZATION_URL", "").strip(),
    }

# --- Open-Meteo -----------------------------------------------------------
OPEN_METEO_ARCHIVE_URL = _get("OPEN_METEO_ARCHIVE_URL", "https://archive-api.open-meteo.com/v1/archive")
OPEN_METEO_FORECAST_URL = _get("OPEN_METEO_FORECAST_URL", "https://api.open-meteo.com/v1/forecast")
OPEN_METEO_ECMWF_URL = _get("OPEN_METEO_ECMWF_URL", "https://api.open-meteo.com/v1/ecmwf")
HTTP_USER_AGENT = _get("HTTP_USER_AGENT", "plantation-rainfall-dashboard/2.0")

HISTORICAL_RAINFALL_START_DATE = _get("HISTORICAL_RAINFALL_START_DATE", "2000-01-01")
SOIL_MOISTURE_START_DATE = _get("SOIL_MOISTURE_START_DATE", "2017-01-01")
ALL_REGIONS_FORECAST_DAYS = _int("ALL_REGIONS_FORECAST_DAYS", 14)
FORECAST_SNAPSHOT_RETENTION_RUNS = _int("FORECAST_SNAPSHOT_RETENTION_RUNS", 90)
PLANTATION_FORECAST_DAYS = _int("PLANTATION_FORECAST_DAYS", 14)
PLANTATION_PAST_DAYS = _int("PLANTATION_PAST_DAYS", 92)

# --- SAP / Syncera --------------------------------------------------------
SAP_WORKERS = _int("SAP_WORKERS", 4)
SAP_REQUEST_DELAY_SECONDS = _float("SAP_REQUEST_DELAY_SECONDS", 0)


def syncera(environment: str) -> dict[str, str]:
    """Connection settings for the dev (historical) or prod (current) Syncera API."""
    prefix = f"SYNCERA_{environment.upper()}"
    return {
        "base_url": _get(f"{prefix}_BASE_URL").rstrip("/"),
        "client_id": _get(f"{prefix}_CLIENT_ID"),
        "client_secret": _get(f"{prefix}_CLIENT_SECRET"),
        "default_estate": _get(f"{prefix}_DEFAULT_ESTATE", "3A"),
    }


def sap_estates() -> list[tuple[str, str]]:
    """Company/estate pairs pulled from SAP, from SAP_ESTATES=CR:3A,TH:3J,..."""
    pairs = []
    for item in _get("SAP_ESTATES").split(","):
        company, _, estate = item.strip().partition(":")
        if not company or not estate:
            raise ConfigError(f"Invalid SAP_ESTATES entry {item!r}; use COMPANY:ESTATE")
        pair = (company.strip().upper(), estate.strip().upper())
        if pair not in pairs:
            pairs.append(pair)
    return pairs

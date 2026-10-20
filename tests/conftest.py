"""Shared fixtures: a throwaway PostgreSQL 18 cluster and network isolation.

The database tests create a temporary cluster with initdb (trust auth) on port
55432, so they never touch the real dashboard database. Set PG_BIN to the
PostgreSQL bin folder if initdb is not on PATH or in the default location.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
from pathlib import Path

import pytest

TEST_PORT = 55432
TEST_DATABASE = "rainfall_test"
BLOCKED_URL = "http://127.0.0.1:9/blocked-in-tests"

# Real environment variables take precedence over .env (see backend/config.py),
# so these are set before any backend module is imported. Tests can therefore
# never reach the real database, Open-Meteo or the Syncera SAP API.
os.environ.update({
    "DATABASE_HOST": "127.0.0.1",
    "DATABASE_PORT": str(TEST_PORT),
    "DATABASE_NAME": TEST_DATABASE,
    "DATABASE_USER": "postgres",
    "DATABASE_PASSWORD": "unused-trust-auth",
    "OPEN_METEO_ARCHIVE_URL": BLOCKED_URL,
    "OPEN_METEO_FORECAST_URL": BLOCKED_URL,
    "OPEN_METEO_ECMWF_URL": BLOCKED_URL,
    "SYNCERA_DEV_BASE_URL": BLOCKED_URL,
    "SYNCERA_DEV_CLIENT_ID": "test",
    "SYNCERA_DEV_CLIENT_SECRET": "test",
    "SYNCERA_PROD_BASE_URL": BLOCKED_URL,
    "SYNCERA_PROD_CLIENT_ID": "test",
    "SYNCERA_PROD_CLIENT_SECRET": "test",
})


def _postgres_bin() -> Path | None:
    candidates = [os.environ.get("PG_BIN"), r"C:\Program Files\PostgreSQL\18\bin"]
    found = shutil.which("initdb")
    if found:
        candidates.append(str(Path(found).parent))
    for candidate in candidates:
        if candidate and (Path(candidate) / ("initdb.exe" if os.name == "nt" else "initdb")).exists():
            return Path(candidate)
    return None


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", port)) == 0


@pytest.fixture(scope="session")
def postgres_server(tmp_path_factory):
    """Start a throwaway PostgreSQL cluster for the test session."""
    bin_dir = _postgres_bin()
    if bin_dir is None:
        pytest.skip("PostgreSQL initdb not found; set PG_BIN to the PostgreSQL 18 bin folder")
    if _port_in_use(TEST_PORT):
        pytest.fail(f"Port {TEST_PORT} is already in use; stop the leftover test server first")

    root = tmp_path_factory.mktemp("pg")
    data_dir, log_file = root / "data", root / "postgres.log"
    subprocess.run(
        [str(bin_dir / "initdb"), "-D", str(data_dir), "-U", "postgres", "-A", "trust",
         "-E", "UTF8", "--locale=C"],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    pg_ctl = str(bin_dir / "pg_ctl")
    started = subprocess.run(
        [pg_ctl, "-D", str(data_dir), "-l", str(log_file), "-w", "-t", "60",
         "-o", f"-p {TEST_PORT} -c listen_addresses=127.0.0.1", "start"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    if started.returncode:
        pytest.fail(f"Test PostgreSQL did not start:\n{log_file.read_text(errors='replace')}")

    import psycopg2

    admin = psycopg2.connect(host="127.0.0.1", port=TEST_PORT, user="postgres", dbname="postgres")
    admin.autocommit = True
    with admin.cursor() as cursor:
        cursor.execute("SHOW server_version_num")
        version = int(cursor.fetchone()[0])
        cursor.execute(f"CREATE DATABASE {TEST_DATABASE} ENCODING 'UTF8' TEMPLATE template0")
    admin.close()
    try:
        yield {"version": version, "data_dir": data_dir}
    finally:
        subprocess.run([pg_ctl, "-D", str(data_dir), "-m", "fast", "-w", "stop"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


@pytest.fixture(scope="session")
def schema(postgres_server):
    from backend.database import init_db

    init_db.main()
    return postgres_server


@pytest.fixture
def db(schema):
    """An empty, freshly seeded database for each test."""
    from backend.database import init_db
    from backend.database.db import transaction

    with transaction() as cursor:
        cursor.execute("""
            SELECT string_agg(format('%I.%I', schemaname, tablename), ', ')
            FROM pg_tables WHERE schemaname = 'rainfall'
        """)
        cursor.execute(f"TRUNCATE {cursor.fetchone()[0]} RESTART IDENTITY CASCADE")
    init_db.seed_weather_regions()
    return schema

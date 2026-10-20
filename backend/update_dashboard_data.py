"""Refresh Open-Meteo and SAP data used by the integrated dashboard.

    python -m backend.update_dashboard_data

Only one refresh runs at a time: a PostgreSQL advisory lock is held for the
whole run, and a second refresh exits at once with EXIT_ALREADY_RUNNING.
"""

from __future__ import annotations

import subprocess
import sys

import psycopg2

from backend import config
from backend.database import repository


EXIT_ALREADY_RUNNING = 75  # EX_TEMPFAIL: retry once the running refresh has finished


def run(label: str, module: str, *arguments: str) -> None:
    print(f"\n{'=' * 68}\n{label}\n{'=' * 68}", flush=True)
    subprocess.run([sys.executable, "-m", module, *arguments], cwd=config.PROJECT_ROOT, check=True)


def refresh() -> int:
    try:
        run("0/2 Checking database schema", "backend.database.init_db")
        run("1/2 Pulling All Regions Open-Meteo data", "backend.all_regions.update_all_regions_data")
        run(
            "2/2 Pulling SAP and plantation Open-Meteo data",
            "backend.plantation.update_all_rainfall_dashboard",
            "--workers", str(config.SAP_WORKERS),
        )
        print("\nAll dashboard data was refreshed successfully.")
        return 0
    except subprocess.CalledProcessError as error:
        print(f"\nUpdate stopped at a failed step (exit code {error.returncode}).")
        return error.returncode or 1


def main() -> int:
    try:
        with repository.refresh_lock():
            return refresh()
    except repository.RefreshAlreadyRunning as error:
        print(f"\n{error} Wait for it to finish, then try again.", file=sys.stderr)
        return EXIT_ALREADY_RUNNING
    except psycopg2.OperationalError as error:
        print(f"\nCould not connect to PostgreSQL to take the refresh lock: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

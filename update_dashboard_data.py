"""Refresh Open-Meteo and SAP data used by the integrated local dashboard."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from sap_credentials import SYNCERA_DEV_CLIENT_SECRET, SYNCERA_PROD_CLIENT_SECRET


ROOT = Path(__file__).resolve().parent


def run(label: str, script: Path, *arguments: str) -> None:
    print(f"\n{'=' * 68}\n{label}\n{'=' * 68}", flush=True)
    subprocess.run([sys.executable, str(script), *arguments], cwd=ROOT, check=True)


def main() -> int:
    os.environ["SYNCERA_DEV_CLIENT_SECRET"] = SYNCERA_DEV_CLIENT_SECRET
    os.environ["SYNCERA_PROD_CLIENT_SECRET"] = SYNCERA_PROD_CLIENT_SECRET
    try:
        run("1/2 Pulling All Regions Open-Meteo data", ROOT / "update_all_regions_data.py")
        run(
            "2/2 Pulling SAP and plantation Open-Meteo data",
            ROOT / "SAP_rainfall" / "update_all_rainfall_dashboard.py",
            "--workers", "4",
        )
        print("\nAll local dashboard data was refreshed successfully.")
        return 0
    except subprocess.CalledProcessError as error:
        print(f"\nUpdate stopped at a failed step (exit code {error.returncode}).")
        return error.returncode or 1


if __name__ == "__main__":
    raise SystemExit(main())

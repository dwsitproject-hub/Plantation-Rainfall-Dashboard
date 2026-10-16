#!/usr/bin/env python3
"""Safely refresh all SAP rainfall data, Open-Meteo forecasts, and dashboard JSON."""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent
WEATHER_ROOT = ROOT.parent
DASHBOARD = WEATHER_ROOT / "rainfall-dashboard"
DATA_DIR = DASHBOARD / "dist" / "data"
DEFAULT_WORKBOOK = WEATHER_ROOT / "koordinat_estate_update.xlsx"
SECRET_NAMES = ("SYNCERA_DEV_CLIENT_SECRET", "SYNCERA_PROD_CLIENT_SECRET")


def ensure_secrets() -> None:
    """Load the package credentials while still allowing environment overrides."""
    try:
        sys.path.insert(0, str(WEATHER_ROOT))
        from sap_credentials import SYNCERA_DEV_CLIENT_SECRET, SYNCERA_PROD_CLIENT_SECRET
    except Exception as exc:
        raise RuntimeError(
            f"Could not load {WEATHER_ROOT / 'sap_credentials.py'}: {exc}"
        ) from exc
    bundled = {
        "SYNCERA_DEV_CLIENT_SECRET": SYNCERA_DEV_CLIENT_SECRET,
        "SYNCERA_PROD_CLIENT_SECRET": SYNCERA_PROD_CLIENT_SECRET,
    }
    for name in SECRET_NAMES:
        value = (os.environ.get(name) or bundled.get(name) or "").strip()
        if not value:
            raise RuntimeError(f"{name} is empty in sap_credentials.py")
        os.environ[name] = value


def validate_setup(workbook: Path) -> None:
    required = (
        ROOT / "pull_sap_rainfall_all_regions.py",
        ROOT / "pull_sap_rainfall.py",
        ROOT / "build_dashboard_data.py",
        DASHBOARD / "update_location_forecast.py",
        DASHBOARD / "dist" / "index.html",
        workbook,
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError("Required file(s) missing:\n- " + "\n- ".join(missing))


def run_step(label: str, command: list[str]) -> None:
    print(f"\n[{label}]")
    subprocess.run(command, cwd=ROOT, check=True)


def validate_csv(path: Path) -> int:
    required = {"company_code", "estate", "posting_date", "total_rainfall_measurement"}
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{path.name} is missing columns: {sorted(missing)}")
        count = sum(1 for _ in reader)
    if count == 0:
        raise ValueError(f"{path.name} contains no rows")
    return count


def latest_actual_date(path: Path) -> date:
    latest = None
    with path.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            value = (row.get("posting_date") or "").strip()
            parsed = None
            for date_format in ("%d.%m.%Y", "%m.%d.%Y", "%Y-%m-%d"):
                try:
                    parsed = datetime.strptime(value, date_format).date()
                    break
                except ValueError:
                    continue
            try:
                rainfall = float((row.get("total_rainfall_measurement") or "0").replace(",", "."))
            except ValueError:
                rainfall = 0
            if parsed is not None and parsed <= date.today() and rainfall > 0 and (latest is None or parsed > latest):
                latest = parsed
    if latest is None:
        raise ValueError(f"{path.name} contains no actual date with portfolio rainfall above zero")
    return latest


def validate_json(path: Path, key: str) -> int:
    payload = json.loads(path.read_text(encoding="utf-8"))
    values = payload.get(key)
    if not isinstance(values, list) or not values:
        raise ValueError(f"{path.name} has no {key}")
    return len(values)


def promote(files: list[tuple[Path, Path]]) -> None:
    prepared = []
    for source, target in files:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".new")
        shutil.copy2(source, temporary)
        prepared.append((temporary, target))
    for temporary, target in prepared:
        os.replace(temporary, target)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Refresh SAP rainfall, the 14-day Open-Meteo forecast, and the local dashboard."
    )
    parser.add_argument("--workbook", type=Path, default=DEFAULT_WORKBOOK)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--delay", type=float, default=0)
    parser.add_argument("--check", action="store_true", help="Validate files and credentials without downloading data.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        ensure_secrets()
    except Exception as exc:
        print(f"Credential setup failed: {exc}", file=sys.stderr)
        return 2
    workbook = args.workbook.resolve()
    try:
        validate_setup(workbook)
    except Exception as exc:
        print(f"Setup check failed: {exc}", file=sys.stderr)
        return 2
    if args.check:
        print("SAP rainfall setup check passed.")
        print(f"Workbook: {workbook}")
        print(f"Dashboard data folder: {DATA_DIR}")
        return 0

    try:
        with tempfile.TemporaryDirectory(prefix="rainfall-refresh-", dir=ROOT) as temporary_name:
            temporary = Path(temporary_name)
            sap_dir = temporary / "sap"
            data_dir = temporary / "dashboard-data"
            sap_dir.mkdir()
            data_dir.mkdir()

            run_step("1/3 Pull SAP rainfall for all plantations", [
                sys.executable,
                str(ROOT / "pull_sap_rainfall_all_regions.py"),
                "--environment", "all",
                "--workers", str(max(1, args.workers)),
                "--delay", str(max(0, args.delay)),
                "--output-dir", str(sap_dir),
            ])

            historical = sap_dir / "SAP rainfall data historical.csv"
            current = sap_dir / "SAP rainfall data current.csv"
            historical_rows = validate_csv(historical)
            current_rows = validate_csv(current)
            current_latest = latest_actual_date(current)
            current_month = date.today().replace(day=1)
            if current_latest < current_month:
                raise ValueError(
                    f"PROD SAP data is stale: latest actual date is {current_latest:%d %b %Y}; "
                    f"expected at least {current_month:%d %b %Y}."
                )

            run_step("2/3 Pull Open-Meteo forecast and rebuild plantation locations", [
                sys.executable,
                str(DASHBOARD / "update_location_forecast.py"),
                "--workbook", str(workbook),
                "--output-dir", str(data_dir),
            ])

            run_step("3/3 Build dashboard rainfall data", [
                sys.executable,
                str(ROOT / "build_dashboard_data.py"),
                "--historical", str(historical),
                "--current", str(current),
                "--output", str(data_dir / "rainfall_daily.json"),
            ])

            rainfall_records = validate_json(data_dir / "rainfall_daily.json", "records")
            locations = validate_json(data_dir / "plantations.json", "locations")
            forecasts = validate_json(data_dir / "forecast_14day.json", "forecasts")
            if forecasts != locations:
                raise ValueError(f"Forecast/location count mismatch: {forecasts} versus {locations}")

            promote([
                (historical, ROOT / "SAP rainfall data historical.csv"),
                (current, ROOT / "SAP rainfall data current.csv"),
                (historical, ROOT / "SAP rainfall data historical - all regions.csv"),
                (current, ROOT / "SAP rainfall data current - all regions.csv"),
                (data_dir / "rainfall_daily.json", DATA_DIR / "rainfall_daily.json"),
                (data_dir / "plantations.json", DATA_DIR / "plantations.json"),
                (data_dir / "forecast_14day.json", DATA_DIR / "forecast_14day.json"),
            ])

        print("\nDashboard refresh completed successfully.")
        print(f"SAP historical rows: {historical_rows:,}")
        print(f"SAP current rows: {current_rows:,}")
        print(f"Latest PROD actual date: {current_latest:%d %b %Y}")
        print(f"Dashboard rainfall records: {rainfall_records:,}")
        print(f"Plantation forecasts: {forecasts:,}")
        print("Refresh the integrated dashboard at http://192.168.1.82:8080/inhouse/ in your browser.")
        return 0
    except subprocess.CalledProcessError as exc:
        print(f"\nRefresh stopped because a step failed (exit code {exc.returncode}).", file=sys.stderr)
        print("The live dashboard files were not replaced.", file=sys.stderr)
        return exc.returncode or 1
    except Exception as exc:
        print(f"\nRefresh stopped: {exc}", file=sys.stderr)
        print("The live dashboard files were not replaced.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

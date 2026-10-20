#!/usr/bin/env python3
"""Safely refresh SAP rainfall, the plantation Open-Meteo forecast and the in-house dashboard data.

Every download is staged in a temporary folder and validated first. Only then is
everything loaded into PostgreSQL in one transaction, so a failed refresh never
replaces the live dashboard data.
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

from backend import config
from backend.database import repository


SECRET_ENVIRONMENTS = ("dev", "prod")


def validate_setup(workbook: Path) -> None:
    for environment in SECRET_ENVIRONMENTS:
        config.syncera(environment)  # raises ConfigError naming the missing .env key
    config.sap_estates()
    if not workbook.is_file():
        raise FileNotFoundError(f"Estate workbook not found: {workbook}")


def run_step(label: str, command: list[str]) -> None:
    print(f"\n[{label}]", flush=True)
    subprocess.run(command, cwd=config.PROJECT_ROOT, check=True)


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Refresh SAP rainfall, the plantation Open-Meteo forecast, and the in-house dashboard."
    )
    parser.add_argument("--workbook", type=Path, default=config.ESTATE_WORKBOOK)
    parser.add_argument("--workers", type=int, default=config.SAP_WORKERS)
    parser.add_argument("--delay", type=float, default=config.SAP_REQUEST_DELAY_SECONDS)
    parser.add_argument("--check", action="store_true", help="Validate settings without downloading data.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    workbook = args.workbook.resolve()
    try:
        validate_setup(workbook)
    except Exception as exc:
        print(f"Setup check failed: {exc}", file=sys.stderr)
        return 2
    if args.check:
        print("SAP rainfall setup check passed.")
        print(f"Workbook: {workbook}")
        print(f"SAP estates configured: {len(config.sap_estates())}")
        return 0

    try:
        config.WORK_DIR.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="rainfall-refresh-", dir=config.WORK_DIR) as temporary_name:
            temporary = Path(temporary_name)
            sap_dir = temporary / "sap"
            data_dir = temporary / "dashboard-data"
            sap_dir.mkdir()
            data_dir.mkdir()

            run_step("1/4 Pull SAP rainfall for all plantations", [
                sys.executable, "-m", "backend.plantation.pull_sap_rainfall_all_regions",
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

            run_step("2/4 Pull Open-Meteo forecast and rebuild plantation locations", [
                sys.executable, "-m", "backend.plantation.update_location_forecast",
                "--workbook", str(workbook),
                "--output-dir", str(data_dir),
            ])

            run_step("3/4 Build dashboard rainfall data", [
                sys.executable, "-m", "backend.plantation.build_dashboard_data",
                "--historical", str(historical),
                "--current", str(current),
                "--output", str(data_dir / "rainfall_daily.json"),
            ])

            rainfall_records = validate_json(data_dir / "rainfall_daily.json", "records")
            locations = validate_json(data_dir / "plantations.json", "locations")
            forecasts = validate_json(data_dir / "forecast_14day.json", "forecasts")
            if forecasts != locations:
                raise ValueError(f"Forecast/location count mismatch: {forecasts} versus {locations}")

            print("\n[4/4 Load validated data into PostgreSQL]", flush=True)
            rainfall = json.loads((data_dir / "rainfall_daily.json").read_text(encoding="utf-8"))
            with repository.tracked_run("plantation", "load") as run:
                repository.load_plantation(
                    run["run_id"],
                    locations=json.loads((data_dir / "plantations.json").read_text(encoding="utf-8"))["locations"],
                    forecast=json.loads((data_dir / "forecast_14day.json").read_text(encoding="utf-8")),
                    workbook_name=workbook.name,
                    sap_rows={"dev": read_csv(historical), "prod": read_csv(current)},
                    rainfall=rainfall,
                )
                run["rows"] = rainfall_records
                run["sap_actual_cutoff"] = rainfall["sap_actual_cutoff"]
                run["message"] = f"SAP dev {historical_rows} rows, prod {current_rows} rows; {forecasts} estates"

        print("\nDashboard refresh completed successfully.")
        print(f"SAP historical rows: {historical_rows:,}")
        print(f"SAP current rows: {current_rows:,}")
        print(f"Latest PROD actual date: {current_latest:%d %b %Y}")
        print(f"Dashboard rainfall records: {rainfall_records:,}")
        print(f"Plantation forecasts: {forecasts:,}")
        print(f"Refresh the in-house dashboard at {config.DASHBOARD_PUBLIC_URL.rstrip('/')}/inhouse/")
        return 0
    except subprocess.CalledProcessError as exc:
        print(f"\nRefresh stopped because a step failed (exit code {exc.returncode}).", file=sys.stderr)
        print("The live dashboard data was not replaced.", file=sys.stderr)
        return exc.returncode or 1
    except Exception as exc:
        print(f"\nRefresh stopped: {exc}", file=sys.stderr)
        print("The live dashboard data was not replaced.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

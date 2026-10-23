#!/usr/bin/env python3
"""Safely refresh SAP rainfall, the plantation Open-Meteo forecast and the in-house dashboard data.

Rainfall comes from the Syncera PROD API only. PROD returns roughly the current
month, so each pull is merged into the PROD rows stored by earlier refreshes:
for every estate and posting date in the new pull the new rows win, and all other
stored days are kept. The dashboard history therefore grows with every refresh.

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


SECRET_ENVIRONMENTS = ("prod",)


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


def sap_day(row: dict) -> tuple[str, str, str]:
    return ((row.get("company_code") or "").strip(), (row.get("estate") or "").strip(),
            (row.get("posting_date") or "").strip())


def merge_sap_rows(stored: list[dict], pulled: list[dict]) -> list[dict]:
    """Stored PROD rows plus a new pull; for each estate and posting date in the pull, the pull wins."""
    refreshed = {sap_day(row) for row in pulled}
    return [row for row in stored if sap_day(row) not in refreshed] + pulled


def write_csv(rows: list[dict], path: Path) -> None:
    fields = list(dict.fromkeys(field for row in rows for field in row))
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, restval="")
        writer.writeheader()
        writer.writerows(rows)


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


def latest_actual_date(path: Path) -> date | None:
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
    return latest


def freshness_warning(pulled_latest: date | None, today: date) -> str | None:
    """A warning when the new pull has no rain reading in the current month.

    The refresh still loads: days stored by earlier refreshes are kept (so the
    dashboard's latest SAP date simply does not advance) and the plantation forecast
    is updated. Stopping here would freeze the forecast as well.
    """
    current_month = today.replace(day=1)
    if pulled_latest is not None and pulled_latest >= current_month:
        return None
    latest = f"{pulled_latest:%d %b %Y}" if pulled_latest else "none"
    return (f"WARNING: SAP PROD returned no rain readings for {current_month:%B %Y} (latest: {latest}). "
            "Loading anyway: rain days stored by earlier refreshes are kept, so the dashboard's latest "
            "SAP date will not move forward until SAP PROD has current data again. "
            "Check with the Syncera team if this lasts more than a day or two.")


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
    parser.add_argument("--requests-per-minute", type=float, default=config.SAP_REQUESTS_PER_MINUTE)
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

            run_step("1/4 Pull SAP PROD rainfall for all plantations", [
                sys.executable, "-m", "backend.plantation.pull_sap_rainfall_all_regions",
                "--environment", "prod",
                "--requests-per-minute", str(args.requests_per_minute),
                "--output-dir", str(sap_dir),
            ])

            current = sap_dir / "SAP rainfall data current.csv"
            current_rows = validate_csv(current)
            current_latest = latest_actual_date(current)
            warning = freshness_warning(current_latest, date.today())
            if warning:
                print(f"\n{warning}\n", flush=True)

            # Keep the days PROD no longer returns (earlier months) from previous refreshes.
            stored_rows = repository.stored_sap_rows("prod")
            sap_rows = merge_sap_rows(stored_rows, read_csv(current))
            all_prod = sap_dir / "SAP rainfall data prod (stored and new).csv"
            write_csv(sap_rows, all_prod)
            print(f"SAP PROD rows: {current_rows:,} pulled, {len(stored_rows):,} stored before, "
                  f"{len(sap_rows):,} after merging", flush=True)

            run_step("2/4 Pull Open-Meteo forecast and rebuild plantation locations", [
                sys.executable, "-m", "backend.plantation.update_location_forecast",
                "--workbook", str(workbook),
                "--output-dir", str(data_dir),
            ])

            run_step("3/4 Build dashboard rainfall data", [
                sys.executable, "-m", "backend.plantation.build_dashboard_data",
                "--input", str(all_prod),
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
                changes = repository.load_plantation(
                    run["run_id"],
                    locations=json.loads((data_dir / "plantations.json").read_text(encoding="utf-8"))["locations"],
                    forecast=json.loads((data_dir / "forecast_14day.json").read_text(encoding="utf-8")),
                    workbook_name=workbook.name,
                    sap_rows={"prod": sap_rows},
                    rainfall=rainfall,
                )
                run["rows"] = rainfall_records
                run["sap_actual_cutoff"] = rainfall["sap_actual_cutoff"]
                run["message"] = (f"SAP prod {current_rows} rows pulled, {len(sap_rows)} stored "
                                  f"(+{changes['sap_added']} / -{changes['sap_removed']}); daily rainfall "
                                  f"+{changes['daily_added']} / -{changes['daily_removed']}; {forecasts} estates"
                                  + ("; no current-month SAP data" if warning else ""))

        print("\nDashboard refresh completed successfully.")
        print(f"SAP PROD rows pulled: {current_rows:,}")
        print(f"SAP PROD rows stored: {len(sap_rows):,} "
              f"({changes['sap_added']:,} new or changed, {changes['sap_removed']:,} removed)")
        print(f"Daily rainfall rows: {changes['daily_added']:,} new or changed, "
              f"{changes['daily_removed']:,} removed")
        print(f"Latest rain reading in this PROD pull: {current_latest:%d %b %Y}" if current_latest
              else "This PROD pull had no rain readings")
        print(f"Dashboard latest SAP date: {rainfall['sap_actual_cutoff']}")
        if warning:
            print(warning)
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

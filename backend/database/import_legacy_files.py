"""One-time import of data files from a pre-PostgreSQL installation.

The forecast-run archive (rainfall_forecast_snapshots.csv) cannot be downloaded
again, so import it before the first refresh on a machine that already ran the
old file-based version:

    python -m backend.database.import_legacy_files "C:\\path\\to\\old\\Plant Rainfall"

Missing files are skipped. Rows for regions that are no longer monitored are ignored.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

from backend.all_regions.rainfall_common import COMBINED_REGION_MEMBERS
from backend.database import repository


WEATHER_FILES = (
    ("rainfall_historical.csv", "rainfall_mm", "historical_actual"),
    ("rainfall_forecast.csv", "rainfall_mm", "forecast"),
    ("temperature_historical.csv", "temperature_mean_c", "historical_actual"),
    ("temperature_forecast.csv", "temperature_mean_c", "forecast"),
    ("soil_moisture_historical.csv", "soil_moisture_m3_m3", "historical_actual"),
    ("soil_moisture_forecast.csv", "soil_moisture_m3_m3", "forecast"),
)


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def iso_date(value: str) -> str:
    value = value.strip()
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        return datetime.strptime(value, "%d/%m/%Y").date().isoformat()


VALUE_FIELDS = ("rainfall_mm", "temperature_mean_c", "soil_moisture_m3_m3")


def fold_combined_regions(rows: list[dict]) -> list[dict]:
    """Merge legacy source regions into their dashboard region, as the dashboard does.

    Older files stored Peninsular Malaysia as Johor, Pahang and Perak. For each
    run/date/type without a direct "Pennisular" row, their average becomes one.
    """
    groups = defaultdict(list)
    for (country, region), members in COMBINED_REGION_MEMBERS.items():
        for row in rows:
            if row.get("country") == country and row.get("region") in members:
                groups[(country, region, row.get("run_date"), row["date"], row.get("data_type"))].append(row)
    member_rows = {id(row) for group in groups.values() for row in group}
    folded = [row for row in rows if id(row) not in member_rows]
    for (country, region, *_), group in groups.items():
        direct = [row for row in group if row["region"] == region]
        if direct:
            folded.extend(direct)
            continue
        merged = {**group[0], "region": region}
        for field in VALUE_FIELDS:
            values = [float(row[field]) for row in group if row.get(field) not in (None, "")]
            if field in group[0]:
                merged[field] = sum(values) / len(values) if values else ""
        merged["latitude"] = f"{sum(float(row['latitude']) for row in group) / len(group):.2f}"
        merged["longitude"] = f"{sum(float(row['longitude']) for row in group) / len(group):.2f}"
        folded.append(merged)
    return folded


def usable(rows: list[dict], known: set[tuple[str, str]]) -> list[dict]:
    dated = []
    for row in rows:
        try:
            dated.append({**row, "date": iso_date(row["date"])})
        except (KeyError, ValueError):
            continue
    return [row for row in fold_combined_regions(dated) if (row.get("country"), row.get("region")) in known]


def import_weather(root: Path, known: set[tuple[str, str]]) -> int:
    total = 0
    for name, metric, data_type in WEATHER_FILES:
        path = root / name
        if not path.is_file():
            print(f"skip  {name} (not found)")
            continue
        rows = usable(read_csv(path), known)
        count = repository.replace_weather(metric, data_type, rows)
        total += count
        print(f"ok    {name}: {count:,} rows")
    return total


def import_snapshots(root: Path, known: set[tuple[str, str]], retention: int) -> int:
    path = root / "rainfall_forecast_snapshots.csv"
    if not path.is_file():
        print("skip  rainfall_forecast_snapshots.csv (not found)")
        return 0
    runs: dict[str, list[dict]] = defaultdict(list)
    for row in usable(read_csv(path), known):
        runs[row["run_date"]].append(row)
    total = sum(repository.save_snapshot(run_date, rows, retention_runs=retention)
                for run_date, rows in sorted(runs.items()))
    print(f"ok    rainfall_forecast_snapshots.csv: {len(runs)} runs, {total:,} rows")
    return total


def import_plantation(root: Path) -> int:
    sap_dir = root / "SAP_rainfall"
    data_dir = root / "rainfall-dashboard" / "dist" / "data"
    files = {
        "dev": sap_dir / "SAP rainfall data historical.csv",
        "prod": sap_dir / "SAP rainfall data current.csv",
        "rainfall": data_dir / "rainfall_daily.json",
        "plantations": data_dir / "plantations.json",
        "forecast": data_dir / "forecast_14day.json",
    }
    missing = [str(path) for path in files.values() if not path.is_file()]
    if missing:
        print("skip  in-house plantation data (missing: " + "; ".join(missing) + ")")
        return 0
    rainfall = json.loads(files["rainfall"].read_text(encoding="utf-8"))
    plantations = json.loads(files["plantations"].read_text(encoding="utf-8"))
    with repository.tracked_run("plantation", "load") as run:
        repository.load_plantation(
            run["run_id"],
            locations=plantations["locations"],
            forecast=json.loads(files["forecast"].read_text(encoding="utf-8")),
            workbook_name=plantations.get("source", "koordinat_estate_update.xlsx"),
            sap_rows={"dev": read_csv(files["dev"]), "prod": read_csv(files["prod"])},
            rainfall=rainfall,
        )
        run["rows"] = len(rainfall["records"])
        run["sap_actual_cutoff"] = rainfall.get("sap_actual_cutoff")
        run["message"] = f"Imported legacy files from {root}"
    print(f"ok    in-house plantation data: {len(rainfall['records']):,} rainfall records")
    return len(rainfall["records"])


def main() -> int:
    from backend import config

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("legacy_root", type=Path, help="Folder of the old installation")
    parser.add_argument("--only", nargs="+", choices=("weather", "snapshots", "plantation"),
                        default=["weather", "snapshots", "plantation"],
                        help="Import only these parts (for example --only snapshots when the "
                             "database already has fresher weather data).")
    args = parser.parse_args()
    root = args.legacy_root.resolve()
    if not root.is_dir():
        raise SystemExit(f"Not a folder: {root}")
    known = repository.known_regions()
    if "weather" in args.only or "snapshots" in args.only:
        with repository.tracked_run("all_regions", "legacy_import") as run:
            run["rows"] = (import_weather(root, known) if "weather" in args.only else 0) + (
                import_snapshots(root, known, config.FORECAST_SNAPSHOT_RETENTION_RUNS)
                if "snapshots" in args.only else 0)
            run["message"] = f"Imported {', '.join(args.only)} from {root}"
    if "plantation" in args.only:
        import_plantation(root)
    print("Legacy import finished.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

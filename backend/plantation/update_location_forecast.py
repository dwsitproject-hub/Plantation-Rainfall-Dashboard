#!/usr/bin/env python3
"""Build plantation location metadata and refresh the 14-day Open-Meteo forecast."""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from backend import config
from backend.all_regions.rainfall_common import fetch_json, response_items


DATA_DIR = config.WORK_DIR / "plantation"

# Geographic province mapping for the estate IDs in the location master.
# The workbook's "Region" column is an internal operating-region grouping, so
# it is retained separately as business_region.
PROVINCE_BY_ESTATE = {
    **{estate: "Riau" for estate in [
        "TH3A", "TH3B", "TH3C", "TH3D", "TH3E", "TH3F", "TH3G", "TH3H", "TH3I", "TH3J",
        "TH3K", "TH3L", "TH3M", "TH3N", "TH3O", "TH3P", "TH3Q", "TH3R", "TH3S", "TH3T",
        "JJ3A", "JJ3B", "JJ9A", "CR3A",
    ]},
    **{estate: "West Sumatra" for estate in ["PN3C", "KU3A"]},
    **{estate: "Jambi" for estate in ["BU3A", "BU3B", "BU3C", "BU3D", "BU9A", "IU3A"]},
    **{estate: "South Sumatra" for estate in ["SI3A", "SI3B", "KB9A"]},
    **{estate: "West Kalimantan" for estate in [
        "PT3A", "PT3B", "SU3A", "SU3B", "WW3A", "WW3B", "IP3A", "PN3A", "PN3B", "PD3A",
        "PD3B", "SA3A", "CM3A", "CM3B", "NJ3A", "BS3A", "GN3A", "GN3B", "GN3C",
    ]},
    **{estate: "East Kalimantan" for estate in ["AJ3A", "TS3A", "TS3B"]},
    "WS3A": "West Sulawesi",
    **{estate: "South Papua" for estate in ["AC3A", "AC3B", "AM3A", "AM3B"]},
}


def clean(value):
    return " ".join(str(value).strip().split())


def load_locations(workbook: Path):
    frame = pd.read_excel(workbook, sheet_name=0)
    required = {"Region", "Estate", "Nama Kebun", "CentroidX", "CentroidY"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"Workbook is missing columns: {sorted(missing)}")
    locations = []
    for _, row in frame.iterrows():
        location_id = clean(row["Estate"]).upper().replace(" ", "")
        if location_id not in PROVINCE_BY_ESTATE:
            raise ValueError(f"No Indonesian province mapping configured for {location_id}")
        locations.append({
            "id": location_id,
            "company": location_id[:2],
            "estate": location_id[2:],
            "name": clean(row["Nama Kebun"]),
            "region": PROVINCE_BY_ESTATE[location_id],
            "business_region": clean(row["Region"]),
            "latitude": round(float(row["CentroidY"]), 6),
            "longitude": round(float(row["CentroidX"]), 6),
        })
    if len({item["id"] for item in locations}) != len(locations):
        raise ValueError("Workbook contains duplicate estate IDs")
    return locations


def fetch_forecasts(locations):
    parameters = {
        "latitude": ",".join(str(item["latitude"]) for item in locations),
        "longitude": ",".join(str(item["longitude"]) for item in locations),
        "daily": "precipitation_sum",
        "forecast_days": config.PLANTATION_FORECAST_DAYS,
        "past_days": config.PLANTATION_PAST_DAYS,
        "timezone": "auto",
        "precipitation_unit": "mm",
    }
    # Retries temporary Open-Meteo/network failures, like every All Regions pull.
    payload = fetch_json(config.OPEN_METEO_FORECAST_URL, parameters, timeout=120)
    responses = response_items(payload)
    if len(responses) != len(locations):
        raise ValueError(f"Expected {len(locations)} forecast responses, received {len(responses)}")
    forecasts = []
    for location, result in zip(locations, responses):
        daily = result.get("daily") or {}
        dates = daily.get("time") or []
        rainfall = daily.get("precipitation_sum") or []
        if len(dates) != len(rainfall):
            raise ValueError(f"Forecast date/value mismatch for {location['id']}")
        today = date.today().isoformat()
        actual_pairs = [(day, value) for day, value in zip(dates, rainfall) if day < today]
        forecast_pairs = [(day, value) for day, value in zip(dates, rainfall) if day >= today]
        forecasts.append({
            "id": location["id"],
            "latitude": result.get("latitude", location["latitude"]),
            "longitude": result.get("longitude", location["longitude"]),
            "timezone": result.get("timezone"),
            "actual_dates": [item[0] for item in actual_pairs],
            "actual_rainfall_mm": [item[1] for item in actual_pairs],
            "forecast_dates": [item[0] for item in forecast_pairs],
            "forecast_rainfall_mm": [item[1] for item in forecast_pairs],
            "dates": [item[0] for item in forecast_pairs],
            "rainfall_mm": [item[1] for item in forecast_pairs],
        })
    return forecasts


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workbook", type=Path, default=config.ESTATE_WORKBOOK)
    parser.add_argument("--output-dir", type=Path, default=DATA_DIR)
    args = parser.parse_args()
    locations = load_locations(args.workbook)
    forecasts = fetch_forecasts(locations)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "plantations.json").write_text(
        json.dumps({"source": args.workbook.name, "locations": locations}, separators=(",", ":")),
        encoding="utf-8",
    )
    (args.output_dir / "forecast_14day.json").write_text(
        json.dumps({
            "source": "Open-Meteo Forecast API",
            "source_url": "https://open-meteo.com/en/docs",
            "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "as_of_date": date.today().isoformat(),
            "past_days": config.PLANTATION_PAST_DAYS,
            "forecast_days": config.PLANTATION_FORECAST_DAYS,
            "forecasts": forecasts,
        }, separators=(",", ":")),
        encoding="utf-8",
    )
    print(json.dumps({
        "locations": len(locations),
        "regions": sorted({item["region"] for item in locations}),
        "forecast_start": forecasts[0]["dates"][0] if forecasts and forecasts[0]["dates"] else None,
        "forecast_end": forecasts[0]["dates"][-1] if forecasts and forecasts[0]["dates"] else None,
    }))


if __name__ == "__main__":
    main()

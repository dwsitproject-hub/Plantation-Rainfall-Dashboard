"""Shared configuration and helpers for combined rainfall/temperature pulls."""

from __future__ import annotations

import csv
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


REGIONS = [
    ("Malaysia", "Pennisular", 3.50, 102.42),
    ("Malaysia", "Sabah", 5.43, 117.03),
    ("Malaysia", "Sarawak", 2.50, 112.95),
    ("Indonesia", "North Sumatra", 2.12, 99.55),
    ("Indonesia", "Jambi", -1.61, 103.61),
    ("Indonesia", "Riau", 0.50, 101.55),
    ("Indonesia", "South Sumatra", -3.13, 104.09),
    ("Indonesia", "Central Kalimantan", -1.50, 113.29),
    ("Indonesia", "East Kalimantan", 0.79, 116.24),
    ("Indonesia", "West Kalimantan", -0.13, 111.10),
    ("Indonesia", "West Sumatra", -0.95, 100.55),
    ("Indonesia", "South Kalimantan", -2.95, 115.15),
    ("Indonesia", "Sulawesi", -2.00, 120.00),
    ("Indonesia", "Papua", -4.27, 138.08),
    ("Thailand", "Surat Thani", 9.14, 99.33),
    ("Thailand", "Krabi", 8.09, 98.91),
    ("Thailand", "Chumphon", 10.49, 99.18),
    ("Thailand", "Nakhon Si Thammarat", 8.43, 99.96),
]

# Temperature uses separate lowland reference coordinates so each regional
# series represents broadly comparable plantation-zone elevation. Rainfall
# continues to use REGIONS above without any coordinate changes.
TEMPERATURE_REGIONS = [
    ("Malaysia", "Pennisular", 3.11, 102.70),
    ("Malaysia", "Sabah", 5.84, 118.12),
    ("Malaysia", "Sarawak", 2.29, 111.83),
    ("Indonesia", "North Sumatra", 3.59, 98.67),
    ("Indonesia", "Jambi", -1.60, 103.61),
    ("Indonesia", "Riau", 0.51, 101.45),
    ("Indonesia", "South Sumatra", -2.99, 104.76),
    ("Indonesia", "Central Kalimantan", -2.21, 113.92),
    ("Indonesia", "East Kalimantan", -0.50, 117.15),
    ("Indonesia", "West Kalimantan", -0.03, 109.34),
    ("Indonesia", "West Sumatra", -0.95, 100.55),
    ("Indonesia", "South Kalimantan", -3.32, 114.59),
    ("Indonesia", "Sulawesi", -2.68, 118.89),
    ("Indonesia", "Papua", -2.58, 140.70),
    ("Thailand", "Surat Thani", 9.14, 99.33),
    ("Thailand", "Krabi", 8.09, 98.91),
    ("Thailand", "Chumphon", 10.49, 99.18),
    ("Thailand", "Nakhon Si Thammarat", 8.43, 99.96),
]

# Country summary series are plantation-area baskets, not whole-country spatial
# averages. Keep the internal ``Overall <country>`` keys stable for compatibility
# with existing CSVs/charts, while exposing the clearer labels below.
MAJOR_PLANTATION_REGIONS = {
    "Indonesia": {
        "Riau", "North Sumatra", "South Sumatra", "Jambi", "West Sumatra",
        "Central Kalimantan", "West Kalimantan", "East Kalimantan", "South Kalimantan",
    },
    "Malaysia": {"Pennisular", "Sabah", "Sarawak"},
    "Thailand": {"Surat Thani", "Krabi", "Chumphon", "Nakhon Si Thammarat"},
}

OVERALL_LABELS = {
    "Indonesia": "Indo Major Plantation Area",
    "Malaysia": "Msia Major Plantation Area",
    "Thailand": "Thailand Major Plantation Area",
}

COMBINED_REGION_MEMBERS = {
    ("Malaysia", "Pennisular"): {"Johor", "Pahang", "Perak", "Pennisular"},
}


def region_members(country: str, region: str) -> set[str]:
    """Return source-region names represented by one dashboard region."""
    return COMBINED_REGION_MEMBERS.get((country, region), {region})


def is_major_plantation_region(country: str, region: str) -> bool:
    return region in MAJOR_PLANTATION_REGIONS.get(country, set())

OUTPUT_COLUMNS = [
    "country",
    "region",
    "latitude",
    "longitude",
    "date",
    "rainfall_mm",
    "data_type",
    "retrieved_at_utc",
    "source",
]

TEMPERATURE_OUTPUT_COLUMNS = [
    "country",
    "region",
    "latitude",
    "longitude",
    "date",
    "temperature_mean_c",
    "data_type",
    "retrieved_at_utc",
    "source",
]

SCRIPT_DIR = Path(__file__).resolve().parent
HISTORICAL_CSV = SCRIPT_DIR / "rainfall_historical.csv"
FORECAST_CSV = SCRIPT_DIR / "rainfall_forecast.csv"
COMBINED_CSV = SCRIPT_DIR / "rainfall_combined.csv"
TEMPERATURE_HISTORICAL_CSV = SCRIPT_DIR / "temperature_historical.csv"
TEMPERATURE_FORECAST_CSV = SCRIPT_DIR / "temperature_forecast.csv"
TEMPERATURE_COMBINED_CSV = SCRIPT_DIR / "temperature_combined.csv"
USER_AGENT = "palm-oil-rainfall-pipeline/1.0"


def fetch_json(url: str, params: dict[str, Any], attempts: int = 4) -> Any:
    """Fetch JSON with bounded retries for temporary API/network failures."""
    request_url = f"{url}?{urlencode(params)}"
    for attempt in range(1, attempts + 1):
        try:
            request = Request(request_url, headers={"User-Agent": USER_AGENT})
            with urlopen(request, timeout=90) as response:
                return json.load(response)
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            if exc.code < 500 and exc.code != 429:
                raise RuntimeError(f"Open-Meteo returned HTTP {exc.code}: {detail}") from exc
            last_error: Exception = exc
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc

        if attempt < attempts:
            delay = 10 * attempt if isinstance(last_error, HTTPError) and last_error.code == 429 else 2 ** (attempt - 1)
            time.sleep(delay)
    raise RuntimeError(f"Open-Meteo request failed after {attempts} attempts: {last_error}")


def response_items(payload: Any) -> list[dict[str, Any]]:
    """Normalize Open-Meteo single- and multiple-location responses."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and payload.get("error"):
        raise RuntimeError(f"Open-Meteo error: {payload.get('reason', 'unknown error')}")
    if isinstance(payload, dict):
        return [payload]
    raise RuntimeError("Unexpected response format from Open-Meteo")


def make_metric_rows(
    payload: Any,
    regions: list[tuple[str, str, float, float]],
    api_field: str,
    output_field: str,
    data_type: str,
    source: str,
) -> list[dict[str, Any]]:
    """Convert one Open-Meteo daily variable into normalized CSV rows."""
    items = response_items(payload)
    if len(items) != len(regions):
        raise RuntimeError(f"Expected {len(regions)} locations, received {len(items)}")

    retrieved_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    rows: list[dict[str, Any]] = []
    for (country, region, latitude, longitude), item in zip(regions, items):
        daily = item.get("daily", {})
        dates = daily.get("time", [])
        values = daily.get(api_field, [])
        if len(dates) != len(values):
            raise RuntimeError(f"Mismatched daily arrays returned for {country} / {region}")
        for day, value in zip(dates, values):
            rows.append({
                "country": country, "region": region,
                "latitude": f"{latitude:.2f}", "longitude": f"{longitude:.2f}",
                "date": day, output_field: "" if value is None else value,
                "data_type": data_type, "retrieved_at_utc": retrieved_at,
                "source": source,
            })
    return rows


def make_rainfall_rows(payload: Any, data_type: str, source: str) -> list[dict[str, Any]]:
    return make_metric_rows(payload, REGIONS, "precipitation_sum", "rainfall_mm", data_type, source)


def make_temperature_rows(payload: Any, data_type: str, source: str) -> list[dict[str, Any]]:
    return make_metric_rows(
        payload, TEMPERATURE_REGIONS, "temperature_2m_mean", "temperature_mean_c", data_type, source
    )


def read_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def write_rows(
    path: Path, rows: Iterable[dict[str, Any]], columns: list[str] = OUTPUT_COLUMNS
) -> None:
    """Write a CSV atomically so interrupted updates do not corrupt the prior file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temp_path, path)


def rebuild_combined() -> int:
    """Merge history and the latest forecast, de-duplicated by region/date/type."""
    merged: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for row in read_rows(HISTORICAL_CSV) + read_rows(FORECAST_CSV):
        key = (row["country"], row["region"], row["date"], row["data_type"])
        merged[key] = row
    rows = sorted(
        merged.values(),
        key=lambda row: (row["date"], row["country"], row["region"], row["data_type"]),
    )
    write_rows(COMBINED_CSV, rows)
    return len(rows)


def rebuild_temperature_combined() -> int:
    """Merge historical and forecast daily mean-temperature files."""
    merged: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for row in read_rows(TEMPERATURE_HISTORICAL_CSV) + read_rows(TEMPERATURE_FORECAST_CSV):
        key = (row["country"], row["region"], row["date"], row["data_type"])
        merged[key] = row
    rows = sorted(
        merged.values(),
        key=lambda row: (row["date"], row["country"], row["region"], row["data_type"]),
    )
    write_rows(TEMPERATURE_COMBINED_CSV, rows, TEMPERATURE_OUTPUT_COLUMNS)
    return len(rows)

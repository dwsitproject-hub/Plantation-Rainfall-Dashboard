"""Shared helpers for the native ECMWF IFS 28-100 cm soil-moisture pipeline."""

from __future__ import annotations

import csv
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from rainfall_common import REGIONS, response_items


SCRIPT_DIR = Path(__file__).resolve().parent
HISTORICAL_CSV = SCRIPT_DIR / "soil_moisture_historical.csv"
FORECAST_CSV = SCRIPT_DIR / "soil_moisture_forecast.csv"
COMBINED_CSV = SCRIPT_DIR / "soil_moisture_combined.csv"
SOIL_DEPTH_CM = "28-100"
SOIL_VARIABLE = "soil_moisture_28_to_100cm"

COLUMNS = [
    "country", "region", "latitude", "longitude", "date",
    "soil_moisture_m3_m3", "soil_depth_cm", "data_type",
    "soil_method", "retrieved_at_utc", "source",
]


def native_soil_rows(
    payload: Any,
    section: str,
    data_type: str,
    source: str,
    method: str,
) -> list[dict[str, Any]]:
    """Average the native 28-100 cm ECMWF IFS field by local calendar day."""
    items = response_items(payload)
    if len(items) != len(REGIONS):
        raise RuntimeError(f"Expected {len(REGIONS)} locations, received {len(items)}")
    retrieved_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    rows: list[dict[str, Any]] = []
    for (country, region, latitude, longitude), item in zip(REGIONS, items):
        block = item.get(section, {})
        times = block.get("time", [])
        values = block.get(SOIL_VARIABLE, [])
        if len(times) != len(values):
            raise RuntimeError(f"Mismatched {section} arrays for {country} / {region}")
        by_day: dict[str, list[float]] = defaultdict(list)
        for timestamp, raw_value in zip(times, values):
            if raw_value is not None:
                value = float(raw_value)
                by_day[timestamp[:10]].append(value)
        for day in sorted(by_day):
            values = by_day[day]
            rows.append({
                "country": country, "region": region,
                "latitude": f"{latitude:.2f}", "longitude": f"{longitude:.2f}",
                "date": day,
                "soil_moisture_m3_m3": round(sum(values) / len(values), 5),
                "soil_depth_cm": SOIL_DEPTH_CM, "data_type": data_type,
                "soil_method": method, "retrieved_at_utc": retrieved_at,
                "source": source,
            })
    if not rows:
        raise RuntimeError("Open-Meteo returned no usable native 28-100 cm soil-moisture values")
    return rows


def read_csv(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temp, path)


def rebuild_combined() -> int:
    merged: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for row in read_csv(HISTORICAL_CSV) + read_csv(FORECAST_CSV):
        key = (row["country"], row["region"], row["date"], row["data_type"])
        merged[key] = row
    rows = sorted(merged.values(), key=lambda r: (r["date"], r["country"], r["region"], r["data_type"]))
    write_csv(COMBINED_CSV, rows)
    return len(rows)

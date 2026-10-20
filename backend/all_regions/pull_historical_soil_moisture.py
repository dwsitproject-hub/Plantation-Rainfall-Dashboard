"""Download native ECMWF IFS 28-100 cm historical soil moisture.

Only calendar years that are incomplete in the database are downloaded again.
"""

from __future__ import annotations

import argparse
import time
from datetime import date, timedelta

from backend import config
from backend.all_regions.rainfall_common import REGIONS, fetch_json
from backend.all_regions.soil_moisture_common import (
    SOIL_DEPTH_CM, SOIL_METHOD, SOIL_METRIC, SOIL_VARIABLE, native_soil_rows,
)
from backend.database import repository

ARCHIVE_URL = config.OPEN_METEO_ARCHIVE_URL
MODEL = "ecmwf_ifs"
EARLIEST_IFS_DATE = date(2017, 1, 1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Pull historical daily soil moisture.")
    parser.add_argument("--start-date", default=config.SOIL_MOISTURE_START_DATE)
    parser.add_argument("--end-date", default=(date.today() - timedelta(days=1)).isoformat())
    args = parser.parse_args()
    start, end = date.fromisoformat(args.start_date), date.fromisoformat(args.end_date)
    if start < EARLIEST_IFS_DATE:
        raise SystemExit("ECMWF IFS 28-100 cm history is available from 2017-01-01")
    if start > end or end >= date.today():
        raise SystemExit("Use a valid range ending no later than yesterday")

    keys = {
        (row["country"], row["region"], row["date"])
        for row in repository.read_weather(SOIL_METRIC, "historical_actual")
        if start.isoformat() <= row["date"] <= end.isoformat()
        and row.get("soil_depth_cm") == SOIL_DEPTH_CM
        and row.get("soil_method") == SOIL_METHOD
    }
    written = 0
    with repository.tracked_run("all_regions", "historical_soil_moisture") as run:
        chunk_start = start
        while chunk_start <= end:
            chunk_end = min(end, date(chunk_start.year, 12, 31))
            expected = ((chunk_end - chunk_start).days + 1) * len(REGIONS)
            present = sum(chunk_start.isoformat() <= key[2] <= chunk_end.isoformat() for key in keys)
            if present != expected:
                payload = fetch_json(ARCHIVE_URL, {
                    "latitude": ",".join(str(r[2]) for r in REGIONS),
                    "longitude": ",".join(str(r[3]) for r in REGIONS),
                    "start_date": chunk_start.isoformat(), "end_date": chunk_end.isoformat(),
                    "hourly": SOIL_VARIABLE, "models": MODEL, "timezone": "auto",
                })
                chunk_rows = native_soil_rows(
                    payload, "hourly", "historical_actual",
                    "Open-Meteo Historical Weather API / ECMWF IFS",
                    SOIL_METHOD,
                )
                written += repository.replace_weather(
                    SOIL_METRIC, "historical_actual", chunk_rows,
                    date_from=chunk_start.isoformat(), date_to=chunk_end.isoformat())
                print(f"Stored soil moisture {chunk_start} to {chunk_end} ({len(chunk_rows):,} rows)")
                time.sleep(1.5)
            chunk_start = chunk_end + timedelta(days=1)
        run["rows"] = written
    print(f"Historical soil moisture up to date ({written:,} rows refreshed)")


if __name__ == "__main__":
    main()

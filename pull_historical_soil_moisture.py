"""Download native ECMWF IFS 28-100 cm historical soil moisture."""

from __future__ import annotations

import argparse
import time
from datetime import date, timedelta

from rainfall_common import REGIONS, fetch_json
from soil_moisture_common import (
    COMBINED_CSV, HISTORICAL_CSV, SOIL_DEPTH_CM, SOIL_VARIABLE,
    native_soil_rows, read_csv, rebuild_combined, write_csv,
)

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
MODEL = "ecmwf_ifs"
METHOD = "Native ECMWF IFS 28-100 cm; hourly values averaged by local day"
EARLIEST_IFS_DATE = date(2017, 1, 1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Pull historical daily soil moisture.")
    parser.add_argument("--start-date", default="2017-01-01")
    parser.add_argument("--end-date", default=(date.today() - timedelta(days=1)).isoformat())
    args = parser.parse_args()
    start, end = date.fromisoformat(args.start_date), date.fromisoformat(args.end_date)
    if start < EARLIEST_IFS_DATE:
        raise SystemExit("ECMWF IFS 28-100 cm history is available from 2017-01-01")
    if start > end or end >= date.today():
        raise SystemExit("Use a valid range ending no later than yesterday")

    rows = [
        row for row in read_csv(HISTORICAL_CSV)
        if start.isoformat() <= row["date"] <= end.isoformat()
        and row.get("soil_depth_cm") == SOIL_DEPTH_CM
        and row.get("soil_method") == METHOD
    ]
    keys = {(row["country"], row["region"], row["date"]) for row in rows}
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
                METHOD,
            )
            rows = [row for row in rows if not chunk_start.isoformat() <= row["date"] <= chunk_end.isoformat()]
            rows.extend(chunk_rows)
            rows.sort(key=lambda row: (row["date"], row["country"], row["region"]))
            write_csv(HISTORICAL_CSV, rows)
            time.sleep(1.5)
        chunk_start = chunk_end + timedelta(days=1)

    total = rebuild_combined()
    print(f"Wrote {len(rows):,} historical rows to {HISTORICAL_CSV}")
    print(f"Rebuilt {COMBINED_CSV} with {total:,} rows")


if __name__ == "__main__":
    main()

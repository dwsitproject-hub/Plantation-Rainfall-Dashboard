"""Download historical daily rainfall and mean temperature for all regions.

The output label is ``historical_actual`` for convenient reporting. Open-Meteo's
archive is reanalysis/modelled weather, however, and is not a rain-gauge record.
"""

from __future__ import annotations

import argparse
from datetime import date, timedelta

from rainfall_common import (
    COMBINED_CSV,
    HISTORICAL_CSV,
    REGIONS,
    TEMPERATURE_COMBINED_CSV,
    TEMPERATURE_HISTORICAL_CSV,
    TEMPERATURE_OUTPUT_COLUMNS,
    TEMPERATURE_REGIONS,
    fetch_json,
    make_rainfall_rows,
    make_temperature_rows,
    rebuild_combined,
    rebuild_temperature_combined,
    write_rows,
)


ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Pull historical daily rainfall.")
    parser.add_argument("--start-date", default="2000-01-01", help="YYYY-MM-DD (default: 2000-01-01)")
    parser.add_argument(
        "--end-date",
        default=(date.today() - timedelta(days=1)).isoformat(),
        help="YYYY-MM-DD (default: yesterday)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    start_date = date.fromisoformat(args.start_date)
    end_date = date.fromisoformat(args.end_date)
    if start_date > end_date:
        raise SystemExit("--start-date must be on or before --end-date")
    if end_date >= date.today():
        raise SystemExit("Historical end date must be no later than yesterday")

    payload = fetch_json(
        ARCHIVE_URL,
        {
            "latitude": ",".join(str(region[2]) for region in REGIONS),
            "longitude": ",".join(str(region[3]) for region in REGIONS),
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "daily": "precipitation_sum",
            "timezone": "auto",
            "precipitation_unit": "mm",
        },
    )
    temperature_payload = fetch_json(
        ARCHIVE_URL,
        {
            "latitude": ",".join(str(region[2]) for region in TEMPERATURE_REGIONS),
            "longitude": ",".join(str(region[3]) for region in TEMPERATURE_REGIONS),
            "start_date": start_date.isoformat(), "end_date": end_date.isoformat(),
            "daily": "temperature_2m_mean", "timezone": "auto", "temperature_unit": "celsius",
        },
    )
    rows = make_rainfall_rows(payload, "historical_actual", "Open-Meteo Historical Weather API")
    temperature_rows = make_temperature_rows(
        temperature_payload, "historical_actual", "Open-Meteo Historical Weather API"
    )
    write_rows(HISTORICAL_CSV, rows)
    write_rows(TEMPERATURE_HISTORICAL_CSV, temperature_rows, TEMPERATURE_OUTPUT_COLUMNS)
    combined_count = rebuild_combined()
    temperature_combined_count = rebuild_temperature_combined()
    print(f"Wrote {len(rows):,} historical rows to {HISTORICAL_CSV}")
    print(f"Rebuilt {COMBINED_CSV} with {combined_count:,} rows")
    print(f"Wrote {len(temperature_rows):,} historical temperature rows to {TEMPERATURE_HISTORICAL_CSV}")
    print(f"Rebuilt {TEMPERATURE_COMBINED_CSV} with {temperature_combined_count:,} rows")


if __name__ == "__main__":
    main()

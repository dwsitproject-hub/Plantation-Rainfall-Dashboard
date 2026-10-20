"""Download historical daily rainfall and mean temperature for all regions.

The output label is ``historical_actual`` for convenient reporting. Open-Meteo's
archive is reanalysis/modelled weather, however, and is not a rain-gauge record.
"""

from __future__ import annotations

import argparse
from datetime import date, timedelta

from backend import config
from backend.all_regions.rainfall_common import (
    REGIONS,
    TEMPERATURE_REGIONS,
    fetch_json,
    make_rainfall_rows,
    make_temperature_rows,
)
from backend.database import repository


ARCHIVE_URL = config.OPEN_METEO_ARCHIVE_URL


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Pull historical daily rainfall.")
    parser.add_argument("--start-date", default=config.HISTORICAL_RAINFALL_START_DATE,
                        help=f"YYYY-MM-DD (default: {config.HISTORICAL_RAINFALL_START_DATE})")
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
    with repository.tracked_run("all_regions", "historical_rainfall_temperature") as run:
        rain_count = repository.replace_weather(
            "rainfall_mm", "historical_actual", rows,
            date_from=start_date.isoformat(), date_to=end_date.isoformat())
        temperature_count = repository.replace_weather(
            "temperature_mean_c", "historical_actual", temperature_rows,
            date_from=start_date.isoformat(), date_to=end_date.isoformat())
        run["rows"] = rain_count + temperature_count
    print(f"Stored {rain_count:,} historical rainfall rows ({start_date} to {end_date})")
    print(f"Stored {temperature_count:,} historical temperature rows ({start_date} to {end_date})")


if __name__ == "__main__":
    main()

"""Refresh daily rainfall and mean-temperature forecasts through today +14."""

from __future__ import annotations

from datetime import date, timedelta

from rainfall_common import (
    COMBINED_CSV,
    FORECAST_CSV,
    REGIONS,
    TEMPERATURE_COMBINED_CSV,
    TEMPERATURE_FORECAST_CSV,
    TEMPERATURE_OUTPUT_COLUMNS,
    TEMPERATURE_REGIONS,
    fetch_json,
    make_rainfall_rows,
    make_temperature_rows,
    rebuild_combined,
    rebuild_temperature_combined,
    write_rows,
)
from forecast_changes import save_snapshot, seed_current_forecast


FORECAST_URL = "https://api.open-meteo.com/v1/forecast"


def main() -> None:
    seed_current_forecast()
    start_date = date.today()
    end_date = start_date + timedelta(days=14)
    payload = fetch_json(
        FORECAST_URL,
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
        FORECAST_URL,
        {
            "latitude": ",".join(str(region[2]) for region in TEMPERATURE_REGIONS),
            "longitude": ",".join(str(region[3]) for region in TEMPERATURE_REGIONS),
            "start_date": start_date.isoformat(), "end_date": end_date.isoformat(),
            "daily": "temperature_2m_mean", "timezone": "auto", "temperature_unit": "celsius",
        },
    )
    rows = make_rainfall_rows(payload, "forecast", "Open-Meteo Weather Forecast API")
    temperature_rows = make_temperature_rows(
        temperature_payload, "forecast", "Open-Meteo Weather Forecast API"
    )
    write_rows(FORECAST_CSV, rows)
    write_rows(TEMPERATURE_FORECAST_CSV, temperature_rows, TEMPERATURE_OUTPUT_COLUMNS)
    combined_count = rebuild_combined()
    temperature_combined_count = rebuild_temperature_combined()
    snapshot_count = save_snapshot(start_date.isoformat(), rows)
    print(f"Wrote {len(rows):,} forecast rows to {FORECAST_CSV}")
    print(f"Rebuilt {COMBINED_CSV} with {combined_count:,} rows")
    print(f"Wrote {len(temperature_rows):,} forecast temperature rows to {TEMPERATURE_FORECAST_CSV}")
    print(f"Rebuilt {TEMPERATURE_COMBINED_CSV} with {temperature_combined_count:,} rows")
    print(f"Saved {snapshot_count:,} rows for forecast run {start_date.isoformat()}")


if __name__ == "__main__":
    main()

"""Refresh daily rainfall and mean-temperature forecasts through today + N days."""

from __future__ import annotations

from datetime import date, timedelta

from backend import config
from backend.all_regions.forecast_changes import save_snapshot, seed_current_forecast
from backend.all_regions.rainfall_common import (
    REGIONS,
    TEMPERATURE_REGIONS,
    fetch_json,
    make_rainfall_rows,
    make_temperature_rows,
)
from backend.database import repository


FORECAST_URL = config.OPEN_METEO_FORECAST_URL


def main() -> None:
    seed_current_forecast()
    start_date = date.today()
    end_date = start_date + timedelta(days=config.ALL_REGIONS_FORECAST_DAYS)
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
    with repository.tracked_run("all_regions", "rainfall_temperature_forecast") as run:
        rain_count = repository.replace_weather("rainfall_mm", "forecast", rows)
        temperature_count = repository.replace_weather("temperature_mean_c", "forecast", temperature_rows)
        snapshot_count = save_snapshot(start_date.isoformat(), rows)
        run["rows"] = rain_count + temperature_count + snapshot_count
    print(f"Stored {rain_count:,} rainfall forecast rows ({start_date} to {end_date})")
    print(f"Stored {temperature_count:,} temperature forecast rows")
    print(f"Archived {snapshot_count:,} rows for forecast run {start_date.isoformat()}")


if __name__ == "__main__":
    main()

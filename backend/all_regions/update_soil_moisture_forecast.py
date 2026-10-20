"""Refresh native ECMWF IFS 28-100 cm soil-moisture forecasts."""

from __future__ import annotations

from datetime import date, timedelta

from backend import config
from backend.all_regions.rainfall_common import REGIONS, fetch_json
from backend.all_regions.soil_moisture_common import SOIL_METHOD, SOIL_METRIC, SOIL_VARIABLE, native_soil_rows
from backend.database import repository

FORECAST_URL = config.OPEN_METEO_ECMWF_URL


def main() -> None:
    start = date.today()
    end = start + timedelta(days=config.ALL_REGIONS_FORECAST_DAYS)
    payload = fetch_json(FORECAST_URL, {
        "latitude": ",".join(str(r[2]) for r in REGIONS),
        "longitude": ",".join(str(r[3]) for r in REGIONS),
        "start_date": start.isoformat(), "end_date": end.isoformat(),
        "hourly": SOIL_VARIABLE, "timezone": "auto",
    })
    rows = native_soil_rows(
        payload, "hourly", "forecast",
        "Open-Meteo ECMWF Forecast API / IFS HRES",
        SOIL_METHOD,
    )
    with repository.tracked_run("all_regions", "soil_moisture_forecast") as run:
        run["rows"] = repository.replace_weather(SOIL_METRIC, "forecast", rows)
    print(f"Stored {run['rows']:,} soil-moisture forecast rows ({start} to {end})")


if __name__ == "__main__":
    main()

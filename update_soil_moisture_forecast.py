"""Refresh native ECMWF IFS 28-100 cm soil-moisture forecasts."""

from __future__ import annotations

from datetime import date, timedelta

from rainfall_common import REGIONS, fetch_json
from soil_moisture_common import (
    COMBINED_CSV, FORECAST_CSV, SOIL_VARIABLE,
    native_soil_rows, rebuild_combined, write_csv,
)

FORECAST_URL = "https://api.open-meteo.com/v1/ecmwf"


def main() -> None:
    start, end = date.today(), date.today() + timedelta(days=14)
    payload = fetch_json(FORECAST_URL, {
        "latitude": ",".join(str(r[2]) for r in REGIONS),
        "longitude": ",".join(str(r[3]) for r in REGIONS),
        "start_date": start.isoformat(), "end_date": end.isoformat(),
        "hourly": SOIL_VARIABLE, "timezone": "auto",
    })
    rows = native_soil_rows(
        payload, "hourly", "forecast",
        "Open-Meteo ECMWF Forecast API / IFS HRES",
        "Native ECMWF IFS 28-100 cm; hourly values averaged by local day",
    )
    write_csv(FORECAST_CSV, rows)
    total = rebuild_combined()
    print(f"Wrote {len(rows):,} forecast rows to {FORECAST_CSV}")
    print(f"Rebuilt {COMBINED_CSV} with {total:,} rows")


if __name__ == "__main__":
    main()

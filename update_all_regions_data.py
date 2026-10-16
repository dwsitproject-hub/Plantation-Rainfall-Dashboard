"""Pull only the data required by the local All Regions dashboard."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from dashboard_server import build_payload, save_payload_cache


ROOT = Path(__file__).resolve().parent
STEPS = (
    ("Historical rainfall and temperature", "pull_historical_rainfall.py"),
    ("Historical soil moisture", "pull_historical_soil_moisture.py"),
    ("Rainfall and temperature forecast", "update_rainfall_forecast.py"),
    ("Soil-moisture forecast", "update_soil_moisture_forecast.py"),
)


def main() -> int:
    for label, filename in STEPS:
        print(f"\n[All Regions] {label}", flush=True)
        subprocess.run([sys.executable, str(ROOT / filename)], cwd=ROOT, check=True)
    payload = build_payload()
    save_payload_cache(payload)
    print(
        f"\nAll Regions data ready: actual through {payload['lastActual']}; "
        f"forecast through {payload['forecastEnd']}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

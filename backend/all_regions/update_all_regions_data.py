"""Pull only the data required by the local All Regions dashboard."""

from __future__ import annotations

import subprocess
import sys

from backend.all_regions.dashboard_payload import build_payload, save_payload_cache
from backend.config import PROJECT_ROOT


STEPS = (
    ("Historical rainfall and temperature", "backend.all_regions.pull_historical_rainfall"),
    ("Historical soil moisture", "backend.all_regions.pull_historical_soil_moisture"),
    ("Rainfall and temperature forecast", "backend.all_regions.update_rainfall_forecast"),
    ("Soil-moisture forecast", "backend.all_regions.update_soil_moisture_forecast"),
)


def main() -> int:
    for label, module in STEPS:
        print(f"\n[All Regions] {label}", flush=True)
        subprocess.run([sys.executable, "-m", module], cwd=PROJECT_ROOT, check=True)
    payload = build_payload()
    save_payload_cache(payload)
    print(
        f"\nAll Regions data ready: actual through {payload['lastActual']}; "
        f"forecast through {payload['forecastEnd']}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

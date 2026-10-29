"""Open-Meteo history downloads start just before the last stored day, not at the beginning."""

from __future__ import annotations

from datetime import date

from backend.all_regions import pull_historical_rainfall as rainfall
from backend.all_regions import pull_historical_soil_moisture as soil
from backend.all_regions.rainfall_common import REGIONS


def test_rainfall_starts_overlap_days_before_the_older_of_rain_and_temperature(monkeypatch):
    latest = {"rainfall_mm": date(2026, 10, 20), "temperature_mean_c": date(2026, 10, 18)}
    monkeypatch.setattr(rainfall.repository, "latest_weather_date", lambda metric, data_type, regions: latest[metric])
    monkeypatch.setattr(rainfall.config, "HISTORY_OVERLAP_DAYS", 14)
    assert rainfall.default_start() == date(2026, 10, 4)


def test_rainfall_downloads_everything_when_a_region_has_no_history(monkeypatch):
    monkeypatch.setattr(rainfall.repository, "latest_weather_date", lambda *args, **kwargs: None)
    assert rainfall.default_start() == date.fromisoformat(rainfall.config.HISTORICAL_RAINFALL_START_DATE)


def test_soil_moisture_finds_the_first_incomplete_day():
    keys = {(country, region, day) for country, region, *_ in REGIONS for day in ("2026-01-01", "2026-01-02")}
    keys.add(("Indonesia", "Riau", "2026-01-03"))  # only one region has the 3rd
    assert soil.first_missing_day(keys, date(2026, 1, 1), date(2026, 12, 31)) == date(2026, 1, 3)

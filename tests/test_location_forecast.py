"""Retry of the in-house Open-Meteo forecast pull (no network is used)."""

from __future__ import annotations

import io
import json
from datetime import date, timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

import pytest

from backend import config
from backend.all_regions import rainfall_common
from backend.plantation.update_location_forecast import fetch_forecasts

LOCATIONS = [
    {"id": "TH3A", "latitude": 0.5, "longitude": 101.5},
    {"id": "CR3A", "latitude": -0.25, "longitude": 102.0},
]


def open_meteo_payload():
    days = [(date.today() + timedelta(days=offset)).isoformat() for offset in (-1, 0, 1)]
    return [{"latitude": item["latitude"], "longitude": item["longitude"], "timezone": "Asia/Jakarta",
             "daily": {"time": days, "precipitation_sum": [1.0, 2.0, 3.0]}} for item in LOCATIONS]


def http_error(code):
    return HTTPError(config.OPEN_METEO_FORECAST_URL, code, "error", {}, io.BytesIO(b'{"reason":"x"}'))


@pytest.fixture
def open_meteo(monkeypatch):
    """Replace urlopen with a scripted sequence of failures and responses."""
    state = {"script": [], "urls": [], "timeouts": [], "sleeps": []}

    def fake_urlopen(request, timeout):
        state["urls"].append(request.full_url)
        state["timeouts"].append(timeout)
        outcome = state["script"].pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return io.BytesIO(json.dumps(outcome).encode("utf-8"))

    monkeypatch.setattr(rainfall_common, "urlopen", fake_urlopen)
    monkeypatch.setattr(rainfall_common.time, "sleep", state["sleeps"].append)
    return state


def test_transient_errors_are_retried(open_meteo):
    open_meteo["script"] = [http_error(503), URLError("connection reset"), open_meteo_payload()]

    forecasts = fetch_forecasts(LOCATIONS)

    assert len(open_meteo["urls"]) == 3
    assert open_meteo["sleeps"] == [1, 2]
    assert open_meteo["timeouts"] == [120, 120, 120]
    assert [item["id"] for item in forecasts] == ["TH3A", "CR3A"]
    today = date.today()
    assert forecasts[0]["actual_dates"] == [(today - timedelta(days=1)).isoformat()]
    assert forecasts[0]["forecast_dates"] == [today.isoformat(), (today + timedelta(days=1)).isoformat()]
    assert forecasts[0]["forecast_rainfall_mm"] == [2.0, 3.0]

    query = parse_qs(urlsplit(open_meteo["urls"][0]).query)
    assert urlsplit(open_meteo["urls"][0]).geturl().startswith(config.OPEN_METEO_FORECAST_URL)
    assert query["latitude"] == ["0.5,-0.25"]
    assert query["past_days"] == [str(config.PLANTATION_PAST_DAYS)]


def test_rate_limit_waits_longer(open_meteo):
    open_meteo["script"] = [http_error(429), open_meteo_payload()]
    fetch_forecasts(LOCATIONS)
    assert open_meteo["sleeps"] == [10]


def test_client_errors_are_not_retried(open_meteo):
    open_meteo["script"] = [http_error(400)]
    with pytest.raises(RuntimeError, match="HTTP 400"):
        fetch_forecasts(LOCATIONS)
    assert len(open_meteo["urls"]) == 1


def test_gives_up_after_four_attempts(open_meteo):
    open_meteo["script"] = [http_error(502)] * 4
    with pytest.raises(RuntimeError, match="failed after 4 attempts"):
        fetch_forecasts(LOCATIONS)
    assert open_meteo["sleeps"] == [1, 2, 4]


def test_open_meteo_error_payload_is_reported(open_meteo):
    open_meteo["script"] = [{"error": True, "reason": "Latitude must be in range"}]
    with pytest.raises(RuntimeError, match="Latitude must be in range"):
        fetch_forecasts(LOCATIONS)


def test_response_count_must_match_locations(open_meteo):
    open_meteo["script"] = [open_meteo_payload()[:1]]
    with pytest.raises(ValueError, match="Expected 2 forecast responses, received 1"):
        fetch_forecasts(LOCATIONS)

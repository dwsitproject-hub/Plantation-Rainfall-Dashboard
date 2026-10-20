"""Estates refused with HTTP 429 are retried after a cool-down instead of failing the refresh."""

from __future__ import annotations

from backend.plantation import pull_sap_rainfall as sap
from backend.plantation import pull_sap_rainfall_all_regions as pull


def run_pull(monkeypatch, refusals: dict[tuple[str, str], int]):
    monkeypatch.setattr(pull.config, "sap_estates", lambda: [("TH", "3A"), ("GN", "3B"), ("TS", "3B")])
    monkeypatch.setattr(sap, "authenticate", lambda environment: "token")
    calls = []

    def fake_fetch(environment, token, company, estate, delay):
        calls.append((company, estate))
        if refusals.get((company, estate), 0) > 0:
            refusals[(company, estate)] -= 1
            raise sap.ApiError(f"HTTP 429 from https://example/rain-data: Rate limit exceeded")
        return [{"company_code": company, "estate": estate, "posting-date": "01.10.2026"}]

    monkeypatch.setattr(pull, "fetch_one", fake_fetch)
    rows, errors = pull.pull_environment("prod", workers=2, delay=0, cooldown_seconds=0)
    return rows, errors, calls


def test_rate_limited_estates_are_retried_and_succeed(monkeypatch):
    rows, errors, calls = run_pull(monkeypatch, {("GN", "3B"): 1, ("TS", "3B"): 2})
    assert errors == []
    assert sorted((row["company_code"], row["estate"]) for row in rows) == [("GN", "3B"), ("TH", "3A"), ("TS", "3B")]
    assert calls.count(("TS", "3B")) == 3


def test_estate_still_refused_after_all_rounds_is_reported(monkeypatch):
    rows, errors, _ = run_pull(monkeypatch, {("GN", "3B"): 99})
    assert len(rows) == 2 and len(errors) == 1 and errors[0].startswith("GN/3B: HTTP 429")

"""SAP requests are paced to Syncera's rate limit; refused calls wait for the window and are retried."""

from __future__ import annotations

import time

from backend.plantation import pull_sap_rainfall as sap
from backend.plantation import pull_sap_rainfall_all_regions as pull


class FakeTime:
    def __init__(self):
        self.now = 0.0

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def run_pull(monkeypatch, failures: dict[tuple[str, str], list[str]], per_minute=10):
    monkeypatch.setattr(pull.config, "sap_estates", lambda: [("TH", "3A"), ("GN", "3B"), ("TS", "3B")])
    fake = FakeTime()
    logins, calls = [], []
    monkeypatch.setattr(sap, "authenticate", lambda environment: logins.append(fake.now) or f"token{len(logins)}")

    def fake_fetch(environment, token, endpoint, company, estate):
        calls.append((fake.now, company, estate, token))
        pending = failures.get((company, estate))
        if pending:
            raise sap.ApiError(pending.pop(0))
        return [{"company_code": company, "estate": estate, "posting-date": "01.10.2026"}]

    monkeypatch.setattr(pull, "fetch_one", fake_fetch)
    rows, errors = pull.pull_environment("prod", per_minute, max_in_flight=1, clock=fake.clock, sleep=fake.sleep)
    return rows["rain-data-current"], errors, calls, logins


def test_requests_are_spaced_to_the_limit_including_the_login(monkeypatch):
    rows, errors, calls, logins = run_pull(monkeypatch, {})
    assert errors == [] and len(rows) == 3
    starts = logins + [call[0] for call in calls]
    assert starts == [0.0, 6.0, 12.0, 18.0]  # 10 per minute = one every 6 s


def test_refused_call_waits_for_the_window_and_succeeds(monkeypatch):
    refused = "HTTP 429 from https://example/rain-data: Rate limit exceeded"
    rows, errors, calls, _ = run_pull(monkeypatch, {("GN", "3B"): [refused]})
    assert errors == [] and len(rows) == 3
    gn = [call[0] for call in calls if call[1] == "GN"]
    assert gn == [12.0, 72.0]  # retried a full minute later
    assert calls[-1][0] == 78.0  # pacing continues from the retry


def test_estate_refused_on_every_attempt_is_reported_and_others_still_load(monkeypatch):
    refused = "HTTP 429 from https://example/rain-data: Rate limit exceeded"
    rows, errors, _, _ = run_pull(monkeypatch, {("GN", "3B"): [refused] * 5})
    assert len(rows) == 2 and len(errors) == 1 and errors[0].startswith("rain-data-current GN/3B: HTTP 429")


def test_expired_login_is_renewed(monkeypatch):
    rows, errors, calls, logins = run_pull(monkeypatch, {("GN", "3B"): ["HTTP 401 from x: token expired"]})
    assert errors == [] and len(rows) == 3 and len(logins) == 2
    assert [call[3] for call in calls if call[1] == "TS"] == ["token2"]


def test_other_errors_are_not_retried(monkeypatch):
    rows, errors, calls, _ = run_pull(monkeypatch, {("TH", "3A"): ["HTTP 400 from x: bad estate"]})
    assert len(rows) == 2 and errors == ["rain-data-current TH/3A: HTTP 400 from x: bad estate"]
    assert sum(call[1] == "TH" for call in calls) == 1


def test_slow_answers_overlap_instead_of_holding_up_the_next_request(monkeypatch):
    estates = [("TH", f"3{letter}") for letter in "ABCDEF"]
    monkeypatch.setattr(pull.config, "sap_estates", lambda: estates)
    monkeypatch.setattr(sap, "authenticate", lambda environment: "token")

    def slow_fetch(environment, token, endpoint, company, estate):
        time.sleep(0.5)  # Syncera sometimes takes many seconds to answer
        return [{"company_code": company, "estate": estate}]

    monkeypatch.setattr(pull, "fetch_one", slow_fetch)
    started = time.monotonic()
    rows, errors = pull.pull_environment("prod", 600, max_in_flight=4)  # one start every 0.1 s
    assert errors == [] and len(rows["rain-data-current"]) == 6
    assert time.monotonic() - started < 1.6  # one after another would take 3 s


def test_both_endpoints_share_one_paced_queue(monkeypatch):
    monkeypatch.setattr(pull.config, "sap_estates", lambda: [("TH", "3A"), ("GN", "3B")])
    monkeypatch.setattr(sap, "authenticate", lambda environment: "token")
    fake, calls = FakeTime(), []

    def fetch(environment, token, endpoint, company, estate):
        calls.append((fake.now, endpoint, company))
        return [{"company_code": company, "estate": estate, "endpoint": endpoint}]

    monkeypatch.setattr(pull, "fetch_one", fetch)
    rows, errors = pull.pull_environment("prod", 10, endpoints=("rain-data", "rain-data-current"),
                                         max_in_flight=1, clock=fake.clock, sleep=fake.sleep)
    assert errors == []
    assert [len(rows["rain-data"]), len(rows["rain-data-current"])] == [2, 2]
    assert [call[0] for call in calls] == [6.0, 12.0, 18.0, 24.0]

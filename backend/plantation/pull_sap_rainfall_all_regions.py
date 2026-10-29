#!/usr/bin/env python3
"""Pull rainfall for every company/estate pair in SAP_ESTATES into one combined CSV per dataset.

The CSVs are staging files; update_all_rainfall_dashboard loads them into PostgreSQL.

Syncera API guide v1.5 (docs/API Access Guide Rain Data (Prod) v.1.5.pdf) has two
endpoints, each taking company_code (required) and estate:
  rain-data-current  the current month          -> "SAP rainfall data current.csv"
  rain-data          all previous months/years  -> "SAP rainfall data history.csv"

Syncera limits requests per minute (measured 23 Oct 2026: the 11th call within a
minute was refused with HTTP 429 and the block cleared a minute after the first; by
29 Oct the staging refresh was refused after about 6 a minute). The API has no date filter and one call returns one estate, so every
refresh needs one call per estate. Calls are therefore started at an even pace
(SAP_REQUESTS_PER_MINUTE, default 5 for a margin) instead of in a burst that
is refused and retried. Up to MAX_IN_FLIGHT calls may be waiting for an answer at
once, so a slow answer (Syncera sometimes takes 20+ s) does not hold up the next
start. A refused call waits for the window to clear and is sent again.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Callable

from backend import config
from backend.plantation import pull_sap_rainfall as sap


DATASETS = {  # name: (endpoint, staging file)
    "current": ("rain-data-current", "SAP rainfall data current.csv"),
    "history": ("rain-data", "SAP rainfall data history.csv"),
}
RATE_LIMIT_COOLDOWN_SECONDS = 60  # Syncera's limit window
ATTEMPTS_PER_ESTATE = 3
MAX_IN_FLIGHT = 4


class Pacer:
    """Spaces request starts evenly so no more than per_minute fall in any minute."""

    def __init__(self, per_minute: float, *, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self.interval = 60.0 / max(per_minute, 0.1)
        self.clock, self.sleep = clock, sleep
        self.next_start = None

    def wait(self) -> None:
        now = self.clock()
        if self.next_start is not None and now < self.next_start:
            self.sleep(self.next_start - now)
            now = self.next_start
        self.next_start = now + self.interval

    def pause(self, seconds: float) -> None:
        """Wait out a refused window; the next request starts after it."""
        self.sleep(seconds)
        self.next_start = self.clock()


def fetch_one(environment: str, token: str, endpoint: str, company_code: str, estate: str):
    base_url = config.syncera(environment)["base_url"]
    url = f"{base_url}/{endpoint}?company_code={company_code}&estate={estate}"
    try:
        # One attempt: pacing and retries are decided by pull_environment.
        result = sap.request_json(
            "GET", url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"}, retries=1)
    except sap.ApiError as exc:
        # PROD returns HTTP 404 / "No data found" for a valid estate with no
        # rows in the current dataset. This is an empty result, not a failed
        # batch; treating it as fatal previously prevented every successful
        # October record from being promoted to the dashboard.
        message = str(exc).lower()
        if "http 404" in message and "no data found" in message:
            return []
        raise
    if isinstance(result, dict) and result.get("success") is False:
        raise sap.ApiError(str(result.get("message", result)))
    data = result.get("data", result) if isinstance(result, dict) else result
    if data is None:
        return []
    return data if isinstance(data, list) else [data]


def is_rate_limited(error: Exception) -> bool:
    return "http 429" in str(error).lower()


def is_expired_token(error: Exception) -> bool:
    return "http 401" in str(error).lower()


def is_transient(error: Exception) -> bool:
    text = str(error).lower()
    return "http 5" in text or "could not reach" in text or "request failed after" in text


def pull_environment(environment: str, requests_per_minute: float, *,
                     endpoints: tuple[str, ...] = ("rain-data-current",),
                     cooldown_seconds: float = RATE_LIMIT_COOLDOWN_SECONDS,
                     max_in_flight: int = MAX_IN_FLIGHT,
                     clock: Callable[[], float] = time.monotonic,
                     sleep: Callable[[float], None] = time.sleep):
    pacer = Pacer(requests_per_minute, clock=clock, sleep=sleep)
    estates = config.sap_estates()
    calls = len(estates) * len(endpoints)
    print(f"{environment}: {', '.join(endpoints)} for {len(estates)} estates = {calls} requests at "
          f"{requests_per_minute:g}/minute (about {calls / requests_per_minute:.0f} min)", flush=True)
    pacer.wait()  # the login counts towards the limit too
    token = sap.authenticate(environment)
    pending = deque((endpoint, company, estate, 1) for endpoint in endpoints for company, estate in estates)
    in_flight: dict = {}
    combined: dict[str, list] = {endpoint: [] for endpoint in endpoints}
    errors = []

    def handle(future) -> None:
        nonlocal token
        endpoint, company, estate, attempt, used_token = in_flight.pop(future)
        label = f"{endpoint} {company}/{estate}"
        try:
            rows = future.result()
        except sap.ApiError as exc:
            last = attempt == ATTEMPTS_PER_ESTATE
            if is_rate_limited(exc) and not last:
                print(f"{environment}: {label} - rate limited; waiting {cooldown_seconds:.0f}s", flush=True)
                pacer.pause(cooldown_seconds)
            elif is_expired_token(exc) and not last:
                if used_token == token:  # renew once, not once per call that used the old login
                    print(f"{environment}: login expired; signing in again", flush=True)
                    pacer.wait()
                    token = sap.authenticate(environment)
            elif is_transient(exc) and not last:
                print(f"{environment}: {label} - {exc}; retrying", flush=True)
            else:
                errors.append(f"{label}: {exc}")
                print(f"{environment}: {label} - ERROR: {exc}", file=sys.stderr, flush=True)
                return
            pending.appendleft((endpoint, company, estate, attempt + 1))
            return
        combined[endpoint].extend(rows)
        print(f"{environment}: {label} - {len(rows):,} rows", flush=True)

    with ThreadPoolExecutor(max_workers=max(1, max_in_flight)) as pool:
        while pending or in_flight:
            if pending and len(in_flight) < max(1, max_in_flight):
                pacer.wait()
                endpoint, company, estate, attempt = pending.popleft()
                future = pool.submit(fetch_one, environment, token, endpoint, company, estate)
                in_flight[future] = (endpoint, company, estate, attempt, token)
                done = [item for item in in_flight if item.done()]
            else:
                done, _ = wait(list(in_flight), return_when=FIRST_COMPLETED)
            for future in done:
                handle(future)
    for rows in combined.values():
        rows.sort(key=lambda row: (
            str(row.get("company_code", "")) if isinstance(row, dict) else "",
            str(row.get("estate", "")) if isinstance(row, dict) else "",
            str(row.get("posting-date", "")) if isinstance(row, dict) else "",
        ))
    return combined, errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Pull SAP rainfall for every estate in SAP_ESTATES.")
    parser.add_argument("--requests-per-minute", type=float, default=config.SAP_REQUESTS_PER_MINUTE,
                        help="Syncera's rate limit (default: SAP_REQUESTS_PER_MINUTE).")
    parser.add_argument("--dataset", choices=("current", "history", "all"), default="current",
                        help="current = this month (every refresh); history = previous months.")
    parser.add_argument("--environment", choices=("dev", "prod"), default="prod")
    parser.add_argument("--output-dir", type=Path, default=config.WORK_DIR / "sap")
    args = parser.parse_args()
    names = list(DATASETS) if args.dataset == "all" else [args.dataset]
    try:
        rows_by_endpoint, errors = pull_environment(
            args.environment, args.requests_per_minute, endpoints=tuple(DATASETS[name][0] for name in names))
    except Exception as exc:
        print(f"Pull failed: {exc}", file=sys.stderr)
        return 1
    for name in names:
        endpoint, filename = DATASETS[name]
        sap.write_csv(rows_by_endpoint[endpoint], args.output_dir / filename)
        print(f"Saved {len(rows_by_endpoint[endpoint]):,} {name} rows to {args.output_dir / filename}")
    if errors:
        print("Completed with estate errors:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

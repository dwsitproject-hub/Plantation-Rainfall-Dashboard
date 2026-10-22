#!/usr/bin/env python3
"""Pull rainfall for every company/estate pair in SAP_ESTATES into two combined CSVs.

The CSVs are staging files; update_all_rainfall_dashboard loads them into PostgreSQL.
"""

from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from backend import config
from backend.plantation import pull_sap_rainfall as sap


RATE_LIMIT_COOLDOWN_SECONDS = 60
RATE_LIMIT_ROUNDS = 3


def fetch_one(environment: str, token: str, company_code: str, estate: str, delay: float):
    if delay:
        time.sleep(delay)
    base_url = config.syncera(environment)["base_url"]
    url = f"{base_url}/rain-data?company_code={company_code}&estate={estate}"
    try:
        result = sap.request_json(
            "GET",
            url,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            retries=8,
        )
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


def pull_environment(environment: str, workers: int, delay: float, *,
                     cooldown_seconds: float = RATE_LIMIT_COOLDOWN_SECONDS):
    token = sap.authenticate(environment)
    combined = []
    errors = []
    rate_limited = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(fetch_one, environment, token, company, estate, delay): (company, estate)
            for company, estate in config.sap_estates()
        }
        for future in as_completed(futures):
            company, estate = futures[future]
            try:
                rows = future.result()
                combined.extend(rows)
                print(f"{environment}: {company}/{estate} - {len(rows):,} rows", flush=True)
            except Exception as exc:
                if is_rate_limited(exc):
                    rate_limited.append((company, estate))
                    print(f"{environment}: {company}/{estate} - rate limited, will retry after a pause", flush=True)
                else:
                    errors.append(f"{company}/{estate}: {exc}")
                    print(f"{environment}: {company}/{estate} - ERROR: {exc}", file=sys.stderr, flush=True)

    # Syncera limits request rates. Estates refused during the parallel pass are
    # retried one at a time after a cool-down, so a busy minute does not fail the refresh.
    for attempt in range(1, RATE_LIMIT_ROUNDS + 1):
        if not rate_limited:
            break
        print(f"{environment}: {len(rate_limited)} estate(s) rate limited; waiting {cooldown_seconds:.0f}s "
              f"before retry round {attempt}/{RATE_LIMIT_ROUNDS}", flush=True)
        time.sleep(cooldown_seconds)
        still_limited = []
        for company, estate in rate_limited:
            try:
                rows = fetch_one(environment, token, company, estate, max(delay, 1.0))
                combined.extend(rows)
                print(f"{environment}: {company}/{estate} - {len(rows):,} rows (retry)", flush=True)
            except Exception as exc:
                if is_rate_limited(exc) and attempt < RATE_LIMIT_ROUNDS:
                    still_limited.append((company, estate))
                else:
                    errors.append(f"{company}/{estate}: {exc}")
                    print(f"{environment}: {company}/{estate} - ERROR: {exc}", file=sys.stderr, flush=True)
        rate_limited = still_limited
    combined.sort(key=lambda row: (
        str(row.get("company_code", "")) if isinstance(row, dict) else "",
        str(row.get("estate", "")) if isinstance(row, dict) else "",
        str(row.get("posting-date", "")) if isinstance(row, dict) else "",
    ))
    return combined, errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Pull all requested SAP rainfall regions.")
    parser.add_argument("--workers", type=int, default=config.SAP_WORKERS)
    parser.add_argument("--delay", type=float, default=config.SAP_REQUEST_DELAY_SECONDS,
                        help="Seconds to wait before each API request.")
    # The dashboard uses PROD only; DEV is kept as an option for manual checks.
    parser.add_argument("--environment", choices=("all", "dev", "prod"), default="prod")
    parser.add_argument("--output-dir", type=Path, default=config.WORK_DIR / "sap")
    args = parser.parse_args()
    jobs = [
        ("dev", "SAP rainfall data historical.csv"),
        ("prod", "SAP rainfall data current.csv"),
    ]
    if args.environment != "all":
        jobs = [job for job in jobs if job[0] == args.environment]
    failures = []
    for environment, filename in jobs:
        try:
            rows, errors = pull_environment(environment, max(1, args.workers), max(0, args.delay))
            sap.write_csv(rows, args.output_dir / filename)
            print(f"Saved {len(rows):,} rows to {args.output_dir / filename}")
            failures.extend(f"{environment} {error}" for error in errors)
        except Exception as exc:
            failures.append(f"{environment}: {exc}")
    if failures:
        print("Completed with region errors:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

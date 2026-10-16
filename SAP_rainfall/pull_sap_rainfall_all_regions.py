#!/usr/bin/env python3
"""Pull rainfall for all requested company/estate pairs into two combined CSVs."""

from __future__ import annotations

import argparse
import getpass
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pull_sap_rainfall as sap


SECRET_NAMES = ("SYNCERA_DEV_CLIENT_SECRET", "SYNCERA_PROD_CLIENT_SECRET")
KEYRING_SERVICE = "SAP Rainfall Dashboard"


def ensure_secrets() -> None:
    """Load secrets from Credential Manager, prompting and saving when needed."""
    try:
        import keyring
    except ImportError:
        keyring = None

    for name in SECRET_NAMES:
        if os.environ.get(name):
            continue
        value = None
        if keyring is not None:
            try:
                value = keyring.get_password(KEYRING_SERVICE, name)
            except Exception as exc:
                print(f"Warning: could not read saved credential {name}: {exc}", file=sys.stderr)
        if not value and sys.stdin.isatty():
            label = "DEV" if "DEV" in name else "PROD"
            value = getpass.getpass(f"Enter the {label} client secret (input hidden): ").strip()
            if value and keyring is not None:
                keyring.set_password(KEYRING_SERVICE, name, value)
                print(f"Saved the {label} credential securely in Windows Credential Manager.")
        if value:
            os.environ[name] = value


# Duplicates from the supplied list have been removed while preserving order.
REGIONS = [
    ("CR", "3A"), ("TH", "3J"), ("PN", "3B"), ("AC", "3B"),
    ("AM", "3A"), ("BU", "3A"), ("BU", "3D"), ("TH", "3F"),
    ("SI", "3A"), ("BU", "3C"), ("TH", "3O"), ("WS", "3A"),
    ("TS", "3A"), ("TS", "3B"), ("AJ", "3A"), ("SU", "3B"),
    ("PT", "3A"), ("AC", "3A"), ("GN", "3A"), ("PN", "3A"),
    ("SA", "3A"), ("GN", "3C"), ("GN", "3B"), ("PT", "3B"),
    ("WW", "3A"), ("TH", "3G"), ("WW", "3B"), ("CM", "3B"),
    ("TH", "3B"), ("SI", "3B"), ("JJ", "9A"), ("TH", "3M"),
    ("SU", "3A"), ("TH", "3K"), ("TH", "3N"), ("TH", "3L"),
    ("PN", "3C"), ("KU", "3A"), ("TH", "3Q"), ("PD", "3A"),
    ("TH", "3P"), ("TH", "3T"), ("IP", "3A"), ("TH", "3R"),
    ("TH", "3H"), ("TH", "3I"), ("PD", "3B"), ("KB", "9A"),
    ("CM", "3A"), ("NJ", "3A"), ("BS", "3A"), ("BU", "3B"),
    ("JJ", "3A"), ("TH", "3E"), ("TH", "3A"), ("TH", "3S"),
    ("TH", "3C"), ("TH", "3D"), ("JJ", "3B"),
]


def fetch_one(environment: str, token: str, company_code: str, estate: str, delay: float):
    if delay:
        time.sleep(delay)
    config = sap.ENVIRONMENTS[environment]
    url = f"{config['base_url']}/rain-data?company_code={company_code}&estate={estate}"
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


def pull_environment(environment: str, workers: int, delay: float):
    token = sap.authenticate(environment)
    combined = []
    errors = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(fetch_one, environment, token, company, estate, delay): (company, estate)
            for company, estate in REGIONS
        }
        for future in as_completed(futures):
            company, estate = futures[future]
            try:
                rows = future.result()
                combined.extend(rows)
                print(f"{environment}: {company}/{estate} - {len(rows):,} rows")
            except Exception as exc:
                errors.append(f"{company}/{estate}: {exc}")
                print(f"{environment}: {company}/{estate} - ERROR: {exc}", file=sys.stderr)
    combined.sort(key=lambda row: (
        str(row.get("company_code", "")) if isinstance(row, dict) else "",
        str(row.get("estate", "")) if isinstance(row, dict) else "",
        str(row.get("posting-date", "")) if isinstance(row, dict) else "",
    ))
    return combined, errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Pull all requested SAP rainfall regions.")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--delay", type=float, default=0, help="Seconds to wait before each API request.")
    parser.add_argument("--environment", choices=("all", "dev", "prod"), default="all")
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    ensure_secrets()
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

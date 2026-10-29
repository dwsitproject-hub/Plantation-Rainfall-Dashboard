#!/usr/bin/env python3
"""Shared Syncera/SAP client (authentication, requests, CSV output), plus a manual
single-estate pull for checks.

The dashboard refresh uses pull_sap_rainfall_all_regions, which follows API guide
v1.5 (PROD only): rain-data-current for the current month and rain-data for all
previous months. The guide documents no date filter; tokens last 3600 seconds.

Base URLs, client IDs and secrets are read from .env (SYNCERA_PROD_*; SYNCERA_DEV_*
only for this manual tool's --historical-environment dev).
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from backend import config


ENVIRONMENTS = ("dev", "prod")


# English output names for the fields returned by the live rainfall API.
HEADER_TRANSLATIONS = {
    "company_code": "company_code",
    "estate": "estate",
    "plant": "plant",
    "posting-date": "posting_date",
    "meas-tools": "measurement_tool",
    "admin": "administrator",
    "rainy-day": "rainy_day",
    "jumlah-hujan-dalam-s": "number_of_rain_events",
    "hasil-pengukuran-pagi": "morning_rainfall_measurement",
    "hasil-pengukuran-siang": "afternoon_rainfall_measurement",
    "hasil-pengukuran-malam": "night_rainfall_measurement",
    "hasil-pengukuran": "total_rainfall_measurement",
    "keterangan": "remarks",
    "lamanya-hujan-dalam-pagi": "morning_rain_duration_minutes",
    "lamanya-hujan-dalam-siang": "afternoon_rain_duration_minutes",
    "lamanya-hujan-dalam-malam": "night_rain_duration_minutes",
}


class ApiError(RuntimeError):
    pass


def request_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
    retries: int = 3,
    timeout: int = 60,
) -> Any:
    encoded = None if body is None else json.dumps(body).encode("utf-8")
    request_headers = dict(headers or {})
    if encoded is not None:
        request_headers["Content-Type"] = "application/json"

    for attempt in range(retries):
        request = Request(url, data=encoded, headers=request_headers, method=method)
        try:
            with urlopen(request, timeout=timeout) as response:
                payload = response.read().decode("utf-8-sig")
                return json.loads(payload) if payload else {}
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            if (exc.code == 429 or exc.code >= 500) and attempt + 1 < retries:
                retry_after = exc.headers.get("Retry-After")
                wait_seconds = float(retry_after) if retry_after else 2**attempt
                time.sleep(min(wait_seconds, 60))
                continue
            try:
                message = json.loads(detail).get("message", detail)
            except json.JSONDecodeError:
                message = detail
            raise ApiError(f"HTTP {exc.code} from {url}: {message}") from exc
        except (URLError, TimeoutError) as exc:
            if attempt + 1 < retries:
                time.sleep(2**attempt)
                continue
            raise ApiError(f"Could not reach {url}: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise ApiError(f"API returned invalid JSON from {url}: {exc}") from exc
    raise ApiError(f"Request failed after {retries} attempts: {url}")


def authenticate(environment: str) -> str:
    try:
        settings = config.syncera(environment)
    except config.ConfigError as exc:
        raise ApiError(str(exc)) from exc
    result = request_json(
        "POST",
        f"{settings['base_url']}/auth",
        body={"clientId": settings["client_id"], "clientSecret": settings["client_secret"]},
    )
    # The PDFs show access_token, while the live APIs currently return accessToken.
    token = (result.get("access_token") or result.get("accessToken")) if isinstance(result, dict) else None
    if not token:
        raise ApiError(f"Authentication response for {environment} contained no access_token.")
    return str(token)


def fetch_rainfall(environment: str, company_code: str, estate: str | None, plant: str | None) -> list[Any]:
    base_url = config.syncera(environment)["base_url"]
    token = authenticate(environment)
    parameters = {"company_code": company_code}
    if estate:
        parameters["estate"] = estate
    if plant:
        parameters["plant"] = plant
    url = f"{base_url}/rain-data?{urlencode(parameters)}"
    result = request_json(
        "GET",
        url,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    if isinstance(result, dict) and result.get("success") is False:
        raise ApiError(f"Rainfall API reported failure for {environment}: {result.get('message', result)}")
    data = result.get("data", result) if isinstance(result, dict) else result
    if data is None:
        return []
    return data if isinstance(data, list) else [data]


def flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    output: dict[str, Any] = {}
    if isinstance(value, dict):
        for key, child in value.items():
            name = f"{prefix}.{key}" if prefix else str(key)
            output.update(flatten(child, name))
    elif isinstance(value, list):
        output[prefix or "value"] = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    else:
        output[prefix or "value"] = value
    return output


def translate_headers(row: dict[str, Any]) -> dict[str, Any]:
    return {HEADER_TRANSLATIONS.get(field, field): value for field, value in row.items()}


def write_csv(records: list[Any], output_path: Path) -> None:
    rows = [translate_headers(flatten(record)) for record in records]
    fields: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for field in row:
            if field not in seen:
                seen.add(field)
                fields.append(field)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_suffix(output_path.suffix + ".tmp")
    with temporary_path.open("w", encoding="utf-8-sig", newline="") as stream:
        if fields:
            writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
    temporary_path.replace(output_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Pull Syncera/SAP rainfall data into two CSV files.")
    parser.add_argument("--company-code", default="TH", help="Required two-character company code (default: TH).")
    parser.add_argument("--historical-environment", choices=ENVIRONMENTS, default="dev")
    parser.add_argument("--current-environment", choices=ENVIRONMENTS, default="prod")
    parser.add_argument("--historical-estate", default=None, help="Default: SYNCERA_<ENV>_DEFAULT_ESTATE in .env.")
    parser.add_argument("--current-estate", default=None, help="Default: SYNCERA_<ENV>_DEFAULT_ESTATE in .env.")
    parser.add_argument("--plant", default=None, help="Optional two-character plant code for both requests.")
    parser.add_argument("--output-dir", type=Path, default=config.WORK_DIR / "sap")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    jobs = [
        ("historical", args.historical_environment, args.historical_estate, "SAP rainfall data historical.csv"),
        ("current", args.current_environment, args.current_estate, "SAP rainfall data current.csv"),
    ]
    failures: list[str] = []
    for label, environment, estate, filename in jobs:
        selected_estate = estate or config.syncera(environment)["default_estate"]
        try:
            records = fetch_rainfall(environment, args.company_code, selected_estate, args.plant)
            output_path = args.output_dir / filename
            write_csv(records, output_path)
            print(f"Saved {len(records):,} {label} rows to {output_path}")
        except ApiError as exc:
            failures.append(f"{label} ({environment}): {exc}")
    if failures:
        print("\nPull completed with errors:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

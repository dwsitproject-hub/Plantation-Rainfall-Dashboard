"""Store rainfall forecast runs and compare matching valid dates across runs."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta

from backend import config
from backend.all_regions.rainfall_common import (
    OVERALL_LABELS, REGIONS, MAJOR_PLANTATION_REGIONS,
    is_major_plantation_region, region_members,
)
from backend.database import repository


def run_date_of(row: dict) -> str:
    """Use the computer's local date of retrieval, not the UTC calendar date."""
    stamp = row.get("retrieved_at_utc", "")
    return datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone().date().isoformat()


def read_snapshots() -> list[dict]:
    return repository.read_snapshots()


def save_snapshot(run_date: str, rows: list[dict], *, only_if_missing: bool = False) -> int:
    return repository.save_snapshot(
        run_date, rows, retention_runs=config.FORECAST_SNAPSHOT_RETENTION_RUNS,
        only_if_missing=only_if_missing,
    )


def seed_current_forecast() -> int:
    """Preserve a pre-existing forecast before its first snapshot-aware update."""
    rows = repository.read_weather("rainfall_mm", "forecast")
    if not rows:
        return 0
    return save_snapshot(run_date_of(rows[0]), rows, only_if_missing=True)


def _run_values(rows: list[dict]) -> dict[str, dict[str, float]]:
    grouped = defaultdict(lambda: defaultdict(list))
    for row in rows:
        try:
            region = ("Pennisular" if row["country"] == "Malaysia" and
                      row["region"] in region_members("Malaysia", "Pennisular")
                      else row["region"])
            grouped[f"{row['country']}|{region}"][row["date"]].append(float(row["rainfall_mm"]))
        except (KeyError, TypeError, ValueError):
            continue
    values = defaultdict(dict, {
        key: {day: sum(amounts) / len(amounts) for day, amounts in dates.items()}
        for key, dates in grouped.items()
    })
    for country in ("Indonesia", "Malaysia", "Thailand"):
        by_day = defaultdict(list)
        for key, dates in values.items():
            region = key.split("|", 1)[1] if "|" in key else ""
            if key.startswith(country + "|") and is_major_plantation_region(country, region):
                for day, amount in dates.items():
                    by_day[day].append(amount)
        values[f"Overall {country}"] = {
            day: sum(amounts) / len(amounts) for day, amounts in by_day.items()
        }
    return values


def build_forecast_changes() -> dict:
    snapshots = read_snapshots()
    current_file = repository.read_weather("rainfall_mm", "forecast")
    # The current file is authoritative for its run date even if the archive
    # has not yet been populated by a snapshot-aware update.
    if current_file:
        current_run_date = run_date_of(current_file[0])
        snapshots = [row for row in snapshots if row["run_date"] != current_run_date]
        snapshots.extend({"run_date": current_run_date, **row} for row in current_file)
    run_dates = sorted({row["run_date"] for row in snapshots})
    if not run_dates:
        return {"latestRun": None, "previousRun": None, "regions": []}
    latest_run = run_dates[-1]
    previous_run = run_dates[-2] if len(run_dates) > 1 else None
    latest = _run_values([row for row in snapshots if row["run_date"] == latest_run])
    previous = _run_values([row for row in snapshots if row["run_date"] == previous_run]) if previous_run else {}
    metadata = [(country, region, f"{country}|{region}") for country, region, _, _ in REGIONS]
    metadata += [(country, OVERALL_LABELS[country], f"Overall {country}") for country in ("Indonesia", "Malaysia", "Thailand")]
    regions = []
    for country, region, key in metadata:
        latest_dates = latest.get(key, {})
        previous_dates = previous.get(key, {})
        dates = sorted(set(latest_dates) & set(previous_dates)) if previous_run else sorted(latest_dates)
        daily = [{"date": day, "latest": round(latest_dates[day], 2),
                  "previous": round(previous_dates[day], 2) if previous_run else None,
                  "delta": round(latest_dates[day] - previous_dates[day], 2) if previous_run else None}
                 for day in dates]
        latest_total = sum(item["latest"] for item in daily) if daily else None
        previous_total = sum(item["previous"] for item in daily) if previous_run and daily else None
        delta = latest_total - previous_total if previous_total is not None else None
        regions.append({
            "country": country, "region": region, "key": key,
            "daysCompared": len(daily), "latestTotal": round(latest_total, 1) if latest_total is not None else None,
            "previousTotal": round(previous_total, 1) if previous_total is not None else None,
            "delta": round(delta, 1) if delta is not None else None,
            "percent": round(delta / previous_total * 100, 1) if previous_total else None,
            "daily": daily,
        })
    country_order = {"Indonesia": 0, "Malaysia": 1, "Thailand": 2}
    regions.sort(key=lambda row: (country_order.get(row["country"], 99),
                                  0 if row["key"].startswith("Overall ") else 1,
                                  row["region"]))
    return {"latestRun": latest_run, "previousRun": previous_run, "regions": regions}


def build_last_week_accuracy() -> dict:
    """Compare the last completed Monday-Sunday week with its start-of-week forecast."""
    today = date.today()
    week_start = today - timedelta(days=today.weekday() + 7)
    week_end = week_start + timedelta(days=6)
    week_days = {(week_start + timedelta(days=offset)).isoformat() for offset in range(7)}

    snapshots = read_snapshots()
    eligible_runs = sorted({
        row["run_date"] for row in snapshots
        if row["run_date"] <= week_start.isoformat() and row.get("date") in week_days
    })
    forecast_run = eligible_runs[-1] if eligible_runs else None
    forecast_rows = [row for row in snapshots if row["run_date"] == forecast_run] if forecast_run else []
    actual_rows = [
        row for row in repository.read_weather(
            "rainfall_mm", "historical_actual",
            date_from=week_start.isoformat(), date_to=week_end.isoformat())
        if row.get("date") in week_days
    ]

    def daily_values(rows: list[dict]) -> dict[str, dict[str, float]]:
        grouped = defaultdict(lambda: defaultdict(list))
        for row in rows:
            if row.get("date") not in week_days:
                continue
            try:
                region = ("Pennisular" if row["country"] == "Malaysia" and
                          row["region"] in region_members("Malaysia", "Pennisular")
                          else row["region"])
                grouped[f"{row['country']}|{region}"][row["date"]].append(float(row["rainfall_mm"]))
            except (KeyError, TypeError, ValueError):
                continue
        return {
            key: {day: sum(amounts) / len(amounts) for day, amounts in dates.items()}
            for key, dates in grouped.items()
        }

    actual_daily = daily_values(actual_rows)
    forecast_daily = daily_values(forecast_rows)

    area_coverage = {}
    for country, region_names in MAJOR_PLANTATION_REGIONS.items():
        # Compare like with like: a member contributes to both lines only when
        # its archived forecast and final actual cover the entire week.
        included = [
            name for name in region_names
            if all(
                day in actual_daily.get(f"{country}|{name}", {})
                and day in forecast_daily.get(f"{country}|{name}", {})
                for day in week_days
            )
        ]
        area_coverage[country] = included
        keys = [f"{country}|{name}" for name in included]
        for values in (actual_daily, forecast_daily):
            values[f"Overall {country}"] = {
                day: sum(values[key][day] for key in keys) / len(keys)
                for day in week_days
            } if keys else {}

    metadata = [(country, region, f"{country}|{region}") for country, region, _, _ in REGIONS]
    metadata += [
        (country, OVERALL_LABELS[country], f"Overall {country}")
        for country in ("Indonesia", "Malaysia", "Thailand")
    ]
    rows = []
    for country, region, key in metadata:
        actual_values = actual_daily.get(key, {})
        forecast_values = forecast_daily.get(key, {})
        actual_total = sum(actual_values.values()) if set(actual_values) == week_days else None
        forecast_total = sum(forecast_values.values()) if set(forecast_values) == week_days else None
        error = None
        if actual_total is not None and forecast_total is not None and actual_total != 0:
            error = (forecast_total - actual_total) / actual_total * 100
        included_members = area_coverage[country] if key.startswith("Overall ") else []
        excluded_members = sorted(set(MAJOR_PLANTATION_REGIONS[country]) - set(included_members)) if key.startswith("Overall ") else []
        missing_members = [
            name for name in excluded_members
            if any(day not in forecast_daily.get(f"{country}|{name}", {}) for day in week_days)
        ]
        rows.append({
            "country": country, "region": region, "key": key,
            "actual": round(actual_total, 1) if actual_total is not None else None,
            "forecast": round(forecast_total, 1) if forecast_total is not None else None,
            "errorPercent": round(error, 1) if error is not None else None,
            "missingForecastRegions": missing_members,
            "includedRegions": included_members,
            "excludedRegions": excluded_members,
            "totalRegionCount": len(MAJOR_PLANTATION_REGIONS[country]) if key.startswith("Overall ") else None,
            "daily": [
                {"date": day, "actual": round(actual_values[day], 2) if day in actual_values else None,
                 "forecast": round(forecast_values[day], 2) if day in forecast_values else None}
                for day in sorted(week_days)
            ],
        })
    country_order = {"Indonesia": 0, "Malaysia": 1, "Thailand": 2}
    rows.sort(key=lambda row: (
        country_order.get(row["country"], 99),
        0 if row["key"].startswith("Overall ") else 1,
        row["region"],
    ))
    return {
        "weekStart": week_start.isoformat(), "weekEnd": week_end.isoformat(),
        "forecastRun": forecast_run, "errorDefinition": "(forecast - actual) / actual × 100",
        "regions": rows,
    }

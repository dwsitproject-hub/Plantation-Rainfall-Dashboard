"""Week-aligned cumulative rainfall series for a selectable 12-month cycle."""

from __future__ import annotations

import sys
import threading
from collections import defaultdict
from datetime import date, timedelta

from backend.all_regions.rainfall_common import MAJOR_PLANTATION_REGIONS, REGIONS, region_members
from backend.database import repository


def cycle_bounds(year: int, month: int) -> tuple[date, date]:
    return date(year, month, 1), date(year + 1, month, 1)


def cycle_label(year: int, month: int) -> str:
    start, end = cycle_bounds(year, month)
    return f"{start:%b %Y} – {(end - timedelta(days=1)):%b %Y}"


# Parsed daily rainfall of every country, held in memory: reading the full history
# takes seconds, building a chart from it milliseconds. After the weather data
# changes the old snapshot keeps serving until the new one is loaded in the background.
DATA_TYPES = ("historical_actual", "forecast")
CountryDays = dict[str, dict[date, list[float]]]  # logical region -> day -> values
_snapshot: tuple[str, dict[tuple[str, str], CountryDays]] | None = None
_snapshot_lock = threading.Lock()


def _read_country(country: str, data_type: str) -> CountryDays:
    peninsular = region_members("Malaysia", "Pennisular")
    days: CountryDays = defaultdict(lambda: defaultdict(list))
    for row in repository.read_weather("rainfall_mm", data_type, country=country):
        logical_region = ("Pennisular" if country == "Malaysia" and row["region"] in peninsular
                          else row["region"])
        try:
            days[logical_region][date.fromisoformat(row["date"])].append(float(row["rainfall_mm"]))
        except (ValueError, TypeError):
            continue
    return days


def load_snapshot(signature: str | None = None) -> tuple[str, dict]:
    """Load (now) every country's rainfall unless the snapshot is already current."""
    global _snapshot
    with _snapshot_lock:
        signature = signature or repository.weather_signature()
        if _snapshot and _snapshot[0] == signature:
            return _snapshot
        countries = sorted({country for country, _, _, _ in REGIONS})
        _snapshot = (signature, {(country, data_type): _read_country(country, data_type)
                                 for country in countries for data_type in DATA_TYPES})
        return _snapshot


def _reload_in_background(signature: str) -> None:
    try:
        load_snapshot(signature)
    except Exception as exc:
        print(f"Reloading cumulative rainfall data failed: {exc}", file=sys.stderr, flush=True)


def current_snapshot() -> tuple[str, dict]:
    """The loaded snapshot; starts a background reload when the weather data changed."""
    snapshot = _snapshot
    if snapshot is None:
        return load_snapshot()
    signature = repository.weather_signature()
    if snapshot[0] != signature and not _snapshot_lock.locked():
        threading.Thread(target=_reload_in_background, args=(signature,),
                         name="reload cumulative rainfall", daemon=True).start()
    return snapshot


def data_signature() -> str:
    """Version of the rainfall that cumulative responses are built from."""
    return current_snapshot()[0]


def country_days(country: str, data_type: str) -> CountryDays:
    return current_snapshot()[1].get((country, data_type), {})


def daily_values(country: str, region: str, *, historical: bool,
                 date_from: date | None = None) -> dict[date, float]:
    """Require every basket member for an overall daily value."""
    members = MAJOR_PLANTATION_REGIONS[country] if region.startswith("Overall ") else {region}
    days = country_days(country, "historical_actual" if historical else "forecast")
    member_days = [days.get(member, {}) for member in members]
    shared = set.intersection(*(set(values) for values in member_days)) if member_days else set()
    return {
        day: sum(sum(values[day]) / len(values[day]) for values in member_days) / len(members)
        for day in shared
        if date_from is None or day >= date_from
    }


def cumulative_days(values: dict[date, float], start: date, end: date) -> list[float] | None:
    totals = [0.0]
    for offset in range((end - start).days):
        value = values.get(start + timedelta(days=offset))
        if value is None:
            return None
        totals.append(totals[-1] + value)
    return totals


def build_cumulative_rainfall(region_key: str, start_month: int,
                              baseline_start: int, baseline_end: int) -> dict:
    if not 1 <= start_month <= 12:
        raise ValueError("Start month must be between 1 and 12.")
    if not 2000 <= baseline_start <= baseline_end <= date.today().year:
        raise ValueError("Historical year range is invalid.")
    if region_key.startswith("Overall "):
        country = region_key.removeprefix("Overall ")
        region = region_key
        if country not in MAJOR_PLANTATION_REGIONS:
            raise ValueError("Unknown region.")
    else:
        matching = [(country, region) for country, region, _, _ in REGIONS
                    if f"{country}|{region}" == region_key]
        if not matching:
            raise ValueError("Unknown region.")
        country, region = matching[0]

    actual_daily = daily_values(country, region, historical=True,
                                date_from=cycle_bounds(baseline_start, start_month)[0])
    forecast_daily = daily_values(country, region, historical=False)
    if not actual_daily:
        raise ValueError("Historical rainfall is unavailable for this region.")
    last_actual = max(actual_daily)
    current_start = last_actual.year if last_actual.month >= start_month else last_actual.year - 1
    if baseline_end >= current_start:
        raise ValueError("The historical range must end before the current period.")
    current_date, current_end = cycle_bounds(current_start, start_month)
    current_days = (current_end - current_date).days

    historical = []
    for year in range(baseline_start, baseline_end + 1):
        first, end = cycle_bounds(year, start_month)
        totals = cumulative_days(actual_daily, first, end)
        if totals is not None:
            historical.append({"year": year, "label": cycle_label(year, start_month),
                               "totals": totals, "total": totals[-1]})
    if not historical:
        raise ValueError("No complete historical periods are available for this selection.")

    # Stop at the first missing day so the line never leaps across a data gap.
    current_totals = [0.0]
    last_contiguous_actual = current_date - timedelta(days=1)
    for offset in range(current_days):
        day = current_date + timedelta(days=offset)
        value = actual_daily.get(day)
        if day > last_actual or value is None:
            break
        current_totals.append(current_totals[-1] + value)
        last_contiguous_actual = day
    actual_offset = len(current_totals) - 1

    combined_totals = list(current_totals)
    last_contiguous_forecast = last_contiguous_actual
    for offset in range(actual_offset, current_days):
        day = current_date + timedelta(days=offset)
        value = forecast_daily.get(day)
        if value is None:
            break
        combined_totals.append(combined_totals[-1] + value)
        last_contiguous_forecast = day
    forecast_offset = len(combined_totals) - 1

    offsets = set(range(0, current_days + 1, 7)) | {current_days}
    if actual_offset:
        offsets.add(actual_offset)
    points = []
    for offset in sorted(offsets):
        comparisons = [(item["year"], item["totals"][min(offset, len(item["totals"]) - 1)])
                       for item in historical]
        low = min(value for _, value in comparisons)
        high = max(value for _, value in comparisons)
        day = current_date + timedelta(days=max(0, offset - 1))
        points.append({
            "day": offset, "week": min((offset + 6) // 7, 53),
            "date": day.isoformat(),
            "average": round(sum(value for _, value in comparisons) / len(comparisons), 2),
            "min": round(low, 2), "max": round(high, 2),
            "minYears": [year for year, value in comparisons if value == low],
            "maxYears": [year for year, value in comparisons if value == high],
            "actual": round(current_totals[offset], 2) if offset <= actual_offset else None,
            "forecast": round(combined_totals[offset], 2)
                        if actual_offset <= offset <= forecast_offset else None,
        })
    return {
        "region": region_key, "startMonth": start_month,
        "currentStartYear": current_start, "currentLabel": cycle_label(current_start, start_month),
        "baselineStart": baseline_start, "baselineEnd": baseline_end,
        "historicalYears": [item["year"] for item in historical],
        "historicalPeriods": [{"year": item["year"], "label": item["label"],
                               "total": round(item["total"], 2)} for item in historical],
        "lastActual": last_actual.isoformat(),
        "actualThrough": last_contiguous_actual.isoformat() if actual_offset else None,
        "forecastThrough": last_contiguous_forecast.isoformat() if forecast_offset > actual_offset else None,
        "currentDays": current_days, "actualOffset": actual_offset,
        "forecastOffset": forecast_offset, "points": points,
    }

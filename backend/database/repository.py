"""All reads and writes of dashboard data. Nothing else in the backend issues SQL.

Weather rows are exchanged as plain dicts using the column names of the former
CSV files (country, region, date, rainfall_mm, data_type, retrieved_at_utc, ...)
so the calculation code is unchanged by the move to PostgreSQL.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import date, datetime, timezone
from typing import Any, Iterable, Iterator

from psycopg2.extras import execute_values

from backend.database.db import connect, transaction


METRIC_FIELDS = ("rainfall_mm", "temperature_mean_c", "soil_moisture_m3_m3")
SAP_COLUMNS = (
    "plant", "posting_date", "measurement_tool", "administrator", "rainy_day",
    "number_of_rain_events", "morning_rainfall_measurement",
    "afternoon_rainfall_measurement", "night_rainfall_measurement",
    "total_rainfall_measurement", "remarks", "morning_rain_duration_minutes",
    "afternoon_rain_duration_minutes", "night_rain_duration_minutes",
)


def _iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _region_ids(cursor) -> dict[tuple[str, str], int]:
    cursor.execute("SELECT country, region_name, region_id FROM weather_region")
    return {(country, region): region_id for country, region, region_id in cursor.fetchall()}


def _bump_version(cursor, name: str) -> None:
    """Mark data as changed (same transaction as the write) for the serving caches."""
    cursor.execute("""
        INSERT INTO data_version (name, version, changed_at) VALUES (%s, 1, now())
        ON CONFLICT (name) DO UPDATE SET version = data_version.version + 1, changed_at = now()
    """, (name,))


def data_version(name: str) -> int:
    with transaction() as cursor:
        cursor.execute("SELECT version FROM data_version WHERE name = %s", (name,))
        row = cursor.fetchone()
    return row[0] if row else 0


def _value(raw: Any) -> float | None:
    if raw is None or raw == "":
        return None
    return float(raw)


# ---------------------------------------------------------------------------
# Refresh lock
# ---------------------------------------------------------------------------

# Advisory lock key shared by every refresh process ("RAIN" in ASCII).
REFRESH_LOCK_KEY = 0x5241494E


class RefreshAlreadyRunning(RuntimeError):
    pass


@contextmanager
def refresh_lock() -> Iterator[None]:
    """Hold a session advisory lock so only one data refresh runs at a time.

    The lock belongs to a dedicated connection, so PostgreSQL releases it when
    the connection closes, including when the refresh process crashes or is
    killed. Raises RefreshAlreadyRunning if another session holds it.
    """
    connection = connect()
    connection.autocommit = True
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(%s)", (REFRESH_LOCK_KEY,))
            if not cursor.fetchone()[0]:
                # pg_locks shows a bigint key as classid (high 32 bits) + objid (low 32 bits).
                cursor.execute("""
                    SELECT l.pid, a.backend_start FROM pg_locks l
                    LEFT JOIN pg_stat_activity a USING (pid)
                    WHERE l.locktype = 'advisory' AND l.granted
                      AND l.classid = %s AND l.objid = %s AND l.objsubid = 1
                """, (REFRESH_LOCK_KEY >> 32, REFRESH_LOCK_KEY & 0xFFFFFFFF))
                holder = cursor.fetchone()
                detail = ""
                if holder and holder[1]:
                    detail = (f" (database session {holder[0]}, "
                              f"started {holder[1].astimezone():%Y-%m-%d %H:%M:%S})")
                raise RefreshAlreadyRunning(f"Another data refresh is already running{detail}.")
        yield
    finally:
        connection.close()


# ---------------------------------------------------------------------------
# Refresh audit
# ---------------------------------------------------------------------------

@contextmanager
def tracked_run(pipeline: str, step: str) -> Iterator[dict]:
    """Record a refresh step in refresh_run, independent of the data transaction.

    Callers may set ``run["rows"]``, ``run["message"]`` and ``run["sap_actual_cutoff"]``.
    """
    connection = connect()
    connection.autocommit = True
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO refresh_run (pipeline, step) VALUES (%s, %s) RETURNING run_id",
                (pipeline, step),
            )
            run = {"run_id": cursor.fetchone()[0], "rows": None, "message": None, "sap_actual_cutoff": None}
        try:
            yield run
        except BaseException as exc:
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE refresh_run SET status='failed', finished_at=now(), message=%s WHERE run_id=%s",
                    (f"{type(exc).__name__}: {exc}"[:2000], run["run_id"]),
                )
            raise
        with connection.cursor() as cursor:
            cursor.execute(
                """UPDATE refresh_run SET status='succeeded', finished_at=now(), rows_written=%s,
                       message=%s, sap_actual_cutoff=%s WHERE run_id=%s""",
                (run["rows"], run["message"], run["sap_actual_cutoff"], run["run_id"]),
            )
    finally:
        connection.close()


# ---------------------------------------------------------------------------
# Weather regions
# ---------------------------------------------------------------------------

def upsert_weather_regions(regions: list[dict]) -> int:
    with transaction() as cursor:
        execute_values(cursor, """
            INSERT INTO weather_region (country, region_name, rain_latitude, rain_longitude,
                temperature_latitude, temperature_longitude, is_major_plantation, display_order)
            VALUES %s
            ON CONFLICT (country, region_name) DO UPDATE SET
                rain_latitude = EXCLUDED.rain_latitude, rain_longitude = EXCLUDED.rain_longitude,
                temperature_latitude = EXCLUDED.temperature_latitude,
                temperature_longitude = EXCLUDED.temperature_longitude,
                is_major_plantation = EXCLUDED.is_major_plantation,
                display_order = EXCLUDED.display_order, updated_at = now()
        """, [(
            item["country"], item["region"], item["rain_latitude"], item["rain_longitude"],
            item["temperature_latitude"], item["temperature_longitude"],
            item["is_major_plantation"], item["display_order"],
        ) for item in regions])
        _bump_version(cursor, "weather")
    return len(regions)


# ---------------------------------------------------------------------------
# All Regions daily weather
# ---------------------------------------------------------------------------

def known_regions() -> set[tuple[str, str]]:
    with transaction() as cursor:
        return set(_region_ids(cursor))


def replace_weather(metric: str, data_type: str, rows: Iterable[dict],
                    *, date_from: str | None = None, date_to: str | None = None) -> int:
    """Atomically replace one metric/data-type slice (optionally a date range)."""
    if metric not in METRIC_FIELDS:
        raise ValueError(f"Unknown metric {metric}")
    with transaction() as cursor:
        regions = _region_ids(cursor)
        values = []
        for row in rows:
            value = _value(row.get(metric))
            if value is None:
                continue  # Open-Meteo returned no value for that day
            region_id = regions.get((row["country"], row["region"]))
            if region_id is None:
                raise ValueError(f"Unknown weather region {row['country']} / {row['region']}")
            values.append((
                region_id, metric, row["date"], data_type, value,
                row["latitude"], row["longitude"], row.get("soil_depth_cm") or None,
                row.get("soil_method") or None, row["source"], row["retrieved_at_utc"],
            ))
        scope = ["metric = %s", "data_type = %s"]
        parameters: list[Any] = [metric, data_type]
        if date_from:
            scope.append("obs_date >= %s"); parameters.append(date_from)
        if date_to:
            scope.append("obs_date <= %s"); parameters.append(date_to)
        cursor.execute(f"DELETE FROM weather_daily WHERE {' AND '.join(scope)}", parameters)
        execute_values(cursor, """
            INSERT INTO weather_daily (region_id, metric, obs_date, data_type, value, latitude,
                longitude, soil_depth_cm, method, source, retrieved_at)
            VALUES %s
        """, values, page_size=5000)
        _bump_version(cursor, "weather")
    return len(values)


def read_weather(metric: str, data_type: str | None = None, *, country: str | None = None,
                 date_from: str | None = None, date_to: str | None = None) -> list[dict]:
    """Return rows shaped like the former <metric>_historical/forecast/combined CSVs."""
    filters, parameters = ["w.metric = %s"], [metric]
    if data_type:
        filters.append("w.data_type = %s"); parameters.append(data_type)
    if country:
        filters.append("r.country = %s"); parameters.append(country)
    if date_from:
        filters.append("w.obs_date >= %s"); parameters.append(date_from)
    if date_to:
        filters.append("w.obs_date <= %s"); parameters.append(date_to)
    with transaction() as cursor:
        cursor.execute(f"""
            SELECT r.country, r.region_name, w.latitude, w.longitude, w.obs_date, w.value,
                   w.data_type, w.retrieved_at, w.source, w.soil_depth_cm, w.method
            FROM weather_daily w JOIN weather_region r USING (region_id)
            WHERE {' AND '.join(filters)}
            ORDER BY w.obs_date, r.country, r.region_name, w.data_type
        """, parameters)
        return [{
            "country": country, "region": region,
            "latitude": f"{latitude:.2f}", "longitude": f"{longitude:.2f}",
            "date": day.isoformat(), metric: value, "data_type": kind,
            "retrieved_at_utc": _iso_utc(retrieved), "source": source,
            "soil_depth_cm": depth or "", "soil_method": method or "",
        } for country, region, latitude, longitude, day, value, kind, retrieved, source, depth, method
            in cursor.fetchall()]


def weather_signature() -> str:
    """Changes whenever weather rows or forecast runs are written (see data_version)."""
    return f"weather-v{data_version('weather')}"


def check_schema() -> None:
    """Raise if the database is unreachable or the schema is missing (health check)."""
    with transaction() as cursor:
        cursor.execute("SELECT 1 FROM refresh_run LIMIT 1")
        cursor.execute("SELECT 1 FROM data_version LIMIT 1")


def plantation_signature() -> str:
    """Changes whenever plantation data is replaced (see data_version)."""
    return f"plantation-v{data_version('plantation')}"


# ---------------------------------------------------------------------------
# Forecast run archive
# ---------------------------------------------------------------------------

def read_snapshots() -> list[dict]:
    with transaction() as cursor:
        cursor.execute("""
            SELECT s.run_date, r.country, r.region_name, s.latitude, s.longitude, s.valid_date,
                   s.rainfall_mm, s.retrieved_at, s.source
            FROM forecast_snapshot s JOIN weather_region r USING (region_id)
            ORDER BY s.run_date, s.valid_date, r.country, r.region_name
        """)
        return [{
            "run_date": run_date.isoformat(), "country": country, "region": region,
            "latitude": f"{latitude:.2f}", "longitude": f"{longitude:.2f}",
            "date": valid.isoformat(), "rainfall_mm": rainfall, "data_type": "forecast",
            "retrieved_at_utc": _iso_utc(retrieved), "source": source,
        } for run_date, country, region, latitude, longitude, valid, rainfall, retrieved, source
            in cursor.fetchall()]


def save_snapshot(run_date: str, rows: list[dict], *, retention_runs: int,
                  only_if_missing: bool = False) -> int:
    """Archive one forecast run and keep only the newest ``retention_runs`` run dates."""
    with transaction() as cursor:
        if only_if_missing:
            cursor.execute("SELECT 1 FROM forecast_snapshot WHERE run_date = %s LIMIT 1", (run_date,))
            if cursor.fetchone():
                return 0
        regions = _region_ids(cursor)
        values = [(
            run_date, regions[(row["country"], row["region"])], row["date"],
            _value(row["rainfall_mm"]), row["latitude"], row["longitude"],
            row["source"], row["retrieved_at_utc"],
        ) for row in rows if _value(row.get("rainfall_mm")) is not None
            and (row["country"], row["region"]) in regions]
        if not values:
            return 0
        cursor.execute("DELETE FROM forecast_snapshot WHERE run_date = %s", (run_date,))
        execute_values(cursor, """
            INSERT INTO forecast_snapshot (run_date, region_id, valid_date, rainfall_mm,
                latitude, longitude, source, retrieved_at) VALUES %s
        """, values, page_size=5000)
        cursor.execute("""
            DELETE FROM forecast_snapshot WHERE run_date NOT IN (
                SELECT DISTINCT run_date FROM forecast_snapshot ORDER BY run_date DESC LIMIT %s)
        """, (retention_runs,))
        _bump_version(cursor, "weather")
    return len(values)


# ---------------------------------------------------------------------------
# In-house plantation
# ---------------------------------------------------------------------------

def load_plantation(run_id: int, *, locations: list[dict], forecast: dict, workbook_name: str,
                    sap_rows: dict[str, list[dict]], rainfall: dict) -> None:
    """Replace all plantation data in a single transaction (all or nothing)."""
    as_of = date.fromisoformat(forecast["as_of_date"])
    with transaction() as cursor:
        execute_values(cursor, """
            INSERT INTO estate (estate_id, company_code, estate_code, estate_name, province,
                business_region, latitude, longitude, workbook_order, is_active)
            VALUES %s
            ON CONFLICT (estate_id) DO UPDATE SET
                company_code = EXCLUDED.company_code, estate_code = EXCLUDED.estate_code,
                estate_name = EXCLUDED.estate_name, province = EXCLUDED.province,
                business_region = EXCLUDED.business_region, latitude = EXCLUDED.latitude,
                longitude = EXCLUDED.longitude, workbook_order = EXCLUDED.workbook_order,
                is_active = true, updated_at = now()
        """, [(
            item["id"], item["company"], item["estate"], item["name"], item["region"],
            item["business_region"], item["latitude"], item["longitude"], order, True,
        ) for order, item in enumerate(locations)])
        cursor.execute("UPDATE estate SET is_active = false, updated_at = now() WHERE NOT (estate_id = ANY(%s))",
                       ([item["id"] for item in locations],))

        # Environments no longer pulled (DEV) are removed with their history.
        cursor.execute("DELETE FROM sap_rainfall_raw WHERE NOT (environment = ANY(%s))", (list(sap_rows),))
        for environment, rows in sap_rows.items():
            cursor.execute("DELETE FROM sap_rainfall_raw WHERE environment = %s", (environment,))
            execute_values(cursor, f"""
                INSERT INTO sap_rainfall_raw (run_id, environment, company_code, estate_code,
                    {', '.join('posting_date_raw' if c == 'posting_date' else c for c in SAP_COLUMNS)},
                    extra_fields) VALUES %s
            """, [(
                run_id, environment, (row.get("company_code") or "").strip(),
                (row.get("estate") or "").strip(),
                *[row.get(column) for column in SAP_COLUMNS],
                json.dumps({key: value for key, value in row.items()
                            if key not in SAP_COLUMNS and key not in ("company_code", "estate")}),
            ) for row in rows], page_size=5000)

        cursor.execute("DELETE FROM plantation_rainfall_daily")
        execute_values(cursor, """
            INSERT INTO plantation_rainfall_daily (run_id, company_code, estate_code, plant,
                measurement_tool, obs_date, rainfall_mm) VALUES %s
        """, [(run_id, *record) for record in rainfall["records"]], page_size=5000)

        cursor.execute("""
            INSERT INTO plantation_forecast_run (run_id, as_of_date, generated_at, past_days,
                forecast_days, source, source_url, workbook_name) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        """, (run_id, as_of, forecast["generated_utc"], forecast["past_days"],
              forecast["forecast_days"], forecast["source"], forecast["source_url"], workbook_name))
        execute_values(cursor, """
            INSERT INTO plantation_forecast_location (run_id, estate_id, latitude, longitude, timezone)
            VALUES %s
        """, [(run_id, item["id"], item["latitude"], item["longitude"], item.get("timezone"))
              for item in forecast["forecasts"]])
        cursor.execute("DELETE FROM plantation_forecast_daily")
        daily = []
        for item in forecast["forecasts"]:
            daily += [(item["id"], day, "recent_actual", value, run_id)
                      for day, value in zip(item["actual_dates"], item["actual_rainfall_mm"])]
            daily += [(item["id"], day, "forecast", value, run_id)
                      for day, value in zip(item["forecast_dates"], item["forecast_rainfall_mm"])]
        execute_values(cursor, """
            INSERT INTO plantation_forecast_daily (estate_id, valid_date, data_type, rainfall_mm, run_id)
            VALUES %s
        """, daily, page_size=5000)
        _bump_version(cursor, "plantation")


def stored_sap_rows(environment: str) -> list[dict]:
    """SAP rows kept from earlier pulls, shaped like the rows of a freshly pulled CSV."""
    columns = ", ".join("posting_date_raw" if column == "posting_date" else column for column in SAP_COLUMNS)
    with transaction() as cursor:
        cursor.execute(f"""
            SELECT company_code, estate_code, {columns}, extra_fields FROM sap_rainfall_raw
            WHERE environment = %s ORDER BY sap_row_id
        """, (environment,))
        stored = cursor.fetchall()
    rows = []
    for company, estate, *values, extra in stored:
        row = {"company_code": company, "estate": estate}
        row.update({column: value for column, value in zip(SAP_COLUMNS, values) if value is not None})
        row.update(extra or {})
        rows.append(row)
    return rows


def plantations_document() -> dict:
    """Same shape as the former dist/data/plantations.json."""
    with transaction() as cursor:
        cursor.execute("SELECT workbook_name FROM plantation_forecast_run ORDER BY run_id DESC LIMIT 1")
        latest = cursor.fetchone()
        cursor.execute("""
            SELECT estate_id, company_code, estate_code, estate_name, province, business_region,
                   latitude, longitude
            FROM estate WHERE is_active ORDER BY workbook_order
        """)
        locations = [{
            "id": estate_id, "company": company.strip(), "estate": estate, "name": name,
            "region": province, "business_region": business, "latitude": float(latitude),
            "longitude": float(longitude),
        } for estate_id, company, estate, name, province, business, latitude, longitude in cursor.fetchall()]
    if latest is None or not locations:
        raise LookupError("No plantation data has been loaded yet. Run the data refresh.")
    return {"source": latest[0], "locations": locations}


def forecast_document() -> dict:
    """Same shape as the former dist/data/forecast_14day.json."""
    with transaction() as cursor:
        cursor.execute("""
            SELECT run_id, as_of_date, generated_at, past_days, forecast_days, source, source_url
            FROM plantation_forecast_run ORDER BY run_id DESC LIMIT 1
        """)
        run = cursor.fetchone()
        if run is None:
            raise LookupError("No plantation forecast has been loaded yet. Run the data refresh.")
        run_id, as_of, generated, past_days, forecast_days, source, source_url = run
        cursor.execute("""
            SELECT l.estate_id, l.latitude, l.longitude, l.timezone
            FROM plantation_forecast_location l JOIN estate e USING (estate_id)
            WHERE l.run_id = %s ORDER BY e.workbook_order
        """, (run_id,))
        locations = cursor.fetchall()
        cursor.execute("""
            SELECT estate_id, valid_date, data_type, rainfall_mm FROM plantation_forecast_daily
            ORDER BY estate_id, valid_date
        """)
        series: dict[str, dict[str, list]] = {}
        for estate_id, valid, kind, value in cursor.fetchall():
            bucket = series.setdefault(estate_id, {"recent_actual": [], "forecast": []})
            bucket[kind].append((valid.isoformat(), value))
    forecasts = []
    for estate_id, latitude, longitude, zone in locations:
        bucket = series.get(estate_id, {"recent_actual": [], "forecast": []})
        actual, ahead = bucket["recent_actual"], bucket["forecast"]
        forecasts.append({
            "id": estate_id, "latitude": float(latitude), "longitude": float(longitude), "timezone": zone,
            "actual_dates": [d for d, _ in actual], "actual_rainfall_mm": [v for _, v in actual],
            "forecast_dates": [d for d, _ in ahead], "forecast_rainfall_mm": [v for _, v in ahead],
            "dates": [d for d, _ in ahead], "rainfall_mm": [v for _, v in ahead],
        })
    return {
        "source": source, "source_url": source_url,
        "generated_utc": _iso_utc(generated), "as_of_date": as_of.isoformat(),
        "past_days": past_days, "forecast_days": forecast_days, "forecasts": forecasts,
    }


def rainfall_document() -> dict:
    """Same shape as the former dist/data/rainfall_daily.json."""
    with transaction() as cursor:
        cursor.execute("""
            SELECT run_id, started_at, sap_actual_cutoff FROM refresh_run
            WHERE pipeline = 'plantation' AND step = 'load' AND status = 'succeeded'
            ORDER BY run_id DESC LIMIT 1
        """)
        run = cursor.fetchone()
        if run is None:
            raise LookupError("No SAP rainfall has been loaded yet. Run the data refresh.")
        cursor.execute("""
            SELECT company_code, estate_code, plant, measurement_tool, obs_date, rainfall_mm
            FROM plantation_rainfall_daily
            ORDER BY company_code, estate_code, measurement_tool, obs_date, record_id
        """)
        records = [[company, estate, plant, tool, day.isoformat(), value]
                   for company, estate, plant, tool, day, value in cursor.fetchall()]
    companies: dict[str, set[str]] = {}
    for company, estate, *_ in records:
        companies.setdefault(company, set()).add(estate)
    return {
        "generated": run[1].astimezone().isoformat(timespec="seconds"),
        "sap_actual_cutoff": run[2].isoformat() if run[2] else None,
        "fields": ["company", "estate", "plant", "tool", "date", "rainfall_mm"],
        "companies": {key: sorted(value) for key, value in sorted(companies.items())},
        "records": records,
    }


# ---------------------------------------------------------------------------
# Serving cache
# ---------------------------------------------------------------------------

def read_cache(cache_key: str, signature: str) -> tuple[bytes, bytes] | None:
    with transaction() as cursor:
        cursor.execute("SELECT body, body_gzip FROM dashboard_cache WHERE cache_key=%s AND signature=%s",
                       (cache_key, signature))
        row = cursor.fetchone()
    return (bytes(row[0]), bytes(row[1])) if row else None


def write_cache(cache_key: str, signature: str, body: bytes, compressed: bytes) -> None:
    with transaction() as cursor:
        cursor.execute("""
            INSERT INTO dashboard_cache (cache_key, signature, body, body_gzip) VALUES (%s,%s,%s,%s)
            ON CONFLICT (cache_key) DO UPDATE SET signature=EXCLUDED.signature, body=EXCLUDED.body,
                body_gzip=EXCLUDED.body_gzip, created_at=now()
        """, (cache_key, signature, body, compressed))

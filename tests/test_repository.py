"""Round trips through backend/database/repository.py on a throwaway PostgreSQL."""

from __future__ import annotations

import psycopg2.errors
import pytest

from backend.all_regions.rainfall_common import REGIONS
from backend.database import init_db, repository
from backend.database.db import transaction

RETRIEVED = "2026-10-16T01:02:03+00:00"


def weather_row(date, value, *, country="Indonesia", region="Riau", metric="rainfall_mm"):
    return {
        "country": country, "region": region, "latitude": "0.50", "longitude": "101.55",
        "date": date, metric: value, "source": "Open-Meteo test", "retrieved_at_utc": RETRIEVED,
    }


def scalar(sql, parameters=()):
    with transaction() as cursor:
        cursor.execute(sql, parameters)
        return cursor.fetchone()[0]


def test_server_is_postgresql_18(db):
    assert db["version"] >= 180000


def test_schema_is_idempotent_and_seeds_every_region(db):
    init_db.main()  # running again must not fail or duplicate regions
    assert repository.known_regions() == {(country, region) for country, region, *_ in REGIONS}


# --- All Regions weather --------------------------------------------------

def test_weather_round_trip(db):
    written = repository.replace_weather("rainfall_mm", "historical_actual", [
        weather_row("2024-01-01", 3.5),
        weather_row("2024-01-02", ""),  # Open-Meteo gave no value: not stored
        weather_row("2024-01-01", 7.25, country="Malaysia", region="Sabah"),
    ])
    assert written == 2

    rows = repository.read_weather("rainfall_mm", "historical_actual")
    assert rows == [
        {"country": "Indonesia", "region": "Riau", "latitude": "0.50", "longitude": "101.55",
         "date": "2024-01-01", "rainfall_mm": 3.5, "data_type": "historical_actual",
         "retrieved_at_utc": RETRIEVED, "source": "Open-Meteo test", "soil_depth_cm": "", "soil_method": ""},
        {"country": "Malaysia", "region": "Sabah", "latitude": "0.50", "longitude": "101.55",
         "date": "2024-01-01", "rainfall_mm": 7.25, "data_type": "historical_actual",
         "retrieved_at_utc": RETRIEVED, "source": "Open-Meteo test", "soil_depth_cm": "", "soil_method": ""},
    ]
    assert [row["region"] for row in repository.read_weather("rainfall_mm", country="Malaysia")] == ["Sabah"]
    assert repository.read_weather("rainfall_mm", "forecast") == []


def test_replace_weather_only_replaces_its_slice(db):
    repository.replace_weather("rainfall_mm", "forecast", [
        weather_row(day, 1.0) for day in ("2024-01-01", "2024-01-02", "2024-01-03")])
    repository.replace_weather("temperature_mean_c", "forecast", [
        weather_row("2024-01-02", 27.0, metric="temperature_mean_c")])

    repository.replace_weather("rainfall_mm", "forecast", [weather_row("2024-01-02", 9.0)],
                               date_from="2024-01-02", date_to="2024-01-02")

    rain = {row["date"]: row["rainfall_mm"] for row in repository.read_weather("rainfall_mm")}
    assert rain == {"2024-01-01": 1.0, "2024-01-02": 9.0, "2024-01-03": 1.0}
    assert len(repository.read_weather("temperature_mean_c")) == 1
    assert [row["date"] for row in repository.read_weather(
        "rainfall_mm", date_from="2024-01-02", date_to="2024-01-03")] == ["2024-01-02", "2024-01-03"]


def test_replace_weather_rolls_back_on_unknown_region(db):
    repository.replace_weather("rainfall_mm", "forecast", [weather_row("2024-01-01", 1.0)])
    with pytest.raises(ValueError, match="Unknown weather region"):
        repository.replace_weather("rainfall_mm", "forecast", [
            weather_row("2024-01-01", 2.0), weather_row("2024-01-01", 2.0, region="Atlantis")])
    assert [row["rainfall_mm"] for row in repository.read_weather("rainfall_mm")] == [1.0]


def test_replace_weather_rejects_unknown_metric(db):
    with pytest.raises(ValueError, match="Unknown metric"):
        repository.replace_weather("snowfall_cm", "forecast", [])


def test_soil_moisture_keeps_depth_and_method(db):
    row = weather_row("2024-01-01", 0.31, metric="soil_moisture_m3_m3")
    row.update(soil_depth_cm="28-100", soil_method="weighted mean")
    repository.replace_weather("soil_moisture_m3_m3", "historical_actual", [row])
    stored = repository.read_weather("soil_moisture_m3_m3")[0]
    assert (stored["soil_depth_cm"], stored["soil_method"]) == ("28-100", "weighted mean")


# --- Forecast snapshots ---------------------------------------------------

def test_snapshots_keep_only_the_newest_runs(db):
    for run_date in ("2026-10-13", "2026-10-14", "2026-10-15"):
        assert repository.save_snapshot(run_date, [weather_row("2026-10-20", 4.0)], retention_runs=2) == 1

    assert repository.save_snapshot("2026-10-15", [weather_row("2026-10-20", 8.0)],
                                    retention_runs=2, only_if_missing=True) == 0
    snapshots = repository.read_snapshots()
    assert [(row["run_date"], row["rainfall_mm"]) for row in snapshots] == [
        ("2026-10-14", 4.0), ("2026-10-15", 4.0)]
    assert snapshots[0]["data_type"] == "forecast"
    assert snapshots[0]["retrieved_at_utc"] == RETRIEVED


def test_weather_signature_changes_when_data_changes(db):
    before = repository.weather_signature()
    repository.replace_weather("rainfall_mm", "forecast", [weather_row("2024-01-01", 1.0)])
    after_weather = repository.weather_signature()
    repository.save_snapshot("2026-10-15", [weather_row("2026-10-20", 4.0)], retention_runs=5)
    assert len({before, after_weather, repository.weather_signature()}) == 3


# --- Refresh audit --------------------------------------------------------

def test_tracked_run_records_success_and_failure(db):
    with repository.tracked_run("all_regions", "historical_rainfall") as run:
        run["rows"], run["message"] = 42, "ok"
    with pytest.raises(RuntimeError):
        with repository.tracked_run("all_regions", "forecast_rainfall"):
            raise RuntimeError("Open-Meteo down")

    with transaction() as cursor:
        cursor.execute("SELECT step, status, rows_written, message, finished_at IS NOT NULL "
                       "FROM refresh_run ORDER BY run_id")
        assert cursor.fetchall() == [
            ("historical_rainfall", "succeeded", 42, "ok", True),
            ("forecast_rainfall", "failed", None, "RuntimeError: Open-Meteo down", True),
        ]


# --- In-house plantation --------------------------------------------------

LOCATIONS = [
    {"id": "TH3A", "company": "TH", "estate": "3A", "name": "Kebun A", "region": "Riau",
     "business_region": "Region 1", "latitude": 0.512345, "longitude": 101.456789},
    {"id": "CR3A", "company": "CR", "estate": "3A", "name": "Kebun B", "region": "Riau",
     "business_region": "Region 2", "latitude": -0.25, "longitude": 102.0},
]


def forecast_for(locations):
    return {
        "source": "Open-Meteo Forecast API", "source_url": "https://open-meteo.com/en/docs",
        "generated_utc": RETRIEVED, "as_of_date": "2026-10-16", "past_days": 2, "forecast_days": 2,
        "forecasts": [{
            "id": item["id"], "latitude": item["latitude"], "longitude": item["longitude"],
            "timezone": "Asia/Jakarta",
            "actual_dates": ["2026-10-14", "2026-10-15"], "actual_rainfall_mm": [1.5, None],
            "forecast_dates": ["2026-10-16", "2026-10-17"], "forecast_rainfall_mm": [3.0, 0.0],
            "dates": ["2026-10-16", "2026-10-17"], "rainfall_mm": [3.0, 0.0],
        } for item in locations],
    }


RAINFALL = {"records": [
    ["CR", "3A", "P001", "Ombrometer", "2026-10-14", 4.0],
    ["TH", "3A", "", "Unspecified", "2026-10-14", 0.0],
    ["TH", "3A", "", "Unspecified", "2026-10-15", 12.5],
]}

SAP_ROWS = {
    "dev": [{"company_code": " TH ", "estate": "3A", "posting_date": "14.10.2026",
             "total_rainfall_measurement": "0", "undocumented_field": "x"}],
    "prod": [{"company_code": "CR", "estate": "3A", "posting_date": "10.14.2026",
              "total_rainfall_measurement": "4"}],
}


def load(locations, *, cutoff="2026-10-15"):
    with repository.tracked_run("plantation", "load") as run:
        repository.load_plantation(run["run_id"], locations=locations, forecast=forecast_for(locations),
                                   workbook_name="estates.xlsx", sap_rows=SAP_ROWS, rainfall=RAINFALL)
        run["sap_actual_cutoff"] = cutoff
    return run["run_id"]


def test_plantation_documents_are_empty_until_loaded(db):
    for document in (repository.plantations_document, repository.forecast_document,
                     repository.rainfall_document):
        with pytest.raises(LookupError, match="Run the data refresh"):
            document()


def test_plantation_round_trip(db):
    before = repository.plantation_signature()
    load(LOCATIONS)

    assert repository.plantations_document() == {"source": "estates.xlsx", "locations": LOCATIONS}
    assert repository.forecast_document() == forecast_for(LOCATIONS)
    rainfall = repository.rainfall_document()
    assert rainfall["records"] == RAINFALL["records"]
    assert rainfall["sap_actual_cutoff"] == "2026-10-15"
    assert rainfall["companies"] == {"CR": ["3A"], "TH": ["3A"]}
    assert repository.plantation_signature() != before  # serving caches rebuild

    with transaction() as cursor:
        cursor.execute("SELECT environment, company_code, posting_date_raw, extra_fields "
                       "FROM sap_rainfall_raw ORDER BY environment")
        assert cursor.fetchall() == [
            ("dev", "TH", "14.10.2026", {"undocumented_field": "x"}),
            ("prod", "CR", "10.14.2026", {}),
        ]


def test_reload_replaces_plantation_data_and_retires_removed_estates(db):
    load(LOCATIONS)
    first = repository.plantation_signature()
    load(LOCATIONS[:1])

    assert [item["id"] for item in repository.plantations_document()["locations"]] == ["TH3A"]
    assert [item["id"] for item in repository.forecast_document()["forecasts"]] == ["TH3A"]
    assert repository.rainfall_document()["records"] == RAINFALL["records"]  # replaced, not appended
    assert scalar("SELECT count(*) FROM sap_rainfall_raw") == 2
    assert scalar("SELECT is_active FROM estate WHERE estate_id = 'CR3A'") is False
    assert repository.plantation_signature() != first


def test_failed_plantation_load_keeps_the_previous_data(db):
    load(LOCATIONS)
    signature = repository.plantation_signature()
    broken = forecast_for(LOCATIONS)
    broken["forecasts"][0]["id"] = "XX9Z"  # unknown estate: foreign key violation
    with pytest.raises(psycopg2.errors.ForeignKeyViolation):
        with repository.tracked_run("plantation", "load") as run:
            repository.load_plantation(run["run_id"], locations=LOCATIONS, forecast=broken,
                                       workbook_name="new.xlsx", sap_rows={"dev": []}, rainfall={"records": []})

    assert repository.plantations_document()["source"] == "estates.xlsx"
    assert repository.rainfall_document()["records"] == RAINFALL["records"]
    assert scalar("SELECT count(*) FROM sap_rainfall_raw") == 2
    assert repository.plantation_signature() == signature  # rolled back with the data


# --- Serving cache --------------------------------------------------------

def test_cache_round_trip(db):
    assert repository.read_cache("api-data", "sig-1") is None
    repository.write_cache("api-data", "sig-1", b"{}", b"gz-1")
    assert repository.read_cache("api-data", "sig-1") == (b"{}", b"gz-1")

    repository.write_cache("api-data", "sig-2", b"{\"a\":1}", b"gz-2")
    assert repository.read_cache("api-data", "sig-1") is None  # stale signature
    assert repository.read_cache("api-data", "sig-2") == (b"{\"a\":1}", b"gz-2")

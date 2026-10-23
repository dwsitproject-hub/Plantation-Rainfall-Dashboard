"""SAP PROD history: each refresh merges its pull into the PROD rows stored before."""

from __future__ import annotations

from backend.plantation.update_all_rainfall_dashboard import merge_sap_rows, write_csv, read_csv


def row(day, mm, company="CR", estate="3A", tool="Ombrometer"):
    return {"company_code": company, "estate": estate, "posting_date": day,
            "measurement_tool": tool, "total_rainfall_measurement": mm}


def test_new_pull_wins_for_its_days_and_older_days_are_kept():
    stored = [row("09.30.2026", "5"), row("10.01.2026", "0"), row("10.01.2026", "3", estate="3B")]
    pulled = [row("10.01.2026", "8"), row("10.01.2026", "2", tool="Manual"), row("10.02.2026", "1")]

    merged = merge_sap_rows(stored, pulled)

    assert row("09.30.2026", "5") in merged                       # September kept after PROD dropped it
    assert row("10.01.2026", "0") not in merged                    # corrected by the new pull
    assert row("10.01.2026", "3", estate="3B") in merged           # estate not in the pull for that day
    assert [r for r in merged if r["posting_date"] == "10.01.2026" and r["estate"] == "3A"] == pulled[:2]
    assert len(merged) == 5


def test_estate_codes_are_compared_without_spaces():
    merged = merge_sap_rows([row("10.01.2026", "0", company=" CR ")], [row("10.01.2026", "4")])
    assert merged == [row("10.01.2026", "4")]


def test_merged_rows_round_trip_through_csv(tmp_path):
    rows = [row("10.01.2026", "4"), {**row("10.02.2026", "1"), "remarks": "late entry"}]
    write_csv(rows, tmp_path / "prod.csv")
    assert read_csv(tmp_path / "prod.csv") == [{**rows[0], "remarks": ""}, rows[1]]


def test_no_current_month_data_gives_a_warning_not_a_stop():
    from datetime import date
    from backend.plantation.update_all_rainfall_dashboard import freshness_warning
    assert freshness_warning(date(2026, 10, 21), date(2026, 10, 23)) is None
    warning = freshness_warning(date(2026, 9, 30), date(2026, 10, 23))
    assert warning.startswith("WARNING: SAP PROD returned no rain readings for October 2026 (latest: 30 Sep 2026)")
    assert "Loading anyway" in warning
    assert "latest: none" in freshness_warning(None, date(2026, 10, 23))


def test_previous_months_are_pulled_only_when_due():
    from datetime import datetime, timedelta, timezone
    from backend.plantation.update_all_rainfall_dashboard import history_due
    wib = timezone(timedelta(hours=7))
    now = datetime(2026, 10, 23, 12, 0, tzinfo=wib)
    assert "no SAP history" in history_due(None, now, 7)
    assert history_due(datetime(2026, 10, 20, 6, 30, tzinfo=wib), now, 7) is None
    assert "8 days ago" in history_due(datetime(2026, 10, 15, 6, 30, tzinfo=wib), now, 7)
    first_of_month = datetime(2026, 11, 1, 6, 30, tzinfo=wib)
    assert "new month" in history_due(datetime(2026, 10, 31, 12, 0, tzinfo=wib), first_of_month, 7)
    # Stored in UTC, compared in local time: 31 Oct 20:00 UTC is already 1 Nov in Jakarta.
    assert history_due(datetime(2026, 10, 31, 20, 0, tzinfo=timezone.utc), first_of_month, 7) is None


def test_current_month_wins_over_history_and_history_over_stored():
    stored = [row("30.09.2026", "1"), row("01.08.2026", "2")]
    history = [row("30.09.2026", "5")]                        # September is now final
    current = [row("30.09.2026", "9"), row("01.10.2026", "3")]  # overlap: current wins
    merged = merge_sap_rows(merge_sap_rows(stored, history), current)
    assert sorted((r["posting_date"], r["total_rainfall_measurement"]) for r in merged) == [
        ("01.08.2026", "2"), ("01.10.2026", "3"), ("30.09.2026", "9")]

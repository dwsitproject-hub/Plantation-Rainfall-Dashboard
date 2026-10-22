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

"""Legacy file import: Johor/Pahang/Perak rows fold into Pennisular like the dashboard does."""

from __future__ import annotations

from backend.database.import_legacy_files import fold_combined_regions


def row(region, value, *, run_date="2026-09-20", day="2026-09-21", lat="2.00"):
    return {"run_date": run_date, "country": "Malaysia", "region": region, "latitude": lat, "longitude": "102.00",
            "date": day, "rainfall_mm": str(value), "data_type": "forecast"}


def test_members_are_averaged_when_no_direct_row():
    folded = fold_combined_regions([row("Johor", 3), row("Pahang", 6, lat="4.00"), row("Perak", 9), row("Sabah", 1)])
    pennisular = [r for r in folded if r["region"] == "Pennisular"]
    assert len(pennisular) == 1 and pennisular[0]["rainfall_mm"] == 6.0
    assert pennisular[0]["latitude"] == "2.67"
    assert {r["region"] for r in folded} == {"Pennisular", "Sabah"}


def test_direct_pennisular_row_wins_and_members_are_dropped():
    folded = fold_combined_regions([row("Pennisular", 5), row("Johor", 100)])
    assert [(r["region"], r["rainfall_mm"]) for r in folded] == [("Pennisular", "5")]


def test_runs_and_dates_are_folded_separately():
    folded = fold_combined_regions([row("Johor", 2, run_date="2026-09-20"), row("Johor", 8, run_date="2026-09-21"),
                                    row("Perak", 4, day="2026-09-22")])
    assert sorted((r["run_date"], r["date"], r["rainfall_mm"]) for r in folded) == [
        ("2026-09-20", "2026-09-21", 2.0), ("2026-09-20", "2026-09-22", 4.0), ("2026-09-21", "2026-09-21", 8.0)]

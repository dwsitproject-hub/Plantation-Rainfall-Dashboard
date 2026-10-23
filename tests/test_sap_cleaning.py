"""SAP cleaning rules in backend/plantation/build_dashboard_data.py."""

from __future__ import annotations

import csv
import json
from datetime import date, timedelta

import pytest

from backend.plantation.build_dashboard_data import build, detect_date_format, parse_posting_date

FIELDS = ["company_code", "estate", "plant", "posting_date", "measurement_tool", "total_rainfall_measurement"]


def write_sap_csv(path, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(FIELDS)
        writer.writerows(rows)
    return path


def sap_row(posting_date, rainfall, company="CR", estate="3A", tool="Ombrometer"):
    return [company, estate, "P001", posting_date, tool, rainfall]


# --- Date-order detection -------------------------------------------------

def test_day_over_12_means_dmy(tmp_path):
    source = write_sap_csv(tmp_path / "s.csv", [sap_row("05.03.2024", "1"), sap_row("25.03.2024", "1")])
    assert detect_date_format(source, "auto") == "%d.%m.%Y"


def test_second_part_over_12_means_mdy(tmp_path):
    source = write_sap_csv(tmp_path / "s.csv", [sap_row("03.05.2024", "1"), sap_row("03.25.2024", "1")])
    assert detect_date_format(source, "auto") == "%m.%d.%Y"


def test_mixed_date_orders_are_rejected(tmp_path):
    source = write_sap_csv(tmp_path / "s.csv", [sap_row("25.03.2024", "1"), sap_row("03.25.2024", "1")])
    with pytest.raises(ValueError, match="mixed or invalid"):
        detect_date_format(source, "auto")


def test_ambiguous_dates_need_an_explicit_order(tmp_path):
    source = write_sap_csv(tmp_path / "s.csv", [sap_row("05.03.2024", "1"), sap_row("not-a-date", "1")])
    with pytest.raises(ValueError, match="--date-order"):
        detect_date_format(source, "auto")
    assert detect_date_format(source, "dmy") == "%d.%m.%Y"
    assert detect_date_format(source, "mdy") == "%m.%d.%Y"


# --- Corrupt posting dates ------------------------------------------------

@pytest.mark.parametrize("raw", [
    "01.01.0100",                               # SAP placeholder year
    "01.01.1999",                               # before the supported range
    f"01.01.{date.today().year + 2}",           # too far in the future
    "31.02.2024",                               # impossible date
    "",
    "2024-03-15",                               # wrong separator
])
def test_corrupt_posting_dates_are_ignored(raw):
    assert parse_posting_date(raw, "%d.%m.%Y") is None


def test_valid_posting_date_is_parsed():
    assert parse_posting_date(" 15.03.2024 ", "%d.%m.%Y") == date(2024, 3, 15)
    assert parse_posting_date("03.15.2024", "%m.%d.%Y") == date(2024, 3, 15)


# --- Full build -----------------------------------------------------------

def test_build_applies_every_cleaning_rule(tmp_path, capsys):
    future = (date.today() + timedelta(days=5)).strftime("%m.%d.%Y")
    historical = write_sap_csv(tmp_path / "historical.csv", [
        sap_row("15.03.2024", "12,5"),                          # comma decimal
        sap_row("15.03.2024", "0", company="TH", estate="3J"),  # zero rain on a real date: kept
        sap_row("16.03.2024", "0"),                             # all-zero placeholder date ...
        sap_row("16.03.2024", "0", company="TH", estate="3J"),  # ... removed for every estate
        sap_row("01.01.0100", "5"),                             # corrupt year: skipped, not fatal
        sap_row("17.03.2024", ""),                              # no rainfall value: skipped
    ])
    current = write_sap_csv(tmp_path / "current.csv", [
        sap_row("03.20.2024", "4"),
        sap_row("03.20.2024", "4"),                             # exact duplicate: removed
        sap_row("03.20.2024", "0", company="TH", estate="3J"),
        sap_row(future, "9"),                                   # future placeholder: removed
    ])
    output = tmp_path / "out" / "rainfall_daily.json"

    assert build([historical, current], output) == 4

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["records"] == [
        ["CR", "3A", "P001", "Ombrometer", "2024-03-15", 12.5],
        ["CR", "3A", "P001", "Ombrometer", "2024-03-20", 4.0],
        ["TH", "3J", "P001", "Ombrometer", "2024-03-15", 0.0],
        ["TH", "3J", "P001", "Ombrometer", "2024-03-20", 0.0],
    ]
    assert payload["sap_actual_cutoff"] == "2024-03-20"
    assert payload["companies"] == {"CR": ["3A"], "TH": ["3J"]}
    out = capsys.readouterr().out
    assert "Detected DD.MM.YYYY in historical.csv" in out
    assert "Detected MM.DD.YYYY in current.csv" in out
    assert "skipped 1 SAP row(s) with invalid posting dates" in out
    assert "posting_date='01.01.0100'" in out


def test_duplicates_are_removed_across_files_but_distinct_tools_are_kept(tmp_path):
    historical = write_sap_csv(tmp_path / "historical.csv", [
        sap_row("15.03.2024", "3", tool="Ombrometer"),
        sap_row("15.03.2024", "3", tool="Manual gauge"),
    ])
    # The same raw row in both files is kept once.
    current = write_sap_csv(tmp_path / "current.csv", [sap_row("15.03.2024", "3", tool="Ombrometer")])
    output = tmp_path / "rainfall_daily.json"

    assert build([historical, current], output) == 2
    tools = [record[3] for record in json.loads(output.read_text(encoding="utf-8"))["records"]]
    assert tools == ["Manual gauge", "Ombrometer"]


def test_build_fails_without_any_rainfall_above_zero(tmp_path):
    historical = write_sap_csv(tmp_path / "historical.csv", [sap_row("15.03.2024", "0")])
    current = write_sap_csv(tmp_path / "current.csv", [sap_row("03.15.2024", "0")])
    with pytest.raises(ValueError, match="no portfolio date with rainfall above zero"):
        build([historical, current], tmp_path / "out.json")


def test_build_fails_when_no_usable_records(tmp_path):
    historical = write_sap_csv(tmp_path / "historical.csv", [sap_row("15.03.2024", "n/a")])
    current = write_sap_csv(tmp_path / "current.csv", [sap_row("03.15.2024", "")])
    with pytest.raises(ValueError, match="no usable rainfall records"):
        build([historical, current], tmp_path / "out.json")

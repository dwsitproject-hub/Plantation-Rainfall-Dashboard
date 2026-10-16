from __future__ import annotations

import argparse
import csv
import json
from datetime import date as date_type, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DASHBOARD = ROOT.parent / "rainfall-dashboard"
DEFAULT_HISTORICAL = ROOT / "SAP rainfall data historical - all regions.csv"
DEFAULT_CURRENT = ROOT / "SAP rainfall data current - all regions.csv"
DEFAULT_OUTPUT = DASHBOARD / "dist" / "data" / "rainfall_daily.json"


def number(value: str):
    try:
        return float(value.replace(",", "."))
    except (ValueError, AttributeError):
        return None


def detect_date_format(source: Path, requested_order: str) -> str:
    if requested_order == "dmy":
        return "%d.%m.%Y"
    if requested_order == "mdy":
        return "%m.%d.%Y"
    first_over_12 = False
    second_over_12 = False
    with source.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            value = row.get("posting_date", "")
            try:
                first, second, _ = (int(part) for part in value.split("."))
            except (TypeError, ValueError):
                continue
            first_over_12 = first_over_12 or first > 12
            second_over_12 = second_over_12 or second > 12
    if first_over_12 and second_over_12:
        raise ValueError(f"{source.name} contains mixed or invalid dotted date formats")
    if first_over_12:
        return "%d.%m.%Y"
    if second_over_12:
        return "%m.%d.%Y"
    raise ValueError(
        f"Could not determine whether {source.name} uses DD.MM.YYYY or MM.DD.YYYY; "
        "pass --date-order dmy or --date-order mdy"
    )


def parse_posting_date(value: str, date_format: str) -> date_type | None:
    """Return a usable SAP date, ignoring corrupt placeholder dates."""
    try:
        parsed = datetime.strptime((value or "").strip(), date_format).date()
    except (TypeError, ValueError):
        return None
    # SAP occasionally returns corrupt placeholder years such as 0100. They
    # are not rainfall observations and must not stop an otherwise valid pull.
    if parsed.year < 2000 or parsed.year > date_type.today().year + 1:
        return None
    return parsed


def build(historical: Path, current: Path, output: Path, date_order: str = "auto") -> int:
    seen = set()
    records = []
    skipped_dates = []
    for source in (historical, current):
        date_format = detect_date_format(source, date_order)
        print(f"Detected {'DD.MM.YYYY' if date_format == '%d.%m.%Y' else 'MM.DD.YYYY'} in {source.name}")
        with source.open(encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                key = tuple(row.items())
                if key in seen:
                    continue
                seen.add(key)
                value = number(row.get("total_rainfall_measurement", ""))
                if value is None:
                    continue
                parsed_date = parse_posting_date(row.get("posting_date", ""), date_format)
                if parsed_date is None:
                    skipped_dates.append(
                        (source.name, row.get("company_code", ""), row.get("estate", ""), row.get("posting_date", ""))
                    )
                    continue
                date = parsed_date.isoformat()
                records.append([
                    row["company_code"].strip(),
                    row["estate"].strip(),
                    (row.get("plant") or "").strip(),
                    (row.get("measurement_tool") or "Unspecified").strip(),
                    date,
                    value,
                ])

    if not records:
        raise ValueError("The SAP files contained no usable rainfall records")
    if skipped_dates:
        print(f"Warning: skipped {len(skipped_dates):,} SAP row(s) with invalid posting dates.")
        for source_name, company, estate, raw_date in skipped_dates[:10]:
            print(f"  - {source_name}: {company}/{estate} posting_date={raw_date!r}")
        if len(skipped_dates) > 10:
            print(f"  - ... and {len(skipped_dates) - 10:,} more invalid date row(s)")

    # The current SAP endpoint can return future/all-zero placeholder rows.
    # A date is an actual observation date only when at least one estate has
    # rainfall above zero. Apply that portfolio-wide date validity to every
    # estate, so valid zero-rain estates are retained on a genuinely loaded
    # date while all-zero placeholder dates are removed everywhere.
    today = date_type.today().isoformat()
    actual_dates = {item[4] for item in records if item[4] <= today and item[5] > 0}
    if not actual_dates:
        raise ValueError("SAP contained no portfolio date with rainfall above zero")
    before_filter = len(records)
    records = [item for item in records if item[4] in actual_dates]
    removed_placeholders = before_filter - len(records)
    actual_cutoff = max(actual_dates)
    print(
        f"SAP actual cutoff: {actual_cutoff} "
        f"(removed {removed_placeholders:,} all-zero or future placeholder rows)"
    )
    records.sort(key=lambda item: (item[0], item[1], item[3], item[4]))
    companies = {}
    for company, estate, *_ in records:
        companies.setdefault(company, set()).add(estate)

    payload = {
        "generated": datetime.now().astimezone().isoformat(timespec="seconds"),
        "sap_actual_cutoff": actual_cutoff,
        "fields": ["company", "estate", "plant", "tool", "date", "rainfall_mm"],
        "companies": {key: sorted(value) for key, value in sorted(companies.items())},
        "records": records,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    print(f"Wrote {len(records):,} deduplicated rows to {output}")
    return len(records)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build rainfall-dashboard JSON from SAP CSV files.")
    parser.add_argument("--historical", type=Path, default=DEFAULT_HISTORICAL)
    parser.add_argument("--current", type=Path, default=DEFAULT_CURRENT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--date-order", choices=("auto", "dmy", "mdy"), default="auto")
    args = parser.parse_args()
    build(args.historical, args.current, args.output, args.date_order)


if __name__ == "__main__":
    main()

"""Create or upgrade the database schema and seed reference data.

Run after every deployment (it is idempotent):

    python -m backend.database.init_db
"""

from __future__ import annotations

from pathlib import Path

from backend.all_regions.rainfall_common import REGIONS, TEMPERATURE_REGIONS, is_major_plantation_region
from backend.database import repository
from backend.database.db import transaction


SCHEMA_SQL = Path(__file__).resolve().parent / "schema.sql"


def apply_schema() -> None:
    with transaction() as cursor:
        cursor.execute(SCHEMA_SQL.read_text(encoding="utf-8"))


def seed_weather_regions() -> int:
    temperature = {(country, region): (lat, lon) for country, region, lat, lon in TEMPERATURE_REGIONS}
    return repository.upsert_weather_regions([{
        "country": country, "region": region,
        "rain_latitude": latitude, "rain_longitude": longitude,
        "temperature_latitude": temperature[(country, region)][0],
        "temperature_longitude": temperature[(country, region)][1],
        "is_major_plantation": is_major_plantation_region(country, region),
        "display_order": order,
    } for order, (country, region, latitude, longitude) in enumerate(REGIONS)])


def main() -> int:
    apply_schema()
    count = seed_weather_regions()
    print(f"Database schema is up to date; {count} weather regions seeded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

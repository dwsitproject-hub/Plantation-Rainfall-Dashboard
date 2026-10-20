"""PostgreSQL connection helpers."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import psycopg2
import psycopg2.extensions

from backend import config


SCHEMA = "rainfall"  # must match backend/database/schema.sql


def connect(*, user: str | None = None, password: str | None = None,
            dbname: str | None = None) -> psycopg2.extensions.connection:
    return psycopg2.connect(
        host=config.DATABASE_HOST,
        port=config.DATABASE_PORT,
        dbname=dbname or config.DATABASE_NAME,
        user=user or config.database_user(),
        password=password if password is not None else config.database_password(),
        connect_timeout=10,
        sslmode=config.DATABASE_SSLMODE,
        application_name="plantation-rainfall",
        options=f"-c search_path={SCHEMA},public",
    )


@contextmanager
def transaction() -> Iterator[psycopg2.extensions.cursor]:
    """Yield a cursor; commit on success, roll back everything on any error."""
    connection = connect()
    try:
        with connection:
            with connection.cursor() as cursor:
                yield cursor
    finally:
        connection.close()

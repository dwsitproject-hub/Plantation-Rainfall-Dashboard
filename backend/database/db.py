"""PostgreSQL connection helpers.

Opening a connection costs far more than a typical query (TCP, SSL negotiation and
authentication: about 150-200 ms from a container to a remote database), so
transaction() borrows connections from a small per-process pool and returns them.
Long-lived or special connections (refresh lock, audit, bootstrap) use connect().
"""

from __future__ import annotations

import queue
import threading
import time
from contextlib import contextmanager
from typing import Iterator

import psycopg2
import psycopg2.extensions

from backend import config


SCHEMA = "rainfall"  # must match backend/database/schema.sql
POOL_WAIT_SECONDS = 30
# Replace pooled connections idle longer than this (servers may drop idle connections).
POOL_MAX_IDLE_SECONDS = 300


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
        keepalives=1, keepalives_idle=60, keepalives_interval=10, keepalives_count=3,
    )


class _Pool:
    """A small blocking pool: at most `size` open connections, reused LIFO."""

    def __init__(self, size: int):
        self.idle: queue.LifoQueue = queue.LifoQueue()
        self.slots = threading.BoundedSemaphore(size)

    def acquire(self) -> psycopg2.extensions.connection:
        if not self.slots.acquire(timeout=POOL_WAIT_SECONDS):
            raise psycopg2.OperationalError("No database connection available (pool exhausted)")
        try:
            while True:
                try:
                    connection, idle_since = self.idle.get_nowait()
                except queue.Empty:
                    return connect()
                if connection.closed:
                    continue
                if time.monotonic() - idle_since > POOL_MAX_IDLE_SECONDS:
                    connection.close()
                    continue
                return connection
        except BaseException:
            self.slots.release()
            raise

    def release(self, connection: psycopg2.extensions.connection, *, broken: bool) -> None:
        try:
            if broken or connection.closed:
                connection.close()
            else:
                self.idle.put_nowait((connection, time.monotonic()))
        finally:
            self.slots.release()

    def close_all(self) -> None:
        while True:
            try:
                self.idle.get_nowait()[0].close()
            except queue.Empty:
                return


_pools: dict[tuple, _Pool] = {}
_pools_lock = threading.Lock()


def _pool() -> _Pool:
    # Keyed by the connection settings, so a changed setting (tests) gets its own pool.
    key = (config.DATABASE_HOST, config.DATABASE_PORT, config.DATABASE_NAME,
           config.database_user(), config.DATABASE_SSLMODE)
    with _pools_lock:
        if key not in _pools:
            _pools[key] = _Pool(config.DATABASE_POOL_SIZE)
        return _pools[key]


def reset_pools() -> None:
    """Close every pooled connection (tests that recreate the database)."""
    with _pools_lock:
        for pool in _pools.values():
            pool.close_all()
        _pools.clear()


@contextmanager
def transaction() -> Iterator[psycopg2.extensions.cursor]:
    """Yield a cursor; commit on success, roll back everything on any error."""
    pool = _pool()
    connection = pool.acquire()
    broken = False
    try:
        with connection:  # commit on success, rollback on error
            with connection.cursor() as cursor:
                yield cursor
    except (psycopg2.OperationalError, psycopg2.InterfaceError):
        broken = True  # the server went away: never reuse this connection
        raise
    finally:
        pool.release(connection, broken=broken)

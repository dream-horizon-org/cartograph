"""Read-only DB access to the cartograph postgres."""

from __future__ import annotations

import os
from contextlib import contextmanager

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool


def _dsn() -> str:
    host = os.getenv("CARTOGRAPH_DB_HOST", "localhost")
    port = os.getenv("CARTOGRAPH_DB_PORT", "5432")
    name = os.getenv("CARTOGRAPH_DB_NAME", "cartograph")
    user = os.getenv("CARTOGRAPH_DB_USER", "cartograph")
    pw = os.getenv("CARTOGRAPH_DB_PASSWORD", "cartograph")
    return f"postgresql://{user}:{pw}@{host}:{port}/{name}"


_pool: ConnectionPool | None = None


def init_pool() -> None:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            conninfo=_dsn(),
            min_size=1,
            max_size=4,
            kwargs={"row_factory": dict_row, "autocommit": True},
        )
        _pool.wait()


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


@contextmanager
def cursor():
    if _pool is None:
        raise RuntimeError("db pool not initialised — call init_pool() first")
    with _pool.connection() as conn, conn.cursor() as cur:
        yield cur

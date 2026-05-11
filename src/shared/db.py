"""PostgreSQL connection pool and query helpers."""

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from shared.config import get_dsn

_pool: ConnectionPool | None = None


def init_pool() -> None:
    global _pool
    _pool = ConnectionPool(
        conninfo=get_dsn(),
        min_size=2,
        max_size=10,
        kwargs={"row_factory": dict_row, "autocommit": False},
    )


def get_pool() -> ConnectionPool:
    if _pool is None:
        raise RuntimeError("DB pool not initialized. Call init_pool() first.")
    return _pool


def execute(query: str, params: tuple | dict = ()) -> list[dict]:
    """Execute a query and return all rows as dicts."""
    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            if cur.description is None:
                conn.commit()
                return []
            rows = cur.fetchall()
            conn.commit()
            return rows


def execute_one(query: str, params: tuple | dict = ()) -> dict | None:
    """Execute a query and return the first row or None."""
    rows = execute(query, params)
    return rows[0] if rows else None


def execute_mutate(query: str, params: tuple | dict = ()) -> int:
    """Execute an INSERT/UPDATE/DELETE and return rowcount."""
    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            rowcount = cur.rowcount
            conn.commit()
            return rowcount


def execute_returning(query: str, params: tuple | dict = ()) -> dict | None:
    """Execute a mutating query with RETURNING and return the row."""
    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            row = cur.fetchone()
            conn.commit()
            return row


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None

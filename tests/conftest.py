"""Shared pytest fixtures for Postgres-backed tests."""

import os
import pytest
import psycopg2
from psycopg2.extras import RealDictCursor

TEST_DSN = {
    "host": os.environ.get("DB_HOST", "localhost"),
    "port": int(os.environ.get("DB_PORT", "5432")),
    "dbname": os.environ.get("DB_TEST_NAME", "cartograph_test"),
    "user": os.environ.get("DB_USER", "cartograph"),
    "password": os.environ.get("DB_PASSWORD", "cartograph"),
}


@pytest.fixture(scope="session", autouse=True)
def bootstrap_schema():
    """Ensure the V2 schema exists once before any test runs.

    Uses os.environ directly (not monkeypatch) because monkeypatch is
    function-scoped and unavailable at session scope. These env vars
    are set permanently for the process lifetime, which is fine — the
    test DB values are what every test wants, and function-scoped
    set_test_db_env re-applies them via monkeypatch for isolation.
    """
    for key, val in [
        ("DB_NAME",     TEST_DSN["dbname"]),
        ("DB_HOST",     TEST_DSN["host"]),
        ("DB_PORT",     str(TEST_DSN["port"])),
        ("DB_USER",     TEST_DSN["user"]),
        ("DB_PASSWORD", TEST_DSN["password"]),
    ]:
        os.environ[key] = val
    from agent_management.db import init_db
    init_db()


@pytest.fixture(autouse=True)
def set_test_db_env(monkeypatch):
    """Point all db.py calls at the test database."""
    monkeypatch.setenv("DB_NAME", TEST_DSN["dbname"])
    monkeypatch.setenv("DB_HOST", TEST_DSN["host"])
    monkeypatch.setenv("DB_PORT", str(TEST_DSN["port"]))
    monkeypatch.setenv("DB_USER", TEST_DSN["user"])
    monkeypatch.setenv("DB_PASSWORD", TEST_DSN["password"])


@pytest.fixture
def pg_conn():
    """Raw psycopg2 connection to the test database (not using db.py)."""
    conn = psycopg2.connect(**TEST_DSN, cursor_factory=RealDictCursor)
    conn.autocommit = True
    yield conn
    conn.close()


@pytest.fixture(autouse=True)
def clean_db(pg_conn):
    """Truncate all tables before each test."""
    pg_conn.cursor().execute("""
        TRUNCATE TABLE
            merge_candidates,
            abbreviations,
            edges,
            unresolved,
            attributions,
            resource_component_agents,
            components,
            communications,
            tasks,
            secrets,
            resources,
            trigger_queue,
            agent_runs
        RESTART IDENTITY CASCADE
    """)
    yield

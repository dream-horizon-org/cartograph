"""Shared test fixtures for admin_ui tests.

CRITICAL SAFETY: forces tests to use a separate 'cartograph_test' database.
Without this, tests DELETE production data.
"""

import os
import pytest
from fastapi.testclient import TestClient

# Force test DB — MUST be set BEFORE importing shared.db
os.environ["CARTOGRAPH_DB_NAME"] = "cartograph_test"

from shared.db import init_pool, close_pool, execute_mutate
from shared.migrations import run_migrations
from shared import config
from admin_ui.server import create_app


@pytest.fixture(scope="session", autouse=True)
def setup_db():
    """Initialize the DB pool and run migrations once per test session."""
    if config.DB_NAME == "cartograph":
        raise RuntimeError(
            "Tests refused to run against the 'cartograph' database — "
            "set CARTOGRAPH_DB_NAME=cartograph_test. "
            "See tests/admin_ui/conftest.py."
        )
    init_pool()
    run_migrations()
    yield
    close_pool()


@pytest.fixture(autouse=True)
def clean_tables():
    """Clean test data before each test (test DB only)."""
    assert config.DB_NAME != "cartograph", "Refusing to wipe dev DB"
    execute_mutate("DELETE FROM proxy_audit")
    execute_mutate("DELETE FROM broadcast_acks")
    execute_mutate("DELETE FROM communications")
    # Phase 5.2: workflow-entity tables — wipe so /api/entities tests
    # see a clean slate. Order respects FKs (children first).
    execute_mutate("DELETE FROM clarifications")
    execute_mutate("DELETE FROM consolidations")
    execute_mutate("DELETE FROM tasks")
    execute_mutate("DELETE FROM flows")
    execute_mutate("DELETE FROM edges")
    execute_mutate("DELETE FROM attributions")
    execute_mutate("DELETE FROM resource_component_agents")
    execute_mutate("DELETE FROM resources")
    execute_mutate("DELETE FROM components")
    execute_mutate("DELETE FROM agent_runs")
    yield


@pytest.fixture
def client():
    """FastAPI test client."""
    app = create_app()
    return TestClient(app)


@pytest.fixture
def agent_factory():
    """Factory to create test agents."""
    def _create(agent_id: str, agent_type: str = "sme", status: str = "idle"):
        execute_mutate(
            """INSERT INTO agent_runs (agent_id, agent_type, status)
               VALUES (%s, %s, %s)""",
            (agent_id, agent_type, status),
        )
        return agent_id
    return _create


@pytest.fixture
def chat_factory():
    """Factory to insert chat messages."""
    def _create(from_agent: str, to_agent: str, text: str):
        from shared.db import execute_returning
        row = execute_returning(
            """INSERT INTO communications (from_agent, to_agent, type, text)
               VALUES (%s, %s, 'chat', %s)
               RETURNING *""",
            (from_agent, to_agent, text),
        )
        return row
    return _create

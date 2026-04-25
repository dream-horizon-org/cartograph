"""Shared test fixtures for MCP tool tests.

CRITICAL SAFETY: forces tests to use a separate 'cartograph_test' database.
Without this, tests DELETE production data. The setup_db fixture refuses
to run if the configured DB name is 'cartograph' (the dev DB).
"""

import os
import pytest

# Force tests to use a separate DB — MUST be set BEFORE importing shared.db
os.environ["CARTOGRAPH_DB_NAME"] = "cartograph_test"

from shared.db import init_pool, close_pool, execute_mutate
from shared import config


@pytest.fixture(scope="session", autouse=True)
def setup_db():
    from shared.migrations import run_migrations
    # Safety check: refuse to run if we're pointed at the dev DB
    if config.DB_NAME == "cartograph":
        raise RuntimeError(
            "Tests refused to run against the 'cartograph' database — "
            "set CARTOGRAPH_DB_NAME=cartograph_test or run tests in CI. "
            "See tests/mcp_tools/conftest.py."
        )
    init_pool()
    run_migrations()
    yield
    close_pool()


@pytest.fixture(autouse=True)
def clean_tables():
    """Clean test data before each test (test DB only)."""
    assert config.DB_NAME != "cartograph", "Refusing to wipe dev DB"
    execute_mutate("DELETE FROM mcp_audit")
    execute_mutate("DELETE FROM agent_insights")
    execute_mutate("DELETE FROM proxy_audit")
    execute_mutate("DELETE FROM broadcast_acks")
    execute_mutate("DELETE FROM communications")
    execute_mutate("DELETE FROM tasks")
    execute_mutate("DELETE FROM consolidations")
    execute_mutate("DELETE FROM clarifications")
    execute_mutate("DELETE FROM unresolved")
    execute_mutate("DELETE FROM flows")
    execute_mutate("DELETE FROM edges")
    execute_mutate("DELETE FROM attributions")
    execute_mutate("DELETE FROM resource_component_agents")
    execute_mutate("DELETE FROM resources")
    execute_mutate("DELETE FROM components")
    execute_mutate("DELETE FROM agent_runs")
    yield


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

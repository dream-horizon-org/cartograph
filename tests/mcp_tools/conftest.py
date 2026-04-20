"""Shared test fixtures for MCP tool tests."""

import pytest

from shared.db import init_pool, close_pool, execute_mutate


@pytest.fixture(scope="session", autouse=True)
def setup_db():
    from shared.migrations import run_migrations
    init_pool()
    run_migrations()
    yield
    close_pool()


@pytest.fixture(autouse=True)
def clean_tables():
    """Clean test data before each test (preserves singleton agents)."""
    execute_mutate("DELETE FROM broadcast_acks")
    execute_mutate("DELETE FROM communications")
    execute_mutate("DELETE FROM tasks")
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

"""Shared test fixtures for admin_ui tests."""

import pytest
from fastapi.testclient import TestClient

from shared.db import init_pool, close_pool, execute_mutate
from shared.migrations import run_migrations
from admin_ui.server import create_app


@pytest.fixture(scope="session", autouse=True)
def setup_db():
    """Initialize the DB pool and run migrations once per test session."""
    init_pool()
    run_migrations()
    yield
    close_pool()


@pytest.fixture(autouse=True)
def clean_tables():
    """Clean test data before each test."""
    execute_mutate("DELETE FROM broadcast_acks")
    execute_mutate("DELETE FROM communications")
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

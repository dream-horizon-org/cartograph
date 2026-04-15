import os
import sqlite3
import tempfile

import pytest

from cartograph.db import (
    init_db,
    create_agent_run,
    get_agent,
    update_agent_status,
    update_agent_session,
    update_agent_heartbeat,
    increment_invocation_count,
)


@pytest.fixture
def db_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    os.unlink(path)


def test_init_db_creates_agent_runs_table(db_path):
    init_db(db_path)
    conn = sqlite3.connect(db_path)
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='agent_runs'"
    )
    assert cursor.fetchone() is not None
    conn.close()


def test_init_db_creates_trigger_queue_table(db_path):
    init_db(db_path)
    conn = sqlite3.connect(db_path)
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='trigger_queue'"
    )
    assert cursor.fetchone() is not None
    conn.close()


def test_init_db_is_idempotent(db_path):
    init_db(db_path)
    init_db(db_path)  # should not raise
    conn = sqlite3.connect(db_path)
    cursor = conn.execute(
        "SELECT count(*) FROM sqlite_master WHERE type='table'"
    )
    count = cursor.fetchone()[0]
    assert count == 2
    conn.close()


@pytest.fixture
def initialized_db(db_path):
    init_db(db_path)
    return db_path


def test_create_and_get_agent(initialized_db):
    create_agent_run(
        agent_id="orch-abc123",
        agent_type="orchestrator",
        workspace_path="/tmp/workspaces/orch-abc123",
    )
    agent = get_agent("orch-abc123")
    assert agent["agent_id"] == "orch-abc123"
    assert agent["agent_type"] == "orchestrator"
    assert agent["status"] == "pending"
    assert agent["trigger_lock"] == 0
    assert agent["invocation_count"] == 0
    assert agent["session_id"] is None
    assert agent["plane"] is None
    assert agent["resource_id"] is None


def test_create_agent_with_plane(initialized_db):
    create_agent_run(
        agent_id="iter-github-abc123",
        agent_type="iterator",
        workspace_path="/tmp/workspaces/iter-github-abc123",
        plane="github",
    )
    agent = get_agent("iter-github-abc123")
    assert agent["plane"] == "github"


def test_create_agent_with_resource(initialized_db):
    create_agent_run(
        agent_id="sme-abc123",
        agent_type="sme",
        workspace_path="/tmp/workspaces/sme-abc123",
        plane="github",
        resource_id="repo-xyz",
    )
    agent = get_agent("sme-abc123")
    assert agent["resource_id"] == "repo-xyz"
    assert agent["plane"] == "github"


def test_get_nonexistent_agent(initialized_db):
    agent = get_agent("does-not-exist")
    assert agent is None


def test_update_agent_status(initialized_db):
    create_agent_run("orch-1", "orchestrator", "/tmp/ws/orch-1")
    update_agent_status("orch-1", "running")
    agent = get_agent("orch-1")
    assert agent["status"] == "running"


def test_update_agent_session(initialized_db):
    create_agent_run("orch-1", "orchestrator", "/tmp/ws/orch-1")
    update_agent_session("orch-1", "session-uuid-abc")
    agent = get_agent("orch-1")
    assert agent["session_id"] == "session-uuid-abc"


def test_update_agent_heartbeat(initialized_db):
    create_agent_run("orch-1", "orchestrator", "/tmp/ws/orch-1")
    update_agent_heartbeat("orch-1")
    agent = get_agent("orch-1")
    assert agent["heartbeat"] is not None


def test_increment_invocation_count(initialized_db):
    create_agent_run("orch-1", "orchestrator", "/tmp/ws/orch-1")
    increment_invocation_count("orch-1")
    increment_invocation_count("orch-1")
    agent = get_agent("orch-1")
    assert agent["invocation_count"] == 2

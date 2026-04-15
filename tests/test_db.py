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
    enqueue_trigger,
    get_pending_triggers,
    update_trigger_status,
    cancel_pending_triggers,
    acquire_trigger_lock,
    release_trigger_lock,
    get_stale_running_agents,
    agent_type_exists,
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


def test_enqueue_and_get_triggers(initialized_db):
    create_agent_run("orch-1", "orchestrator", "/tmp/ws/orch-1")
    enqueue_trigger("orch-1", "Start orchestration", 100)
    triggers = get_pending_triggers()
    assert len(triggers) == 1
    assert triggers[0]["agent_id"] == "orch-1"
    assert triggers[0]["prompt"] == "Start orchestration"
    assert triggers[0]["priority"] == 100
    assert triggers[0]["status"] == "pending"


def test_triggers_ordered_by_priority_then_created(initialized_db):
    create_agent_run("orch-1", "orchestrator", "/tmp/ws/orch-1")
    create_agent_run("sme-1", "sme", "/tmp/ws/sme-1")
    create_agent_run("iter-1", "iterator", "/tmp/ws/iter-1")
    enqueue_trigger("sme-1", "Analyze resource", 40)
    enqueue_trigger("orch-1", "Coordinate", 100)
    enqueue_trigger("iter-1", "List resources", 60)
    triggers = get_pending_triggers()
    assert [t["agent_id"] for t in triggers] == ["orch-1", "iter-1", "sme-1"]


def test_update_trigger_status(initialized_db):
    create_agent_run("orch-1", "orchestrator", "/tmp/ws/orch-1")
    enqueue_trigger("orch-1", "Start", 100)
    triggers = get_pending_triggers()
    update_trigger_status(triggers[0]["id"], "done")
    assert len(get_pending_triggers()) == 0


def test_cancel_pending_triggers(initialized_db):
    create_agent_run("orch-1", "orchestrator", "/tmp/ws/orch-1")
    enqueue_trigger("orch-1", "Prompt 1", 100)
    enqueue_trigger("orch-1", "Prompt 2", 100)
    cancel_pending_triggers("orch-1")
    assert len(get_pending_triggers()) == 0


def test_acquire_trigger_lock_on_pending_agent(initialized_db):
    create_agent_run("orch-1", "orchestrator", "/tmp/ws/orch-1")
    assert acquire_trigger_lock("orch-1") is True
    agent = get_agent("orch-1")
    assert agent["trigger_lock"] == 1
    assert agent["status"] == "invoking"


def test_acquire_trigger_lock_on_idle_agent(initialized_db):
    create_agent_run("orch-1", "orchestrator", "/tmp/ws/orch-1")
    update_agent_status("orch-1", "idle")
    assert acquire_trigger_lock("orch-1") is True
    agent = get_agent("orch-1")
    assert agent["trigger_lock"] == 1
    assert agent["status"] == "invoking"


def test_acquire_trigger_lock_fails_on_running_agent(initialized_db):
    create_agent_run("orch-1", "orchestrator", "/tmp/ws/orch-1")
    update_agent_status("orch-1", "running")
    assert acquire_trigger_lock("orch-1") is False


def test_acquire_trigger_lock_fails_on_already_locked(initialized_db):
    create_agent_run("orch-1", "orchestrator", "/tmp/ws/orch-1")
    acquire_trigger_lock("orch-1")
    assert acquire_trigger_lock("orch-1") is False


def test_release_trigger_lock(initialized_db):
    create_agent_run("orch-1", "orchestrator", "/tmp/ws/orch-1")
    acquire_trigger_lock("orch-1")
    release_trigger_lock("orch-1")
    agent = get_agent("orch-1")
    assert agent["trigger_lock"] == 0


def test_get_stale_running_agents(initialized_db):
    create_agent_run("orch-1", "orchestrator", "/tmp/ws/orch-1")
    update_agent_status("orch-1", "running")
    # Set heartbeat to a time far in the past
    conn = sqlite3.connect(initialized_db)
    conn.execute(
        "UPDATE agent_runs SET heartbeat = '2020-01-01T00:00:00+00:00' WHERE agent_id = 'orch-1'"
    )
    conn.commit()
    conn.close()
    stale = get_stale_running_agents(timeout_seconds=60)
    assert len(stale) == 1
    assert stale[0]["agent_id"] == "orch-1"


def test_get_stale_running_agents_ignores_fresh(initialized_db):
    create_agent_run("orch-1", "orchestrator", "/tmp/ws/orch-1")
    update_agent_status("orch-1", "running")
    update_agent_heartbeat("orch-1")
    stale = get_stale_running_agents(timeout_seconds=60)
    assert len(stale) == 0


def test_agent_type_exists_returns_false_when_none(initialized_db):
    assert agent_type_exists("orchestrator") is False


def test_agent_type_exists_returns_true_when_active(initialized_db):
    create_agent_run("orch-1", "orchestrator", "/tmp/ws/orch-1")
    assert agent_type_exists("orchestrator") is True


def test_agent_type_exists_returns_false_when_all_decommissioned(initialized_db):
    create_agent_run("orch-1", "orchestrator", "/tmp/ws/orch-1")
    update_agent_status("orch-1", "decommissioned")
    assert agent_type_exists("orchestrator") is False


def test_agent_type_exists_returns_true_when_some_active(initialized_db):
    create_agent_run("orch-1", "orchestrator", "/tmp/ws/orch-1")
    create_agent_run("orch-2", "orchestrator", "/tmp/ws/orch-2")
    update_agent_status("orch-1", "decommissioned")
    assert agent_type_exists("orchestrator") is True

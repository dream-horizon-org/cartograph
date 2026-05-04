import pytest
import psycopg2
from psycopg2.extras import RealDictCursor

from agent_management.db import init_db


def _raw_conn():
    import os
    return psycopg2.connect(
        host=os.environ.get("DB_HOST", "localhost"),
        port=int(os.environ.get("DB_PORT", "5432")),
        dbname=os.environ.get("DB_NAME", "cartograph_test"),
        user=os.environ.get("DB_USER", "cartograph"),
        password=os.environ.get("DB_PASSWORD", "cartograph"),
        cursor_factory=RealDictCursor,
    )


def test_init_db_creates_agent_runs_table():
    init_db()
    conn = _raw_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'agent_runs'
    """)
    assert cur.fetchone() is not None
    conn.close()


def test_init_db_creates_components_table():
    init_db()
    conn = _raw_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'components'
    """)
    assert cur.fetchone() is not None
    conn.close()


def test_init_db_creates_merge_candidates_table():
    init_db()
    conn = _raw_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'merge_candidates'
    """)
    assert cur.fetchone() is not None
    conn.close()


def test_init_db_is_idempotent():
    init_db()
    init_db()  # must not raise


def test_create_and_get_agent():
    from agent_management.db import create_agent_run, get_agent
    init_db()
    create_agent_run(
        agent_id="orch-abc123",
        agent_type="orchestrator",
        workspace_path="/tmp/ws/orch-abc123",
    )
    agent = get_agent("orch-abc123")
    assert agent["agent_id"] == "orch-abc123"
    assert agent["agent_type"] == "orchestrator"
    assert agent["status"] == "pending"
    assert agent["trigger_lock"] is False
    assert agent["invocation_count"] == 0
    assert agent["session_id"] is None


def test_acquire_trigger_lock_on_pending_agent():
    from agent_management.db import create_agent_run, acquire_trigger_lock, get_agent
    init_db()
    create_agent_run("orch-1", "orchestrator", "/tmp/ws/orch-1")
    assert acquire_trigger_lock("orch-1") is True
    agent = get_agent("orch-1")
    assert agent["trigger_lock"] is True
    assert agent["status"] == "invoking"


def test_acquire_trigger_lock_fails_on_running_agent():
    from agent_management.db import (
        create_agent_run, update_agent_status, acquire_trigger_lock
    )
    init_db()
    create_agent_run("orch-1", "orchestrator", "/tmp/ws/orch-1")
    update_agent_status("orch-1", "running")
    assert acquire_trigger_lock("orch-1") is False


def test_acquire_trigger_lock_fails_if_already_locked():
    from agent_management.db import create_agent_run, acquire_trigger_lock
    init_db()
    create_agent_run("orch-1", "orchestrator", "/tmp/ws/orch-1")
    acquire_trigger_lock("orch-1")
    assert acquire_trigger_lock("orch-1") is False


def test_triggers_ordered_by_priority_then_created():
    from agent_management.db import (
        create_agent_run, enqueue_trigger, get_pending_triggers
    )
    init_db()
    create_agent_run("orch-1", "orchestrator", "/tmp/ws/orch-1")
    create_agent_run("sme-1", "sme", "/tmp/ws/sme-1")
    create_agent_run("iter-1", "iterator", "/tmp/ws/iter-1")
    enqueue_trigger("sme-1", "Analyze resource", 40)
    enqueue_trigger("orch-1", "Coordinate", 100)
    enqueue_trigger("iter-1", "List resources", 60)
    triggers = get_pending_triggers()
    assert [t["agent_id"] for t in triggers] == ["orch-1", "iter-1", "sme-1"]

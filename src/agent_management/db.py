"""SQLite data layer for agent runtime. All SQL is encapsulated here."""

import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

_DB_PATH: str | None = None


def _connect() -> sqlite3.Connection:
    if _DB_PATH is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: str) -> None:
    global _DB_PATH
    _DB_PATH = db_path
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS agent_runs (
            agent_id        TEXT PRIMARY KEY,
            agent_type      TEXT NOT NULL CHECK(agent_type IN ('orchestrator','iterator','sme','resolver')),
            session_id      TEXT,
            resource_id     TEXT,
            plane           TEXT,
            status          TEXT NOT NULL DEFAULT 'pending'
                            CHECK(status IN ('pending','invoking','running','idle','done','errored','decommissioned')),
            phase           TEXT,
            heartbeat       TEXT,
            invocation_count INTEGER NOT NULL DEFAULT 0,
            trigger_lock    INTEGER NOT NULL DEFAULT 0 CHECK(trigger_lock IN (0,1)),
            workspace_path  TEXT NOT NULL,
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS trigger_queue (
            id              TEXT PRIMARY KEY,
            agent_id        TEXT NOT NULL REFERENCES agent_runs(agent_id),
            prompt          TEXT NOT NULL,
            priority        INTEGER NOT NULL,
            status          TEXT NOT NULL DEFAULT 'pending'
                            CHECK(status IN ('pending','processing','done')),
            created_at      TEXT NOT NULL
        );
    """)
    conn.commit()
    conn.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_agent_run(
    agent_id: str,
    agent_type: str,
    workspace_path: str,
    plane: str | None = None,
    resource_id: str | None = None,
) -> None:
    now = _now()
    conn = _connect()
    conn.execute(
        """INSERT INTO agent_runs
           (agent_id, agent_type, workspace_path, plane, resource_id, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (agent_id, agent_type, workspace_path, plane, resource_id, now, now),
    )
    conn.commit()
    conn.close()


def get_agent(agent_id: str) -> dict | None:
    conn = _connect()
    cursor = conn.execute(
        "SELECT * FROM agent_runs WHERE agent_id = ?", (agent_id,)
    )
    row = cursor.fetchone()
    conn.close()
    if row is None:
        return None
    return dict(row)


def update_agent_status(agent_id: str, status: str) -> None:
    conn = _connect()
    conn.execute(
        "UPDATE agent_runs SET status = ?, updated_at = ? WHERE agent_id = ?",
        (status, _now(), agent_id),
    )
    conn.commit()
    conn.close()


def update_agent_session(agent_id: str, session_id: str) -> None:
    conn = _connect()
    conn.execute(
        "UPDATE agent_runs SET session_id = ?, updated_at = ? WHERE agent_id = ?",
        (session_id, _now(), agent_id),
    )
    conn.commit()
    conn.close()


def update_agent_heartbeat(agent_id: str) -> None:
    conn = _connect()
    conn.execute(
        "UPDATE agent_runs SET heartbeat = ?, updated_at = ? WHERE agent_id = ?",
        (_now(), _now(), agent_id),
    )
    conn.commit()
    conn.close()


def increment_invocation_count(agent_id: str) -> None:
    conn = _connect()
    conn.execute(
        "UPDATE agent_runs SET invocation_count = invocation_count + 1, updated_at = ? WHERE agent_id = ?",
        (_now(), agent_id),
    )
    conn.commit()
    conn.close()


def enqueue_trigger(agent_id: str, prompt: str, priority: int) -> str:
    trigger_id = str(uuid.uuid4())
    conn = _connect()
    conn.execute(
        """INSERT INTO trigger_queue (id, agent_id, prompt, priority, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (trigger_id, agent_id, prompt, priority, _now()),
    )
    conn.commit()
    conn.close()
    return trigger_id


def get_pending_triggers() -> list[dict]:
    conn = _connect()
    cursor = conn.execute(
        """SELECT * FROM trigger_queue
           WHERE status = 'pending'
           ORDER BY priority DESC, created_at ASC"""
    )
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


def update_trigger_status(trigger_id: str, status: str) -> None:
    conn = _connect()
    conn.execute(
        "UPDATE trigger_queue SET status = ? WHERE id = ?",
        (status, trigger_id),
    )
    conn.commit()
    conn.close()


def cancel_pending_triggers(agent_id: str) -> None:
    conn = _connect()
    conn.execute(
        "UPDATE trigger_queue SET status = 'done' WHERE agent_id = ? AND status = 'pending'",
        (agent_id,),
    )
    conn.commit()
    conn.close()


def acquire_trigger_lock(agent_id: str) -> bool:
    conn = _connect()
    cursor = conn.execute(
        """UPDATE agent_runs
           SET trigger_lock = 1, status = 'invoking', updated_at = ?
           WHERE agent_id = ? AND status IN ('pending', 'idle') AND trigger_lock = 0""",
        (_now(), agent_id),
    )
    changed = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return changed


def release_trigger_lock(agent_id: str) -> None:
    conn = _connect()
    conn.execute(
        "UPDATE agent_runs SET trigger_lock = 0, updated_at = ? WHERE agent_id = ?",
        (_now(), agent_id),
    )
    conn.commit()
    conn.close()


def agent_type_exists(agent_type: str) -> bool:
    """Check if a non-decommissioned agent of this type exists."""
    conn = _connect()
    cursor = conn.execute(
        "SELECT 1 FROM agent_runs WHERE agent_type = ? AND status != 'decommissioned' LIMIT 1",
        (agent_type,),
    )
    exists = cursor.fetchone() is not None
    conn.close()
    return exists


def get_stale_running_agents(timeout_seconds: int) -> list[dict]:
    conn = _connect()
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=timeout_seconds)
    cursor = conn.execute(
        """SELECT * FROM agent_runs
           WHERE status = 'running' AND heartbeat < ?""",
        (cutoff.isoformat(),),
    )
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows

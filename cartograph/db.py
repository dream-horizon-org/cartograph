"""SQLite data layer for agent runtime. All SQL is encapsulated here."""

import sqlite3
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

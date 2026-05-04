# Cartograph V2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate Cartograph from the V1 SQLite + agent-negotiation architecture to V2 Postgres + batch merge, removing the Resolver agent and SME cross-phase persistence.

**Architecture:** Replace SQLite with Postgres + pgvector, add a batch merger that runs as code (no LLM) after materialisation, remove the Resolver agent, make SMEs ephemeral, and add contextual description embeddings for better cross-plane matching.

**Tech Stack:** Python 3.12+, PostgreSQL 16 + pgvector, psycopg2-binary, openai SDK (text-embedding-3-small), pytest, pytest-mock

---

## File Map

```
src/agent_management/
  config.py            NEW  — DB + API settings from env vars
  db.py                REPLACE  — Postgres instead of SQLite, V2 schema
  embedding.py         NEW  — synthesize_description(), embed_text(), re_embed_component()
  union_find.py        NEW  — UnionFind class for transitive merge grouping
  batch_merger.py      NEW  — find_candidates(), classify_tier(), execute_merge()
  trigger_manager.py   UPDATE  — phase state machine, batch merge phase
  agent_manager.py     UPDATE  — no Resolver creation
  agent_types/
    orchestrator.py    UPDATE  — V2 phases, no Resolver mention
    sme.py             UPDATE  — ephemeral lifecycle, no consolidation phase
    resolver.py        DELETE
  __init__.py          unchanged
  agent_types/__init__.py  unchanged
  agent_types/base.py  unchanged
  agent_types/iterator.py  unchanged
src/main.py            UPDATE  — no Resolver creation

tests/
  conftest.py          NEW  — Postgres test DB fixture
  test_config.py       NEW
  test_db.py           REPLACE  — Postgres-based tests
  test_embedding.py    NEW  — mock OpenAI
  test_union_find.py   NEW
  test_batch_merger.py NEW
  test_trigger_manager.py  UPDATE  — phase tests
  test_agent_types.py  UPDATE  — no Resolver
  test_main.py         UPDATE  — no Resolver
  test_agent_manager.py    unchanged
  test_integration.py  UPDATE  — V2 flow

requirements.txt       UPDATE
pyproject.toml         UPDATE
```

---

### Task 1: Dependencies and Config

**Files:**
- Modify: `requirements.txt`
- Modify: `pyproject.toml`
- Create: `src/agent_management/config.py`
- Create: `tests/test_config.py`

- [ ] **Step 1: Update requirements.txt**

```
pyyaml>=6.0
psycopg2-binary>=2.9
openai>=1.0
pytest>=8.0
pytest-mock>=3.12
```

- [ ] **Step 2: Update pyproject.toml**

```toml
[project]
name = "cartograph"
version = "0.2.0"
requires-python = ">=3.12"
dependencies = [
    "pyyaml>=6.0",
    "psycopg2-binary>=2.9",
    "openai>=1.0",
]

[project.optional-dependencies]
dev = ["pytest>=8.0", "pytest-mock>=3.12"]

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
```

- [ ] **Step 3: Install dependencies**

```bash
cd /Users/bipulkarnani/IdeaProjects/cartograph
pip install psycopg2-binary openai pytest pytest-mock
```

Expected: no errors.

- [ ] **Step 4: Write failing test for config**

Write to `tests/test_config.py`:

```python
import os
import pytest
from agent_management.config import get_config


def test_get_config_defaults():
    for key in ("DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD",
                "OPENAI_API_KEY", "EMBEDDING_MODEL"):
        os.environ.pop(key, None)

    cfg = get_config()
    assert cfg.db_host == "localhost"
    assert cfg.db_port == 5432
    assert cfg.db_name == "cartograph"
    assert cfg.db_user == "cartograph"
    assert cfg.db_password == "cartograph"
    assert cfg.embedding_model == "text-embedding-3-small"


def test_get_config_from_env(monkeypatch):
    monkeypatch.setenv("DB_HOST", "myhost")
    monkeypatch.setenv("DB_PORT", "5433")
    monkeypatch.setenv("DB_NAME", "mydb")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    cfg = get_config()
    assert cfg.db_host == "myhost"
    assert cfg.db_port == 5433
    assert cfg.db_name == "mydb"
    assert cfg.openai_api_key == "sk-test"
```

- [ ] **Step 5: Run test to verify it fails**

```bash
cd /Users/bipulkarnani/IdeaProjects/cartograph && python -m pytest tests/test_config.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'agent_management.config'`

- [ ] **Step 6: Implement config.py**

Write to `src/agent_management/config.py`:

```python
"""DB and API configuration from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    db_host: str
    db_port: int
    db_name: str
    db_user: str
    db_password: str
    openai_api_key: str
    embedding_model: str


def get_config() -> Config:
    return Config(
        db_host=os.environ.get("DB_HOST", "localhost"),
        db_port=int(os.environ.get("DB_PORT", "5432")),
        db_name=os.environ.get("DB_NAME", "cartograph"),
        db_user=os.environ.get("DB_USER", "cartograph"),
        db_password=os.environ.get("DB_PASSWORD", "cartograph"),
        openai_api_key=os.environ.get("OPENAI_API_KEY", ""),
        embedding_model=os.environ.get(
            "EMBEDDING_MODEL", "text-embedding-3-small"
        ),
    )
```

- [ ] **Step 7: Run test to verify it passes**

```bash
python -m pytest tests/test_config.py -v
```

Expected: 2 tests PASS.

- [ ] **Step 8: Commit**

```bash
git add requirements.txt pyproject.toml src/agent_management/config.py tests/test_config.py
git commit -m "feat: add dependencies and config module for V2"
```

---

### Task 2: Postgres DB Layer — Connection and Schema

**Files:**
- Replace: `src/agent_management/db.py`
- Create: `tests/conftest.py`
- Replace: `tests/test_db.py`

- [ ] **Step 1: Start Postgres**

```bash
cd /Users/bipulkarnani/IdeaProjects/cartograph && docker-compose up -d
```

Expected: `postgres` container starts. Verify:

```bash
docker-compose ps
```

Expected: `cartograph-postgres-1` state `running`.

- [ ] **Step 2: Create test database**

```bash
docker exec -it $(docker-compose ps -q postgres) \
  psql -U cartograph -c "CREATE DATABASE cartograph_test;"
```

Expected: `CREATE DATABASE`.

- [ ] **Step 3: Write conftest.py**

Write to `tests/conftest.py`:

```python
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
```

- [ ] **Step 4: Write failing tests for init_db**

Write to `tests/test_db.py`:

```python
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
```

- [ ] **Step 5: Run tests to verify they fail**

```bash
python -m pytest tests/test_db.py -v
```

Expected: FAIL — `ModuleNotFoundError` (db.py is still SQLite version).

- [ ] **Step 6: Write new db.py — connection and schema**

Write to `src/agent_management/db.py`:

```python
"""PostgreSQL data layer for Cartograph V2. All SQL is encapsulated here."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import psycopg2
from psycopg2.extras import RealDictCursor

from agent_management.config import get_config


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

def _connect() -> psycopg2.extensions.connection:
    cfg = get_config()
    conn = psycopg2.connect(
        host=cfg.db_host,
        port=cfg.db_port,
        dbname=cfg.db_name,
        user=cfg.db_user,
        password=cfg.db_password,
        cursor_factory=RealDictCursor,
    )
    conn.autocommit = False
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS agent_runs (
    agent_id         TEXT PRIMARY KEY,
    agent_type       TEXT NOT NULL CHECK (agent_type IN (
                         'orchestrator', 'iterator', 'sme')),
    session_id       TEXT,
    resource_id      TEXT,
    plane            TEXT,
    status           TEXT NOT NULL DEFAULT 'pending' CHECK (status IN (
                         'pending', 'invoking', 'running', 'idle',
                         'done', 'errored', 'decommissioned')),
    phase            TEXT,
    heartbeat        TIMESTAMPTZ,
    invocation_count INT NOT NULL DEFAULT 0,
    trigger_lock     BOOLEAN NOT NULL DEFAULT FALSE,
    workspace_path   TEXT NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS trigger_queue (
    id          TEXT PRIMARY KEY,
    agent_id    TEXT NOT NULL REFERENCES agent_runs(agent_id),
    prompt      TEXT NOT NULL,
    priority    INT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'processing', 'done')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS resources (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    plane         TEXT NOT NULL CHECK (plane IN (
                      'github', 'deploy', 'cloud', 'telemetry', 'config')),
    resource_type TEXT NOT NULL,
    identifier    TEXT NOT NULL,
    access_desc   TEXT,
    metadata      JSONB NOT NULL DEFAULT '{}',
    status        TEXT NOT NULL DEFAULT 'pending' CHECK (status IN (
                      'pending', 'assigned', 'done')),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(plane, resource_type, identifier)
);

CREATE TABLE IF NOT EXISTS secrets (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    plane      TEXT NOT NULL,
    key        TEXT NOT NULL,
    value      TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(plane, key)
);

CREATE TABLE IF NOT EXISTS tasks (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_agent_id  TEXT NOT NULL,
    worker_agent_id TEXT NOT NULL REFERENCES agent_runs(agent_id) ON DELETE CASCADE,
    description     TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'BW' CHECK (status IN (
                        'BW', 'BO', 'WD', 'TC')),
    blocker_detail  TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS components (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    canonical_name          TEXT UNIQUE NOT NULL,
    display_name            TEXT NOT NULL,
    component_type          TEXT NOT NULL CHECK (component_type IN (
                                'application', 'database', 'cache', 'queue',
                                'lambda', 'cron', 'external-service',
                                'library', 'infrastructure')),
    status                  TEXT NOT NULL DEFAULT 'active' CHECK (status IN (
                                'active', 'deprecated', 'decommissioned')),
    confidence              FLOAT NOT NULL DEFAULT 1.0,
    metadata                JSONB NOT NULL DEFAULT '{}',
    embedding               vector(1536),
    split_from_component_id UUID REFERENCES components(id) ON DELETE SET NULL,
    split_briefing          TEXT,
    scanned_at              TIMESTAMPTZ,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS attributions (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    component_id  UUID NOT NULL REFERENCES components(id) ON DELETE CASCADE,
    plane         TEXT NOT NULL CHECK (plane IN (
                      'github', 'deploy', 'cloud', 'telemetry', 'config')),
    resource_type TEXT NOT NULL,
    identifier    TEXT NOT NULL,
    evidence      TEXT,
    confidence    FLOAT NOT NULL DEFAULT 1.0,
    metadata      JSONB NOT NULL DEFAULT '{}',
    embedding     vector(1536),
    discovered_by TEXT,
    discovered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(plane, resource_type, identifier)
);

CREATE TABLE IF NOT EXISTS edges (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id    UUID NOT NULL REFERENCES components(id) ON DELETE CASCADE,
    target_id    UUID NOT NULL REFERENCES components(id) ON DELETE CASCADE,
    edge_type    TEXT NOT NULL CHECK (edge_type IN (
                     'calls', 'reads_from', 'writes_to', 'triggers',
                     'publishes_to', 'consumes_from', 'runs_on')),
    identifier   TEXT NOT NULL,
    evidence     JSONB NOT NULL DEFAULT '[]',
    confidence   FLOAT NOT NULL DEFAULT 1.0,
    metadata     JSONB NOT NULL DEFAULT '{}',
    embedding    vector(1536),
    discovered_by TEXT NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(source_id, target_id, edge_type, identifier),
    CHECK(source_id != target_id)
);

CREATE TABLE IF NOT EXISTS unresolved (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    found_in_component_id    UUID REFERENCES components(id) ON DELETE SET NULL,
    reference_type           TEXT NOT NULL,
    reference_value          TEXT NOT NULL,
    context                  JSONB,
    embedding                vector(1536),
    resolved                 BOOLEAN NOT NULL DEFAULT FALSE,
    resolved_to_component_id UUID REFERENCES components(id) ON DELETE SET NULL,
    found_by_agent           TEXT,
    attempts                 INT NOT NULL DEFAULT 0,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS resource_component_agents (
    resource_id  UUID NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
    component_id UUID NOT NULL REFERENCES components(id) ON DELETE CASCADE,
    agent_id     TEXT NOT NULL REFERENCES agent_runs(agent_id) ON DELETE CASCADE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY(resource_id, component_id)
);

CREATE TABLE IF NOT EXISTS communications (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    from_agent  TEXT NOT NULL,
    to_agent    TEXT NOT NULL,
    type        TEXT NOT NULL CHECK (type IN ('task', 'broadcast', 'chat')),
    source_id   UUID,
    text        TEXT NOT NULL,
    metadata    JSONB NOT NULL DEFAULT '{}',
    acked_at    TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS merge_candidates (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    component_a_id UUID NOT NULL REFERENCES components(id),
    component_b_id UUID NOT NULL REFERENCES components(id),
    surviving_id   UUID REFERENCES components(id),
    tier           TEXT NOT NULL CHECK (tier IN ('auto', 'confirm', 'flag')),
    confidence     FLOAT NOT NULL,
    evidence       JSONB NOT NULL DEFAULT '[]',
    status         TEXT NOT NULL DEFAULT 'pending' CHECK (status IN (
                       'pending', 'pending_human', 'confirmed',
                       'rejected', 'executed', 'skipped')),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK(component_a_id != component_b_id)
);

CREATE TABLE IF NOT EXISTS abbreviations (
    short_form  TEXT NOT NULL,
    full_form   TEXT NOT NULL,
    plane       TEXT,
    created_by  TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY(short_form, full_form)
);

CREATE INDEX IF NOT EXISTS idx_agent_status
    ON agent_runs(status);
CREATE INDEX IF NOT EXISTS idx_agent_idle
    ON agent_runs(agent_type, status) WHERE status = 'idle';
CREATE INDEX IF NOT EXISTS idx_agent_locked
    ON agent_runs(trigger_lock) WHERE trigger_lock = TRUE;
CREATE INDEX IF NOT EXISTS idx_tq_pending
    ON trigger_queue(priority DESC, created_at ASC) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_res_pending
    ON resources(status) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_attr_component
    ON attributions(component_id);
CREATE INDEX IF NOT EXISTS idx_attr_type_id
    ON attributions(resource_type, identifier);
CREATE INDEX IF NOT EXISTS idx_mc_status
    ON merge_candidates(status);
"""


def init_db() -> None:
    """Create all V2 tables. Idempotent (IF NOT EXISTS throughout)."""
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(_SCHEMA)
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# agent_runs
# ---------------------------------------------------------------------------

def create_agent_run(
    agent_id: str,
    agent_type: str,
    workspace_path: str,
    plane: str | None = None,
    resource_id: str | None = None,
) -> None:
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO agent_runs
                   (agent_id, agent_type, workspace_path, plane, resource_id,
                    created_at, updated_at)
                   VALUES (%s, %s, %s, %s, %s, now(), now())""",
                (agent_id, agent_type, workspace_path, plane, resource_id),
            )
        conn.commit()
    finally:
        conn.close()


def get_agent(agent_id: str) -> dict | None:
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM agent_runs WHERE agent_id = %s", (agent_id,)
            )
            row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_agent_status(agent_id: str, status: str) -> None:
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE agent_runs SET status = %s, updated_at = now()"
                " WHERE agent_id = %s",
                (status, agent_id),
            )
        conn.commit()
    finally:
        conn.close()


def update_agent_session(agent_id: str, session_id: str) -> None:
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE agent_runs SET session_id = %s, updated_at = now()"
                " WHERE agent_id = %s",
                (session_id, agent_id),
            )
        conn.commit()
    finally:
        conn.close()


def update_agent_heartbeat(agent_id: str) -> None:
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE agent_runs SET heartbeat = now(), updated_at = now()"
                " WHERE agent_id = %s",
                (agent_id,),
            )
        conn.commit()
    finally:
        conn.close()


def increment_invocation_count(agent_id: str) -> None:
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE agent_runs"
                " SET invocation_count = invocation_count + 1, updated_at = now()"
                " WHERE agent_id = %s",
                (agent_id,),
            )
        conn.commit()
    finally:
        conn.close()


def agent_type_exists(agent_type: str) -> bool:
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM agent_runs"
                " WHERE agent_type = %s AND status != 'decommissioned' LIMIT 1",
                (agent_type,),
            )
            return cur.fetchone() is not None
    finally:
        conn.close()


def get_stale_running_agents(timeout_seconds: int) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=timeout_seconds)
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM agent_runs WHERE status = 'running'"
                " AND heartbeat < %s",
                (cutoff,),
            )
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# trigger_queue
# ---------------------------------------------------------------------------

def enqueue_trigger(agent_id: str, prompt: str, priority: int) -> str:
    trigger_id = str(uuid.uuid4())
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO trigger_queue (id, agent_id, prompt, priority,"
                " created_at) VALUES (%s, %s, %s, %s, now())",
                (trigger_id, agent_id, prompt, priority),
            )
        conn.commit()
        return trigger_id
    finally:
        conn.close()


def get_pending_triggers() -> list[dict]:
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM trigger_queue WHERE status = 'pending'"
                " ORDER BY priority DESC, created_at ASC"
            )
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def update_trigger_status(trigger_id: str, status: str) -> None:
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE trigger_queue SET status = %s WHERE id = %s",
                (status, trigger_id),
            )
        conn.commit()
    finally:
        conn.close()


def cancel_pending_triggers(agent_id: str) -> None:
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE trigger_queue SET status = 'done'"
                " WHERE agent_id = %s AND status = 'pending'",
                (agent_id,),
            )
        conn.commit()
    finally:
        conn.close()


def acquire_trigger_lock(agent_id: str) -> bool:
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE agent_runs"
                " SET trigger_lock = TRUE, status = 'invoking',"
                "     updated_at = now()"
                " WHERE agent_id = %s"
                "   AND status IN ('pending', 'idle')"
                "   AND trigger_lock = FALSE",
                (agent_id,),
            )
            changed = cur.rowcount > 0
        conn.commit()
        return changed
    finally:
        conn.close()


def release_trigger_lock(agent_id: str) -> None:
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE agent_runs SET trigger_lock = FALSE, updated_at = now()"
                " WHERE agent_id = %s",
                (agent_id,),
            )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# components
# ---------------------------------------------------------------------------

def upsert_component(
    canonical_name: str,
    display_name: str,
    component_type: str,
    metadata: dict | None = None,
    embedding: list[float] | None = None,
) -> str:
    """Insert or update a component. Returns component id."""
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO components
                   (canonical_name, display_name, component_type, metadata,
                    embedding, scanned_at, updated_at)
                   VALUES (%s, %s, %s, %s, %s, now(), now())
                   ON CONFLICT (canonical_name) DO UPDATE SET
                     display_name  = EXCLUDED.display_name,
                     metadata      = EXCLUDED.metadata,
                     embedding     = EXCLUDED.embedding,
                     scanned_at    = now(),
                     updated_at    = now()
                   RETURNING id""",
                (
                    canonical_name,
                    display_name,
                    component_type,
                    psycopg2.extras.Json(metadata or {}),
                    embedding,
                ),
            )
            row = cur.fetchone()
        conn.commit()
        return str(row["id"])
    finally:
        conn.close()


def get_component(component_id: str) -> dict | None:
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM components WHERE id = %s", (component_id,)
            )
            row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_component_embedding(component_id: str, embedding: list[float]) -> None:
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE components SET embedding = %s, updated_at = now()"
                " WHERE id = %s",
                (embedding, component_id),
            )
        conn.commit()
    finally:
        conn.close()


def decommission_component(component_id: str) -> None:
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE components SET status = 'decommissioned',"
                " updated_at = now() WHERE id = %s",
                (component_id,),
            )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# attributions
# ---------------------------------------------------------------------------

def upsert_attribution(
    component_id: str,
    plane: str,
    resource_type: str,
    identifier: str,
    evidence: str | None = None,
    confidence: float = 1.0,
    metadata: dict | None = None,
    embedding: list[float] | None = None,
    discovered_by: str | None = None,
) -> str:
    """Insert or update an attribution. Returns attribution id."""
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO attributions
                   (component_id, plane, resource_type, identifier, evidence,
                    confidence, metadata, embedding, discovered_by,
                    discovered_at, last_seen_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, now(), now())
                   ON CONFLICT (plane, resource_type, identifier) DO UPDATE SET
                     component_id  = EXCLUDED.component_id,
                     evidence      = EXCLUDED.evidence,
                     confidence    = EXCLUDED.confidence,
                     metadata      = EXCLUDED.metadata,
                     embedding     = EXCLUDED.embedding,
                     last_seen_at  = now()
                   RETURNING id""",
                (
                    component_id,
                    plane,
                    resource_type,
                    identifier,
                    evidence,
                    confidence,
                    psycopg2.extras.Json(metadata or {}),
                    embedding,
                    discovered_by,
                ),
            )
            row = cur.fetchone()
        conn.commit()
        return str(row["id"])
    finally:
        conn.close()


def get_attributions(component_id: str) -> list[dict]:
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM attributions WHERE component_id = %s",
                (component_id,),
            )
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def transfer_attributions(from_component_id: str, to_component_id: str) -> None:
    """Re-point all attributions from absorbed to surviving component."""
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE attributions SET component_id = %s"
                " WHERE component_id = %s",
                (to_component_id, from_component_id),
            )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# merge_candidates
# ---------------------------------------------------------------------------

def create_merge_candidate(
    component_a_id: str,
    component_b_id: str,
    tier: str,
    confidence: float,
    evidence: list[dict],
) -> str:
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO merge_candidates
                   (component_a_id, component_b_id, tier, confidence,
                    evidence, status)
                   VALUES (%s, %s, %s, %s, %s,
                     CASE %s WHEN 'auto' THEN 'pending'
                             WHEN 'confirm' THEN 'pending_human'
                             ELSE 'skipped' END)
                   RETURNING id""",
                (
                    component_a_id,
                    component_b_id,
                    tier,
                    confidence,
                    psycopg2.extras.Json(evidence),
                    tier,
                ),
            )
            row = cur.fetchone()
        conn.commit()
        return str(row["id"])
    finally:
        conn.close()


def get_merge_candidates(status: str) -> list[dict]:
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM merge_candidates WHERE status = %s", (status,)
            )
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def update_merge_candidate(
    candidate_id: str,
    status: str,
    surviving_id: str | None = None,
) -> None:
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE merge_candidates"
                " SET status = %s, surviving_id = %s, updated_at = now()"
                " WHERE id = %s",
                (status, surviving_id, candidate_id),
            )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# merge execution
# ---------------------------------------------------------------------------

def execute_merge_in_db(surviving_id: str, absorbed_id: str) -> None:
    """
    Atomically re-point all absorbed component's data to the surviving
    component and decommission the absorbed component.
    """
    conn = _connect()
    try:
        with conn.cursor() as cur:
            # Re-point attributions
            cur.execute(
                "UPDATE attributions SET component_id = %s"
                " WHERE component_id = %s",
                (surviving_id, absorbed_id),
            )
            # Re-point edges (source)
            cur.execute(
                "UPDATE edges SET source_id = %s WHERE source_id = %s",
                (surviving_id, absorbed_id),
            )
            # Re-point edges (target)
            cur.execute(
                "UPDATE edges SET target_id = %s WHERE target_id = %s",
                (surviving_id, absorbed_id),
            )
            # Re-point resource_component_agents
            cur.execute(
                "UPDATE resource_component_agents SET component_id = %s"
                " WHERE component_id = %s",
                (surviving_id, absorbed_id),
            )
            # Decommission absorbed
            cur.execute(
                "UPDATE components SET status = 'decommissioned',"
                " updated_at = now() WHERE id = %s",
                (absorbed_id,),
            )
        conn.commit()
    finally:
        conn.close()


def find_exact_attribute_matches() -> list[dict]:
    """
    Return all pairs of components that share an exact attribution
    (same resource_type + identifier). Each row has:
      component_a_id, component_b_id, resource_type, identifier
    """
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT a1.component_id AS component_a_id,
                          a2.component_id AS component_b_id,
                          a1.resource_type,
                          a1.identifier
                   FROM   attributions a1
                   JOIN   attributions a2
                     ON   a1.resource_type = a2.resource_type
                    AND   a1.identifier    = a2.identifier
                    AND   a1.component_id  < a2.component_id
                   JOIN   components ca ON ca.id = a1.component_id
                                      AND ca.status = 'active'
                   JOIN   components cb ON cb.id = a2.component_id
                                      AND cb.status = 'active'"""
            )
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def get_active_components() -> list[dict]:
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM components WHERE status = 'active'"
            )
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def edge_exists_between(component_a_id: str, component_b_id: str) -> bool:
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM edges WHERE"
                " (source_id = %s AND target_id = %s)"
                " OR (source_id = %s AND target_id = %s) LIMIT 1",
                (component_a_id, component_b_id,
                 component_b_id, component_a_id),
            )
            return cur.fetchone() is not None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# abbreviations
# ---------------------------------------------------------------------------

def upsert_abbreviation(
    short_form: str,
    full_form: str,
    plane: str | None = None,
    created_by: str | None = None,
) -> None:
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO abbreviations (short_form, full_form, plane, created_by)
                   VALUES (%s, %s, %s, %s)
                   ON CONFLICT (short_form, full_form) DO NOTHING""",
                (short_form, full_form, plane, created_by),
            )
        conn.commit()
    finally:
        conn.close()


def get_abbreviations() -> list[dict]:
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM abbreviations")
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()
```

- [ ] **Step 7: Run tests to verify they pass**

```bash
python -m pytest tests/test_db.py -v
```

Expected: all tests PASS.

- [ ] **Step 8: Commit**

```bash
git add src/agent_management/config.py src/agent_management/db.py \
        tests/conftest.py tests/test_db.py requirements.txt pyproject.toml
git commit -m "feat: replace SQLite with Postgres, implement V2 schema"
```

---

### Task 3: Embedding System

**Files:**
- Create: `src/agent_management/embedding.py`
- Create: `tests/test_embedding.py`

- [ ] **Step 1: Write failing tests**

Write to `tests/test_embedding.py`:

```python
import pytest
from unittest.mock import MagicMock, patch

from agent_management.embedding import synthesize_description, embed_text


def test_synthesize_description_minimal():
    component = {"component_type": "application", "canonical_name": "feeds-api"}
    attributions = []
    result = synthesize_description(component, attributions)
    assert "application" in result
    assert "feeds-api" in result


def test_synthesize_description_with_attributions():
    component = {"component_type": "application", "canonical_name": "feeds-api"}
    attributions = [
        {"resource_type": "hostname",      "identifier": "feeds-api.dream11.local"},
        {"resource_type": "entry_point",   "identifier": "FeedsApplication.java"},
        {"resource_type": "runtime",       "identifier": "java"},
        {"resource_type": "endpoint",      "identifier": "GET /v2/feeds/{userId}"},
        {"resource_type": "endpoint",      "identifier": "POST /v2/feeds/refresh"},
    ]
    result = synthesize_description(component, attributions)
    assert "feeds-api.dream11.local" in result
    assert "FeedsApplication.java" in result
    assert "java" in result
    assert "GET /v2/feeds/{userId}" in result


def test_synthesize_description_prioritises_discriminating_fields():
    component = {"component_type": "application", "canonical_name": "svc"}
    attributions = [
        {"resource_type": "region",      "identifier": "ap-south-1"},
        {"resource_type": "entry_point", "identifier": "Main.java"},
    ]
    result = synthesize_description(component, attributions)
    # entry_point should appear before region in the output
    assert result.index("Main.java") < result.index("ap-south-1")


def test_embed_text_calls_openai():
    fake_vector = [0.1] * 1536
    mock_response = MagicMock()
    mock_response.data = [MagicMock(embedding=fake_vector)]

    with patch("agent_management.embedding._get_openai_client") as mock_client:
        instance = mock_client.return_value
        instance.embeddings.create.return_value = mock_response
        result = embed_text("hello world")

    assert result == fake_vector
    instance.embeddings.create.assert_called_once()


def test_embed_text_uses_configured_model():
    fake_vector = [0.0] * 1536
    mock_response = MagicMock()
    mock_response.data = [MagicMock(embedding=fake_vector)]

    with patch("agent_management.embedding._get_openai_client") as mock_client:
        instance = mock_client.return_value
        instance.embeddings.create.return_value = mock_response
        embed_text("test")

    call_kwargs = instance.embeddings.create.call_args
    assert call_kwargs.kwargs["model"] == "text-embedding-3-small"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_embedding.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'agent_management.embedding'`

- [ ] **Step 3: Implement embedding.py**

Write to `src/agent_management/embedding.py`:

```python
"""Contextual description synthesis and embedding for Cartograph V2."""

from __future__ import annotations

import functools

from openai import OpenAI

from agent_management.config import get_config

# Attribute types in priority order — most discriminating first.
# Fields earlier in this list carry more semantic weight in the embedding.
_FIELD_PRIORITY = [
    "entry_point",
    "deploy_config",
    "hostname",
    "runtime",
    "region",
    "endpoint",
    "config_key",
    "repo",
    "asg",
    "load_balancer",
    "k8s_workload",
    "lambda",
    "rds",
    "datadog_service",
]


@functools.lru_cache(maxsize=1)
def _get_openai_client() -> OpenAI:
    cfg = get_config()
    return OpenAI(api_key=cfg.openai_api_key)


def synthesize_description(
    component: dict,
    attributions: list[dict],
) -> str:
    """
    Build a natural-language paragraph that describes what the component IS —
    not just what it is called. Richer descriptions produce better vector
    similarity across planes with different naming conventions.
    """
    parts = [
        f"{component['component_type']} named {component['canonical_name']}"
    ]

    # Group attributions by resource_type
    by_type: dict[str, list[str]] = {}
    for attr in attributions:
        by_type.setdefault(attr["resource_type"], []).append(attr["identifier"])

    # Emit fields in priority order
    for field in _FIELD_PRIORITY:
        if field in by_type:
            values = ", ".join(by_type[field])
            parts.append(f"{field}: {values}")

    # Emit any remaining fields not in the priority list
    for field, values in by_type.items():
        if field not in _FIELD_PRIORITY:
            parts.append(f"{field}: {', '.join(values)}")

    return ". ".join(parts)


def embed_text(text: str) -> list[float]:
    """Embed a string using the configured OpenAI embedding model."""
    cfg = get_config()
    client = _get_openai_client()
    response = client.embeddings.create(
        input=text,
        model=cfg.embedding_model,
    )
    return response.data[0].embedding


def embed_component(component: dict, attributions: list[dict]) -> list[float]:
    """Synthesize description and embed it. Use after every attribution write."""
    description = synthesize_description(component, attributions)
    return embed_text(description)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_embedding.py -v
```

Expected: all 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agent_management/embedding.py tests/test_embedding.py
git commit -m "feat: add embedding module with contextual description synthesis"
```

---

### Task 4: Union-Find

**Files:**
- Create: `src/agent_management/union_find.py`
- Create: `tests/test_union_find.py`

- [ ] **Step 1: Write failing tests**

Write to `tests/test_union_find.py`:

```python
from agent_management.union_find import UnionFind


def test_single_element():
    uf = UnionFind()
    uf.add("a")
    assert uf.find("a") == "a"


def test_union_two_elements():
    uf = UnionFind()
    uf.union("a", "b")
    assert uf.find("a") == uf.find("b")


def test_transitive_union():
    uf = UnionFind()
    uf.union("a", "b")
    uf.union("b", "c")
    assert uf.find("a") == uf.find("b") == uf.find("c")


def test_separate_groups():
    uf = UnionFind()
    uf.union("a", "b")
    uf.union("c", "d")
    assert uf.find("a") == uf.find("b")
    assert uf.find("c") == uf.find("d")
    assert uf.find("a") != uf.find("c")


def test_get_groups_returns_all():
    uf = UnionFind()
    uf.union("a", "b")
    uf.union("b", "c")
    uf.add("d")
    groups = uf.get_groups()
    assert len(groups) == 2  # {a,b,c} and {d}

    members = sorted([sorted(g) for g in groups])
    assert members == [["a", "b", "c"], ["d"]]


def test_get_groups_single_element_not_included():
    """Groups with only 1 member are not merge candidates — skip them."""
    uf = UnionFind()
    uf.union("a", "b")
    uf.add("lone")
    groups = uf.get_merge_groups()  # only groups with 2+ members
    assert len(groups) == 1
    assert sorted(groups[0]) == ["a", "b"]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_union_find.py -v
```

Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement union_find.py**

Write to `src/agent_management/union_find.py`:

```python
"""Union-Find for transitive merge grouping in the batch merger."""

from __future__ import annotations

from collections import defaultdict


class UnionFind:
    def __init__(self) -> None:
        self._parent: dict[str, str] = {}
        self._rank: dict[str, int] = {}

    def add(self, item: str) -> None:
        if item not in self._parent:
            self._parent[item] = item
            self._rank[item] = 0

    def find(self, item: str) -> str:
        self.add(item)
        if self._parent[item] != item:
            self._parent[item] = self.find(self._parent[item])  # path compression
        return self._parent[item]

    def union(self, a: str, b: str) -> None:
        self.add(a)
        self.add(b)
        root_a = self.find(a)
        root_b = self.find(b)
        if root_a == root_b:
            return
        # Union by rank
        if self._rank[root_a] < self._rank[root_b]:
            root_a, root_b = root_b, root_a
        self._parent[root_b] = root_a
        if self._rank[root_a] == self._rank[root_b]:
            self._rank[root_a] += 1

    def get_groups(self) -> list[list[str]]:
        """Return all groups (including singletons)."""
        buckets: dict[str, list[str]] = defaultdict(list)
        for item in self._parent:
            buckets[self.find(item)].append(item)
        return list(buckets.values())

    def get_merge_groups(self) -> list[list[str]]:
        """Return only groups with 2+ members (actual merge candidates)."""
        return [g for g in self.get_groups() if len(g) >= 2]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_union_find.py -v
```

Expected: all 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agent_management/union_find.py tests/test_union_find.py
git commit -m "feat: add UnionFind for transitive merge grouping"
```

---

### Task 5: Batch Merger — Candidate Finding and Tier Classification

**Files:**
- Create: `src/agent_management/batch_merger.py`
- Create: `tests/test_batch_merger.py`

- [ ] **Step 1: Write failing tests**

Write to `tests/test_batch_merger.py`:

```python
import pytest
from agent_management.db import init_db, upsert_component, upsert_attribution
from agent_management.batch_merger import (
    find_candidates,
    classify_tier,
    TIER_AUTO,
    TIER_CONFIRM,
    TIER_SKIP,
)


@pytest.fixture(autouse=True)
def setup_db():
    init_db()


def _make_component(name: str, ctype: str = "application") -> str:
    return upsert_component(
        canonical_name=name,
        display_name=name,
        component_type=ctype,
    )


def test_find_candidates_exact_hostname_match():
    a = _make_component("feeds-aggregator-v2")
    b = _make_component("fav2-api-prod")
    upsert_attribution(a, "github", "hostname", "feeds-agg.dream11.local")
    upsert_attribution(b, "cloud",  "hostname", "feeds-agg.dream11.local")

    candidates = find_candidates()
    pairs = {
        (min(c["component_a_id"], c["component_b_id"]),
         max(c["component_a_id"], c["component_b_id"]))
        for c in candidates
    }
    assert (min(a, b), max(a, b)) in pairs


def test_find_candidates_no_match():
    a = _make_component("service-alpha")
    b = _make_component("service-beta")
    upsert_attribution(a, "github", "hostname", "alpha.dream11.local")
    upsert_attribution(b, "github", "hostname", "beta.dream11.local")

    candidates = find_candidates()
    assert candidates == []


def test_find_candidates_deduplicates_same_pair():
    a = _make_component("svc-a")
    b = _make_component("svc-b")
    # Two matching attributions between same pair
    upsert_attribution(a, "github", "hostname", "shared.local")
    upsert_attribution(b, "cloud",  "hostname", "shared.local")
    upsert_attribution(a, "github", "repo",     "org/shared-repo")
    upsert_attribution(b, "deploy", "repo",     "org/shared-repo")

    candidates = find_candidates()
    pair_ids = [
        (min(c["component_a_id"], c["component_b_id"]),
         max(c["component_a_id"], c["component_b_id"]))
        for c in candidates
    ]
    # Should appear once per matching attribute but union-find groups them
    assert len(set(pair_ids)) == 1


def test_classify_tier_strong_single_attr():
    evidence = [{"resource_type": "entry_point", "identifier": "Main.java",
                 "match_type": "exact"}]
    assert classify_tier(evidence, has_hard_block=False) == TIER_AUTO


def test_classify_tier_two_weak_attrs():
    evidence = [
        {"resource_type": "hostname", "identifier": "svc.local",
         "match_type": "exact"},
        {"resource_type": "repo",     "identifier": "org/svc",
         "match_type": "exact"},
    ]
    assert classify_tier(evidence, has_hard_block=False) == TIER_AUTO


def test_classify_tier_one_weak_attr():
    evidence = [{"resource_type": "hostname", "identifier": "svc.local",
                 "match_type": "exact"}]
    assert classify_tier(evidence, has_hard_block=False) == TIER_CONFIRM


def test_classify_tier_hard_block_overrides():
    evidence = [{"resource_type": "entry_point", "identifier": "Main.java",
                 "match_type": "exact"}]
    assert classify_tier(evidence, has_hard_block=True) == TIER_SKIP


def test_classify_tier_no_evidence():
    assert classify_tier([], has_hard_block=False) == TIER_SKIP
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_batch_merger.py -v
```

Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement batch_merger.py — candidate finding and tier classification**

Write to `src/agent_management/batch_merger.py`:

```python
"""
Batch Merger — V2 replacement for SME negotiation.

Runs as code after all SMEs finish materialisation. No LLM involved.
Finds merge candidates, classifies them by confidence tier, and executes
AUTO merges. CONFIRM candidates are surfaced to the human admin.
"""

from __future__ import annotations

import logging

from agent_management import db
from agent_management.union_find import UnionFind

logger = logging.getLogger(__name__)

TIER_AUTO    = "auto"
TIER_CONFIRM = "confirm"
TIER_FLAG    = "flag"
TIER_SKIP    = "skip"

# Attributes that are conclusive on their own (same entry_point = same process)
_STRONG_ATTRS = {"entry_point", "deploy_config"}

# Attributes that need 2+ to form a confident match
_WEAK_ATTRS = {"hostname", "repo", "asg", "k8s_workload", "datadog_service",
               "load_balancer", "config_key"}

# Runtime/type mismatches that block a merge regardless of name similarity
_BLOCKING_RUNTIME_PAIRS = frozenset([
    ("java", "python"), ("java", "node"), ("java", "go"),
    ("python", "node"), ("python", "go"), ("node", "go"),
])


def find_candidates() -> list[dict]:
    """
    Find all pairs of active components that share at least one exact
    attribution (same resource_type + identifier).

    Returns a deduplicated list of dicts:
      { component_a_id, component_b_id, evidence: [{resource_type, identifier, match_type}] }
    """
    raw_matches = db.find_exact_attribute_matches()

    # Group matches by (component_a_id, component_b_id) pair
    pair_evidence: dict[tuple[str, str], list[dict]] = {}
    for row in raw_matches:
        key = (str(row["component_a_id"]), str(row["component_b_id"]))
        pair_evidence.setdefault(key, []).append({
            "resource_type": row["resource_type"],
            "identifier":    row["identifier"],
            "match_type":    "exact",
        })

    return [
        {
            "component_a_id": k[0],
            "component_b_id": k[1],
            "evidence": v,
        }
        for k, v in pair_evidence.items()
    ]


def _has_hard_block(component_a_id: str, component_b_id: str) -> bool:
    """
    Return True if merging these two components is blocked regardless of
    evidence quality.
    """
    # Block if an edge already exists between them (caller/callee pair)
    if db.edge_exists_between(component_a_id, component_b_id):
        logger.debug(
            "Hard block: edge exists between %s and %s",
            component_a_id, component_b_id,
        )
        return True

    comp_a = db.get_component(component_a_id)
    comp_b = db.get_component(component_b_id)
    if not comp_a or not comp_b:
        return True

    # Block if component types differ
    if comp_a["component_type"] != comp_b["component_type"]:
        logger.debug(
            "Hard block: type mismatch %s vs %s",
            comp_a["component_type"], comp_b["component_type"],
        )
        return True

    # Block if runtimes are known-incompatible
    attrs_a = {a["resource_type"]: a["identifier"]
               for a in db.get_attributions(component_a_id)}
    attrs_b = {a["resource_type"]: a["identifier"]
               for a in db.get_attributions(component_b_id)}
    rt_a = attrs_a.get("runtime", "").lower()
    rt_b = attrs_b.get("runtime", "").lower()
    if rt_a and rt_b and rt_a != rt_b:
        pair = tuple(sorted([rt_a, rt_b]))
        if pair in _BLOCKING_RUNTIME_PAIRS:
            logger.debug("Hard block: runtime mismatch %s vs %s", rt_a, rt_b)
            return True

    return False


def classify_tier(evidence: list[dict], has_hard_block: bool) -> str:
    """
    Determine the confidence tier for a merge candidate.

    AUTO    — execute immediately, no human needed
    CONFIRM — surface to human for bulk approval
    FLAG    — record only, do not merge
    SKIP    — discard
    """
    if has_hard_block or not evidence:
        return TIER_SKIP

    matched_types = {e["resource_type"] for e in evidence}

    # One strong attribute is sufficient for AUTO
    if matched_types & _STRONG_ATTRS:
        return TIER_AUTO

    # Two or more weak attributes corroborating → AUTO
    weak_hits = matched_types & _WEAK_ATTRS
    if len(weak_hits) >= 2:
        return TIER_AUTO

    # Exactly one weak attribute → CONFIRM
    if len(weak_hits) == 1:
        return TIER_CONFIRM

    return TIER_SKIP


def _pick_surviving(component_a_id: str, component_b_id: str) -> tuple[str, str]:
    """
    Return (surviving_id, absorbed_id).
    Surviving = the component with more attributions (more evidence).
    """
    count_a = len(db.get_attributions(component_a_id))
    count_b = len(db.get_attributions(component_b_id))
    if count_a >= count_b:
        return component_a_id, component_b_id
    return component_b_id, component_a_id


def run_batch_merge(
    re_embed_fn: "Callable[[dict, list[dict]], list[float]] | None" = None,
) -> dict:
    """
    Full batch merge pass. Call after all SMEs finish materialisation.

    re_embed_fn: optional callable(component, attributions) -> vector.
                 If provided, surviving component is re-embedded after merge.
                 Pass None in tests to skip embedding.

    Returns summary dict:
      { auto_executed: N, pending_human: N, skipped: N }
    """
    candidates = find_candidates()
    logger.info("Batch merger: found %d raw candidate pairs", len(candidates))

    summary = {"auto_executed": 0, "pending_human": 0, "skipped": 0}

    for cand in candidates:
        a_id = cand["component_a_id"]
        b_id = cand["component_b_id"]
        evidence = cand["evidence"]

        hard_block = _has_hard_block(a_id, b_id)
        tier = classify_tier(evidence, hard_block)

        if tier == TIER_SKIP:
            summary["skipped"] += 1
            continue

        candidate_id = db.create_merge_candidate(
            component_a_id=a_id,
            component_b_id=b_id,
            tier=tier,
            confidence=_confidence_score(evidence),
            evidence=evidence,
        )

        if tier == TIER_AUTO:
            surviving_id, absorbed_id = _pick_surviving(a_id, b_id)
            db.execute_merge_in_db(surviving_id, absorbed_id)
            db.update_merge_candidate(candidate_id, "executed", surviving_id)

            if re_embed_fn is not None:
                component = db.get_component(surviving_id)
                attributions = db.get_attributions(surviving_id)
                vector = re_embed_fn(component, attributions)
                db.update_component_embedding(surviving_id, vector)

            logger.info(
                "Auto-merged %s ← %s (evidence: %s)",
                surviving_id, absorbed_id,
                [e["resource_type"] for e in evidence],
            )
            summary["auto_executed"] += 1

        elif tier == TIER_CONFIRM:
            # Status already set to 'pending_human' by create_merge_candidate
            logger.info(
                "Merge candidate surfaced for human review: %s ↔ %s",
                a_id, b_id,
            )
            summary["pending_human"] += 1

    return summary


def _confidence_score(evidence: list[dict]) -> float:
    """
    Heuristic confidence score based on evidence strength.
    Strong attrs score 0.95, weak attrs 0.70 each (capped at 0.99).
    """
    if not evidence:
        return 0.0
    score = 0.0
    for e in evidence:
        if e["resource_type"] in _STRONG_ATTRS:
            score = max(score, 0.95)
        elif e["resource_type"] in _WEAK_ATTRS:
            score = max(score, min(score + 0.20, 0.90))
    return round(min(score, 0.99), 2)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_batch_merger.py -v
```

Expected: all 8 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agent_management/batch_merger.py tests/test_batch_merger.py
git commit -m "feat: add batch merger with candidate finding and tier classification"
```

---

### Task 6: Trigger Manager — Phase Management

**Files:**
- Modify: `src/agent_management/trigger_manager.py`
- Modify: `tests/test_trigger_manager.py`

- [ ] **Step 1: Write failing tests for phase management**

Append to `tests/test_trigger_manager.py`:

```python
from agent_management.trigger_manager import Phase


def test_phase_enum_values():
    assert Phase.ITERATION.value == "iteration"
    assert Phase.MATERIALISATION.value == "materialisation"
    assert Phase.BATCH_MERGE.value == "batch_merge"
    assert Phase.RESOLUTION.value == "resolution"
    assert Phase.EDGE_DISCOVERY.value == "edge_discovery"
    assert Phase.USER_FEEDBACK.value == "user_feedback"


def test_trigger_manager_has_current_phase(manager):
    trigger_mgr = TriggerManager(
        agent_manager=manager,
        poll_interval=0.1,
        heartbeat_timeout=300,
    )
    assert trigger_mgr.current_phase == Phase.ITERATION
```

- [ ] **Step 2: Run new tests to verify they fail**

```bash
python -m pytest tests/test_trigger_manager.py::test_phase_enum_values \
                 tests/test_trigger_manager.py::test_trigger_manager_has_current_phase -v
```

Expected: FAIL — `ImportError: cannot import name 'Phase'`

- [ ] **Step 3: Update trigger_manager.py**

Write to `src/agent_management/trigger_manager.py`:

```python
"""Trigger Manager — polls for pending triggers and invokes agents.

V2: adds Phase state machine and Batch Merge phase driven as code.
"""

from __future__ import annotations

import logging
import threading
import time
from enum import Enum

from agent_management import db
from agent_management.agent_manager import AgentManager

logger = logging.getLogger(__name__)


class Phase(Enum):
    ITERATION      = "iteration"
    MATERIALISATION = "materialisation"
    BATCH_MERGE    = "batch_merge"
    RESOLUTION     = "resolution"
    EDGE_DISCOVERY = "edge_discovery"
    USER_FEEDBACK  = "user_feedback"


class TriggerManager:
    def __init__(
        self,
        agent_manager: AgentManager,
        poll_interval: float = 2.0,
        heartbeat_timeout: int = 300,
    ) -> None:
        self.agent_manager = agent_manager
        self.poll_interval = poll_interval
        self.heartbeat_timeout = heartbeat_timeout
        self.current_phase = Phase.ITERATION
        self.running = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self.running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        logger.info(
            "Trigger manager started (poll_interval=%.1fs)", self.poll_interval
        )

    def stop(self) -> None:
        self.running = False
        if self._thread is not None:
            self._thread.join(timeout=self.poll_interval * 2)
        logger.info("Trigger manager stopped")

    def advance_phase(self, phase: Phase) -> None:
        logger.info(
            "Phase transition: %s → %s",
            self.current_phase.value, phase.value,
        )
        self.current_phase = phase

    def _loop(self) -> None:
        while self.running:
            try:
                self._handle_stale_agents()
                if self.current_phase == Phase.BATCH_MERGE:
                    self._run_batch_merge_phase()
                else:
                    self._process_pending_triggers()
            except Exception:
                logger.exception("Error in trigger manager loop")
            time.sleep(self.poll_interval)

    def _handle_stale_agents(self) -> None:
        stale = db.get_stale_running_agents(self.heartbeat_timeout)
        for agent in stale:
            agent_id = agent["agent_id"]
            logger.warning("Agent %s is stale, marking as errored", agent_id)
            db.update_agent_status(agent_id, "errored")
            db.release_trigger_lock(agent_id)

    def _process_pending_triggers(self) -> None:
        triggers = db.get_pending_triggers()
        for trigger in triggers:
            if not self.running:
                break

            agent = db.get_agent(trigger["agent_id"])
            if agent is None:
                db.update_trigger_status(trigger["id"], "done")
                continue

            if agent["status"] not in ("pending", "idle"):
                continue

            if not db.acquire_trigger_lock(trigger["agent_id"]):
                continue

            db.update_trigger_status(trigger["id"], "processing")
            try:
                self.agent_manager.invoke_agent(
                    trigger["agent_id"], trigger["prompt"]
                )
            except Exception:
                logger.exception(
                    "Failed to invoke agent %s", trigger["agent_id"]
                )
                db.update_agent_status(trigger["agent_id"], "errored")
                db.release_trigger_lock(trigger["agent_id"])
            finally:
                db.update_trigger_status(trigger["id"], "done")

    def _run_batch_merge_phase(self) -> None:
        """
        Execute the batch merge phase.
        Runs once when the phase is active, then advances to RESOLUTION.
        Imported here to avoid circular imports.
        """
        from agent_management.batch_merger import run_batch_merge
        from agent_management.embedding import embed_component

        logger.info("Starting batch merge phase")
        summary = run_batch_merge(re_embed_fn=embed_component)
        logger.info(
            "Batch merge complete: auto_executed=%d pending_human=%d skipped=%d",
            summary["auto_executed"],
            summary["pending_human"],
            summary["skipped"],
        )

        if summary["pending_human"] > 0:
            logger.info(
                "%d candidates awaiting human confirmation",
                summary["pending_human"],
            )
            # Stay in BATCH_MERGE phase until human confirms/rejects all
            pending = db.get_merge_candidates("pending_human")
            if not pending:
                self.advance_phase(Phase.RESOLUTION)
        else:
            self.advance_phase(Phase.RESOLUTION)
```

- [ ] **Step 4: Run all trigger manager tests**

```bash
python -m pytest tests/test_trigger_manager.py -v
```

Expected: all tests PASS including the two new ones.

- [ ] **Step 5: Commit**

```bash
git add src/agent_management/trigger_manager.py tests/test_trigger_manager.py
git commit -m "feat: add Phase enum and batch merge phase to trigger manager"
```

---

### Task 7: Agent Type Updates — Remove Resolver, Update SME and Orchestrator

**Files:**
- Delete: `src/agent_management/agent_types/resolver.py`
- Modify: `src/agent_management/agent_types/sme.py`
- Modify: `src/agent_management/agent_types/orchestrator.py`
- Modify: `src/agent_management/agent_manager.py`
- Modify: `src/main.py`
- Modify: `tests/test_agent_types.py`

- [ ] **Step 1: Delete resolver.py**

```bash
rm src/agent_management/agent_types/resolver.py
```

- [ ] **Step 2: Update base.py factory — remove resolver**

Edit `src/agent_management/agent_types/base.py` — remove the resolver branch:

```python
"""Agent type contract and factory function."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentTypeConfig:
    agent_type: str
    allowed_tools: list[str]
    mcp_servers: list[str]
    system_prompt: str
    priority: int
    can_install: bool


def get_config(agent_type: str, **kwargs: str) -> AgentTypeConfig:
    if agent_type == "orchestrator":
        from agent_management.agent_types.orchestrator import build_config
        return build_config(**kwargs)
    elif agent_type == "iterator":
        from agent_management.agent_types.iterator import build_config
        return build_config(**kwargs)
    elif agent_type == "sme":
        from agent_management.agent_types.sme import build_config
        return build_config(**kwargs)
    else:
        raise ValueError(f"Unknown agent type: {agent_type}")
```

- [ ] **Step 3: Update sme.py for V2 ephemeral lifecycle**

Write to `src/agent_management/agent_types/sme.py`:

```python
"""SME agent type configuration — V2 ephemeral lifecycle."""

from agent_management.agent_types.base import AgentTypeConfig

SYSTEM_PROMPT_TEMPLATE = """\
You are an SME (Subject Matter Expert) agent in the Cartograph system.

== YOUR IDENTITY ==
- Agent ID: {agent_id}
- Type: sme
- Assigned resource: {resource_identifier} on plane {plane}
- Lifecycle: EPHEMERAL — you exist only for the duration of materialisation.
  Once you yield after completing materialisation, your session is discarded.
  All your work must be written to the DB before you yield.

== THE SYSTEM ==
Cartograph discovers and maps every deployable component across an organisation.
You are part of a multi-agent system with:
  - Orchestrator: coordinates phases, assigns tasks, handles blockers
  - Iterator: lists resources (already done before you are spawned)
  - SME (you): deeply analyses one resource, builds components and attributions
  - Merging: handled by a batch process AFTER all SMEs complete — not by you

There is NO consolidation phase involving you. You do NOT negotiate with other
SMEs. After materialisation, a batch process matches components automatically.

== YOUR JOB ==

Materialisation (your only active phase):
  - Deeply analyse your assigned resource
  - For GitHub: clone repo, find deploy artifacts, scan for endpoints
    (JAX-RS, Spring, Express), outbound HTTP calls, config refs
  - For Deploy: scan Helm charts, Odin specs, ArgoCD configs
  - For Cloud: walk infra chain (R53 → ALB → TG → ASG, K8s workloads)
  - For Telemetry: query traces, logs, metrics, service map
  - For Config (supporter): resolve config key references only.
    Do NOT create new components.
  - For each component you discover:
    1. Exact name/hostname match in DB → attribute to existing (conf=1.0)
    2. No exact match → create new component, embed immediately
  - Write ALL attributions exhaustively:
    hostname, entry_point, deploy_config, endpoints, runtime, region,
    repo, config_keys, asg_name — everything you find
  - Write outbound calls as unresolved references
  - Re-embed your component after every attribution you add
    (the embedding becomes richer, improving batch merge accuracy)
  - Raise blockers for missing tools or access issues
  - When complete: yield. Session will be discarded.

== EMBEDDING QUALITY ==
The batch merge system matches components across planes using vector similarity
on synthesized descriptions. The more attributions you write, the better the
matching. A hostname or entry_point attribution is the single most valuable
piece of evidence — always try to find and write these.

== TOOLS ==
  Read:
    - get_action_items_summary(agent_id)
    - get_my_tasks(agent_id)
    - get_task_thread(task_id)
    - get_component(component_id)
    - get_attributions(component_id)
    - get_unacked_chats(agent_id)
    - vector_search(query_text, table, limit)

  Act:
    - upsert_component(agent_id, component_data)
    - upsert_attribution(agent_id, component_id, attribution_data)
    - create_edge(agent_id, edge_data)
    - insert_unresolved(agent_id, unresolved_data)
    - resolve_reference(agent_id, unresolved_id, resolved_to_component_id)
    - respond_task(agent_id, task_id, message, new_status, blocker_detail?)
    - raise_blocker(agent_id, task_id, blocker_detail)
    - send_chat(from_agent_id, to_agent_id, message)
    - ack_chats(agent_id, communication_ids[])

  Plane MCP (read-only, scoped to your assigned resource):
    {plane_tools}

  Bash: available but you CANNOT install anything — raise a blocker instead.

== RULES ==
  - Write ALL attributions before yielding — the DB is the only state that survives
  - Re-embed after every attribution write
  - Never hold back an attribution thinking "I'll add it later" — there is no later
  - Admin messages are highest priority
  - Always use YOUR agent_id in all tool calls
  - On tool failure: retry once, then raise blocker if critical
"""


def build_config(**kwargs: str) -> AgentTypeConfig:
    plane = kwargs.get("plane", "unknown")
    resource_id = kwargs.get("resource_id", "unknown")
    agent_id = kwargs.get("agent_id", "unknown")
    mcp_registry_keys = kwargs.get("mcp_registry_keys", "")
    plane_tools = f"{plane}-reader" if f"{plane}-reader" in mcp_registry_keys else ""
    return AgentTypeConfig(
        agent_type="sme",
        allowed_tools=["bash", "Read", "Glob", "Grep"],
        mcp_servers=["cartograph-db", f"{plane}-reader"],
        system_prompt=SYSTEM_PROMPT_TEMPLATE.format(
            plane=plane,
            resource_identifier=resource_id,
            agent_id=agent_id,
            plane_tools=plane_tools,
        ),
        priority=40,
        can_install=False,
    )
```

- [ ] **Step 4: Update orchestrator.py for V2 phases**

Write to `src/agent_management/agent_types/orchestrator.py`:

```python
"""Orchestrator agent type configuration — V2."""

from agent_management.agent_types.base import AgentTypeConfig

SYSTEM_PROMPT = """\
You are the Orchestrator agent in the Cartograph system.

== YOUR IDENTITY ==
- Agent ID: {agent_id}
- Type: orchestrator
- Lifecycle: PERSISTENT — you exist for the lifetime of the system.

== THE SYSTEM ==
Cartograph discovers and maps every deployable component across an organisation.
Agent types:
  - Orchestrator (you): coordinates phases, handles blockers, talks to the user
  - Iterator: one per plane, lists resources, terminates after
  - SME: one per resource, materialises components + attributions, terminates after

There is NO Resolver agent in V2. Merging is done automatically by the batch
merger (code, not an agent) after all SMEs finish materialisation.

== PHASES ==
  1. USER INPUT: collect credentials, validate, store in secrets, create iterators
  2. ITERATION: iterators list resources per plane — wait for all to complete
  3. MATERIALISATION: SMEs analyse resources — auto-spawned by trigger manager.
     Monitor progress. Handle blockers.
  4. BATCH MERGE: trigger manager runs automatically — no action needed from you
     unless CONFIRM candidates need to be surfaced to the user for approval
  5. RESOLUTION: config SMEs resolve remaining refs
  6. EDGE DISCOVERY: SMEs resolve outbound calls to edges
  7. USER FEEDBACK: present results, handle corrections

== YOUR TOOLS ==
  Read:
    - get_action_items_summary(agent_id)
    - get_action_items_detail(agent_id)
    - get_my_tasks(agent_id)
    - get_task_thread(task_id)
    - get_component(component_id)
    - get_attributions(component_id)
    - get_merge_candidates(status)
    - get_unacked_chats(agent_id)
    - vector_search(query_text, table, limit)

  Act:
    - create_task(owner_agent_id, worker_agent_id, description)
    - respond_task(agent_id, task_id, message, new_status, blocker_detail?)
    - confirm_merge_candidate(agent_id, candidate_id)
    - reject_merge_candidate(agent_id, candidate_id)
    - send_chat(from_agent_id, to_agent_id, message)
    - send_broadcast(from_agent_id, to_agent_type, message)
    - ack_chats(agent_id, communication_ids[])

  Bash: available

== RULES ==
  - You do NOT perform discovery or analysis — delegate to SMEs
  - You do NOT install tools — assign to the relevant plane's iterator
  - For CONFIRM merge candidates: present to the user clearly and ask for
    bulk approve/reject before the trigger manager can advance to RESOLUTION
  - Admin messages are highest priority
  - Always use YOUR agent_id in all tool calls
  - Check get_action_items_summary() first on every wake
"""


def build_config(**kwargs: str) -> AgentTypeConfig:
    agent_id = kwargs.get("agent_id", "unknown")
    return AgentTypeConfig(
        agent_type="orchestrator",
        allowed_tools=["bash", "Read", "Write", "Edit", "Glob", "Grep"],
        mcp_servers=["cartograph-db"],
        system_prompt=SYSTEM_PROMPT.format(agent_id=agent_id),
        priority=100,
        can_install=False,
    )
```

- [ ] **Step 5: Update agent_manager.py — no Resolver type**

Edit `src/agent_management/agent_manager.py` — remove `"resolver"` from `_TYPE_PREFIXES` and update `_initial_prompt`:

```python
"""Agent Manager — create, invoke, and deactivate agents."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import uuid

import yaml

from agent_management import db
from agent_management.agent_types.base import get_config

logger = logging.getLogger(__name__)

_TYPE_PREFIXES = {
    "orchestrator": "orch",
    "iterator":     "iter",
    "sme":          "sme",
}


class AgentManager:
    def __init__(self, workspace_root: str, mcp_config_path: str) -> None:
        self.workspace_root = workspace_root
        with open(mcp_config_path) as f:
            self.mcp_registry: dict = yaml.safe_load(f)

    def create_agent(
        self,
        agent_type: str,
        plane: str | None = None,
        resource_id: str | None = None,
    ) -> str:
        short_id = uuid.uuid4().hex[:8]
        prefix = _TYPE_PREFIXES[agent_type]
        if agent_type == "iterator" and plane:
            agent_id = f"{prefix}-{plane}-{short_id}"
        else:
            agent_id = f"{prefix}-{short_id}"

        config = get_config(
            agent_type,
            plane=plane or "",
            resource_id=resource_id or "",
            agent_id=agent_id,
            mcp_registry_keys=",".join(self.mcp_registry.keys()),
        )

        workspace_path = os.path.join(self.workspace_root, agent_id)
        os.makedirs(workspace_path, exist_ok=True)

        self._write_mcp_json(workspace_path, config.mcp_servers)

        db.create_agent_run(
            agent_id=agent_id,
            agent_type=agent_type,
            workspace_path=workspace_path,
            plane=plane,
            resource_id=resource_id,
        )

        initial_prompt = self._initial_prompt(agent_type, plane, resource_id)
        db.enqueue_trigger(agent_id, initial_prompt, config.priority)

        return agent_id

    def invoke_agent(self, agent_id: str, prompt: str) -> str:
        agent = db.get_agent(agent_id)
        if agent is None:
            raise ValueError(f"Agent not found: {agent_id}")

        config = get_config(
            agent["agent_type"],
            plane=agent.get("plane") or "",
            resource_id=agent.get("resource_id") or "",
            agent_id=agent_id,
            mcp_registry_keys=",".join(self.mcp_registry.keys()),
        )

        cmd = [
            "claude",
            "-p", prompt,
            "--output-format", "json",
            "--allowedTools", ",".join(config.allowed_tools),
            "--system-prompt", config.system_prompt,
            "--cwd", agent["workspace_path"],
        ]
        if agent["session_id"]:
            cmd.extend(["--session-id", agent["session_id"]])

        db.update_agent_status(agent_id, "running")
        db.release_trigger_lock(agent_id)
        db.update_agent_heartbeat(agent_id)

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,
            )

            if result.returncode != 0:
                logger.error("Agent %s failed: %s", agent_id, result.stderr)
                db.update_agent_status(agent_id, "errored")
                return result.stderr

            output = result.stdout

            if agent["session_id"] is None:
                session_id = self._extract_session_id(output)
                if session_id:
                    db.update_agent_session(agent_id, session_id)

            db.update_agent_status(agent_id, "idle")
            db.increment_invocation_count(agent_id)
            db.update_agent_heartbeat(agent_id)
            return output

        except subprocess.TimeoutExpired:
            logger.error("Agent %s timed out", agent_id)
            db.update_agent_status(agent_id, "errored")
            return "TIMEOUT"

    def deactivate_agent(self, agent_id: str) -> None:
        db.update_agent_status(agent_id, "decommissioned")
        db.cancel_pending_triggers(agent_id)

    def _write_mcp_json(
        self, workspace_path: str, mcp_server_names: list[str]
    ) -> None:
        servers = {}
        for name in mcp_server_names:
            if name in self.mcp_registry:
                servers[name] = {"url": self.mcp_registry[name]["url"]}
        mcp_json = {"mcpServers": servers}
        path = os.path.join(workspace_path, ".mcp.json")
        with open(path, "w") as f:
            json.dump(mcp_json, f, indent=2)

    def _initial_prompt(
        self,
        agent_type: str,
        plane: str | None,
        resource_id: str | None,
    ) -> str:
        if agent_type == "orchestrator":
            return (
                "System boot. You are the orchestrator. "
                "Review the current state of agent_runs and begin coordination."
            )
        elif agent_type == "iterator":
            return (
                f"Begin iteration for the {plane} plane. "
                "List all accessible resources and insert them into the resources table."
            )
        elif agent_type == "sme":
            return (
                f"You have been assigned resource {resource_id} from the {plane} plane. "
                "Begin materialisation — analyse the resource and write all components "
                "and attributions. Re-embed after every attribution write. "
                "Yield when complete."
            )
        return "Begin work."

    def _extract_session_id(self, output: str) -> str | None:
        try:
            data = json.loads(output)
            return data.get("session_id")
        except (json.JSONDecodeError, TypeError):
            return None
```

- [ ] **Step 6: Update main.py — no Resolver**

Write to `src/main.py`:

```python
"""Cartograph Agent Runtime V2 — entry point."""

from __future__ import annotations

import logging
import os
import signal
import sys

from agent_management import db
from agent_management.agent_manager import AgentManager
from agent_management.trigger_manager import TriggerManager

logger = logging.getLogger(__name__)

_DEFAULT_WORKSPACE_ROOT = "workspaces"
_DEFAULT_MCP_CONFIG = os.path.join(
    os.path.dirname(__file__), "agent_management", "mcp_servers.yaml"
)


def boot(
    workspace_root: str = _DEFAULT_WORKSPACE_ROOT,
    mcp_config_path: str = _DEFAULT_MCP_CONFIG,
    start_trigger_manager: bool = True,
    poll_interval: float = 2.0,
    heartbeat_timeout: int = 300,
) -> TriggerManager:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    db.init_db()

    os.makedirs(workspace_root, exist_ok=True)

    manager = AgentManager(
        workspace_root=workspace_root,
        mcp_config_path=mcp_config_path,
    )

    if not db.agent_type_exists("orchestrator"):
        manager.create_agent("orchestrator")
        logger.info("Created orchestrator agent")

    trigger_mgr = TriggerManager(
        agent_manager=manager,
        poll_interval=poll_interval,
        heartbeat_timeout=heartbeat_timeout,
    )

    if start_trigger_manager:
        trigger_mgr.start()
        logger.info("Cartograph V2 Agent Runtime is running")

    return trigger_mgr


def main() -> None:
    trigger_mgr = boot()

    def handle_shutdown(signum, frame):
        logger.info("Shutting down...")
        trigger_mgr.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    signal.pause()


if __name__ == "__main__":
    main()
```

- [ ] **Step 7: Update test_agent_types.py — remove resolver test**

Edit `tests/test_agent_types.py` — remove the `test_get_config_resolver` test and update `test_get_config_unknown_type_raises` to no longer expect resolver to be valid:

```python
import pytest
from agent_management.agent_types.base import AgentTypeConfig, get_config


def test_agent_type_config_dataclass():
    config = AgentTypeConfig(
        agent_type="orchestrator",
        allowed_tools=["bash", "Read"],
        mcp_servers=["cartograph-db"],
        system_prompt="You are the orchestrator.",
        priority=100,
        can_install=False,
    )
    assert config.agent_type == "orchestrator"
    assert config.priority == 100
    assert config.can_install is False


def test_get_config_orchestrator():
    config = get_config("orchestrator", agent_id="orch-test")
    assert config.agent_type == "orchestrator"
    assert config.priority == 100
    assert config.can_install is False
    assert "bash" in config.allowed_tools
    assert "cartograph-db" in config.mcp_servers
    assert "Orchestrator" in config.system_prompt


def test_get_config_iterator_with_plane():
    config = get_config("iterator", plane="github")
    assert config.agent_type == "iterator"
    assert config.priority == 60
    assert config.can_install is True
    assert "github" in config.system_prompt


def test_get_config_sme_with_resource():
    config = get_config(
        "sme", plane="github", resource_id="repo-xyz", agent_id="sme-test"
    )
    assert config.agent_type == "sme"
    assert config.priority == 40
    assert config.can_install is False
    assert "repo-xyz" in config.system_prompt
    assert "github" in config.system_prompt


def test_get_config_resolver_raises():
    with pytest.raises(ValueError, match="Unknown agent type"):
        get_config("resolver")


def test_get_config_unknown_type_raises():
    with pytest.raises(ValueError, match="Unknown agent type"):
        get_config("unknown")
```

- [ ] **Step 8: Run full test suite**

```bash
python -m pytest tests/ -v
```

Expected: all tests PASS.

- [ ] **Step 9: Commit**

```bash
git add src/agent_management/agent_types/base.py \
        src/agent_management/agent_types/sme.py \
        src/agent_management/agent_types/orchestrator.py \
        src/agent_management/agent_manager.py \
        src/main.py \
        tests/test_agent_types.py
git rm src/agent_management/agent_types/resolver.py
git commit -m "feat: remove Resolver agent, update SME to ephemeral V2 lifecycle"
```

---

### Task 8: Integration Test — V2 Boot and Batch Merge

**Files:**
- Replace: `tests/test_integration.py`

- [ ] **Step 1: Write integration test**

Write to `tests/test_integration.py`:

```python
"""Integration test — V2 full lifecycle without real Claude CLI or OpenAI."""

import os
import time
from unittest.mock import patch

import pytest
import yaml

from agent_management import db
from agent_management.main import boot


@pytest.fixture
def tmp_project(tmp_path):
    workspace_root = str(tmp_path / "workspaces")
    os.makedirs(workspace_root, exist_ok=True)
    mcp_config_path = str(tmp_path / "mcp_servers.yaml")
    mcp_config = {"cartograph-db": {"url": "http://localhost:3002"}}
    with open(mcp_config_path, "w") as f:
        yaml.dump(mcp_config, f)
    return {"workspace_root": workspace_root, "mcp_config_path": mcp_config_path}


def test_boot_creates_only_orchestrator(tmp_project):
    """V2 boot creates orchestrator only — no Resolver."""
    db.init_db()
    trigger_mgr = boot(
        workspace_root=tmp_project["workspace_root"],
        mcp_config_path=tmp_project["mcp_config_path"],
        start_trigger_manager=False,
    )
    agent_types = {
        db.get_agent(t["agent_id"])["agent_type"]
        for t in db.get_pending_triggers()
    }
    assert "orchestrator" in agent_types
    assert "resolver" not in agent_types


def test_boot_is_idempotent(tmp_project):
    db.init_db()
    boot(
        workspace_root=tmp_project["workspace_root"],
        mcp_config_path=tmp_project["mcp_config_path"],
        start_trigger_manager=False,
    )
    boot(
        workspace_root=tmp_project["workspace_root"],
        mcp_config_path=tmp_project["mcp_config_path"],
        start_trigger_manager=False,
    )
    triggers = db.get_pending_triggers()
    agent_ids = {t["agent_id"] for t in triggers}
    assert len(agent_ids) == 1  # only orchestrator


def test_batch_merge_auto_executes_exact_hostname_match(tmp_project):
    """
    Two components sharing an exact hostname attribution are auto-merged
    by the batch merger without any LLM involvement.
    """
    db.init_db()

    comp_a = db.upsert_component(
        canonical_name="feeds-aggregator-v2",
        display_name="feeds-aggregator-v2",
        component_type="application",
    )
    comp_b = db.upsert_component(
        canonical_name="fav2-api-prod",
        display_name="fav2-api-prod",
        component_type="application",
    )
    db.upsert_attribution(
        comp_a, "github", "hostname", "feeds-agg.dream11.local",
        discovered_by="sme-test-a",
    )
    db.upsert_attribution(
        comp_b, "cloud", "hostname", "feeds-agg.dream11.local",
        discovered_by="sme-test-b",
    )
    db.upsert_attribution(
        comp_a, "github", "entry_point", "FeedsApplication.java",
        discovered_by="sme-test-a",
    )
    db.upsert_attribution(
        comp_b, "cloud", "entry_point", "FeedsApplication.java",
        discovered_by="sme-test-b",
    )

    from agent_management.batch_merger import run_batch_merge

    summary = run_batch_merge(re_embed_fn=None)

    assert summary["auto_executed"] >= 1
    assert summary["pending_human"] == 0

    # One component should now be decommissioned
    a = db.get_component(comp_a)
    b = db.get_component(comp_b)
    statuses = {a["status"], b["status"]}
    assert "decommissioned" in statuses
    assert "active" in statuses


def test_batch_merge_blocks_on_edge(tmp_project):
    """Components with an edge between them are not merged (caller/callee)."""
    db.init_db()

    comp_a = db.upsert_component(
        canonical_name="service-alpha",
        display_name="service-alpha",
        component_type="application",
    )
    comp_b = db.upsert_component(
        canonical_name="service-beta",
        display_name="service-beta",
        component_type="application",
    )
    db.upsert_attribution(
        comp_a, "github", "hostname", "shared.dream11.local",
        discovered_by="sme-a",
    )
    db.upsert_attribution(
        comp_b, "cloud", "hostname", "shared.dream11.local",
        discovered_by="sme-b",
    )

    # Create an edge: alpha → beta
    conn = db._connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO edges
                   (source_id, target_id, edge_type, identifier, discovered_by)
                   VALUES (%s, %s, 'calls', 'GET /api/data', 'sme-a')""",
                (comp_a, comp_b),
            )
        conn.commit()
    finally:
        conn.close()

    from agent_management.batch_merger import run_batch_merge
    summary = run_batch_merge(re_embed_fn=None)

    assert summary["auto_executed"] == 0

    a = db.get_component(comp_a)
    b = db.get_component(comp_b)
    assert a["status"] == "active"
    assert b["status"] == "active"
```

- [ ] **Step 2: Run integration tests**

```bash
python -m pytest tests/test_integration.py -v
```

Expected: all 4 tests PASS.

- [ ] **Step 3: Run full test suite**

```bash
python -m pytest tests/ -v
```

Expected: all tests PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/test_integration.py
git commit -m "test: add V2 integration tests covering batch merge and boot sequence"
```

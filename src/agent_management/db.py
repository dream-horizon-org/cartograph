"""PostgreSQL data layer for Cartograph V2. All SQL is encapsulated here."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import psycopg2
import psycopg2.extras
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
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id     UUID NOT NULL REFERENCES components(id) ON DELETE CASCADE,
    target_id     UUID NOT NULL REFERENCES components(id) ON DELETE CASCADE,
    edge_type     TEXT NOT NULL CHECK (edge_type IN (
                      'calls', 'reads_from', 'writes_to', 'triggers',
                      'publishes_to', 'consumes_from', 'runs_on')),
    identifier    TEXT NOT NULL,
    evidence      JSONB NOT NULL DEFAULT '[]',
    confidence    FLOAT NOT NULL DEFAULT 1.0,
    metadata      JSONB NOT NULL DEFAULT '{}',
    embedding     vector(1536),
    discovered_by TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
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
            cur.execute(
                "UPDATE attributions SET component_id = %s"
                " WHERE component_id = %s",
                (surviving_id, absorbed_id),
            )
            cur.execute(
                "UPDATE edges SET source_id = %s WHERE source_id = %s",
                (surviving_id, absorbed_id),
            )
            cur.execute(
                "UPDATE edges SET target_id = %s WHERE target_id = %s",
                (surviving_id, absorbed_id),
            )
            cur.execute(
                "UPDATE resource_component_agents SET component_id = %s"
                " WHERE component_id = %s",
                (surviving_id, absorbed_id),
            )
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
    Return all pairs of active components sharing an exact attribution.
    Each row: component_a_id, component_b_id, resource_type, identifier
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
            cur.execute("SELECT * FROM components WHERE status = 'active'")
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

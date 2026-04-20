"""Database migrations — creates all tables from SCHEMA.md."""

from shared.db import get_pool


def run_migrations() -> None:
    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")

            # --- Category 1: Component Graph ---

            cur.execute("""
                CREATE TABLE IF NOT EXISTS components (
                    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    canonical_name  TEXT UNIQUE NOT NULL,
                    display_name    TEXT NOT NULL,
                    component_type  TEXT NOT NULL CHECK (component_type IN (
                                        'application','database','cache','queue',
                                        'lambda','cron','external-service','library',
                                        'infrastructure'
                                    )),
                    status          TEXT NOT NULL DEFAULT 'active' CHECK (status IN (
                                        'active','deprecated','decommissioned'
                                    )),
                    confidence      FLOAT NOT NULL DEFAULT 1.0,
                    metadata        JSONB NOT NULL DEFAULT '{}',
                    embedding       vector(1536),
                    split_from_component_id UUID REFERENCES components(id) ON DELETE SET NULL,
                    split_briefing  TEXT,
                    scanned_at      TIMESTAMPTZ,
                    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS attributions (
                    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    component_id    UUID NOT NULL REFERENCES components(id) ON DELETE CASCADE,
                    plane           TEXT NOT NULL CHECK (plane IN (
                                        'github','deploy','cloud','telemetry','config'
                                    )),
                    resource_type   TEXT NOT NULL,
                    identifier      TEXT NOT NULL,
                    evidence        TEXT,
                    confidence      FLOAT NOT NULL DEFAULT 1.0,
                    metadata        JSONB NOT NULL DEFAULT '{}',
                    embedding       vector(1536),
                    discovered_by   TEXT,
                    discovered_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
                    last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
                    UNIQUE(plane, resource_type, identifier)
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS edges (
                    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    source_id       UUID NOT NULL REFERENCES components(id) ON DELETE CASCADE,
                    target_id       UUID NOT NULL REFERENCES components(id) ON DELETE CASCADE,
                    edge_type       TEXT NOT NULL CHECK (edge_type IN (
                                        'calls','reads_from','writes_to','triggers',
                                        'publishes_to','consumes_from','runs_on'
                                    )),
                    identifier      TEXT NOT NULL,
                    source_attr_id  UUID REFERENCES attributions(id) ON DELETE SET NULL,
                    target_attr_id  UUID REFERENCES attributions(id) ON DELETE SET NULL,
                    evidence        JSONB NOT NULL DEFAULT '[]',
                    confidence      FLOAT NOT NULL DEFAULT 1.0,
                    metadata        JSONB NOT NULL DEFAULT '{}',
                    embedding       vector(1536),
                    discovered_by   TEXT NOT NULL,
                    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
                    last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
                    UNIQUE(source_id, target_id, edge_type, identifier),
                    CHECK (source_id != target_id)
                )
            """)

            cur.execute("""
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
                )
            """)

            # --- Category 2: Agent Infrastructure ---

            cur.execute("""
                CREATE TABLE IF NOT EXISTS agent_runs (
                    agent_id         TEXT PRIMARY KEY,
                    agent_type       TEXT NOT NULL CHECK (agent_type IN (
                                        'orchestrator','iterator','sme','resolver'
                                     )),
                    session_id       TEXT,
                    status           TEXT NOT NULL DEFAULT 'pending' CHECK (status IN (
                                        'pending','running','idle','done','errored','decommissioned'
                                     )),
                    trigger_lock     BOOLEAN NOT NULL DEFAULT FALSE,
                    phase            TEXT,
                    heartbeat        TIMESTAMPTZ,
                    invocation_count INT NOT NULL DEFAULT 0,
                    error_msg        TEXT,
                    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS resources (
                    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    plane           TEXT NOT NULL CHECK (plane IN (
                                        'github','deploy','cloud','telemetry','config'
                                    )),
                    resource_type   TEXT NOT NULL,
                    identifier      TEXT NOT NULL,
                    access_desc     TEXT,
                    metadata        JSONB NOT NULL DEFAULT '{}',
                    status          TEXT NOT NULL DEFAULT 'pending' CHECK (status IN (
                                        'pending','assigned','done'
                                    )),
                    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
                    UNIQUE(plane, resource_type, identifier)
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS resource_component_agents (
                    resource_id     UUID NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
                    component_id    UUID NOT NULL REFERENCES components(id) ON DELETE CASCADE,
                    agent_id        TEXT NOT NULL REFERENCES agent_runs(agent_id) ON DELETE CASCADE,
                    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
                    PRIMARY KEY(resource_id, component_id)
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    owner_agent_id  TEXT NOT NULL,
                    worker_agent_id TEXT NOT NULL REFERENCES agent_runs(agent_id) ON DELETE CASCADE,
                    description     TEXT NOT NULL,
                    status          TEXT NOT NULL DEFAULT 'BW' CHECK (status IN (
                                        'BW','BO','WD','TC'
                                    )),
                    blocker_detail  TEXT,
                    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS secrets (
                    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    plane           TEXT NOT NULL,
                    key             TEXT NOT NULL,
                    value           TEXT NOT NULL,
                    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
                    UNIQUE(plane, key)
                )
            """)

            # --- Category 3: Consolidation ---

            cur.execute("""
                CREATE TABLE IF NOT EXISTS consolidations (
                    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    proposed_by     TEXT NOT NULL,
                    agent_a_id      TEXT NOT NULL,
                    agent_b_id      TEXT,
                    component_a_id  UUID NOT NULL REFERENCES components(id) ON DELETE CASCADE,
                    component_b_id  UUID REFERENCES components(id) ON DELETE SET NULL,
                    nomination_type TEXT NOT NULL CHECK (nomination_type IN ('merge','split')),
                    a_conf_score    FLOAT,
                    b_conf_score    FLOAT,
                    r_conf_score    FLOAT,
                    status          TEXT NOT NULL DEFAULT 'B2' CHECK (status IN (
                                        'B1','B2','R','M','MD','D','F'
                                    )),
                    mutation_assigned_to TEXT,
                    child_agent_id  TEXT,
                    resolved_by     TEXT,
                    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
                    resolved_at     TIMESTAMPTZ
                )
            """)

            # --- Category 4: Communication ---

            cur.execute("""
                CREATE TABLE IF NOT EXISTS communications (
                    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    from_agent      TEXT NOT NULL,
                    to_agent        TEXT NOT NULL,
                    type            TEXT NOT NULL CHECK (type IN (
                                        'consolidation','task','clarification','broadcast','chat'
                                    )),
                    source_id       UUID,
                    text            TEXT NOT NULL,
                    metadata        JSONB NOT NULL DEFAULT '{}',
                    acked_at        TIMESTAMPTZ,
                    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS broadcast_acks (
                    communication_id UUID NOT NULL REFERENCES communications(id) ON DELETE CASCADE,
                    agent_id         TEXT NOT NULL REFERENCES agent_runs(agent_id) ON DELETE CASCADE,
                    acked_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
                    PRIMARY KEY(communication_id, agent_id)
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS clarifications (
                    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    asker_agent_id  TEXT NOT NULL,
                    responder_agent_id TEXT,
                    status          TEXT NOT NULL DEFAULT 'B2' CHECK (status IN (
                                        'B1','B2','QR','QC','CC'
                                    )),
                    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS proxy_items (
                    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    surviving_agent_id  TEXT NOT NULL REFERENCES agent_runs(agent_id),
                    decommissioned_agent_id TEXT NOT NULL,
                    item_type           TEXT NOT NULL CHECK (item_type IN (
                                            'task','consolidation','clarification','chat','broadcast'
                                        )),
                    item_id             UUID NOT NULL,
                    status              TEXT NOT NULL DEFAULT 'pending' CHECK (status IN (
                                            'pending','adopted','closed'
                                        )),
                    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
                    resolved_at         TIMESTAMPTZ
                )
            """)

            # --- Indexes ---
            _create_indexes(cur)

        conn.commit()


def _create_indexes(cur) -> None:
    """Create all indexes idempotently."""
    indexes = [
        # Components
        "CREATE INDEX IF NOT EXISTS idx_comp_embedding ON components USING hnsw (embedding vector_cosine_ops)",
        "CREATE INDEX IF NOT EXISTS idx_comp_type ON components(component_type)",
        "CREATE INDEX IF NOT EXISTS idx_comp_status ON components(status) WHERE status = 'active'",

        # Attributions
        "CREATE INDEX IF NOT EXISTS idx_attr_component ON attributions(component_id)",
        "CREATE INDEX IF NOT EXISTS idx_attr_identifier ON attributions(identifier)",
        "CREATE INDEX IF NOT EXISTS idx_attr_type ON attributions(resource_type)",
        "CREATE INDEX IF NOT EXISTS idx_attr_hostname ON attributions(identifier) WHERE resource_type = 'hostname'",
        "CREATE INDEX IF NOT EXISTS idx_attr_endpoint ON attributions(identifier) WHERE resource_type = 'endpoint'",
        "CREATE INDEX IF NOT EXISTS idx_attr_embedding ON attributions USING hnsw (embedding vector_cosine_ops)",

        # Edges
        "CREATE INDEX IF NOT EXISTS idx_edge_source ON edges(source_id)",
        "CREATE INDEX IF NOT EXISTS idx_edge_target ON edges(target_id)",
        "CREATE INDEX IF NOT EXISTS idx_edge_type ON edges(edge_type)",
        "CREATE INDEX IF NOT EXISTS idx_edge_identifier ON edges(identifier)",
        "CREATE INDEX IF NOT EXISTS idx_edge_source_attr ON edges(source_attr_id)",
        "CREATE INDEX IF NOT EXISTS idx_edge_target_attr ON edges(target_attr_id)",
        "CREATE INDEX IF NOT EXISTS idx_edge_embedding ON edges USING hnsw (embedding vector_cosine_ops)",

        # Unresolved
        "CREATE INDEX IF NOT EXISTS idx_unres_value ON unresolved(reference_value)",
        "CREATE INDEX IF NOT EXISTS idx_unres_open ON unresolved(resolved) WHERE resolved = FALSE",
        "CREATE INDEX IF NOT EXISTS idx_unres_component ON unresolved(found_in_component_id)",
        "CREATE INDEX IF NOT EXISTS idx_unres_embedding ON unresolved USING hnsw (embedding vector_cosine_ops)",

        # Agent runs
        "CREATE INDEX IF NOT EXISTS idx_agent_status ON agent_runs(status)",
        "CREATE INDEX IF NOT EXISTS idx_agent_type ON agent_runs(agent_type)",
        "CREATE INDEX IF NOT EXISTS idx_agent_idle ON agent_runs(agent_type, status) WHERE status = 'idle'",
        "CREATE INDEX IF NOT EXISTS idx_agent_locked ON agent_runs(trigger_lock) WHERE trigger_lock = TRUE",

        # Resources
        "CREATE INDEX IF NOT EXISTS idx_res_status ON resources(status) WHERE status = 'pending'",
        "CREATE INDEX IF NOT EXISTS idx_res_plane ON resources(plane)",

        # Resource-Component-Agents
        "CREATE INDEX IF NOT EXISTS idx_rca_agent ON resource_component_agents(agent_id)",
        "CREATE INDEX IF NOT EXISTS idx_rca_component ON resource_component_agents(component_id)",
        "CREATE INDEX IF NOT EXISTS idx_rca_resource ON resource_component_agents(resource_id)",

        # Tasks
        "CREATE INDEX IF NOT EXISTS idx_task_worker ON tasks(worker_agent_id)",
        "CREATE INDEX IF NOT EXISTS idx_task_owner ON tasks(owner_agent_id)",
        "CREATE INDEX IF NOT EXISTS idx_task_bw ON tasks(worker_agent_id) WHERE status = 'BW'",
        "CREATE INDEX IF NOT EXISTS idx_task_bo ON tasks(owner_agent_id) WHERE status = 'BO'",
        "CREATE INDEX IF NOT EXISTS idx_task_wd ON tasks(owner_agent_id) WHERE status = 'WD'",

        # Consolidations
        "CREATE INDEX IF NOT EXISTS idx_consol_b1 ON consolidations(agent_a_id) WHERE status = 'B1'",
        "CREATE INDEX IF NOT EXISTS idx_consol_b2 ON consolidations(agent_b_id) WHERE status = 'B2'",
        "CREATE INDEX IF NOT EXISTS idx_consol_pending ON consolidations(status) WHERE status IN ('B1','B2')",
        "CREATE INDEX IF NOT EXISTS idx_consol_resolver ON consolidations(status) WHERE status = 'R'",
        "CREATE INDEX IF NOT EXISTS idx_consol_mutation ON consolidations(status) WHERE status IN ('M','MD')",
        "CREATE INDEX IF NOT EXISTS idx_consol_agents ON consolidations(agent_a_id, agent_b_id)",

        # Communications
        "CREATE INDEX IF NOT EXISTS idx_comm_to ON communications(to_agent)",
        "CREATE INDEX IF NOT EXISTS idx_comm_source ON communications(source_id)",
        "CREATE INDEX IF NOT EXISTS idx_comm_type ON communications(type)",
        "CREATE INDEX IF NOT EXISTS idx_comm_created ON communications(created_at)",
        "CREATE INDEX IF NOT EXISTS idx_comm_unacked_chat ON communications(to_agent) WHERE type = 'chat' AND acked_at IS NULL",

        # Broadcast acks
        "CREATE INDEX IF NOT EXISTS idx_bcast_agent ON broadcast_acks(agent_id)",

        # Clarifications
        "CREATE INDEX IF NOT EXISTS idx_clar_asker ON clarifications(asker_agent_id) WHERE status IN ('B1','QR','QC')",
        "CREATE INDEX IF NOT EXISTS idx_clar_responder ON clarifications(responder_agent_id) WHERE status = 'B2'",

        # Proxy items
        "CREATE INDEX IF NOT EXISTS idx_proxy_surviving ON proxy_items(surviving_agent_id) WHERE status = 'pending'",
        "CREATE INDEX IF NOT EXISTS idx_proxy_decom ON proxy_items(decommissioned_agent_id)",
    ]
    for idx in indexes:
        cur.execute(idx)

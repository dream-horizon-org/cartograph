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
                    workspace_path   TEXT,
                    plane            TEXT,                         -- iterators only
                                                                   -- SME→resource assignment lives in RCA
                    status           TEXT NOT NULL DEFAULT 'pending' CHECK (status IN (
                                        'pending','running','idle','done','errored','decommissioned'
                                     )),
                    trigger_lock     BOOLEAN NOT NULL DEFAULT FALSE,
                    phase            TEXT,
                    heartbeat        TIMESTAMPTZ,
                    invocation_count INT NOT NULL DEFAULT 0,
                    error_msg        TEXT,
                    -- errored_at, recovery_attempts, sleep_until added via
                    -- post-creation ALTERs below so the single CREATE-then-
                    -- ALTER path handles both fresh + existing databases.
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
                                        'pending','assigned','done','rejected'
                                    )),
                    rejected_at     TIMESTAMPTZ,
                    rejected_by     TEXT,
                    rejected_reason TEXT,
                    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
                    UNIQUE(plane, resource_type, identifier)
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS resource_component_agents (
                    resource_id     UUID NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
                    component_id    UUID REFERENCES components(id) ON DELETE CASCADE,
                                    -- nullable: at spawn time the SME has an RCA row
                                    -- with component_id=NULL (reserved assignment slot);
                                    -- filled in when the SME calls upsert_component.
                    agent_id        TEXT NOT NULL REFERENCES agent_runs(agent_id) ON DELETE CASCADE,
                    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
                    PRIMARY KEY(resource_id, agent_id)
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

            # Note: communications.to_agent is agent_id; to_agent_type (added
            # via ALTER below) is the agent_type of the broadcast target.
            # Exactly one must be non-null — CHECK enforced via additive migration.
            cur.execute("""
                CREATE TABLE IF NOT EXISTS communications (
                    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    from_agent      TEXT NOT NULL,                 -- agent_id or 'admin'
                    to_agent        TEXT,                          -- agent_id ('admin' or specific); NULL for broadcasts
                    to_agent_type   TEXT,                          -- agent_type for broadcasts; NULL otherwise
                    type            TEXT NOT NULL CHECK (type IN (
                                        'consolidation','task','clarification','broadcast','chat'
                                    )),
                    source_id       UUID,
                    text            TEXT NOT NULL,
                    metadata        JSONB NOT NULL DEFAULT '{}',
                    acked_at        TIMESTAMPTZ,
                    is_persistent   BOOLEAN NOT NULL DEFAULT FALSE, -- broadcasts: apply to future agents too
                    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
                    CONSTRAINT comm_target_exactly_one
                      CHECK ((to_agent IS NOT NULL) <> (to_agent_type IS NOT NULL))
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

            # --- Post-creation ALTERs (additive schema evolution) ---
            # Recovery bookkeeping for errored agents. Idempotent — safe to
            # run against existing databases; no data rewritten.
            cur.execute(
                "ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS errored_at TIMESTAMPTZ"
            )
            cur.execute(
                "ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS recovery_attempts INT NOT NULL DEFAULT 0"
            )

            # Resource soft-delete: 'rejected' status + audit columns. Existing
            # databases created before this migration have a narrower CHECK
            # constraint; swap it out to include 'rejected'. All existing rows
            # already match the new set so this is non-destructive.
            cur.execute(
                "ALTER TABLE resources ADD COLUMN IF NOT EXISTS rejected_at TIMESTAMPTZ"
            )
            cur.execute(
                "ALTER TABLE resources ADD COLUMN IF NOT EXISTS rejected_by TEXT"
            )
            cur.execute(
                "ALTER TABLE resources ADD COLUMN IF NOT EXISTS rejected_reason TEXT"
            )
            cur.execute(
                "ALTER TABLE resources DROP CONSTRAINT IF EXISTS resources_status_check"
            )
            cur.execute(
                """ALTER TABLE resources ADD CONSTRAINT resources_status_check
                   CHECK (status IN ('pending','assigned','done','rejected'))"""
            )

            # Phase-2 SME spawn: RCA becomes single source of truth for
            # assignment+ownership. component_id becomes nullable (reserved
            # at spawn, filled at materialisation). PK moves from
            # (resource_id, component_id) to (resource_id, agent_id) so a
            # row can exist BEFORE the component does. Order matters: drop
            # the old PK first (can't relax NOT NULL on a PK column), then
            # drop NOT NULL, then add the new PK.
            cur.execute(
                "ALTER TABLE resource_component_agents DROP CONSTRAINT IF EXISTS resource_component_agents_pkey"
            )
            cur.execute(
                "ALTER TABLE resource_component_agents ALTER COLUMN component_id DROP NOT NULL"
            )
            cur.execute(
                """DO $$
                   BEGIN
                     IF NOT EXISTS (
                       SELECT 1 FROM pg_constraint
                       WHERE conname = 'resource_component_agents_pkey'
                     ) THEN
                       ALTER TABLE resource_component_agents
                         ADD PRIMARY KEY (resource_id, agent_id);
                     END IF;
                   END$$"""
            )

            # Drop agent_runs.resource_id — superseded by RCA. No SMEs have
            # been spawned yet in any environment (Phase 1 never reached SME
            # spawn) so there is no data to migrate.
            cur.execute(
                "ALTER TABLE agent_runs DROP COLUMN IF EXISTS resource_id"
            )

            # Sleep support (Phase 2.5). Agents with sleep_until in the future
            # are skipped by the trigger scanner's idle-lock filter.
            cur.execute(
                "ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS sleep_until TIMESTAMPTZ"
            )

            # Phase 3: SME-authored human-readable component doc rendered
            # in the graph-viz hover popup. Markdown; nullable.
            cur.execute(
                "ALTER TABLE components ADD COLUMN IF NOT EXISTS component_doc_md TEXT"
            )

            # Persistent broadcasts (Phase 2.5). Normal broadcasts are
            # forward-only — they apply to agents that exist at broadcast
            # time. is_persistent=TRUE means "this policy applies to future
            # agents too" (e.g. standing instructions).
            cur.execute(
                "ALTER TABLE communications ADD COLUMN IF NOT EXISTS is_persistent BOOLEAN NOT NULL DEFAULT FALSE"
            )

            # Clean separation of broadcast targets from point-to-point
            # targets on communications. Previously `to_agent` held either
            # an agent_id OR an agent_type (for broadcasts) — overloaded.
            # Split into `to_agent` (agent_id, nullable) + `to_agent_type`
            # (agent_type for broadcasts, nullable) with a CHECK that
            # exactly one is set. Order matters: DROP NOT NULL first so the
            # backfill UPDATE can set to_agent = NULL.
            cur.execute(
                "ALTER TABLE communications ADD COLUMN IF NOT EXISTS to_agent_type TEXT"
            )
            cur.execute(
                "ALTER TABLE communications ALTER COLUMN to_agent DROP NOT NULL"
            )
            # Backfill: broadcast rows had their agent_type in to_agent.
            cur.execute(
                """UPDATE communications
                   SET to_agent_type = to_agent, to_agent = NULL
                   WHERE type = 'broadcast' AND to_agent_type IS NULL AND to_agent IS NOT NULL"""
            )
            # Add the exactly-one-of constraint. Idempotent via DO block.
            cur.execute(
                """DO $$
                   BEGIN
                     IF NOT EXISTS (
                       SELECT 1 FROM pg_constraint
                       WHERE conname = 'comm_target_exactly_one'
                     ) THEN
                       ALTER TABLE communications ADD CONSTRAINT comm_target_exactly_one
                         CHECK ((to_agent IS NOT NULL) <> (to_agent_type IS NOT NULL));
                     END IF;
                   END$$"""
            )

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
        # Recovery scanner: scoped to errored rows so scan is cheap.
        "CREATE INDEX IF NOT EXISTS idx_agent_errored ON agent_runs(errored_at) WHERE status = 'errored'",
        # Sleep filter: partial index on sleeping agents (small set typically).
        "CREATE INDEX IF NOT EXISTS idx_agent_sleep ON agent_runs(sleep_until) WHERE sleep_until IS NOT NULL",

        # Resources
        "CREATE INDEX IF NOT EXISTS idx_res_status ON resources(status) WHERE status = 'pending'",
        "CREATE INDEX IF NOT EXISTS idx_res_plane ON resources(plane)",
        # Cascade safety check: fast existence lookup for SME assignments
        "CREATE INDEX IF NOT EXISTS idx_rca_resource_id ON resource_component_agents(resource_id)",

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
        "CREATE INDEX IF NOT EXISTS idx_comm_to ON communications(to_agent) WHERE to_agent IS NOT NULL",
        "CREATE INDEX IF NOT EXISTS idx_comm_to_type ON communications(to_agent_type) WHERE to_agent_type IS NOT NULL",
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

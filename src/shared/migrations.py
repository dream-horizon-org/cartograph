"""Database migrations — creates all tables from SCHEMA.md."""

from shared import config
from shared.db import get_pool


_EMBEDDING_TABLES = [
    ("components",   "idx_comp_embedding"),
    ("attributions", "idx_attr_embedding"),
    ("edges",        "idx_edge_embedding"),
    ("unresolved",   "idx_unres_embedding"),
]


def _migrate_edges_asymmetric(cur) -> None:
    """Phase 3.9: edges schema redesign.

    Idempotent. On a fresh DB the columns already exist as source_id /
    target_id from the CREATE TABLE; on a Phase-3.9-already-migrated
    DB the renamed columns + partial indexes are already there. This
    helper detects which state we're in and does only what's needed.
    """
    # 1. Rename source_id → from_component_id if old name still around.
    cur.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name='edges'
          AND column_name IN ('source_id','target_id',
                              'from_component_id','to_component_id')
    """)
    cols = {r["column_name"] for r in cur.fetchall()}
    if "source_id" in cols and "from_component_id" not in cols:
        cur.execute("ALTER TABLE edges RENAME COLUMN source_id TO from_component_id")
    if "target_id" in cols and "to_component_id" not in cols:
        cur.execute("ALTER TABLE edges RENAME COLUMN target_id TO to_component_id")

    # 2. Drop NOT NULL on both. Idempotent.
    cur.execute("ALTER TABLE edges ALTER COLUMN from_component_id DROP NOT NULL")
    cur.execute("ALTER TABLE edges ALTER COLUMN to_component_id DROP NOT NULL")

    # 3. Drop the old global UNIQUE constraint if it survived the rename
    # (Postgres carries auto-named constraints across renames). Both old
    # and new constraint-name forms are tried because the constraint name
    # depends on the column names at constraint-creation time.
    for old_name in (
        "edges_source_id_target_id_edge_type_identifier_key",
        "edges_from_component_id_to_component_id_edge_type_identifier_key",
    ):
        cur.execute(f"ALTER TABLE edges DROP CONSTRAINT IF EXISTS {old_name}")

    # 4. Drop the old self-loop CHECK if present (it referenced source_id).
    # The constraint name is auto-generated; find + drop by definition.
    # `%` doubled because psycopg treats single `%` as a placeholder marker
    # even in unparameterised queries.
    cur.execute("""
        SELECT conname FROM pg_constraint
        WHERE conrelid = 'edges'::regclass
          AND contype = 'c'
          AND pg_get_constraintdef(oid) ILIKE '%%source_id%%'
    """)
    for row in cur.fetchall():
        cur.execute(f"ALTER TABLE edges DROP CONSTRAINT IF EXISTS {row['conname']}")

    # 5. Add the three partial unique indexes (idempotent via IF NOT EXISTS).
    cur.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS edges_bound_unique
          ON edges (from_component_id, to_component_id, edge_type, identifier)
          WHERE from_component_id IS NOT NULL AND to_component_id IS NOT NULL
    """)
    cur.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS edges_catalog_unique
          ON edges (to_component_id, edge_type, identifier)
          WHERE from_component_id IS NULL
    """)
    cur.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS edges_dangling_unique
          ON edges (from_component_id, edge_type, identifier)
          WHERE to_component_id IS NULL
    """)

    # 6. Add the new CHECK constraints if they're missing.
    cur.execute("""
        DO $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conname = 'edges_at_least_one_endpoint'
          ) THEN
            ALTER TABLE edges ADD CONSTRAINT edges_at_least_one_endpoint
              CHECK (from_component_id IS NOT NULL OR to_component_id IS NOT NULL);
          END IF;
        END$$
    """)
    cur.execute("""
        DO $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conname = 'edges_no_self_loop_v2'
          ) THEN
            ALTER TABLE edges ADD CONSTRAINT edges_no_self_loop_v2
              CHECK (from_component_id IS NULL
                     OR to_component_id IS NULL
                     OR from_component_id <> to_component_id);
          END IF;
        END$$
    """)


def _create_flows_table(cur) -> None:
    """Phase 3.9: flows table — set-based link between incoming and
    outgoing edges of a component, owned by the component's SME."""
    cur.execute("""
        CREATE TABLE IF NOT EXISTS flows (
          id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          component_id      UUID NOT NULL REFERENCES components(id) ON DELETE CASCADE,
          incoming_edge_id  UUID NOT NULL REFERENCES edges(id) ON DELETE CASCADE,
          outgoing_edge_id  UUID NOT NULL REFERENCES edges(id) ON DELETE CASCADE,
          confidence        FLOAT NOT NULL DEFAULT 1.0,
          metadata          JSONB NOT NULL DEFAULT '{}',
          discovered_by     TEXT NOT NULL,
          created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE (component_id, incoming_edge_id, outgoing_edge_id)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_flows_component ON flows(component_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_flows_incoming ON flows(incoming_edge_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_flows_outgoing ON flows(outgoing_edge_id)")


def _create_proxy_audit_table(cur) -> None:
    """Phase 4: append-only log of 'who acted via proxy on what.'

    Every call to act_on_proxy_item writes one row. Admin UI joins this
    against item rows authored by decommissioned agents to render a
    via-<survivor> badge. Never updated — audit trail is immutable."""
    # survivor_id / proxy_agent_id are TEXT to match agent_runs.agent_id.
    # item_id is TEXT too — different item tables use TEXT IDs (tasks,
    # communications) vs UUID (consolidations, clarifications); we store
    # the stringified form so one table covers all.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS proxy_audit (
          id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          survivor_id      TEXT NOT NULL REFERENCES agent_runs(agent_id),
          proxy_agent_id   TEXT NOT NULL REFERENCES agent_runs(agent_id),
          item_type        TEXT NOT NULL,
          item_id          TEXT NOT NULL,
          action           TEXT NOT NULL,
          payload_summary  JSONB NOT NULL DEFAULT '{}',
          created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_proxy_audit_survivor "
        "ON proxy_audit(survivor_id, created_at DESC)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_proxy_audit_item "
        "ON proxy_audit(item_type, item_id)"
    )


def _migrate_embedding_dims(cur, target_dim: int) -> None:
    """Flip embedding columns to target_dim if they're on a different dim.

    pgvector stores dim in `pg_attribute.atttypmod`. Read it to decide
    whether a migration is needed. Drops HNSW indexes first (operator
    class is dim-specific), nulls existing embeddings, alters the column,
    then rebuilds HNSW.
    """
    for table, idx_name in _EMBEDDING_TABLES:
        cur.execute(
            """SELECT a.atttypmod
               FROM pg_attribute a
               JOIN pg_class c ON c.oid = a.attrelid
               WHERE c.relname = %s AND a.attname = 'embedding'""",
            (table,),
        )
        row = cur.fetchone()
        if row is None:
            # table or column doesn't exist; nothing to migrate
            continue
        current_dim = row["atttypmod"]
        # pgvector's typmod IS the dim for a pure vector(N) column
        if current_dim == target_dim:
            continue
        # Rare path: embedded data would be lost. Document + proceed.
        cur.execute(f"DROP INDEX IF EXISTS {idx_name}")
        cur.execute(f"UPDATE {table} SET embedding = NULL")
        cur.execute(
            f"ALTER TABLE {table} ALTER COLUMN embedding TYPE vector({target_dim})"
        )
        cur.execute(
            f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table} "
            f"USING hnsw (embedding vector_cosine_ops)"
        )


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

            # Phase 3.9 baseline shape: from_component_id / to_component_id
            # both nullable. The CREATE TABLE here matches the post-3.9
            # state directly so fresh installs don't need to dance through
            # the rename. _migrate_edges_asymmetric still handles existing
            # databases that started before 3.9.
            cur.execute("""
                CREATE TABLE IF NOT EXISTS edges (
                    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    from_component_id  UUID REFERENCES components(id) ON DELETE CASCADE,
                    to_component_id    UUID REFERENCES components(id) ON DELETE CASCADE,
                    edge_type          TEXT NOT NULL CHECK (edge_type IN (
                                           'calls','reads_from','writes_to','triggers',
                                           'publishes_to','consumes_from','runs_on'
                                       )),
                    identifier         TEXT NOT NULL,
                    source_attr_id     UUID REFERENCES attributions(id) ON DELETE SET NULL,
                    target_attr_id     UUID REFERENCES attributions(id) ON DELETE SET NULL,
                    evidence           JSONB NOT NULL DEFAULT '[]',
                    confidence         FLOAT NOT NULL DEFAULT 1.0,
                    metadata           JSONB NOT NULL DEFAULT '{}',
                    embedding          vector(1536),
                    discovered_by      TEXT NOT NULL,
                    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
                    last_seen_at       TIMESTAMPTZ NOT NULL DEFAULT now()
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

            # Phase 3.8: structural slice description — which parts of
            # which source resource(s) this component covers. Nullable
            # (single-resource full-component components have no slice).
            # Shape documented in SCHEMA.md §components; keyed by
            # resource_id so merge mutations (multi-resource components)
            # are naturally representable.
            cur.execute(
                "ALTER TABLE components ADD COLUMN IF NOT EXISTS source_slice JSONB"
            )

            # Phase 3.9: asymmetric edge protocol.
            # Rename source_id → from_component_id, target_id → to_component_id,
            # both nullable. Catalog row: from IS NULL (callee owns).
            # Bound row: both non-null (caller owns). Dangling outgoing:
            # to IS NULL (caller owns). Replace global UNIQUE with three
            # partial uniques. See docs/SCHEMA.md §edges and
            # IMPLEMENTATION-PHASES.md Phase 3.9 for the full protocol.
            _migrate_edges_asymmetric(cur)
            _create_flows_table(cur)

            # Phase 3.7: switch embeddings from OpenAI text-embedding-3-small
            # (1536d) to local Ollama mxbai-embed-large (1024d). Runs on the
            # Apple Silicon GPU via Metal — no API key, no egress, faster.
            # Idempotent: only runs the flip when columns are still 1536d.
            # Safe to drop existing embeddings because none of them had
            # content — we never had an OPENAI_API_KEY set in production,
            # so every embedding column is NULL. If that changes in the
            # future, this block must be replaced with a re-embed pipeline
            # rather than a destructive alter.
            _migrate_embedding_dims(cur, target_dim=config.EMBEDDING_DIMS)

            # Rejected resources are tombstones — they must not block
            # re-upsert of a row with the same (plane, resource_type,
            # identifier). Replace the table-wide UNIQUE with a partial
            # unique index scoped to live rows (status != 'rejected').
            # Idempotent: if the old constraint or an earlier version of
            # the partial index already exists, both paths are safe.
            cur.execute(
                "ALTER TABLE resources DROP CONSTRAINT IF EXISTS "
                "resources_plane_resource_type_identifier_key"
            )
            cur.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS resources_live_unique "
                "ON resources (plane, resource_type, identifier) "
                "WHERE status != 'rejected'"
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

            # Phase 4: proxy inheritance context on agent deactivation.
            # When an agent is decommissioned via absorb_agent, we record
            # who absorbed them (single-hop pointer — chains walked not
            # flattened), the reason, and a brief. get_my_proxy_items
            # walks these pointers transitively; proxy_audit logs who
            # ACTUALLY acted via proxy.
            cur.execute(
                "ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS deactivation_reason TEXT"
            )
            cur.execute(
                "ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS deactivation_notes TEXT"
            )
            # agent_runs.agent_id is TEXT (session-scoped string), not UUID,
            # so the self-referential pointer stays TEXT.
            cur.execute(
                "ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS merged_into_agent_id TEXT"
            )
            # Self-referential FK — needs a named constraint so the IF NOT
            # EXISTS dance via pg_constraint works idempotently.
            cur.execute(
                """DO $$
                   BEGIN
                     IF NOT EXISTS (
                       SELECT 1 FROM pg_constraint
                       WHERE conname = 'agent_runs_merged_into_fk'
                     ) THEN
                       ALTER TABLE agent_runs ADD CONSTRAINT agent_runs_merged_into_fk
                         FOREIGN KEY (merged_into_agent_id)
                         REFERENCES agent_runs(agent_id) ON DELETE SET NULL;
                     END IF;
                   END$$"""
            )
            # Walk this pointer bottom-up in get_my_proxy_items.
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_agent_runs_merged_into "
                "ON agent_runs(merged_into_agent_id) WHERE merged_into_agent_id IS NOT NULL"
            )

            _create_proxy_audit_table(cur)

            # Self-heal: any split consolidation stuck at B2 with no
            # counter-party bumps up to 'R' so the resolver can pick it
            # up. Pre-Phase-4 code inserted ALL nominations at B2, which
            # created zombie splits (no agent_b → never auto-escalates
            # via the both-scores-above-threshold rule). Safe to re-run:
            # only rows matching this exact state are touched.
            cur.execute(
                """UPDATE consolidations
                   SET status = 'R', updated_at = now()
                   WHERE status = 'B2'
                     AND nomination_type = 'split'
                     AND agent_b_id IS NULL"""
            )

            # Belt-and-suspenders for the per-type state machines. Split
            # consolidations must never sit at B2 — structurally, B2 is a
            # "nominated's turn" state that only exists for merge. This
            # constraint codifies the invariant so any bug that tried to
            # park a split at B2 fails hard at the DB layer. Idempotent
            # via pg_constraint existence check.
            cur.execute(
                """DO $$
                   BEGIN
                     IF NOT EXISTS (
                       SELECT 1 FROM pg_constraint
                       WHERE conname = 'consolidation_split_no_b2'
                     ) THEN
                       ALTER TABLE consolidations
                         ADD CONSTRAINT consolidation_split_no_b2
                         CHECK (NOT (nomination_type = 'split' AND status = 'B2'));
                     END IF;
                   END$$"""
            )

            # Phase 4.1: consolidations.metadata JSONB for structured
            # demo-tagging, evidence blobs, cosine scores cited, etc.
            # Defaults to empty dict; caller populates via
            # nominate_consolidation(metadata={...}).
            cur.execute(
                "ALTER TABLE consolidations ADD COLUMN IF NOT EXISTS "
                "metadata JSONB NOT NULL DEFAULT '{}'"
            )

            # Phase 5.9: agent_insights — self-improvement loop.
            # Agents call record_insight() to flag prompt gaps, tactic
            # wins, tool gaps, doc confusion, workflow friction. Admin
            # triages from the UI; "promoted" entries inform prompt /
            # doc updates. Append-only from the agent side; status flips
            # via admin triage.
            cur.execute(
                """CREATE TABLE IF NOT EXISTS agent_insights (
                    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    agent_id    TEXT NOT NULL REFERENCES agent_runs(agent_id) ON DELETE CASCADE,
                    kind        TEXT NOT NULL CHECK (kind IN (
                                  'prompt_gap',
                                  'tactic_win',
                                  'tool_gap',
                                  'doc_confusing',
                                  'workflow_friction'
                                )),
                    target      TEXT NOT NULL,
                    body        TEXT NOT NULL,
                    evidence    JSONB,
                    status      TEXT NOT NULL DEFAULT 'open' CHECK (status IN (
                                  'open', 'investigating', 'promoted', 'wontfix'
                                )),
                    triaged_by  TEXT,
                    triaged_at  TIMESTAMPTZ,
                    triage_note TEXT,
                    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
                )"""
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
        # Phase 3.9: source_id/target_id renamed to from_component_id/to_component_id.
        "CREATE INDEX IF NOT EXISTS idx_edge_source ON edges(from_component_id)",
        "CREATE INDEX IF NOT EXISTS idx_edge_target ON edges(to_component_id)",
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

        # Phase 5.9: Agent insights
        "CREATE INDEX IF NOT EXISTS idx_insights_agent ON agent_insights(agent_id)",
        "CREATE INDEX IF NOT EXISTS idx_insights_status ON agent_insights(status)",
        "CREATE INDEX IF NOT EXISTS idx_insights_target ON agent_insights(target)",
    ]
    for idx in indexes:
        cur.execute(idx)

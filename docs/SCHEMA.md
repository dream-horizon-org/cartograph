# Cartograph — Schema Document

```sql
CREATE EXTENSION IF NOT EXISTS vector;  -- pgvector for embedding search
-- gen_random_uuid() is built into Postgres 13+, no extension needed
```

---

## Category 1: Component Graph

The product output. What we're building.

### `components`

The nodes. Each independently runnable thing discovered across planes.

```sql
CREATE TABLE components (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    canonical_name  TEXT UNIQUE NOT NULL,        -- stable identifier, survives renames
    display_name    TEXT NOT NULL,               -- human-friendly name
    component_type  TEXT NOT NULL CHECK (component_type IN (
                        'application',           -- API, web service, backend
                        'database',              -- RDS, Aurora, DynamoDB
                        'cache',                 -- ElastiCache, Redis
                        'queue',                 -- SQS, SNS, Kafka
                        'lambda',                -- serverless function
                        'cron',                  -- scheduled job
                        'external-service',      -- third-party (SI, Kadamba, Slack)
                        'library',               -- shared lib, not independently deployable
                        'infrastructure'         -- shared platform (EKS cluster, shared VPC)
                    )),
    status          TEXT NOT NULL DEFAULT 'active' CHECK (status IN (
                        'active',                -- currently running/deployed
                        'deprecated',            -- still running but scheduled for removal
                        'decommissioned'         -- merged/deleted/gone
                    )),
    confidence      FLOAT NOT NULL DEFAULT 1.0,  -- 0.0 to 1.0, how sure we are this exists
    metadata        JSONB NOT NULL DEFAULT '{}', -- runtime, framework, region, repo URL, etc.
    embedding       vector(1536),                -- for fuzzy matching during consolidation
    split_from_component_id UUID REFERENCES components(id) ON DELETE SET NULL,
                                                 -- if this component was born from a split, points to parent
    split_briefing  TEXT,                        -- briefing doc from parent explaining what this component is
    component_doc_md TEXT,                       -- SME-authored markdown component doc (Phase 3).
                                                 -- Rendered in graph-viz hover popup (Phase 3.5).
    scanned_at      TIMESTAMPTZ,                -- last time an SME analysed this
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_comp_embedding ON components USING hnsw (embedding vector_cosine_ops);
CREATE INDEX idx_comp_type ON components(component_type);
CREATE INDEX idx_comp_status ON components(status) WHERE status = 'active';
-- owned_by_agent removed — derivable via resource_component_agents table
```

### `attributions`

Everything we know about a component. Key-value pairs — `resource_type` is the key, `identifier` is the value. Repeatable (many endpoints per component). Tagged with the plane that discovered it.

```sql
CREATE TABLE attributions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    component_id    UUID NOT NULL REFERENCES components(id) ON DELETE CASCADE,
    plane           TEXT NOT NULL CHECK (plane IN (
                        'github',                -- code repos
                        'deploy',                -- deploy-script repos
                        'cloud',                 -- AWS/GCP/Azure + K8s
                        'telemetry',             -- Datadog, traces, logs
                        'config'                 -- Consul, Vault, Param Store (supporter)
                    )),
    resource_type   TEXT NOT NULL,               -- open taxonomy: endpoint, asg, r53, repo, etc.
    identifier      TEXT NOT NULL,               -- the value: "GET /cricket/scorecard", "sg-3e743b49"
    evidence        TEXT,                        -- free text: why we think this belongs here
    confidence      FLOAT NOT NULL DEFAULT 1.0,
    metadata        JSONB NOT NULL DEFAULT '{}', -- per-resource details (port, image tag, handler)
    embedding       vector(1536),                -- for fuzzy resource lookup across planes
    discovered_by   TEXT,                        -- agent_id that wrote this
    discovered_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT now(), -- updated on re-scan, used for drift detection

    UNIQUE(plane, resource_type, identifier)     -- same resource can't be attributed twice from same plane
);

CREATE INDEX idx_attr_component ON attributions(component_id);
CREATE INDEX idx_attr_identifier ON attributions(identifier);
CREATE INDEX idx_attr_type ON attributions(resource_type);
CREATE INDEX idx_attr_hostname ON attributions(identifier) WHERE resource_type = 'hostname';
CREATE INDEX idx_attr_endpoint ON attributions(identifier) WHERE resource_type = 'endpoint';
CREATE INDEX idx_attr_embedding ON attributions USING hnsw (embedding vector_cosine_ops);
```

### `edges`

Dependencies between components.

```sql
CREATE TABLE edges (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id       UUID NOT NULL REFERENCES components(id) ON DELETE CASCADE,
    target_id       UUID NOT NULL REFERENCES components(id) ON DELETE CASCADE,
    edge_type       TEXT NOT NULL CHECK (edge_type IN (
                        'calls',                 -- HTTP/gRPC call
                        'reads_from',            -- reads data from (DB, cache, queue)
                        'writes_to',             -- writes data to
                        'triggers',              -- event-based trigger (CloudWatch → Lambda)
                        'publishes_to',          -- publishes messages to (SNS, Kafka, Slack)
                        'consumes_from',         -- consumes messages from (SQS, Kafka)
                        'runs_on'                -- runs on shared infrastructure (pod → EKS)
                    )),
    identifier      TEXT NOT NULL,               -- the specific call: "GET /scorecard", "SELECT * FROM matches"
    source_attr_id  UUID REFERENCES attributions(id) ON DELETE SET NULL,  -- which attribution in source makes this call
    target_attr_id  UUID REFERENCES attributions(id) ON DELETE SET NULL,  -- which attribution in target receives it
    evidence        JSONB NOT NULL DEFAULT '[]', -- array of {plane, detail, config_key} objects
    confidence      FLOAT NOT NULL DEFAULT 1.0,
    metadata        JSONB NOT NULL DEFAULT '{}', -- throughput, latency, error rate from telemetry
    embedding       vector(1536),                -- for fuzzy matching calls across components
    discovered_by   TEXT NOT NULL,               -- agent_id
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT now(), -- drift detection

    UNIQUE(source_id, target_id, edge_type, identifier), -- one edge per specific call
    CHECK (source_id != target_id)               -- no self-loops
);

CREATE INDEX idx_edge_source ON edges(source_id);
CREATE INDEX idx_edge_target ON edges(target_id);
CREATE INDEX idx_edge_type ON edges(edge_type);
CREATE INDEX idx_edge_identifier ON edges(identifier);
CREATE INDEX idx_edge_source_attr ON edges(source_attr_id);
CREATE INDEX idx_edge_target_attr ON edges(target_attr_id);
CREATE INDEX idx_edge_embedding ON edges USING hnsw (embedding vector_cosine_ops);
```

### `unresolved`

References agents found but couldn't resolve. Parked for later planes/phases.

```sql
CREATE TABLE unresolved (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    found_in_component_id    UUID REFERENCES components(id) ON DELETE SET NULL,
    reference_type           TEXT NOT NULL,       -- what kind of reference: hostname, config_key, url
    reference_value          TEXT NOT NULL,       -- the actual value: "CONFIG_COMMON_FEEDPROVIDERURLS_SI"
    context                  JSONB,               -- where it was found: {file, line, code_snippet}
    embedding                vector(1536),        -- for fuzzy resolution against components/attributions
    resolved                 BOOLEAN NOT NULL DEFAULT FALSE,
    resolved_to_component_id UUID REFERENCES components(id) ON DELETE SET NULL,
    found_by_agent           TEXT,                -- agent_id that discovered this
    attempts                 INT NOT NULL DEFAULT 0, -- how many resolution attempts, prevents infinite retry
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_unres_value ON unresolved(reference_value);
CREATE INDEX idx_unres_open ON unresolved(resolved) WHERE resolved = FALSE;
CREATE INDEX idx_unres_component ON unresolved(found_in_component_id);
CREATE INDEX idx_unres_embedding ON unresolved USING hnsw (embedding vector_cosine_ops);
```

---

## Category 2: Agent Infrastructure

What runs the system.

### `agent_runs`

Agent registry. Every agent in the system has a row here. Invoke engine's state store.

```sql
CREATE TABLE agent_runs (
    agent_id         TEXT PRIMARY KEY,            -- unique agent identity (e.g., "sme-fav2-api")
    agent_type       TEXT NOT NULL CHECK (agent_type IN (
                        'orchestrator',
                        'iterator',
                        'sme',
                        'resolver'
                     )),
    session_id       TEXT,                        -- Claude session ID for resume
    workspace_path   TEXT,                        -- per-agent cwd (contains .mcp.json)
    plane            TEXT,                        -- iterators only (their assigned plane)
    -- SME→resource assignment lives in resource_component_agents (single source of truth)
    -- Previous fields removed as superseded by RCA: resource_ids[], planes[], resource_id
    status           TEXT NOT NULL DEFAULT 'pending' CHECK (status IN (
                        'pending',                -- created, waiting for first invoke
                        'running',                -- actively executing
                        'idle',                   -- done with current work, waiting for next trigger
                        'done',                   -- finished all work across all phases
                        'errored',                -- failed, needs recovery
                        'decommissioned'          -- merged away or no longer needed
                     )),
    trigger_lock     BOOLEAN NOT NULL DEFAULT FALSE,
                                                 -- TRUE = trigger manager wants this agent woken
                                                 -- agent manager picks up, sets running, clears lock
                                                 -- only set when status = 'idle'
    phase            TEXT,                        -- current phase the agent is in
    heartbeat        TIMESTAMPTZ,                -- last sign of life
    invocation_count INT NOT NULL DEFAULT 0,      -- for trigger priority (lower = earlier pickup)
    error_msg        TEXT,                        -- last error if status = errored (stderr tail / exception repr)
    errored_at       TIMESTAMPTZ,                 -- stamped when transitioning to errored (recovery backoff anchor)
    recovery_attempts INT NOT NULL DEFAULT 0,    -- bounded by MAX_RECOVERY_ATTEMPTS=3; reset to 0 on successful idle
    sleep_until      TIMESTAMPTZ,                 -- if set AND in future, trigger scanner skips this agent.
                                                 -- Admin chat auto-wakes (clears it); bulk_wake_agents also clears.
                                                 -- Expired sleep_until is effectively awake (scanner filter treats it as NULL).
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_agent_status ON agent_runs(status);
CREATE INDEX idx_agent_type ON agent_runs(agent_type);
CREATE INDEX idx_agent_idle ON agent_runs(agent_type, status) WHERE status = 'idle';
CREATE INDEX idx_agent_locked ON agent_runs(trigger_lock) WHERE trigger_lock = TRUE;
CREATE INDEX idx_agent_errored ON agent_runs(errored_at) WHERE status = 'errored';
CREATE INDEX idx_agent_sleep ON agent_runs(sleep_until) WHERE sleep_until IS NOT NULL;
```

### `resources`

Iterator output queue. Each row is a **heuristic component candidate** — the iterator's best guess at one deployable unit. An SME later validates the guess (build / merge / split / reject). Sub-artifacts (branches, workflows, individual ALB/TG/listener records) belong in the parent row's `metadata` JSONB — NOT as separate rows. See iterator granularity rules in `AGENT-PROMPTS.md §0`.

```sql
CREATE TABLE resources (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    plane           TEXT NOT NULL CHECK (plane IN (
                        'github', 'deploy', 'cloud', 'telemetry', 'config'
                    )),
    resource_type   TEXT NOT NULL,               -- repo, r53_chain, k8s_workload, rds, service, etc.
    identifier      TEXT NOT NULL,               -- "dream11/feeds-aggregator-v2", "feeds-agg-v2.dream11.local"
    access_desc     TEXT,                        -- how to access: "clone via SSH", "describe-asg", "kubectl get"
    metadata        JSONB NOT NULL DEFAULT '{}', -- pre-resolved chain data, sub-artifacts, cluster info, etc.
    -- assigned_to removed — derivable via resource_component_agents table
    status          TEXT NOT NULL DEFAULT 'pending' CHECK (status IN (
                        'pending',                -- discovered by iterator, not yet assigned
                        'assigned',               -- SME created and assigned
                        'done',                   -- SME finished analysing
                        'rejected'                -- soft-deleted (iterator self-cleanup or orchestrator override)
                    )),
    rejected_at     TIMESTAMPTZ,                  -- when soft-deleted
    rejected_by     TEXT,                         -- agent_id that rejected
    rejected_reason TEXT,                         -- required audit trail for cleanups
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
    -- No table-wide UNIQUE on (plane, resource_type, identifier).
    -- See partial unique index below: uniqueness only among live rows
    -- so rejected tombstones don't block re-upsert of the same identity.
);

CREATE INDEX idx_res_status ON resources(status) WHERE status = 'pending';
CREATE INDEX idx_res_plane ON resources(plane);
-- Live-row uniqueness: rejected rows are tombstones. A re-upsert of a
-- previously-rejected identity inserts a fresh `pending` row next to the
-- tombstone instead of colliding. `upsert_resource(_bulk)` ON CONFLICT
-- targets this partial index explicitly.
CREATE UNIQUE INDEX resources_live_unique
  ON resources (plane, resource_type, identifier)
  WHERE status != 'rejected';
-- assigned_to index removed — use resource_component_agents table
```

### `resource_component_agents`

Single source of truth for the resource → component → agent relationship — both **assignment** (`component_id` NULL = SME reserved to analyse this resource) and **ownership** (`component_id` NOT NULL = SME currently owns this component derived from this resource). Re-pointed during merges and splits. Replaces the removed columns `owned_by_agent`, `resource_ids[]`, `planes[]`, `assigned_to`, and `agent_runs.resource_id`.

```sql
CREATE TABLE resource_component_agents (
    resource_id     UUID NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
    component_id    UUID REFERENCES components(id) ON DELETE CASCADE,
                    -- nullable: at spawn time the SME has an RCA row with
                    -- component_id=NULL (reserved assignment slot). Filled
                    -- by upsert_component on first materialisation.
    agent_id        TEXT NOT NULL REFERENCES agent_runs(agent_id) ON DELETE CASCADE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY(resource_id, agent_id)
);

-- "What does this agent own or is assigned to?"
CREATE INDEX idx_rca_agent ON resource_component_agents(agent_id);
-- "Who manages this component?" (NULLs excluded naturally from equality joins)
CREATE INDEX idx_rca_component ON resource_component_agents(component_id);
-- "Who is assigned to this resource?"
CREATE INDEX idx_rca_resource ON resource_component_agents(resource_id);
```

**Lifecycle states:**
- **Reserved**: `(resource_id=X, component_id=NULL, agent_id=Y)` — written by `bulk_spawn_smes` / `create_agent` when an SME is spawned. Signals "Y is the SME assigned to analyse X, hasn't materialised yet."
- **Owned**: `(resource_id=X, component_id=C, agent_id=Y)` — written by `upsert_component` on first materialisation (UPDATE-in-place on the reserved row). Signals "Y owns component C derived from X."

**Mutation behaviour:**
- **Merge:** re-point `agent_id` on absorbed agent's rows to surviving agent. Re-point `component_id` to surviving component.
- **Split:** add new row for child agent (new `(resource_id, child_agent_id)`). Parent row stays.
- **Decommission:** depending on `resource_action`, RCA row is kept (`leave`), deleted + resource reset (`reset`), or deleted + resource rejected (`reject`).
- **Plane derivation:** `SELECT DISTINCT r.plane FROM resource_component_agents rca JOIN resources r ON r.id = rca.resource_id WHERE rca.agent_id = ?`

---

### `tasks`

Work assignments. Orchestrator assigns tasks to agents. Agents raise blockers as tasks.

```sql
CREATE TABLE tasks (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_agent_id  TEXT NOT NULL,               -- who assigned (orchestrator, admin)
    worker_agent_id TEXT NOT NULL REFERENCES agent_runs(agent_id) ON DELETE CASCADE,
    description     TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'BW' CHECK (status IN (
                        'BW',                     -- Blocked on Worker: worker's turn — initial state
                        'BO',                     -- Blocked on Owner: owner's turn (blocker raised)
                        'WD',                     -- Worker Done: worker finished, owner to review
                        'TC'                      -- Task Completed: owner accepted
                    )),
    blocker_detail  TEXT,                        -- if BO: what's needed ("need helm CLI")
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- See TRIGGER-MANAGEMENT.md for valid state transitions
CREATE INDEX idx_task_worker ON tasks(worker_agent_id);
CREATE INDEX idx_task_owner ON tasks(owner_agent_id);
CREATE INDEX idx_task_bw ON tasks(worker_agent_id) WHERE status = 'BW';
CREATE INDEX idx_task_bo ON tasks(owner_agent_id) WHERE status = 'BO';
CREATE INDEX idx_task_wd ON tasks(owner_agent_id) WHERE status = 'WD';
```

### `secrets`

Encrypted credentials per plane. Written by orchestrator during user input phase.

```sql
CREATE TABLE secrets (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    plane           TEXT NOT NULL,               -- which plane this credential is for
    key             TEXT NOT NULL,               -- credential name: "github_token", "aws_access_key"
    value           TEXT NOT NULL,               -- encrypted credential value
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE(plane, key)
);
```

---

## Category 3: Consolidation

The negotiation mechanism.

### `consolidations`

Merge/split nominations. SMEs propose, negotiate via communications table, resolver approves/rejects.

```sql
CREATE TABLE consolidations (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    proposed_by     TEXT NOT NULL,                -- agent_id of the nominator (agent_a)
    agent_a_id      TEXT NOT NULL,                -- nominator agent
    agent_b_id      TEXT,                         -- nominated agent (NULL for self-nominated splits)
    component_a_id  UUID NOT NULL REFERENCES components(id) ON DELETE CASCADE,
    component_b_id  UUID REFERENCES components(id) ON DELETE SET NULL,
    nomination_type TEXT NOT NULL CHECK (nomination_type IN (
                        'merge',
                        'split'
                    )),
    a_conf_score    FLOAT,                       -- nominator's confidence (updated each turn)
    b_conf_score    FLOAT,                       -- nominated agent's confidence
    r_conf_score    FLOAT,                       -- resolver's confidence (NULL until resolver intervenes)
    status          TEXT NOT NULL DEFAULT 'B2' CHECK (status IN (
                        'B1',                     -- Blocked on agent_a (nominator's turn)
                        'B2',                     -- Blocked on agent_b (nominated's turn) — initial state
                        'R',                      -- Resolver review
                        'M',                      -- Mutation in progress
                        'MD',                     -- Materialisation done (mutation executed)
                        'D',                      -- Done (fully complete, acked)
                        'F'                       -- Failed / rejected
                    )),
    mutation_assigned_to TEXT,                   -- agent_id responsible for executing mutation
                                                 -- merge: resolver picks A1 or A2 (more planes wins)
                                                 -- split: always A1 (self-nominator)
    child_agent_id  TEXT,                        -- set by spawn_child_agent during split
                                                 -- prevents duplicate spawns (one child per nomination)
    resolved_by     TEXT,                        -- resolver agent_id
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at     TIMESTAMPTZ
);

-- See TRIGGER-MANAGEMENT.md for valid state transitions
CREATE INDEX idx_consol_b1 ON consolidations(agent_a_id) WHERE status = 'B1';
CREATE INDEX idx_consol_b2 ON consolidations(agent_b_id) WHERE status = 'B2';
CREATE INDEX idx_consol_pending ON consolidations(status) WHERE status IN ('B1', 'B2');
CREATE INDEX idx_consol_resolver ON consolidations(status) WHERE status = 'R';
CREATE INDEX idx_consol_mutation ON consolidations(status) WHERE status IN ('M', 'MD');
CREATE INDEX idx_consol_agents ON consolidations(agent_a_id, agent_b_id);
```

---

## Category 4: Communication

Universal message bus.

### `communications`

ALL messages between agents, admin, and system. The single conversation table. Trigger manager watches this for routing.

Target columns are **cleanly split**: `to_agent` holds an agent_id (point-to-point — chat/task/consolidation/clarification); `to_agent_type` holds an agent_type (broadcasts only). A CHECK enforces that exactly one is non-null. `metadata` carries structured extras like `state_transition: {from, to}` on state-changing responses (see `respond_task`).

```sql
CREATE TABLE communications (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    from_agent      TEXT NOT NULL,               -- agent_id or 'admin'
    to_agent        TEXT,                        -- agent_id target (chat/task/...). NULL for broadcasts.
    to_agent_type   TEXT,                        -- agent_type target (broadcasts only). NULL otherwise.
    type            TEXT NOT NULL CHECK (type IN (
                        'consolidation',          -- SME ↔ SME negotiation
                        'task',                   -- orchestrator ↔ agent work assignment
                        'clarification',          -- agent ↔ admin question/answer
                        'broadcast',              -- admin → all agents of a type
                        'chat'                    -- admin ↔ specific agent free-form
                    )),
    source_id       UUID,                        -- FK to consolidations.id, tasks.id, or clarifications.id
    text            TEXT NOT NULL,               -- the message content
    metadata        JSONB NOT NULL DEFAULT '{}', -- confidence scores, state_transition {from,to}, evidence refs
    acked_at        TIMESTAMPTZ,                 -- when recipient acknowledged (chat only, NULL = unacked)
    is_persistent   BOOLEAN NOT NULL DEFAULT FALSE, -- broadcasts: TRUE = also applies to agents spawned later
                                                    -- (standing policy). Default FALSE = forward-only,
                                                    -- only agents existing at send time see it.
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT comm_target_exactly_one
      CHECK ((to_agent IS NOT NULL) <> (to_agent_type IS NOT NULL))
);

-- Partial indexes: each query path hits only the relevant half.
CREATE INDEX idx_comm_to      ON communications(to_agent)      WHERE to_agent IS NOT NULL;
CREATE INDEX idx_comm_to_type ON communications(to_agent_type) WHERE to_agent_type IS NOT NULL;
CREATE INDEX idx_comm_source  ON communications(source_id);
CREATE INDEX idx_comm_type    ON communications(type);
CREATE INDEX idx_comm_created ON communications(created_at);
CREATE INDEX idx_comm_unacked_chat ON communications(to_agent)
    WHERE type = 'chat' AND acked_at IS NULL;
```

### `broadcast_acks`

Per-agent acknowledgement of broadcast messages. One broadcast, many recipients — each needs their own ack.

```sql
CREATE TABLE broadcast_acks (
    communication_id UUID NOT NULL REFERENCES communications(id) ON DELETE CASCADE,
    agent_id         TEXT NOT NULL REFERENCES agent_runs(agent_id) ON DELETE CASCADE,
    acked_at         TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY(communication_id, agent_id)
);

CREATE INDEX idx_bcast_agent ON broadcast_acks(agent_id);
```

### `clarifications`

Metadata for questions agents raise. Status tracking only — conversations flow through `communications`.

```sql
CREATE TABLE clarifications (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    asker_agent_id  TEXT NOT NULL,               -- agent_id that raised the question
    responder_agent_id TEXT,                     -- agent_id or "admin" answering
    status          TEXT NOT NULL DEFAULT 'B2' CHECK (status IN (
                        'B1',                     -- Blocked on asker (asker's turn to elaborate/ack)
                        'B2',                     -- Blocked on responder (responder's turn) — initial state
                        'QR',                     -- Query Rejected by responder
                        'QC',                     -- Query Clarified (answer provided)
                        'CC'                      -- Clarification Completed (asker acked)
                    )),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- See TRIGGER-MANAGEMENT.md for valid state transitions
CREATE INDEX idx_clar_asker ON clarifications(asker_agent_id) WHERE status IN ('B1', 'QR', 'QC');
CREATE INDEX idx_clar_responder ON clarifications(responder_agent_id) WHERE status = 'B2';
```

### `proxy_items`

Routes decommissioned agent's pending items to the surviving agent after a merge. The surviving agent triages these: close permanently or reopen under its own name.

```sql
CREATE TABLE proxy_items (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    surviving_agent_id  TEXT NOT NULL REFERENCES agent_runs(agent_id),
    decommissioned_agent_id TEXT NOT NULL,        -- the absorbed agent
    item_type           TEXT NOT NULL CHECK (item_type IN (
                            'task', 'consolidation', 'clarification',
                            'chat', 'broadcast'
                        )),
    item_id             UUID NOT NULL,            -- FK to the source table row
    status              TEXT NOT NULL DEFAULT 'pending' CHECK (status IN (
                            'pending',             -- not yet triaged by surviving agent
                            'adopted',             -- reopened under surviving agent's name
                            'closed'               -- permanently closed
                        )),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at         TIMESTAMPTZ
);

CREATE INDEX idx_proxy_surviving ON proxy_items(surviving_agent_id) WHERE status = 'pending';
CREATE INDEX idx_proxy_decom ON proxy_items(decommissioned_agent_id);
```

---

## Embedding Strategy

Embeddings generated at write time via `cartograph-db` MCP. No batch step.


| Table          | What's embedded                                        | Purpose                                                                                                |
| -------------- | ------------------------------------------------------ | ------------------------------------------------------------------------------------------------------ |
| `components`   | `"{type}: {canonical_name} {display_name} {metadata}"` | Fuzzy matching during consolidation                                                                    |
| `attributions` | `"{resource_type}: {identifier}"`                      | Fuzzy resource lookup across planes                                                                    |
| `unresolved`   | `"{reference_type}: {reference_value}"`                | Match dangling refs to components                                                                      |
| `edges`        | `"{edge_type}: {identifier}"`                          | Fuzzy match calls across components — e.g., match `GET /scorecard/cricket` to `GET /cricket/scorecard` |


Lookup protocol for **outbound references** (refs an SME finds in its
own resource pointing at OTHER components — e.g. a DB hostname in a
config file, an API URL in outbound HTTP calls). SMEs do NOT run this
protocol for their own component — the 1-SME = 1-component invariant
means they create exactly one component via `upsert_component` and
Consolidation (not the SME's own lookup) is the mechanism for handling
duplicates across SMEs.

Cosine bands calibrated empirically for `mxbai-embed-large`:
  - exact match first (hostname / identifier in attributions)
  - vector fallback:
    - **≥ 0.75** → strong match → `create_edge` to the matched target
    - **0.60 – 0.75** → hint → `insert_unresolved` with candidate
    - **< 0.60** → `insert_unresolved` with NO candidate (Resolution
      phase links it later)
  Noise floor on this model is ~0.40-0.50 — scores in that range are
  cosine artefacts, not semantic matches. Bands were recalibrated in
  Phase 3.7 after production data showed the prior OpenAI-sized
  thresholds (0.85/0.70) sat above even verbatim-name hits.

Model: **`mxbai-embed-large` via local Ollama** (1024 dims, Metal-accelerated on Apple Silicon). Warm embed ~40-60ms per call; no API key, no network egress. Configurable via `CARTOGRAPH_EMBEDDING_MODEL` + `CARTOGRAPH_EMBEDDING_DIMS` + `CARTOGRAPH_OLLAMA_URL`. Schema migration flips `vector(N)` automatically on boot if the configured dim differs.

---

## Hydration Status


| Table          | Status |
| -------------- | ------ |
| components     | done   |
| attributions   | done   |
| edges          | done   |
| unresolved     | done   |
| agent_runs     | done   |
| resources      | done   |
| tasks          | done   |
| secrets        | done   |
| consolidations | done   |
| communications | done   |
| clarifications | done   |
| broadcast_acks | done   |
| proxy_items    | done   |
| resource_component_agents | done |



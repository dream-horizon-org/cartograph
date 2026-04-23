# Cartograph — Implementation Phases

**Status (2026-04-23):** Phase 0 ✅ · Phase 1 ✅ (incl. runtime-robustness + Phase-2 kickoff) · Phase 2 ✅ (2.1 lanes, 2.2 component-graph tools, 2.3 notification hook, 2.4 admin UI panel + broadcast) · Phase 2.5 ✅ (sleep + forward-only broadcasts) · Phase 3 ✅ (consolidation + clarification tools, embeddings live, vector_search, component_doc_md, admin UI detail views) · Phase 3.5 ✅ (graph viz with 3d-force-graph) · Phase 3.7 ✅ (local embeddings via Ollama + Metal, 1024d) · Phase 3.8 ✅ (`components.source_slice` for monorepo splits + SME materialisation flow rewrite) · Phase 3.9 ✅ (asymmetric edge protocol — catalog + bindings + flows) · **Phase 3.10 ✅** (graph viz upgrades — edge discriminators + 3-zone hover + light-of-sight BFS + sidebar tabs).
- **65 MCP tools** registered · **292 tests** passing.
- Services running: Postgres (docker), trigger manager, MCP server (:8100), admin UI (:8200), agent manager with 8 concurrent lane workers (1 orch + 2 iter + 1 res + 4 sme) + stale watchdog.

---

## Phase 0: Foundation ✅

Everything that all subsequent phases depend on.

### Infrastructure
- Docker Compose with `pgvector/pgvector:pg16`
- Postgres config: DB=cartograph, user=cartograph

### DB Migrations (ALL tables created upfront)
- `CREATE EXTENSION IF NOT EXISTS vector`
- Component graph: `components`, `attributions`, `edges`, `unresolved`
- Agent infra: `agent_runs`, `resources`, `resource_component_agents`, `tasks`, `secrets`
- Consolidation: `consolidations`
- Communication: `communications`, `broadcast_acks`, `clarifications`, `proxy_items`
- All indexes (including HNSW vector indexes, partial indexes)
- All CHECK constraints, UNIQUE constraints, FK constraints

### Shared Python Infrastructure
- DB client: connection pool, query helpers, transaction support
- Config: env vars (DB host, port, user, password, embedding model API key)
- Embedding client: embed text via `text-embedding-3-small`, return vector(1536)
- Shared types/models matching every table schema

### Core MCP Tools (cartograph-db — always available to all agents)
**Read:**
- `get_action_items_summary(agent_id)` — counts: {consolidations_pending, tasks_pending, clarifications_pending, unacked_chats, unacked_broadcasts}
  - Internally runs all 5 trigger scan queries, returns counts
- `get_action_items_detail(agent_id)` — full rows for every pending item across all categories
  - Same 5 scans but returns full row data
- `get_unacked_chats(agent_id)` — `WHERE to_agent = agent_id AND type = 'chat' AND acked_at IS NULL`
- `get_unacked_broadcasts(agent_id, agent_type)` — `WHERE type = 'broadcast' AND to_agent = agent_type AND id NOT IN (SELECT communication_id FROM broadcast_acks WHERE agent_id = ?)`
- `get_chat_history(agent_id, page, limit)` — paginated, filterable by type/from_agent/date

**Act:**
- `send_chat(from_agent_id, to_agent_id, message)` — INSERT communication type='chat'. Validate: agents can only message "admin" initially; admin can message any agent.
- `ack_chats(agent_id, communication_ids[])` — SET acked_at=now() WHERE id IN (...) AND to_agent=agent_id AND type='chat'. Selective by IDs.
- `send_broadcast(from_agent_id, to_agent_type, message)` — INSERT communication type='broadcast', to_agent=agent_type. Validate: only orchestrator/admin.
- `ack_broadcast(agent_id, communication_id)` — INSERT into broadcast_acks. Validate: communication exists and is type='broadcast'.

### Trigger Manager: Core Loop
- Scan `agent_runs` for all agents with status='idle' AND trigger_lock=FALSE
- For each idle agent, run action item scans (consolidations, tasks, clarifications, chats, broadcasts)
- If any pending items found → `UPDATE agent_runs SET trigger_lock=TRUE WHERE agent_id=? AND status='idle' AND trigger_lock=FALSE`
- Priority ordering: orchestrator > resolver > sme > iterator
- Within same type: higher invocation_count = lower priority
- Auto-transitions: consolidation confidence breach check (both > 0.85 AND r_conf IS NULL → set status='R')
- Configurable sleep interval between scans

### Agent Manager (implemented in `src/agent_management/`)
- Polls agents with `trigger_lock=TRUE`; atomic pickup: `SET status='running', trigger_lock=FALSE, invocation_count++, heartbeat=now()`.
- Spawns `claude -p` subprocess per-agent in workspace dir with `.mcp.json` pointing at `http://localhost:8100/mcp` (streamable-http FastMCP).
- Flags used: `--setting-sources project --dangerously-skip-permissions --allowedTools Bash,Read,Write,Edit,Glob,Grep,mcp__cartograph-db__*`.
- Per-type subprocess timeouts: iterator/SME = 1800s (long enumerations, deep analysis), orchestrator/resolver = 900s.
- Background heartbeat thread updates every 10s during subprocess; stale `running` agents (heartbeat > 120s beyond last update) flipped to `errored` as a safety net.
- On every errored path (non-zero exit, `TimeoutExpired`, uncaught exception, stale detection): `set_agent_errored(error_msg)` persists stderr tail / exception repr + stamps `errored_at`. No more silent failures.
- Survives parent shell death via `SIGHUP = SIG_IGN`; on startup flips any orphaned `running` → `errored` (leaves existing `errored` alone — recovery scanner governs retries).
- Agent type configs in `agent_types/{orchestrator,iterator,sme,resolver}.py` supply system prompt, allowed tools, MCP servers.
- Shared `MISSION_AND_VOCABULARY` block in `agent_types/base.py` injected into every prompt — defines component / resource / attribution / edge, per-plane iterator granularity rules, and the scale sanity check.

### Bounded Auto-Recovery (`src/trigger_management/scanners/recovery.py`)
- New columns on `agent_runs`: `errored_at TIMESTAMPTZ`, `recovery_attempts INT NOT NULL DEFAULT 0`.
- Recovery scanner runs each trigger-manager cycle: `WHERE status='errored' AND recovery_attempts<3 AND now()-errored_at >= backoff(recovery_attempts)` → flip to `idle`, clear `trigger_lock`, increment `recovery_attempts`, keep `error_msg` for history.
- Backoff ladder: **[60s, 300s, 1800s]**. After 3 attempts the agent stays `errored` for human triage (visible in admin UI with its `error_msg`).
- Successful invocation (`status=running → idle`) calls `clear_recovery_state` → resets `recovery_attempts=0` and clears `error_msg`, so past transient errors don't count against a now-healthy agent.
- Orchestrator escape hatch: `reset_agent(agent_id, target_agent_id)` MCP tool force-resets a permanently-errored agent regardless of the cap.

### Admin Chat UI (`src/admin_ui/`)
- FastAPI server on :8200 · 5 endpoints: list agents, get chat (paginated), poll-new, send, ack.
- Vanilla JS with marked.js + DOMPurify + highlight.js for safe markdown rendering.
- 2s polling, infinite-scroll pagination via `before` cursor.

### Test Isolation (operational best practice — learned the hard way)
- All test conftest.py files set `os.environ["CARTOGRAPH_DB_NAME"] = "cartograph_test"` **before** importing `shared.db`.
- `setup_db` fixture raises `RuntimeError` if `config.DB_NAME == "cartograph"`.
- `cartograph_test` DB must exist (`CREATE DATABASE cartograph_test` in same Postgres container).

---

## Phase 1: Iteration ✅

Task management — orchestrator assigns work to iterators.

### MCP Tools Added
**Read:**
- `get_my_tasks(agent_id)` — `WHERE (worker_agent_id = agent_id) OR (owner_agent_id = agent_id)`. Returns status, description, blocker_detail.
- `get_task_thread(task_id)` — all communications WHERE source_id = task_id AND type = 'task', paginated. Scoped: only owner or worker can read.

**Act:**
- `create_task(owner_agent_id, worker_agent_id, description)` — INSERT task (status=BW) + INSERT communication (type='task'). Validate: only orchestrator/admin.
- `respond_task(agent_id, task_id, message, new_status, blocker_detail?)` — UPDATE task status + INSERT communication. Validate:
  - Agent is owner or worker on this task
  - State actually changes
  - Worker valid transitions: BW→BO (raise blocker), BW→WD (complete)
  - Owner valid transitions: BO→BW (resolve blocker), BO→TC (close directly), WD→BW (reject), WD→TC (accept)
- `raise_blocker(agent_id, task_id, blocker_detail)` — shortcut: sets task status=BO + INSERT communication to owner. Validate: agent is worker.

### Trigger Manager Additions
- Scan tasks: `WHERE (worker_agent_id = agent_id AND status = 'BW') OR (owner_agent_id = agent_id AND status IN ('BO', 'WD'))`
- Include task counts in action items summary/detail

### Additional Phase-1 Tools (built while implementing)
**Secrets (`tools/secrets.py`):**
- `put_secret(agent_id, plane, key, value)` — ORCHESTRATOR-only. Upserts on `(plane, key)`.
- `get_secret(agent_id, plane, key)` — any agent can read.
- `list_secrets_for_plane(agent_id, plane)` — returns keys only (no values).
- `delete_secret(agent_id, plane, key)` — ORCHESTRATOR-only.

**Agent lifecycle (`cartograph_mcp/server.py` + `tools/agent_lifecycle.py`):**
- `create_agent(agent_id, new_agent_type, plane?, resource_id?)` — ORCHESTRATOR-only. Spawns iterator (plane) or SME (resource_id). For SMEs also writes RCA reservation row (`component_id=NULL`) and flips resource to 'assigned'.
- `bulk_spawn_smes(agent_id, plane, resource_ids?, all_pending?, task_description?)` — ORCHESTRATOR-only. One-transaction bulk spawn: N agent_runs + N RCA reservation rows + optional N tasks. Refuses blank-wipe; skips resources already assigned.
- `list_agents(agent_id)` — any agent can see all non-decommissioned agents.
- `reset_agent(agent_id, target_agent_id)` — ORCHESTRATOR-only override for permanently-errored agents (bypasses recovery attempt cap).
- `decommission_agent(agent_id, target_agent_id, reason, resource_action='leave'|'reset'|'reject')` — ORCHESTRATOR-only. Flips target to 'decommissioned' with explicit resource cascade. Refuses self-decom.
- `decommission_agents_bulk(agent_id, reason, agent_ids?, agent_type?, resource_action?)` — bulk teardown (cohort / plane-wide). Refuses agent_type='orchestrator'; never decommissions caller.
- `decommission_component(agent_id, component_id, reason)` — ORCHESTRATOR-only soft-delete (status='decommissioned').
- `decommission_components_bulk(agent_id, component_ids[], reason)` — bulk variant with explicit id list (refuses blank-wipe).

**Schema: RCA becomes single source of truth for SME assignment.**
- `resource_component_agents.component_id` made nullable; PK moved from `(resource_id, component_id)` to `(resource_id, agent_id)` so a reservation row can exist before the component does.
- Reserved row: `(resource_id=X, component_id=NULL, agent_id=Y)` = "Y assigned to X, not materialised yet".
- Owned row: `(resource_id=X, component_id=C, agent_id=Y)` = "Y owns C derived from X" (written by `upsert_component` on first materialisation — Phase 2).
- `agent_runs.resource_id` dropped — superseded by RCA. Agent manager looks up the SME's resource from RCA at invoke time for prompt templating.

**Resources (`tools/resources.py`):**
- `upsert_resource(agent_id, plane, resource_type, identifier, access_desc, metadata?)` — ITERATOR-only, own plane only. Idempotent on `(plane, resource_type, identifier)`.
- `upsert_resources_bulk(agent_id, plane, items[])` — ITERATOR-only bulk variant (limit 5000, one transaction, `executemany(RETURNING)`). Used for large planes to avoid N MCP round-trips per iterator invocation.
- `get_resource(agent_id, resource_id)`, `list_resources_for_plane(agent_id, plane)`, `list_all_resources(agent_id, status?)`, `get_resource_counts(agent_id)`.
- `mark_resource_done(agent_id, resource_id)` — SME-only; validated via `resource_component_agents`.
- `reject_resource(agent_id, resource_id, reason, force=False)`, `reject_resources_bulk(agent_id, plane, resource_ids?, resource_types?, reason, force=False)` — soft-delete (`status='rejected'` + `rejected_at/by/reason` audit trail). Iterator on own plane by default; orchestrator with `force=True` as override. Plane-scoped, refuses blank-wipe. Cascade-safe: rows already linked to an SME via `resource_component_agents` are skipped and returned in `skipped_cascade`.
- Schema additions: `resources.status` gained `'rejected'`; new columns `rejected_at`, `rejected_by`, `rejected_reason`. `list_all_resources` excludes rejected rows by default.

### Tables Active
- agent_runs (+ `errored_at`, `recovery_attempts`), resources (+ `rejected_at/by/reason`), tasks, communications, secrets, (resource_component_agents written on mark_resource_done check path + read by reject cascade check)

---

## Phase 2: Materialisation + Parallel Runtime ✅

Shipped in four commits, order 2.1 → 2.2 → 2.4 → 2.3:
- `9532927` Phase 2.1 — lane-based parallel invoke loop
- `aa8dcf3` Phase 2.2 — SME component-graph tool set (9 new tools)
- `b1dcd59` Phase 2.4 — admin UI communications panel + broadcast + state metadata
  (then `32eaefc` cleaned up the overloaded `to_agent`; `f9a2d25` added
  `from_agent_type`/`to_agent_type` filters; `5f7b1b0` added participant
  filters; `2fa4205` grouped the chat sidebar by type with per-group search)
- `c99b83d` Phase 2.3 — PostToolUse notification hook

### 2.1 Parallel Invoke Loop (lane-based) ✅

**Problem today:** `InvokeLoop` runs one claude subprocess at a time. With
iterator/SME timeouts of 1800s, an SME mid-analysis blocks orchestrator
wake-ups behind it. Also: `_process_locked_agents` takes a snapshot and
iterates it, so higher-priority items that get `trigger_lock`ed mid-cycle
are ignored until the next scan.

**Design:**
- Per-type concurrency caps (env-configurable, sensible defaults):
  - `INVOKE_LANES_ORCH=1`
  - `INVOKE_LANES_RES=1`
  - `INVOKE_LANES_ITER=2`
  - `INVOKE_LANES_SME=4`
  - Total: 8 concurrent claude subprocesses by default.
- Implementation: one `ThreadPoolExecutor(max_workers=8)` + per-type
  `threading.Semaphore` guarding pickup. Worker acquires the type sem
  before it calls `pickup_agent`; releases after subprocess completes.
- Dispatcher re-reads the queue on every iteration (no snapshot) using
  `SELECT ... FOR UPDATE SKIP LOCKED` so higher-priority agents that
  land during a cycle are picked up immediately on the next free slot.
- Priority within bucket: `ORDER BY invocation_count ASC` (unchanged).
- Per-type priority across types preserved via the existing `PRIORITY_ORDER`
  — an orch with 0 invocations outranks an SME with 0 invocations only
  when lanes are contending (i.e. when orch lane is free).

### 2.2 SME Component-Graph Tool Set ✅

Full Phase-2 SME write set + reads for all agents. Auto-embedding deferred
(columns stay `NULL`; embedding client + backfill + `vector_search` land
in Phase 3 prep).

**MCP Tools Added:**

Writes (SME-scoped):
- `upsert_component(agent_id, component_data)` — CREATE on first call (fills
  the SME's RCA reservation row: `component_id=NULL` → `component_id=C`);
  UPDATE on subsequent calls. Enforces the **1-active-component-per-SME
  invariant**: refuses if SME already owns a non-decommissioned component
  and this call would create a second.
- `upsert_attribution(agent_id, component_id, attribution_data)` — scoped
  to the caller's own component (via RCA). ON CONFLICT on `(plane,
  resource_type, identifier)` does UPDATE.
- `create_edge(agent_id, edge_data)` — scoped to SME owning `source_id`.
  CHECK `source_id != target_id`; UNIQUE on `(source_id, target_id,
  edge_type, identifier)`.
- `insert_unresolved(agent_id, unresolved_data)` — scoped to SME owning
  `found_in_component_id`.
- `resolve_reference(agent_id, unresolved_id, resolved_to_component_id)` —
  SET `resolved=TRUE`, `resolved_to_component_id`; optionally create edge.

Reads (all agents):
- `get_component(component_id)`
- `get_attributions(component_id)`
- `get_edges(component_id)` — inbound + outbound
- `get_unresolved(component_id)`

**Explicitly deferred:**
- `vector_search` — needs embedding pipeline, Phase 3 prep.
- Auto-embed on write — same.

### 2.3 PostToolUse Notification Hook (per-agent async channel) ✅

Every agent gets a Claude Code `PostToolUse` hook that queries a new MCP
endpoint after each tool call. If new high-priority messages landed since
the agent's last invocation, the hook emits `[NOTIFY] ...` to the
conversation so the agent can react mid-session.

**Design:**
- New MCP tool `get_agent_notifications(agent_id, priority_from_agent_types=[...])`
  returning compact JSON:
  ```
  {high_priority: 3,
   breakdown: [{from_type: 'orchestrator', type: 'task', count: 2},
               {from_type: 'admin',        type: 'chat', count: 1}]}
  ```
- Hook script: small Python CLI in `src/agent_management/hooks/notify.py`
  that takes `--agent-id` + `--priority=<csv>`, hits MCP via localhost,
  and ON notifications emits a JSON envelope that Claude Code injects
  into the agent's conversation as `additionalContext`:
  ```json
  {"hookSpecificOutput": {
     "hookEventName": "PostToolUse",
     "additionalContext": "[NOTIFY] N new high-priority item(s): ..."}}
  ```
  This JSON-envelope format is mandatory for PostToolUse: plain stdout
  from a PostToolUse hook only reaches the user's transcript view, NOT
  the model. Empty stdout on quiet cycles = silent (agent sees nothing).
- `create_agent` writes a per-workspace `.claude/settings.json` with a
  `PostToolUse` matcher `*` calling the notify script with the agent's
  own `agent_id` + its per-type priority source list:
  - orchestrator: `['admin']`
  - iterator:     `['admin', 'orchestrator']`
  - SME:          `['admin', 'orchestrator']`
  - resolver:     `['admin', 'orchestrator']`
- Rate-limiting: notify script short-circuits if called within 10s of its
  last run (state in `{cwd}/.cartograph-notify-last`) — prevents 50-tool-
  call sessions from triggering 50 DB hits.

### 2.4 Admin UI — Communications Panel + Broadcast + State Metadata ✅

**Backend additions:**
- Log state transitions in `communications.metadata` where applicable:
  - `respond_task` → `metadata.state_transition = {from, to}` on the
    communication row (Phase 1 tool; backfill now).
  - `respond_consolidation`, `respond_clarification` → same shape when
    those tools land (Phase 3).
- New HTTP endpoints in admin UI:
  - `GET /api/communications?from_agent=&to_agent=&type=&source_id=&before=&limit=`
  - `GET /api/task/:id` — task row + current status + thread
  - `GET /api/consolidation/:id` — (placeholder, Phase 3)
  - `GET /api/clarification/:id` — (placeholder, Phase 3)
  - `POST /api/broadcast` — admin broadcasts via the UI directly (inserts
    communication type='broadcast', from_agent='admin', to_agent=agent_type).

**Frontend:**
- Top nav with two tabs: **Chat** (original) and **Communications** (new).
- Chat tab: agent list now grouped by agent_type with per-group typable
  search (orchestrator, resolver, iterator, sme sections).
- Communications tab: 3-panel grid [filter bar | list | detail]. Filters
  split into _Participant_ (`agent`, `agent_type` — either direction) and
  _Directional_ (`from_agent`, `from_agent_type`, `to_agent`, `to_agent_type`)
  plus communication type. Typable-combobox inputs for agent ids.
- Detail panel: for task rows, renders status/owner/worker/blocker/thread
  with per-message state-transition pills.
- 📢 Broadcast button in top nav opens a dialog → POST `/api/broadcast`.

**Rationale for admin-direct broadcast:** admin is already the most
privileged actor. Routing broadcast through orchestrator adds a round-trip
+ token cost + human wait for zero architectural gain.

**Bonus cleanup that fell out of 2.4:** `communications.to_agent` was
overloaded (either an agent_id or an agent_type depending on `type`).
Split into `to_agent` (agent_id, nullable) + new `to_agent_type`
(agent_type, nullable) with a CHECK-exactly-one constraint; broadcast
rows backfilled; every read/write site updated. This is what makes the
agent-type filters clean (no discriminator dance in SQL).

### Testing discipline

- Every new function + tool gets unit tests covering success paths, error
  paths, and scope/authorization checks. No "we'll test it later." No
  ordering enforcement (test-first vs test-with is fine), but the suite
  must go green before commit.
- Each sub-phase lands in its own commit once its tests pass.

### Tables Activated
- components, attributions, edges, unresolved, resource_component_agents
  (reservation → owned transition)

### Deferred to Phase 3 prep
- Embedding pipeline (OpenAI client, text-embedding-3-small, auto-embed on
  write, HNSW index usage).
- `vector_search` tool.
- Consolidation + clarification state tooling + `state_transition`
  metadata backfill for those tools.

---

## Phase 2.5: Sleep + forward-only broadcasts ✅

Both fixes target the same problem — the trigger scanner pestering agents
pointlessly. Shipped in commit `70b89b0` with small UI follow-ups
(`48a4b38` per-agent buttons, `e4b3371` dialog UX, `b058ae2` expired-sleep FE fix).

### Sleep
- `agent_runs.sleep_until TIMESTAMPTZ` (nullable). Trigger scanner
  idle-lock filter adds `AND (sleep_until IS NULL OR sleep_until <= now())`.
- `idx_agent_sleep` partial index (cheap scans on the small sleeping set).
- Three new MCP tools (46 → 48 live):
  - `sleep_self(agent_id, duration_seconds, reason)` — any agent;
    ≤ 7 days.
  - `bulk_sleep_agents(agent_id, until, reason, agent_ids?, agent_type?)`
    — orch/admin. Refuses `agent_type='orchestrator'` and never sleeps caller.
  - `bulk_wake_agents(agent_id, agent_ids?, agent_type?)` — orch/admin.
- Interrupt semantic: admin chat to a sleeping agent auto-wakes
  (`send_chat` clears `sleep_until` when `from_agent='admin'`).
  Broadcasts, tasks, orchestrator-to-agent chats do NOT interrupt sleep.
- Admin UI: per-agent 💤/⏰ row buttons + per-group 💤/⏰ headers + sleep
  chip on agent rows when asleep. New `POST /api/sleep` + `POST /api/wake`.

### Forward-only broadcasts
- `communications.is_persistent BOOLEAN NOT NULL DEFAULT FALSE`.
- `send_broadcast(..., persistent=False)` and admin `POST /api/broadcast`
  both accept the flag; UI broadcast dialog gets a "Persistent" checkbox.
- Query scoping updated in three places (scanner, `get_unacked_broadcasts`,
  `get_agent_notifications`):
  `AND (c.is_persistent OR c.created_at > (SELECT created_at FROM agent_runs WHERE agent_id = me))`
- Fixes "new agent bombarded by all historical broadcasts of its type."

### Agent prompts
- All four types gained a `== SLEEP WHEN WAITING ==` block referencing
  `sleep_self` + interrupt rules (admin-chat override, bulk wake).
- Orchestrator additionally learned `bulk_sleep_agents` / `bulk_wake_agents`.

### Tests
- `tests/mcp_tools/test_sleep.py` (19): sleep_self validation, bulk sleep
  by ids/type, orch-type refusal, caller-skip, past-timestamp refusal,
  blank-wipe refusal, non-orch refusal, admin path, wake clears,
  scanner idle-filter skip, expired-sleep re-pickup, admin-chat auto-wake,
  non-admin chat doesn't interrupt.
- `tests/mcp_tools/test_broadcast_scoping.py` (7): new agent doesn't see
  historical non-persistent broadcast; persistent historical broadcast
  visible; post-spawn non-persistent broadcast visible to existing agents;
  notifications respect scoping; default False; stored TRUE.

Total: 170 → 196 tests.

### Prompt-session caveat (operational note)
Existing agents spawned before Phase 2.3 do not get the PostToolUse hook
automatically — their workspaces predate the `_write_claude_settings`
code. Remedy: write `.claude/settings.json` into each existing workspace
(one-shot manual backfill, or let agents respawn). NEW agents created via
`create_agent` / `bulk_spawn_smes` get it correctly. System prompt updates
DO apply to resumed sessions (each `claude -p` invocation passes the
latest `--system-prompt` regardless of `--resume`), so no session reset is
needed for prompt changes — only for hook-config changes.

**Hook-output format gotcha (learned empirically, commit `557ffc4`):** the
first cut of `notify.py` printed the `[NOTIFY] ...` string as plain stdout.
Claude Code's PostToolUse hook does NOT inject plain stdout into the
agent's conversation — it only surfaces to the user's transcript view.
To feed text back to the model, the hook must emit
`{"hookSpecificOutput": {"hookEventName": "PostToolUse",
"additionalContext": "..."}}` on stdout. The current script does this;
ordinary text would silently fail to reach the agent despite the script
running correctly.

---

## Phase 3: Consolidation ✅

SMEs negotiate merges/splits. Resolver reviews and approves. Embeddings
populated at write time. Admin UI renders consolidation + clarification
detail views with confidence pills and state-transition pills on every
thread message.

Shipped across seven focused commits:
- `fc74d05` step 1 — `components.component_doc_md` schema migration
- `397c3bb` steps 2+3 — embedding pipeline + component_doc_md wiring
- `9fdbae6` step 4 — `vector_search` MCP tool
- `7e06978` step 5 — 5 consolidation MCP tools
- `9d14cdc` step 6 — 4 clarification MCP tools
- `a984ddf` step 7 — auto-transitions verified + action_items broadcast fix
- `c9a6c1b` step 8 — admin UI consolidation/clarification detail views
- `d1ceace` step 9 — SME prompt (component_doc_md + evidence ladder)
- step 10 — 46 new tests

### 3.1 Schema additions
- `components.component_doc_md TEXT` (nullable). SME-authored markdown
  blob — short human-readable description rendered in the graph-viz
  hover popup (Phase 3.5). Set on `upsert_component`; COALESCE-style
  update so subsequent calls that omit the key leave the value intact.

### 3.2 Embeddings
- `shared/embedding.py` — OpenAI `text-embedding-3-small` (1536 dims).
  Graceful degrade on missing `OPENAI_API_KEY` / API error: writes
  `embedding=NULL`, `vector_search` skips the row.
- Auto-embed inside `upsert_component`, `upsert_attribution`, `create_edge`,
  `insert_unresolved` — per SCHEMA.md §Embedding Strategy.

### 3.3 MCP Tools Added

**vector_search (all agents):**
- `vector_search(agent_id, query_text, table, limit)` — embeds query and
  KNN-cosines against target table's embedding column. Tables:
  `components`, `attributions`, `unresolved`, `edges`. Returns
  `{query_embedded: bool, results: [...]}` so callers can distinguish
  "no matches" from "couldn't embed". Limit clamped to [1, 50].

**Consolidation (SME-scoped writes + resolver):**
- `nominate_consolidation(agent_id, component_a_id, component_b_id?, type, confidence, message)`
  — SME-only, caller must own `component_a`. type='merge' requires
  `component_b_id` owned by another SME; type='split' accepts `component_b_id=None`
  (the child is spawned after resolver approval, Phase 4).
- `respond_consolidation(agent_id, consolidation_id, confidence, message, new_status)`
  — state-machine validated. Writes `a_conf_score` if caller is agent_a,
  `b_conf_score` if caller is agent_b. Manual escalate to R is refused
  unless `r_conf_score` is already set; first escalation goes through
  the auto_transitions scanner.
- `review_consolidation(agent_id, consolidation_id, r_confidence, message, new_status, mutation_assigned_to?)`
  — resolver-only. Transitions R → B1/B2/F/M. M requires
  `mutation_assigned_to`; split enforces it equals `agent_a`; merge
  requires it be `agent_a` or `agent_b`.
- `get_my_consolidations(agent_id)` — non-terminal rows where agent is
  a participant. Resolvers see all non-terminal.
- `get_consolidation_thread(agent_id, consolidation_id, page, limit)`
  — paginated thread, scoped to participants + resolver.

**Clarification (all agents):**
- `create_clarification(asker, responder, question)` — responder can be
  any agent or literal `'admin'`. Inserts row (status=B2) + initial
  question comm.
- `respond_clarification(agent_id, clarification_id, message, new_status)`
  — state-machine validated per role (asker/responder).
- `get_my_clarifications(agent_id)` — non-terminal rows where agent is
  asker or responder.
- `get_clarification_thread(agent_id, clarification_id, page, limit)`
  — paginated thread, scoped to asker + responder.

All respond writes stamp `metadata.state_transition = {from, to}` on
the communication row (mirrors `respond_task` from Phase 1), which
powers the admin UI's per-message state pills.

### 3.4 Auto-transitions (verified)
`trigger_management/scanners/auto_transitions.py`:
- `a_conf_score >= 0.85 AND b_conf_score >= 0.85 AND r_conf_score IS NULL` →
  set `status='R'`.
- `a_conf_score <= 0.3 AND b_conf_score <= 0.3` → set `status='F'`.
Scanner called once per `trigger_loop.run_once` cycle.

### 3.5 Admin UI detail views
`/api/consolidation/:id` and `/api/clarification/:id` already returned
`{row, thread}` from Phase 2.4; the FE replaced the JSON pre-dump with
structured renderers:
- Consolidation: status badge, nomination type, agent_a/b, component_a/b,
  three color-coded confidence pills (A/B/R with high / mid / low / null
  bands), mutation POC when set, full thread with `state_transition`
  pills on every response.
- Clarification: status badge, asker, responder, full thread.
- CSS cache-bust `?v=14`.

### 3.6 SME prompt
`sme.py` gained:
- Explicit guidance to populate `component_doc_md` on every
  `upsert_component` call (3–8 lines, what the component does +
  key attributions + dependencies).
- Evidence Ladder for consolidation confidence calibration — five bands
  mapping observable signals (shared deploy manifest, shared DB conn
  string, shared hostname, shared repo path, name similarity) to
  confidence ranges.
- Updated consolidation tool contracts to match the implementation.

### 3.7 Tests
- `tests/mcp_tools/test_component_doc_md.py` (4)
- `tests/mcp_tools/test_consolidation.py` (16)
- `tests/mcp_tools/test_clarification.py` (11)
- `tests/mcp_tools/test_auto_transitions_phase3.py` (3)
- `tests/mcp_tools/test_vector_search.py` (5)
- conftest `clean_tables` now wipes consolidations / clarifications /
  unresolved / edges / attributions (prior gap).
- `tests/test_agent_types.py` pre-existing `bash` → `Bash` case fix.

### 3.8 Latent bug caught en passant
`action_items._count_unacked_broadcasts` + `_get_unacked_broadcast_details`
were still querying `to_agent` for broadcast targets, which has been
NULL for broadcasts since the Phase 2.4 split to `to_agent_type`. Fixed
+ forward-only scoping (`is_persistent OR created_at > agent.created_at`)
applied to match the other broadcast readers.

### Tables activated
- consolidations, clarifications
- embedding columns on components / attributions / edges / unresolved
  are now populated at write time (were NULL through Phase 2)

---

## Phase 3.5: Graph Viz ✅

Interactive 3D force-directed graph of components + dependencies, served
from the admin UI. Nothing new on the agent side — the graph is a
read-only visualisation of the data SMEs write via Phase 2/3 tools.

### Components

**Backend** (`admin_ui/server.py`): new `GET /api/graph` endpoint.
```json
{
  "nodes": [{id, canonical_name, display_name, component_type,
             status, component_doc_md, planes: [plane, ...]}],
  "edges": [{id, source_id, target_id, edge_type, identifier, confidence}]
}
```
Nodes aggregate distinct planes per component via a LEFT JOIN on
attributions. Decommissioned components and edges to/from them are
filtered out. Single query per request; no pagination (component count
expected to stay in the low thousands).

**Frontend:** new **Graph** tab in the top nav, sitting beside Chat +
Communications. Uses `3d-force-graph@1.73.4` from jsDelivr (pure JS,
bundles three.js + three-forcegraph internally — consistent with the
existing CDN-loaded `marked` and `DOMPurify`).

- Node color per plane (github/deploy/cloud/telemetry/config). Multi-
  plane components get an RGB-averaged blend so components with
  attributions across planes stand out visually. Components without any
  attributions render grey.
- Node size scales with plane count — more planes = bigger node.
- Link label: `{edge_type}: {identifier}`.
- Hover or click a node → sidebar renders:
  - Canonical name + display name + component type
  - Plane pills (same color key as nodes)
  - `component_doc_md` rendered through the existing `marked` +
    `DOMPurify` pipeline (reused from the chat view)
- Sidebar legend + live counts of components / edges.
- Interactive physics: drag a node → it bounces/jiggles/settles (library
  default — no custom pause/resume logic). Zoom via scroll, orbit via
  left-drag, pan via right-drag.

### Files touched
- `src/admin_ui/server.py` — new `/api/graph` endpoint.
- `src/admin_ui/static/index.html` — Graph tab, graph view, CDN script.
- `src/admin_ui/static/app.js` — `switchTab` handles 'graph';
  `initOrRefreshGraph()`, `blendColors()`, `nodeColor()`,
  `showGraphHoverDoc()`; window resize handler.
- `src/admin_ui/static/style.css` — `#graph-view.active` grid,
  sidebar styling, legend dots, plane pills, canvas fill.
- Cache-bust bumped to `?v=15`.

### Tests
`tests/admin_ui/test_graph_endpoint.py` (4):
- empty graph
- components + edges returned with component_doc_md
- plane aggregation across multiple attributions per component
- decommissioned components + their edges excluded from both lists

`conftest.clean_tables` for admin UI now wipes components / attributions
/ edges / RCA / resources to avoid cross-test leakage (same gap fix as
mcp_tools/conftest).

---

## Phase 3.7: Local embeddings via Ollama ✅

Replaced the remote OpenAI embedder with a local Ollama instance so the
embedding path has no API key, no network egress, and no rate limits —
and runs on the Apple Silicon GPU via Metal.

### Why
- Production had no `OPENAI_API_KEY` set → every `vector_search` call
  returned `{query_embedded: false, results: []}` and every embedding
  column was NULL. The SME materialisation similarity ladder collapsed
  to "always create new", breaking dedup before it even started.
- Local inference on M-series is ~40-60ms warm (vs 150-300ms for OpenAI
  round-trips) and doesn't care about internet weather.

### Model
- Default: `mxbai-embed-large` (1024 dims). Top-of-MTEB-board for
  English in its size class, 335M params, ~670MB on disk.
- Override via `CARTOGRAPH_EMBEDDING_MODEL` + `CARTOGRAPH_EMBEDDING_DIMS`
  if you prefer something else (e.g. `nomic-embed-text` at 768d).

### Changes
- `shared/embedding.py`: swapped `https://api.openai.com/v1/embeddings`
  POST for `http://localhost:11434/api/embeddings` (Ollama). Kept the
  graceful-degrade contract — if Ollama is unreachable or the model
  isn't pulled, `embed_text` returns None, writes land with
  `embedding=NULL`, `vector_search` returns `query_embedded=False`.
- `shared/embedding.py::warmup()`: new helper. Fires a throwaway embed
  at MCP server boot so the first real call doesn't eat the ~1s
  cold-start. Called from `cartograph_mcp.server.main()`.
- `shared/config.py`: `OLLAMA_URL`, `EMBEDDING_MODEL=mxbai-embed-large`,
  `EMBEDDING_DIMS=1024`. Dropped `OPENAI_API_KEY`.
- `shared/migrations.py::_migrate_embedding_dims()`: reads each table's
  current vector dim via `pg_attribute.atttypmod`. If it differs from
  `config.EMBEDDING_DIMS`, drops the HNSW index, NULLs the column,
  ALTERs to the target dim, rebuilds HNSW. Idempotent across boots.
  Destructive-of-embeddings, but embeddings on prod were all NULL so
  no data lost. Future dim flips (if a better model ships) will need
  a re-embed pass, not a blind migration.

### Operational
- `brew install ollama && brew services start ollama`
- `ollama pull mxbai-embed-large` (one-time, ~670MB).
- MCP server restart → `run_migrations` flips the vector dims +
  `warmup` loads the model.

### Tests
- `test_vector_search.py` updated: 5 structural tests (mostly
  graceful-degrade behaviour when Ollama is unreachable) + 1 live
  end-to-end test that's `@skipif` when Ollama isn't running.

### Backward-compat: re-embed existing rows
- `shared/embedding_backfill.py::backfill_all()` — scans each of the 4
  vector tables for `embedding IS NULL`, builds the embed text with
  the same helpers as the write path, re-embeds, writes back.
  Idempotent — a second run is a no-op.
- Runnable two ways:
  - CLI: `python -m shared.embedding_backfill` from `src/`.
  - Admin-UI endpoint: `POST /api/backfill_embeddings` returns
    per-table `{done, skipped}` counts.
- Skipped rows (Ollama unreachable, empty embed text) stay NULL and
  can be retried by re-running.

### Status on prod
Ran once right after the Phase 3.7 cutover — 5/5 NULL embeddings
backfilled (2 components, 2 attributions, 1 unresolved).

### Deferred
- Batched embedding for bulk writes (e.g. `upsert_attributions_bulk`).
  One-at-a-time is fine at current scale; revisit if SME materialisation
  latency becomes a bottleneck.

---

## Phase 3.8: `source_slice` + SME materialisation flow rewrite ✅

Two motivations:

1. **Monorepos are real.** A resource representing a monorepo can
   contain N>1 deployable components. Hydrating everything into one
   component is useless — humans reading the graph want per-service
   nodes, and Phase 4's split machinery needs a way to describe which
   parts of the source a component covers.
2. **The prior SME materialisation prompt was wrong on two axes.**
   It (a) told SMEs to "dedup check before upsert_component" which
   steps on Consolidation's job and races with other SMEs; (b) said
   "for each potential component, create new if similarity was low"
   which contradicts the 1-SME = 1-component invariant and would
   cause an SME to create multiple components from one resource.

### Schema

`components.source_slice JSONB` (nullable). Map keyed by `resource_id`
UUID; inner dict carries `plane` + typed sub-arrays (`paths`, `files`,
`manifests`, `workflows`, `entry_points`, `k8s_workloads`, open taxonomy
for additions). NULL = component covers its whole source resource.
Multi-resource components (post-merge) carry multiple keys.

See SCHEMA.md §components for the full shape + exclusivity invariant.

### `upsert_component` semantics

- Accept optional `component_data.source_slice` dict.
- Structural validation: must be a dict if present.
- REPLACE-on-provide (caller passes FULL current view).
- COALESCE-preserve on omit (previous slice intact).

Mirrors `component_doc_md`'s semantics — caller pattern is identical.

### SME materialisation flow (rewritten)

Three outcomes (was: one monolithic "for each potential component"
loop):

- **(A) Not a component** — shallow check reveals no deployable →
  `raise_blocker` to orchestrator.
- **(B) Monorepo** — multi-component resource. Write a SKELETON
  component (doc = split plan, source_slice = container-level
  coverage, minimal cross-cutting attributions). Enter split loop:
  nominate one split at a time (serial — each split mutates the
  parent, concurrent nominations would race against a moving target),
  wait for D, re-evaluate, repeat until the remaining slice is one
  coherent component, then hydrate fully.
- **(C) Single component** — normal flow: upsert_component once,
  hydrate attributions exhaustively, resolve outbound refs via the
  cosine ladder.

The cosine ladder stays in Step 3 (outbound refs only, never for the
SME's own component).

### Phase 4 mutation contract (documented; implemented when Phase 4 lands)

- `spawn_child_agent` (split): inside the mutation transaction, copy
  child's share out of parent's `source_slice` into child's
  `source_slice`, remove from parent. Atomic.
- `absorb_agent` (merge): merge target's `source_slice` into
  surviving's — union inner arrays per `resource_id`, dedup preserving
  first-seen order. Decommissioned target's slice stays as frozen
  tombstone.
- `transfer_attributions`: deliberately does NOT auto-touch
  `source_slice` — attributions are evidence, slice is structural.
  The mutation_assigned_to SME must call `upsert_component`
  explicitly on both sides to update slice. This is called out in
  the SME mutation prompt.

Exclusivity invariant ("no two active components share
(resource_id, path)") is NOT SQL-enforced — JSONB unique constraints
on inner arrays aren't practical. Enforced socially via resolver
review during split. Future admin-UI drift panel will surface
overlaps.

### Graph viz (Phase 3.5 extended)

- `/api/graph` returns `source_slice` per node.
- Frontend hover popup renders a "Source slice" section beneath the
  markdown doc: one block per `resource_id` with plane-coloured
  header + typed sub-rows (paths/files/manifests/workflows/etc.).
- Cache-bust `?v=16`.

### Tests (12 new)

- `tests/mcp_tools/test_source_slice.py` (7): set on create, replace
  on update, COALESCE on omit, nullable default, non-dict refused,
  multi-resource shape, get_component round-trip.
- `tests/admin_ui/test_graph_endpoint.py` extended: `source_slice`
  flows through the graph API including null-case.
- Plus `tests/test_agent_types.py` exercises the SME prompt string
  formatting (caught a brace-escape bug in the updated JSON example).

265 tests total passing.

### Caught-while-writing

The SME prompt's JSON example used single braces `{...}` which
Python's `.format()` call in `build_config` treats as placeholders —
caused a `KeyError` on SME spawn. Fixed by doubling braces in the
literal JSON example. Lesson: any `{` / `}` inside a
`.format()`-targeted string literal must be `{{` / `}}`.

---

## Phase 3.9: Edge Protocol — Catalog + Bindings + Flows ✅

Shipped across six commits (`2ae4a0d` schema → `c9072ff` edge writes →
`06360a8` flows + categorised reads → `664d307` SME prompt →
`1addd63` tests → docs sync). 26 new tests, 7 new MCP tools (1
shimmed), 0 data-loss migrations.

Asymmetric edge model that lets a component publish what it exposes
(its catalog), lets callers bind to specific catalog entries, and
links incoming↔outgoing edges inside a component via a flow table.
This is the data layer; viz upgrades that consume it ship in 3.10.

### Motivation

Current `edges` table is symmetric: one row per `(source, target,
edge_type, identifier)`. Works for "A calls B at GET /x" as a single
fact. Doesn't represent:

- **Catalog of exposed endpoints / consumed topics on the callee side**
  before any caller binds to them. Today there's no way for a callee
  SME to declare "I expose POST /charge" except as part of an existing
  edge — which doesn't exist until a caller arrives.
- **Dangling outgoing on the caller side** when the target component
  isn't pinned yet. Today modelled awkwardly through `unresolved`,
  but `unresolved` is for "I see a string, no idea what it is" — a
  different problem from "I know I'm calling SOMETHING via this
  endpoint, target component just not in our graph yet."
- **Flow inside a component**: which incoming endpoint triggers which
  downstream calls. Required for blast-radius ("if I break DB X,
  which endpoints degrade?") and impact analysis ("if I change endpoint
  Y, what propagates?").

### Concepts

**Catalog row** — `from_component_id IS NULL`, `to_component_id =
callee`. The callee's SME publishes this to declare "I expose this
endpoint / I consume this topic / I accept this query". Only the
callee's SME owns and modifies it.

**Bound row** — `from_component_id = caller`, `to_component_id =
callee`. The caller writes this to record "I call callee at this
identifier". Only the caller's SME owns and modifies it. Filling `to`
on a previously-dangling row does NOT transfer ownership; it stays
with `from`.

**Dangling outgoing** — `from_component_id = caller`, `to_component_id
IS NULL`. Caller knows they're calling something at this identifier
but the target component isn't pinned in the graph yet. Owned by
caller. Resolved later via `bind_edge` once the target component is
identified.

**Flow row** — `(component_id, incoming_edge_id, outgoing_edge_id)`.
Inside a single component owned by one SME, declares "when this
incoming edge fires, this outgoing edge is one of the things that
fires downstream." Set-based, not sequenced — multiple flow rows for
one incoming = fan-out. Owned by the component's SME.

**Multi-source = multi-plane on one SME**, NOT multi-SME. The single
SME that owns a component may discover the same edge from multiple
planes its component spans (e.g. github code + telemetry traces +
deploy config). All sources accumulate into ONE edge row's `metadata`
+ `confidence`. Edges are deliberately NOT per-plane (unlike
attributions, which are).

**Soft protocol**. Nothing in SQL prevents a caller from writing
`from=caller, to=callee, identifier=X` even when no matching catalog
row exists on the callee. The prompt nudges callers to dangle +
clarify in that case. Callees run a hygiene pass on every wake,
spot illegal binds + missing catalog rows, and open clarifications
back to the affected caller. Convergence happens through prompts +
clarifications, never SQL blocks.

### Schema changes

`edges` table:

```sql
ALTER TABLE edges RENAME COLUMN source_id TO from_component_id;
ALTER TABLE edges RENAME COLUMN target_id TO to_component_id;
ALTER TABLE edges ALTER COLUMN from_component_id DROP NOT NULL;
ALTER TABLE edges ALTER COLUMN to_component_id DROP NOT NULL;

-- At least one endpoint must be set
ALTER TABLE edges ADD CONSTRAINT edges_at_least_one_endpoint
  CHECK (from_component_id IS NOT NULL OR to_component_id IS NOT NULL);

-- No self-loop when both are set
ALTER TABLE edges ADD CONSTRAINT edges_no_self_loop
  CHECK (from_component_id IS NULL
         OR to_component_id IS NULL
         OR from_component_id <> to_component_id);

-- Replace the global UNIQUE with three partial uniques
DROP CONSTRAINT IF EXISTS edges_source_id_target_id_edge_type_identifier_key;

CREATE UNIQUE INDEX edges_bound_unique
  ON edges (from_component_id, to_component_id, edge_type, identifier)
  WHERE from_component_id IS NOT NULL AND to_component_id IS NOT NULL;

CREATE UNIQUE INDEX edges_catalog_unique
  ON edges (to_component_id, edge_type, identifier)
  WHERE from_component_id IS NULL;

CREATE UNIQUE INDEX edges_dangling_unique
  ON edges (from_component_id, edge_type, identifier)
  WHERE to_component_id IS NULL;
```

Migration is a rename + nullability flip + index swap — no row data
moves, no embeddings invalidate. Existing rows (all currently bound)
satisfy `edges_bound_unique`. Idempotent: ALTER ... IF EXISTS / NOT
EXISTS, DROP CONSTRAINT IF EXISTS, CREATE INDEX IF NOT EXISTS.

`flows` table (new):

```sql
CREATE TABLE flows (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  component_id UUID NOT NULL REFERENCES components(id) ON DELETE CASCADE,
  incoming_edge_id UUID NOT NULL REFERENCES edges(id) ON DELETE CASCADE,
  outgoing_edge_id UUID NOT NULL REFERENCES edges(id) ON DELETE CASCADE,
  confidence FLOAT NOT NULL DEFAULT 1.0,
  metadata JSONB NOT NULL DEFAULT '{}',
  discovered_by TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (component_id, incoming_edge_id, outgoing_edge_id)
);

CREATE INDEX idx_flows_component ON flows(component_id);
CREATE INDEX idx_flows_incoming ON flows(incoming_edge_id);
CREATE INDEX idx_flows_outgoing ON flows(outgoing_edge_id);
```

Tool-level invariants on flow writes (validated in Python, not in
SQL — keeps DB simple, error messages clearer):
- `incoming_edge.to_component_id = component_id`
- `outgoing_edge.from_component_id = component_id`
- caller (agent_id) owns `component_id` via RCA

### MCP tools

Writes (4 new, 1 deprecated):

```
upsert_edge_catalog(agent_id, edge_data)
  Callee declares an exposed endpoint / consumed topic / accepted query.
  edge_data: {to_component_id, edge_type, identifier, metadata?, confidence?}
  Inserts/updates a row with from_component_id=NULL.
  Scope: caller must own to_component_id via RCA.
  ON CONFLICT (to, type, identifier WHERE from IS NULL) DO UPDATE
  with metadata merge + confidence accumulation.

upsert_edge_outbound(agent_id, edge_data)
  Caller declares an outgoing edge.
  edge_data: {from_component_id, to_component_id?, edge_type, identifier, ...}
  to_component_id may be NULL (dangling outgoing) or set (bound).
  Scope: caller must own from_component_id.
  ON CONFLICT routed by which partial index matches:
    - bound (from, to, type, identifier)
    - dangling (from, type, identifier WHERE to IS NULL)

bind_edge(agent_id, edge_id, to_component_id)
  Resolves a dangling outgoing by setting to_component_id.
  Scope: caller must own the edge's from_component_id.
  Refuses with clear error if the resulting (from, to, type, identifier)
  collides with an existing bound row — caller can choose to merge
  metadata into the existing row + delete the dangling, or rename the
  dangling identifier. No silent merge.

upsert_flow(agent_id, component_id, incoming_edge_id, outgoing_edge_id, metadata?, confidence?)
  Links one incoming edge to one outgoing edge inside the SME's
  component. Validates ownership + edge endpoints match component_id.
  ON CONFLICT (component_id, incoming, outgoing) DO UPDATE
  with metadata merge + confidence accumulation.
```

Deprecated (kept as a backward-compat shim that dispatches to
`upsert_edge_outbound` so existing callers don't break):

```
create_edge(agent_id, edge_data)
  → upsert_edge_outbound(agent_id, edge_data)
  Logs a deprecation warning. Removed in a future phase once SME
  prompts have transitioned.
```

Reads (3 new):

```
get_component_edges(agent_id, component_id) -> dict
  All-agent read. Returns categorised buckets:
    incoming_bound: edges where to=component_id and from IS NOT NULL
    incoming_catalog: edges where to=component_id and from IS NULL (my catalog)
    outgoing_bound: edges where from=component_id and to IS NOT NULL
    outgoing_dangling: edges where from=component_id and to IS NULL
  Replaces the existing get_edges() which returned just {outbound,
  inbound}. Old shape kept as backward-compat by collapsing
  outgoing_bound + outgoing_dangling = outbound, incoming_bound +
  incoming_catalog = inbound.

get_flow(component_id, incoming_edge_id) -> list[edge]
  Set of outgoing edges triggered by this incoming edge inside the
  component. All-agent read.

get_flow_inverse(component_id, outgoing_edge_id) -> list[edge]
  Set of incoming edges that trigger this outgoing edge. All-agent
  read.
```

Existing `get_edges(component_id)` keeps working (backward-compat
collapse) but is documented as deprecated; new callers should use
`get_component_edges` for the richer view.

### SME prompt updates

Three sections touched in `agent_management/agent_types/sme.py` (and
mirrored in `docs/AGENT-PROMPTS.md`):

**Materialisation Step 2 (attributions section)** — add:
- "If your component_type is application/lambda/external-service,
  declare your catalog: every endpoint you expose, every topic you
  consume, every queue you accept from. Use upsert_edge_catalog with
  to_component_id=YOURS, from_component_id=NULL. Identifier =
  endpoint path / topic name / queue name. metadata.description =
  human-readable summary."
- "Database / cache / queue / object-store components don't have
  catalogs (they accept arbitrary queries / writes / publishes).
  Skip catalog declaration for those types."

**Materialisation Step 3 (outbound refs section)** — extend the cosine
ladder with catalog awareness:
- "Before binding, check the target's catalog: get_component_edges
  (target_id) → incoming_catalog. If a row matches your intended
  identifier, your bind is the natural happy path."
- "If the target has a catalog but no matching row: you have two
  choices. Recommended: write a dangling outgoing
  (upsert_edge_outbound with to=NULL) + create_clarification to the
  callee asking them to add the catalog row + clarify. After they
  respond, bind_edge to set to=callee."
- "If the target has no catalog at all (db/cache/queue/etc.):
  upsert_edge_outbound with to=callee directly. Free-form."
- "If you have an identifier but cannot identify ANY target component
  (vector_search returned no useful match): write a dangling outgoing
  with to=NULL + insert_unresolved with the identifier. Both are fine."

**Edge Discovery phase (existing section)** — replace symmetric edge
guidance with directional + flow guidance:
- "Resolve outbound calls into bound edges (or dangling + clarify)
  per the materialisation Step 3 ladder."
- "Record flows: for each of YOUR component's incoming edges (rows
  where to=YOURS), declare which of YOUR outgoing edges fire as a
  result. upsert_flow(yours, incoming_id, outgoing_id) per link.
  Multiple flow rows per incoming = fan-out, that's normal."
- "Bidirectional validation now runs through catalog: when you bind
  outbound at identifier X, check target's catalog has a row at X.
  If not, raise clarification."

**Hygiene cycle (new section in the prompt)** — added to the wake-up
routine:
- "Caller hygiene (every few wakes): re-check your dangling outgoings.
  Run vector_search again — has the target component appeared in the
  graph since you last looked? If yes, bind_edge."
- "Callee hygiene (every few wakes): list incoming_bound edges to
  YOUR component. For each, check whether a matching catalog row
  exists. If a caller has bound to an identifier you don't expose,
  decide:
    - Catalog row should exist (you forgot or it's new behaviour) →
      upsert_edge_catalog to add it.
    - Caller is wrong (typo, deprecated endpoint, hallucinated
      target) → create_clarification to the caller asking them to
      remove or correct the bind.
    - Either way, your routine check converges the data."

### Hygiene scanner (deferred to a later phase, NOT in 3.9)

Optional auto-clarification scanner: any dangling-outgoing or
catalog-mismatched edge older than N cycles → auto-open a
clarification. Out of scope for 3.9; 3.9 ships pure protocol +
prompt-driven hygiene. We add the scanner later if humans-in-the-
loop find prompt-driven hygiene too slow.

### Tests

Unit tests in `tests/mcp_tools/test_edges.py` (new) and
`tests/mcp_tools/test_flows.py` (new):

Edges:
- Catalog write + ownership refusal (caller can't write a catalog
  row for a component they don't own).
- Outbound write + ownership refusal.
- Dangling outbound write (to=NULL) + uniqueness on partial index.
- Bound edge write + idempotent ON CONFLICT update with metadata
  accumulation.
- bind_edge happy path: dangling → bound, ownership preserved.
- bind_edge collision refusal when bound row already exists for
  same (from, to, type, identifier).
- Catalog vs bound vs dangling reads via get_component_edges
  return correctly categorised buckets.
- Backward compat: legacy create_edge call dispatches and writes a
  bound row. get_edges() returns the collapsed shape.
- Migration: existing edges retain content under renamed columns;
  count parity verified pre/post.

Flows:
- Upsert + ownership refusal.
- Validation: incoming.to must equal component_id; outgoing.from
  must equal component_id.
- get_flow + get_flow_inverse return the expected sets.
- Cascade: deleting a component cascades flows; deleting an edge
  cascades flow rows referencing it.

End-to-end:
- Two SMEs (caller + callee). Callee declares catalog. Caller binds
  via vector_search match. Hygiene check on callee finds no
  mismatches. Caller updates flow. get_flow returns the set.

### Docs to update

- `docs/SCHEMA.md`: edges table redesign + flows table.
- `docs/TRIGGER-MANAGEMENT.md`: tool contracts for the 7 new + 1
  deprecated tools.
- `docs/HLD.md`: tool matrix rows for catalog / bind / flow tools.
- `docs/AGENT-PROMPTS.md`: SME materialisation / edge discovery /
  hygiene sections.
- `docs/IMPLEMENTATION-PHASES.md`: mark 3.9 shipped, status row.

### Operational

- Migration runs idempotently on MCP server boot.
- Zero data migration — pure schema rename + nullability flip + index
  swap.
- Restart MCP server (loads new tools + runs migration), agent_manager
  (loads new SME prompt), admin_ui (no change but harmless to refresh).
- Existing `create_edge` callers continue to work via the shim.

### Out of scope (deferred to 3.10)

All graph-viz changes consuming this data: edge styling, hover
zones, light-of-sight BFS. These ship after the data is populated
and the protocol is exercised by real SMEs, so the viz design can
respond to actual data shapes rather than guesses.

---

## Phase 3.10: Edge Graph Viz — Hovers + Light-of-Sight ✅ (depends on 3.9)

Shipped initially in two commits; iterated many times since on
visual tuning, hover semantics, LOS behaviour, node rendering,
junction pinning, cursor zoom. Final live behaviour as of compact:

### What's live

**Backend:** `GET /api/graph` returns `{nodes, edges, flows}` with
per-edge `kind` (bound/catalog/dangling). Bound-edge JOIN filters
decommissioned components.

**Node rendering (per-type geometry):** `makeNodeMesh` returns a
`THREE.MeshLambertMaterial` mesh shaped per `component_type`:
sphere (application), cylinder (database), torus (cache), cone
(queue), octahedron (lambda), icosahedron (cron), tetrahedron
(external-service), cube (library), flat box (infrastructure),
tiny tetrahedron (junction). Scene ambient 0.9 + directional
key 0.7 + fill 0.35 for bright Lambert illumination.

**Palette:** Tailwind 600-range jewel tones — teal / blue / pink /
purple / orange; slate-800 fallback for unattributed.

**Edge bundling:** when N≥3 bound edges share `(target, edge_type,
identifier)`, a virtual junction node is inserted. Callers route
through the junction. Junction is PINNED each tick (`onEngineTick`)
at `target_pos + unit_vector_to_callers_centroid × 14` so it
always sits close to the target, not mid-line. Junction is slate
and tiny so it reads as scaffolding.

**Layout forces:** charge strength `-180`, link distances
55 / 40 / 12 (regular / junction-in / junction-out), soft radial
containment at r=110 applied in `onEngineTick` to prevent runaway
stretch. Library default center force untouched.

**Cursor-centric zoom:** manual wheel handler (library's
`enableZoom = false`). Exponential factor `exp(deltaY * 0.0015)`
clamped to [0.94, 1.06] per-event. Raycasts cursor to a point at
current orbit-target distance; scales camera offset around that
point. Orbit target stays stable (no lerp drift).

**Hover zones (3 zones):**
- `caller` — bound-edge first half OR junction-in segment body.
  Glows caller's incoming edges where `flow.outgoing = this_edge`.
- `target` — bound-edge second half OR junction-out body.
  Glows callee's outgoing edges where `flow.incoming` ∈
  `effectiveFlowIncomingsForEdge(this_edge)` (catalog-bridged).
- `convergence` — tiny zone at the junction itself (last 15% of
  junction-in OR first 15% of junction-out). Glows all edges
  converging at that junction.
Visual refresh uses fresh arrow functions on every call
(`linkColor(l => resolveLinkColor(l))`) because the library caches
accessor results by identity.

**Light-of-sight (strict forward BFS, layered):**
- Seed: clicked edge (+ bundle siblings if bundled).
- Each layer: from the DESTINATION of every edge in the current
  layer, find flows on that destination where the edge is the
  incoming (catalog-bridged). Add those flow outgoings as the
  next layer.
- Visited tracking: `"component|incoming_edge"` pairs AND
  `outgoing_edge` ids — prevents cycles + duplicate emission.
- 220 ms stagger per layer animates the pulse outward.
- Persists until user clicks empty space or another edge.
- Amber color + 4 directional particles for lit state.

**Sidebar tabs on component click:** Doc · Slice · Catalog ·
Bindings in · Bindings out · Flows. Flows grouped by incoming.

**Dangling-edge stubs (shipped post-3.10 polish):**
- Orphan catalog (kind='catalog' with no bound caller matching
  `(target, edge_type, identifier)`) renders as `? → X`: a muted
  stub placeholder on the caller side, real component on the
  target side. Catalogs WITH bound callers stay hidden — their
  bound edge already represents them.
- Outgoing dangling (kind='dangling') renders as `X → ?`: real
  source, stub placeholder on the target side.
- Stub `?` nodes pin at a fixed radial offset outside their
  anchor (20-ish units), so they cluster at the graph boundary.
- Hover the stub side → `"no known caller"` / `"unknown target"`
  tooltip. Hover the real side → standard caller/target flow
  lookup (finds feeders or downstream via flows on the anchor).
- LOS click on an orphan catalog seeds BFS at the real target;
  the chain propagates normally via flows. LOS on an outbound
  dangling lights only itself (no downstream component).

### Known gaps / future polish
- No true bloom post-processing (tried emissive; user rejected
  "water bubble feel"). Current Lambert + bright lighting is the
  compromise.
- Frontend has no test harness yet; changes verified manually.

### Tests
- Backend: `/api/graph` payload shape (kind discriminator + flows
  array) verified in `tests/admin_ui/test_graph_endpoint.py`.
- 292 tests passing total.

### Demo mock data
`src/admin_ui/mock_seed.py` seeds two demo topologies:
1. **Feeds / payments mesh** — 7+ callers converging on payments-svc
   GET /balance (bundle demo), 3 on auth-svc POST /verify.
2. **Chain demo** — p→q→r→s→t→u and x→y→z→s→t→u sharing s/t/u.
   Click any head edge for multi-layer LOS propagation.
Cleanup: `python -m admin_ui.mock_seed cleanup`.

---

## Phase 3.10: Edge Graph Viz — Hovers + Light-of-Sight (ORIGINAL PLAN — superseded by above)

Renders the asymmetric edge model from 3.9 in the 3d-force-graph
view: catalog vs bound vs dangling are visually distinct; hovering
near source / midpoint / target reveals different cuts of the
relationship; clicking does a full BFS through bindings + flows.

### Motivation

Phase 3.5 ships a flat `edges → links` view that loses everything
asymmetric about the model. Once 3.9 lands, the data exists for:

- "Who calls this endpoint?" — midpoint convergence query.
- "What does this endpoint trigger downstream?" — flow-forward.
- "What incoming endpoints fan into this outgoing call?" —
  flow-backward.
- "If I follow this request all the way through, what does it
  touch?" — light-of-sight BFS.

Without the viz, blast-radius / impact-analysis remains a
SQL-and-vector-search affair instead of a click.

### Backend additions

Extend `GET /api/graph` payload:

```jsonc
{
  "nodes": [...],         // unchanged
  "edges": [
    {
      "id": "...",
      "from_component_id": "..." | null,    // null = catalog
      "to_component_id":   "..." | null,    // null = dangling
      "edge_type": "...",
      "identifier": "...",
      "kind": "bound" | "catalog" | "dangling",  // FE convenience
      "confidence": 0.92,
      "metadata": { ... }
    }
  ],
  "flows": [
    {
      "component_id": "...",
      "incoming_edge_id": "...",
      "outgoing_edge_id": "...",
      "confidence": ...
    }
  ]
}
```

Adding `flows` to the payload is the key new piece — viz needs the
incoming↔outgoing links to render line-of-sight. Single query at tab
load (low thousands of components × handful of edges each = bounded);
no pagination. Dangling/catalog rows shown by default; toggle in the
sidebar to hide either.

### 3-zone hover model

Each edge line is divided into thirds:

| Zone | Position on segment | Perspective | Highlight rule |
|---|---|---|---|
| Source-half | First third of line, anchored at source node | Caller-internal | Highlight all incoming edges of SOURCE whose flows include this outgoing edge (i.e. "what incoming requests at the caller fan out through this call"). |
| Midpoint | Middle third of line | Convergence at endpoint | Highlight all OTHER bound edges from any source that share the same (to_component_id, edge_type, identifier) — i.e. "who else calls this endpoint" |
| Target-half | Last third of line, anchored at target node | Callee-internal | Highlight all outgoing edges of TARGET in the flow triggered by this incoming edge (i.e. "what does the callee fire downstream when this endpoint is hit") |

Hover detection: on `onLinkHover`, compute the cursor's normalised
position along the link (0.0 at source → 1.0 at target). 0.0–0.33 =
source-half, 0.33–0.66 = midpoint, 0.66–1.0 = target-half. The
3d-force-graph library exposes link hover events with cursor
position; if normalised position isn't directly available, derive
from event.clientX/Y projected onto the link's midpoint.

Highlighted edges render with a different colour + thickness; node
ends pulse softly.

### Light-of-sight (click)

Clicking an edge triggers a BFS from that edge through the
bindings + flows graph:

1. Start from clicked edge.
2. If clicked edge is bound (from, to both set):
   - Forward: enqueue all flows where outgoing_edge_id = clicked. For
     each flow's incoming_edge_id, recurse.
   - Backward: enqueue all flows where incoming_edge_id = clicked. For
     each flow's outgoing_edge_id, recurse.
3. Mark every visited edge with a "lit" colour for ~3-5 seconds
   (or until the user clicks elsewhere / hits Esc).
4. Animation: light pulse propagates along each lit edge in the
   direction of the call (linkDirectionalParticles for the lit set).

Cap BFS depth at ~10 to avoid pathological graphs; show a "depth
limit reached" indicator if reached.

### Edge styling

| Kind | Colour | Style |
|---|---|---|
| Bound (from + to set) | White ~80% opacity | Solid |
| Catalog (from = NULL) | Plane colour of `to`, ~50% opacity | Dashed, anchored at the callee, free end floats outward as a stub |
| Dangling outgoing (to = NULL) | Caller's plane colour, ~50% opacity | Dashed, free end floats outward |
| Highlighted (hover or BFS) | Lit colour (white 100%) | Solid + thicker + directional particles |

Sidebar toggles:
- "Show catalog stubs" (default on)
- "Show dangling outgoings" (default on)
- "Group by endpoint" — when ≥ N callers bind to the same catalog
  row, bundle their lines visually into one fat line that splits at
  the midpoint. (Optional polish; ship later if 3.10 grows.)

### Sidebar component panel — additions

When a node is selected (existing hover/click behaviour), expand the
sidebar to show a tabbed view:

- **Doc** — existing component_doc_md render.
- **Slice** — existing source_slice render (Phase 3.8).
- **Catalog** — list of `incoming_catalog` rows owned by this
  component. Each row: edge_type, identifier, who-calls-it count.
- **Bindings (in)** — list of `incoming_bound` rows. Each: caller
  name, edge_type, identifier.
- **Bindings (out)** — list of `outgoing_bound` + `outgoing_dangling`.
  Dangling shown with a "??" target icon.
- **Flows** — list of (incoming, outgoing) pairs, grouped by
  incoming for fan-out display.

### Tests

Frontend has no unit-test infra today, so Phase 3.10 testing is
backend + manual:

Backend (`tests/admin_ui/test_graph_endpoint.py` extended):
- /api/graph payload contains `kind` per edge correctly.
- Catalog rows surface in the response.
- Dangling rows surface in the response.
- Flows array populated.

Manual smoke test (documented in IMPLEMENTATION-PHASES.md):
- Spin up a 3-component test fixture with a catalog row, two bound
  rows binding to it, one dangling. Verify each renders correctly.
- Hover each zone, confirm correct highlight set.
- Click for light-of-sight, confirm propagation.
- Toggle catalog/dangling visibility.

Cache-bust `?v=17`.

### Docs to update

- `docs/HLD.md` §10 (admin UI): describe the 3-zone hover + LOS
  click + edge styling.
- `docs/IMPLEMENTATION-PHASES.md`: mark 3.10 shipped.

### Operational

- Restart admin_ui only (frontend bundle + /api/graph schema change).
- No DB migration.
- No agent prompt changes.

### Open questions to resolve during 3.10 implementation

These are small enough to defer to ship-time:

- Exact colour palette per kind (today's plane colours work for
  catalog/dangling tinting; need to pick a "lit" colour — white-yellow
  vs pure white).
- BFS depth cap (10 seems right; revisit on real data).
- Whether to bundle convergent edges visually or keep them separate
  for clarity even at high fan-in (ship without bundling first; add
  if it gets noisy).

---

## Phase 4: Mutation + Resolution + Edges

Executing approved merges/splits, resolving references, building the edge graph.

### MCP Tools Added
**Act — Mutation (gated: only when agent is mutation_assigned_to on consolidation in state M):**
- `execute_mutation(agent_id, consolidation_id, new_status)` — Validate: agent is mutation_assigned_to. Transition: M→MD.
- `complete_consolidation(agent_id, consolidation_id)` — Transition: MD→D.
- `absorb_agent(agent_id, target_agent_id)` — MERGE:
  - Create proxy_items entries for ALL of target's pending items (tasks WHERE worker_agent_id=target AND status IN ('BW','BO'), consolidations WHERE (agent_a_id=target OR agent_b_id=target) AND status NOT IN ('D','F'), clarifications WHERE (asker=target OR responder=target) AND status NOT IN ('CC'), unacked chats WHERE to_agent=target AND acked_at IS NULL, unacked broadcasts for target)
  - SET target agent status='decommissioned'
  - Validate: agent is mutation_assigned_to on an active consolidation in state M
- `spawn_child_agent(parent_agent_id, consolidation_id, component_data, briefing)` — SPLIT:
  - INSERT new component with split_from_component_id=parent's component, split_briefing=briefing
  - INSERT new agent in agent_runs (status='idle')
  - INSERT resource_component_agents row for new agent+component
  - SET consolidation.child_agent_id = new agent (prevent duplicate spawns)
  - Validate: agent is mutation_assigned_to; consolidation.child_agent_id IS NULL
  - Auto-embed new component
- `transfer_attributions(from_component_id, to_component_id, attribution_ids[])` — UPDATE attributions SET component_id=to WHERE id IN (...). Re-embed BOTH components. Validate: agent owns from_component. Does NOT auto-touch `source_slice` — attributions are evidence, slice is structural. The mutation_assigned_to agent must call `upsert_component` explicitly on both sides to keep `source_slice` consistent.
- `get_proxy_items(agent_id)` — SELECT * FROM proxy_items WHERE surviving_agent_id=agent_id. Returns all inherited items with type, item_id, status.
- `get_proxy_chats(agent_id, proxy_agent_id, page, limit)` — SELECT from communications WHERE (from_agent=proxy_agent_id OR to_agent=proxy_agent_id) AND type='chat', paginated. Validate: proxy_agent_id is a decommissioned agent absorbed by agent_id (check proxy_items).

### `source_slice` consistency contract (Phase 3.8 → implemented in Phase 4)

`components.source_slice` is a structural JSONB field describing which
parts of which source resource(s) a component covers. Three mutation
paths touch it — they MUST keep the invariant "no two active components
claim the same (resource_id, path) pair":

- **`spawn_child_agent` (split)** — inside the same transaction:
  - Copy the child's share out of the parent's `source_slice` into the
    new component's `source_slice`.
  - Remove those entries from the parent's `source_slice`.
  - Callers pass the child's intended slice in `component_data.source_slice`;
    the mutation call must also accept (or compute) the updated parent
    slice and apply both writes atomically.
- **`absorb_agent` (merge)** — in the mutation transaction:
  - MERGE target's `source_slice` into the surviving component's.
  - Per `resource_id` key: union the inner arrays (dedup preserving
    order of first appearance).
  - Decommissioned target's `source_slice` stays frozen as a tombstone
    (not deleted).
- **`transfer_attributions`** — does NOT auto-touch `source_slice`.
  Deliberately decoupled: attributions can move independently of slice
  (e.g. a hostname attribution moves without changing which paths the
  component owns). The SME calling `transfer_attributions` is responsible
  for a follow-up `upsert_component` on both sides if slice changes too.

Social invariants (NOT SQL-enforced, caught at resolver-review time):
- Exclusivity: no two active components share (resource_id, path).
- Totality: union of all active components' slices for a given
  resource_id should cover the parts of the resource SMEs intend to
  represent. Uncovered parts indicate missing components; overlapping
  parts indicate a missed consolidation.
- Phase 4 resolver review for splits should run a lightweight check
  via the admin-UI drift panel (future work) or by eyeballing the
  consolidation thread.

### Trigger Manager Additions
- Scan consolidations for mutation: `WHERE mutation_assigned_to=agent_id AND status='M'`
- Scan consolidations for MD verification: `WHERE status='MD'` → triggers resolver
- Scan proxy_items: `WHERE surviving_agent_id=agent_id AND status='pending'` → include in action items for surviving agent

### Proxy Item Triage Rules (enforced via system prompt, not code)
- Tasks: review in context, close permanently if irrelevant or reopen under own agent_id
- Chats: respond to pending admin messages
- Broadcasts: ack inherited unacked broadcasts
- Consolidations: continue as yourself or close if merge made them irrelevant
- Agent reads proxy chats via get_proxy_chats() for context before triaging

### Resolution (no new tools — uses existing Phase 2 tools)
- Config SMEs use `upsert_attribution` to register resolved hostnames
- All SMEs use `resolve_reference` to mark unresolved as resolved + create edge
- re-scan unresolved table: agents use `get_unresolved` + `vector_search` + `resolve_reference`

### Edge Discovery (no new tools — uses existing Phase 2 tools)
- SMEs use `create_edge` with identifier, source_attr_id, target_attr_id
- One edge per specific API call / query (UNIQUE on source_id, target_id, edge_type, identifier)
- Bidirectional validation: check if target has matching endpoint attribution (enforced via system prompt)

### Tables Activated
- proxy_items, broadcast_acks (broadcast_acks used by absorb_agent to find unacked broadcasts)

---

## Validation Rules (enforced in ALL phases)

### Universal
- Cannot respond without a state change (consolidation, task, clarification)
- Cannot update another agent's component (checked via resource_component_agents)
- State transitions must follow defined state machines
- Agent must be a participant (owner/worker, asker/responder, agent_a/agent_b)
- agent_id validated on every MCP call — reject if mismatch

### Per-type
- Consolidation: system auto-transitions B1/B2→R when both conf > 0.85 AND r_conf IS NULL. Manual escalate to R only if r_conf IS NOT NULL.
- Task: only orchestrator/admin can create. Worker cannot close (only owner → TC).
- Chat: agents initially only message admin. Admin can message any agent. Ack is selective by IDs.
- Broadcast: only orchestrator/admin can broadcast. Ack is per-broadcast per-agent.

---

## File Structure (actual — as of 2026-04-20)

```
cartograph/
  docker-compose.yml          — Postgres + pgvector (pg16)
  requirements.txt            — psycopg[binary], psycopg-pool, fastapi, uvicorn, mcp>=1.0, pyyaml
  pyproject.toml              — pythonpath=["src"]
  docs/                       — Design docs
  src/
    main.py                   — entry point (SIGHUP ignore, startup state reset)
    mcp_servers.yaml          — registry of plane-reader MCP servers

    shared/
      config.py               — env-based config (DB, embedding, timeouts, thresholds)
      db.py                   — psycopg3 ConnectionPool; execute/_one/_mutate/_returning
      migrations.py           — idempotent CREATE TABLE for all 14 tables + indexes

    trigger_management/
      scanners/{chats,broadcasts,tasks,consolidations,clarifications,auto_transitions}.py
      trigger_loop.py         — idle-agent scan → prioritise → trigger_lock=TRUE
      main.py

    cartograph_mcp/
      server.py               — FastMCP("cartograph-db") on :8100, 26 @mcp.tool registrations
      tools/
        action_items.py       — Phase 0: summary + detail
        chat.py               — Phase 0: send/ack/unacked/history
        broadcast.py          — Phase 0: send/ack/unacked
        tasks.py              — Phase 1: create/respond/raise_blocker/my/thread
        secrets.py            — Phase 1: put/get/list/delete
        resources.py          — Phase 1: upsert/get/list/counts/mark_done

    agent_management/
      agent_manager.py        — workspace + .mcp.json + subprocess spawn + heartbeat
      invoke_loop.py          — polls trigger_lock=TRUE, invokes claude -p
      db.py                   — pickup/heartbeat/status helpers
      agent_types/{orchestrator,iterator,sme,resolver,base}.py

    admin_ui/
      server.py               — FastAPI :8200 (5 endpoints)
      static/{index.html, app.js, style.css}

  tests/
    admin_ui/                 — 17 tests (+ conftest.py with test-DB isolation)
    mcp_tools/                — 44 tests (tasks: 27, resources: 17, + conftest.py)
    test_{db, agent_manager, agent_types, trigger_manager, integration, main}.py
```

---

## Deferred to Phase 2+

- `embedding.py` / `vector_search` tool — not built yet (needed for materialisation lookups).
- Components/attributions/edges/unresolved/consolidations/clarifications/mutations tool modules — not built.
- Auto-spawn scanner for SMEs from pending resources — not built (orchestrator currently spawns via `create_agent`).

# Cartograph — Implementation Phases

**Status (2026-04-29):** Phases 0 → 8 all ✅ except Phase 6 (Globe — parked on `feat/globe-experimental`). **Token optimisation Rounds 1, 2, 3 ALL SHIPPED 2026-04-29.** **Phase 8 (Token Optimisation + Gap Closings) ✅ SHIPPED 2026-04-29** — 7 sub-batches across 7 commits closing the 4-row-type corrective delete surface, finishing Round-4 bulks, eliminating the `notify.py` PostToolUse race, making `insert_unresolved` idempotent. Tool count 89 → 107 (+18).

Most recent (2026-04-26 → 2026-04-29):
- 7.4 (catalogs first-class), 7.4.2 (flows reference catalogs), 7.4.3/4/5/6 (DEMO7-round-1 fixes + doc syncs).
- 7.4.7 (per-type model + admin UI plane source from RCA→resources + agent-row plane symbols + workspace-as-memory + code-repo clone-mandatory + §2.8 mass prompt infusion).
- 7.4.8 (WebGL GPU memory leak fix v1 — geometry/material caches + `/api/clientlog` + server log piping).
- 7.4.9 (Communications tab — surface decommissioned agents + plane symbols on rows + component-name filter).
- 7.4.10 (Graph crash root-cause fix v2 — pause RAF when tab hidden + visibilitychange + wheel null-deref + idempotent ctx-lost).
- **7.4.11** (`delete_edge` MCP tool + SME identifier normalisation rule + post-merge EDGE DEDUP step) — commits `72b4a93`, `84b0941`, `bc69c23`.
- **Parallel tool calls** (commits `5b3144d`, `c7f5fd5`) — removed "Call tools sequentially" prompt rule, shipped `== BATCH + PARALLEL TOOL CALLS ==` block in shared mission. Targets the 0.08% pre-fix parallel-tool-call rate.

- **89 MCP tools** registered (Phase 7.4.11 added `delete_edge`; Phase 7.4.12 added 3 bulk write variants). Verify with `grep "tools registered" /tmp/cartograph-logs/mcp.log`.
- Services running under the active Claude Code session: Postgres (docker), trigger_management.main (logs → `/tmp/cartograph-logs/triggers.log`), cartograph_mcp.server :8100 (`mcp.log`), admin_ui.server :8200 (`admin_ui.log`), agent_management `main` (`agents.log`), browser-side errors (`browser.log` via `/api/clientlog`). 8 concurrent lane workers (1 orch + 2 iter + 1 res + 4 sme) + stale watchdog.
- See `docs/PROMPT-ENHANCEMENTS.md` for the active prompt-quality backlog (§2 shipped, §3 open gaps, §4 operational nudges + broadcast log + chat log).

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

**Edge bundling:** when N≥2 bound edges share `(target, edge_type,
identifier)`, a virtual junction node is inserted. Callers route
through the junction. Junction is PINNED each tick (`onEngineTick`)
at `target_pos + unit_vector_to_callers_centroid × 14` so it
always sits close to the target, not mid-line. Junction is slate
and tiny so it reads as scaffolding. Singletons stay as a straight
edge; N≥2 always bundles so pairs also get a visible convergence
point.

**Layout forces:** charge strength `-180`, link distances
55 / 40 / 12 / 22 (regular / junction-in / junction-out / stub),
soft radial containment at r=110 applied in `onEngineTick` to
prevent runaway stretch. Library default center force untouched.

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
Catalog rows show their actual bound-caller list (e.g.
"7 caller(s): feeds-api, kyc-svc, …") when bindings exist; only
genuine orphans render as "exposed — no caller bound yet".

**Dangling-edge stubs (shipped post-3.10 polish):**
- Orphan catalog (kind='catalog' with no bound caller matching
  `(target, edge_type, identifier)`) renders as `? → X`: a muted
  stub placeholder on the caller side, real component on the
  target side. Catalogs WITH bound callers stay hidden — their
  bound edge already represents them.
- Outgoing dangling (kind='dangling') renders as `X → ?`: real
  source, stub placeholder on the target side.
- Stub `?` nodes pin at a FROZEN per-stub offset vector from
  their anchor. Siblings of one anchor fan around a small tilted
  ring (distinct theta per sibling), so multiple orphan catalogs
  or danglings on the same component (e.g. payments `POST /charge`
  + `POST /refund`) don't stack on top of each other. Offsets are
  computed once at transform time and never re-derived from anchor
  position — anchor motion doesn't rotate stub direction, so there
  are no sibling-stub collision pulses into the cluster.
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
`src/admin_ui/mock_seed.py` seeds three demo topologies plus stubs:
1. **Feeds / payments mesh** — 7+ callers converging on payments-svc
   GET /balance (bundle demo), 3 on auth-svc POST /verify. Several
   orphan catalogs (GET /scores, POST /charge, POST /refund, etc.)
   and outbound danglings (vendor feed, kyc API, slack channel)
   exercise stub rendering.
2. **Chain demo** — p→q→r→s→t→u and x→y→z→s→t→u sharing s/t/u.
   Plus orphan catalog at p (`p.admin_ping`) with a flow to p→q,
   orphan at s (`s.debug`, isolated), dangling at s
   (`metrics.unknown`) wired into s's flow, dangling at u
   (`webhook.unknown`) wired into u's flow — so clicking p's
   inbound stub propagates the full chain including trailing
   danglings.
3. **Fan-out demo** — A/B/C/D with two endpoints each (a1/a2, b1/b2,
   c1/c2, d1/d2). Flows route A.a1→A→B(b1)→B→C(c1) and
   A.a2→A→B(b2)→B→D(d1). Exercises distinct incoming endpoints on
   the same component fanning to different downstream components.
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

## Phase 4: Mutation + Proxy Inheritance

Executing approved merges/splits. Core shift from the original spec:
no `proxy_items` table — inherited items are DERIVED at read time by
walking `agent_runs.merged_into_agent_id` chains. Survivor acts on
inherited items via a single router that invokes existing public
tools in a relaxed-validation ContextVar.

Net surface: 5 mutation tools + 2 proxy tools + 1 shared helper
refactor + 3 schema columns + 1 audit table + 1 notifications
reshape + admin-UI touchpoints.

### 4.0 Schema delta

`agent_runs` — three new nullable columns populated on deactivation:
- `deactivation_reason TEXT` — enum-ish: `'merged'` | `'split_absorbed'` | `'retired'`
- `merged_into_agent_id UUID REFERENCES agent_runs(agent_id)` — single-hop pointer; transitive chains walked at read time (never flattened, so historical `deactivation_notes` stay accurate)
- `deactivation_notes TEXT` — human/agent-authored brief; populated per-hop at the moment of deactivation

New append-only table `proxy_audit`:
```sql
CREATE TABLE proxy_audit (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  survivor_id       UUID NOT NULL REFERENCES agent_runs(agent_id),
  proxy_agent_id    UUID NOT NULL REFERENCES agent_runs(agent_id),
  item_type         TEXT NOT NULL,   -- 'task' | 'consolidation' | 'clarification' | 'chat' | 'broadcast'
  item_id           UUID NOT NULL,
  action            TEXT NOT NULL,   -- 'respond' | 'close' | 'ack' | ...
  payload_summary   JSONB NOT NULL DEFAULT '{}',
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_proxy_audit_survivor ON proxy_audit(survivor_id, created_at DESC);
CREATE INDEX idx_proxy_audit_item     ON proxy_audit(item_type, item_id);
```

`proxy_items` table from the original plan is NOT created. Lifecycle
state lives on the underlying item rows (`tasks.status`,
`communications.acked_at`, etc.); "take over" does not exist.

### 4.1 require_active_agent helper + ContextVar (prerequisite refactor)

Today `status != 'decommissioned'` checks are scattered:
- 4 duplicated `_caller` helpers in `tools/{clarification,consolidation,sleep,components}.py`
- 6 inline SELECTs in `tools/{chat.py:18, notifications.py:44, secrets.py:20,46,64,79}`

All 10 consolidate into `shared.actor_auth.require_active_agent(agent_id) -> dict`:
```python
from contextvars import ContextVar
_PROXY_CTX: ContextVar[dict | None] = ContextVar('proxy_ctx', default=None)

def require_active_agent(agent_id: str) -> dict:
    ctx = _PROXY_CTX.get()
    allow_decommissioned = bool(ctx and ctx.get('proxy_agent_id') == agent_id)
    filter_sql = "" if allow_decommissioned else "AND status != 'decommissioned'"
    row = execute_one(
        f"SELECT agent_id, agent_type, status FROM agent_runs "
        f"WHERE agent_id = %s {filter_sql}",
        (agent_id,),
    )
    if row is None:
        raise ValueError(f"Agent {agent_id} not found")
    return row
```

Rule: any future "actor must be active" check MUST go through this
helper. Documented at the helper's definition. Code review gate.

### 4.2 Mutation MCP tools

Gated on consolidation `status='M' AND mutation_assigned_to=agent_id`:

- `execute_mutation(agent_id, consolidation_id)` — M→MD transition.
- `complete_consolidation(agent_id, consolidation_id)` — MD→D (resolver-gated).
- `absorb_agent(agent_id, target_agent_id, deactivation_reason, deactivation_notes)` — merge:
  - Validate consolidation is in M and agent is assigned.
  - SET target.status='decommissioned', populate the three new `agent_runs` columns in ONE UPDATE.
  - Union source_slice: survivor's slice gets each of target's `(resource_id, paths[])` pairs merged per-key (union of arrays, dedup preserving first appearance).
  - Target's source_slice is NOT deleted — frozen as tombstone evidence.
- `spawn_child_agent(parent_agent_id, consolidation_id, component_data, split_briefing)` — split inside one transaction:
  - INSERT new component (`split_from_component_id=parent_component_id`, slice = child's carve-out from `component_data.source_slice`).
  - UPDATE parent component SET source_slice = parent minus child's share.
  - INSERT idle agent_runs row for new SME.
  - INSERT resource_component_agents row.
  - SET consolidation.child_agent_id to prevent re-spawn.
  - Auto-embed new component.
- `transfer_attributions(agent_id, from_component_id, to_component_id, attribution_ids[])` — attribution move:
  - Validate agent owns from_component.
  - UPDATE attributions SET component_id=to WHERE id IN (...).
  - Re-embed BOTH components.
  - DOES NOT touch source_slice (intentionally decoupled — caller does follow-up `upsert_component` if slice changes).

### 4.3 Proxy read tool

`get_my_proxy_items(agent_id, limit=100)` — returns inherited work:

```python
# 1. Walk the reverse merged_into chain.
#    Find all agents A where walking A.merged_into_agent_id reaches survivor.
#    Recursive CTE bounded at depth 10.
# 2. For each proxied agent, UNION pending work across:
#    - tasks WHERE worker_agent_id=P AND status IN ('BW','BO')
#    - communications WHERE to_agent=P AND type='chat' AND acked_at IS NULL
#    - consolidations WHERE (agent_a_id=P OR agent_b_id=P) AND status NOT IN ('D','F')
#    - clarifications WHERE (asker_agent_id=P OR responder_agent_id=P) AND status NOT IN ('CC')
#    - broadcasts WHERE id NOT IN broadcast_acks.broadcast_id FOR P
# 3. Enrich each row with the proxy agent's deactivation_reason/notes.
```

Response shape:
```json
{
  "proxied": [{
    "proxy_agent_id": "...",
    "deactivation_reason": "merged",
    "deactivation_notes": "...",
    "merged_into_agent_id": "...",    // may equal survivor or be a hop in chain
    "items": [{"item_type": "task", "item_id": "...", ...}]
  }]
}
```

### 4.4 Proxy act router

`act_on_proxy_item(survivor_id, item_type, item_id, action, payload)` — the
one sanctioned path for a survivor to act as a decommissioned agent.

```python
def act_on_proxy_item(survivor_id, item_type, item_id, action, payload):
    require_active_agent(survivor_id)                       # R1: strict
    proxy_agent_id = _resolve_proxy_chain(                  # R2: who owns this item
        item_type, item_id, survivor_id                     # & is survivor reachable?
    )
    token = _PROXY_CTX.set({                                # R3: enter ctx
        'proxy_agent_id': proxy_agent_id,
        'survivor_id': survivor_id,
    })
    try:
        result = _DISPATCH[(item_type, action)](            # R4: invoke public tool
            proxy_agent_id, item_id, payload                #     with actor=proxy agent
        )
    finally:
        _PROXY_CTX.reset(token)                             # R5: always reset
    _insert_proxy_audit(                                    # R6: audit trail
        survivor_id, proxy_agent_id,
        item_type, item_id, action, payload,
    )
    return result
```

Dispatch map (one entry per public tool we allow via proxy):
```python
_DISPATCH = {
  ('task',          'respond'): respond_task,
  ('task',          'close'):   close_task,
  ('consolidation', 'respond'): respond_consolidation,
  ('clarification', 'respond'): respond_clarification,
  ('chat',          'ack'):     ack_chats,
  ('chat',          'send'):    send_chat,
  ('broadcast',     'ack'):     ack_broadcast,
}
```

`_resolve_proxy_chain(item_type, item_id, survivor_id)` returns the
item's owner IFF survivor sits on the merged_into chain above the
owner; else raises.

Public tools are unchanged. All ownership/state/content validation
fires exactly as for a direct call because `agent_id=proxy_agent_id`
IS the original owner.

### 4.5 Notifications reshape

`get_notifications` + `get_detailed_notifications` response shape:
```json
{
  "my":       [/* survivor's own notifications */],
  "proxied":  [{
    "proxy_agent_id": "...",
    "deactivation_brief": "merged — <notes>",
    "items": [/* same item shape as get_my_proxy_items */]
  }]
}
```

Not interleaved — agent sees "this is me" vs "legacy to wind down" at a glance.

### 4.6 Trigger manager integration

Three new scan paths:
- `WHERE status='M' AND mutation_assigned_to=agent_id` → queue mutation
- `WHERE status='MD'` → queue resolver for MD→D review
- Fold `get_my_proxy_items` shape into survivor's action-items list so proxies surface on wake

### 4.7 source_slice invariants (unchanged from earlier spec)

Social invariants — NOT SQL-enforced, caught at resolver review:
- Exclusivity: no two active components share `(resource_id, path)`.
- Totality: union of all active slices covers what SMEs intend.
- `spawn_child_agent` carves atomically; `absorb_agent` unions per-resource; `transfer_attributions` stays decoupled.

### 4.8 Admin UI changes

- **Communications tab** — every row authored by a decommissioned agent joins `proxy_audit ON (item_type, item_id)` to display a `via <survivor>` badge with tooltip (deactivation_brief + chain).
- **Agent detail page** — deactivation block (reason + notes + merged_into_agent_id link) for decommissioned agents. "Follow chain" button walks to ultimate survivor.
- **Proxy chains view** — one-page visualization: `A → B → C (active)` lineage per chain with timestamps.
- **Action-items dashboard** — split "my" vs "inherited via proxy" buckets. Inherited items grouped by proxy agent with deactivation header.

### 4.9 Testing matrix

- Schema: idempotent migrations; rollback safe.
- Helper: strict default; ContextVar match permits; mismatch forbids; concurrent proxy calls don't stomp (async/thread).
- Mutation tools: happy paths + wrong actor + invalid state + slice merge correctness + carve-out atomicity + re-embed triggered + source_slice untouched by `transfer_attributions`.
- Proxy read: chain depth 1/2/3; status filters per item type.
- Proxy router: survivor strict; legitimate proxy permitted; non-proxy rejected; ctx reset on success AND exception; audit row inserted; dispatch covers all 7 entries.
- Notifications: split-bucket shape; trigger scanner picks proxy items.
- UI: badges render; deactivation block renders; chain view renders; bucket split works.

### 4.10 Tables Activated
- `proxy_audit` (new).
- `broadcast_acks` (exists) — consumed by `get_my_proxy_items` for unacked broadcasts per proxied agent.
- `proxy_items` (original spec) — NOT created.

### 4.11 Implementation ordering (commit cadence)

Each step → incremental commit, tests alongside code:

1. Schema migrations (three `agent_runs` columns + `proxy_audit`).
2. `require_active_agent` consolidation refactor (standalone-mergeable; groundwork).
3. Mutation tools — `execute_mutation` + `complete_consolidation` (simplest).
4. Mutation tool — `absorb_agent` (populates new columns, union slices).
5. Mutation tool — `spawn_child_agent` (atomic carve-out).
6. Mutation tool — `transfer_attributions`.
7. `get_my_proxy_items` (read side).
8. `act_on_proxy_item` router + dispatch map + audit inserts.
9. Notifications reshape + trigger manager integration.
10. Admin UI: communications badges, deactivation block, proxy chains, bucket split.
11. Final doc + memory sync.

### 4.12 Resolution & Edge Discovery (reused from Phase 2 / 3.9)
- `resolve_reference`, `upsert_attribution` — unchanged.
- Edge discovery uses the `upsert_edge_*` / `bind_edge` / `upsert_flow` surface shipped in 3.9. No new tools.

---

## Phase 4.1: Mutation completeness + demo-uncovered gaps ✅

Ships alongside Phase 4 after the first end-to-end demo (2026-04-24)
surfaced ten real gaps. The demo ran parent SME → split nomination →
resolver approval → spawn_child_agent → execute_mutation successfully,
but revealed edge cases and missing primitives that Phase 4 glossed
over. This phase closes them.

### 4.1.0 Gaps uncovered

| # | Issue | Severity | Lands in |
|---|-------|----------|----------|
| 3 | `get_action_items_summary` pydantic schema mismatch on `proxied` field | blocker (every SME wake) | 4.1.1 |
| 7 | `split_briefing` written but never delivered to spawned child | blocker for splits | 4.1.4 |
| 9 | No `transfer_edges` tool — orphans on merge/split | blocker | 4.1.2 |
| 10 | No `transfer_flows` tool — orphans on merge/split | blocker | 4.1.2 |
| 1 | `spawn_child_agent` silently fails slice carve when `source_slice` lives in `metadata` not top-level column | high | 4.1.4 |
| 2 | Child agent can't discover its own component_id on first wake | high | 4.1.4 (task) + 4.1.7 (tool) |
| 6 | Child inherits parent's resource row with `status='done'` — no signal to re-materialise | medium | 4.1.5 |
| 8 | SME prompt hardcodes "Orchestrator already decided to spawn you" — wrong for split-spawned children | low | 4.1.8 |
| 5 | `nominate_consolidation` has no `metadata` param — caller had to inline demo tag into message text | low | 4.1.6 |
| — | `transfer_attributions` (shipped in 4.0) is under-gated — works without an active mutation | architectural fix | 4.1.2 |

### 4.1.1 Fix `get_action_items_summary` schema

**Problem:** added `proxied: list[dict]` to the response in Phase 4
without updating the MCP tool's return type annotation. Pydantic
serializer couldn't reconcile. Every SME hits it on wake.

**Fix:** adjust the MCP wrapper's response type hint + dict shape so
`proxied` is explicitly `list[dict[str, Any]]`. Keep the internal
helper's shape unchanged.

**Files:** `src/cartograph_mcp/server.py`, `src/cartograph_mcp/tools/action_items.py`
**Tests:** 1 new — summary endpoint returns 200 + schema valid when caller has ≥1 proxied group
**Effort:** XS

### 4.1.2 Mutation-scoped transfer tools + stale hygiene

Rewrite `transfer_attributions` gate and add `transfer_edges`,
`transfer_flows`, `get_stale_edges`, `get_stale_flows`.

**Principle:** ownership transfers are mutation primitives. They ONLY
work when:
1. An active consolidation exists in status='M'.
2. Caller = `consolidation.mutation_assigned_to`.
3. The `from_component_id` and `to_component_id` are within that
   consolidation's declared scope (merge: a↔b pairs; split: parent→child
   via `child_agent_id` lookup).

Shared helper:
```python
def _assert_transfer_scope(agent_id, consolidation_id, from_comp, to_comp):
    cons = _assert_mutation_gate(agent_id, consolidation_id)
    if cons["nomination_type"] == "merge":
        allowed = {(cons["component_a_id"], cons["component_b_id"]),
                   (cons["component_b_id"], cons["component_a_id"])}
    else:  # split
        if not cons["child_agent_id"]:
            raise ValueError("Spawn child first")
        child_comp = _component_of_agent(cons["child_agent_id"])
        allowed = {(cons["component_a_id"], child_comp)}
    if (from_comp, to_comp) not in allowed:
        raise ValueError(f"Transfer ({from_comp} → {to_comp}) outside scope")
    return cons
```

**New / changed tools:**

- `transfer_attributions(agent_id, consolidation_id, attribution_ids, from_component_id, to_component_id)` — **signature change** (adds `consolidation_id`). Re-embed both sides.

- `transfer_edges(agent_id, consolidation_id, edge_ids, direction='both')` — new. `direction` controls which endpoint columns are rewritten:
  - `'from'` — rewrite `from_component_id` only (for bound/dangling owned by caller).
  - `'to'` — rewrite `to_component_id` only (for catalogs).
  - `'both'` — rewrite both (rare; edges that touch both sides).
  - Collision rules:
    - Catalog: collapse on `(to_component_id, edge_type, identifier)` — keep target's existing row, drop source's.
    - Bound/dangling: reject on full-key collision.

- `transfer_flows(agent_id, consolidation_id, flow_ids)` — new. Reject on `(component_id, incoming_edge_id, outgoing_edge_id)` collision.

- `get_stale_edges(agent_id)` — new. Returns edges owned by caller's component where the OTHER endpoint's component is decommissioned. Each row includes `stale_component_merged_into_agent_id` so caller can re-bind to the survivor.

- `get_stale_flows(agent_id)` — new. Returns flows where referenced incoming/outgoing edges point at decommissioned components.

**Hygiene wiring:** SME prompt gets a bullet in the edge-hygiene cycle
telling them to call `get_stale_edges` + `get_stale_flows` on some wakes
and re-bind via survivor pointers.

**Files:** `src/cartograph_mcp/tools/mutation.py` (transfers), `src/cartograph_mcp/tools/components.py` (hygiene reads), `src/cartograph_mcp/server.py`
**Tests:** ~15 new (gate enforcement per-type, direction param on edges, collision rules, stale-edge hygiene surface, retrofit transfer_attributions with new sig)
**Effort:** M

### 4.1.3 `absorb_agent` cascade: auto-transfer body

Adds three flags to `absorb_agent` (all default `True`):

- `cascade_attributions=True` — moves all target's attributions into survivor.
- `cascade_edges=True` — moves all target's from-edges + to-edges into survivor.
- `cascade_flows=True` — moves all target's flows into survivor.

Internally calls `transfer_attributions` / `transfer_edges` /
`transfer_flows` with this consolidation's `consolidation_id`. Survivor
workflow collapses from 5 calls (`transfer_attrs` + `transfer_edges` +
`transfer_flows` + `absorb_agent` + `execute_mutation`) to 2
(`absorb_agent` + `execute_mutation`).

Flags allow the caller to opt out if they want to hand-pick what moves
— useful when survivor has already populated their own edges with
better metadata and wants to keep them.

**Files:** `src/cartograph_mcp/tools/mutation.py`, `src/cartograph_mcp/server.py`
**Tests:** 5 new (all cascades on, each cascade off independently, post-absorb stale hygiene returns empty)
**Effort:** S

### 4.1.4 `spawn_child_agent` hardening

Three fixes bundled:

**(a) Top-level `source_slice` validation.** Read `components.source_slice` column directly. If null/empty, raise:
```
Parent component has no source_slice set at components.source_slice.
Call upsert_component with source_slice={resource_id: {...}} first.
```
This catches the demo-POC gotcha where parent had stashed `source_slice`
inside `metadata`, causing silent carve-out with empty slice.

**(b) Atomic split-carve.** Accept two optional params:
```python
spawn_child_agent(..., transfer_edge_ids: list[str] = None,
                       transfer_flow_ids: list[str] = None)
```
Calls `transfer_edges` + `transfer_flows` in the same transaction using
the consolidation's id. Edges/flows can't be partially moved.

**(c) Welcome task for child.** After spawning the child + setting
`consolidation.child_agent_id`, INSERT a BW task:
```python
create_task(
    owner_agent_id=parent_agent_id,
    worker_agent_id=child_agent_id,
    description=f"[split-welcome] component_id={child_component_id}\n\n{split_briefing}"
)
```
Child wakes with a visible task carrying both their component_id and
the briefing. Solves discoverability (#2) + briefing delivery (#7) in
one step.

**Files:** `src/cartograph_mcp/tools/mutation.py`
**Tests:** 5 new (parent-slice-empty raises, welcome task exists with component_id + briefing in description, transfer_edge_ids param works, transfer_flow_ids param works, task owner is parent)
**Effort:** S

### 4.1.5 `mark_resource_done` idempotency (shared resource model)

Retains shared resource row between parent and split-child. Confirmed
via audit: `resources.status='done'` is not gating — no scanner,
trigger, or tool filters by it (only surfaces in
`get_resource_counts` dashboard). Child inherits same resource_id via
their new RCA row; wakes via the welcome task (4.1.4(c)); materialises
their slice; calls `mark_resource_done` → already done → idempotent
no-op.

**Fixes:** SME prompt — one-line clarification that `mark_resource_done`
is idempotent and should be called when YOUR slice is materialised,
regardless of the resource row's current status.

**Deferred:** per-RCA status column (option b from the design review).
YAGNI until dashboard accuracy becomes a hard requirement.

**Files:** `src/agent_management/agent_types/sme.py`
**Tests:** 1 — `mark_resource_done` called twice on same resource_id returns cleanly on both calls
**Effort:** XS

### 4.1.6 `nominate_consolidation` metadata param

**Migration:** `ALTER TABLE consolidations ADD COLUMN IF NOT EXISTS
metadata JSONB NOT NULL DEFAULT '{}'`.

**Tool:** accepts `metadata: dict | None = None` param, persists to column.

Demo caller hacked around this by inlining `[DEMO / metadata.demo=true]`
into the message text — structured column means demo cleanup can now
filter cleanly: `DELETE FROM consolidations WHERE metadata->>'demo' = 'true'`.

**Files:** `src/shared/migrations.py`, `src/cartograph_mcp/tools/consolidation.py`, `src/cartograph_mcp/server.py`
**Tests:** 2 new (metadata round-trip, default empty dict on omission)
**Effort:** XS

### 4.1.7 `get_my_components` tool

New read tool. SME can enumerate components they own via RCA join.
Critical for split-spawned children before they discover the welcome
task, and for general SME self-inspection.

```python
get_my_components(agent_id) -> list[dict]
  # SELECT c.id, canonical_name, display_name, component_type,
  #        source_slice, split_briefing, split_from_component_id,
  #        status
  # FROM resource_component_agents rca
  # JOIN components c ON c.id = rca.component_id
  # WHERE rca.agent_id = %s AND c.status != 'decommissioned'
```

**Files:** `src/cartograph_mcp/tools/components.py`, `src/cartograph_mcp/server.py`
**Tests:** 3 new (SME with one component, SME with zero components, post-split child returns child component)
**Effort:** XS

### 4.1.8 SME prompt — split-aware + discoverability

Update two lines in `src/agent_management/agent_types/sme.py`:

- Soften "Orchestrator already decided to spawn you on this resource":
  ```
  You were spawned either by the orchestrator (initial
  materialisation) or by a parent SME via split_child_agent (Phase 4
  split consolidation). Either way, the system has already decided you
  own exactly one component.
  ```

- Add a wake-up step:
  ```
  - If this is your first wake and you're unsure which component you
    own, call get_my_components(your_agent_id).
  - If your first action-item has subject "[split-welcome]", read it
    FIRST — it carries your component_id + split_briefing.
  ```

No session reset needed — `agent_manager.py:190` passes `--system-prompt`
fresh on every invocation.

**Files:** `src/agent_management/agent_types/sme.py`
**Tests:** N/A (prompt-only; behavior verified manually on next SME spawn)
**Effort:** XS

### 4.1.9 Doc + memory sync

Update 6 docs for consistency post-4.1:
- **TRIGGER-MANAGEMENT.md** §2 Act — new transfer tool signatures, stale hygiene tools, nominate metadata param.
- **SCHEMA.md** — `consolidations.metadata` JSONB column.
- **HLD.md** — §2 Tools table — add `transfer_edges`, `transfer_flows`, `get_stale_edges`, `get_stale_flows`, `get_my_components`.
- **AGENT-PROMPTS.md** — section on split-child onboarding flow.
- **IMPLEMENTATION-PHASES.md** — mark 4.1 shipped; update test count.
- **ONE-PAGER.md** — no functional impact; verify still accurate.
- **memory/cartograph_state.md** — append 4.1 summary.

**Effort:** S

### 4.1 Ordering + dependencies

```
4.1.1 (pydantic)         ← independent, ship first
   │
4.1.2 (transfer tools + hygiene)
   │
   ├─ 4.1.3 (absorb cascade — uses transfers)
   │
   └─ 4.1.4 (spawn hardening — uses transfers + welcome task)

4.1.5 (mark_resource_done prompt)  ← independent, any point
4.1.6 (metadata param)              ← independent, any point
4.1.7 (get_my_components)           ← pairs naturally with 4.1.4
4.1.8 (SME prompt)                  ← after 4.1.4 + 4.1.7
4.1.9 (docs + memory)               ← last
```

### 4.1 Test budget

| Sub-phase | New | Retrofit |
|-----------|-----|----------|
| 4.1.1 | 1 | 0 |
| 4.1.2 | 15 | 3 |
| 4.1.3 | 5 | 0 |
| 4.1.4 | 5 | 0 |
| 4.1.5 | 1 | 0 |
| 4.1.6 | 2 | 0 |
| 4.1.7 | 3 | 0 |

Total: ~32 new tests. Target final count: ~386 (from 354).

### 4.1 Shipped summary (2026-04-24)

All 9 sub-phases landed:
- `87f4636` — 4.1.1 pydantic schema unblock
- `0bd05cb` — 4.1.2 mutation-scoped transfer tools + stale hygiene (transfer_attributions retightened with consolidation_id; new: transfer_edges, transfer_flows, get_stale_edges, get_stale_flows)
- `ceb035d` — 4.1.3 absorb_agent cascade flags (default on; workflow collapses from 5 calls to 2)
- `c502a39` — 4.1.4 spawn_child_agent hardening (strict source_slice guard, welcome task for child, transfer_edge_ids + transfer_flow_ids params)
- `1fc6385` — 4.1.5 + 4.1.6 + 4.1.7 + 4.1.8 bundle (mark_resource_done idempotency prompt, nominate_consolidation metadata JSONB, get_my_components, SME prompt split-awareness)

Final test count: 383 passing. MCP tool count: 77 (up from 72 — added 5 tools, one signature change).
Pre-existing flake on `test_flows.test_flow_fan_out_one_incoming_many_outgoing` unrelated to this work.

---

## Phase 4.2: DEMO41 post-run fixes ✅

Shipped after DEMO41 verification run surfaced 3 real bugs + 1 false-alarm that still warranted an ergonomic fix.

### 4.2.1 `get_action_items_summary` pydantic still erroring
Phase 4.1.1 widened the return annotation to `dict[str, Any]` but FastMCP still inferred a strict `DictModel` on some code paths. Removing the return-type annotation entirely on the MCP wrapper lets the bare-dict pass through. Verified live against the DEMO41 DB state: agents now see populated `proxied` list without pydantic erroring.

### 4.2.2 `transfer_attribution_ids` on `spawn_child_agent`
DEMO41's split left a hostname attribution (`payments.internal.demo41`) stranded on the parent (resolver flagged). We had `transfer_attributions` as a standalone tool, but `spawn_child_agent` only integrated `transfer_edge_ids` + `transfer_flow_ids`. Added `transfer_attribution_ids: list[str] = None` param — moves attrs atomically during split carve via the mutation-scoped helper. SME prompt updated to mark this as MANDATORY split hygiene.

### 4.2.3 `act_on_proxy_item` docstring cheat-sheet
Multiple SMEs had to guess payload shapes (`payload={"proxy_agent_id": ...}` for broadcast.ack; `{"to_agent_id": ..., "message": ...}` for chat.send, etc.) and hit error → retry cycles. Docstring now enumerates required keys per `(item_type, action)` pair. Pure doc change, no behavior.

### 4.2.4 `get_my_proxy_items(include_empty=False)`
DEMO41 behavior #24 ("transitive chain-walk broken") was a false alarm — the recursive CTE walks all hops correctly, but the code drops proxy groups with zero pending items to reduce survivor inbox noise. Added `include_empty: bool = False` param — default preserves production ergonomics; `True` surfaces the full transitive chain for audit / verification views. Confirmed live against DEMO41 DB: passing `include_empty=True` to `sme-770e0f4a` now returns depth=1 (sme-9c9775f1) AND depth=2 (sme-f33d6204).

### 4.2 commit + test delta
Single commit `59a6a4f` with 2 new tests (include_empty surfacing depth-2; transfer_attribution_ids cascade during split). Phase 4 + 4.1 test count: 99 → 101. Pre-existing ollama flakes elsewhere unrelated.

---

## Phase 5: Tweaks & Improvements ✅

**Shipped 2026-04-25.** All 11 sub-phases landed across incremental commits:
- `48dabf0` — 5.1 SPA routing foundation
- `5310b3d` — 5.2 Entities tab + drill-downs
- `d02a579` — 5.3 Catalog tab + drill-downs
- `9ea62a7` — 5.4 sidebar component-search + persistence pill
- `48399fe` — 5.5 auto-ack on terminal communications
- `5278afb` — 5.6 per-message confidence stamping
- `58bdd23` — 5.7 toggle broadcast persistence post-send
- `28f25de` — 5.8 admin chat wakes from sleep fix
- `1e0ecbc` — 5.9 record_insight + agent_insights + UI
- `1e27f54` — 5.10 mcp_audit decorator + table
- `7cae011` — 5.11 doc + memory sync
- `68027a6 + bc483d7` — 5.12 entity `kind → type` rename for FE consistency, entity-ref pill on Communications rows (cross-tab drill-in), persistence toggle on Entities broadcast rows (mirrors Communications toggle)

Final test count: ~510 (up from 383). MCP tool count: **79** (up from 77 — added `update_broadcast_persistence` + `record_insight`). Two new tables: `agent_insights`, `mcp_audit`.

**5.12 specifics:**
- `/api/entities` query param + JSON response field: `kind → type` (canonical now across both Communications and Entities)
- `/api/entity/{kind}/{id}` URL path: renamed to `/api/entity/{type}/{id}`
- Entities tab Type filter dropdown labeled "Type" (was "Kind")
- Communications rows render `type/<short-id>` pills that SPA-navigate to `/entities/{type}/{id}` (cross-tab cross-reference)
- Entities-tab broadcast rows render the same 📌 / ↪ persistence toggle as Communications

One bundle of admin-UI navigation, communication-hygiene fixes, and a self-improvement feedback loop for the agent system. Sub-numbered for commit cadence; all roll up to Phase 5. Ships incrementally — each sub-phase is its own commit + push.

### 5.0 Motivation

Phase 4 + 4.1 + 4.2 closed out the mutation/proxy primitives and battle-tested them via DEMO41. Out of that demo + day-to-day usage, a backlog of small ergonomic gaps surfaced — none of them deserve a phase-flow rethink, but together they meaningfully lower the friction of running Cartograph at scale. Phase 5 collects them.

Three themes:

1. **Navigation**: the admin UI is currently four tabs (Chat / Communications / Graph) with no URL routing — every state lives in JS memory, can't be shared via link, and refreshes blow the state away. Two new views (Entities + Catalog) need to fit alongside, and routing must extend across all of them.
2. **Communication hygiene**: small write-site additions (auto-ack on terminal states, per-message confidence stamping, persistence-toggle on broadcasts) that remove daily annoyances and unlock cleaner thread visualisations.
3. **Self-improvement loop**: agents can already chat with admin and raise blockers, but there's no structured channel for "I figured out a clever tactic" or "this prompt section confused me." Adding it now starts the data flywheel for prompt refinement before the org grows past a manageable handful of SMEs.

### 5.1 Routing foundation + URL scheme migration

**Backend**: FastAPI catch-all serves `index.html` for any non-`/api/*` path — single-line route at the bottom of `src/admin_ui/server.py`:
```python
@app.get("/{full_path:path}")
def spa_fallback(full_path: str): return FileResponse("static/index.html")
```

**Frontend**: vanilla History API router in `app.js`. New module-level `Router`:
```js
function navigate(path, {set, clear, replace} = {}) {
  // Merge query: preserve existing params, apply set/clear, push state.
  const url = buildUrl(path, set, clear);
  history[replace ? 'replaceState' : 'pushState']({}, '', url);
  dispatchEvent(new CustomEvent('routechange'));
}
function route() { /* parse location, return {tab, path, query} */ }
window.addEventListener('popstate', () => dispatchEvent(new CustomEvent('routechange')));
```

**Invariant**: `navigate(path)` preserves existing query params by default. To clear, opt-in via `clear: ['q']` or `set: {q: undefined}`. This is what makes sidebar search / filter state survive across detail-panel navigations.

**URL scheme** (canonical):
```
/chat/:agent_id?q=&group=
/communications?q=&type=&from_agent=&to_agent=&participant=&before=&limit=
/graph
/graph/component/:id
/entities?q=&type=&status=&participant=&before=&limit=
/entities/{task|consolidation|clarification|chat|broadcast}/:id
/catalog?q=&type=&plane=&status=&before=&limit=
/catalog/component/:id
/agent/:id/chain
/broadcast/new
```

**Migration of existing tabs (Chat / Communications / Graph)**: every place today that calls `switchTab(name)` / `selectAgent(id)` / `applyFilter(...)` is replaced by a `navigate(...)` call. The actual rendering moves to a single `routechange` listener that reads `route()` and dispatches to the right view-renderer. URL is the source of truth; FE state objects shrink to "what's currently on screen for the active route."

**Files touched:** `src/admin_ui/server.py` (catch-all route), `src/admin_ui/static/index.html` (no structural change but `<script>` cache-bust), `src/admin_ui/static/app.js` (router module + every `switchTab` / `selectAgent` / filter-apply rewritten).

**Tests:** N/A (FE has no test harness; verified manually).

**Effort:** S-M (router itself is ~80 lines; the rewrite touch-points are wide but mechanical).

### 5.2 Entities tab + drill-downs

**New tab in top nav**: Entities, sitting between Communications and Graph.

**View**: list of every workflow-entity (task / consolidation / clarification / chat / broadcast) as one row each. Type pill, status pill (type-aware values), participants, last-activity timestamp. Click → drill-down panel showing full thread + entity-specific metadata.

**Backend** — new endpoints (or one consolidated):
- `GET /api/entities?type=&status=&participant=&q=&before=&limit=` — returns `{entities: [{kind, id, status, participants, last_activity, summary}], has_more}`. Implementation: type-discriminated UNION across `tasks` + `consolidations` + `clarifications` + `communications` (chat / broadcast as their own row-types). Default sort: most-recent activity first.
- `GET /api/entity/:kind/:id` — returns `{entity: {...row}, thread: [...communications]}`. Already exists for task/consolidation/clarification — extend for chat (single message + replies) and broadcast (broadcast + per-agent ack roster).

**Frontend**: `entitiesView` module with two sub-renderers (`renderList`, `renderDetail`). Type-aware status filter dropdown (BW/BO/WD/TC for tasks, B1/B2/R/M/MD/D/F for consolidations, B1/B2/QR/QC/CC for clarifications, "acked / unacked" for chat/broadcast). Pagination via `before=` cursor (same idiom as existing chat pagination).

**Files touched:** `src/admin_ui/server.py` (new endpoints), `src/admin_ui/static/index.html` (new tab), `src/admin_ui/static/app.js` (entitiesView module), `src/admin_ui/static/style.css` (list/detail styling).

**Tests:** `tests/admin_ui/test_entities_endpoint.py` (~6: empty list, type filter, status filter, participant filter, pagination cursor, drill-down per kind).

**Effort:** M.

### 5.3 Catalog tab + drill-downs

**New tab in top nav**: Catalog, sitting alongside Graph (the spatial view) as the textual / scrollable counterpart.

**View**: paginated component list. Each row: canonical_name, display_name, type (with mesh icon mirroring Graph palette), plane pills, owner SME (link), attribution count, edge count (broken into bound/catalog/dangling). Click → drill-down panel with tabs Doc / Slice / Attributions / Edges / Flows / Resources.

**Backend** — new endpoints:
- `GET /api/components?type=&plane=&status=&q=&before=&limit=` — returns `{components: [{id, canonical_name, display_name, component_type, status, planes[], owner_sme_id, attribution_count, edge_count: {bound, catalog, dangling}}], has_more}`. Default sort: canonical_name ASC. `q` LIKE-matches canonical_name + display_name + metadata.
- `GET /api/component/:id/drilldown` — returns `{component, attributions: [...], edges: {bound_in, bound_out, catalog, dangling_out}, flows: [...], resources: [...]}`. One round-trip, lazy-loaded only when drill-down is opened.

**Frontend**: `catalogView` module. Filter bar (type, plane, status, search). Lazy-load drill-down on row click. Tab navigation inside the drill-down panel mirrors Graph's sidebar tabs for consistency.

**Files touched:** `src/admin_ui/server.py` (new endpoints), `src/admin_ui/static/index.html` (new tab), `src/admin_ui/static/app.js` (catalogView), `src/admin_ui/static/style.css`.

**Tests:** `tests/admin_ui/test_catalog_endpoint.py` (~6: empty, type filter, plane filter, q search, pagination, drilldown shape).

**Effort:** M.

### 5.4 Sidebar agent search by component name + broadcast persistence pill

**Sidebar agent search**: today's predicate filters `agent_id`. Extend to also match `component_canonical` + `component_display` (already in the `/api/agents` payload from Phase 4). Pure FE change, ~5 lines:
```js
const matches = q => a =>
  a.agent_id.toLowerCase().includes(q) ||
  (a.component_canonical || '').toLowerCase().includes(q) ||
  (a.component_display  || '').toLowerCase().includes(q);
```

**Multi-component edge case (deferred)**: post-merge an SME may own multiple active components historically (1-active-component invariant ensures one *current*, but list_components_for_agent could return more if we extend RCA shape). When that becomes high-traffic, switch `/api/agents` to `array_agg(component_*)` and update the FE filter to scan arrays. Flagged, not implemented now.

**Broadcast persistence pill**: render 📌 on persistent broadcast rows in the Communications tab. Backend already returns `is_persistent` on `/api/communications`; FE just needs the badge.

**Files touched:** `src/admin_ui/static/app.js`, `src/admin_ui/static/style.css`.

**Tests:** N/A (FE only).

**Effort:** XS.

### 5.5 Auto-ack on terminal communications

**Problem**: when a task transitions to TC, a consolidation to D/F, or a clarification to CC, the announcing communication row stays unacked. Survivors / participants get re-notified for state they consider closed.

**Fix at write site**: in `respond_task` / `respond_consolidation` / `review_consolidation` / `respond_clarification`, when the new state is terminal for the recipient role, stamp `acked_at = now()` on the freshly inserted communication row in the same transaction.

Terminal-for-recipient mapping:
- `tasks`: TC closes both worker + owner; auto-ack the comm sent to the non-actor.
- `consolidations`: D / F close both agents + resolver; auto-ack the comms sent to non-actors.
- `clarifications`: CC closes both asker + responder; auto-ack the comm sent to non-actor.

**Why write-site over scanner**: scanner approach would have to re-scan terminal entities continually. Write-site is one INSERT that knows what just transitioned and can pre-stamp the ack.

**Files touched:** `src/cartograph_mcp/tools/{tasks,consolidation,clarification}.py`.

**Tests:** ~6 new — one per (tool, terminal-transition) combo verifying the comm row is created with `acked_at` set.

**Effort:** S.

### 5.6 Per-message confidence stamping on consolidation responses

**Pattern**: same as the existing `metadata.state_transition = {from, to}` already stamped on `respond_consolidation` — add one more JSONB key.

**Stamp at three write sites** in `tools/consolidation.py`:
- `nominate_consolidation` → `metadata.confidence_at_send = {a: <a_conf_score>, b: null, r: null}`
- `respond_consolidation` → `metadata.confidence_at_send = {a, b, r}` reflecting all three scores AS OF after this write.
- `review_consolidation` → same shape after `r_conf_score` is updated.

The `consolidations` row stays the single source of truth for current scores; the `communications` thread carries the immutable timeline.

**Admin UI** consumes this for inline score pills on every thread message + a confidence sparkline at the top of the consolidation drill-down panel.

**Files touched:** `src/cartograph_mcp/tools/consolidation.py`, `src/admin_ui/static/app.js` (consolidation detail renderer).

**Tests:** 3 new — one per write site, asserting `metadata.confidence_at_send` keys + values match the consolidation row state after the call.

**Effort:** XS.

### 5.7 Toggle broadcast persistence post-send

**Problem**: today `is_persistent` is set at send time. Admin sometimes realises after sending that a "quick fix" is actually a standing policy (or vice versa).

**Tool**: `update_broadcast_persistence(agent_id, communication_id, persistent)` — admin/orchestrator only. Single UPDATE. Refuses on non-broadcast rows.

**Admin UI**: 📌 toggle button inline on broadcast rows in the Communications tab. POST `/api/broadcast/:id/persistence` with `{persistent: bool}`.

**Semantic**: toggling OFF leaves existing reads alone (already-acked agents stay acked); only future scanner reads / new agents change behaviour. Toggling ON makes the broadcast visible to future-spawned agents. No retroactive re-notification.

**Files touched:** `src/cartograph_mcp/tools/broadcast.py`, `src/cartograph_mcp/server.py`, `src/admin_ui/server.py`, `src/admin_ui/static/app.js`.

**Tests:** 4 new — toggle ON, toggle OFF, refuses non-broadcast, refuses non-admin.

**Effort:** S.

### 5.8 Verify + fix admin chat wakes from sleep

**Reported**: admin chat to a sleeping agent doesn't wake the agent — but `send_chat` already clears `sleep_until` when `from_agent='admin'` (Phase 2.5).

**Likely root cause**: the admin UI `POST /api/chat/:agent_id` endpoint writes directly to `communications` via `shared/db.py` (per HLD §10.3 — "direct DB access, not MCP"), bypassing the wake side-effect that lives inside `send_chat`.

**Investigation**: `git grep "POST /api/chat" + read the endpoint`. If confirmed, fix at the FastAPI endpoint:
```python
# After INSERT into communications, also clear sleep_until.
execute_mutate(
  "UPDATE agent_runs SET sleep_until = NULL "
  "WHERE agent_id = %s AND sleep_until IS NOT NULL",
  (agent_id,),
)
```

**Tests:** 2 new — admin UI chat to sleeping agent clears `sleep_until`; admin UI chat to awake agent leaves it alone.

**Effort:** XS.

### 5.9 `record_insight` MCP tool + agent_insights table + admin UI triage

**Schema** — new table:
```sql
CREATE TABLE agent_insights (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  agent_id      TEXT NOT NULL REFERENCES agent_runs(agent_id) ON DELETE CASCADE,
  kind          TEXT NOT NULL CHECK (kind IN (
                  'prompt_gap',     -- something missing or confusing in the prompt
                  'tactic_win',     -- I found a smart way to do X
                  'tool_gap',       -- I needed a tool that doesn't exist
                  'doc_confusing',  -- on-disk doc was misleading
                  'workflow_friction' -- the multi-step dance is awkward
                )),
  target        TEXT NOT NULL,    -- what this is about: 'sme.materialisation', 'transfer_edges', 'TRIGGER-MANAGEMENT.md §1.1b'
  body          TEXT NOT NULL,    -- the insight itself
  evidence      JSONB,            -- {task_ids, comm_ids, file_paths} optional
  status        TEXT NOT NULL DEFAULT 'open' CHECK (status IN (
                  'open', 'investigating', 'promoted', 'wontfix'
                )),
  triaged_by    TEXT,             -- admin agent_id when status moves off 'open'
  triaged_at    TIMESTAMPTZ,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_insights_agent  ON agent_insights(agent_id);
CREATE INDEX idx_insights_status ON agent_insights(status);
CREATE INDEX idx_insights_target ON agent_insights(target);
```

**MCP tool** (open to all active agents):
```
record_insight(agent_id, kind, target, body, evidence?)
  Inserts an open insight. Returns {id}.
```

**Agent prompts** — append to all four agent types:
> If you discover a smart tactic, hit a prompt gap, miss a tool you wish existed, or find an on-disk doc misleading, call `record_insight(...)` with the relevant `kind` + `target`. Be specific in `body` and link evidence (task ids, file paths) where possible. Don't over-report — one insight per genuinely-new finding, not every mild irritation.

**Admin UI** — new tab "Insights" or fold into a new "Meta" / "Ops" tab. Listable + filterable by `kind` / `target` / `status` / `agent_id`. Each row has triage actions: "promoted to prompt", "investigating", "wontfix", with a notes field.

**Files touched:** `src/shared/migrations.py`, `src/cartograph_mcp/tools/insights.py` (new), `src/cartograph_mcp/server.py`, `src/agent_management/agent_types/{orchestrator,iterator,sme,resolver}.py`, `src/admin_ui/server.py`, `src/admin_ui/static/{index.html,app.js,style.css}`.

**Tests:** ~6 — record happy path, kind validation, requires active agent, list endpoint with filters, triage UPDATE, prompt smoke test (each agent type prompt formats clean).

**Effort:** M.

### 5.10 MCP tool decorator audit (`mcp_audit` table)

**Goal**: cheap blanket audit of every MCP tool call. Foundation for debugging + future replay.

**Schema** (partitioned by week):
```sql
CREATE TABLE mcp_audit (
  id           BIGSERIAL,
  agent_id     TEXT NOT NULL,
  tool_name    TEXT NOT NULL,
  args_hash    TEXT NOT NULL,       -- sha1 of canonicalised args; full args NOT stored
  result_status TEXT NOT NULL,      -- 'ok' | 'error'
  error_msg    TEXT,                -- on error only
  duration_ms  INT NOT NULL,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (id, created_at)
) PARTITION BY RANGE (created_at);
-- Initial partition; rotation managed by a tiny weekly cron.
```

**Decorator** in `cartograph_mcp/server.py`:
```python
def audited(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        agent_id = kwargs.get('agent_id') or (args[0] if args else 'unknown')
        t0 = time.monotonic()
        try:
            result = fn(*args, **kwargs)
            _record_audit(agent_id, fn.__name__, args, kwargs, 'ok', None, t0)
            return result
        except Exception as e:
            _record_audit(agent_id, fn.__name__, args, kwargs, 'error', repr(e)[:500], t0)
            raise
    return wrapper
```

Applied at the `@mcp.tool` registration site so it wraps every tool uniformly.

**args_hash, not full payload**: keeps row size bounded. Audit answers "agent X called Y at time T and got result Z" not "what exactly did they pass." Investigations that need payloads have the underlying `tasks` / `communications` / etc. tables.

**Retention**: 30 days. Tiny script `src/admin_ui/audit_rotate.py` drops partitions older than 4 weeks. Run from a daily cron or manually.

**Admin UI** — per-agent activity timeline view inside the existing agent detail (Chat tab). "Last 24h: 47 tool calls, 2 errors. Most-used: get_action_items_summary (12), respond_task (8), ..."

**Files touched:** `src/shared/migrations.py`, `src/cartograph_mcp/server.py` (decorator + apply to every `@mcp.tool`), `src/admin_ui/server.py` (timeline endpoint), `src/admin_ui/static/app.js`, `src/admin_ui/audit_rotate.py` (new).

**Tests:** ~5 — decorator records on success, decorator records on exception, args_hash stable across equivalent calls, error_msg populated, timeline endpoint groups correctly.

**Effort:** M.

### 5.11 Doc + memory sync

Update all six docs + memory to reflect Phase 5 surface:
- **HLD.md** §2 (tool table — add `record_insight`, `update_broadcast_persistence`), §10 (admin UI — Entities + Catalog tabs, URL scheme, sidebar component-search, broadcast persistence pill), §10.1 (new API endpoints), §11 (move "list-view tab" out of future scope).
- **SCHEMA.md** — `agent_insights` + `mcp_audit` tables, `communications.metadata.confidence_at_send` documented under category 4.
- **TRIGGER-MANAGEMENT.md** §3 — add `record_insight` + `update_broadcast_persistence` to Act tools.
- **AGENT-PROMPTS.md** §0 (mention insights channel) + per-type prompts (insight rule).
- **IMPLEMENTATION-PHASES.md** — mark Phase 5 shipped with sub-commits + final test count.
- **ONE-PAGER.md** — likely no change (high-level pitch); verify still accurate.
- **memory/cartograph_state.md** — append Phase 5 summary.

**Effort:** M.

### 5.12 Ordering + commit cadence

```
5.1  routing foundation                     ← independent, ship FIRST (unblocks UI work)
  │
  ├─ 5.2  entities tab                      ← uses 5.1 router
  └─ 5.3  catalog tab                       ← uses 5.1 router

5.4  sidebar component-search + persistence pill   ← independent, can land anywhere after 5.1
5.5  auto-ack terminal comms                       ← independent
5.6  per-message confidence                        ← independent
5.7  toggle broadcast persistence                  ← independent (UI button needs 5.4 / 5.1 ideally)
5.8  admin-chat-wakes-from-sleep fix               ← independent
5.9  record_insight                                ← independent
5.10 mcp_audit decorator                           ← independent (defer until end of phase to capture all the new tools we added)
5.11 doc + memory sync                             ← LAST
```

Each sub-phase = its own commit + push. Final `5.11` is a single doc-sync commit.

### 5.13 Test budget

| Sub-phase | New tests |
|-----------|-----------|
| 5.1 | 0 (FE only; manual) |
| 5.2 | 6 |
| 5.3 | 6 |
| 5.4 | 0 (FE only) |
| 5.5 | 6 |
| 5.6 | 3 |
| 5.7 | 4 |
| 5.8 | 2 |
| 5.9 | 6 |
| 5.10 | 5 |

Total: ~38 new. Target final count: ~421 (from 383).

---

## Phase 7: Acks + handoff + self-loops + catalogs first-class ✅

**Shipped 2026-04-26.** All 4 sub-phases landed across incremental commits:
- `b850a28` — 7.1 terminal-state ack model (replaces Phase 5.5 auto-ack)
- `feecdc0` — 7.2 pre-merge handoff convention (SME prompt only)
- `53a2894` — 7.3 self-loop CHECK relaxation
- `595ef85` — 7.4 catalogs as first-class table

Final test count: 490 (up from 469 pre-Phase-7). MCP tool count: **85** (up from 79 — added `ack_terminal`, `upsert_catalog`, `get_my_catalogs`, `get_my_catalog_callers`, `get_unmatched_callers`, `get_orphan_catalogs`). Two new tables: `terminal_acks`, `catalogs`. Two CHECK constraints dropped: `edges_no_self_loop_v2`, `edges_check`.

Bundle of four conceptually distinct improvements that share infrastructure (schema migrations, scanner extensions, agent-prompt updates) and naturally batch together. Sub-phases are independently shippable but presented as one phase for narrative coherence.

**Note on numbering:** Phase 6 (Globe — sphere-constrained graph view) was carved off into the `feat/globe-experimental` branch and is NOT on main. Future phases (phase-flow completion, observability, DM, knowledge pool) shift right by one — see end-of-file.

### 7.0 Motivation

Four threads surfaced through Phase 5 + 6 work + design discussions:

1. **Phase 5.5's "auto-ack on terminal communications" was solving the wrong problem.** It silently dropped closure announcements without explicit comprehension by participants. Closure should be an explicit forcing function — every participant should be re-woken on terminal entities until they ack.
2. **Merge protocol has no structured handoff.** The agent being absorbed (B) has accumulated runtime knowledge — caveats, configs, runtime nuances — that aren't captured in `component_doc_md` / source_slice / attributions. Once B is decommissioned, that context is gone. Pre-merge clarification gives A a chance to extract it.
3. **`edges_no_self_loop_v2` CHECK constraint blocks legitimate self-invocation patterns** (cron self-trigger, service publishing + consuming the same topic, recursive component-level calls). Loosening costs nothing.
4. **Catalog modeling via `from IS NULL` in `edges` is structurally awkward** — `edge_type='calls'` on a callee declaration grafts the future-caller's POV onto the callee. A separate `catalogs` table with noun-form `kind` enum (`endpoint`/`topic`/`queue`/...) reads cleanly and gives us first-class queryability for unmatched-caller / orphan-catalog detection.

---

### 7.1 Terminal-state ack model (replaces Phase 5.5 auto-ack)

#### Schema

New table:
```sql
CREATE TABLE terminal_acks (
  entity_type TEXT NOT NULL CHECK (entity_type IN ('task','consolidation','clarification')),
  entity_id   UUID NOT NULL,
  agent_id    TEXT NOT NULL REFERENCES agent_runs(agent_id) ON DELETE CASCADE,
  acked_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (entity_type, entity_id, agent_id)
);
CREATE INDEX idx_term_acks_agent ON terminal_acks(agent_id);
```

#### Phase 5.5 revert

Drop `acked_at = now()` pre-stamping at four sites:
- `src/cartograph_mcp/tools/tasks.py::respond_task` — TC transition
- `src/cartograph_mcp/tools/consolidation.py::review_consolidation` — F transition
- `src/cartograph_mcp/tools/consolidation.py::complete_consolidation` — D transition (both rows)
- `src/cartograph_mcp/tools/clarification.py::respond_clarification` — CC + QR transitions

Terminal announcement comm rows now land UNACKED. Trigger scanner picks them up via the new wake condition.

#### MCP tool

```
ack_terminal(agent_id, entity_type, entity_id) -> dict
  Insert a row into terminal_acks. ON CONFLICT DO NOTHING (idempotent).
  Validates:
    - entity_type ∈ ('task','consolidation','clarification')
    - entity exists at entity_id
    - caller is a participant of the entity
    - entity is in a terminal state (TC for task, D/F for consolidation, CC/QR for clarification)
  Returns {acked: bool, already_acked: bool}
```

Lives in `src/cartograph_mcp/tools/terminal_acks.py` (new file).

#### Trigger scanner extension

Add three EXISTS-NOT scans in `src/trigger_management/scanners/`. Each returns "wake needed" if there's a terminal-state participant entity I haven't acked:

```sql
-- Tasks
SELECT 1 FROM tasks t
 WHERE (t.owner_agent_id = :me OR t.worker_agent_id = :me)
   AND t.status = 'TC'
   AND NOT EXISTS (
         SELECT 1 FROM terminal_acks ta
          WHERE ta.entity_type = 'task' AND ta.entity_id = t.id AND ta.agent_id = :me
       )
LIMIT 1
```

Same shape for consolidations (`status IN ('D','F')`, participants = `agent_a_id`, `agent_b_id`, `resolved_by`, `mutation_assigned_to`) and clarifications (`status IN ('CC','QR')`, participants = `asker_agent_id`, `responder_agent_id`).

Combine into existing trigger scan. Add `terminal_pending_ack_count` to action-items summary.

#### Action-items surface

Extend `get_action_items_summary` + `get_action_items_detail` to include:
```json
{
  "terminal_pending_ack": [
    {"entity_type": "task", "entity_id": "...", "summary": "...", "since": "..."}
  ]
}
```

Agents see this on every wake until they ack each entry.

#### Decommission auto-ack

In `src/cartograph_mcp/tools/mutation.py::absorb_agent`: after decommissioning the target, run the scanner query for the target and bulk-insert terminal_acks rows on the target's behalf. ~5 lines.

This handles the case where B has unacked terminal entities at decommission time — A can't easily ack on B's behalf via proxy (terminal-acks aren't a comm row), so we just close them out automatically. Audit trail loss is acceptable per design discussion.

#### Agent prompts

All four agent types get a wake-up rule (in `MISSION_AND_VOCABULARY` shared block in `src/agent_management/agent_types/base.py`):

> "When `get_action_items_summary` shows `terminal_pending_ack` entries, fetch each entity (`get_*_thread` by id), read the resolution, then call `ack_terminal(entity_type, entity_id)` to confirm understanding. You will keep being woken on these until you ack."

#### Tests (~10 new in `tests/mcp_tools/test_terminal_acks.py`)

- `test_ack_terminal_inserts_row` — happy path
- `test_ack_terminal_idempotent` — ON CONFLICT DO NOTHING
- `test_ack_terminal_rejects_non_participant`
- `test_ack_terminal_rejects_non_terminal_entity`
- `test_ack_terminal_rejects_unknown_entity`
- `test_scanner_wakes_on_unacked_terminal_task`
- `test_scanner_wakes_on_unacked_terminal_consolidation_for_both_agents`
- `test_scanner_wakes_on_unacked_terminal_clarification`
- `test_scanner_skips_after_ack`
- `test_decommission_auto_acks_target_pending`

Plus update existing `tests/mcp_tools/test_terminal_auto_ack.py` (Phase 5.5 tests) — assertions inverted: announcement comm should be `acked_at IS NULL` after the transition.

#### Files touched

- `src/shared/migrations.py` — terminal_acks table + index
- `src/cartograph_mcp/tools/{tasks,consolidation,clarification}.py` — revert auto-ack
- `src/cartograph_mcp/tools/terminal_acks.py` — new
- `src/cartograph_mcp/tools/action_items.py` — extend summary + detail responses
- `src/cartograph_mcp/tools/mutation.py` — decommission auto-ack in absorb_agent
- `src/cartograph_mcp/server.py` — register `ack_terminal` MCP tool
- `src/trigger_management/scanners/` — extend wake scan
- `src/agent_management/agent_types/base.py` — shared prompt addition
- `tests/mcp_tools/test_terminal_acks.py` — new
- `tests/mcp_tools/test_terminal_auto_ack.py` — invert assertions
- `tests/admin_ui/conftest.py` — add `terminal_acks` to clean_tables

**Effort:** S (~½ day)

---

### 7.2 Pre-merge context handoff convention

#### No new tools, no schema. Pure agent-prompt addition.

In `src/agent_management/agent_types/sme.py`, in the merge mutation section, add a step before `absorb_agent`:

> **Step 0 — Pre-absorb handoff (mandatory):**
> Before calling `absorb_agent(target_agent_id=B)`, raise a clarification to B:
>
> ```
> create_clarification(
>   asker_agent_id = me,
>   responder_agent_id = B,
>   question_message = "Pre-merge handoff: I'm about to absorb you. Brief me on
>     anything important you know that's NOT captured in your component_doc_md /
>     source_slice / attributions / edges / flows: configs, runtime nuances,
>     known issues, monitoring quirks, deploy gotchas. Respond with QC."
> )
> ```
>
> WAIT for B's QC response. Read it. Capture relevant facts in your own
> `component_doc_md` (call `upsert_component` with appended doc). THEN call
> `absorb_agent`.
>
> If B is unresponsive for >30 minutes (no state change off B2), escalate
> to admin via `send_chat` instead of blocking the mutation. Admin may
> proceed without handoff or intervene with B directly.

Same convention for split via `spawn_child_agent` (parent can pre-clarify with admin or a domain-expert SME if needed for briefing quality). Documented as optional for split.

#### Tests

None — convention only, manual smoke-verifiable. The `create_clarification` + `respond_clarification` + `upsert_component` tools all have existing test coverage.

#### Files touched

- `src/agent_management/agent_types/sme.py` — prompt update only

**Effort:** XS (~30 min)

---

### 7.3 Self-loop CHECK constraint relaxation

#### Schema

Drop the `edges_no_self_loop_v2` CHECK constraint via migration:
```sql
ALTER TABLE edges DROP CONSTRAINT IF EXISTS edges_no_self_loop_v2;
```

The other two CHECK constraints stay (`edges_at_least_one_endpoint`, the old `edges_no_self_loop` if it exists).

#### MCP tools

No changes. `upsert_edge_outbound` already accepts arbitrary `from_component_id` + `to_component_id`; the constraint was the only blocker.

#### Graph view

3d-force-graph handles self-links natively — they render as a small curl off the node. No code change. Bundling already works: a self-loop `(X, X, calls, /foo)` joins the convergence-group keyed on `(target=X, calls, /foo)` and becomes a contributor at the junction.

#### Globe view

Globe lives on `feat/globe-experimental` branch — not on main. Adding the great-circle self-loop fallback there is part of that branch's polish, NOT this phase.

#### Tests (~3 new in `tests/mcp_tools/test_self_loops.py`)

- `test_self_loop_bound_edge_accepted` — `upsert_edge_outbound` with from = to succeeds
- `test_self_loop_idempotent` — ON CONFLICT updates as expected
- `test_self_loop_bundles_into_existing_junction` — verify convergence-group key includes self-loop

#### Files touched

- `src/shared/migrations.py` — drop constraint
- `tests/mcp_tools/test_self_loops.py` — new

**Effort:** XS (~½ hour)

---

### 7.4 Catalogs as first-class table

The largest sub-phase. Promotes catalog rows from `edges` (where `from IS NULL`) to a dedicated `catalogs` table with noun-form `kind` enum.

#### Schema

```sql
CREATE TABLE catalogs (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  component_id  UUID NOT NULL REFERENCES components(id) ON DELETE CASCADE,
  kind          TEXT NOT NULL CHECK (kind IN (
                  'endpoint',         -- HTTP endpoint
                  'topic',            -- pub/sub topic
                  'queue',            -- message queue
                  'data_source',      -- DB / cache / object store
                  'trigger_target'    -- something that can be triggered
                )),
  identifier    TEXT NOT NULL,        -- the specific endpoint/topic/queue/etc.
  metadata      JSONB NOT NULL DEFAULT '{}',
  confidence    FLOAT NOT NULL DEFAULT 1.0,
  embedding     vector(1024),         -- mxbai-embed-large dim
  discovered_by TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

  UNIQUE (component_id, kind, identifier)
);

CREATE INDEX idx_catalogs_component ON catalogs(component_id);
CREATE INDEX idx_catalogs_identifier ON catalogs(identifier);
CREATE INDEX idx_catalogs_kind ON catalogs(kind);
CREATE INDEX idx_catalogs_embedding ON catalogs USING hnsw (embedding vector_cosine_ops);
```

#### Migration

Move existing catalog-shape rows from `edges` to `catalogs`:
```sql
INSERT INTO catalogs (component_id, kind, identifier, metadata, confidence, embedding, discovered_by, created_at)
SELECT to_component_id,
       CASE edge_type
         WHEN 'calls' THEN 'endpoint'
         WHEN 'reads_from' THEN 'data_source'
         WHEN 'writes_to' THEN 'data_source'
         WHEN 'publishes_to' THEN 'topic'
         WHEN 'consumes_from' THEN 'queue'
         WHEN 'triggers' THEN 'trigger_target'
         WHEN 'runs_on' THEN 'data_source'  -- infrastructure-side; may need review
         ELSE 'endpoint'
       END AS kind,
       identifier, metadata, confidence, embedding, discovered_by, created_at
FROM edges
WHERE from_component_id IS NULL
ON CONFLICT (component_id, kind, identifier) DO NOTHING;

DELETE FROM edges WHERE from_component_id IS NULL;
```

After migration, `edges_at_least_one_endpoint` CHECK still holds (we removed the NULL-from rows). Drop the catalog-related index `edges_catalog_unique` (no longer needed).

#### MCP tools — added

```
upsert_catalog(agent_id, component_id, kind, identifier, metadata?, confidence?) -> dict
  Replaces upsert_edge_catalog.
  Validates: agent owns component_id via RCA.
  ON CONFLICT (component_id, kind, identifier) DO UPDATE
    metadata = catalogs.metadata || EXCLUDED.metadata,
    confidence = GREATEST(catalogs.confidence, EXCLUDED.confidence),
    updated_at = now().
  Auto-embeds identifier via shared.embedding.

get_my_catalogs(agent_id) -> list[dict]
  Returns catalogs for components the agent owns (via RCA).
  Includes a 'caller_count' field (how many bound edges currently
  match this catalog).

get_my_catalog_callers(agent_id, catalog_id?) -> dict
  For each of agent's catalog rows, list bound callers matched by
  (target=catalog.component_id, edge_type=mapped_from_kind, identifier).
  Returns {catalog_id: [{caller_id, edge_id, edge_type, identifier}]}.

get_unmatched_callers(agent_id) -> list[dict]
  Bound edges whose target = a component owned by agent, but where no
  matching catalog row exists. Per row:
    {edge_id, source_id (caller), edge_type, identifier,
     suggested_action: 'add_catalog' | 'raise_clarification'}
  Suggested action is informational only — SME triages: dynamic
  identifier (DB-like, ignore), missing catalog (upsert), caller
  error (raise clarification).

get_orphan_catalogs(agent_id) -> list[dict]
  Catalogs the agent owns where no bound caller currently matches.
  Companion to get_unmatched_callers — together they show the
  catalog-coverage health of the agent's component.
```

The kind ↔ edge_type mapping for matching:
```python
_CATALOG_KIND_TO_EDGE_TYPES = {
    'endpoint':       {'calls'},
    'topic':          {'publishes_to', 'consumes_from'},
    'queue':          {'publishes_to', 'consumes_from'},
    'data_source':    {'reads_from', 'writes_to'},
    'trigger_target': {'triggers'},
}
```

#### MCP tools — renamed

- `upsert_edge_catalog` → `upsert_catalog` (signature change: `kind` replaces `edge_type` for the new noun semantics)

The old name kept as a deprecated wrapper that translates `edge_type` → `kind` and forwards to `upsert_catalog` (via the inverse mapping) for one phase, then removed in Phase 8+.

#### MCP tools — modified

- `bind_edge` — when binding a dangling outgoing, no longer matches against catalogs in `edges`; instead optionally validates against a `catalogs` row by `(to_component_id, mapped_kind, identifier)`. Soft validation (warn but don't block) — preserves "bind first, catalog later" workflow.
- `get_component_edges` — return shape adds a top-level `catalog` bucket sourced from the new table (separate from `incoming_bound`/`outgoing_bound`/`outgoing_dangling`). Or rename to `incoming_catalog` to keep names parallel.

#### LOS BFS — catalog bridging across two tables

Today `effectiveFlowIncomingsForEdge` walks `graphSnapshot.edges` to find catalog rows matching `(target, edge_type, identifier)`. Update to walk `catalogs` table instead, mapping `edge_type` to expected catalog `kind`. This is FE-only logic; same algorithm.

#### Backend `/api/graph` payload

Returns `nodes`, `edges` (only bound + dangling now), `flows`, AND new `catalogs` array:
```json
{
  "nodes": [...],
  "edges": [...],
  "flows": [...],
  "catalogs": [{
    "id": "...", "component_id": "...", "kind": "endpoint",
    "identifier": "GET /scorecard", "metadata": {...}, "confidence": 0.95
  }]
}
```

FE Graph + Catalog tabs + Globe consume the new shape — UNION local renders so the visual orphan-catalog stub spike treatment continues to work.

#### Embedding pipeline

Catalog rows embed `"{kind}: {identifier}"` at write time. Same pipeline as edges/components.

#### Stale hygiene

`get_stale_edges` continues to surface bound edges whose target component is decommissioned. Catalogs of decommissioned components are now visible via `get_orphan_catalogs` (returning rows with no callers — which post-decommission they all are).

#### Agent prompts

Update SME prompt (`sme.py`):

- **Materialisation Step 2** — replace `upsert_edge_catalog(edge_type=...)` references with `upsert_catalog(kind=...)`. Update the noun semantics: "applications/lambdas/external-services declare exposed endpoints; databases declare data_source rows for queryable schemas; queue components declare queue rows for accepted topics; etc."
- **Hygiene cycle** — add: "On wake, periodically call `get_unmatched_callers(your_agent_id)` to find bound edges to your component that have no matching catalog row. Triage each: dynamic identifier (DB-like, ignore), missing catalog (call `upsert_catalog`), caller error (raise clarification)."

#### Backwards compatibility

- The deprecated `upsert_edge_catalog` shim accepts the old call shape and forwards. Logged at WARN level so we know who's still calling it.
- The old `incoming_catalog` field on `get_component_edges` still returned (sourced from new table now), so admin UI / Graph / Globe don't need to change their FE shape immediately.

#### Tests (~20 new in `tests/mcp_tools/test_catalogs.py` + extensions)

- `test_catalogs_table_exists`
- `test_upsert_catalog_creates`
- `test_upsert_catalog_scope_check_owner_only`
- `test_upsert_catalog_idempotent_on_unique_key`
- `test_upsert_catalog_metadata_merge_on_conflict`
- `test_upsert_catalog_kind_validation`
- `test_upsert_catalog_embeds_identifier`
- `test_get_my_catalogs_returns_owned_only`
- `test_get_my_catalogs_includes_caller_count`
- `test_get_my_catalog_callers_matches_by_kind_mapping`
- `test_get_unmatched_callers_finds_orphan_bounds`
- `test_get_unmatched_callers_skips_matched`
- `test_get_orphan_catalogs_returns_zero_caller_rows`
- `test_get_orphan_catalogs_skips_matched`
- `test_bind_edge_no_longer_blocks_on_missing_catalog` (soft validation)
- `test_migration_moves_existing_catalog_rows` (manual / fixture-based)
- `test_old_upsert_edge_catalog_shim_forwards`
- `test_graph_endpoint_returns_catalogs_array` (admin_ui)
- `test_graph_endpoint_excludes_catalog_rows_from_edges` (admin_ui)
- `test_los_bfs_bridges_catalog_via_two_tables` (smoke; manually verified)

Plus migrations of existing edge tests that asserted catalog rows in `edges` — update assertions or move to catalog tests.

#### Files touched

- `src/shared/migrations.py` — catalogs table + indexes + migration of catalog rows + drop edges_catalog_unique
- `src/cartograph_mcp/tools/catalogs.py` — new (upsert_catalog, get_my_catalogs, get_my_catalog_callers, get_unmatched_callers, get_orphan_catalogs)
- `src/cartograph_mcp/tools/components.py` — `bind_edge` soft validation against catalogs
- `src/cartograph_mcp/tools/components.py` — `get_component_edges` reads catalog bucket from new table
- `src/cartograph_mcp/server.py` — register 5 new tools + deprecation wrapper for `upsert_edge_catalog`
- `src/admin_ui/server.py` — `/api/graph` UNION includes catalogs array
- `src/admin_ui/static/app.js` — Graph tab uses new catalogs source for orphan stub rendering
- `src/admin_ui/static/app.js` — Catalog tab drill-down reads from new shape (was already consuming the `incoming_catalog` bucket; should still work)
- `src/agent_management/agent_types/sme.py` — prompt rewrites
- `tests/mcp_tools/test_catalogs.py` — new
- `tests/mcp_tools/test_edges.py` + `test_flows.py` — assertions updated
- `tests/admin_ui/test_graph_endpoint.py` — extend for catalogs payload
- `tests/admin_ui/conftest.py` + `tests/mcp_tools/conftest.py` — clean_tables adds `catalogs`

**Effort:** L (~2-3 days)

---

### 7.5 Sub-phase ordering + commit cadence

```
7.0  pre-Phase-7 doc sync (DONE — commit c67ecb3)
7.1  terminal-acks            ← S, ½ day
7.2  pre-merge handoff prompt ← XS, 30 min  (independent, can land any time)
7.3  self-loop loosening       ← XS, ½ hour (independent, can land any time)
7.4  catalogs first-class      ← L, 2-3 days (largest)
7.5  doc + memory sync         ← M, ½ day
```

Each sub-phase = its own commit + push. TDD: tests written first, run red, then implementation makes them green.

### 7.6 Test budget

| Sub-phase | New tests | Modified tests |
|-----------|-----------|----------------|
| 7.1 | ~10 | ~8 (Phase 5.5 invert) |
| 7.2 | 0 | 0 |
| 7.3 | ~3 | 0 |
| 7.4 | ~20 | ~5 (edge tests with catalog-row assertions) |

Total: ~33 new + ~13 modified. Target final count: ~545 (from ~510).

### 7.7 Schema delta summary

- `+terminal_acks` table
- `+catalogs` table
- `-edges_no_self_loop_v2` constraint
- `-edges_catalog_unique` index
- migration: catalog rows moved from `edges` to `catalogs`
- communications table unchanged (no longer auto-stamping acked_at on terminal rows)

### 7.8 MCP tool delta summary

- `+ack_terminal` (Phase 7.1)
- `+upsert_catalog`, `+get_my_catalogs`, `+get_my_catalog_callers`, `+get_unmatched_callers`, `+get_orphan_catalogs` (Phase 7.4)
- `=upsert_edge_catalog` (kept as deprecated wrapper)

Net: 79 → 84 tools.

---

## Phase 7.4.2: flows reference catalogs first-class + admin UI catalog endpoint fixes ✅

**Shipped 2026-04-26.** Single commit `d8fe56f`.

### Motivation

Phase 7.4's catalog migration ran `DELETE FROM edges WHERE
from_component_id IS NULL`. `flows.incoming_edge_id` had a hard FK to
`edges` with `ON DELETE CASCADE` — so every flow whose incoming was a
catalog row (the canonical pattern, per Phase 3.9 `mock_seed.py:354-361`)
got cascade-deleted. Result: 0 flows post-migration.

Two admin-UI endpoints also missed the migration: `/api/components`'s
`catalog_count` column and `/api/component/{id}/drilldown`'s `catalog`
bucket both still queried `edges WHERE from_component_id IS NULL`.
Both returned 0 / empty for every component.

### Schema change (idempotent migration)

```sql
ALTER TABLE flows DROP COLUMN incoming_edge_id;
ALTER TABLE flows ADD COLUMN incoming_catalog_id UUID NOT NULL
  REFERENCES catalogs(id) ON DELETE CASCADE;
DROP CONSTRAINT flows_component_id_incoming_edge_id_outgoing_edge_id_key;
CREATE UNIQUE INDEX flows_unique_catalog_incoming
  ON flows (component_id, incoming_catalog_id, outgoing_edge_id);
DROP INDEX idx_flows_incoming;
CREATE INDEX idx_flows_incoming_catalog ON flows(incoming_catalog_id);
```

The migration is in `_migrate_edges_asymmetric` block (post-7.4
catalogs creation) and idempotent across re-runs.

### MCP tool changes

- `upsert_flow(agent_id, component_id, incoming_catalog_id,
  outgoing_edge_id, ...)` — param renamed; validates
  `catalog.component_id == component_id`.
- `get_flow(component_id, incoming_catalog_id)` — query updated.
- `get_flow_inverse(component_id, outgoing_edge_id)` — **contract
  change**: now returns rows from `catalogs` table (was: `edges`).
- `get_stale_flows` — incoming-side staleness check dropped (catalogs
  are owned by the same component, can't have a "dead counterparty");
  only outgoing-side decommissioned-target check remains.

### Mutation cascade additions

- `absorb_agent` — added unconditional **catalog cascade** runs BEFORE
  flow cascade. Re-points target's catalogs to survivor; drops on
  `(kind, identifier)` collision (cascading any flows that referenced
  the dropped catalog). New cascade counter `cascade.catalogs`.
- `spawn_child_agent(... transfer_catalog_ids?)` — new optional
  param to atomically carve catalogs into the child during a split.
  Returns `transferred_catalogs`.

### Admin UI changes

- `/api/components.catalog_count` — sourced from `catalogs` table.
- `/api/component/{id}/drilldown.edges.catalog` — sourced from
  `catalogs`; reshaped to legacy edge-row shape so FE consumers don't
  need a payload-shape change. Carries `catalog_kind` for richer
  rendering.
- `/api/graph` flows payload — `incoming_edge_id` →
  `incoming_catalog_id`.

### Frontend (`app.js`)

- Field rename throughout (LOS BFS, hover lookups, junction grouping,
  drill-down flow render).
- Catalog drill-down now renders kind + identifier + confidence (no
  spurious `→ to_component_id` arrow — catalogs are self-referential
  surface declarations).
- Cache-bust v=57 → v=58.

### Mock seed (`mock_seed.py`)

- `seed_edges` writes catalog rows into `catalogs` table.
- `seed_flows` uses catalog UUIDs as `incoming_catalog_id`.
- `cleanup` purges catalogs first so cascade clears flows cleanly.

### SME prompt (`sme.py` STEP 4)

Updated to teach the new model: flow incoming is ALWAYS a catalog id
(not an edge id). Bound caller edges bridge to the catalog via
`(target, edge_type, identifier)` for rendering / hygiene, but they
are NOT the flow anchor. No catalog → no flow.

### Tests

- `test_flows.py` rewritten end-to-end (11 tests). Covers happy paths,
  ownership, validation, idempotent merge, catalog DELETE cascade,
  outgoing edge DELETE cascade, fan-out, unknown catalog refusal,
  foreign-component catalog refusal.
- `test_mutation.py` — 5 flow-insert sites updated; cascade
  assertions adjusted; stale-flow test re-keyed on outgoing-side.
- `test_catalog_endpoint.py` — drill-down test seeds catalog into
  catalogs table; new regression test for `catalog_count`.

### Verified post-deploy

- 495 / 505 tests green (10 pre-existing failures unrelated to this
  change).
- Mocks re-seeded: 25 components, 26 catalogs, 66 edges, 22 flows.
- /api/graph flows[0] carries `incoming_catalog_id`.
- /api/components feeds-api `edge_count.catalog == 2` (was 0).
- Drill-down `edges.catalog` populated.

### Files touched

- `src/shared/migrations.py`
- `src/cartograph_mcp/server.py`, `tools/components.py`, `tools/mutation.py`
- `src/admin_ui/server.py`, `mock_seed.py`, `static/app.js`, `static/index.html`
- `src/agent_management/agent_types/sme.py`
- `tests/mcp_tools/test_flows.py`, `test_mutation.py`
- `tests/admin_ui/test_catalog_endpoint.py`

**Effort:** S (~2 hours including tests + verify).

---

## Phase 7.4.3: doc + memory sync for 7.4.2 ✅

**Shipped 2026-04-26.** Commit `faeca1b`. Updates 6 canonical docs +
memory file to reflect the flows-reference-catalogs-first-class
model shipped in 7.4.2.

---

## Phase 7.4.4: spawn_child wrapper exposes transfer_catalog_ids + drop Python self-loop guards + lean vector_search projection ✅

**Shipped 2026-04-26.** Single commit `ead74bf`. Closes 3 DEMO7-surfaced gaps.

### #1 spawn_child_agent MCP wrapper missed `transfer_catalog_ids`

The inner `mutation_tool.spawn_child_agent` had the param (added in
Phase 7.4.2) but the `@mcp.tool()` wrapper at `server.py:982` didn't
declare or pass it through, so SMEs literally couldn't use it.
sme-payments-split flagged in DEMO7 feedback: *"Phase 7.4.2 catalog
'transfer' did NOT actually transfer: the parent monolith-x still
owns the original POST /payments/charge catalog row."* Fix: add the
param to the wrapper signature + pass through.

### #2 Phase 7.3 left 3 application-layer Python self-loop guards

Phase 7.3 dropped the DB CHECK constraints (`edges_no_self_loop_v2`,
`edges_check`) but left the Python-side `if from == to: raise` guards
in three places:
- `components.py:303` `create_edge` — "source_id and target_id must differ (no self-loops)"
- `components.py:443` `upsert_edge_outbound` — "from_component_id and to_component_id must differ (no self-loops)"
- `components.py:553` `bind_edge` — "Cannot bind to self (no self-loops)"

DEMO7 sme-cron blocked on all 3 paths despite Phase 7.3 being marked
shipped. The migration succeeded but the public tool surface looked
the same as before — Phase 7.3 was effectively a no-op. All 3 raises
removed, replaced with `# Phase 7.3` comments. New tests
(`test_create_edge_accepts_self_loop`, `test_upsert_edge_outbound_
accepts_self_loop`, `test_bind_edge_accepts_self_target`) prevent
regression.

### #6 vector_search lean projection

Pre-7.4.4, `_VALID_TABLES` queries used `SELECT c.*` which inlined
the full 1024-d embedding vector + heavy JSONB blobs
(`component_doc_md`, `source_slice`, `metadata`, `evidence`,
`context`) on every result row. DEMO7 SMEs reported single calls
returning **50–115KB** and blowing their tool-result token budgets,
forcing `jq` workarounds across the board (sme-auth-1, sme-auth-2,
sme-cron all flagged it).

New projection per table — agents get just enough to triage matches,
then call `get_*(id)` for full detail (search-then-fetch pattern):

| Table | Returned columns |
|---|---|
| components | id, canonical_name, display_name, component_type, status, similarity |
| attributions | id, component_id, plane, resource_type, identifier, confidence, similarity |
| unresolved | id, found_in_component_id, reference_type, reference_value, resolved, similarity |
| edges | id, from_component_id, to_component_id, edge_type, identifier, confidence, similarity |
| catalogs | id, component_id, kind, identifier, confidence, similarity |

Test asserts forbidden fields `{embedding, component_doc_md,
source_slice, metadata}` do NOT leak.

### Files touched

- `src/cartograph_mcp/server.py` (spawn_child wrapper)
- `src/cartograph_mcp/tools/components.py` (3 self-loop guards dropped)
- `src/cartograph_mcp/tools/search.py` (lean projections × 5)
- `tests/mcp_tools/test_self_loops.py` (+ 3 tool-path tests)
- `tests/mcp_tools/test_vector_search.py` (+ no-leak assertion)

**Verified:** 83/83 across vector_search + self_loops + flows + catalogs + mutation green.

---

## Phase 7.4.5: get_my_catalogs DISTINCT + summary uniform-int + SME prompt cleanup ✅

**Shipped 2026-04-26.** Single commit `9b94805`. Closes 3 pre-existing bugs DEMO7 surfaced.

### #3 get_my_catalogs (and 3 sibling tools) returned duplicates post-merge

`get_my_catalogs`, `get_my_catalog_callers`, `get_unmatched_callers`,
`get_orphan_catalogs` all `JOIN resource_component_agents rca ON
rca.component_id = c.component_id`. After `absorb_agent` re-points
the target's RCA rows to the survivor, the survivor can have N RCA
rows pointing at the same component (one per inherited resource).
The JOIN multiplied each catalog by N. DEMO7 sme-f9bde48a saw 12
rows for 6 distinct catalogs after absorbing sme-d3c8a998 — diagnosed
in resolver insight `d4c923dc`.

Fix: switch all 4 queries from `JOIN ... rca` to `WHERE EXISTS
(SELECT 1 FROM resource_component_agents WHERE rca.agent_id = %s)` —
short-circuits, no fan-out, each catalog returned exactly once.
Regression test (`test_get_my_catalogs_no_duplicates_when_multiple_
rca_rows`) reproduces the multi-RCA shape and asserts no duplicates.

### #4 get_action_items_summary pydantic crash

Summary returned a mixed-type dict (6 ints + `proxied: list[dict]`).
The MCP client's pydantic inferred `dict[str, int]` from the int
siblings then crashed on the list with `Input should be a valid
integer [type=int_type, input_value=[], input_type=list]`. DEMO7
resolver hit this on **every wake**. Phase 4.1.1 + 4.2.1 attempts to
fix via untyped return annotation didn't hold.

Real fix: make the summary a uniform `dict[str, int]`. `proxied` is
now `proxied_count: int`; the rich per-proxy breakdown
(proxy_agent_id, deactivation_reason, depth, item lists) lives on
`get_action_items_detail` — call it when `proxied_count > 0`.
Wrapper declares `-> dict[str, int]` explicitly. Regression test
(`test_summary_response_uniform_int_shape`) asserts every value is
an int and `proxied` is NOT in the response.

**Wire-shape change (note for callers):**

Pre-7.4.5 summary:
```json
{ "tasks_pending": 1, "proxied": [{"proxy_agent_id": "...", ...}] }
```

Post-7.4.5 summary:
```json
{ "tasks_pending": 1, "proxied_count": 1 }
```

Per-proxy detail moved to `get_action_items_detail.proxied`.

### #8 SME prompt stale catalog references

`sme.py` materialisation hygiene cycle still pointed SMEs at the
deprecated `upsert_edge_catalog` instead of the Phase 7.4 noun-form
`upsert_catalog`. Multiple DEMO7 SMEs flagged the inconsistency.
Updated:
- Hygiene cycle (line ~165): point at `upsert_catalog` + the new
  `get_unmatched_callers` tool that surfaces the missing-catalog
  case directly.
- Edge Discovery section: added Phase 7.3 self-loop note + Phase
  7.4.2 "use upsert_catalog, not the deprecated wrapper" note.

### Stale tests inverted

Two tests asserted the now-removed Phase-7.3 guards:
- `test_create_edge_self_loop_rejected` → `_accepted`
- `test_outbound_self_loop_refused` → `_accepted`

### Files touched

- `src/cartograph_mcp/tools/catalogs.py` (4 EXISTS rewrites)
- `src/cartograph_mcp/tools/action_items.py` (summary reshape)
- `src/cartograph_mcp/server.py` (summary wrapper return type)
- `src/agent_management/agent_types/sme.py` (hygiene + edge discovery prompt)
- `tests/mcp_tools/test_action_items_proxy.py` (uniform-shape regression)
- `tests/mcp_tools/test_catalogs.py` (multi-RCA dedup regression)
- `tests/mcp_tools/test_components.py` + `test_edges_phase39.py` (invert stale self-loop tests)

**Verified:** 498 / 500 mcp_tools+admin_ui tests green (2 pre-existing failures unrelated).

---

## Phase 7.4.7: per-type model + admin UI plane source + agent-row plane symbols + workspace-as-memory ✅

**Shipped 2026-04-27.** Multiple iterations on `feat/trigger-manager-cartograh-mcp`. Closes a cluster of DEMO7 follow-ups + a plane-semantics correction that surfaced from a real-data run.

### Per-type Claude model + reasoning effort

Pre-7.4.7, every spawned `claude -p` subprocess inherited the user's default model (Opus 4.7 1M context) regardless of agent type — heavy reasoning model used for high-volume iterator listing AND singleton resolver decisions alike. Wasteful at scale.

- New fields on `AgentTypeConfig` (`src/agent_management/agent_types/base.py`): `model: str = "claude-sonnet-4-6"` and `effort: str | None = None`.
- Per-type defaults in `{orchestrator,resolver}.py::build_config`: `model="claude-opus-4-6"`, `effort="medium"` (singleton coordination + user chat).
- Per-type defaults in `{iterator,sme}.py::build_config`: `model="claude-sonnet-4-6"`, no effort flag (high-volume).
- `agent_manager.py::185-193` cmd list now appends `--model <id>` unconditionally and `--effort <level>` when set.
- System prompt rebuilt fresh per spawn from disk → no agent_manager restart needed when prompt files change.

### Admin UI: graph node planes from RCA→resources, not attributions

Pre-7.4.7, three SQL callsites in `src/admin_ui/server.py` (`/api/components`, `/api/component/{id}/drilldown`, `/api/graph`) computed `planes[]` from `ARRAY_AGG(DISTINCT attributions.plane)`. This conflated DISCOVERY plane (where evidence was found) with CATEGORICAL plane (what plane the component lives on). Per the 2026-04-27 admin broadcast, `attributions.plane` is the discovery plane — a github SME finding a hostname tags `plane='github'` even when the hostname feels deploy-y. The graph then mis-colored components.

Fix: all three callsites now `LEFT JOIN resource_component_agents rca ON rca.component_id = c.id LEFT JOIN resources r ON r.id = rca.resource_id` and aggregate `r.plane`. The plane filter EXISTS sub-query also flipped. Front-end `PLANE_COLORS` rendering unchanged — only data source changed.

### Admin UI: agent-row plane symbols (G/C/T/D/F)

`/api/agents` SQL extended to a 2-level CTE returning `resource_planes text[]` per agent (RCA→resources.plane). `_renderAgentRow` in `app.js` reads `agent.resource_planes` and renders one tiny pill per plane next to the component tag:

| Letter | Plane | Color |
|---|---|---|
| G | github | teal `#0d9488` |
| C | cloud | pink `#db2777` |
| T | telemetry | purple `#9333ea` |
| D | deploy | blue `#2563eb` |
| F | config | orange `#ea580c` (avoids C collision) |

New `.plane-sym` CSS class — monospace 9px bold pill. Cache-bust v=58 → v=59.

### SME prompt: workspace as private memory + pre-merge detail capture

New `== WORKSPACE: PRE-MERGE DETAIL CAPTURE ==` block in `sme.py` immediately below YOUR WORKSPACE. Doctrine: database holds the WHAT, workspace holds the WHY. Three-step pre-absorb capture:

1. Save handoff QC verbatim → `./handoffs/<absorbed_id>.md`.
2. Snapshot absorbed component's DB state (get_component + get_attributions + get_component_edges) → JSON files in `./handoffs/`.
3. Append narrative entry to `./MERGE_LOG.md` per merge: their name, reason, key evidence, new code paths inherited, follow-ups for next wake.

For splits: optional `./split_briefing.md` in survivor's own workspace summarising what was carved out + why.

### SME prompt: code-repo plane: git clone is mandatory

New `== CODE-REPO PLANE: GIT CLONE IS MANDATORY ==` block in `sme.py`. Spells out:
1. Mandatory first-action sequence on every fresh wake — check for existing clone, pull if present, clone fresh if not (token via `get_secret(plane='github', key='github_token')`).
2. Failure path — `raise_blocker` immediately on clone failure; never fall back to API-only metadata (produces hollow components).
3. ANALYSIS DEPTH per decision type: NORMAL MATERIALISATION (walk tree, grep per-stack, cite file_path:line), MERGE EVALUATION (re-read repo slice on every nomination), SPLIT NOMINATION (prove boundary in code BEFORE nominating), POST-MERGE/POST-SPLIT REFRESH (mutation cascade only moves existing rows; SME must discover NEW evidence in newly-owned/trimmed code on next wake).

### SME prompt: §2.8 mass infusion (see PROMPT-ENHANCEMENTS.md §2.8)

ATTRIBUTION vs EDGE rule, attribution global-uniqueness rule, plane=DISCOVERY rule, INBOUND/OUTBOUND grep catalog, DANGLING-EDGE-pair rule, STEP 4 explicit pseudocode, MATERIALISATION COMPLETION CHECKLIST, ONGOING-not-one-off consolidation rule, EXTERNAL MCP ONBOARDING, BULK MCP TACTIC, SLEEP rewrite (LAST RESORT, 300–600s MAX), WAKE BUDGET rule.

### Iterator prompt

ACCESS PRECHECK (first wake on a plane), SEND_BROADCAST ACL note (you cannot call it; propose via chat), BULK MCP TACTIC, SLEEP rewrite.

### Orchestrator prompt

SEND_BROADCAST self-policing (never delegate), CREDENTIALS-via-chat → put_secret pattern, SLEEP rewrite.

### Resolver prompt

vector_search-as-bulk-fetch evidence-triangulation note (companion to §3.10 — no `get_attributions_bulk` tool exists yet; use vector_search where it expresses the query).

### Base mission

CHAT-ADDRESSED-TO-YOU block — read full unacked queue before acting on any single chat (admin sometimes mis-addresses + follows up with "stop stop").

### Files touched

- `src/admin_ui/server.py` — 3 SQL callsites for planes + `/api/agents` 2-level CTE for resource_planes.
- `src/admin_ui/static/app.js` — `_renderAgentRow` plane-symbol render + cache-bust constants.
- `src/admin_ui/static/style.css` — new `.plane-sym` class.
- `src/admin_ui/static/index.html` — cache-bust v=58 → v=59.
- `src/agent_management/agent_types/base.py` — `model` + `effort` fields + CHAT-ADDRESSED-TO-YOU block.
- `src/agent_management/agent_types/sme.py` — code-repo clone block, workspace-as-memory block, §2.8 infusion.
- `src/agent_management/agent_types/iterator.py` — access precheck, send_broadcast ACL, bulk MCP tactic, sleep rewrite.
- `src/agent_management/agent_types/orchestrator.py` — send_broadcast self-policing, credentials via chat, sleep rewrite.
- `src/agent_management/agent_types/resolver.py` — vector_search bulk-fetch hint.
- `src/agent_management/agent_manager.py` — `--model` + `--effort` CLI flags.
- `docs/PROMPT-ENHANCEMENTS.md` — backlog tracking doc (§2 shipped, §3 open gaps, §4 operational nudges + broadcast log + chat log + insights).

**Effort:** L (multi-day, multi-session work). All five `.py` prompt files compile clean; admin_ui server restarted to pick up SQL changes.

---

## Phase 7.4.8: WebGL GPU memory leak fix + browser-side crash logging ✅

**Shipped 2026-04-27.** Closes the recurring "graph canvas keeps crashing after a few minutes; have to fully quit Chrome to reset WebGL" symptom. User reported `chrome://gpu` showing GPU process crash count = 11 (Chrome 147) and = 5 (Chrome Canary 149) — same `Exit code 5` signature on both, ruling out browser-version specifics.

### Root cause (confirmed via app.js audit)

`makeNodeMesh()` in `app.js` allocated fresh `THREE.Geometry` + `THREE.MeshLambertMaterial` PER NODE PER `graphData()` invocation. Each refresh / tab switch / hover-driven `refreshGraphVisuals()` discarded the prior nodes from 3d-force-graph but never disposed the underlying GPU buffers (`.dispose()` was never called). After ~30 refreshes with 50 nodes, ~1500 leaked GPU resources accumulated. Eventually Chrome's GPU process OOMs and gets killed → `Exit code 5` → restart → leak compounds → after 11 crashes Chrome blocklists WebGL until app restart. The "non-existent mailbox" errors in the log are the GPU process trying to reference SharedImages destroyed when the prior context died.

### Fix #1: shared geometry + material caches

`app.js` gains module-scope `_geomCache` + `_matCache` (Map, capped at 256 entries each, keyed on `(type, quantised-size)` and `(color, isMuted)` respectively). `makeNodeMesh` now consults the caches. Net effect: GPU buffer count is bounded by distinct `(type, size, color)` tuples (~10–30 in practice), regardless of node count or refresh count.

### Fix #2: WebGL context-lost / restored handlers

New `_installWebGLContextHooks(canvasEl)` adds listeners on the inner `<canvas>` element 3d-force-graph mounts:

- `webglcontextlost` → `preventDefault()` (so Chrome attempts restore), POST `/api/clientlog` with snapshot + cache stats, dispose caches, tear down `graphInstance`, render an amber message in the canvas surface so the user can see what happened.
- `webglcontextrestored` → POST clientlog + re-init the graph cleanly.

### Fix #3: `/api/clientlog` endpoint + browser.log

New POST endpoint in `src/admin_ui/server.py` appends one JSON line per client event to `/tmp/cartograph-logs/browser.log`. Captures: `window.error`, `unhandledrejection`, `webglcontextlost`, `webglcontextrestored`, `beforeunload`. Best-effort write — never throws back to the client. Each line: `{server_ts} {client_ts} {event} {detail_json}` for `grep webglcontextlost /tmp/cartograph-logs/browser.log`.

### Server-side log piping (operational improvement)

All four cartograph processes (cartograph_mcp, trigger_management, agent_management `main`, admin_ui) now run as background shells under the active Claude Code session with stdout/stderr piped to `/tmp/cartograph-logs/{mcp,triggers,agents,admin_ui}.log` via `python -u -m <module> > /tmp/cartograph-logs/<file>.log 2>&1`. The `-u` flag makes Python unbuffered → logs flush in real-time. Combined with `browser.log`, that's five greppable log files for in-flight debugging. Restart sequence documented in `POST-COMPACTION-RECOLLECTION.md §2`.

### Files touched

- `src/admin_ui/static/app.js` — geometry/material caches, dispose helper, WebGL context hooks, client-log POST helpers.
- `src/admin_ui/server.py` — `/api/clientlog` endpoint + `_append_client_log` helper.
- `src/admin_ui/static/index.html` — cache-bust v=59 → v=60.

**Effort:** S (~1 hour). Verified with smoke-test POST landing in `browser.log`.

---

## Phase 7.4.9: Communications tab — decommissioned-agent surfacing + plane symbols + component-name filter ✅

**Shipped 2026-04-27.** Single commit `4237393` (bundled). Closes the
"Communications tab implicitly only showed active agents" gap (it
didn't, but the FE participant datalists only suggested active agents,
and `_componentLabelForAgent` lost the 📦 pill on decommissioned).

- Backend `/api/communications` gains optional `participant_component`
  query param: filters rows where either side's agent owns a component
  whose `canonical_name` OR `display_name` matches `ILIKE %query%`.
  Includes decommissioned agents/components for historical traffic.
- Frontend: dual fetch in `fetchAgents` — user-facing list (respects
  `showDecommissioned` toggle) plus `_allAgentsById` Map containing
  ALL agents always. Comms row lookups (`_componentLabelForAgent`,
  `_planeSymbolsForAgent`) read from the all-agents cache so
  decommissioned agents on historical Communications rows still get
  their 📦 + plane symbols (with `.component-tag-decom` strikethrough
  styling for visual distinction).
- Plane-symbol pills (G/C/T/D/F) now also render on Communications
  rows next to from + to. Same palette as chat sidebar.
- Participant datalists include decommissioned agents.
- New "Component name" filter input in the Participant section of the
  filter bar.

**Files:** `admin_ui/server.py`, `admin_ui/static/{app.js,index.html,style.css}`. Cache-bust v=60→v=61.

---

## Phase 7.4.10: Graph crash root-cause fix v2 — RAF pause + null-deref + idempotent ctx-lost ✅

**Shipped 2026-04-27.** Single commit `4237393`. The 7.4.8 GPU-leak fix
helped but didn't eliminate Chrome GPU crashes — `browser.log` captured
9 `webglcontextlost` events on URLs OTHER than `/graph`
(`/chat/sme-22a64543`, `/entities`, `/catalog`). Real root cause:
3d-force-graph kept its `requestAnimationFrame` render loop alive even
when the Graph view was hidden via `display:none`. Every frame
allocated link materials / arrow heads / particle systems for 597
edges + 37 nodes — even while the user was on a different tab.
After ~30 minutes of background rendering, GPU process OOMs → Exit
code 5.

- `switchTab()` now calls `graphInstance.pauseAnimation()` on tab
  leave and `resumeAnimation()` on entry. Eliminates the offscreen
  GPU burn entirely.
- `visibilitychange` listener: pauses when whole browser tab is
  backgrounded.
- Wheel-handler null-deref fix (`!graphInstance.camera` would throw
  TypeError when graphInstance had been nulled by a prior
  context-lost teardown — `browser.log` captured 153 of these). Fixed
  by short-circuiting on `!graphInstance` first.
- Idempotent context-lost handler: `_ctxLostHandled` flag prevents
  duplicate disposal when both inner `<canvas>` and outer container
  fire `webglcontextlost` (the `geomCacheSize: 0` second-fires we saw
  in the log). Reset on successful restore.

**Files:** `admin_ui/static/app.js`. Cache-bust v=61→v=62.

---

## Phase 7.4.11: delete_edge MCP tool + SME identifier normalisation rule + post-merge EDGE DEDUP step ✅

**Shipped 2026-04-27.** Three commits: `72b4a93` (delete_edge tool),
`84b0941` (SME prompt update), `bc69c23` (PROMPT-ENHANCEMENTS §2.9
log).

### Symptom

sme-7f4a958a (feeds-aggregator-v2-aurora) ended up with two edges for
the same Aurora master after absorbing a github SME:

| edge | discovered via | identifier |
|---|---|---|
| `0236cd0d` | telemetry plane | `feeds-aggregator-v2-aurora-master.dream11.local` |
| `8f9c82ad` | github plane (transferred to me on absorb) | `feeds-aggregator-v2-aurora-master.dream11.local/FeedsAggregatorV2` |

Violated the holistic-edge invariant ("one row per call/query, multi-
source metadata accumulates — never multiple rows"). SME flagged it +
filed insight `db290ec3`.

### Root causes (two)

1. **Prompt gap:** holistic-edge invariant requires identifier byte-
   identical across SMEs from different planes. My prompt said
   "metadata accumulates" but never told SMEs to NORMALISE the
   identifier across planes BEFORE writing. Telemetry surfaces bare
   hostname; github surfaces `host/dbname` (JDBC URL). Both correct
   in isolation; the gap was the missing normalisation contract.
2. **Tool gap:** no `delete_edge(agent_id, edge_id)` tool existed.
   The full edge-write surface was create_edge (legacy shim),
   upsert_edge_outbound, bind_edge, transfer_edges (mutation-scoped).
   None remove rows. decommission_component cascades them on full
   component teardown, but nothing for "this single duplicate is wrong."

### Fix #1 — `delete_edge` MCP tool (commit `72b4a93`)

- Owner-scoped: caller must own `from_component_id` (the row's
  caller-side owner).
- Catalog rows (from IS NULL — pre-Phase-7.4 remnants) refuse with
  `reason='catalog_not_supported'`.
- Idempotent: deleting non-existent edge_id returns
  `{deleted: False, reason: 'not_found'}` rather than raising.
- Cascade: `flows.outgoing_edge_id` has ON DELETE CASCADE — flows
  anchored on the edge are removed atomically. Cascaded count
  reported in the response for telemetry.
- Tool count: 85 → 86.

### Fix #2 — SME prompt: IDENTIFIER NORMALISATION rule (STEP 3) + post-merge EDGE DEDUP step (commit `84b0941`)

New IDENTIFIER NORMALISATION block in STEP 3 (CRITICAL — edges are
HOLISTIC). Concrete normalisation per identifier class:
- DB hosts: drop `/dbname` suffix; DB name → `metadata.db_name`.
- HTTP endpoints: lowercase host, drop trailing slashes + query
  strings; templatise path params (`/users/{{id}}`).
- Kafka topics / SQS queues: bare name only.
- Heuristic: "would another SME observing this same dep from a
  different plane write the SAME identifier string?" If no,
  normalise more.
- Tiebreak rule: write the LEANER form (what telemetry naturally
  surfaces); put richer details in metadata.

New EDGE DEDUP — MANDATORY step in the post-merge / post-split
refresh block. Concrete pseudocode walking inherited edges, finding
(target, edge_type) pairs with different identifiers, picking
canonical, merging metadata via `upsert_edge_outbound`, then
`delete_edge` on the duplicate.

### Hot-fix in same commit

Smoke-testing the prompt template caught a stray single-brace
`'/v1/users/{id}'` in the URL example I added — Python's
`str.format()` would have crashed with `KeyError: 'id'` on every SME
spawn (same class as the earlier `{get,post}` bug). Doubled to
`'/v1/users/{{id}}'`. Going forward: every prompt edit must run a
`SYSTEM_PROMPT_TEMPLATE.format(plane='x', resource_id='y')` smoke
test before commit.

**Files:** `cartograph_mcp/{server.py,tools/components.py}`,
`agent_management/agent_types/sme.py`,
`docs/PROMPT-ENHANCEMENTS.md`.

**Effort:** S (~1 hour).

---

## Phase 7.4.12: Bulk MCP write tools (Round 2 #3 of token optimisation) ✅

**Shipped 2026-04-29.** Single commit `716554d`. Adds 3 atomic-with-pre-validation bulk write tools targeting the highest-frequency same-tool streaks observed in real agent runs:

- `upsert_attributions_bulk(agent_id, component_id, attributions[])` — sme-22a64543 had 11× streak.
- `upsert_catalogs_bulk(agent_id, component_id, catalogs[])` — sme-22a64543 had 13×, 8×, 8×, 8× streaks.
- `upsert_edges_outbound_bulk(agent_id, edges[])` — sme-22a64543 had 7× streak.

### Atomic-with-pre-validation pattern

1. Pre-validate every row server-side BEFORE opening transaction (plane/kind/identifier required, confidence 0..1, agent owns from_component_id, etc.)
2. If any row fails pre-check → return per-row errors map, write NOTHING:
   ```
   {"committed": False, "applied": 0, "errors": {<row_idx>: <reason>}}
   ```
3. If all rows pass → atomic transaction with N upserts:
   ```
   {"committed": True, "applied": N, "rows": [...]}
   ```
4. For idempotent UPSERT operations: per-row results report `{inserted: bool, updated: bool}` — no failures from key collisions (the U in upsert handles them as success-with-update).

### Cross-component conflict handling (upsert_attributions_bulk)

If any row's `(plane, resource_type, identifier)` already belongs to a DIFFERENT component → reject the WHOLE batch with per-row error pointing at the conflicting component_id. Consolidation is the right path for cross-component reassignment; bulk write isn't.

### Mixed bound/dangling (upsert_edges_outbound_bulk)

Each row's `to_component_id` independently determines ON CONFLICT routing (bound on `(from, to, type, identifier)` vs dangling on `(from, type, identifier) WHERE to IS NULL`). Self-loops permitted (Phase 7.3).

### Single-row tools updated

`upsert_attribution`, `upsert_catalog`, `upsert_edge_outbound` doc-strings now point at the bulk variant for ≥3 same-component writes.

### Tool count

86 → 89. Verify via `grep "tools registered" /tmp/cartograph-logs/mcp.log`.

**Files:** `cartograph_mcp/{server.py, tools/components.py, tools/catalogs.py}`. Companion: SME prompt's BULK CALLS DECISION LADDER (Phase 7.4.12 prompt update — commit `135bc57` + hot-fix `5e969a1` for brace-escape) directs SMEs to use the bulk variant before falling back to parallel tool_use blocks or Python-script bypass.

**Effort:** M (~3 hrs incl. tests + smoke).

---

## Phase 7.4.13: Pre-injected action items in invocation prompt (Round 2 #5) ✅

**Shipped 2026-04-29.** Single commit `08c58d1`. Replaces the agent's first-turn `get_action_items_summary` + `get_action_items_detail` round-trips with a snapshot pre-computed by `agent_manager` at spawn time + embedded in the invocation USER message (NOT system_prompt — system_prompt must remain byte-identical for cache hits).

### Mechanism

- `agent_manager._build_action_items_snapshot(agent_id, agent_type)` runs ONE SQL query aggregating pending counts:
  - consolidations_pending, tasks_pending, clarifications_pending
  - unacked_chats, unacked_broadcasts
  - terminal_pending_ack
  - proxied_count
- Mirrors `get_action_items_summary`'s logic but as direct DB query (saves the MCP round-trip too).
- `_GENERIC_INVOCATION_PROMPT_TEMPLATE` gains an `== ACTION ITEMS SNAPSHOT (at <ts>) ==` block with formatted counts inline.
- `invoke_agent()` calls the helper before spawning `claude -p`; if it fails, falls back gracefully with "snapshot unavailable" note.

### Cache safety

CRITICAL: snapshot text varies per wake → goes in USER message (the `-p` prompt arg). System prompt (`--system-prompt config.system_prompt`) remains byte-identical → still hits 1h-extended cache.

### Workflow note

Updated invocation template tells the agent: "Use `get_action_items_detail` only if you need full row contents the snapshot didn't include (e.g. message bodies, blocker_detail)." Agent CAN still fetch fresh detail mid-wake if it suspects drift — just doesn't do it unconditionally on every wake.

### Companion to PostToolUse notification hook

The existing `notify.py` PostToolUse hook stays — it serves a DIFFERENT purpose (mid-session live interrupts on new admin/orchestrator chats, not first-turn summary). The two are complementary: pre-injection covers the wake-time snapshot; the hook covers mid-wake new arrivals.

### Estimated saving

3-5% of total spend (1-2 round-trips per wake × 247 wakes lifetime).

**Files:** `agent_management/agent_manager.py`. **Effort:** S (~30 LOC).

---

## Phase 7.4.14: Wake debouncing 5-min (Round 3 #6 of token optimisation) ✅

**Shipped 2026-04-29.** Single commit `8d1a7d3`. Coalesces drip-fed action items into one wake instead of N small wakes (each previously re-paying the 16k-token cached system-prompt read).

### Schema (idempotent migration)

```sql
ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS first_pending_at TIMESTAMPTZ;
```

In `shared/migrations.py` adjacent to the Phase 2.5 sleep_until column. No backfill needed; column starts NULL and gets stamped on first scanner observation.

### Trigger loop changes (`trigger_management/trigger_loop.py`)

- New `WAKE_DEBOUNCE_SECONDS = 300` constant.
- `get_idle_agents()` returns `first_pending_at` so caller can compare.
- New `stamp_first_pending(agent_id)` — idempotent, sets the column on first sighting.
- New `clear_first_pending(agent_id)` — clears when no items pending.
- New `has_debounce_override(agent_id)` — bypass conditions:
  - Pending admin chat (always immediate; admin chat is the auto-wake-from-sleep signal).
  - Agent is `mutation_assigned_to` on a state=M consolidation (mid-mutation must not be delayed).
- `run_once()` flow per agent:
  1. Skip agents with no pending items (clear stale stamp if any).
  2. Stamp `first_pending_at` on first sighting.
  3. If override fires → wake immediately.
  4. If `first_pending_at + 5min > now()` → still in debounce window, skip this cycle. Wake on a later cycle.

### Agent manager changes (`agent_management/{db.py, agent_manager.py}`)

- New `db.clear_first_pending(agent_id)` — called from `invoke_agent` on yield. Trigger scanner re-stamps when it next sees pending items, starting a fresh 5-min window per cycle.

### Trade-off

- Routine work (peer consolidation responses, broadcasts, non-admin chats from orch) sees up to 5-min latency between event arrival and agent wake.
- Mid-merge dance (state=M `mutation_assigned_to`) stays responsive via override.
- Admin chat stays instant via override.

### Estimated saving

10-15% of total spend (eliminates the "drip-fed wakes" pattern where 3 items arrive 30s apart and trigger 3 separate wakes).

### Tunability

`WAKE_DEBOUNCE_SECONDS` in `trigger_loop.py` — drop to 60s during interactive demo runs if 5 min proves too long for the work cadence.

**Files:** `shared/migrations.py`, `trigger_management/trigger_loop.py`, `agent_management/{db.py, agent_manager.py}`. **Effort:** M (~120 LOC + migration).

---

## Phase 7.4.12 [LEGACY NUMBERING]: Parallel tool calls — undo "Call tools sequentially" rule ✅

**Shipped 2026-04-27.** Two commits: `5b3144d` (the rule change),
`c7f5fd5` (terminology fix from "SDK" to "subprocess").

### Diagnostic

Across 6 representative agents (2,596 assistant messages from JSONL),
only 2 messages emitted >1 tool_use block — a parallel-tool-call rate
of **0.08%**. Distribution: ~40% of messages had 0 tool_uses
(reasoning / final response), ~60% had exactly 1, ~0% had 2+. Every
independent tool call (e.g. get_attributions for two components, or
five hygiene reads at the start of a wake) was being serialised into
its own /v1/messages round-trip — re-paying the cached system prompt
read each time.

### Root cause

My own prompt rules. orchestrator.py:212 had "Call tools sequentially,
not in parallel" and sme.py:1242 had "Call tools sequentially". Claude
obediently emitted at most one tool_use per turn.

The Anthropic API supports parallel tool calling natively. Claude Code's
agent loop has it enabled by default (`disable_parallel_tool_use=False`).
There is no infrastructure blocker — Cartograph's MCP server handles
concurrent tool calls fine; mutation paths that need ordering are
already enforced server-side via state-machine validation, not via the
prompt's "be sequential" rule.

### Fix

`base.py` — new shared `== BATCH + PARALLEL TOOL CALLS — PREFER THESE
OVER ONE-AT-A-TIME ==` block in MISSION_AND_VOCABULARY (visible to all
4 agent types). Lays out:
- Why it matters: per-turn round-trip cost.
- WHEN TO PARALLELISE: independent reads, hygiene sweeps, action-items
  triage, independent acks. Concrete examples (5-tool-batch on a
  merge investigation, 5-tool hygiene sweep at wake start).
- WHEN TO STAY SEQUENTIAL: dependent inputs, same-component writes,
  mutation transitions, split nominations.
- USE BULK VARIANTS: upsert_resources_bulk, bulk_spawn_smes,
  decommission_*_bulk, reject_resources_bulk.
- Pointer to BULK MCP TACTIC for >50 same-shape calls.

`orchestrator.py` + `sme.py` — replaced the "Call tools sequentially"
RULES line with a positive rule pointing at the BATCH + PARALLEL block
in shared mission, with the explicit caveats inlined.

`c7f5fd5` follow-up: replaced "the SDK runs them concurrently" / "Claude
Code SDK has it enabled" with "the agent loop running inside your
`claude -p` subprocess dispatches them concurrently" / "the `claude -p`
subprocess has it enabled by default" — the agent doesn't run in our
Python SDK; it runs in the spawned Claude Code subprocess.

### Mechanism (unchanged from API protocol)

When the LLM emits `[tool_use_A, tool_use_B, tool_use_C]` in one
assistant turn, the agent loop dispatches all 3 concurrently to the
MCP server, collects all 3 results, bundles them as ONE next user turn
(`content: [tool_result_A, tool_result_B, tool_result_C]`), and posts
that to /v1/messages. So 6 parallel tool calls = 2 LLM round-trips
(1 to dispatch, 1 to ingest). Compared to 6 serial tool calls = 7
round-trips. The LLM is NEVER re-invoked per result mid-batch — it
sees the bundled results in one turn.

### Estimated savings

~30-50% LLM round-trip reduction on tool-heavy phases (Materialisation,
hygiene, merge investigation). Translates to ~$200-400 saved over
project lifetime spend ($1,234), with steeper savings going forward
as prompt edits stabilise and cache hits stay above 90%.

**Files:** `agent_management/agent_types/{base,orchestrator,sme}.py`.

---

## Phase 7.4.6: doc + memory sync for 7.4.4 + 7.4.5 ✅

**Shipped 2026-04-26.** Single commit. Updates IMPLEMENTATION-PHASES
(adds 7.4.4 + 7.4.5 entries above), HLD (vector_search projection
note + spawn_child wrapper signature), AGENT-PROMPTS (catalog
hygiene cycle clarification + self-loop note), TRIGGER-MANAGEMENT
(action_items_summary uniform-int wire shape), POST-COMPACTION-
RECOLLECTION + memory `cartograph_state.md`. ONE-PAGER unchanged.
SCHEMA.md unchanged (no schema deltas in 7.4.4/7.4.5).

---

## Token Optimisation Plan (2026-04-29 — Round 1+ in flight)

Comprehensive token + cost reduction plan derived from forensic
analysis of the project-lifetime spend ($1,234.85 across 14,894 LLM
round-trips, 92.7% cache hit ratio). Sources: per-agent JSONL token
usage, tool-call sequence analysis, mcp_audit. Target: ~43% reduction
in lifetime spend (~$535 saved over current spend; ~$1,500-1,800
per active month at current scale).

### Diagnostic findings driving the plan

- **44.5% of assistant messages are TEXT-ONLY** (no tool_use blocks).
  Pure reasoning / "I'll start by..." preambles / post-hoc summaries.
  Each is a paid /v1/messages round-trip producing only output tokens.
- **0.08% of messages emit >1 tool_use block** (parallel tool calls).
  Pre-fix the prompt explicitly forbade parallelisation; now removed
  in commit `5b3144d`. Awaiting next-session sample to measure uptake.
- **27 consecutive same-tool runs of ≥3 in one representative SME's
  session** — 112 round-trips wasted on textbook batchable patterns
  (11× upsert_attribution, 13× upsert_catalog, etc.).
- **Per-type cost distribution:** orch ($220, 17% of total) + resolver
  ($94, 8%) + iterator ($92, 7%) + sme ($828, 67%). Orch+resolver are
  on opus-4-6; iterators have legacy opus-4-7 history; sme is on
  sonnet-4-6.
- **Per-agent average context size at last wake:** sme 97K tokens
  (max 192K), iterator 113K, orch 47K, resolver 42K.
- **Cache hit ratio per type:** resolver 94.7%, orch 94.4%, iterator
  92.8%, sme 90.4%. Caching IS working; ~$13K saved over the project
  lifetime vs the no-cache equivalent.
- **Output tokens (never cached):** 12.18M cumulative, ~$417 cost
  (34% of total spend). 67.7% on sonnet, 30.7% on opus-4-7 (Max
  default during early sessions), 1.5% on opus-4-6.

### Final plan — what shipped, in order (all 3 rounds done 2026-04-29)

| # | Lever | Effort | Saving | Round | Status / Commit |
|---|---|---|---:|---|---|
| 0 | Parallel tool calls (BATCH block) | done | 20-30% | — | ✅ shipped 2026-04-27 (`5b3144d`) |
| 1 | Concise output rule | XS | 8-10% | Round 1 | ✅ shipped (`c9bb733`) |
| 2 | Orch → sonnet (resolver STAYS opus-4-6) | XS | 10-14% | Round 1 | ✅ shipped (`53a6ef1`) |
| 3 | Top-3 bulk MCP write tools | M | 6-9% | Round 2 | ✅ shipped (`716554d`) — top tier (attributions/catalogs/edges_outbound). Middle + lower tiers (read bulks, bind/delete bulks) still queued. |
| 4 | BULK MCP TACTIC concrete examples (DECISION LADDER) | XS | 3-5% | Round 2 | ✅ shipped (`135bc57` + brace-fix `5e969a1`) |
| 5 | Pre-inject action items in user message | S | 3-5% | Round 2 | ✅ shipped (`08c58d1`) |
| 6 | Wake debouncing (5-min window) | M | 10-15% | Round 3 | ✅ shipped (`8d1a7d3`) |

**Compound math:**

```
Project lifetime spend:                   $1,234.85
After Round 1 (#1 + #2):                  ~$987     (-20%)
After Round 2 (#3 + #4 + #5):             ~$820     (-34%)
After Round 3 (#6):                       ~$700     (-43%)
```

**~43% reduction → ~$535 saved over project lifetime, ~$1,500-1,800/month at sustained scale.**

### Levers explicitly DROPPED (not shipping)

| Lever | Why dropped |
|---|---|
| Session reset at phase boundaries | Loses context continuity — user flagged as not-aligned. |
| Lane-cap quiet mode | Doesn't help in our system — bursts dominate, not quiet periods. The cap matters during contention, not during idle. |
| Haiku for trivial wakes / skip terminal-ack-only wakes | Kills agent's productive follow-up opportunity on those wakes. |
| Effort='low' on opus | Subsumed by #2 (orch → sonnet). Only resolver remains on opus and we want medium effort there. |
| Zero-state-change wake skip | Scanners are already state-driven (verified scanner SQL). Speculative against bugs we don't have. |
| Resolver → sonnet | Resolver does the highest-stakes reasoning (merge/split approve/reject, absorber-pick heuristic, pre-M conflict checks). Saving $94 there isn't worth a wrong-merge that corrupts graph state. STAY opus-4-6. |
| Generic `bulk_execute(calls=[...])` dispatcher | Re-implements native parallel tool calls, but stringly-typed (no schema introspection). Native parallel + per-shape bulk variants cover the same ground with better LLM ergonomics. |

### Round 1 — Concise output rule (#1)

New `== CONCISE OUTPUT — DON'T NARRATE WHAT YOU'RE ABOUT TO DO ==`
block in `base.py::MISSION_AND_VOCABULARY`. Defines:

- Default ≤2 sentences of explanation per assistant turn unless user
  asked for detail.
- NO preambles ("I'll start by...", "Let me first...", "Here's what
  I'm going to do...").
- NO post-hoc summaries unless they capture a NON-obvious result the
  next agent will need.
- Tool calls speak for themselves — don't restate what the tool will
  do; just call it.
- When responding to admin/orchestrator chat: state the answer + cite
  evidence; skip throat-clearing.
- When investigating during consolidation: cite file paths +
  identifiers, don't explain methodology.

**CRITICAL caveat — DO NOT compromise on identifiers, file paths,
hostnames, or specific data.** Trim only the english/jargon
regurgitation, never the concrete evidence. The savings come from
removing prose, not from being vague.

Reserve longer text for: `component_doc_md` (3-8 lines, structured),
consolidation message bodies (one paragraph max with concrete
evidence), `blocker_detail` (specific + actionable), `record_insight`
body (non-obvious finding).

**Saving:** 8-10% (output tokens are 34% of spend; cutting ~30% of
text-only narration messages × full output rate).

### Round 1 — Orch → sonnet (#2)

`orchestrator.py::build_config`: `model="claude-sonnet-4-6"` (down
from `claude-opus-4-6`). Drop `effort="medium"` (sonnet doesn't take
effort flag). **Resolver UNTOUCHED — stays on `claude-opus-4-6` with
`effort="medium"`.** Resolver does merge/split approve/reject decisions
which are too high-stakes to downgrade.

Rationale: orchestrator is mostly routing + monitoring + blocker
triage — high-volume but per-call lower-stakes than resolver. Sonnet
handles it fine; if quality drops we revert.

**Saving:** ~$155 over project lifetime (orch's $220 → ~$65 if equivalent
calls done on sonnet). ~10-14% of total.

### Round 2 — Bulk MCP tools (#3)

Three sub-batches:

**Top tier (high-frequency observed streaks):**
- `upsert_attributions_bulk(agent_id, component_id, attributions[])`
- `upsert_catalogs_bulk(agent_id, component_id, catalogs[])`
- `upsert_edges_outbound_bulk(agent_id, edges[])`
- `upsert_flows_bulk(agent_id, component_id, flows[])`
- `insert_unresolved_bulk(agent_id, items[])`
- `ack_broadcasts_bulk(agent_id, communication_ids[])`
- `ack_terminals_bulk(agent_id, items[{entity_type, entity_id}])`

**Middle tier (resolver triangulation gap, §3.10):**
- `get_attributions_bulk(component_ids[])`
- `get_components_bulk(ids[])`
- `get_component_edges_bulk(ids[])`

**Lower tier (companions to existing single-row tools):**
- `bind_edges_bulk(agent_id, bindings[])`
- `delete_edges_bulk(agent_id, edge_ids[])`

**Implementation pattern (atomic-with-pre-validation):**
1. Pre-validate every row server-side BEFORE opening transaction.
2. If any pre-check failed → return per-row errors map, write nothing,
   `{committed: False, errors: {...}, applied: 0}`.
3. If all clear → atomic transaction → commit → return per-row results
   `{committed: True, applied: N, rows: [...]}`.
4. For idempotent UPSERT operations: per-row results report
   `{inserted: bool, updated: bool}` — no failures from key collisions
   (the U in upsert handles them as success-with-update).

**EXPLICITLY NOT shipping:**
- `respond_*_bulk` for state-machine tools (per-row validation differs;
  partial-failure semantics nasty; low frequency; per-call is correct).
- `delete_attributions_bulk` / `delete_catalogs_bulk` (no single-row
  delete exists; soft-delete via metadata is current pattern).
- `vector_search_bulk` (each query needs its own embedding call;
  parallel tool_use blocks already cover this case).

**Saving:** 6-9% (token reduction on aggregate result-side input + less
output-side boilerplate; insurance against parallel-tool-call
under-adoption).

### Round 2 — BULK MCP TACTIC concrete examples in prompt (#4)

The `BULK MCP CALLS — PYTHON SCRIPT TACTIC` block already exists in
SME + iterator prompts but is abstract. Strengthen with worked
examples in priority order:

```
1. If a bulk MCP variant exists (upsert_attributions_bulk etc.),
   use that. One tool_use call. Atomic.
2. If no bulk variant, emit the N tool_use blocks as a parallel
   batch in ONE assistant turn (per BATCH + PARALLEL TOOL CALLS).
   Two LLM round-trips total.
3. For >50 same-shape calls (no bulk variant + parallel batch
   would blow the per-tool-arg token ceiling), Python script via
   Bash tool over JSON-RPC HTTP to localhost:8100/mcp. Zero LLM
   round-trips for the batch itself.
```

Plus one concrete example per pattern.

**Saving:** 3-5% (concretises the abstract guidance).

### Round 2 — Pre-inject action items (#5)

`agent_manager.py::invoke_agent`: before spawning `claude -p`, run a
single SQL query to fetch the agent's pending items
(consolidations + tasks + clarifications + unacked-chats +
unacked-broadcasts + terminal_pending + proxied summary). Serialise as
concise markdown into the **invocation USER MESSAGE** (NOT
system_prompt — modifying system_prompt would bust the prompt cache
key, cost 10× more than it saves).

Format:
```
== ACTION ITEMS SNAPSHOT (at <iso_ts>) ==
- consolidations_pending: 3   (B2: a1b2c3, R: d4e5f6)
- tasks_pending: 1            (BW: ghi789)
- unacked_chats: 2            (admin)
- unacked_broadcasts: 0
- terminal_pending_ack: 5
- proxied_count: 0

Use get_action_items_detail for full rows. Use get_action_items_*
tools mid-wake if you need fresher state.
```

Existing `get_agent_notifications` PostToolUse hook stays — it serves
a different purpose (mid-session live interrupts on new
admin/orchestrator chats). The pre-injection covers the **first-turn**
fetch; the hook covers **mid-session new arrivals**.

**Caveat — cache safety critical:** the snapshot text MUST go in the
invocation user message, not in `--system-prompt`. The system prompt
must remain byte-identical across wakes for cache hits. Test before
merge: smoke-fire a wake, confirm the response's
`cache_read_input_tokens > cache_creation_input_tokens` (i.e. the
system prompt still cached even with the new user message).

**Saving:** 3-5% (skip 1-2 round-trips per wake spent on
`get_action_items_summary` + `_detail` calls). Bonus: kills one of the
text-only narration turns where Claude says "Let me first check my
action items..."

### Round 3 — Wake debouncing 5-min (#6)

Schema migration: add `agent_runs.first_pending_at TIMESTAMPTZ NULL`.
Each scanner stamps it on first pending observation; clears when
agent yields with empty queue.

Trigger logic change: before flipping `trigger_lock=TRUE`, check
`now() - first_pending_at >= INTERVAL '5 minutes'`. EXEMPTIONS that
override the debounce:
- Pending admin chat (always immediate).
- Agent is `mutation_assigned_to` on a state=M consolidation
  (mid-mutation must not be delayed).
- An orchestrator on a phase transition (debatable — we may not
  enforce this, just exempt via the broader admin override).

Trade-off: routine work (peer consolidation responses, broadcasts,
non-admin chats from orch) sees up to 5-min latency between event
arrival and agent wake. Mid-merge dance (already-in-state-M) stays
responsive. Admin chat stays instant.

**Saving:** 10-15% (eliminates the "drip-fed wakes" pattern where 3
items arrive 30s apart and trigger 3 separate wakes paying the 16K
system-prompt re-read each time).

### Verification + ship discipline

After each Round, re-run the per-agent JSONL tool-call distribution
analysis and check:
- Round 1: text-only message ratio drops from 44.5% to <30%.
- Round 1: per-message output token average drops from ~1010 (sonnet)
  toward ~600.
- After parallel-tool-call uptake (already shipped): >1-tool-use rate
  climbs from 0.08% to ≥10% on tool-heavy phases.
- Round 2: 7-tool hygiene sweeps appear as 1 turn with 7 tool_uses
  (or 1 bulk tool_use if bulk variant available).
- Round 3: per-agent invocation count drops while tool-call total
  stays steady → drip-fed wakes coalesced.

Each lever ships as its own commit, individually revertable. Smoke-
test SYSTEM_PROMPT_TEMPLATE.format() before every prompt commit
(caught 2 brace bugs already: `{get,post}` and `{id}`).

---

## Phase 8: Token Optimisation + Gap Closings ✅

**SHIPPED 2026-04-29.** 7 commits on `feat/trigger-manager-cartograh-mcp`:
- `b77152b` — doc: Phase 8 plan
- `ce64584` — 8.1 notify.py flock fix
- `feb7c73` — 8.2 4 corrective delete singletons
- `d27587a` — 8.3 5 corrective delete bulks
- `9997a33` — 8.4 4 write bulks + unresolved idempotency + UNIQUE migration
- `65154ad` — 8.5 5 multi-component bulk reads
- `de20dbb` — 8.6 SME + Resolver prompt updates
- (8.7 doc sync + DEMO8 prompt — this commit)

Tool count: 89 → 107 (+18). 50 new tests across the 4 new test files all green.

**Scope.** A cohesive sub-phase that closes the SME corrective-action surface (4 deletes — attribution, catalog, flow, unresolved), finishes the Round-4 bulk surface (writes + reads + deletes), eliminates the `notify.py` PostToolUse hook race condition, and adds idempotency to `insert_unresolved`. **No schema changes beyond ONE idempotent migration** (UNIQUE on unresolved). Cascade behaviour for all deletes piggy-backs on existing FKs — no new cascade logic to design.

**Tool count: 89 → 107 (+18 tools).**

### 8.0 Motivation

After token-optimisation Rounds 1/2/3 shipped (2026-04-29), four open items remain that together form the next cohesive batch:

1. **`notify.py` PostToolUse race** — when an agent emits N parallel tool_use blocks, N hook subprocesses spawn concurrently and race past the 10-second rate-limit gate (all read the same stale marker). All N may emit duplicate `[NOTIFY]` strings into the next bundled user turn. ~50-100 token waste per parallel batch + agent-confusing duplicate noise. Fix: `fcntl.flock(LOCK_EX | LOCK_NB)` around the marker check — one lock-holder runs, siblings exit silent. ~10 LOC.

2. **No corrective deletes for the 4 row types SMEs own.** `delete_edge` shipped in Phase 7.4.11 covering post-merge edge dedup. The other three types (attribution, catalog, flow) plus `unresolved` have no owner-scoped delete path. SMEs hitting wrong-shape attributions (the `outbound_db_host` ATTRIBUTION-vs-EDGE confusion from sme-daa3b7b3 insight), deprecated catalog declarations, or wrong catalog→outgoing flow joins have no recovery path other than overwrite-with-superseded-flag.

3. **Round-4 bulk completion incomplete.** Round 2 shipped 3 bulk write tools (`upsert_attributions_bulk`, `upsert_catalogs_bulk`, `upsert_edges_outbound_bulk`). Five more were planned (`upsert_flows_bulk`, `insert_unresolved_bulk`, `ack_broadcasts_bulk`, `ack_terminals_bulk`, `delete_edges_bulk`) plus 5 read bulks for resolver triangulation (`get_attributions_bulk`, `get_components_bulk`, `get_component_edges_bulk`, `get_catalogs_bulk`, `get_flows_bulk`). Without these, the BULK CALLS DECISION LADDER's Rung 1 ("use a bulk variant if available") is incomplete for half the surface.

4. **`insert_unresolved` is not idempotent.** No UNIQUE constraint on `(found_in_component_id, reference_type, reference_value)` — repeated grep sweeps across SME wakes pile duplicate rows. Quiet bloat path; SMEs noticed but worked around it via `get_unresolved` + de-dup-then-insert dance.

### 8.1 Pre: notify.py flock fix (~10 LOC, 1 commit)

`src/agent_management/hooks/notify.py` gains `fcntl.flock(fd, LOCK_EX | LOCK_NB)` around the read-and-write of `.cartograph-notify-last`:

```python
import fcntl

def main() -> int:
    # ... arg parsing ...
    try:
        fd = os.open(MARKER_FILE, os.O_RDWR | os.O_CREAT, 0o600)
    except OSError:
        return 0
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            return 0  # sibling won the race; stay silent
        # ... existing rate-limit + MCP-call + notify body, all guarded ...
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(fd)
    return 0
```

**Effect:** N parallel hooks → 1 lock holder runs the MCP call + emits NOTIFY (if any), N-1 exit silent. No duplicate strings injected into the bundled user turn. POSIX-only (macOS + Linux fine; we don't target Windows).

**Verification:** spawn an SME on a tool-heavy phase, check `/tmp/cartograph-logs/agents.log` for `[NOTIFY]` strings — should appear at most once per parallel batch.

### 8.2 Sub-batch A: 4 delete singletons (~180 LOC, ~16 tests, 1 commit)

Mirror the Phase 7.4.11 `delete_edge` contract:

| Tool | Auth | Cascade (existing FK) | Idempotent return |
|---|---|---|---|
| `delete_attribution(agent_id, attribution_id, reason?)` | caller owns `component_id` of attribution | edges' `source_attr_id` / `target_attr_id` → SET NULL | `{deleted: False, reason: 'not_found'}` on missing id |
| `delete_catalog(agent_id, catalog_id, reason?)` | caller owns `component_id` of catalog | flows.incoming_catalog_id → CASCADE delete | same |
| `delete_flow(agent_id, flow_id, reason?)` | caller owns `component_id` of flow | leaf — no cascade | same |
| `delete_unresolved(agent_id, unresolved_id, reason?)` | caller owns `found_in_component_id` of unresolved | leaf — no cascade | same |

**Common shape:**
- Owner check via RCA: `WHERE component_id IN (SELECT component_id FROM resource_component_agents WHERE agent_id = ?)`
- Idempotent: missing id → `{deleted: False, id: <id>, reason: 'not_found'}` (no raise)
- Optional `reason: str | None` param — surfaces in `mcp_audit.args_hash` for forensic queries
- Returns `{deleted, id, cascaded_flows | severed_edge_pointers | none}` per tool

**Files:** `src/cartograph_mcp/tools/components.py` (delete_attribution, delete_flow, delete_unresolved), `src/cartograph_mcp/tools/catalogs.py` (delete_catalog). All four wrappers added to `src/cartograph_mcp/server.py`.

**Tests:** `tests/mcp_tools/test_deletes.py` — happy path × 4, owner refusal × 4, idempotent missing-id × 4, cascade verification × 4 (where applicable).

**Tool count:** 89 → 93.

### 8.3 Sub-batch B: 5 delete bulks (~300 LOC, ~20 tests, 1 commit)

Atomic-with-pre-validation pattern (mirror Round 2 Phase 7.4.12 bulks):

| Tool | Pattern |
|---|---|
| `delete_attributions_bulk(agent_id, attribution_ids[])` | per-row owner check; max 500; if any not owned, reject whole batch |
| `delete_catalogs_bulk(agent_id, catalog_ids[])` | same |
| `delete_flows_bulk(agent_id, flow_ids[])` | same |
| `delete_edges_bulk(agent_id, edge_ids[])` | same — companion to existing `delete_edge` |
| `delete_unresolved_bulk(agent_id, unresolved_ids[])` | same |

**Pre-validation:** check existence + owner-scope for every id. If any row not found → idempotent (skip). If any row not owned by caller → reject whole batch with per-row errors.

**Returns:**
```
{"committed": True, "applied": N, "rows": [{deleted, id, cascaded: N}]}
{"committed": False, "applied": 0, "errors": {<row_index>: <reason>}}
```

Atomic via `BEGIN ... DELETE ... DELETE ... COMMIT;` — Postgres FK cascades fire row-by-row inside the transaction.

**Files:** same as A. **Tests:** `tests/mcp_tools/test_delete_bulks.py` — atomic on success, atomic on partial-failure pre-validation, idempotent missing-ids mixed with valid ids.

**Tool count:** 93 → 98.

### 8.4 Sub-batch C: 4 write bulks + unresolved idempotency + UNIQUE migration (~400 LOC, ~20 tests, 1 commit)

#### Schema migration (idempotent ALTER)

```sql
-- src/shared/migrations.py — adjacent to existing unresolved index block
ALTER TABLE unresolved
  ADD CONSTRAINT IF NOT EXISTS unresolved_unique_per_ref
  UNIQUE (found_in_component_id, reference_type, reference_value);
```

Single UNIQUE constraint. Existing rows with duplicate keys would fail the migration — pre-flight check before the ALTER:
```sql
-- Defensive: if any duplicate exists, log + skip the constraint addition.
-- DEMO8 will run on a fresh DB so this won't fire; only matters when
-- restoring from snap-2026-04-29-pre-demo8.sql.
```
If duplicates exist in restored snapshot, the migration logs a warning and skips — operator can manually de-dup later via `DELETE FROM unresolved WHERE id NOT IN (SELECT MIN(id) FROM unresolved GROUP BY ...)` then re-run migrations.

#### Behaviour change — existing `insert_unresolved` becomes ON CONFLICT idempotent

```sql
INSERT INTO unresolved (found_in_component_id, reference_type, reference_value, ...)
VALUES (...)
ON CONFLICT (found_in_component_id, reference_type, reference_value) DO UPDATE
  SET context = EXCLUDED.context,
      embedding = EXCLUDED.embedding,
      attempts = unresolved.attempts + 1
RETURNING *;
```

Caller signature unchanged. Repeated grep sweeps now update-in-place (bumping `attempts`) instead of duplicating.

#### New write bulks

| Tool | Use case |
|---|---|
| `upsert_flows_bulk(agent_id, component_id, flows[])` | STEP 4 catalog→outgoing join produces N flows; currently fans to N calls |
| `insert_unresolved_bulk(agent_id, items[])` | Pairs with `upsert_edges_outbound_bulk` for the DANGLING-EDGE-pair rule (each dangling needs both an unresolved + a dangling edge). `items[i] = {found_in_component_id, reference_type, reference_value, context?}`. Idempotent on the triple per the new ON CONFLICT. |
| `ack_broadcasts_bulk(agent_id, communication_ids[])` | Bulk ack queued broadcasts in one round-trip |
| `ack_terminals_bulk(agent_id, items[{entity_type, entity_id}])` | Bulk ack queued terminal entities |

Same atomic-with-pre-validation pattern as sub-batches A/B.

**Files:** `src/shared/migrations.py` (UNIQUE), `src/cartograph_mcp/tools/components.py` (insert_unresolved ON CONFLICT, insert_unresolved_bulk, upsert_flows_bulk), `src/cartograph_mcp/tools/broadcast.py` (ack_broadcasts_bulk), `src/cartograph_mcp/tools/terminal_acks.py` (ack_terminals_bulk), `src/cartograph_mcp/server.py` (4 wrappers).

**Tests:** `tests/mcp_tools/test_write_bulks.py` — atomic pre-validation, idempotency, max-500 limit, owner refusal. `tests/mcp_tools/test_unresolved_idempotency.py` — repeat-insert bumps attempts not duplicates.

**Tool count:** 98 → 102.

### 8.5 Sub-batch D: 5 read bulks (~250 LOC, ~20 tests, 1 commit)

Multi-component reads for resolver triangulation + hygiene sweeps. All open to active agents (no SME gate — these are pure reads).

| Tool | Returns |
|---|---|
| `get_attributions_bulk(agent_id, component_ids[])` | `dict[component_id_str, list[attribution_row]]` |
| `get_components_bulk(agent_id, component_ids[])` | `dict[component_id_str, component_row]` |
| `get_component_edges_bulk(agent_id, component_ids[])` | `dict[component_id_str, {incoming_bound, incoming_catalog, outgoing_bound, outgoing_dangling}]` |
| `get_catalogs_bulk(agent_id, component_ids[])` | `dict[component_id_str, list[catalog_row]]` |
| `get_flows_bulk(agent_id, component_ids[])` | `dict[component_id_str, list[flow_row]]` |

Implementation: one `WHERE component_id = ANY(%s::uuid[])` query per tool, results bucketed by component_id in Python. Max 500 ids per call.

**Files:** `src/cartograph_mcp/tools/components.py` (4 of them), `src/cartograph_mcp/tools/catalogs.py` (get_catalogs_bulk), `src/cartograph_mcp/server.py` (5 wrappers).

**Tests:** `tests/mcp_tools/test_read_bulks.py` — happy path × 5, empty input handling, missing-id silently omitted, max-500 limit.

**Tool count:** 102 → 107.

### 8.6 Sub-batch E: SME + Resolver prompt updates (~70 LOC, smoke test only, 1 commit)

#### SME prompt — new `== CORRECTIVE ACTIONS — DELETE WHEN YOU GET IT WRONG ==` block

> When you discover you wrote something wrong-shape, fix it cleanly:
>
> - **Wrong attribution** (e.g. you wrote `outbound_db_host` as own attribution, but the DB hostname is actually an EDGE to a separate component): `delete_attribution(your_id, attr_id, reason='wrong shape — converting to edge')` then write the correct edge via `upsert_edge_outbound` + `insert_unresolved`.
> - **Stale catalog** (deprecated endpoint, was wrong all along): `delete_catalog(your_id, cat_id, reason='deprecated since YYYY-MM')`. Dependent flows cascade automatically.
> - **Wrong flow** (you wired `catalog_A → edge_X` but the right join is `catalog_A → edge_Y`): `delete_flow(your_id, flow_id)` then upsert the correct one.
> - **Stale unresolved** (the ref turned out to be a typo / not actually a dependency): `delete_unresolved(your_id, unresolved_id, reason='typo' | 'not_a_dep')`.
>
> All four are owner-scoped (caller must own the component the row belongs to) and idempotent. Use the bulk variants (`delete_*_bulk`) for batch cleanup.

#### SME prompt — BULK CALLS DECISION LADDER refresh

Add the new bulks to RUNG 1:
- Reads: `get_attributions_bulk`, `get_components_bulk`, `get_component_edges_bulk`, `get_catalogs_bulk`, `get_flows_bulk` (multi-component triangulation)
- Writes: `upsert_flows_bulk`, `insert_unresolved_bulk`, `ack_broadcasts_bulk`, `ack_terminals_bulk`
- Deletes: `delete_attributions_bulk`, `delete_catalogs_bulk`, `delete_flows_bulk`, `delete_edges_bulk`, `delete_unresolved_bulk`

#### Resolver prompt — multi-component triangulation hint

> When verifying merge evidence across N candidates, prefer `get_attributions_bulk(component_ids)` + `get_catalogs_bulk(component_ids)` + `get_component_edges_bulk(component_ids)` over per-candidate loops. One round-trip vs N.

**Smoke test (mandatory before commit):**
```
python3 -c "
import sys; sys.path.insert(0, 'src')
from agent_management.agent_types import sme, resolver
print('sme:', len(sme.SYSTEM_PROMPT_TEMPLATE.format(plane='github', resource_id='test')))
print('resolver:', len(resolver.SYSTEM_PROMPT))
"
```

**Files:** `src/agent_management/agent_types/sme.py`, `src/agent_management/agent_types/resolver.py`.

### 8.7 Sub-batch F: Final doc sync + DEMO8 prompt (~200 LOC docs, no code, 1 commit)

#### Doc sync (mechanical updates across 7 docs)

- **HLD.md** §2.5 + §9 — tool matrix updated (89 → 107, all 18 new tools listed with auth + cascade notes).
- **SCHEMA.md** — note the new UNIQUE constraint on unresolved + behaviour change to `insert_unresolved`.
- **TRIGGER-MANAGEMENT.md** §3 — new tool contracts (the 4 deletes + bulks). Cascade tables.
- **AGENT-PROMPTS.md** — note the corrective-action surface; reference §8.6 prompt updates.
- **IMPLEMENTATION-PHASES.md** — mark Phase 8 shipped (this file gets the per-sub-batch commit hashes).
- **POST-COMPACTION-RECOLLECTION.md** — refresh §6 tool surface (89 → 107), §11 (mark soft-delete plan replaced by hard-delete shipped), §16 re-hydration count.
- **PROMPT-ENHANCEMENTS.md** — promote §3 entries that were closed by Phase 8 (delete-as-corrective-action) to §2 with commit hash.

#### DEMO8 prompt (`docs/oorch-test-prompt-demo8`)

Comprehensive superset of DEMO7. Covers everything DEMO7 did **plus** everything shipped since:
- DEMO7 phases: catalogs first-class, flows reference catalogs, self-loops, pre-merge handoff, terminal acks, proxy inheritance, mutation lifecycle, evidence ladder, in-flight learning, one-merge-ripens, pre-M conflict check, mcp_audit, vector_search lean projection, get_my_catalogs no-dup, summary uniform-int.
- New since DEMO7: `delete_edge` + identifier normalisation (Phase 7.4.11), 3 Round-2 bulk writes (Phase 7.4.12), BULK CALLS DECISION LADDER, parallel tool calls (BATCH block), concise output, pre-injected action items (Phase 7.4.13), wake debouncing 5-min (Phase 7.4.14), per-type model + reasoning effort (Phase 7.4.7), graph WebGL fixes (7.4.8 + 7.4.10).
- New in Phase 8: 4 delete singletons + 5 delete bulks (corrective actions), 4 write bulks, 5 read bulks (resolver triangulation), `insert_unresolved` idempotency, notify.py flock fix.

Tag conventions: `[DEMO8-PHASE-N]`, `[DEMO8-OK]`, `[DEMO8-FAIL]`, `[DEMO8-BUG]`, `[DEMO8-NOTE]`, `[DEMO8-RESULT]`.

### 8.8 Sub-phase ordering + commit cadence

```
8.1  flock fix (notify.py)                  ← Pre, ship FIRST
8.2  4 delete singletons                     ← cohesive corrective surface
8.3  5 delete bulks                          ← bulks of singletons
8.4  4 write bulks + unresolved idempotency  ← schema migration runs on MCP boot
8.5  5 read bulks                            ← resolver triangulation
8.6  prompt updates (SME + resolver)         ← teaches agents the new tools
8.7  doc sync + DEMO8 prompt                 ← ship-ready
```

Each sub-batch = its own commit + push. Restart MCP server between 8.1 and 8.2 so the migration in 8.4 runs cleanly. Restart agent_manager after 8.6 (system_prompt rebuild from disk per spawn — strictly not needed but cleaner).

### 8.9 Test budget

| Sub-batch | New tests | Modified |
|---|---|---|
| 8.1 flock fix | 1 (concurrent-call dedup) | 0 |
| 8.2 delete singletons | ~16 | 0 |
| 8.3 delete bulks | ~20 | 0 |
| 8.4 write bulks + unresolved | ~20 (incl. idempotency) | 1 (existing test_components covers insert_unresolved) |
| 8.5 read bulks | ~20 | 0 |
| 8.6 prompt updates | smoke only | 0 |

Total: ~77 new + ~1 modified. Target final test count: ~590 (from ~510).

### 8.10 Tool surface delta

| Sub-batch | Tools added | Cumulative |
|---|---|---|
| 8.1 | 0 | 89 |
| 8.2 | 4 (delete_attribution, delete_catalog, delete_flow, delete_unresolved) | 93 |
| 8.3 | 5 (4 bulks of 8.2 + delete_edges_bulk) | 98 |
| 8.4 | 4 (upsert_flows_bulk, insert_unresolved_bulk, ack_broadcasts_bulk, ack_terminals_bulk) | 102 |
| 8.5 | 5 (5 read bulks) | 107 |
| 8.6 | 0 | 107 |
| 8.7 | 0 | 107 |

Verify after 8.5: `grep "tools registered" /tmp/cartograph-logs/mcp.log | tail -1` → 107 tools.

### 8.11 Schema delta summary

- `+UNIQUE (found_in_component_id, reference_type, reference_value)` on `unresolved`
- `insert_unresolved` becomes ON CONFLICT idempotent (behaviour, not schema)

No other schema changes.

### 8.12 Why this is the right scope

- **Closes the 4-row-type corrective gap** (delete_attribution / catalog / flow / unresolved) — the user's primary ask.
- **Hard delete with existing FK cascades** — no new cascade logic; Postgres handles it. No read-site filter audit (every read already filters on `WHERE deleted_at IS NULL` was the soft-delete tax we avoided).
- **Audit trail preserved via `mcp_audit`** — every delete already captured (agent_id, tool_name, args_hash, timestamp). No second audit channel needed.
- **Finishes Round 4 bulks** so DEMO8 exercises the complete bulk surface (write + read + delete in 5 categories).
- **Closes `insert_unresolved` quiet bloat** with a one-line schema migration + ON CONFLICT shim.
- **Eliminates parallel-tool-call NOTIFY noise** with a 10-LOC flock fix.
- **No schema invasive changes** — purely additive tool surface + one idempotent UNIQUE migration.
- **Half-day to full-day of work, 7 commits, +18 tools, +77 tests.**

---

## Phase 9: Round 5 token-opt — mcp_call_batch + caveman output style ✅

**Status (2026-04-30):** SHIPPED. 4 commits on `feat/trigger-manager-cartograh-mcp`:
- `b587055` — 9.0 plan
- `801c577` — 9.1 mcp_call_batch implementation (108th tool, 18 new tests, all green)
- `236e80a` — 9.2 caveman output style + per-wake reminder
- (this commit) — 9.3 doc sync pass

Tool count: 107 → 108. Closes the two unrealised levers from DEMO8 token-savings gap analysis (PROMPT-ENHANCEMENTS §7.1 + §7.2).

DEMO8 measured 0/851 assistant messages emitted >1 tool_use block — Claude Code's agent loop disables parallel tool_use internally, no CLI/settings/env override exists. And the Round 1 #1 concise output rule barely shifted the text-only-message ratio (44.5% pre → 46.5% post). Phase 9 attacks both at the architecture layer rather than via further prompt nudges.

### 9.0 Scope

Two levers:
1. **mcp_call_batch (108th tool)** — server-side parallel dispatcher for heterogeneous batches. Recovers the 12-18% token saving lost to Claude Code's disabled native-parallel.
2. **Caveman output style** — telegraphic English block at the TOP of `MISSION_AND_VOCABULARY` + per-wake user-message reminder. Recovers the 8-12% saving the buried concise rule didn't deliver.

Out of scope for Phase 9 (parked):
- §7.3 WD-owner-wake (existing 5-min debounce already bounds the cost — verified during user review).
- §7.4 auto-handoff clarification on resolver M-transition (good-to-have, not urgent).
- §7.5 pre-compute transfer ids before split nomination (drift risk on concurrent consolidations — premature optimisation).

### 9.1 mcp_call_batch (108th tool)

**Design.** The agent calls ONE `mcp_call_batch` tool with a list of sub-calls. Server-side, the wrapper validates → dispatches each sub-call concurrently via `concurrent.futures.ThreadPoolExecutor` → collects all results (success + per-call errors) → returns one structured response. The LLM sees ONE round-trip instead of N.

**Signature:**
```python
mcp_call_batch(
    agent_id: str,
    calls: list[dict],   # [{tool: str, args: dict}, ...]
) -> dict
```

Returns:
```json
{
  "results": [
    {"idx": 0, "tool": "get_my_catalogs", "ok": true, "result": {...}},
    {"idx": 1, "tool": "get_unmatched_callers", "ok": false, "error": "..."}
  ]
}
```

**Rules (server-enforced):**
- `agent_id` is the caller. If a sub-call's `args` doesn't carry its own `agent_id`, the wrapper injects the caller's id. If it carries a DIFFERENT id, the sub-call is allowed to keep it (proxy paths) — sub-call's own ownership/auth checks fire as normal.
- **No nesting.** If any sub-call's `tool == 'mcp_call_batch'`, refuse the whole batch with a clear error. Recursive batches have no benefit (always flattenable to one outer batch) and risk pathological recursion.
- **Cap:** 50 sub-calls per batch. Above that, the SME prompt directs use of bulk variants (`upsert_attributions_bulk` etc.) which take 500 rows each.
- **Collect-all, never strict-mode.** Matches native parallel tool_use semantics: each sub-call succeeds or fails on its own; one failure does NOT abort siblings.
- **Sub-call audit:** each sub-call still goes through its `@mcp.tool()`-registered wrapper, so the existing `audited` decorator records it in `mcp_audit`. The outer `mcp_call_batch` call is also audited as a single row.

**Concurrency:** `ThreadPoolExecutor(max_workers=min(8, len(calls)))`. Cartograph's MCP tools are sync Python over a psycopg connection pool — true I/O parallelism on DB operations. Mutation paths that need ordering already have server-side state-machine validation, so concurrent dispatch is safe.

**Decision Ladder (revised in SME prompt):**

```
RUNG 1 — Direct single call.        When: 1 tool, 1 row.
RUNG 2 — Bulk variant (atomic).     When: same tool × N rows (≥3). Examples:
                                          upsert_attributions_bulk, ack_terminals_bulk, ...
RUNG 3 — mcp_call_batch.            When: N different tools in one logical step
                                          (wake-start hygiene sweep, resolver triangulation,
                                          mid-investigation reads).
DON'T do native parallel tool_use blocks. Claude Code's agent loop serialises them today —
emitting [tool_use_A, tool_use_B] in one assistant turn does NOT save round-trips. Use
mcp_call_batch instead for mixed-tool batches.
```

(Explicit removal of the old "RUNG 2 — Native parallel tool_use blocks" + the "RUNG 3 / 4 — Python script via Bash" rungs. Native-parallel doesn't work; the Python-bash bypass is moot now that mcp_call_batch can chain `upsert_attributions_bulk` calls of 500 rows each through ONE batch.)

**Files:**
- `src/cartograph_mcp/tools/call_batch.py` — new (~80 LOC).
- `src/cartograph_mcp/server.py` — register `mcp_call_batch` + `_BATCH_DISPATCH` table mapping each tool name → its decorated function.
- `tests/mcp_tools/test_call_batch.py` — new tests.

**Tests (~12 new):**
- happy-path heterogeneous batch (3 different reads in one call).
- per-call error collection (one bad arg → that call returns error, others succeed).
- nesting refusal — `mcp_call_batch` inside calls[] rejects whole batch.
- empty batch → `{results: []}` (no error).
- max-50 cap.
- unknown tool name → error before dispatch.
- agent_id injection when sub-call omits it.
- agent_id preservation when sub-call carries different id (proxy path).
- audit: outer batch + every sub-call recorded in `mcp_audit`.
- bulk + read mix in one batch (e.g. `upsert_attributions_bulk` + `get_my_catalogs` together).
- exception during dispatch is caught and reported per-call (not raised to caller).
- ThreadPoolExecutor concurrency (call 4 sleep tools in parallel; total time < 4× single).

**Tool count:** 107 → 108.

### 9.2 Caveman output style

**Symptom (DEMO8):** 396/851 assistant messages were text-only (no tool_use), avg ~150-250 output tokens of pure English narration. Baseline 44.5% → post-fix 46.5% basically unchanged. The Round 1 #1 concise rule lives ~50% through the 76k-char system prompt and gets skimmed.

**Fix.** New `== OUTPUT FORMAT — CAVEMAN ENGLISH ==` block at the TOP of `MISSION_AND_VOCABULARY` (above everything else, including current intro paragraph). Telegraphic English — drop articles, conjunctions, most adverbs, all preambles. Keep nouns, verbs, identifiers, numbers verbatim.

**Scope (intentionally broad):**

| Context | Format | Why |
|---|---|---|
| Status reports / mid-task narration / acks | CAVEMAN | low-info |
| Tool-result reactions | CAVEMAN | low-info |
| Final assistant turn before yield | CAVEMAN | low-info |
| Consolidation message bodies (evidence) | CAVEMAN | other SMEs read it; trained on same prompt |
| `blocker_detail` | CAVEMAN | orch reads it; trained on same prompt |
| `record_insight.body` | CAVEMAN | admin scans Insights tab; tech-fluent reader |
| Admin chat REPLIES to user | CAVEMAN-LIGHT | user is tech-fluent; terse > verbose |
| `component_doc_md` | NORMAL ENGLISH | rendered in graph-viz hover popup for end-users browsing the graph |

Only `component_doc_md` retains normal English — every other path goes caveman.

**Hard caveat (carried verbatim from concise rule):** caveman trims English/preambles ONLY. NEVER compromise on identifiers, file paths, hostnames, IDs, numbers, hashes, version strings, error messages. The brevity comes from cutting prose, not data.

**Worked examples** (in the prompt):
```
VERBOSE: "I'll start by checking my action items, then process each one in turn.
          I just looked at task ee1ddfc7 and it's now in WD status."
CAVEMAN: "checked items. task ee1ddfc7 → WD."

VERBOSE: "Let me investigate the consolidation thread first. I'll read it,
          then look at the evidence both SMEs cited, then form my own opinion."
CAVEMAN: "reading thread. checking evidence A + B. forming view."

VERBOSE: "I confirmed via vector_search that catalog POST /payments/charge
          (1163c724) on payments-svc is a strong match for the caller's
          identifier. Binding the edge now."
CAVEMAN: "vector_search confirmed. catalog POST /payments/charge (1163c724)
          on payments-svc strong match. binding edge."
```

**Per-wake reminder.** A single line injected into the per-wake user message (alongside the existing Phase 7.4.13 `ACTION ITEMS SNAPSHOT` block):

```
== OUTPUT STYLE ==
Caveman English outside reserved long-text contexts (component_doc_md only).
Identifiers / paths / IDs verbatim. No preambles.
```

System prompt is cached + skimmed by the model after first wake; the user message is fresh every turn — that's where late instructions land hardest.

**Estimated saving:** 12-18% (broader scope than original 8-10% target — bodies of consolidation messages, blocker_detail, insights all caveman now).

**Files:**
- `src/agent_management/agent_types/base.py` — new CAVEMAN block at top of `MISSION_AND_VOCABULARY`.
- `src/agent_management/agent_manager.py` — extend the per-wake user-message template to include the OUTPUT STYLE reminder.
- Smoke test: `SYSTEM_PROMPT_TEMPLATE.format(plane='x', resource_id='y')` for SME + iterator (the templated ones); module import for orch + resolver.

**Tests:** prompt-only edits — manual verification via DEMO9. No new pytest test file.

### 9.3 Combined ceiling

| Phase | Lever | Saving |
|---|---|---:|
| Already realised (Phases 1-8) | Rounds 1-3 + Phase 8 | ~35-50% |
| **Phase 9.1** | mcp_call_batch | **+12-18%** |
| **Phase 9.2** | Caveman output style | **+12-18%** |
| **Phase 9 total** | | **~60-83% lifetime reduction** |

Above the original 43% target by a wide margin. DEMO9 (this branch's verification run) measures actuals.

### 9.4 Sub-phase ordering + commit cadence (SHIPPED)

```
9.0 plan                              ← b587055 doc-only
9.1 mcp_call_batch implementation     ← 801c577 code + 18 tests, all green
9.2 caveman + per-wake reminder       ← 236e80a base.py + agent_manager.py
9.3 doc sync pass                     ← (this commit)
9.4 DEMO9 prompt                      ← docs/oorch-test-prompt-demo9
                                        (next commit)
```

After 9.4 the user runs DEMO9 to verify in-the-wild behaviour: actual parallel-tool-call rate via mcp_call_batch usage in JSONL, text-only-message ratio shift, per-message output-token average shift.

### 9.5 What this is NOT solving

- Output volume on `component_doc_md` — those are intentionally human-facing and stay normal English.
- Native parallel tool_use — confirmed unfixable from our side; mcp_call_batch is the workaround.
- §7.3 / §7.4 / §7.5 — parked per user review.

---

## Phase 10: Search/Discovery + Embedding fix + Race guard + TEMP lock-step doctrine ✅

**Status (2026-05-04):** SHIPPED + tightened. Sub-commits:
- `8100a91` — 10.0 plan
- `2a62836` — 10.1 symmetric-nomination race guard (3 new tests, 32/32 consolidation suite green)
- `351c42c` — 10.1.1 atomic partial UNIQUE index (5 phase10 tests green; live `consolidations_pair_unique` index applied to dev DB)
- `773f4da` — 10.1.2 split-spawned children get dedicated workspaces (2 new tests; 46/46 mutation suite green; closes the parent-child cwd-share bug observed in real-data DB)
- `2d225db` — 10.2 component_doc_md embedding extension + backfill flag (3 new tests; live backfill of 8 dev components)
- `718f436` — 10.3 six deterministic search tools (30 new tests; tool count 108 → 114)
- `7052288` — 10.4 TEMP lock-step phase progression doctrine (prompt-only)
- `dcb7f3f` — 9.2-rewrite: caveman block restructured to mirror the upstream juliusbrussee/caveman skill (pattern formula + persistence clause + 3 intensity levels + auto-clarity carve-outs); per-wake reminder synced
- (this commit) — 10.5 doc sync pass

Tool count: 108 → 114.

Bundle of small lookup-correctness wins surfaced from DEMO9 review + post-DEMO8 architectural reflection. Four sub-batches; each is small enough to ship as one commit.

### 10.0 Scope

| Sub-batch | What | Effort | Tool count delta |
|---|---|---|---|
| **10.1** | Symmetric-nomination race guard in `nominate_consolidation` | XS | 0 |
| **10.2** | Add `component_doc_md` to `component_embed_text` + backfill | XS | 0 |
| **10.3** | 6 deterministic search tools (search_components / _attributions / _edges / _catalogs / _flows / _unresolved) | M | +6 (108 → 114) |
| **10.4** | TEMP lock-step phase progression doctrine in prompts | XS | 0 |

Out-of-scope (parked): full phase-state-machine enforcement at the DB layer (Phase 10.4 stays prompt-only — it's a coordination doctrine, not a hard contract). If the prompt doctrine proves insufficient, escalate to `agent_runs.phase` enforcement in a future phase.

---

### 10.1 Symmetric-nomination race guard

**Symptom (architectural review, 2026-05-04):** if SME-A nominates SME-B for merge at T0 and SME-B nominates SME-A at T0+ε, both INSERTs into `consolidations` succeed. No DB UNIQUE constraint, no application-layer pre-check. Result: 2 consolidation rows for the same logical pair, double work, double resolver review, occasional "second mutation silently fails post-decommission" if both reach M.

**Fix.** Add a pre-INSERT SELECT in `tools/consolidation.py::nominate_consolidation` (after the existing component-ownership checks, before the INSERT):

```python
existing = execute_one(
    """SELECT id FROM consolidations
        WHERE nomination_type = 'merge'
          AND status NOT IN ('D','F')
          AND ((component_a_id = %s AND component_b_id = %s)
            OR (component_a_id = %s AND component_b_id = %s))""",
    (component_a_id, component_b_id, component_b_id, component_a_id),
)
if existing:
    raise ValueError(
        f"Open consolidation between these components already exists "
        f"(id={existing['id']}). Respond on that thread instead of "
        f"nominating again."
    )
```

Skip for split nominations (no symmetry — split has only one party).

**Atomic guard (Phase 10.1.1, shipped 2026-05-04).** The pure SELECT-then-INSERT pattern from 10.1 has a microsecond race window — two transactions can both see no existing row and both insert. Closed via a partial UNIQUE index on a normalised pair (commit lands as 10.1.1):

```sql
CREATE UNIQUE INDEX consolidations_pair_unique
  ON consolidations (
    LEAST(component_a_id, component_b_id),
    GREATEST(component_a_id, component_b_id)
  )
  WHERE nomination_type = 'merge' AND status NOT IN ('D', 'F');
```

`LEAST`/`GREATEST` normalise the pair so A→B and B→A collide on the same key. Partial WHERE excludes terminal-status rows so re-nomination after rejection stays allowed. Restricted to merge (splits have no symmetry). Together with the 10.1 SELECT pre-check this is belt-and-suspenders — SELECT raises a clean error message in the common case, INDEX catches the microsecond race.

**Tests (~3 new in `tests/mcp_tools/test_consolidation.py`):**
- A→B nomination, then B→A → second raises with "already exists".
- A→B nomination → resolver rejects to F → B→A nomination now succeeds (terminal-status pair doesn't block).
- A→B split nomination → A→B merge nomination should still succeed (different `nomination_type`).

**Files:** `src/cartograph_mcp/tools/consolidation.py`, `tests/mcp_tools/test_consolidation.py`.

---

### 10.2 `component_doc_md` embedding extension

**Symptom (DEMO9 review, 2026-05-04):** vector_search on components clusters on short identity strings only — `f"{component_type}: {canonical_name} {display_name} {meta_json}"`. Real recall gap: query "auth service handling user verify" misses `canonical_name=fav2-api` even when its `component_doc_md` says exactly that.

**Fix.** Extend `shared/embedding.py::component_embed_text` to include `component_doc_md` (capped at 500 chars to keep embedding signal balanced):

```python
def component_embed_text(canonical_name, display_name, component_type,
                         metadata, component_doc_md):
    meta = json.dumps(metadata or {}, sort_keys=True)
    doc = (component_doc_md or "")[:500]
    return f"{component_type}: {canonical_name} {display_name} {doc} {meta}"
```

Update both call-sites in `tools/components.py::upsert_component` (line ~108 + the bulk variant if it embeds, though the bulk variant doesn't currently embed — components don't have a bulk write).

**Backfill.** Run `shared/embedding_backfill.py::backfill_all()` once after the change ships. Re-embeds existing component rows with the new text shape. ~50ms × N components, single CLI invocation. Idempotent.

**NOT changing:**
- `source_slice` — paths/files = structural references, not semantic content. Would dilute embed signal.
- Other tables (`attributions`, `edges`, `unresolved`, `catalogs`) — already identity-only by design. Short embed = better matching precision.

**Threshold caveat.** Existing cosine threshold of 0.75 may need re-calibration to ~0.70 post-fix — doc_md adds recall but slightly hurts precision. Measure on DEMO10 data before adjusting.

**Tests (~2 new in `tests/mcp_tools/test_component_doc_md.py`):**
- After `upsert_component(... component_doc_md='auth service ...')`, the embedding row carries doc-influenced content. Hard to assert directly without comparing vectors; instead assert via integration: vector_search('auth service') returns the component when its doc_md says auth-service even when canonical_name is unrelated.
- Cap test: 1500-char doc_md → embed-text built from first 500 chars only.

**Files:** `src/shared/embedding.py`, `src/cartograph_mcp/tools/components.py`, `tests/mcp_tools/test_component_doc_md.py`.

---

### 10.3 Six deterministic search tools

**Gap.** `vector_search` is currently the ONLY fuzzy/cross-component reader. Per-table read tools (`get_attributions`, `get_edges`, `get_my_catalogs`, etc.) all need a `component_id` first. There's no "find every component calling `GET /payments/charge`"-style query short of fanning out across all components or shelling to the admin UI's `/api/components?q=...`.

**Design.** 6 SQL-LIKE-based search tools. AND across columns; OR within a column via list. Plain string → exact match; `%`/`_`-bearing string → ILIKE.

```
search_components(agent_id,
    canonical_name_pattern? | display_name_pattern? | name_pattern?,
    component_type? | list,
    status?='active' | list,
    plane? | list,
) -> list[component_row]

search_attributions(agent_id,
    identifier_pattern?,
    plane? | list,
    resource_type? | list,
    component_id?,
) -> list[attribution_row]

search_edges(agent_id,
    identifier_pattern?,
    edge_type? | list,
    kind? in {'bound', 'catalog', 'dangling'} | list,
    from_component_id?,
    to_component_id?,
) -> list[edge_row]
# 'catalog' kind is historical (pre-Phase-7.4 from-NULL rows that
# migrated to the catalogs table) — kept as a kind filter for
# search completeness even though no live edge rows match.

search_catalogs(agent_id,
    identifier_pattern?,
    kind? | list,
    component_id?,
) -> list[catalog_row]

search_flows(agent_id,
    component_id?,
    incoming_catalog_id?,
    outgoing_edge_id?,
) -> list[flow_row]
# No string pattern field — flow rows have no human-readable identifier,
# only FK references. ID-based filtering only.

search_unresolved(agent_id,
    reference_value_pattern?,
    reference_type? | list,
    found_in_component_id?,
    only_unresolved=True,    # default — exclude resolved=TRUE rows
) -> list[unresolved_row]
```

**Shared SQL builder helper.** New `src/cartograph_mcp/tools/_search_helper.py`:
- `_pattern_clause(column, value)` → returns `(sql_fragment, param)` or `(None, None)` when value is None.
- Auto-detects exact vs LIKE: returns `f"{column} = %s"` for plain strings, `f"{column} ILIKE %s"` for `%`/`_`-bearing.
- `_in_clause(column, values)` → returns `(f"{column} IN ({placeholders})", values)` for list-valued filters.
- `assemble(filters: list[tuple[clause, params]]) -> (where_sql, params)` — joins with AND, drops Nones.
- Hard cap helper: appends `LIMIT 100` to all search SQL.
- Refusal helper: raise if every filter is None.

**Validation rules (server-enforced):**
- At least ONE filter must be non-None — else raise `ValueError("blank filter would dump entire table; narrow your filter")`.
- Cap 100 rows per call. Hitting the cap doesn't error — caller sees 100 rows + can narrow + re-call.
- `agent_id` validated against `agent_runs` (any active agent can call).
- Lean projection — same shape as `get_*_bulk` reads (id + identity columns + similarity-irrelevant fields). No embedding vectors, no JSONB blobs.

**For components specifically — name search ergonomics:**
- `name_pattern` is a convenience field that ILIKEs both `canonical_name` AND `display_name` (admin UI parity with the `q` param).
- `canonical_name_pattern` and `display_name_pattern` exist for explicit single-column targeting.
- Pass at most ONE of {`name_pattern`, `canonical_name_pattern`, `display_name_pattern`}; raise if more than one.

**Tool count:** 108 → 114.

**Tests (~40 new in `tests/mcp_tools/test_search_tools.py`):**
- Per-tool happy path with each filter dimension.
- Exact vs LIKE auto-detection.
- List-valued filter (OR within column).
- Multi-filter AND.
- Blank-filter refusal.
- Cap-100 enforcement.
- Cross-tool: a search returning a component_id can feed into bulk-reads on the other tables.

**Files:**
- `src/cartograph_mcp/tools/_search_helper.py` (new, shared SQL builder).
- `src/cartograph_mcp/tools/search.py` (extend — currently holds vector_search).
- `src/cartograph_mcp/server.py` (register 6 wrappers).
- `tests/mcp_tools/test_search_tools.py` (new).

**Effort:** M (~600 LOC across helper + 6 tool fns + 40 tests).

---

### 10.4 TEMP lock-step phase progression doctrine

**Why this exists (current scale rationale).** With 2-4 SMEs in a typical demo run, the cost of cross-phase confusion is real:
- SME-A binds an outbound edge to SME-B's component before consolidation has stabilised SME-B's identity → merge rewrites SME-B → SME-A's edge is stale → re-bind work on SME-A's next wake.
- SME-A starts post-mutation hygiene before all peer SMEs have finished materialisation → finds half-built peers, dangling everything, no progress.

The fix is voluntary phase coordination, not state-machine enforcement. Orchestrator broadcasts phase transitions; agents respect them by convention. If agents drift, orch nudges via chat. **TEMPORARY** — drop once self-pacing proves reliable at higher scale.

**Five phases:**

```
1. USER_DISCUSSION       admin↔orch onboarding, creds, scope.
2. ITERATION             iterators enumerate resources;
                         orch dispatches per-plane.
3. MATERIALISATION       SMEs hydrate own component:
                           - upsert_component + doc_md + source_slice
                           - exhaustive attributions
                           - own catalogs (what I expose)
                           - DANGLING-only outbound edges (to=NULL)
                             + insert_unresolved for the identifier
                           - flows tying own catalogs ↔ own danglings
                         Do NOT bind edges to peer components yet.
4. CONSOLIDATION_MUTATION  merge/split nominate, negotiate, resolver
                           review, mutation execute (absorb_agent /
                           spawn_child / cascades / handoff).
5. EDGE_DISCOVERY        graph is now stable, so:
                           - resolve unresolved refs against now-stable
                             component registry
                           - bind_edge danglings via cosine ladder
                           - cross-SME hygiene: get_unmatched_callers,
                             post-merge edge dedup via delete_edge,
                             stale-edge / stale-flow re-bind
```

**Broadcast contract.** Orchestrator emits a single broadcast at each transition:

```
[PHASE-END: <prev>] [PHASE-START: <next>]

Phase <next> begins. Stay within this phase's scope. See your
system prompt's TEMP PHASE-WISE LOCK-STEP block for what's allowed
and what's deferred.
```

Phase end heuristic — orch declares done when **most** (≥80%) of the phase's expected agents have finished their phase work + the remaining stragglers have either raised a blocker or are idle with no new work to pull. Stragglers carry over into the next phase if blocked — orch issues per-agent BW tasks to pull them along.

**Prompt addition (TEMP block in `base.py::MISSION_AND_VOCABULARY`):**

```
== TEMP: PHASE-WISE LOCK-STEP PROGRESSION ==
(May be removed once agent self-pacing proves reliable.)

The system runs in 5 sequential phases. Orchestrator announces
transitions via broadcast: [PHASE-END: <prev>] [PHASE-START: <next>].

Stay within the announced phase. If you receive an action item that
doesn't fit the current phase (e.g. a clarification asking you to
bind during MATERIALISATION), respond per the phase contract — record
as dangling, defer the binding to EDGE_DISCOVERY.

Phases:
  1. USER_DISCUSSION    admin↔orch only
  2. ITERATION          iterators enumerate; SMEs idle
  3. MATERIALISATION    SMEs hydrate OWN component
                         - own catalogs, own attributions
                         - outbound edges DANGLING ONLY (to=NULL)
                         - insert_unresolved for outbound identifiers
                         - flows on own catalogs ↔ own danglings
                         DO NOT bind to peer components yet.
  4. CONSOLIDATION_MUTATION  merges/splits negotiate + execute
  5. EDGE_DISCOVERY     resolve unresolveds, bind danglings,
                         cross-SME hygiene, post-merge edge dedup

Why: prevents wasted work where you bind to a peer that gets
merged/split mid-storm. By staying in your phase, you only do work
that's safe at that timing.

If unsure which phase is active, check your most recent unacked
broadcast — orch's [PHASE-START] is the source of truth.
```

**Orchestrator prompt addition** (`agent_types/orchestrator.py`) — phase-coordinator section telling orch to:
- monitor per-phase progress (count of agents idle with phase-work-done vs total)
- emit `[PHASE-END / PHASE-START]` broadcast (persistent=True, so future-spawned SMEs see it on first wake)
- keep stragglers via direct task dispatch rather than holding the whole storm

**Files:**
- `src/agent_management/agent_types/base.py` — TEMP block.
- `src/agent_management/agent_types/orchestrator.py` — phase-coordinator section.

**Effort:** XS (~80 LOC across two prompt files, smoke-test only).

**Removal contract:** when removed (future phase), update both prompt files to drop the TEMP block + the orch coordinator section. Doc-sync the change.

---

### 10.5 Sub-phase ordering + commit cadence (SHIPPED)

```
10.0 plan                                   ← 8100a91 doc-only
10.1 symmetric-nomination guard             ← 2a62836 code + 3 tests
10.2 component_doc_md embedding             ← 2d225db code + 3 tests + live backfill
10.3 six search tools                       ← 718f436 helper + 6 tools + 30 tests
10.4 TEMP lock-step doctrine                ← 7052288 prompt edits + smoke
10.5 doc sync pass                          ← (this commit)
10.6 DEMO10 prompt                          ← docs/oorch-test-prompt-demo10
                                              (next commit)
```

After 10.6 the user runs DEMO10 to verify in-the-wild behaviour: phase
broadcasts firing on transitions, search tool usage in JSONL,
embedding recall on doc_md-bearing components.

### 10.6 Tool surface delta

| Phase | Tools added | Cumulative |
|---|---|---|
| 10.1 | 0 | 108 |
| 10.2 | 0 | 108 |
| 10.3 | 6 (search_components, _attributions, _edges, _catalogs, _flows, _unresolved) | 114 |
| 10.4 | 0 | 114 |

Verify after 10.3: `grep "tools registered" /tmp/cartograph-logs/mcp.log | tail -1` → 114 tools.

### 10.6.5 Phase 10.1.2: Split-spawned child cwd isolation (added post-plan)

**Bug discovered post-shipping:** dev DB inspection of post-DEMO9 state revealed:

```
workspace_path                              | array_agg(agent_id)
workspaces/sme-3ed92c56                     | {sme-3ed92c56, sme-pay8a1b2}
```

`sme-pay8a1b2` was spawned via `spawn_child_agent` (split mutation) and inherited the parent's `workspace_path` verbatim. Pre-fix code in `tools/mutation.py::spawn_child_agent` step 3 was explicit about the design choice:

```python
# 3. Insert idle SME agent_runs row.
#    plane + workspace_path copied from parent for consistency.
parent_agent = execute_one(
    "SELECT plane, workspace_path FROM agent_runs WHERE agent_id = %s",
    (agent_id,),
)
execute_mutate(... workspace_path = parent_agent["workspace_path"] ...)
```

Real consequences:
- `notify.py` rate-limit marker (`./.cartograph-notify-last`) shared between parent + child. One agent's 10s NOTIFY gate blocks the other.
- Scratch files / `./handoffs/` / `./MERGE_LOG.md` / cloned repo all shared. They write into each other's notes.
- `.mcp.json` + `.claude/settings.json` never written for the child (parent's reused — works by accident).
- Concurrent invocations of parent + child race on file writes.

**Fix shape:**

1. Extract `provision_workspace(agent_id, agent_type, mcp_server_names)` method on `AgentManager` — same logic that was inline in `create_agent` (build path, `os.makedirs`, write `.mcp.json` + `.claude/settings.json`). `create_agent` now delegates to it. Idempotent on the directory + files.

2. `spawn_child_agent` gains an optional `agent_manager` kwarg. When passed (production via `server.py` wrapper passes `_agent_manager_for_spawn`), the child's `workspace_path` is provisioned FRESH via `provision_workspace`. When None (test contexts that don't wire an agent_manager), falls back to legacy parent-share with a logged warning. Server wrapper updated.

**Verified by tests (2 new in `test_mutation.py`):**

- `test_phase10_1_2_child_gets_dedicated_workspace` — end-to-end split with real `AgentManager` rooted at `tmp_path`. Asserts child's `workspace_path` differs from parent's, dir exists on disk, `.mcp.json` + `.claude/settings.json` both present.
- `test_phase10_1_2_no_agent_manager_falls_back_with_warning` — documents fallback behaviour stays parent-share when `agent_manager=None` (production never hits this path).

44/44 existing mutation tests stay green via the fallback path. 46/46 total mutation suite green post-fix.

**Workspace creation remains deterministic + orchestrator-driven.** The agent itself never creates its own workspace; `AgentManager` does it before spawning the `claude -p` subprocess. Both spawn paths (`create_agent` for initial spawns, `spawn_child_agent` for split children) now share the same `provision_workspace` method.

**Commit:** `773f4da`.

---

### 10.6.6 Phase 10.1.3: per-tool caller-id kwarg name-map in mcp_call_batch + create_edge docstring fix

**Bugs filed by agents during DEMO-MEGA (2026-05-04):**

`mcp_call_batch` (Phase 9.1) auto-injects the caller's identity into every sub-call's args dict so agents don't have to re-type their own id N times per batch. Pre-Phase-10.1.3 the inject was hard-coded to push under `agent_id`. Most tools accept `agent_id` as their caller-id kwarg, but 4 use semantically-meaningful role names — `from_agent_id` (chat / broadcast — caller is the sender), `owner_agent_id` (create_task — caller is the task owner), `survivor_id` (act_on_proxy_item — caller is the survivor of a merge). Inject pushed `agent_id=...` → tool TypeError'd on the unexpected kwarg → those 4 tools were uncallable inside batches.

5 separate insights filed by SMEs hitting the bug: `7ce4622c` (send_broadcast), `7c0bae81` (create_task), `4a220f29` (act_on_proxy_item), `f41f310e` (send_chat), `5e58b07d` (send_chat — same class as act_on_proxy_item).

Plus 1 docstring lie: `create_edge` MCP wrapper claimed "Refuses self-loops" — wrong since Phase 7.3 (DB CHECK dropped) + Phase 7.4.4 (Python guards dropped). Insight `192d84c4`.

**Two design options considered:**

1. **Rename the 4 tool params to `agent_id`.** Uniform but loses role semantics; would also need to chase prompt + doc references for every tool whose param appears in a worked example. Tested via working-tree changes; reverted.

2. **Per-tool caller-id kwarg name-map in `mcp_call_batch`** ✅ **shipped.** Tools keep their semantically-meaningful param names; the dispatcher consults a one-tool-per-line dict to choose which kwarg to inject under. Less invasive, preserves role names, and the same map can absorb future tools with non-standard caller-id params.

**Implementation:**

`src/cartograph_mcp/tools/call_batch.py` — new module-level dict + helper:

```python
_CALLER_KWARG_BY_TOOL: dict[str, str] = {
    "send_chat":            "from_agent_id",
    "send_broadcast":       "from_agent_id",
    "create_task":          "owner_agent_id",
    "act_on_proxy_item":    "survivor_id",
    "create_clarification": "asker_agent_id",  # same class as the 4 above; closed proactively
}

def _caller_kwarg_for(tool_name: str) -> str:
    return _CALLER_KWARG_BY_TOOL.get(tool_name, "agent_id")
```

The `_run_one` dispatcher inside `call_batch` now consults `_caller_kwarg_for(tool_name)` to choose the inject target instead of hard-coding `agent_id`. Idempotency / override semantics unchanged: if the sub-call already carries the right kwarg, no inject — proxy paths can still override.

**Includes `create_clarification`** — that's a 5th tool with the same shape (param `asker_agent_id`) that no DEMO-MEGA agent happened to batch during the run. Closed proactively; same map entry covers it.

**`create_edge` docstring fix** (independent of name-map): single-line edit in `src/cartograph_mcp/server.py:642-643`. Replace the false "Refuses self-loops" line with the truth ("Self-loops permitted (Phase 7.3 dropped DB CHECK; Phase 7.4.4 dropped Python guard)").

**Tests added (`tests/mcp_tools/test_call_batch.py`):**

11 new regression tests pinning the fix:

- `test_phase10_1_3_unit_caller_kwarg_lookup` — direct unit test on the name-map (catches future drift if a tool's public signature changes without map update).
- `test_phase10_1_3_unit_inject_pushes_to_right_kwarg` — synthetic dispatch test, no DB. Confirms inject reaches the right kwarg per tool.
- `test_phase10_1_3_unit_does_not_inject_when_kwarg_already_present` — pinned override semantics.
- 5 real-dispatch tests (one per affected tool) via the actual server registry: `send_chat`, `send_broadcast`, `create_task`, `act_on_proxy_item`, `create_clarification` all callable inside `mcp_call_batch`.
- `test_phase10_1_3_all_5_renamed_tools_in_one_batch` — the big one: 3 in one orch batch + 2 in one survivor batch, all ok=True.
- `test_phase10_1_3_create_edge_self_loop_succeeds_via_shim` — behavioural confirmation that the shim accepts a self-loop.
- `test_phase10_1_3_create_edge_docstring_no_longer_lies` — docstring grep regression.

29/29 tests in `test_call_batch.py` green.

**Touched files:** `src/cartograph_mcp/tools/call_batch.py` (name-map + inject), `src/cartograph_mcp/server.py` (docstring fix only — no signature changes), `tests/mcp_tools/test_call_batch.py` (11 new tests). Tool count unchanged: 114. No prompt or doc-sync ripples — public tool signatures stay identical to what agents already learned.

---

### 10.7 Schema delta

One additive index (Phase 10.1.1):

```sql
CREATE UNIQUE INDEX consolidations_pair_unique
    ON consolidations (
      LEAST(component_a_id, component_b_id),
      GREATEST(component_a_id, component_b_id)
    )
    WHERE nomination_type = 'merge' AND status NOT IN ('D', 'F');
```

Idempotent (`IF NOT EXISTS`) in `shared/migrations.py`. No table
schema changes, no data migration. All other Phase 10 work is pure
application-layer.

### 10.8 What this is NOT solving

- True microsecond race on symmetric nominations (would need partial UNIQUE index on normalised pair).
- Component-embedding precision drop after doc_md addition (caveat documented; measure post-DEMO10 + re-calibrate threshold if needed).
- Phase-state-machine enforcement at the DB layer (10.4 stays prompt-only; escalate later if voluntary doctrine fails).
- Per-phase cost analyzer (separate work, on the radar but not in Phase 10 scope).

---

## Phase 11+: Future phases (planned, not started)

**Phase 11 — Phase-flow completion** (orchestrator-driven sweeps):
- **Resolution phase orchestration** — wake config-SMEs to resolve `unresolved` table rows; re-run cosine ladder against now-consolidated component registry.
- **Edge Discovery phase orchestration** — dedicated bidirectional-validation pass + telemetry trace edge injection.
- **User Feedback phase** — admin UI workflows for "merge these two" / "missed this" / "this doesn't exist anymore" → orchestrator routes to the right SME(s).

**Phase 12 — Observability + cost controls** (HLD §11):
- Dashboard on `agent_runs`: token usage, phase progress, unresolved count, blocker count, B1/B2/R/M/MD/D/F counts.
- `max_turns` per agent per phase, embedding budget caps, consolidation max-rounds.

**Phase 13 — DM between agents** (HLD §11.2):
- Lighter-weight than consolidation for one-off SME↔SME clarifications.

**Phase 14 — Knowledge pool** (HLD §11.3):
- Shared facts table any agent can read/write ("all dream11 services use `{service}.dream11.local`").

**Phase 6 — Globe (sphere-constrained graph view)** is parked on `feat/globe-experimental` branch. Re-introduce by merging that branch when ready; doc sync brief lives at `docs/GLOBE-MERGE-BRIEF.md` on that branch.

---

## Open chores (do alongside any phase)

- **DEMO41 metadata purge** (`DELETE WHERE metadata->>'demo41'='true'`) — awaiting explicit say-so.
- **DEMO41 adversarial tests** — scope-gating (#26-28), stale hygiene (#29-30), admin UI HTTP (#33-35).
- **AGENT-PROMPTS.md §9 deferred** — evidence quality guidelines + per-type negative instruction sets.

---

## Parked indefinitely

- Communications-tab "via `<survivor>`" badge fix on broadcast rows — DO NOT TOUCH without explicit say-so.

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

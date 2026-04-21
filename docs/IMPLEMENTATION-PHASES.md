# Cartograph — Implementation Phases

**Status (2026-04-21):** Phase 0 ✅ complete · Phase 1 ✅ complete (incl. runtime-robustness hardening) · Phase 2–4 pending.
- 30 MCP tools registered · 86 tests passing (27 tasks, 34 resources, 17 admin_ui, 8 recovery).
- Services running: Postgres (docker), trigger manager, MCP server (:8100), admin UI (:8200), agent manager with orchestrator + resolver singletons.

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

**Agent lifecycle (`cartograph_mcp/server.py` via colocated AgentManager):**
- `create_agent(agent_id, new_agent_type, plane?, resource_id?)` — ORCHESTRATOR-only. Spawns iterator (plane) or SME (resource_id). Creates workspace, writes `.mcp.json`, inserts `agent_runs` row.
- `list_agents(agent_id)` — any agent can see all non-decommissioned agents.
- `reset_agent(agent_id, target_agent_id)` — ORCHESTRATOR-only override for permanently-errored agents (bypasses recovery attempt cap).

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

## Phase 2: Materialisation

Component discovery — SMEs analyse resources and build the component graph.

### MCP Tools Added
**Read:**
- `get_component(component_id)` — read any component. All agents can read all components.
- `get_attributions(component_id)` — read attributions for any component.
- `get_edges(component_id)` — read edges for any component (inbound + outbound: WHERE source_id=? OR target_id=?).
- `get_unresolved(component_id)` — read unresolved references for a component.
- `vector_search(query_text, table, limit)` — embed query_text via embedding client, search against table's embedding column using cosine similarity, return top N with similarity scores. Tables: components, attributions, unresolved, edges.

**Act:**
- `upsert_component(agent_id, component_data)` — INSERT or UPDATE component. Validate: agent owns this component via resource_component_agents (or new component → create RCA row). Auto-embed: `"{type}: {canonical_name} {display_name} {metadata}"`.
- `upsert_attribution(agent_id, component_id, attribution_data)` — INSERT or UPDATE attribution. Validate: agent owns component. Auto-embed: `"{resource_type}: {identifier}"`. Handle UNIQUE(plane, resource_type, identifier) via ON CONFLICT.
- `create_edge(agent_id, edge_data)` — INSERT edge. Validate: agent owns source component. CHECK source_id != target_id. Auto-embed: `"{edge_type}: {identifier}"`. Handle UNIQUE(source_id, target_id, edge_type, identifier).
- `insert_unresolved(agent_id, unresolved_data)` — INSERT unresolved reference. Auto-embed: `"{reference_type}: {reference_value}"`.
- `resolve_reference(agent_id, unresolved_id, resolved_to_component_id)` — SET resolved=TRUE, resolved_to_component_id=?. Optionally create edge.

### Trigger Manager Additions
- SME auto-spawn: when resource with status='pending' exists AND no agent assigned to it:
  - Create new SME agent in agent_runs (status='idle')
  - Create resource_component_agents entry (resource→placeholder component→new agent)
  - Set resource status='assigned'
  - Next trigger scan picks up the new idle agent with pending task
- Include component/attribution counts in action items if relevant

### Embedding Operations
- Components: embed on upsert_component
- Attributions: embed on upsert_attribution
- Edges: embed on create_edge
- Unresolved: embed on insert_unresolved
- Model: text-embedding-3-small, 1536 dims
- Lookup protocol: exact match first → vector fallback (>0.85 match conf=0.8, 0.7-0.85 hint as unresolved, <0.7 create new)

### Tables Activated
- components, attributions, edges, unresolved, resource_component_agents

---

## Phase 3: Consolidation

SMEs negotiate merges/splits. Resolver reviews and approves.

### MCP Tools Added
**Read:**
- `get_my_consolidations(agent_id)` — all consolidation rows where agent_a_id=agent_id OR agent_b_id=agent_id. Includes nomination_type, both conf scores, r_conf_score, status.
- `get_consolidation_thread(consolidation_id)` — all communications WHERE source_id=consolidation_id AND type='consolidation', paginated. Scoped: agent must be agent_a or agent_b (or resolver). Not involved → empty result.
- `get_my_clarifications(agent_id)` — `WHERE asker_agent_id=agent_id OR responder_agent_id=agent_id`.
- `get_clarification_thread(clarification_id)` — all communications WHERE source_id=clarification_id AND type='clarification', paginated. Scoped: only asker or responder.

**Act — Consolidation:**
- `nominate_consolidation(agent_id, component_a_id, component_b_id, type, confidence, message)` — INSERT consolidation (status=B2) + INSERT communication (type='consolidation'). Validate: only SMEs; agent owns component_a via RCA table.
- `respond_consolidation(agent_id, consolidation_id, confidence, message, new_status)` — UPDATE conf score + INSERT communication. Validate:
  - Agent is agent_a or agent_b
  - State actually changes
  - Nominated (agent_b) transitions: B2→B1, B2→R (only if r_conf IS NOT NULL)
  - Nominator (agent_a) transitions: B1→B2, B1→R (only if r_conf IS NOT NULL)
- `review_consolidation(agent_id, consolidation_id, r_confidence, message, new_status, mutation_assigned_to?)` — Resolver only. UPDATE r_conf_score + INSERT communication. Validate: agent_type='resolver'. Transitions:
  - R→B1 or R→B2 (needs more info)
  - R→F (reject)
  - R→M (approve — MUST set mutation_assigned_to. Merge: agent with more planes. Split: always agent_a.)

**Act — Clarification:**
- `create_clarification(asker_agent_id, responder_agent_id, question_message)` — INSERT clarification (status=B2) + INSERT communication (type='clarification').
- `respond_clarification(agent_id, clarification_id, message, new_status)` — UPDATE status + INSERT communication. Validate:
  - Agent is asker or responder
  - State actually changes
  - Responder transitions: B2→B1, B2→QR, B2→QC
  - Asker transitions: B1→B2, B1→QC, QC→CC, QC→B2, QR→CC

### Trigger Manager Additions
- Scan consolidations:
  - SME: `WHERE (agent_b_id=agent_id AND status='B2') OR (agent_a_id=agent_id AND status='B1')`
  - Resolver: `WHERE status IN ('R', 'MD')`
  - Mutation POC: `WHERE mutation_assigned_to=agent_id AND status='M'`
- Scan clarifications: `WHERE (asker_agent_id=agent_id AND status IN ('B1','QR','QC')) OR (responder_agent_id=agent_id AND status='B2')`
- Auto-transition: both a_conf_score > 0.85 AND b_conf_score > 0.85 AND r_conf_score IS NULL → SET status='R'
- Auto-reject: both a_conf_score < 0.3 AND b_conf_score < 0.3 → SET status='F'
- Include consolidation/clarification counts in action items

### Tables Activated
- consolidations, clarifications

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
- `transfer_attributions(from_component_id, to_component_id, attribution_ids[])` — UPDATE attributions SET component_id=to WHERE id IN (...). Re-embed BOTH components. Validate: agent owns from_component.
- `get_proxy_items(agent_id)` — SELECT * FROM proxy_items WHERE surviving_agent_id=agent_id. Returns all inherited items with type, item_id, status.
- `get_proxy_chats(agent_id, proxy_agent_id, page, limit)` — SELECT from communications WHERE (from_agent=proxy_agent_id OR to_agent=proxy_agent_id) AND type='chat', paginated. Validate: proxy_agent_id is a decommissioned agent absorbed by agent_id (check proxy_items).

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

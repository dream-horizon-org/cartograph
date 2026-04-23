# Cartograph — High-Level Design

---

## 1. System Overview

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                              CARTOGRAPH                                      │
│                                                                              │
│  ┌────────────────┐    ┌──────────────────┐    ┌─────────────────────────┐   │
│  │  AGENT RUNTIME  │    │ TRIGGER MANAGER  │    │     DATA LAYER          │   │
│  │                │    │                  │    │                         │   │
│  │ Agent Sessions │◄──►│ Watches tables   │◄──►│ PostgreSQL + pgvector   │   │
│  │ Invoke Engine  │    │ Locks + Priority │    │                         │   │
│  │ File Spaces    │    │ Batch scheduling │    │ Component graph tables  │   │
│  │ Heartbeat      │    │                  │    │ Agent infra tables      │   │
│  └───────┬────────┘    └──────────────────┘    │ Communication tables    │   │
│          │                                      │ Secrets                 │   │
│          │  READ-ONLY MCP SERVERS               └────────────┬────────────┘   │
│          ▼                                                   │               │
│  ┌──────────────────────────────┐                            │               │
│  │ GitHub │ AWS │ DD │ Consul   │────────────────────────────┘               │
│  │ (read) │(read)│(read)│(read) │   agents read sources, write only to DB   │
│  └──────────────────────────────┘                                            │
│                                                                              │
│  ┌──────────────────────────────┐                                            │
│  │       ADMIN INTERFACE        │   Chat with any agent, answer questions,   │
│  │                              │   broadcast feedback, view progress        │
│  └──────────────────────────────┘                                            │
└──────────────────────────────────────────────────────────────────────────────┘

Initial deployment assumption:
  • 1 Org/Client, all agents on the same machine
  • Shared runtime — installing a tool reflects for all agents
  • DB can be local or external
  • Agent architecture wraps Claude Code (reference implementation)
```

---

## 2. Agent Architecture

### 2.1 Agent Contract

Every agent is a **persistent, stateful process** with its own tools and workspace. It exposes a single interface: `invoke()`.

```
┌──────────────────────────────────────────┐
│              AGENT                        │
│                                          │
│  Identity:   agent_id (unique)           │
│  Session:    persistent across invokes   │
│  Workspace:  isolated file space         │
│  Tools:      scoped per agent type       │
│                                          │
│  Interface:                              │
│    invoke(prompt) → resumes session,     │
│                     executes work,       │
│                     yields control       │
│                     when done            │
│                                          │
│  On invoke:                              │
│    1. Session resumes (full context)     │
│    2. Receives phase-specific prompt     │
│    3. Does work using scoped tools       │
│    4. Yields control → status = idle     │
│                                          │
│  Trigger manager can only invoke         │
│  agents that are idle, never running.    │
└──────────────────────────────────────────┘
```

### Implementation Notes

**Agent isolation via `cwd` scoping:** Each agent's invoke sets an explicit `cwd` pointing to its file space directory. This is set at invoke time in agent options, not derived from the parent process. Multiple agents can run in parallel from the same Python process — each with a different `cwd` — and they won't interfere. The `cwd` is immune to `os.chdir()` in the parent process.

**MCP lifecycle:** MCP servers run as persistent HTTP processes (not stdio — stdio dies with each session). Any agent can install a new MCP: start it via bash, register it in `.mcp.json` at project root. All subsequent agent invocations with `setting_sources=["project"]` pick it up automatically. Use `supergateway` to wrap any npx stdio MCP as HTTP. To test a newly installed MCP, the installing agent raises a dummy blocker to orchestrator, orchestrator resolves it immediately, trigger manager re-invokes the agent — fresh session loads the new `.mcp.json`, agent tests the MCP.

**Shared runtime:** All agents share the same machine and OS environment. A tool installed by one agent (via bash) is available to all agents on their next bash call. This is why only iterators are permitted to install — prevents race conditions from parallel installs.

### 2.2 Agent Types

```
┌─────────────────────────────────────────────────────────────────────┐
│                          AGENT TYPES                                 │
│                                                                     │
│  ORCHESTRATOR                          ITERATOR                     │
│  ─────────────                         ────────                     │
│  Count: 1 (singleton, always exists)   Count: 1 per plane          │
│  Created: at system start              Created: by orchestrator     │
│  Lifecycle: long-lived                 Lifecycle: short-lived       │
│  Job: coordinate everything,           Job: list resources for      │
│       handle blockers, involve user         its plane, nothing more │
│                                                                     │
│  SME                                   RESOLVER                     │
│  ───                                   ────────                     │
│  Count: 1 per component                Count: 1 (singleton,        │
│           (initially one per               always exists)           │
│           resource candidate;          Created: at system start     │
│           identity evolves via         Lifecycle: long-lived        │
│           merge/split)                 Job: gatekeeper for          │
│  Created: orchestrator via                  merges/splits.          │
│           create_agent MCP tool             Grants permission,      │
│  Lifecycle: persistent                      SMEs execute.          │
│  Job: validate the resource            Processes in batches,        │
│       candidate, build components,     yields, sleeps.             │
│       negotiate consolidation,         Potential bottleneck —       │
│       execute mutations                batch processing mitigates.  │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.3 Agent Tools (scoped per type)

```
ORCHESTRATOR
  ├── bash
  ├── cartograph-db: full read/write (agent_runs, resources, tasks, secrets)
  │     ├── create_agent, list_agents, reset_agent (agent lifecycle)
  │     └── reject_resource / reject_resources_bulk with force=True (cleanup override)
  ├── read-only plane MCPs (credential validation only)
  └── chat tools (communicate with user + all agents)

ITERATOR
  ├── bash
  ├── one read-only plane MCP (scoped to its assigned plane)
  ├── cartograph-db: write to resources table + read secrets for its plane
  │     ├── upsert_resource, upsert_resources_bulk (own plane)
  │     └── reject_resource, reject_resources_bulk (self-cleanup, own plane)
  ├── can install tools/CLIs (sole agent type with install permission)
  └── chat tools

SME
  ├── bash (CANNOT install anything — must raise blocker)
  ├── one read-only plane MCP (scoped to its assigned resource)
  ├── cartograph-db (SCOPED):
  │     ├── CREATE/UPDATE own component(s)        ✓  (planned, Phase 2)
  │     ├── CREATE attributions for own component  ✓  (planned, Phase 2)
  │     ├── READ all components + attributions     ✓  (planned, Phase 2)
  │     ├── UPDATE someone else's component        ✗ BLOCKED
  │     ├── WRITE to consolidation table           ✓  (planned, Phase 3)
  │     ├── WRITE to communication table           ✓
  │     └── mark_resource_done on assigned resource ✓
  ├── vector-search (search embeddings across all tables)  (planned, Phase 2)
  └── chat tools

RESOLVER
  ├── bash
  ├── cartograph-db: read all + write consolidation status  (planned, Phase 3)
  ├── vector-search  (planned, Phase 2)
  └── chat tools

ALL AGENTS (regardless of type)
  ├── chat: emit messages, read messages, respond to admin
  ├── raise blocker: flag issues that need human/orchestrator attention
  └── communication: send/receive via communication table
```

### 2.4 Installation Management

```
SME needs a CLI tool (e.g., helm, kubectl):
    │
    │  SME CANNOT install it (prompted not to)
    │
    ├── Raises blocker via tasks table
    │   "Need helm CLI to parse Helm charts"
    │
    ▼
Orchestrator picks up blocker
    │
    ├── Assigns iterator of that plane to install it
    │   "Install helm CLI — needed by multiple SMEs"
    │
    ▼
Iterator installs generically (shared runtime)
    │
    ├── helm now available to all agents
    │   (each bash call = fresh shell, inherits global installs)
    │
    ▼
Blocker resolved → SME re-triggered
```

Why only iterators install: they understand the plane's tooling, they run one-at-a-time per plane, and it prevents 50 SMEs racing to `apt install` simultaneously.

### 2.5 Tool × Agent Matrix

Exhaustive per-tool scoping, grouped by functional category. Live = currently registered in `src/cartograph_mcp/server.py`. Planned = designed but gated behind a later phase (see `IMPLEMENTATION-PHASES.md`).

**Legend:**
- ✓ = allowed with no extra gating
- ✓ *scope* = allowed but scoped (e.g., *own plane*, *own component*, *assigned*, *participant*, *force-only*)
- — = not allowed
- *admin* = also callable by the admin agent_id via the admin UI

---

#### Action Items (live · Phase 0)

| Tool | Orch | Iter | SME | Res | Scope notes |
|---|---|---|---|---|---|
| `get_action_items_summary(agent_id)` | ✓ | ✓ | ✓ | ✓ | Returns counts for the caller only |
| `get_action_items_detail(agent_id)`  | ✓ | ✓ | ✓ | ✓ | Same |

#### Chat & Broadcast (live · Phase 0 + 2.5)

| Tool | Orch | Iter | SME | Res | Scope notes |
|---|---|---|---|---|---|
| `send_chat(from, to, message)` | ✓ | ✓ *→ admin only* | ✓ *→ admin only* | ✓ *→ admin only* | Non-admin agents may only message `admin`; admin may message anyone. Admin chat to a sleeping agent auto-wakes it. |
| `ack_chats(agent_id, ids[])` | ✓ | ✓ | ✓ | ✓ | Only acks rows where `to_agent = agent_id` |
| `get_unacked_chats(agent_id)` | ✓ | ✓ | ✓ | ✓ | Own inbox |
| `get_chat_history(agent_id, page, limit)` | ✓ | ✓ | ✓ | ✓ | Own history |
| `send_broadcast(from, to_type, message, persistent=False)` | ✓ | — | — | — | Also admin. `persistent=True` makes it apply to agents spawned later too (standing policy). Default forward-only. |
| `ack_broadcast(agent_id, id)` | ✓ | ✓ | ✓ | ✓ | Per-agent ack record |
| `get_unacked_broadcasts(agent_id, agent_type)` | ✓ | ✓ | ✓ | ✓ | Own inbox by type. Skips pre-spawn non-persistent broadcasts. |

#### Tasks (live · Phase 1)

| Tool | Orch | Iter | SME | Res | Scope notes |
|---|---|---|---|---|---|
| `create_task(owner, worker, description)` | ✓ | — | — | — | Also admin |
| `respond_task(agent_id, task_id, msg, new_status, blocker?)` | ✓ | ✓ | ✓ | ✓ | Only participants (owner or worker) |
| `raise_blocker(agent_id, task_id, detail)` | — | ✓ | ✓ | ✓ | Worker-only shortcut BW→BO |
| `get_my_tasks(agent_id)` | ✓ | ✓ | ✓ | ✓ | Owner- or worker-scoped |
| `get_task_thread(task_id)` | ✓ | ✓ | ✓ | ✓ | Participants only |

#### Secrets (live · Phase 1)

| Tool | Orch | Iter | SME | Res | Scope notes |
|---|---|---|---|---|---|
| `put_secret(agent_id, plane, key, value)` | ✓ | — | — | — | Orchestrator-only |
| `delete_secret(agent_id, plane, key)` | ✓ | — | — | — | Orchestrator-only |
| `get_secret(agent_id, plane, key)` | ✓ | ✓ | ✓ | ✓ | Iterators typically read their own plane; no hard scope |
| `list_secrets_for_plane(agent_id, plane)` | ✓ | ✓ | ✓ | ✓ | Returns keys only (no values) |

#### Resources (live · Phase 1)

| Tool | Orch | Iter | SME | Res | Scope notes |
|---|---|---|---|---|---|
| `upsert_resource(agent_id, plane, type, identifier, ...)` | — | ✓ *own plane* | — | — | Iterator plane must match |
| `upsert_resources_bulk(agent_id, plane, items[])` | — | ✓ *own plane* | — | — | Limit 5000; one transaction |
| `reject_resource(agent_id, resource_id, reason, force=False)` | ✓ *force only* | ✓ *own plane* | — | — | Soft-delete with audit trail |
| `reject_resources_bulk(agent_id, plane, ids?/types?, reason, force=False)` | ✓ *force only* | ✓ *own plane* | — | — | Refuses blank-wipe; cascade-safe |
| `mark_resource_done(agent_id, resource_id)` | — | — | ✓ *assigned* | — | SME must be linked via RCA |
| `get_resource(agent_id, resource_id)` | ✓ | ✓ | ✓ | ✓ | |
| `list_resources_for_plane(agent_id, plane)` | ✓ | ✓ | ✓ | ✓ | |
| `list_all_resources(agent_id, status?)` | ✓ | ✓ | ✓ | ✓ | Excludes `rejected` unless requested |
| `get_resource_counts(agent_id)` | ✓ | ✓ | ✓ | ✓ | Orchestrator's gatekeeper query |

#### Agent Lifecycle (live · Phase 1 + 2-kickoff + 2.5)

| Tool | Orch | Iter | SME | Res | Scope notes |
|---|---|---|---|---|---|
| `create_agent(agent_id, new_type, plane?, resource_id?)` | ✓ | — | — | — | Single-agent spawn; for SMEs, also writes RCA row with `component_id=NULL` |
| `bulk_spawn_smes(agent_id, plane, resource_ids?, all_pending?, task_description?)` | ✓ | — | — | — | Bulk SME spawn + optional one-task-per-SME; writes RCA reservation rows |
| `list_agents(agent_id)` | ✓ | ✓ | ✓ | ✓ | All non-decommissioned |
| `reset_agent(agent_id, target_agent_id)` | ✓ | — | — | — | Force-reset permanently-errored agent |
| `decommission_agent(agent_id, target, reason, resource_action='leave'/'reset'/'reject')` | ✓ | — | — | — | Self-decom refused. Resource cascade per action |
| `decommission_agents_bulk(agent_id, reason, agent_ids?, agent_type?, resource_action?)` | ✓ | — | — | — | Cohort teardown; refuses `agent_type='orchestrator'` and blank-wipe; skips caller |
| `decommission_component(agent_id, component_id, reason)` | ✓ | — | — | — | Soft-delete (status='decommissioned') |
| `decommission_components_bulk(agent_id, component_ids[], reason)` | ✓ | — | — | — | Requires explicit id list; refuses blank-wipe |
| `sleep_self(agent_id, duration_seconds, reason)` | ✓ | ✓ | ✓ | ✓ | Self-sleep up to 7 days. Admin chat / bulk_wake_agents wakes. Broadcasts/tasks queue but don't interrupt. |
| `bulk_sleep_agents(agent_id, until, reason, agent_ids?, agent_type?)` | ✓ | — | — | — | Also admin. Refuses `agent_type='orchestrator'`, never sleeps caller, ISO timestamp |
| `bulk_wake_agents(agent_id, agent_ids?, agent_type?)` | ✓ | — | — | — | Also admin. Clears `sleep_until` on cohort |

#### Component Graph (live · Phase 2.2 · embeddings deferred)

| Tool | Orch | Iter | SME | Res | Scope notes |
|---|---|---|---|---|---|
| `upsert_component(agent_id, component_data)` | — | — | ✓ *one per SME* | — | First call fills the SME's `component_id=NULL` RCA slot. Subsequent calls UPDATE in place. 1-active-component-per-SME invariant structurally enforced (splits go through consolidation). canonical_name cross-owner conflict → refuse. |
| `upsert_attribution(agent_id, component_id, data)` | — | — | ✓ *own component* | — | Idempotent on `(plane, resource_type, identifier)`; cross-component conflict → refuse. |
| `create_edge(agent_id, edge_data)` | — | — | ✓ *own source* | — | [Phase 3.9 legacy shim] Dispatches to `upsert_edge_outbound` with both endpoints set. Accepts `source_id`/`target_id` keys. Prefer `upsert_edge_outbound` in new code. |
| `upsert_edge_catalog(agent_id, edge_data)` | — | — | ✓ *own to_component* | — | Phase 3.9. Callee declares exposed endpoint / consumed topic. Writes `from_component_id=NULL`. Idempotent per `(to, type, identifier)`. |
| `upsert_edge_outbound(agent_id, edge_data)` | — | — | ✓ *own from_component* | — | Phase 3.9. Caller's outgoing edge. `to_component_id` may be set (bound) or NULL (dangling). Metadata + max-confidence accumulation on repeat writes. |
| `bind_edge(agent_id, edge_id, to_component_id)` | — | — | ✓ *owns from* | — | Phase 3.9. Resolves a dangling outgoing. Refuses collision with existing bound row. |
| `upsert_flow(agent_id, component_id, in, out, metadata?, confidence?)` | — | — | ✓ *own component* | — | Phase 3.9. Links incoming↔outgoing inside owned component. Validates `incoming.to = outgoing.from = component_id`. Set-based. |
| `insert_unresolved(agent_id, data)` | — | — | ✓ *own component* | — | |
| `resolve_reference(agent_id, unresolved_id, target_component_id)` | ✓ | ✓ | ✓ | ✓ | Any active agent (cross-SME resolution). Refuses decommissioned target. |
| `get_component(component_id)` | ✓ | ✓ | ✓ | ✓ | All readable |
| `get_attributions(component_id)` | ✓ | ✓ | ✓ | ✓ | |
| `get_edges(component_id)` | ✓ | ✓ | ✓ | ✓ | [Phase 3.9 backward-compat shape] `{outbound, inbound}`, bound-rows only. Prefer `get_component_edges` for the full view. |
| `get_component_edges(component_id)` | ✓ | ✓ | ✓ | ✓ | Phase 3.9. Returns `{incoming_bound, incoming_catalog, outgoing_bound, outgoing_dangling}`. |
| `get_flow(component_id, incoming_edge_id)` | ✓ | ✓ | ✓ | ✓ | Phase 3.9. Outgoing edges triggered by this incoming. |
| `get_flow_inverse(component_id, outgoing_edge_id)` | ✓ | ✓ | ✓ | ✓ | Phase 3.9. Incoming edges that trigger this outgoing. |
| `get_unresolved(component_id)` | ✓ | ✓ | ✓ | ✓ | |
| `vector_search(agent_id, query, table, limit)` | ✓ | ✓ | ✓ | ✓ | Embeds query with `mxbai-embed-large` (Phase 3.7), KNN-cosines against the target table. Returns `{query_embedded: bool, results}`. Tables: components / attributions / unresolved / edges. Limit clamped [1, 50]. |

#### Notifications (live · Phase 2.3)

| Tool | Orch | Iter | SME | Res | Scope notes |
|---|---|---|---|---|---|
| `get_agent_notifications(agent_id, priority_from_agent_types?, since?)` | ✓ | ✓ | ✓ | ✓ | Compact count of unacked chats + broadcasts from priority source types. Used by the per-agent PostToolUse hook for async wake-up. Hook emits `{"hookSpecificOutput":{"hookEventName":"PostToolUse","additionalContext":"[NOTIFY] …"}}` on stdout (plain stdout is swallowed by Claude Code — the JSON envelope is mandatory to reach the model). Tasks intentionally excluded (they surface via action_items_summary on normal wake-up). |

#### Consolidation (live · Phase 3)

| Tool | Orch | Iter | SME | Res | Scope notes |
|---|---|---|---|---|---|
| `nominate_consolidation(agent_id, comp_a, comp_b?, type, confidence, message)` | — | — | ✓ *owns comp_a* | — | merge requires comp_b (owned by different SME); split: comp_b optional (child spawned in Phase 4). Writes row status=B2 + initial comm with `state_transition` metadata. |
| `respond_consolidation(agent_id, cons_id, confidence, message, new_status)` | — | — | ✓ *participant* | — | State-machine validated. Writes `a_conf_score` or `b_conf_score` per role. Manual escalate to R requires `r_conf_score` already set; first escalation goes through auto_transitions. |
| `review_consolidation(agent_id, cons_id, r_conf, message, new_status, mutation_assigned_to?)` | — | — | — | ✓ | R → B1/B2/F/M. M requires `mutation_assigned_to`; split enforces it equals agent_a; merge requires it be agent_a or agent_b. |
| `get_my_consolidations(agent_id)` | — | — | ✓ *participant* | ✓ *all* | Non-terminal only. Resolver sees all non-terminal rows. |
| `get_consolidation_thread(agent_id, cons_id, page, limit)` | — | — | ✓ *participant* | ✓ | Paginated. Non-participants get `[]` (silent denial). |

#### Clarifications (live · Phase 3)

| Tool | Orch | Iter | SME | Res | Scope notes |
|---|---|---|---|---|---|
| `create_clarification(asker, responder, question)` | ✓ | ✓ | ✓ | ✓ | Responder can be any agent or literal `'admin'`. Inserts status=B2 + initial comm. |
| `respond_clarification(agent_id, clar_id, message, new_status)` | ✓ | ✓ | ✓ | ✓ | State-machine validated per role (asker/responder). |
| `get_my_clarifications(agent_id)` | ✓ | ✓ | ✓ | ✓ | Non-terminal where agent is asker or responder |
| `get_clarification_thread(agent_id, clar_id, page, limit)` | ✓ | ✓ | ✓ | ✓ | Scoped to asker + responder. |

#### Mutation (planned · Phase 4)

| Tool | Orch | Iter | SME | Res | Scope notes |
|---|---|---|---|---|---|
| `execute_mutation(agent_id, cons_id, new_status)` | — | — | ✓ *mutation POC* | — | Gated on `mutation_assigned_to == agent_id` AND `status='M'` |
| `complete_consolidation(agent_id, cons_id)` | — | — | — | ✓ | MD→D |
| `absorb_agent(agent_id, target_agent_id)` | — | — | ✓ *mutation POC* | — | Merge |
| `spawn_child_agent(parent_id, cons_id, comp_data, briefing)` | — | — | ✓ *mutation POC* | — | Split |
| `transfer_attributions(from_comp, to_comp, attr_ids[])` | — | — | ✓ *owns from_comp* | — | |
| `get_proxy_items(agent_id)` | — | — | ✓ | — | Inherited items from absorbed agents |
| `get_proxy_chats(agent_id, proxy_agent_id, page, limit)` | — | — | ✓ | — | Chat history of absorbed agent |

---

## 3. Trigger Management

### 3.1 Overview

Trigger manager and agent manager are two separate loops communicating
via `agent_runs.trigger_lock` + `agent_runs.status`.

```
┌──────────────────────────────┐    ┌──────────────────────────────┐
│       TRIGGER MANAGER        │    │  AGENT MANAGER (LANE-BASED)  │
│                              │    │                              │
│  LOOP:                       │    │  ON BOOT:                    │
│  1. Scan tables for          │    │   Spawn one worker thread    │
│     actionable events        │    │   per lane, per type:        │
│  2. Identify agents that     │    │     orch   = 1               │
│     need waking              │    │     iter   = 2               │
│  3. Filter: status='idle'    │    │     res    = 1               │
│     AND trigger_lock=FALSE   │    │     sme    = 4               │
│  4. SET trigger_lock = TRUE  │    │   (env-configurable;         │
│     (atomic, skip if 0 rows) │    │   default total = 8)         │
│  5. Recovery scan: errored   │    │                              │
│     + backoff elapsed →      │    │  PER-WORKER LOOP:            │
│     flip to idle (§2.1 of    │    │  1. Atomically claim next    │
│     TRIGGER-MANAGEMENT.md)   │    │     locked agent of my type  │
│  6. Does NOT invoke agents   │    │     via SELECT ... FOR       │
│  7. Sleep briefly → loop     │    │     UPDATE SKIP LOCKED       │
│                              │    │     (no snapshot → no stale  │
│  PRIORITY:                   │    │     priority)                │
│  orchestrator > resolver     │    │  2. invoke_agent(...) —      │
│  > sme > iterator            │    │     spawns claude -p         │
│  Within type: lower          │    │     subprocess with          │
│  invocation_count first      │    │     per-type timeout         │
│                              │    │     (iter/sme 1800s,         │
│                              │    │     orch/res 900s)           │
│                              │    │  3. On yield → idle +        │
│                              │    │     clear_recovery_state.    │
│                              │    │     On timeout/error →       │
│                              │    │     set_agent_errored        │
│                              │    │     (error_msg persisted,    │
│                              │    │     errored_at stamped).     │
│                              │    │                              │
│                              │    │  STALE WATCHDOG (separate    │
│                              │    │  thread, 120s threshold):    │
│                              │    │   running agents with stale  │
│                              │    │   heartbeat → errored        │
└──────────────────────────────┘    └──────────────────────────────┘
```

### 3.2 Trigger Conditions Per Agent Type

```
ORCHESTRATOR triggers:
  ├── Phase transition (all iterators done → start materialisation)
  ├── Blocker escalated by any agent
  ├── User sends message to orchestrator
  └── System startup

ITERATOR triggers:
  ├── Orchestrator assigns a task via tasks table
  ├── Orchestrator communicates back wrt an ongoing task
  └── User speaks with iterator

SME triggers:
  ├── Created by orchestrator via create_agent MCP tool when a new
  │   resource candidate passes its gatekeeper sanity check. Trigger
  │   manager then wakes the new idle SME via trigger_lock.
  ├── Orchestrator assigns a task via tasks table
  ├── Orchestrator communicates back wrt an ongoing task
  ├── Another SME nominates this SME in consolidation table
  ├── Another SME / Resolver responds in consolidation chat
  └── User speaks with this SME

RESOLVER triggers:
  ├── Confidence scores of BOTH agents in a consolidation row
  │   breach threshold (above merge threshold OR below reject threshold)
  ├── An SME nominates itself for a split
  ├── User speaks with resolver
  │
  │  BATCH PROCESSING:
  │    Resolver wakes → processes as many ready nominations as it can
  │    → yields control → sleeps
  │    → trigger manager sees more pending → wakes again
  │    → processes next batch → yields
  │    Natural backpressure. No overload.
  └──
```

### 3.3 Lock Mechanism

```
Trigger Manager              agent_runs              Agent Manager
      │                          │                         │
      │  1. Find idle agent      │                         │
      │     with pending items   │                         │
      │                          │                         │
      ├── SET trigger_lock=TRUE ►│                         │
      │   WHERE status='idle'    │                         │
      │   AND trigger_lock=FALSE │                         │
      │   (0 rows → skip)       │                         │
      │                          │                         │
      │  (done — does NOT        │                         │
      │   invoke anything)       │                         │
      │                          │◄── poll lock=TRUE ──────┤
      │                          │                         │
      │                          │    SET status='running' │
      │                          │◄── SET lock=FALSE ──────┤
      │                          │    invocation_count++   │
      │                          │                         │
      │                          │    invoke(agent, prompt)│
      │                          │    ... agent works ...  │
      │                          │                         │
      │                          │◄── SET status='idle' ───┤
      │                          │                         │
      │  next scan: idle +       │                         │
      │  more pending → lock     │                         │
```

---

## 4. Phases (End-to-End Flow)

### 4.0 User Input Phase

```
USER                           ORCHESTRATOR
  │                                │
  │  "I have GitHub, AWS,          │
  │   Datadog, Consul"             │
  │                                │
  ├── credentials ────────────────►│
  │                                │
  │                                ├── Validate credentials
  │                                │   (quick API call per plane)
  │                                │
  │                                ├── Store in secrets table
  │                                │   (encrypted, keyed by plane)
  │                                │
  │                                ├── Create iterators:
  │                                │   iter-github, iter-deploy,
  │                                │   iter-cloud, iter-telemetry,
  │                                │   iter-config
  │                                │
  │◄── "Ready. 5 planes            │
  │     configured. Starting       │
  │     iteration." ───────────────┤
```

### 4.1 Iteration Phase

```
ORCHESTRATOR assigns tasks to iterators
    │
    ├──► iter-github
    │      │  list repos via GitHub API
    │      │  for each repo:
    │      │    INSERT into resources (plane=github, identifier=repo_name)
    │      │  encounters private repo with no access?
    │      │    → raise blocker → orchestrator involves user
    │      └──► yields (status = idle, task = done)
    │
    ├──► iter-cloud
    │      │  list AWS resources
    │      │  walk R53 chains (R53 → ALB → TG → ASG)
    │      │  discover EKS clusters → get K8s creds from same AWS role
    │      │  list K8s Deployments, CronJobs, StatefulSets
    │      │  for each: INSERT into resources with access_description
    │      │  encounters EKS cluster with no RBAC?
    │      │    → raise blocker
    │      └──► yields
    │
    ├──► iter-telemetry
    │      │  list all services from Datadog service catalog
    │      │  for each: INSERT into resources
    │      └──► yields
    │
    ├──► iter-deploy
    │      │  list deploy-script repos
    │      │  for each: INSERT into resources
    │      └──► yields
    │
    └──► iter-config (supporter)
           │  list Consul key prefixes, Vault paths
           │  for each: INSERT into resources
           └──► yields

All iterators run in parallel.
Orchestrator monitors: all iterator tasks = "done".
Phase complete → resources table populated.
```

### 4.2 Materialisation Phase

```
Resources table has entries with status = "pending"

ORCHESTRATOR (gatekeeper, then delegator):
    │
    │  1. get_resource_counts — sanity-check iterator granularity against
    │     plane scale heuristic. If >2× expected, broadcast correction to
    │     iterator before spawning SMEs (prevents the 10k-SME blast radius).
    │  2. bulk_spawn_smes(plane=X, all_pending=True, task_description="Analyse your assigned resource")
    │     → writes N agent_runs rows (status='idle'), N RCA rows with
    │       component_id=NULL (reservation slots), flips matching resources
    │       to status='assigned', creates N tasks (BW, orchestrator→SME).
    │  3. Tasks are the wake signal — trigger manager picks up each new SME
    │     because it has a BW task.
    │
    ▼
SMEs run in parallel (one per resource):

  SME-repo-fav2 (GitHub plane)
      │
      │  Clone repo
      │  Find deploy artifacts (.odin/, Dockerfile, serverless.yml)
      │  For each deploy artifact → potential component
      │
      ▼
  For each potential component:
      │
      ├── Exact match in DB?
      │     YES → attribute to existing (conf = 1.0)
      │
      ├── Vector similarity > 0.85?
      │     YES → attribute to match (conf = 0.8)
      │
      ├── Similarity 0.7 - 0.85?
      │     YES → insert unresolved with candidate hint
      │
      └── No match (< 0.7)?
            CREATE new component + embed immediately
      │
      ▼
  Hydrate attributions:
      ├── endpoints (JAX-RS, Spring, Express annotations)
      ├── outbound HTTP calls → unresolved references
      ├── config references (Consul keys) → unresolved
      ├── repo, entry_point, runtime, deploy_config
      └── embed each attribution at write time
      │
      ▼
  Need helm to parse Helm chart?
      ├── Raise blocker: "need helm CLI"
      ├── Orchestrator → assigns iter-deploy to install
      ├── Installed → SME re-triggered, continues
      │
      ▼
  SME yields → status = idle

Orchestrator monitors: all SME tasks for this phase = "done".
```

### 4.3 Consolidation Phase

```
ORCHESTRATOR wakes all SMEs for consolidation round
    │
    ▼
EACH SME (in parallel):
    │
    ├── SELF-CHECK: is my component actually multiple components?
    │     │
    │     ├── Multiple entry points? Multiple deploy configs?
    │     │   Different runtimes within same resource?
    │     │     YES → nominate SPLIT in consolidation table
    │     │           (with confidence score + reasoning)
    │     │
    │     └── Looks like a single component → no split
    │
    ├── SIBLING SEARCH: are there other components that are me?
    │     │
    │     ├── Vector search across all components
    │     ├── Query shared attributions (same hostname, same repo)
    │     │
    │     └── Found similar? → nominate MERGE in consolidation table
    │           (with confidence score + reasoning)
    │
    └── SME yields → status = idle


NEGOTIATION (driven by trigger manager):

  ┌────────────────────────────────────────────────────────────────┐
  │                                                                │
  │  SME-A nominates merge with SME-B                              │
  │      │                                                         │
  │      ▼                                                         │
  │  Consolidation table:                                          │
  │    proposed_by=SME-A, status=B2 (SME-B's turn)                 │
  │      │                                                         │
  │      ▼                                                         │
  │  Trigger manager wakes SME-B (idle + status=B2)                │
  │      │                                                         │
  │      ▼                                                         │
  │  SME-B investigates:                                           │
  │    grep, DB queries, vector search, check attributions         │
  │    updates own confidence + appends communication              │
  │    sets status = B1 (flips to SME-A's turn)                    │
  │    yields                                                      │
  │      │                                                         │
  │      ▼                                                         │
  │  Trigger manager wakes SME-A (idle + status=B1)                │
  │      │                                                         │
  │      ▼                                                         │
  │  SME-A reads response, investigates further                    │
  │    updates own confidence + appends communication              │
  │    sets status = B2 (flips to SME-B's turn)                    │
  │    yields                                                      │
  │      │                                                         │
  │      ▼                                                         │
  │  ... back and forth (B1↔B2) until both confidence scores       │
  │      breach threshold → system auto-transitions to R           │
  │      │                                                         │
  │      ▼                                                         │
  │  TRIGGER MANAGER: status=R → wake RESOLVER                     │
  │                                                                │
  └────────────────────────────────────────────────────────────────┘


RESOLVER (batch processing):

  Resolver wakes up
      │
      ├── Scans consolidation table for all actionable rows:
      │   • status = R → ready for review
      │   • status = MD → ready for completion verification
      │
      ├── Processes batch:
      │   │
      │   ├── Merge/split candidate (status=R):
      │   │     Read conversation thread, verify evidence claims
      │   │     Basic sanity: shared hostname? same runtime?
      │   │     Any glaring contradictions?
      │   │     ├── OK → status=M, set mutation_assigned_to
      │   │     │         (merge: agent with more planes)
      │   │     │         (split: always agent_a)
      │   │     ├── Issue → set r_conf_score, send back to B1 or B2
      │   │     │           (only if something very basic/major is off)
      │   │     └── Clearly wrong → status=F (rejected)
      │   │
      │   └── Mutation complete (status=MD):
      │         Verify mutation was executed correctly
      │         → status=D (done)
      │
      ├── Yields control → status = idle
      │
      └── Trigger manager: more pending? → wake again → next batch
```

### 4.4 Mutation Phase

Mutations are executed by SMEs (the mutation_assigned_to agent), not the resolver. Resolver grants permission, SMEs do the work.

```
MERGE EXECUTION (consolidation status = M):

  Trigger manager wakes mutation_assigned_to agent (agent with more planes)
      │
      ├── Read target's component + attributions (understand what you're absorbing)
      ├── Call absorb_agent() → creates proxy entries for target's pending
      │     items (tasks, chats, broadcasts, consolidations), decommissions target
      ├── Read proxy items + target's chat history (understand context)
      │
      ├── Transfer target's attributions → own component
      ├── Re-point target's edges → own component
      ├── Re-point resource_component_agents rows → own agent + component
      ├── Decommission target's component (status = 'decommissioned')
      ├── Re-embed own component with merged metadata
      │
      ├── Triage inherited proxy items with understanding:
      │     • Tasks: close if irrelevant, or reopen under own agent_id
      │     • Chats: respond to pending admin messages
      │     • Broadcasts: ack inherited unacked broadcasts
      │     • Consolidations: continue as yourself or close
      │
      ├── execute_mutation() → status = MD
      └── Yield


SPLIT EXECUTION (consolidation status = M):

  Trigger manager wakes mutation_assigned_to agent (always agent_a / self-nominator)
      │
      ├── Call spawn_child_agent(consolidation_id, component_data, briefing)
      │     • Creates ONE new agent + ONE new component
      │     • Sets component.split_from_component_id to parent component
      │     • Sets component.split_briefing with context
      │     • Sets consolidation.child_agent_id (prevents duplicate spawns)
      │     • One spawn per consolidation nomination
      │
      ├── transfer_attributions() → move relevant attributions to child
      ├── Re-point relevant edges to child component
      ├── Add row to resource_component_agents for child
      ├── Re-embed both components
      │
      ├── Triage own communications:
      │     close items that belong to child, nudge stakeholders to reopen
      │     with the new agent
      │
      ├── execute_mutation() → status = MD
      └── Yield → parent continues with trimmed component

  If MORE components to split: nominate another split in a NEW consolidation
  entry AFTER this one completes. One child per split.

  Child agent auto-invoked by trigger manager — reads split_briefing to
  understand its origin and what it owns.
```

### 4.5 Resolution Phase

```
ORCHESTRATOR:
    │
    ├── Wake Config SMEs (supporters)
    │     │
    │     ├── Read Consul KV / Vault keys
    │     ├── Resolve config references left by other SMEs
    │     │   e.g., CONFIG_COMMON_FEEDPROVIDERURLS_SI_S_API → actual URL
    │     ├── Register resolved hostnames/URLs as attributions
    │     └── Does NOT create new components
    │
    ├── Re-scan unresolved table
    │     │
    │     ├── For each unresolved reference:
    │     │   try matching against now-consolidated component registry
    │     │     matched → create edge, mark resolved
    │     │     no match → remains unresolved
    │     │
    │     └── Flag remaining unresolved for human review
    │
    └── Phase complete
```

### 4.6 Edge Discovery Phase

```
ORCHESTRATOR wakes all SMEs
    │
    ▼
EACH SME:
    │
    ├── Already knows its outbound calls from materialisation phase
    │   (stored as unresolved references)
    │
    ├── Resolve hostnames/URLs against component table:
    │     exact match → create edge with evidence
    │     vector match → create edge with reduced confidence
    │     no match → flag as unresolved
    │
    ├── Telemetry SMEs additionally:
    │     inject trace-based edges (actual call graph from Datadog)
    │     these are the most authoritative edges
    │
    ├── Bidirectional validation:
    │     if I say "I call component B at GET /scorecard"
    │     does B have an endpoint attribution for GET /scorecard?
    │     no → flag edge as low confidence
    │
    └── Yield


RESULT: Complete component graph
    ├── Components (nodes) with attributions from all planes
    ├── Edges (dependencies) with evidence and confidence
    └── Unresolved references flagged for human review
```

### 4.7 User Feedback Phase

```
ORCHESTRATOR presents results to user:
    │
    ├── "Found N components, M edges, K unresolved references"
    │
    ├── User reviews:
    │     • "These two should actually be merged"
    │       → orchestrator assigns task to both SMEs
    │       → triggers new consolidation round for those specific components
    │
    │     • "This component doesn't exist anymore"
    │       → orchestrator assigns task to owning SME
    │       → SME marks component as decommissioned
    │
    │     • "You missed this service"
    │       → orchestrator creates manual resource entry
    │       → trigger manager spawns new SME
    │
    └── Iterate until user is satisfied
```

---

## 5. Agent × Phase Matrix

```
                │ User     │ Iteration │ Material- │ Consolid- │ Mutation │ Resol- │ Edges  │ User
                │ Input    │           │ isation   │ ation     │          │ ution  │        │ Feedback
────────────────┼──────────┼───────────┼───────────┼───────────┼──────────┼────────┼────────┼──────────
ORCHESTRATOR    │ Collect  │ Create    │ Monitor   │ Monitor   │ Monitor  │Trigger │Trigger │ Present
                │ creds,   │ iterators,│ SMEs,     │ consol    │ mutation │config  │ SMEs,  │ results,
                │ store    │ assign    │ handle    │ table,    │ progress │SMEs,   │validate│ handle
                │ secrets  │ tasks     │ blockers  │ handle    │          │re-scan │        │ feedback
                │          │           │           │ blockers  │          │unresolvd│       │
────────────────┼──────────┼───────────┼───────────┼───────────┼──────────┼────────┼────────┼──────────
ITERATOR        │   —      │ List      │   —       │    —      │    —     │   —    │   —    │   —
                │          │ resources,│           │           │          │        │        │
                │          │ install   │           │           │          │        │        │
                │          │ tools     │           │           │          │        │        │
────────────────┼──────────┼───────────┼───────────┼───────────┼──────────┼────────┼────────┼──────────
SME             │   —      │    —      │ Analyse   │ Nominate  │ EXECUTE  │Resolve │Resolve │ Handle
                │          │           │ resource, │ merges/   │ merges   │config  │outbound│ user
                │          │           │ create    │ splits,   │ + splits │refs,   │calls → │ corrections
                │          │           │ comps +   │ negotiate │ (granted │check   │edges   │
                │          │           │ attrs     │ back-and- │ by       │unresolvd│       │
                │          │           │           │ forth     │ resolver)│        │        │
────────────────┼──────────┼───────────┼───────────┼───────────┼──────────┼────────┼────────┼──────────
RESOLVER        │   —      │    —      │    —      │ Review    │ GRANT    │   —    │   —    │   —
                │          │           │           │ in batch, │ merge/   │        │        │
                │          │           │           │ verify    │ split    │        │        │
                │          │           │           │ evidence, │ permission│       │        │
                │          │           │           │ yield +   │          │        │        │
                │          │           │           │ sleep     │          │        │        │
```

---

## 6. Communication

### 6.1 Communication Table (unified message bus)

All agent interactions flow through one table. Trigger manager watches it.

```
communications
  ├── id         UUID
  ├── text       TEXT (the message)
  ├── from       TEXT (agent_id or "admin")
  ├── to         TEXT (agent_id, agent_type for broadcast, or "admin")
  ├── type       TEXT (consolidation | task | clarification | broadcast | chat)
  ├── source_id  UUID (FK to consolidations | tasks | clarifications)
  ├── created_at TIMESTAMPTZ
  └── metadata   JSONB

TRIGGER RULE:
  New row where "to" = an idle agent → wake that agent
  New row where "to" = agent_type → wake all idle agents of that type
```

### 6.2 Communication Types

```
CONSOLIDATION
  SME-A ←→ SME-B negotiation
  source_id → consolidations.id
  All back-and-forth with evidence and confidence flows here.
  Consolidation table holds state (scores, status B1/B2/R/M/MD/D/F).
  Communication table holds the conversation.

TASK
  Orchestrator → assigns work to iterator/SME
  Agent → reports completion/blocker back to orchestrator
  source_id → tasks.id

CLARIFICATION
  Agent → asks question when uncertain
  source_id → clarifications.id
  Clarification table holds metadata (who asked, status, answer).
  All back-and-forth about the clarification flows through
  communication table — same pattern as consolidation.

BROADCAST
  Admin → sends feedback to all agents of a type
  "All SMEs: when you see Odin specs, check for pre-deploy hooks"
  to = "sme" (agent type, not specific agent)
```

**Design principle:** State tables (consolidations, clarifications, tasks) hold metadata and status. The communication table holds ALL conversations. No table has its own chat column — communication is the universal message bus.

### 6.3 Admin Chat

```
Admin can:
  ├── Message any specific agent
  │   → trigger manager wakes that agent
  │   → agent reads message, responds, incorporates feedback
  │
  ├── Broadcast to agent type
  │   → all idle agents of that type get woken with the message
  │   → running agents receive it on next wake
  │
  └── View any agent's communication history
      → filter by type, source, time range

System prompt rule:
  Admin messages are highest priority during context compaction.
  They are never dropped or summarised away.
```

---

## 7. Blocker Handling

```
ANY AGENT encounters a blocker:
    │
    ├── raise_blocker() → sets task status = BO, notifies owner
    ├── Send communication to orchestrator
    └── Yield → status = idle
    │
    ▼
ORCHESTRATOR wakes (has pending blocker):
    │
    ├── Is this a common blocker? (same issue from multiple agents)
    │     YES → broadcast solution to all affected agents
    │
    ├── Can orchestrator resolve it?
    │     YES → resolve + notify agent
    │     e.g., "need helm CLI" → assign iterator to install
    │
    ├── Needs user input?
    │     YES → communicate with user
    │     e.g., "need access to repo X" → user grants
    │
    └── Resolution → update task status → trigger manager wakes blocked agent
```

Orchestrator's job across ALL phases: identify blockers that are common, broadcast solutions, and handle things case-by-case by involving the user.

---

## 8. Data Layer

### 8.1 Table Map

```
┌──────────────────────────────────────────────────────────────────┐
│                      POSTGRESQL + pgvector                        │
│                                                                  │
│  COMPONENT GRAPH         RELATIONS              AGENT INFRA      │
│  ────────────────        ─────────              ──────────────   │
│  components              resource_component_    agent_runs        │
│  attributions              agents              resources          │
│  edges                                         tasks              │
│  unresolved                                    secrets            │
│                                                                  │
│  CONSOLIDATION           COMMUNICATION          MUTATION          │
│  ─────────────           ──────────────         ────────         │
│  consolidations          communications         proxy_items      │
│                          clarifications                          │
│                          broadcast_acks                           │
└──────────────────────────────────────────────────────────────────┘
```

### 8.2 Schemas

See SCHEMA.md for full CREATE TABLE statements with constraints and indexes.
Below is a summary for quick reference.

**Component Graph:**

```
components
  id, canonical_name (UNIQUE), display_name, component_type, status,
  confidence, metadata (JSONB), embedding (vector), split_from_component_id,
  split_briefing, scanned_at, created_at, updated_at

attributions
  id, component_id (FK), plane, resource_type, identifier, evidence,
  confidence, metadata (JSONB), embedding (vector), discovered_by,
  discovered_at, last_seen_at
  UNIQUE(plane, resource_type, identifier)

edges
  id, source_id (FK), target_id (FK), edge_type, identifier,
  source_attr_id (FK → attributions), target_attr_id (FK → attributions),
  evidence (JSONB), confidence, metadata (JSONB), embedding (vector),
  discovered_by, created_at, last_seen_at
  UNIQUE(source_id, target_id, edge_type, identifier)

unresolved
  id, found_in_component_id (FK), reference_type, reference_value,
  context (JSONB), embedding (vector), resolved, resolved_to_component_id (FK),
  found_by_agent, attempts, created_at
```

**Relations:**

```
resource_component_agents
  resource_id (FK → resources), component_id (FK → components),
  agent_id (FK → agent_runs), created_at
  PRIMARY KEY(resource_id, component_id)

  Single source of truth for resource → component → agent relationship.
  Replaces: components.owned_by_agent, agent_runs.resource_ids[],
            agent_runs.planes[], resources.assigned_to
```

**Consolidation:**

```
consolidations
  id, proposed_by, agent_a_id, agent_b_id, component_a_id (FK),
  component_b_id (FK), nomination_type (merge|split),
  a_conf_score, b_conf_score, r_conf_score,
  status (B1|B2|R|M|MD|D|F) — initial state = B2,
  mutation_assigned_to, child_agent_id, resolved_by,
  created_at, updated_at, resolved_at
```

**Agent Infrastructure:**

```
agent_runs
  agent_id (PK), agent_type (orchestrator|iterator|sme|resolver),
  session_id, workspace_path, plane (iterators), resource_id (SMEs),
  status (pending|running|idle|done|errored|decommissioned),
  trigger_lock (BOOLEAN, default FALSE — set by trigger manager, cleared by agent manager),
  phase, heartbeat, invocation_count, error_msg,
  errored_at, recovery_attempts (bounded auto-recovery; MAX=3),
  created_at, updated_at

resources
  id, plane, resource_type, identifier, access_desc, metadata (JSONB),
  status (pending|assigned|done|rejected),
  rejected_at, rejected_by, rejected_reason (soft-delete audit trail),
  created_at
  UNIQUE(plane, resource_type, identifier)

tasks
  id, owner_agent_id, worker_agent_id (FK), description,
  status (BW|BO|WD|TC) — initial state = BW,
  blocker_detail, created_at, updated_at

secrets
  id, plane, key, value (encrypted), created_at, updated_at
  UNIQUE(plane, key)
```

**Communication:**

```
communications
  id, from_agent, to_agent, type (consolidation|task|clarification|
  broadcast|chat), source_id, text, metadata (JSONB), acked_at, created_at

clarifications
  id, asker_agent_id, responder_agent_id,
  status (B1|B2|QR|QC|CC) — initial state = B2,
  created_at, updated_at

broadcast_acks
  communication_id (FK), agent_id (FK), acked_at
  PRIMARY KEY(communication_id, agent_id)

proxy_items
  id, surviving_agent_id (FK), decommissioned_agent_id, item_type,
  item_id, status (pending|adopted|closed), created_at, resolved_at
```

### 8.3 Embedding Strategy

Embeddings generated at write time via `cartograph-db` MCP. No batch step.

| Table | What's embedded | Purpose |
|-------|----------------|---------|
| components | `"{type}: {canonical_name} {display_name} {metadata}"` | Fuzzy matching during consolidation |
| attributions | `"{resource_type}: {identifier}"` | Fuzzy resource lookup across planes |
| unresolved | `"{reference_type}: {reference_value}"` | Match dangling refs to components |
| edges | `"{edge_type}: {identifier}"` | Fuzzy match calls across components |

Lookup protocol: exact match first → vector fallback (>0.85 match, 0.7-0.85 hint, <0.7 create new).

Model: `text-embedding-3-small` (1536 dims). All in pgvector.

---

## 9. MCP Servers

```
READ-ONLY (one per plane, scoped per agent):

  github-reader     clone_repo, list_repos, read_file, list_branches
  cloud-reader      describe_asgs, list_r53, describe_rds, list_eks, kubectl_get
  telemetry-reader  query_traces, list_services, search_logs, query_metrics
  config-reader     get_kv, list_keys

  Enforcement: credential scopes (GitHub PAT, IAM policy, API key, ACL token)
  No write/mutate operations exposed.

WRITE TARGET (single, shared by all agents):

  cartograph-db  (FastMCP streamable-http on :8100/mcp)
    LIVE groups (65 tools registered in src/cartograph_mcp/server.py;
    see TRIGGER-MANAGEMENT.md §3 for per-tool contracts):
      action_items    (2):  summary, detail
      chat            (4):  send, ack, unacked, history
      broadcast       (3):  send, ack, unacked
      secrets         (4):  put, get, list, delete
      tasks           (5):  create, respond, raise_blocker, my, thread
      resources       (9):  upsert, upsert_bulk, get, list_for_plane,
                            list_all, get_counts, mark_done,
                            reject, reject_bulk
      agent_lifecycle (11): create_agent, bulk_spawn_smes, list_agents,
                            reset_agent, decommission_agent(_bulk),
                            decommission_component(_bulk),
                            sleep_self, bulk_sleep_agents, bulk_wake_agents
      components      (16): upsert_component, upsert_attribution,
                            create_edge (3.9 legacy shim),
                            insert_unresolved, resolve_reference,
                            upsert_edge_catalog, upsert_edge_outbound,
                            bind_edge, upsert_flow (Phase 3.9),
                            get_component, get_attributions, get_edges,
                            get_unresolved, get_component_edges,
                            get_flow, get_flow_inverse
      notifications   (1):  get_agent_notifications
      consolidation   (5):  nominate, respond, review, get_my, get_thread
      clarification   (4):  create, respond, get_my, get_thread
      search          (1):  vector_search

    PLANNED (Phase 4 — mutation):
      execute_mutation, complete_consolidation, absorb_agent,
      spawn_child_agent, transfer_attributions, get_proxy_items,
      get_proxy_chats.

  Typed operations, NOT raw SQL.
  Scoping enforced inside each tool: agent_id + agent_type + plane checked
  against agent_runs before any write.
```

---

## 10. Admin Chat UI

Lightweight web UI for humans to chat with any agent. Kicks off agent activity
(e.g., admin tells orchestrator "start iteration") and surfaces agent responses.

Lives in `src/admin_ui/`, served on `:8200` via uvicorn — separate from the
MCP server (`:8100`). Reads/writes the `communications` table directly via
`shared/db.py` (no MCP hop).

```
 ┌─────────────────────────────────────────────────────────────────┐
 │                    ADMIN CHAT UI (Browser)                      │
 │                                                                 │
 │  ┌──────────────┐  ┌─────────────────────────────────────────┐ │
 │  │  AGENTS      │  │  CHAT: orch-abc123                       │ │
 │  │              │  │                                          │ │
 │  │ ▸ orch-abc   │  │   [admin] kick off iteration             │ │
 │  │   orchestr.  │  │   [orch] starting github iteration...   │ │
 │  │   idle       │  │   [admin] any blockers?                  │ │
 │  │              │  │   [orch] yes, missing AWS creds          │ │
 │  │   iter-gh-1  │  │                                          │ │
 │  │   iterator   │  │   ─────────────────────                  │ │
 │  │   running    │  │   [type message...]              [Send]  │ │
 │  │              │  │                                          │ │
 │  │   sme-fav2   │  └─────────────────────────────────────────┘ │
 │  │   sme        │                                               │
 │  │   idle       │  Scroll up → loads older messages             │
 │  └──────────────┘  Auto-refresh 2s → new messages appear        │
 └─────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
 ┌─────────────────────────────────────────────────────────────────┐
 │              FastAPI Backend (src/admin_ui/)                    │
 │                                                                 │
 │  GET  /api/agents                    → list all active agents   │
 │  GET  /api/chat/:id?before=&limit=   → paginated chat history   │
 │  GET  /api/chat/:id/new?after=       → messages since timestamp │
 │  POST /api/chat/:id                  → admin sends message      │
 │  POST /api/chat/:id/ack              → ack agent messages       │
 │                                                                 │
 └─────────────────────────────┬───────────────────────────────────┘
                               │
                               ▼
 ┌─────────────────────────────────────────────────────────────────┐
 │                  PostgreSQL (communications table)              │
 │                                                                 │
 │  Insert from admin → trigger manager sees unacked chat          │
 │  → locks target agent → agent manager invokes                   │
 │  → agent reads chat, responds, inserts reply                    │
 │  → UI polls /new, appends reply                                 │
 └─────────────────────────────────────────────────────────────────┘
```

### 10.1 API Contract

Chat view (the original 1:1 admin↔agent view):
```
GET /api/agents
  Returns: [{agent_id, agent_type, status, created_at}]
  Filters: excludes decommissioned

GET /api/chat/:agent_id?before=<iso_timestamp>&limit=20
  Returns: {messages: [...], has_more: bool}
  Paginated older messages — cursor on created_at (before)
  Default limit 20, max 100

GET /api/chat/:agent_id/new?after=<iso_timestamp>
  Returns: {messages: [...]}
  Polling endpoint — messages with created_at > after

POST /api/chat/:agent_id
  Body: {message: "..."}
  Inserts communication (from='admin', to_agent=agent_id, type='chat')
  Returns: the inserted row

POST /api/chat/:agent_id/ack
  Body: {communication_ids: ["uuid", ...]}
  Sets acked_at on messages where to='admin' and id IN (...)
  Admin-side ack — optional (for read receipts in UI)
```

Communications panel (Phase 2.4 — cross-agent feed with filters):
```
GET /api/communications
  Query params (all AND together, all optional):
    from_agent       — exact agent_id sender
    to_agent         — exact agent_id recipient (point-to-point only;
                        broadcasts have to_agent NULL)
    from_agent_type  — joins agent_runs.agent_type of from_agent.
                        'admin' matches from_agent='admin' literally.
    to_agent_type    — matches to_agent_type column (broadcasts)
                        OR agent_runs.agent_type of to_agent (p2p).
    agent            — PARTICIPANT: from_agent=X OR to_agent=X.
    agent_type       — PARTICIPANT-BY-TYPE: sender side OR recipient
                        side OR broadcast column matches T.
    type             — chat / broadcast / task / consolidation / clarification
    source_id        — UUID of the source entity (task/consolidation/...)
    before           — created_at cursor for pagination
    limit            — 1..200, default 50
  Returns: {messages: [...], has_more: bool}

GET /api/task/:id                     → {task, thread}
GET /api/consolidation/:id            → {consolidation, thread}   (Phase 3)
GET /api/clarification/:id            → {clarification, thread}   (Phase 3)

POST /api/broadcast
  Body: {to_agent_type: "sme"|"iterator"|"orchestrator"|"resolver",
         message: "..."}
  Inserts communication (from_agent='admin', to_agent=NULL,
                         to_agent_type=<type>, type='broadcast').
  Admin short-circuits the need to ask orchestrator.

GET /api/graph                        → {nodes, edges}   (Phase 3.5)
  Nodes: active components with aggregated plane set + component_doc_md.
  Edges: edges table (join filtered to active components on both sides).
  One query per tab entry; no pagination (O(thousands) scale).
```

### 10.2 Frontend Behaviour

Three tabs in the top nav: **Chat**, **Communications**, and **Graph**.

Chat tab:
- **Agent list panel:** fetched on load, refreshed every 5s. Now **grouped
  by agent_type** (orchestrator, resolver, iterator, sme) with a per-group
  typable search input. Group count pill shows filtered/total.
- **Chat panel:** shows messages from `communications` where
  `(from_agent = selected_agent AND to_agent = 'admin')` OR
  `(from_agent = 'admin' AND to_agent = selected_agent)` AND `type = 'chat'`
- **Infinite scroll up:** when scroll reaches top, fetch with `before` cursor
- **New message polling:** every 2s, fetch with `after` = latest message timestamp
- **Message alignment:** admin messages right-aligned, agent messages left-aligned

Communications tab:
- Three-panel grid: [filter bar | message list | detail panel].
- **Filter bar** is sectioned into _Participant (either direction)_ and
  _Directional_ — same columns as the API. Typable `<datalist>` combo-
  boxes for agent inputs (suggestions from `/api/agents`).
- **Message list** shows newest-first with per-row type pill, from→to
  arrow (broadcasts render "all `<type>`s"), and a `state_transition`
  pill when the row was a response that changed state.
- **Detail panel** renders the source entity when clicked: chat/broadcast
  shows the message body + metadata; task shows status + owner/worker/
  blocker + the full thread with per-message state_transition pills.
- **Broadcast button** in the top-right opens a dialog: pick target type
  + write message → admin broadcasts directly.

Graph tab (Phase 3.5):
- Two-panel grid: [sidebar | 3d-force-graph canvas].
- **Sidebar**: plane legend, live component/edge counts, and a hover/click
  panel that renders the hovered component's name, type, plane pills,
  and `component_doc_md` (via marked + DOMPurify).
- **Canvas**: 3d-force-graph (loaded from jsDelivr — same pattern as
  marked/DOMPurify). Nodes = active components, colored by the set of
  planes the component has attributions in (RGB-averaged blend when
  multi-plane; grey when no attributions). Node size scales with plane
  count. Links labelled `{edge_type}: {identifier}`.
- Interactive physics: drag nodes → library-default settle behaviour
  (no custom pause/resume wiring needed).
- Single `GET /api/graph` fetch on tab entry; manual refresh button.

### 10.3 Why Direct DB Access (not MCP)

The admin UI is for humans, not agents. MCP tools are agent-facing with
agent_id validation and scoping. Admin UI has different concerns:
- List all agents (MCP has no such tool)
- Pagination over chat history (MCP's `get_chat_history` is per-agent-scoped)
- No agent_id in the caller (it's "admin")

Cleaner to have admin UI talk to DB directly via shared/db.py.

---

## 11. Future Scope

### 11.1 Observability & Cost Controls

```
MONITORING:
  Today: admin UI (:8200) + list_agents MCP tool + agent_runs SQL give live
  counts/statuses. Errored agents surface with error_msg after bounded recovery
  (3 attempts) exhausts.
  Future additions:
  ├── Agent status dashboard    — visualisation layer on agent_runs
  ├── Token usage per agent     — logged from SDK yield stream
  ├── Phase progress            — timestamps on status transitions
  ├── Consolidation progress    — B1/B2/R/M/MD/D/F counts
  ├── Unresolved count          — WHERE resolved = FALSE
  ├── Blocker count             — tasks WHERE status = 'BO'
  └── Communication volume      — row counts per type

COST CONTROLS (future):
  ├── max_turns per agent per phase (set at invoke time)
  │     Iterator: 5-10
  │     SME materialisation: 50-100
  │     SME consolidation (per nomination): 10-15
  │     SME edges: 10-20
  │     Resolver (per batch): 20-30
  ├── Heartbeat timeout          — invoke engine kills stuck agents
  ├── Consolidation max rounds   — after N rounds → flag for human
  ├── Resolver batch size        — processes N nominations per wake, yields, sleeps
  └── Embedding budget           — embed at write time only, lightweight model
```

### 11.2 DM Between Agents

Agents can message each other directly for quick clarification without going through the consolidation flow. Lighter weight than a formal nomination.

### 11.3 Knowledge Pool

Shared knowledge base that any agent can write to and read from. Facts that are useful beyond a single agent's scope.

```
"All dream11 services use the pattern {service}.dream11.local for R53"
"Odin specs are in .odin/{service-name}/ directory"
"VPC suffix follows pattern: .vpc{N}.local for prod, .stag.local for staging"
```

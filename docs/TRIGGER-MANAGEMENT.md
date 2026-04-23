# Cartograph — Trigger Management

This document defines how agents get invoked, what they can read, and what they can do. Three scoped operations per agent: **Trigger** (what wakes me), **Read** (what can I see), **Act** (what can I do).

---

## 1. State Machines

### 1.1 Consolidation States

```
States:
  B1  = Blocked on Agent1 (nominator's turn)
  B2  = Blocked on Agent2 (nominated's turn) ← INITIAL STATE
  R   = Resolver review
  M   = Mutation in progress (mutation_assigned_to executes)
  MD  = Materialisation Done (mutation executed, pending ack)
  D   = Done (fully complete)
  F   = Failed / rejected

Agent1 = nominator (proposed_by). Agent2 = nominated.
Initial state = B2 (nominator already provided evidence at creation).

Transitions:
  B2 → B1    (nominated responds, flips to nominator)
  B1 → B2    (nominator responds, flips to nominated)
  B1 → R     (system: if r_conf IS NULL and both conf breach threshold)
             (agent: if r_conf IS NOT NULL, agent explicitly escalates)
  B2 → R     (same conditions as above)
  R  → B1/B2 (resolver needs more info, sends back to either agent)
  R  → F     (resolver rejects)
  R  → M     (resolver approves, sets mutation_assigned_to)
  M  → MD    (mutation_assigned_to agent completes merge/split)
  MD → D     (ack — done)

Diagram:

             ┌────┐ ◄──────────── ┌────┐
             │ B1 │ ─────────────►│ B2 │ (initial)
             └─┬──┘               └──┬─┘
               │▲                   ▲│
               ││                   ││
               │└─────────┬─────────┘│
               │          │          │
               └─────────┬┼──────────┘
   (conf-breach/escalate)││
                         ▼│(back to negotiation)
                       ┌──┴─┐
              ┌────────│ R  │
              │        └─┬──┘
              │          │   
              ▼          ▼   
            ┌────┐     ┌────┐
            │ F  │     │ M  │
            └────┘     └─┬──┘
                         │
                         ▼
                       ┌────┐
                       │ MD │
                       └─┬──┘
                         │
                         ▼
                       ┌────┐
                       │ D  │
                       └────┘
```

**Who gets triggered and their options:**


| State  | Triggered            | Options (valid next states)                                                                                      |
| ------ | -------------------- | ---------------------------------------------------------------------------------------------------------------- |
| B2     | Nominated (agent_b)  | B1 (respond, flip to nominator)                                                                                  |
| B1     | Nominator (agent_a)  | B2 (respond, flip to nominated)                                                                                  |
| B1, B2 | System (auto)        | R (if both conf breach threshold and r_conf IS NULL)                                                             |
| B1, B2 | Agent (manual)       | R (escalate, only if r_conf IS NOT NULL)                                                                         |
| R      | Resolver             | B1 (need more from nominator), B2 (need more from nominated), F (reject), M (approve + set mutation_assigned_to) |
| M      | mutation_assigned_to | MD (mutation complete)                                                                                           |
| MD     | Resolver             | D (done)                                                                                                         |


---

### 1.2 Task States

```
States:
  BW  = Blocked on Worker (worker's turn) ← INITIAL STATE
  BO  = Blocked on Owner (owner's turn — blocker raised)
  WD  = Worker Done (worker finished, owner to review)
  TC  = Task Completed (owner accepted)

Initial state = BW (task created by owner, worker's turn from the start).

Transitions:
  BW → BO    (worker raises blocker)
  BW → WD    (worker completes)
  BO → BW    (owner resolves blocker, back to worker)
  BO → TC    (owner closes task directly)
  WD → BW    (owner rejects, back to worker)
  WD → TC    (owner accepts — task complete)

Diagram:

       ┌────┐ ◄──────────── ┌────┐
       │ BO │ ─────────────►│ BW │ (initial)
       └─┬──┘               └─┬──┘
         │                    │▲
         │                    ││
         │                    ▼│
         │                   ┌─┴──┐
         │                   │ WD │
         │                   └─┬──┘
         │                     │
         │                     ▼
         │                   ┌────┐
         └──────────────────►│ TC │
                             └────┘

```

**Who gets triggered and their options:**


| State | Triggered | Options (valid next states)               |
| ----- | --------- | ----------------------------------------- |
| BW    | Worker    | BO (raise blocker), WD (complete)         |
| BO    | Owner     | BW (resolve blocker), TC (close directly) |
| WD    | Owner     | BW (reject, back to worker), TC (accept)  |


---

### 1.3 Clarification States

```
States:
  B1  = Blocked on Asker (asker's turn)
  B2  = Blocked on Responder (responder's turn) ← INITIAL STATE
  QR  = Query Rejected (asker's turn to ack)
  QC  = Query Clarified (asker's turn to ack or reopen)
  CC  = Clarification Completed

Asker = agent who raised the question. Responder = agent/admin answering.
Initial state = B2 (asker already asked the question at creation).

Transitions:
  B2 → B1    (responder asks for more detail, asker's turn)
  B2 → QR    (responder rejects)
  B2 → QC    (responder answers)
  B1 → B2    (asker elaborates, back to responder)
  B1 → QC    (Asker directly closes not needed anymore)
  QC → CC    (asker accepts answer — done)
  QC → B2    (asker not satisfied, back to responder)
  QR → CC    (asker acks rejection — done)

Diagram:

       ┌────┐ ◄──────────── ┌────┐
       │ B1 │ ─────────────►│ B2 │(initial)
       └─┬──┘               └─┬──┘
         │                    │▲
         │                    ││
         │        ┌───────────┤│
         │        │           ││
         │        ▼           ▼│
         │      ┌────┐       ┌─┴──┐
         │      │ QR │       │ QC │
         │      └─┬──┘       └─┬──┘
         │        │            │
         │        ▼            ▼
         │      ┌────────────────┐
         └─────►│       CC       │
                └────────────────┘

```

**Who gets triggered and their options:**


| State | Triggered | Options (valid next states)                        |
| ----- | --------- | -------------------------------------------------- |
| B2    | Responder | B1 (ask for more detail), QR (reject), QC (answer) |
| B1    | Asker     | B2 (elaborate, back to responder), QC (close, not needed anymore) |
| QC    | Asker     | CC (accept answer), B2 (not satisfied, reopen)     |
| QR    | Asker     | CC (ack rejection)                                 |


---

### 1.4 Chat

No state machine. Plain back-and-forth with ack.

```
- Messages need to be acknowledged by agents
- Unacked messages pile up in agent's notification queue
- Trigger manager wakes agent if it has unacked chat messages
- Agent acks in bulk (single call acks all pending chat messages)
- Initially: agents can message only admin, admin can message any agent
```

### 1.5 Broadcast

No state machine. One-to-many with per-agent ack.

```
- Orchestrator/admin broadcasts to an agent type
- Each agent of that type needs to independently ack
- Unacked broadcasts trigger agent invocation
- Agent acks via broadcast_acks table
- Acked = stop triggering for this broadcast
```

---

## 2. Trigger Manager

### 2.1 Core Loop

```
LOOP (continuous):
  │
  ├── 1. Scan for actionable items across all tables
  │
  ├── 2. Build wake list:
  │     For each idle agent (status='idle' AND trigger_lock=FALSE
  │     AND (sleep_until IS NULL OR sleep_until <= now())), check:
  │       ├── consolidations where status triggers this agent?
  │       ├── tasks where status triggers this agent?
  │       ├── clarifications where status triggers this agent?
  │       ├── communications where type='chat' AND to=agent AND acked_at IS NULL?
  │       ├── broadcasts where type='broadcast' AND to_agent_type=agent_type AND no ack
  │       │   AND (is_persistent OR created_at > agent.created_at)?  -- forward-only
  │       └── system auto-transitions (confidence breach → R)?
  │
  ├── 3. Prioritise:
  │     orchestrator > resolver > sme > iterator
  │     Within same type: higher invocation_count = lower priority
  │     (spread work evenly, don't starve idle agents)
  │
  ├── 4. For each agent to wake (by priority):
  │     ├── Take lock:
  │     │     UPDATE agent_runs SET trigger_lock = TRUE
  │     │     WHERE agent_id = ? AND status = 'idle' AND trigger_lock = FALSE
  │     │     RETURNING agent_id
  │     │     (0 rows = someone else got it or agent not idle → skip)
  │     └── Done. Trigger manager does NOT invoke. Just sets the lock.
  │
  ├── 5. Check for auto-transitions:
  │     ├── Consolidation: both conf scores breach threshold
  │     │   AND r_conf IS NULL → auto-set status = 'R'
  │     └── (Other system-level transitions)
  │
  ├── 6. Recovery scan (scanners/recovery.py):
  │     ├── SELECT WHERE status='errored'
  │     │     AND recovery_attempts < 3
  │     │     AND now() - errored_at >= backoff(recovery_attempts)
  │     ├── Flip to idle, clear trigger_lock, increment recovery_attempts,
  │     │   keep error_msg for history.
  │     └── Backoff ladder: [60s, 300s, 1800s]. After 3 → stays errored
  │         for human triage (visible in admin UI with error_msg).
  │
  └── 7. Sleep briefly → loop
```

### 2.2 Agent Manager (lane-based parallel dispatcher)

Agent manager is a separate process. One dedicated worker thread per lane
per agent type. Lane caps are env-configurable
(`CARTOGRAPH_INVOKE_LANES_{ORCH,ITER,RES,SME}`), defaulting to
1 / 2 / 1 / 4 (eight concurrent claude subprocesses total). Plus one
stale-watchdog thread.

```
ON BOOT:
  Spawn N worker threads — one per lane, per agent_type.
  Spawn 1 stale-watchdog thread.

PER-WORKER LOOP (for agent_type T):
  │
  ├── 1. Atomically claim the next locked agent of type T:
  │     UPDATE agent_runs SET status='running', trigger_lock=FALSE,
  │            invocation_count=invocation_count+1, heartbeat=now()
  │     WHERE agent_id = (
  │       SELECT agent_id FROM agent_runs
  │       WHERE agent_type = T AND trigger_lock = TRUE
  │       ORDER BY invocation_count ASC
  │       LIMIT 1 FOR UPDATE SKIP LOCKED
  │     )
  │     RETURNING *;
  │     (SKIP LOCKED = no worker-vs-worker races; priority re-read every
  │      pickup = no stale-snapshot bug.)
  │
  ├── 2. invoke_agent(row, already_picked_up=True):
  │     ├── Spawn claude -p subprocess in workspace cwd.
  │     │   Per-type timeout: iterator/SME = 1800s, orch/resolver = 900s.
  │     ├── Background heartbeat thread updates agent_runs.heartbeat
  │     │   every 10s while subprocess runs.
  │     ├── On yield           → status='idle' + clear_recovery_state
  │     ├── On non-zero exit   → set_agent_errored(stderr tail)
  │     ├── On TimeoutExpired  → set_agent_errored('TimeoutExpired...')
  │     └── On any exception   → set_agent_errored(repr(exc))
  │
  └── 3. Loop immediately (another agent of same type may be waiting);
         if pickup returned None, sleep poll_interval and retry.

STALE WATCHDOG (separate thread, runs every poll_interval × 5):
  WHERE status='running' AND heartbeat < now() - 120s → set_agent_errored(
    'Heartbeat stale… heartbeat keeper stopped or process died').
  Safety net for crashes between heartbeat updates; the normal timeout/
  exception paths above write error_msg themselves.

On startup, orphaned `running` agents (previous agent-manager process died
mid-subprocess) are flipped to `errored` rather than blanket-idle, so the
recovery scanner governs retry cadence instead of blindly resurrecting them.
```

### 2.3 Flow Diagram

```
TRIGGER MANAGER                    agent_runs table                AGENT MANAGER
(scans for work)                   (shared state)                  (invokes agents)
      │                                  │                              │
      │  scan tables, find               │                              │
      │  agent X has pending items       │                              │
      │                                  │                              │
      ├── SET trigger_lock=TRUE ────────►│                              │
      │   WHERE status='idle'            │                              │
      │   AND trigger_lock=FALSE         │                              │
      │                                  │                              │
      │  (trigger manager done           │                              │
      │   with this agent)               │                              │
      │                                  │◄── poll trigger_lock=TRUE ───┤
      │                                  │                              │
      │                                  │    SET status='running'      │
      │                                  │◄── SET trigger_lock=FALSE ───┤
      │                                  │                              │
      │                                  │    invoke(agent_id, prompt)  │
      │                                  │                              │
      │                                  │    ... agent works ...       │
      │                                  │                              │
      │                                  │◄── SET status='idle' ────────┤
      │                                  │                              │
      │  next scan: agent idle           │                              │
      │  + has more pending items        │                              │
      │  → lock again                    │                              │
```

---

## 3. Agent Tools

Tools are exposed as MCP server operations. The `cartograph-db` MCP server validates `agent_id` and `agent_type` on every call and enforces scoping.

> **Implementation status.** 65 tools live in `src/cartograph_mcp/server.py` (up from 58 with the Phase 3.9 edge protocol: `upsert_edge_catalog`, `upsert_edge_outbound`, `bind_edge`, `upsert_flow`, `get_component_edges`, `get_flow`, `get_flow_inverse`). Covers action_items, chat, broadcast, secrets, tasks, resources, agent_lifecycle, components (including asymmetric edge writes + flows), notifications, consolidation, clarification, vector_search. Mutation tools (execute_mutation, complete_consolidation, absorb_agent, spawn_child_agent, transfer_attributions, get_proxy_items, get_proxy_chats) are designed below but ship in Phase 4 — see `IMPLEMENTATION-PHASES.md` for phase gating.

### 3.1 Trigger Tools (what wakes me — read-only, used by trigger manager)

These are NOT agent tools — they're trigger manager's internal queries. Listed here for completeness.

```
trigger_scan_consolidations(agent_id, agent_type)
  → Returns consolidation rows where this agent should be triggered
  │
  │  Logic:
  │    IF agent_type = 'sme':
  │      WHERE (agent_b_id = agent_id AND status = 'B2')
  │         OR (agent_a_id = agent_id AND status = 'B1')
  │    IF agent_type = 'resolver':
  │      WHERE status IN ('R', 'MD')
  │    IF agent_type = 'sme' AND agent is mutation POC:
  │      WHERE status = 'M'

trigger_scan_tasks(agent_id)
  → Returns task rows where this agent should act
  │
  │  Logic:
  │    WHERE (worker_agent_id = agent_id AND status = 'BW')
  │       OR (owner_agent_id = agent_id AND status IN ('BO', 'WD'))

trigger_scan_clarifications(agent_id)
  → Returns clarification rows where this agent should act
  │
  │  Logic:
  │    WHERE (asker_agent_id = agent_id AND status IN ('B1', 'QR', 'QC'))
  │       OR (responder_agent_id = agent_id AND status = 'B2')

trigger_scan_chats(agent_id)
  → Returns unacked chat messages for this agent
  │
  │  Logic:
  │    SELECT * FROM communications
  │    WHERE to_agent = agent_id AND type = 'chat' AND acked_at IS NULL

trigger_scan_broadcasts(agent_id, agent_type)
  → Returns unacked broadcasts for this agent
  │
  │  Logic:
  │    SELECT c.* FROM communications c
  │    WHERE c.type = 'broadcast' AND c.to_agent = agent_type
  │    AND c.id NOT IN (SELECT communication_id FROM broadcast_acks WHERE agent_id = ?)
```

### 3.2 Read Tools (what can I see — scoped by agent_id and type)

Exposed to agents via `cartograph-db` MCP.

```
get_action_items_summary(agent_id) → ActionItemsSummary
  Quick stats of everything pending for this agent.
  Returns:
    { consolidations_pending: 3,
      tasks_pending: 1,
      clarifications_pending: 0,
      unacked_chats: 5,
      unacked_broadcasts: 2 }
  Use this first to decide what to tackle.

get_action_items_detail(agent_id) → ActionItemsDetail
  Full rows for every pending item across all categories.
  Returns:
    { consolidations: [full rows with status, conf scores, latest message...],
      tasks: [full rows with status, description, blocker_detail...],
      clarifications: [full rows with status, latest message...],
      chats: [full unacked chat messages...],
      broadcasts: [full unacked broadcast messages...] }
  Use this to see everything exhaustively.

get_my_consolidations(agent_id) → ConsolidationRow[]
  Returns all consolidation rows involving this agent.
  Includes: nomination_type, both confidence scores, status.
  Agent sees the full state of its negotiations.

get_consolidation_thread(consolidation_id) → CommunicationRow[]
  Returns all communications for a consolidation, paginated.
  Any agent involved in the consolidation can read the full thread.
  Agent NOT involved → empty result (scoped).

get_my_tasks(agent_id) → TaskRow[]
  Returns tasks where agent is owner or worker.
  Includes: status, description, blocker_detail.

get_task_thread(task_id) → CommunicationRow[]
  Returns all communications for a task, paginated.
  Scoped: only owner or worker can read.

get_my_clarifications(agent_id) → ClarificationRow[]
  Returns clarifications where agent is asker or responder.

get_clarification_thread(clarification_id) → CommunicationRow[]
  Returns all communications for a clarification, paginated.
  Scoped: only asker or responder can read.

get_unacked_chats(agent_id) → CommunicationRow[]
  Returns unacked chat messages addressed to this agent.

get_unacked_broadcasts(agent_id, agent_type) → CommunicationRow[]
  Returns broadcast messages this agent hasn't acked.

get_chat_history(agent_id, page, limit) → CommunicationRow[]
  Returns paginated chat history for this agent.
  Filters: type, from_agent, date range.

get_component(component_id) → ComponentRow
  Read any component. All agents can read all components.

get_attributions(component_id) → AttributionRow[]
  Read attributions for any component.

get_edges(component_id) → EdgeRow[]
  Read edges for any component (inbound + outbound).

get_unresolved(component_id) → UnresolvedRow[]
  Read unresolved references for a component.

vector_search(agent_id, query_text, table, limit) → {query_embedded, results}
  Embed query_text (text-embedding-3-small, 1536 dims) and KNN-cosine
  against the target table's embedding column. Tables: components,
  attributions, unresolved, edges. limit clamped to [1, 50].
  Returns {query_embedded: False, results: []} when the query can't be
  embedded (missing OPENAI_API_KEY, transport error, empty text) so
  callers can distinguish "no matches" from "couldn't search".
  Available to all active agents.

get_resource(agent_id, resource_id) → ResourceRow
  Read a single resource row.

list_resources_for_plane(agent_id, plane) → ResourceRow[]
  Read all resources for a plane (iterator monitors its own, orchestrator any).

list_all_resources(agent_id, status?) → ResourceRow[]
  Read every resource, optionally filtered by status (pending/assigned/done).
  Orchestrator dashboard view.

get_resource_counts(agent_id) → { by_plane_status: [...] }
  Aggregate counts grouped by (plane, status).

list_agents(agent_id) → AgentRow[]
  See all non-decommissioned agents (any agent can call).

get_secret(agent_id, plane, key) → string
  Read a credential value (any agent).

list_secrets_for_plane(agent_id, plane) → string[]
  List credential keys for a plane (values not returned).
```

### 3.3 Act Tools (what can I do — scoped writes with mandatory state change)

Exposed to agents via `cartograph-db` MCP. Every act on a stateful entity (consolidation, task, clarification) requires a state transition. Cannot respond without changing state.

**Consolidation acts (live · Phase 3):**

```
nominate_consolidation(agent_id, component_a_id, component_b_id?, type, confidence, message)
  Creates new consolidation row (status=B2) + communication message with
  state_transition metadata.
  Only SMEs can nominate. Validates: caller owns component_a.
  type='merge' → component_b_id required, must be owned by a different SME.
  type='split' → component_b_id optional (child component is spawned in
                 Phase 4 via spawn_child_agent after resolver approval).

respond_consolidation(agent_id, consolidation_id, confidence, message, new_status)
  Updates confidence score + appends communication.
  Validates:
    - agent is agent_a or agent_b on this consolidation
    - new_status is a valid transition from current status
    - state actually changes (can't respond without transition)
  Valid transitions for nominated (agent_b):
    B2 → B1 (respond, flip to nominator)
    B2 → R  (escalate to resolver, only if r_conf IS NOT NULL)
  Valid transitions for nominator (agent_a):
    B1 → B2 (respond, flip to nominated)
    B1 → R  (escalate to resolver, only if r_conf IS NOT NULL)

review_consolidation(agent_id, consolidation_id, r_confidence, message, new_status, mutation_assigned_to?)
  Resolver-only. Reviews and decides. Writes r_conf_score + state
  transition + resolved_by / resolved_at (on F/D). Communication row
  metadata carries role='resolver' + state_transition.
  Validates: agent_type='resolver'
  Valid transitions:
    R → B1 (back to nominator) | R → B2 (back to nominated)
    R → F  (rejected — terminal)
    R → M  (approved — mutation_assigned_to REQUIRED)
           merge: caller picks agent_a or agent_b (resolver judgment);
                  must equal one of the two IDs on the row.
           split: must equal agent_a (enforced).

execute_mutation(agent_id, consolidation_id, new_status)  -- Phase 4
  SME executes approved merge/split. M → MD.

complete_consolidation(agent_id, consolidation_id)  -- Phase 4
  Resolver final ack. MD → D.
```

**Auto-transitions (system, not an agent tool):**

The trigger manager runs `auto_transitions.run_auto_transitions` on
every cycle (see §2.1 step 5). Two rules:

- `a_conf_score >= 0.85 AND b_conf_score >= 0.85 AND r_conf_score IS NULL`
  → set `status='R'`. This is how first escalation to resolver happens —
  not an explicit agent call. Manual `respond_consolidation(new_status='R')`
  is refused until `r_conf_score` is non-null (resolver has already
  weighed in).
- `a_conf_score <= 0.3 AND b_conf_score <= 0.3`
  → set `status='F'`. Both sides strongly disagree → terminal reject, no
  resolver needed.

**Task acts:**

```
create_task(owner_agent_id, worker_agent_id, description)
  Creates new task (status=BW) + communication.
  Only orchestrator/admin can create tasks.

respond_task(agent_id, task_id, message, new_status, blocker_detail?)
  Updates task + appends communication.
  Validates:
    - agent is owner or worker
    - new_status is valid transition
    - state actually changes
  Valid transitions for worker:
    BW → BO (raise blocker)
    BW → WD (complete)
  Valid transitions for owner:
    BO → BW (resolve blocker, back to worker)
    BO → TC (close task directly)
    WD → BW (reject, back to worker)
    WD → TC (accept, task complete)
```

**Clarification acts (live · Phase 3):**

```
create_clarification(asker_agent_id, responder_agent_id, question_message)
  Creates new clarification (status=B2) + communication.

respond_clarification(agent_id, clarification_id, message, new_status)
  Updates clarification + appends communication.
  Validates:
    - agent is asker or responder
    - state actually changes
  Valid transitions for responder:
    B2 → B1 (ask for more detail)
    B2 → QR (reject)
    B2 → QC (answer)
  Valid transitions for asker:
    B1 → B2 (elaborate, back to responder)
    B1 → QC (close, not needed anymore)
    QC → CC (accept answer, close)
    QC → B2 (not satisfied, back to responder)
    QR → CC (ack rejection, close)
```

**Chat acts:**

```
send_chat(from_agent_id, to_agent_id, message)
  Inserts communication with type='chat'.
  Initially: agents can only message "admin". Admin can message any agent.

ack_chats(agent_id, communication_ids[])
  Ack specific messages by ID.
  UPDATE communications SET acked_at=now()
  WHERE id IN (communication_ids) AND to_agent=agent_id AND type='chat'.
  Agent chooses which messages to ack — not blanket.
```

**Broadcast acts:**

```
send_broadcast(from_agent_id, to_agent_type, message, persistent=False)
  Inserts communication (type='broadcast', to_agent_type=<type>,
  is_persistent=<flag>). Only orchestrator/admin can broadcast.
  persistent=False (default) → forward-only, only agents existing at
  send time see it. persistent=True → also applies to agents spawned
  later (standing policy).

ack_broadcast(agent_id, communication_id)
  Inserts row into broadcast_acks.
  Stops trigger manager from re-invoking this agent for this broadcast.
```

**Component graph acts (live as of Phase 2.2 — SME-scoped to own component. Embedding pipeline deferred to Phase 3 prep; embedding columns stay NULL for now):**

```
upsert_component(agent_id, component_data)
  SME-only. First call fills the SME's RCA reservation row
  (component_id=NULL → new component), subsequent calls UPDATE the
  existing component in place. 1-active-component-per-SME invariant is
  structurally enforced via the same RCA lookup. Cross-owner canonical_name
  conflicts refuse — merges go through consolidation (Phase 3), not
  name-collision upsert.
  component_data keys: canonical_name (required), display_name (required),
  component_type (required — application/database/cache/queue/lambda/cron/
  external-service/library/infrastructure), confidence (default 1.0), metadata.

upsert_attribution(agent_id, component_id, attribution_data)
  SME-only. Validates: agent owns component_id via RCA.
  Idempotent on (plane, resource_type, identifier); cross-component
  conflict refuses.
  attribution_data keys: plane, resource_type, identifier, evidence,
  confidence, metadata.

create_edge(agent_id, edge_data)
  SME-only. Validates: agent owns source_id. CHECK source ≠ target.
  Idempotent on (source_id, target_id, edge_type, identifier).
  edge_data keys: source_id, target_id, edge_type (calls/reads_from/
  writes_to/triggers/publishes_to/consumes_from/runs_on), identifier,
  source_attr_id?, target_attr_id?, evidence (array), confidence, metadata.

insert_unresolved(agent_id, unresolved_data)
  SME-only. Validates: agent owns found_in_component_id via RCA.
  Keys: found_in_component_id, reference_type, reference_value, context.

resolve_reference(agent_id, unresolved_id, resolved_to_component_id)
  Open to any active agent (cross-SME resolution is the norm — config
  SMEs resolve hostname refs on behalf of application SMEs, etc.).
  Refuses decommissioned target or already-resolved unresolved.

raise_blocker(agent_id, task_id, blocker_detail)
  Shortcut: sets task status to BO + sends communication to owner.
```

**Notifications (live as of Phase 2.3):**

```
get_agent_notifications(agent_id, priority_from_agent_types=None, since=None)
  Compact unread-count query for the PostToolUse notification hook.
  Counts unacked chats + broadcasts from the priority source types
  (default: ['admin']; typically ['admin', 'orchestrator'] for
  iterator/SME/resolver).
  Tasks intentionally excluded — they're persistent action items that
  surface via get_action_items_summary on natural wake-up; this tool
  is for async mid-session interruptibility.
  Returns: {high_priority_count, breakdown: [{from, type, count}],
  max_seen_at: ISO timestamp | null}.
  Round-trip max_seen_at back as `since` on next call to filter to
  strictly-newer items only.
```

**Mutation acts (only available when agent is mutation_assigned_to on consolidation in state M):**

```
absorb_agent(agent_id, target_agent_id)
  MERGE: re-point all of target's pending items to agent via proxy table.
  Creates proxy_items entries for target's tasks, chats, broadcasts, consolidations.
  Decommissions target agent (status = 'decommissioned').
  Validates: agent is mutation_assigned_to on an active consolidation in state M.

spawn_child_agent(parent_agent_id, consolidation_id, component_data, briefing)
  SPLIT: create new agent + new component.
  Sets component.split_from_component_id to parent's component.
  Sets component.split_briefing to the briefing doc.
  Sets consolidation.child_agent_id to the new agent (prevents duplicate spawns).
  Registers new agent in agent_runs (trigger manager will auto-invoke).
  Validates:
    - agent is mutation_assigned_to on this consolidation in state M
    - consolidation.child_agent_id IS NULL (one spawn per nomination)

transfer_attributions(from_component_id, to_component_id, attribution_ids[])
  Move specific attributions from one component to another.
  Used during both merge (move target's attrs to survivor) and split
  (move split-off attrs to child component).
  Auto-re-embeds both components after transfer.

get_proxy_items(agent_id)
  Read all items inherited from absorbed agents via proxy routing.
  Returns tasks, chats, broadcasts, consolidations that were proxied.

get_proxy_chats(agent_id, proxy_agent_id, page, limit)
  Read paginated chat history of an absorbed agent.
  Used for context when handling inherited items.
```

**Resource acts (iterator writes own plane; SME marks done when assigned):**

```
upsert_resource(agent_id, plane, resource_type, identifier, access_desc, metadata?)
  Iterator-only. Idempotent on (plane, resource_type, identifier).
  Validates: agent_type='iterator' AND agent.plane == plane.
  Status starts 'pending'.

upsert_resources_bulk(agent_id, plane, items[])
  Iterator-only bulk variant — one transaction, same idempotency.
  items[i] = {resource_type, identifier, access_desc?, metadata?}.
  Use for large enumerations (limit 5000 per call) to avoid N
  round-trips from a single iterator invocation.

reject_resource(agent_id, resource_id, reason, force=False)
reject_resources_bulk(agent_id, plane, resource_ids?, resource_types?, reason, force=False)
  Soft-delete (status='rejected', rejected_at/by/reason recorded).
  Iterator on own plane by default; orchestrator with force=True.
  Bulk plane-scoped, filters AND together, refuses blank-wipe.
  Cascade safety: rows already linked to an SME via
  resource_component_agents are skipped (returned in skipped_cascade).
  Primary use: iterator self-cleanup after over-granular emission.

mark_resource_done(agent_id, resource_id)
  SME-only. Validates: agent is linked via resource_component_agents
  to the resource. Sets status='done'.
```

**Secret acts (orchestrator writes; all agents read):**

```
put_secret(agent_id, plane, key, value)
  Orchestrator-only. Upserts on (plane, key).

get_secret(agent_id, plane, key)
list_secrets_for_plane(agent_id, plane)
  Any active agent reads. list_ returns keys only (no values).

delete_secret(agent_id, plane, key)
  Orchestrator-only. Removes a credential.
```

**Agent lifecycle:**

```
create_agent(agent_id, new_agent_type, plane?, resource_id?)
  Orchestrator-only. Spawns an iterator (pass plane) or SME (pass resource_id).
  Creates workspace dir + .mcp.json (port :8100). For SMEs, also writes the
  RCA reservation row (component_id=NULL) and flips the resource to 'assigned'.

bulk_spawn_smes(agent_id, plane, resource_ids?, all_pending=False, task_description=None)
  Orchestrator-only. One call spawns N SMEs for a plane. Writes N agent_runs
  rows + N RCA reservation rows + flips resources to 'assigned'. If
  task_description is set, creates one task per SME (BW, owner=caller,
  worker=new SME) as the wake signal. Refuses blank-wipe: at least one of
  resource_ids[] or all_pending=True. Skips resources already assigned.

list_agents(agent_id)
  Any active agent. Returns all non-decommissioned agents with
  agent_id / agent_type / status / plane (iterators only) /
  invocation_count / sleep_until / errored_at / created_at.
  SME→resource assignment lives in resource_component_agents — join
  that table if you need it.

reset_agent(agent_id, target_agent_id)
  Orchestrator-only override. Force-resets a permanently-errored agent
  back to idle (clears error_msg, recovery_attempts=0, trigger_lock=FALSE).
  Use after the bounded auto-recovery (3 attempts) has given up.

sleep_self(agent_id, duration_seconds, reason)
  Any active agent. Self-sleep up to 7 days (duration_seconds ≤ 604800).
  Sets agent_runs.sleep_until = now() + duration_seconds. Trigger scanner
  skips sleeping agents in its idle-lock filter (see §2.1 step 2).
  Admin chat to a sleeping agent auto-wakes it. Broadcasts, tasks, and
  orchestrator-to-agent chats do NOT interrupt sleep.

bulk_sleep_agents(agent_id, until, reason, agent_ids?, agent_type?)
  Orchestrator/admin only. Puts a cohort to sleep until an ISO-8601
  timestamp. At least one of agent_ids / agent_type required.
  Refuses agent_type='orchestrator' (never bulk-pause coordinators).
  Caller is never slept (excluded even if in agent_ids[]).

bulk_wake_agents(agent_id, agent_ids?, agent_type?)
  Orchestrator/admin only. Clears sleep_until on the cohort so trigger
  scanner picks them up on the next cycle.

decommission_agent(agent_id, target_agent_id, reason, resource_action='leave')
  Orchestrator-only. Flips target to status='decommissioned'. resource_action:
    'leave'  → keep RCA row (orphan; debugging only)
    'reset'  → delete RCA row + flip resource back to 'pending'
    'reject' → delete RCA row + mark resource 'rejected' with audit trail
  Refuses self-decom. Reason required.

decommission_agents_bulk(agent_id, reason, agent_ids?, agent_type?, resource_action='leave')
  Orchestrator-only bulk variant. At least one of agent_ids[] or agent_type
  required. Refuses agent_type='orchestrator'. Caller is never decommissioned
  even if in agent_ids[].

decommission_component(agent_id, component_id, reason)
  Orchestrator-only. Soft-delete (status='decommissioned'). Attributions
  and edges stay as-is (mirrors merge deprecation semantics).

decommission_components_bulk(agent_id, component_ids[], reason)
  Bulk variant; requires explicit id list (no "all components" filter).
```

---

## 4. Prompt Construction

Agent manager invokes every agent with the same generic prompt. No snapshot — agent discovers its own action items via tools.

```
INVOCATION PROMPT (same for every agent, every invocation):

  You have been woken up because you have pending action items.

  Action items may be arriving concurrently — always use your tools
  to get the latest state, don't rely on stale information.

  Your workflow:
  1. Call get_action_items_summary() to see current counts
  2. Call get_action_items_detail() for full details on items you want to address
  3. Address each item using your act tools
  4. You MUST change state on every response — no empty replies
  5. Work on as many items as you can handle, then yield control
  6. You will be woken again if more items arrive

  Refer to your system prompt for phase-specific instructions and tool usage.
```

---

## 5. Validation Rules Summary

```
UNIVERSAL:
  - Cannot respond without a state change (consolidation, task, clarification)
  - Cannot update another agent's component
  - State transitions must follow the defined state machine
  - Agent must be a participant (owner/worker, asker/responder, agent_a/agent_b)

CONSOLIDATION-SPECIFIC:
  - System auto-transitions B1/B2 → R when:
    both conf scores breach threshold AND r_conf IS NULL
  - Agent can manually escalate to R only if r_conf IS NOT NULL
    (resolver has already weighed in before)
  - Resolver raises issues only if something very basic/major is off

TASK-SPECIFIC:
  - Only orchestrator/admin can create tasks
  - Worker cannot close task (only owner can → TC)

CHAT-SPECIFIC:
  - Agents can initially only message admin
  - Admin can message any agent
  - Ack is selective by message IDs (agent chooses which to ack)

BROADCAST-SPECIFIC:
  - Only orchestrator/admin can broadcast
  - Ack is per-broadcast per-agent
```


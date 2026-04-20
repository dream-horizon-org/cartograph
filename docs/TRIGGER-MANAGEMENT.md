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
  │     For each idle agent (status='idle' AND trigger_lock=FALSE), check:
  │       ├── consolidations where status triggers this agent?
  │       ├── tasks where status triggers this agent?
  │       ├── clarifications where status triggers this agent?
  │       ├── communications where type='chat' AND to=agent AND acked_at IS NULL?
  │       ├── broadcasts where type='broadcast' AND to=agent_type AND no ack?
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
  └── 6. Sleep briefly → loop
```

### 2.2 Agent Manager (separate loop)

Agent manager is a separate process that reads trigger locks and invokes agents.

```
AGENT MANAGER LOOP:
  │
  ├── 1. Poll: SELECT * FROM agent_runs
  │           WHERE trigger_lock = TRUE
  │           ORDER BY priority (orchestrator > resolver > sme > iterator)
  │
  ├── 2. For each locked agent:
  │     ├── UPDATE agent_runs
  │     │   SET status = 'running', trigger_lock = FALSE,
  │     │       invocation_count = invocation_count + 1,
  │     │       heartbeat = now()
  │     │   WHERE agent_id = ? AND trigger_lock = TRUE
  │     │
  │     ├── Invoke agent with generic prompt:
  │     │   "You've been woken up. Check get_action_items_summary()."
  │     │
  │     └── When agent yields:
  │           SET status = 'idle'
  │
  └── 3. Sleep briefly → loop
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

vector_search(query_text, table, limit) → Row[]
  Embed query_text, search against specified table's embeddings.
  Returns top N results with similarity scores.
  Available to all agents.
```

### 3.3 Act Tools (what can I do — scoped writes with mandatory state change)

Exposed to agents via `cartograph-db` MCP. Every act on a stateful entity (consolidation, task, clarification) requires a state transition. Cannot respond without changing state.

**Consolidation acts:**

```
nominate_consolidation(agent_id, component_a_id, component_b_id, type, confidence, message)
  Creates new consolidation row (status=B2) + communication message.
  Only SMEs can nominate.
  Validates: agent owns component_a.

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
  Resolver-only. Reviews and decides.
  Validates: agent_type = 'resolver'
  Valid transitions:
    R → B1/B2 (needs more info, sends back to either agent)
    R → F     (rejected)
    R → M     (approved — must set mutation_assigned_to)
              merge: resolver picks agent with more planes
              split: always agent_a

execute_mutation(agent_id, consolidation_id, new_status)
  SME executes approved merge/split.
  Validates: agent is the designated mutation POC
  Valid transitions:
    M → MD (mutation complete)

complete_consolidation(agent_id, consolidation_id)
  Final ack.
  Valid transitions:
    MD → D (done)
```

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

**Clarification acts:**

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
send_broadcast(from_agent_id, to_agent_type, message)
  Inserts communication with type='broadcast', to_agent=agent_type.
  Only orchestrator/admin can broadcast.

ack_broadcast(agent_id, communication_id)
  Inserts row into broadcast_acks.
  Stops trigger manager from re-invoking this agent for this broadcast.
```

**Component graph acts (scoped to own component):**

```
upsert_component(agent_id, component_data)
  Create or update a component.
  Validates: agent_id owns this component via resource_component_agents (or new component).
  Auto-embeds at write time.

upsert_attribution(agent_id, component_id, attribution_data)
  Add attribution to a component.
  Validates: agent owns this component.
  Auto-embeds at write time.

create_edge(agent_id, edge_data)
  Create a dependency edge.
  Validates: agent owns the source component.
  Auto-embeds at write time.

insert_unresolved(agent_id, unresolved_data)
  Record an unresolved reference.

resolve_reference(agent_id, unresolved_id, resolved_to_component_id)
  Mark an unresolved reference as resolved, create edge.

raise_blocker(agent_id, task_id, blocker_detail)
  Shortcut: sets task status to BO + sends communication to owner.
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


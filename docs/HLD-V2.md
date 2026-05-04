# Cartograph — High-Level Design V2

> **Read alongside:** `HLD.md` (V1). This document describes what changed, why it changed, and the full design of the simplified system. Both documents are intentionally kept separate so the reasoning behind the evolution is not lost.

---

## 1. What Changed and Why

### 1.1 The V1 Consolidation Problem

V1 used a multi-agent negotiation protocol (B1↔B2 turn-taking) where two SMEs would exchange evidence and confidence scores until both breached a threshold, then a singleton Resolver agent would review and approve or reject the merge/split. The reasoning was sound — LLM agents grounded in their session context would produce fewer hallucinations than a single automated matching pass.

In practice this created three problems:

**Cost.** Each negotiation round is an LLM subprocess call. With N components across K planes, the number of agent invocations grows as O(N × R) where R is the average number of rounds per negotiation.

**Unnecessary symmetry.** The B1↔B2 protocol assumed SME-B would re-derive the same evidence SME-A already found. The only unique contribution SME-B has is workspace context not in the DB — things it discovered but didn't attribute, or contradicting knowledge from its own artifacts. This does not require a full symmetric negotiation; it requires a single response.

**Wrong primary matching mechanism.** Vector search on component names (identifiers) is unreliable for org service names — abbreviations, cross-system naming drift, and environment/version suffixes all degrade match quality. The negotiation was compensating for a weak matching foundation. A better matching foundation eliminates most of the need for negotiation.

### 1.2 The V2 Decisions

| V1 | V2 | Reason |
|---|---|---|
| SME-to-SME negotiation (B1↔B2) | Eliminated | Replaced by batch matching |
| Resolver agent (singleton, persistent) | Eliminated | No negotiation to review |
| SMEs persistent across all phases | SMEs ephemeral (materialisation only) | No cross-phase session context needed |
| Vector search on identifiers | Vector search on contextual descriptions | Better recall, fewer false positives |
| Consolidation approved by Resolver | Auto-merge (high confidence) or human confirms (medium) | Simpler, faster |
| Consolidations, proxy_items, clarifications tables | Removed | No negotiation state machine |

### 1.3 What V2 Does Not Change

- Iterators listing resources per plane
- SMEs doing deep materialisation analysis
- Orchestrator coordinating phases and handling blockers
- The component graph output (components, attributions, edges, unresolved)
- Persistent sessions within materialisation (still valuable for complex resources)
- Human feedback loop at the end

---

## 2. System Overview

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                            CARTOGRAPH V2                                      │
│                                                                              │
│  ┌────────────────┐    ┌──────────────────┐    ┌─────────────────────────┐   │
│  │  AGENT RUNTIME  │    │  TRIGGER MANAGER  │    │     DATA LAYER          │   │
│  │                │    │                  │    │                         │   │
│  │ Orchestrator   │◄──►│ Phase driver     │◄──►│ PostgreSQL + pgvector   │   │
│  │ (persistent)   │    │ Batch merger     │    │                         │   │
│  │                │    │ Lock + priority  │    │ Component graph tables  │   │
│  │ Iterator       │    │                  │    │ Agent infra tables      │   │
│  │ (ephemeral)    │    └──────────────────┘    │ merge_candidates table  │   │
│  │                │                            └────────────┬────────────┘   │
│  │ SME            │                                         │               │
│  │ (ephemeral)    │      READ-ONLY MCP SERVERS              │               │
│  └───────┬────────┘                                         │               │
│          │          ┌──────────────────────────┐            │               │
│          └─────────►│ GitHub │ AWS │ DD │Consul │───────────┘               │
│                     │ (read) │(read)│(read)│(read)│                          │
│                     └──────────────────────────┘                            │
│                                                                              │
│  ┌──────────────────────────────┐                                            │
│  │       ADMIN INTERFACE        │  Confirm merges, chat with agents,         │
│  │                              │  view progress, give feedback              │
│  └──────────────────────────────┘                                            │
└──────────────────────────────────────────────────────────────────────────────┘
```

**Key difference from V1:** No Resolver box. No consolidation loop. The Trigger Manager gains a Batch Merger component that runs as code, not as an LLM agent.

---

## 3. Agent Model

### 3.1 Agent Types (3, down from 4)

```
┌─────────────────────────────────────────────────────────────────────┐
│                          AGENT TYPES                                 │
│                                                                     │
│  ORCHESTRATOR                          ITERATOR                     │
│  ─────────────                         ────────                     │
│  Count: 1 (singleton)                  Count: 1 per plane          │
│  Lifecycle: persistent                 Lifecycle: ephemeral         │
│  Session: kept across all phases       Session: one invocation,     │
│  Job: coordinate phases, handle                  then discarded     │
│       blockers, drive batch            Job: list resources for      │
│       merge confirmation,                   its plane only          │
│       present results                                               │
│                                                                     │
│  SME                                   RESOLVER                     │
│  ───                                   ────────                     │
│  Count: 1 per resource                 REMOVED IN V2                │
│  Lifecycle: ephemeral                                               │
│  Session: warm within materialisation                               │
│           phase, discarded after                                    │
│  Job: deeply analyse resource,                                      │
│       build components + attrs,                                     │
│       resolve edges (later phase)                                   │
└─────────────────────────────────────────────────────────────────────┘
```

### 3.2 Persistence: When It Is and Is Not Needed

Persistence means resuming a Claude Code session (`--session-id`) so the LLM has its full conversation history as context.

**Where persistence matters:**

A complex resource — a monorepo with 5 deploy artifacts, an EKS cluster with 40 workloads — cannot be fully analysed in a single LLM response. The SME needs multiple invocations to clone files, walk dependency trees, parse configs, and write attributions. Across these invocations, the session context means the agent remembers what it already found and doesn't re-derive it.

This is **within-phase persistence**: the session is warm as long as the SME is actively working on its resource. Once materialisation is complete and the SME yields, the session can be discarded. All state is in the DB.

**Where persistence is not needed:**

V1 kept SME sessions alive across all phases because SMEs were involved in consolidation. In V2, consolidation is replaced by a batch process. The SME has no work to do after materialisation is complete. There is no reason to hold a session open for weeks.

The DB is the source of truth. When an SME is invoked for edge discovery (Phase 5), it reads its own components and attributions from the DB. A fresh session loaded with this DB context is equivalent to a resumed session for this purpose.

```
V1 SME lifecycle:
  spawn → materialisation → IDLE (session held) → consolidation
  → IDLE (session held) → mutation → IDLE → edge discovery → done

V2 SME lifecycle:
  spawn → materialisation → terminate (session discarded)
  
  (later, if edge discovery needs LLM reasoning)
  spawn fresh → read own attributions from DB → resolve edges → terminate
```

**Rule of thumb:** hold a session only as long as the agent has active, in-progress work. Never hold a session across phase boundaries.

### 3.3 Tool Scoping (unchanged from V1 except Resolver removed)

```
ORCHESTRATOR
  ├── bash
  ├── cartograph-db: full read/write
  ├── read-only plane MCPs (credential validation)
  └── chat tools

ITERATOR
  ├── bash (can install tools/CLIs)
  ├── one read-only plane MCP (scoped to its plane)
  ├── cartograph-db: write to resources table + read secrets
  └── chat tools

SME
  ├── bash (CANNOT install — must raise blocker)
  ├── one read-only plane MCP (scoped to its resource)
  ├── cartograph-db (SCOPED):
  │     ├── CREATE/UPDATE own component(s)        ✓
  │     ├── CREATE attributions for own component  ✓
  │     ├── READ all components + attributions     ✓
  │     └── UPDATE someone else's component        ✗ BLOCKED
  └── chat tools
```

---

## 4. Phases End-to-End

### 4.0 User Input Phase
*(unchanged from V1)*

Orchestrator collects credentials per plane, validates each with a quick API call, stores encrypted in secrets table, creates iterators.

### 4.1 Iteration Phase
*(unchanged from V1)*

Iterators run in parallel, one per plane. Each lists all accessible resources and inserts into the resources table. Raises blockers for access issues. Yields and terminates.

### 4.2 Materialisation Phase
*(unchanged from V1)*

Trigger manager auto-spawns one SME per resource as they appear in the resources table. SMEs run in parallel. Each deeply analyses its resource, creates components and attributions, records unresolved references and outbound calls. Session kept warm across invocations within this phase. Yields when done. Session discarded.

Embedding happens at write time — **contextual description, not identifier** (see Section 6).

### 4.3 Batch Merge Phase
*(replaces Consolidation + Mutation from V1)*

This phase is driven by the Trigger Manager as code — no LLM agents involved unless a human confirms a medium-confidence candidate.

```
All SMEs finish materialisation
  ↓
Trigger Manager runs batch pre-screening:

  STEP 1: Exact attribute matching (SQL)
    SELECT a1.component_id, a2.component_id,
           a1.resource_type, a1.identifier
    FROM   attributions a1
    JOIN   attributions a2
      ON   a1.resource_type = a2.resource_type
      AND  a1.identifier    = a2.identifier
      AND  a1.component_id  < a2.component_id

  STEP 2: Fuzzy attribute matching (vector per attribute type)
    For each resource_type bucket:
      vector_search("hostname: X") → find near-matches across components
      vector_search("deploy_config: X") → find near-matches
      (one search per type, not per SME)

  STEP 3: Union-Find grouping
    A ↔ B (shared hostname)
    B ↔ C (shared deploy_config)
    → group {A, B, C} → one merge decision

  STEP 4: Classify each group by confidence tier (see Section 5)

  STEP 5: Execute per tier:
    AUTO   → execute merge directly in DB, log to merge_candidates
    CONFIRM → write to merge_candidates (status=pending_human)
               surface to admin interface for bulk confirmation
    FLAG   → write to merge_candidates (status=flagged), do not merge
    SKIP   → discard
  ↓
Human reviews CONFIRM candidates (bulk approve/reject in admin interface)
  ↓
Trigger Manager executes confirmed merges in DB
```

Merge execution is a DB operation — re-point attributions, re-point edges, decommission absorbed component, re-embed surviving component. No LLM involved.

### 4.4 Resolution Phase
*(unchanged from V1)*

Config SMEs (supporter plane) wake up, resolve remaining Consul/Vault key references, register resolved hostnames as attributions. Re-scan unresolved table. Fresh sessions loaded from DB context.

### 4.5 Edge Discovery Phase
*(unchanged from V1)*

Each SME wakes (fresh session or warm if still active), resolves outbound calls against the now-consolidated component registry, creates granular edges with source_attr_id and target_attr_id. Bidirectional validation: if A says it calls B at GET /scorecard, verify B has that endpoint attribution.

### 4.6 User Feedback Phase
*(unchanged from V1)*

Orchestrator presents results. Human corrections trigger targeted re-runs. New resources can be added manually.

---

## 5. Batch Merge Design

### 5.1 Confidence Tiers

```
STRONG SINGLE ATTRIBUTE (auto-merge on its own):
  entry_point match   → same main class/function = same process
  deploy_config match → same Helm chart / Odin spec = same deployment unit

WEAK ATTRIBUTES (need 2+ corroborating to auto-merge):
  hostname match      → could be shared load balancer
  repo match          → could be monorepo
  runtime match       → coincidence
  region match        → everything runs there

HARD BLOCKS (prevent merge regardless of other matches):
  edge exists between A and B   → caller/callee pair, not same component
  different component_type      → application ≠ database
  different runtime             → java ≠ python
  different entry_point         → different processes
```

### 5.2 Tier Classification

```
┌─────────────────────────────────────────────────────────────────┐
│  Evidence                              → Tier                   │
├─────────────────────────────────────────────────────────────────┤
│  Strong single attr + no hard blocks   → AUTO                   │
│  2+ weak attrs agree + no hard blocks  → AUTO                   │
│  1 weak attr (exact) + no hard blocks  → CONFIRM (human)        │
│  1 weak attr (fuzzy) + no hard blocks  → FLAG (review only)     │
│  Hard block present                    → SKIP regardless        │
│  No corroborating attrs                → SKIP                   │
└─────────────────────────────────────────────────────────────────┘
```

### 5.3 Merge Execution (DB only, no LLM)

```
For each approved merge (surviving = component with more attributions):

  1. Transfer attributions:
     UPDATE attributions SET component_id = surviving_id
     WHERE component_id = absorbed_id

  2. Re-point edges:
     UPDATE edges SET source_id = surviving_id WHERE source_id = absorbed_id
     UPDATE edges SET target_id = surviving_id WHERE target_id = absorbed_id

  3. Re-point resource_component_agents:
     UPDATE resource_component_agents
     SET component_id = surviving_id, agent_id = surviving_agent_id
     WHERE component_id = absorbed_id

  4. Decommission absorbed component:
     UPDATE components SET status = 'decommissioned' WHERE id = absorbed_id

  5. Re-embed surviving component (with merged attributions now included)

  6. Update merge_candidates: status = 'executed'
```

### 5.4 Split Detection

Splits (one resource containing multiple components) are detected by SMEs during materialisation, not by the batch merge phase. An SME that finds multiple entry points or deploy artifacts in one resource creates multiple components from the start. No split nomination needed.

---

## 6. Embedding Strategy

### 6.1 Embed Descriptions, Not Identifiers

The V1 approach embedded component names and attribution identifiers as raw strings. This is unreliable for org service names because:
- Abbreviations (`fav2` vs `feeds-aggregator-v2`) have low vector similarity
- Cross-system naming (`feeds-aggregator-v2` vs `feeds-agg-v2-api-prod`) is inconsistent
- Version variants (`v1` vs `v2`) have misleadingly high similarity

V2 embeds a **synthesized contextual description** — a paragraph that describes what the component IS, not just what it is called:

```
V1 component embedding:
  "application: feeds-aggregator-v2"

V2 component embedding:
  "Java application service named feeds-aggregator-v2.
   Helm chart name myteam-api. Entry point FeedsApplication.java.
   Hostname feeds-aggregator-v2.dream11.local.
   Endpoints GET /v2/feeds/{userId}, POST /v2/feeds/refresh.
   Config keys FEEDS_AGG_V2_HOST, REDIS_FEEDS_HOST.
   Runtime java. Region ap-south-1."
```

The hostname, entry point, and deploy config appear in both the GitHub SME's description and the AWS SME's description. The vector distance between these two paragraphs is meaningfully smaller than the distance between their names alone.

### 6.2 Progressive Re-Embedding

The synthesis can only be as rich as the attributions written. Embed progressively:

```
SME creates component       → embed name + type only (weak)
SME writes hostname         → re-synthesize + re-embed
SME writes entry_point      → re-synthesize + re-embed
SME writes endpoints        → re-synthesize + re-embed
SME writes config_keys      → re-synthesize + re-embed
SME yields (phase done)     → final embedding is fully enriched
```

Batch merge runs after all SMEs yield. By then every component's embedding captures the full picture of what was found.

### 6.3 Synthesis Schema

```python
def synthesize_description(component, attributions) -> str:
    parts = [f"{component.component_type} named {component.canonical_name}"]

    by_type = group_by(attributions, key="resource_type")

    # Most discriminating fields first (weight heavily in embedding)
    for field in ["entry_point", "deploy_config", "hostname",
                  "runtime", "region", "endpoint", "config_key",
                  "repo", "asg", "load_balancer"]:
        if field in by_type:
            values = ", ".join(by_type[field])
            parts.append(f"{field}: {values}")

    return ". ".join(parts)
```

### 6.4 Multi-Angle Query at Merge Time

When the batch merger searches for candidates, it fires multiple queries per component — not just the component description:

```
For component C with attributions [hostname H, repo R, entry_point E]:

  query_1: vector_search(component_description_of_C, table=components)
  query_2: vector_search("hostname: H",              table=components)
  query_3: vector_search("repo: R",                  table=components)
  query_4: vector_search("entry_point: E",            table=components)

  Union results, rank by best score across any query
```

Even if the component-level description vectors don't match, a shared hostname query surfaces the candidate.

### 6.5 Abbreviation Dictionary

When an SME successfully attributes two identifiers to the same component (e.g., repo name `feeds-aggregator-v2` and Helm chart name `fav2`), that mapping is stored:

```sql
CREATE TABLE abbreviations (
    short_form  TEXT NOT NULL,
    full_form   TEXT NOT NULL,
    plane       TEXT,
    created_by  TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY(short_form, full_form)
);
```

Future synthesis applies known expansions before embedding. The dictionary grows automatically as the system processes more resources.

---

## 7. Schema Changes

### 7.1 Tables Removed

| Table | Why removed |
|---|---|
| `consolidations` | Negotiation state machine eliminated |
| `proxy_items` | Only needed for persistent agent merges |
| `clarifications` | Negotiation artifact |
| `broadcast_acks` | Simplified without long-lived SME agents |

### 7.2 Tables Added

**`merge_candidates`** — stores batch merge results before and after execution:

```sql
CREATE TABLE merge_candidates (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    component_a_id      UUID NOT NULL REFERENCES components(id),
    component_b_id      UUID NOT NULL REFERENCES components(id),
    surviving_id        UUID REFERENCES components(id),
    tier                TEXT NOT NULL CHECK (tier IN ('auto', 'confirm', 'flag')),
    confidence          FLOAT NOT NULL,
    evidence            JSONB NOT NULL DEFAULT '[]',
                        -- array of {resource_type, identifier, match_type}
    status              TEXT NOT NULL DEFAULT 'pending' CHECK (status IN (
                            'pending',           -- not yet executed
                            'pending_human',     -- awaiting human confirmation
                            'confirmed',         -- human approved
                            'rejected',          -- human rejected or hard block
                            'executed',          -- merge complete
                            'skipped'            -- below threshold
                        )),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    CHECK (component_a_id != component_b_id)
);

CREATE INDEX idx_mc_status ON merge_candidates(status);
CREATE INDEX idx_mc_components ON merge_candidates(component_a_id, component_b_id);
```

**`abbreviations`** — org-specific abbreviation dictionary (see Section 6.5).

### 7.3 Tables Unchanged

`components`, `attributions`, `edges`, `unresolved`, `agent_runs`, `resources`, `tasks`, `secrets`, `resource_component_agents`, `communications`.

### 7.4 `communications` Table — Simplified Usage

Without long-lived SME agents, the communication types in use narrow to:

```
KEEP:
  chat       → admin ↔ orchestrator, admin ↔ specific agent
  broadcast  → admin → all agents of a type
  task       → orchestrator ↔ iterator/SME work assignments

REMOVE:
  consolidation  → negotiation artifact
  clarification  → negotiation artifact
```

The `broadcast_acks` table is removed. Broadcasts go to ephemeral agents that are active during a narrow window; unacked broadcasts for terminated agents are irrelevant.

---

## 8. Trigger Manager Changes

### 8.1 New Responsibilities

The Trigger Manager gains a **Batch Merger** role that runs as a phase transition step (code, not an agent):

```
TRIGGER MANAGER LOOP (V2):

  ├── Handle stale agents (unchanged)
  │
  ├── Phase: ITERATION
  │     Drive iterators, monitor completion
  │
  ├── Phase: MATERIALISATION
  │     Auto-spawn SMEs from resources table
  │     Monitor all SMEs complete
  │
  ├── Phase: BATCH_MERGE  ← new
  │     Run batch attribute matching (SQL)
  │     Run batch vector search per attribute type
  │     Apply union-find grouping
  │     Classify candidates into tiers
  │     Execute AUTO merges directly
  │     Write CONFIRM candidates to merge_candidates
  │     Notify orchestrator → orchestrator surfaces to human
  │     On human confirmation → execute confirmed merges
  │     Phase complete when no pending_human candidates remain
  │
  ├── Phase: RESOLUTION
  │     Spawn config SMEs (fresh sessions)
  │
  ├── Phase: EDGE_DISCOVERY
  │     Spawn/wake SMEs for edge resolution
  │
  └── Phase: USER_FEEDBACK
        Orchestrator-driven
```

### 8.2 Removed Scanning Logic

The following trigger scans from V1 are removed entirely:

```
REMOVED:
  trigger_scan_consolidations()    → no consolidations table
  trigger_scan_clarifications()    → no clarifications table
  confidence breach auto-transition → no B1↔B2 state machine
  proxy_item scanning              → no proxy_items table
```

### 8.3 Priority Ordering

With the Resolver removed, the priority order simplifies:

```
orchestrator (100) > iterator (60) > sme (40)
```

---

## 9. What V1 Had That V2 Doesn't

This section is intentionally preserved so the reasoning behind removed components is not lost.

### 9.1 B1↔B2 Negotiation Protocol

V1 had a turn-taking state machine where two SMEs exchanged evidence and confidence scores, flipping between B1 and B2 status until both breached a threshold. This was designed to surface session-grounded context that would not appear in any DB query.

**Why removed:** The primary value was preventing wrong merges through a second independent perspective. This value is preserved in V2 through: (a) the hard block rules in the confidence tier classification, (b) the CONFIRM tier requiring human review for uncertain cases. The mechanical back-and-forth added agent invocation cost without proportional benefit for the majority of cases. The human confirmation step is lighter and more direct for genuinely uncertain cases.

### 9.2 Resolver Agent

V1's Resolver was a singleton persistent LLM agent that reviewed negotiation conversation threads, verified evidence claims, and approved or rejected merges. It processed in batches to avoid becoming a bottleneck.

**Why removed:** With negotiation eliminated, the Resolver had no conversation threads to review. Its sanity-check role (verify evidence, catch contradictions) is performed by the hard block rules and confidence tier logic in the batch merger, which run as deterministic code. The human confirmation step covers cases where deterministic rules are insufficient.

### 9.3 Consolidations State Machine

The consolidations table had a seven-state state machine (B1, B2, R, M, MD, D, F) governing the full lifecycle of a merge or split nomination. The proxy_items table routed decommissioned agent's pending items to the surviving agent after a merge.

**Why removed:** The state machine was a consequence of the negotiation protocol. Without negotiation, merge state collapses to the simpler merge_candidates table (pending → confirmed/rejected → executed). Proxy routing is unnecessary because SMEs are ephemeral — there are no long-lived sessions inheriting pending work from decommissioned agents.

### 9.4 Persistent SME Sessions

V1 SMEs maintained persistent Claude Code sessions across all phases — materialisation, consolidation, mutation, resolution, and edge discovery — because they needed session memory during consolidation to evaluate merge proposals.

**Why removed:** Without consolidation, the SME's cross-phase memory has no use. The DB holds all discovered components, attributions, and unresolved references. A fresh session reading from the DB is equivalent to a resumed session for resolution and edge discovery. Holding sessions open for weeks between phases consumes resources with no benefit.

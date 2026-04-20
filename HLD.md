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
│  Count: 1 per resource                 Count: 1 (singleton,        │
│  Created: auto by trigger manager           always exists)          │
│           when resource appears in     Created: at system start     │
│           resources table              Lifecycle: long-lived        │
│  Lifecycle: persistent                 Job: gatekeeper for          │
│  Job: deeply analyse resource,              merges/splits.          │
│       build components, negotiate           Grants permission,      │
│       consolidation, execute                SMEs execute.           │
│       mutations                        Processes in batches,        │
│                                        yields, sleeps.             │
│                                        Potential bottleneck —       │
│                                        batch processing mitigates.  │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.3 Agent Tools (scoped per type)

```
ORCHESTRATOR
  ├── bash
  ├── cartograph-db: full read/write (agent_runs, resources, tasks, secrets)
  ├── read-only plane MCPs (credential validation only)
  └── chat tools (communicate with user + all agents)

ITERATOR
  ├── bash
  ├── one read-only plane MCP (scoped to its assigned plane)
  ├── cartograph-db: write to resources table + read secrets for its plane
  ├── can install tools/CLIs (sole agent type with install permission)
  └── chat tools

SME
  ├── bash (CANNOT install anything — must raise blocker)
  ├── one read-only plane MCP (scoped to its assigned resource)
  ├── cartograph-db (SCOPED):
  │     ├── CREATE/UPDATE own component(s)        ✓
  │     ├── CREATE attributions for own component  ✓
  │     ├── READ all components + attributions     ✓
  │     ├── UPDATE someone else's component        ✗ BLOCKED
  │     ├── WRITE to consolidation table           ✓
  │     └── WRITE to communication table           ✓
  ├── vector-search (search embeddings across all tables)
  └── chat tools

RESOLVER
  ├── bash
  ├── cartograph-db: read all + write consolidation status
  ├── vector-search
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

---

## 3. Trigger Management

### 3.1 Overview

```
┌──────────────────────────────────────────────────────────────────┐
│                       TRIGGER MANAGER                             │
│                                                                  │
│  LOOP:                                                           │
│    1. Scan tables for actionable events                          │
│    2. Identify which agents need to wake                         │
│    3. Filter: only invoke IDLE agents (skip running ones)        │
│    4. Take lock on agent in agent table                          │
│    5. Call agent.invoke(prompt)                                   │
│    6. Agent's invoke releases lock + sets status = running       │
│    7. When agent yields → status = idle                          │
│    8. Loop back to 1                                             │
│                                                                  │
│  PRIORITY (invocation count tracks this):                        │
│    Orchestrator > Resolver > Iterator > SME                      │
│    If multiple agents need waking, higher priority goes first    │
└──────────────────────────────────────────────────────────────────┘
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
  ├── Auto-created when new resource appears in resources table
  │   (trigger manager spawns SME + assigns resource)
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
Trigger Manager                          Agent
      │                                    │
      │  1. Identify idle agent with       │
      │     pending action items           │
      │                                    │
      ├── 2. Take lock on agent ──────────►│
      │     (UPDATE agent SET              │
      │      status = 'invoking'           │
      │      WHERE status = 'idle')        │
      │                                    │
      ├── 3. Call invoke(prompt) ─────────►│
      │                                    │
      │     4. Agent atomically:           │
      │        • Releases lock             │
      │        • Sets status = 'running'   │
      │        • Begins work               │
      │                                    │
      │     5. Agent does work...          │
      │                                    │
      │     6. Agent yields control        │
      │        • Sets status = 'idle'      │
      │                                    │
      │◄── 7. Trigger manager sees idle ───┤
      │     with pending items → repeat    │
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

TRIGGER MANAGER:
    │
    │  For each resource with status = "pending":
    │    1. Create new SME agent in agent_runs
    │    2. Assign resource to SME
    │    3. Invoke SME with materialisation prompt
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
  │    proposed_by=SME-A, pending_on=SME-B, status=open            │
  │      │                                                         │
  │      ▼                                                         │
  │  Trigger manager wakes SME-B (idle + has pending nomination)   │
  │      │                                                         │
  │      ▼                                                         │
  │  SME-B investigates:                                           │
  │    grep, DB queries, vector search, check attributions         │
  │    updates own confidence + appends chat                       │
  │    flips pending_on = SME-A                                    │
  │    yields                                                      │
  │      │                                                         │
  │      ▼                                                         │
  │  Trigger manager wakes SME-A (idle + pending_on = SME-A)       │
  │      │                                                         │
  │      ▼                                                         │
  │  SME-A reads response, investigates further                    │
  │    updates own confidence + appends chat                       │
  │    flips pending_on = SME-B                                    │
  │    yields                                                      │
  │      │                                                         │
  │      ▼                                                         │
  │  ... back and forth until both confidence scores breach        │
  │      threshold (up for merge, down for reject)                 │
  │      │                                                         │
  │      ▼                                                         │
  │  TRIGGER MANAGER: both scores > merge threshold                │
  │    → wake RESOLVER                                             │
  │                                                                │
  └────────────────────────────────────────────────────────────────┘


RESOLVER (batch processing):

  Resolver wakes up
      │
      ├── Scans consolidation table for all actionable rows:
      │   • Both scores > merge threshold → ready for merge review
      │   • Both scores < reject threshold → ready for rejection
      │   • Self-nominated split → ready for split review
      │
      ├── Processes batch:
      │   │
      │   ├── Merge candidate:
      │   │     Read conversation, verify evidence claims
      │   │     Basic sanity: shared hostname? same runtime?
      │   │     Any glaring contradictions?
      │   │     ├── OK → grant merge (status = 'approved_merge')
      │   │     └── Issue → inject own concern into chat,
      │   │               add own confidence, continue conversation
      │   │               (only if something very basic/major is off)
      │   │
      │   ├── Reject candidate:
      │   │     Verify agents genuinely disagree
      │   │     → status = 'rejected'
      │   │
      │   └── Split candidate:
      │         Verify: different entry points? deploy configs? runtimes?
      │         Check consistency with SME's other nominations
      │         ├── OK → grant split (status = 'approved_split')
      │         └── Issue → ask SME for clarification
      │
      ├── Yields control → status = idle
      │
      └── Trigger manager: more pending? → wake again → next batch
```

### 4.4 Mutation Phase

Mutations are executed by SMEs, not the resolver. Resolver grants permission, SMEs do the work.

```
MERGE EXECUTION:

  Consolidation row: status = 'approved_merge'
  Agent-A has 3 planes of attribution, Agent-B has 1 plane
      │
      ▼
  Trigger manager wakes Agent-A (higher plane count = absorber)
      │
      ├── Read Agent-B's component + all attributions
      ├── Absorb into own component:
      │     • Re-point B's attributions → A's component
      │     • Re-point B's edges → A's component
      │     • Merge metadata (A wins on conflicts)
      │     • Re-embed component with merged data
      │
      ├── Decommission Agent-B's component (status = 'decommissioned')
      ├── Decommission Agent-B (status = 'decommissioned' in agent_runs)
      │
      ├── Update consolidation: status = 'merged'
      └── Yield


SPLIT EXECUTION:

  Consolidation row: status = 'approved_split'
  SME-parent owns component with 3 deploy artifacts
      │
      ▼
  Trigger manager wakes SME-parent
      │
      ├── Create new component(s) for the split-off parts
      │     e.g., component-admin, component-cron
      │
      ├── Redistribute attributions:
      │     • Attributions belonging to admin → move to component-admin
      │     • Attributions belonging to cron → move to component-cron
      │     • Remaining attributions stay on parent's component
      │
      ├── Write briefing docs for new components
      │     (what they are, what was discovered, what's pending)
      │
      ├── Create new agents in agent_runs for each new component
      │     (trigger manager will auto-invoke them)
      │
      ├── Update own component (trimmed — only its own attributions remain)
      ├── Re-embed all affected components
      │
      ├── Update consolidation: status = 'split'
      └── Yield → parent continues as SME for its trimmed component

  New SMEs are auto-invoked by trigger manager
  (new entry in agent_runs with status = 'pending')
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
  ├── type       TEXT (consolidation | task | clarification | broadcast)
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
  Consolidation table holds state (scores, status, pending_on).
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
    ├── Create task (status = blocked, description = what's needed)
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
┌──────────────────────────────────────────────────────────────┐
│                    POSTGRESQL + pgvector                       │
│                                                              │
│  COMPONENT GRAPH         AGENT INFRA        COMMUNICATION    │
│  ────────────────        ──────────────     ──────────────   │
│  components              agent_runs         communications   │
│  attributions            resources          clarifications   │
│  edges                   tasks                               │
│  unresolved              secrets                             │
│                                                              │
│  CONSOLIDATION                                               │
│  ─────────────                                               │
│  consolidations                                              │
└──────────────────────────────────────────────────────────────┘
```

### 8.2 Schemas

**Component Graph:**

```
components
  id              UUID PK
  canonical_name  TEXT UNIQUE
  display_name    TEXT
  component_type  TEXT (application|database|cache|queue|lambda|
                       cron|external-service|library|infrastructure)
  status          TEXT (active|deprecated|decommissioned)
  confidence      FLOAT
  metadata        JSONB
  embedding       vector(1536)
  owned_by_agent  TEXT → agent_runs.agent_id
  scanned_at      TIMESTAMPTZ
  created_at      TIMESTAMPTZ
  updated_at      TIMESTAMPTZ

attributions
  id              UUID PK
  component_id    UUID FK → components
  plane           TEXT
  resource_type   TEXT      (key — repeatable: many "endpoint" rows per component)
  identifier      TEXT      (value)
  evidence        TEXT
  confidence      FLOAT
  metadata        JSONB
  embedding       vector(1536)
  discovered_by   TEXT → agent_runs.agent_id
  discovered_at   TIMESTAMPTZ
  last_seen_at    TIMESTAMPTZ
  UNIQUE(plane, resource_type, identifier)

edges
  id              UUID PK
  source_id       UUID FK → components
  target_id       UUID FK → components
  edge_type       TEXT (calls|reads_from|writes_to|triggers|
                       publishes_to|consumes_from|runs_on)
  evidence        JSONB
  confidence      FLOAT
  metadata        JSONB
  discovered_by   TEXT
  created_at      TIMESTAMPTZ
  last_seen_at    TIMESTAMPTZ
  UNIQUE(source_id, target_id, edge_type)

unresolved
  id                       UUID PK
  found_in_component_id    UUID FK → components
  reference_type           TEXT
  reference_value          TEXT
  context                  JSONB
  embedding                vector(1536)
  resolved                 BOOLEAN
  resolved_to_component_id UUID FK → components
  found_by_agent           TEXT
  attempts                 INT
  created_at               TIMESTAMPTZ
```

**Consolidation:**

```
consolidations
  id              UUID PK
  proposed_by     TEXT → agent_runs.agent_id
  agent_a_id      TEXT
  agent_b_id      TEXT
  component_a_id  UUID FK → components
  component_b_id  UUID FK → components
  nomination_type TEXT (merge|split)
  a_conf_score    FLOAT
  b_conf_score    FLOAT
  pending_on      TEXT → agent_runs.agent_id (whose turn)
  status          TEXT (open|approved_merge|approved_split|merged|split|rejected)
  resolved_by     TEXT
  created_at      TIMESTAMPTZ
  updated_at      TIMESTAMPTZ
  resolved_at     TIMESTAMPTZ
```

**Agent Infrastructure:**

```
agent_runs
  agent_id        TEXT PK
  agent_type      TEXT (orchestrator|iterator|sme|resolver)
  session_id      TEXT
  resource_id     TEXT → resources.id
  plane           TEXT
  status          TEXT (pending|invoking|running|idle|done|errored|decommissioned)
  phase           TEXT
  heartbeat       TIMESTAMPTZ
  invocation_count INT (for trigger priority)
  created_at      TIMESTAMPTZ
  updated_at      TIMESTAMPTZ

resources
  id              UUID PK
  plane           TEXT
  resource_type   TEXT
  identifier      TEXT
  access_desc     TEXT (how to access — e.g., "clone via SSH", "describe-asg")
  metadata        JSONB
  assigned_to     TEXT → agent_runs.agent_id
  status          TEXT (pending|assigned|done)
  created_at      TIMESTAMPTZ
  UNIQUE(plane, resource_type, identifier)

tasks
  id              UUID PK
  agent_id        TEXT → agent_runs.agent_id
  description     TEXT
  status          TEXT (pending|in_progress|done|blocked)
  created_at      TIMESTAMPTZ
  updated_at      TIMESTAMPTZ

secrets
  id              UUID PK
  plane           TEXT
  key             TEXT
  value           TEXT (encrypted)
  created_at      TIMESTAMPTZ
  UNIQUE(plane, key)
```

**Communication:**

```
communications
  id              UUID PK
  text            TEXT
  from_agent      TEXT
  to_agent        TEXT
  type            TEXT (consolidation|task|clarification|broadcast)
  source_id       UUID
  metadata        JSONB
  created_at      TIMESTAMPTZ

clarifications
  id              UUID PK
  from_agent      TEXT
  status          TEXT (open|answered|dismissed)
  created_at      TIMESTAMPTZ
  answered_at     TIMESTAMPTZ

  Note: clarification question text, answers, and back-and-forth
  all flow through the communications table with type='clarification'
  and source_id pointing here. This table only holds status metadata.
```

### 8.3 Embedding Strategy

Embeddings generated at write time via `cartograph-db` MCP. No batch step.

| Table | What's embedded | Purpose |
|-------|----------------|---------|
| components | `"{type}: {name} {display_name} {metadata}"` | Fuzzy matching during consolidation |
| attributions | `"{resource_type}: {identifier}"` | Fuzzy resource lookup across planes |
| unresolved | `"{reference_type}: {reference_value}"` | Match dangling refs to components |

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

  cartograph-db
    ├── upsert_component     (scoped: own components only for SMEs)
    ├── upsert_attribution   (scoped: own components only for SMEs)
    ├── create_edge
    ├── insert_unresolved
    ├── nominate_consolidation
    ├── send_communication
    ├── raise_blocker
    ├── vector_search
    └── query_*              (read: all agents)

  Typed operations, NOT raw SQL.
  Scoping enforced at the MCP level: agent_id checked on every write.
```

---

## 10. Future Scope

### 10.1 Observability & Cost Controls

```
MONITORING (future):
  ├── Agent status dashboard    — agent_runs table (running/idle/errored counts)
  ├── Token usage per agent     — logged from SDK yield stream
  ├── Phase progress            — timestamps on status transitions
  ├── Consolidation progress    — open/approved/merged/rejected/split counts
  ├── Unresolved count          — WHERE resolved = FALSE
  ├── Blocker count             — tasks WHERE status = 'blocked'
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

### 10.2 DM Between Agents

Agents can message each other directly for quick clarification without going through the consolidation flow. Lighter weight than a formal nomination.

### 10.3 Knowledge Pool

Shared knowledge base that any agent can write to and read from. Facts that are useful beyond a single agent's scope.

```
"All dream11 services use the pattern {service}.dream11.local for R53"
"Odin specs are in .odin/{service-name}/ directory"
"VPC suffix follows pattern: .vpc{N}.local for prod, .stag.local for staging"
```

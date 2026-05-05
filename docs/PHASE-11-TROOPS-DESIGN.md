# Cartograph — Phase 11 Design: Troop Architecture (multi-machine deployment)

> **Status: DESIGN ONLY (2026-05-05).** No code shipped. This doc captures
> the architecture discussion + open decisions. Phase 10 (search tools +
> symmetric-nomination guard + `doc_md` embed) ships first; Phase 11 is
> the next major architectural shift.

---

## 1. Motivation

Today Cartograph is a single-machine deployable. HLD §1 explicitly says
*"all agents on the same machine, shared runtime — installing a tool
reflects for all agents."* That assumption shows up in three places:

1. **Iterator's install role.** Iterators install CLIs (`helm`,
   `kubectl`, `aws`) globally via `bash` because everyone shares the
   filesystem. Multi-machine breaks this — an iter on `M1` installing
   `helm` doesn't help an SME on `M2`.
2. **Workspace = local filesystem.** `src/workspaces/<agent_id>/`
   holds the cloned repo, `MERGE_LOG.md`, `handoffs/`, the agent's
   `.mcp.json`, the agent's `.claude/settings.json`. Workspace lives
   on the box that ran the agent; it doesn't migrate.
3. **Process layout assumes one host.** `agent_management.invoke_loop`
   polls `trigger_lock=TRUE` and spawns `claude -p` on the local host.
   No machine-aware filter.

We want a deployment model where:

- Multiple physical hosts can each run a slice of the agent fleet.
- Physical hosts are interchangeable (cattle, not pets) — when one
  dies, another can pick up the slack.
- Per-host state (workspace, JSONL transcript, installed-tools list)
  follows the *deployment slot*, not the physical host.
- The architecture supports planned drains (machine maintenance) and
  unplanned failures (machine crash).

The **troop** is the abstraction that makes all this work.

---

## 2. Concepts

### 2.1 Troop = stable deployment slot

A troop is a logical identity (e.g. `troop-1`, `troop-2`, `troop-3`)
that represents *a slot in the agent fleet capable of running N
concurrent claude-p subprocesses*. Troops live in a `troops` table in
the central Postgres. The fleet has a fixed number of troops, sized
to expected load.

Physical hosts are interchangeable. A physical box boots a **troop
daemon** that:

1. Looks for an available `troops` row.
2. Atomically claims it via CAS (compare-and-swap on `claimed_by`
   + `lease_term`).
3. Heartbeats `troops.last_heartbeat` every N seconds.
4. Owns the troop until either it dies (heartbeat goes stale → row
   becomes available again to a new physical host) or it's deliberately
   drained.

The agents pinned to that troop (via `agent_runs.troop_id`) are
served by whichever physical host currently holds the lease.

### 2.2 Lease term — split-brain safety

Every claim bumps `troops.lease_term` by 1. The current lease holder
remembers the term it was assigned at claim time. Every write the
lease holder makes (pickup queries, heartbeat updates) carries the
term.

If the heartbeat goes stale and a new physical host claims the troop,
`lease_term` bumps. The old physical host's writes (still in flight,
maybe due to network lag) check the term and fail-safe: SQL update
returns 0 rows, the daemon notices, exits cleanly.

This is the same pattern as Kubernetes leases, etcd leader election,
Consul sessions. Battle-tested.

### 2.3 Central admin cohort vs troop cohort

| Cohort | Where it runs | What's in it |
|---|---|---|
| **Central admin** | One dedicated host (or HA pair, eventually) | Postgres + pgvector · Ollama (embeddings) · Trigger manager · Stale-heartbeat watchdog · Recovery scanner · Admin UI · Orchestrator agent · Resolver agent |
| **Troop** | N hosts, each holding a troop lease | MCP server (local `:8100`, points at central Postgres) · Agent manager / invoke_loop (filtered on `troop_id`) · Workspace sync daemon · Troop daemon (heartbeat + lease) · Pinned agents: iterators, SMEs, machine_managers |

**Why orchestrator + resolver stay central:**
- Both are **singletons** — easier to enforce singleton-ness if they
  don't move.
- Both have **light workspace** (no cloned repos, mostly read-only).
- Orchestrator is the **admin chat partner** — admin UI lives on
  central, so co-locating orch removes an extra hop on every admin
  message.

**Why heavy-workspace agents (SMEs, iterators) go on troops:**
- SMEs clone repos, write `MERGE_LOG.md`, accumulate `handoffs/`.
  This state is what justifies "pin to a troop and migrate
  workspace via S3 sync".
- Iterators cache paginated API responses in workspace (e.g. a
  10-page GitHub repo list). Same justification.

### 2.4 machine_manager — new agent type

Currently iterators install CLIs because they're the only type with
install permission. That's a misfit — iterators are **plane-scoped**
(one per github / cloud / telemetry plane) but installs are
**machine-scoped** (one filesystem at a time).

Phase 11 introduces `agent_type='machine_manager'`. **One per troop.**
Roles:

1. Maintain `troops.metadata.installed_tools[]`.
2. Handle install blockers routed by orch ("SME on troop-2 needs
   `helm`" → orch tasks `machine_manager` for troop-2 → bash install).
3. On a re-bootstrap (when a new physical host claims a troop that
   already has installed tools recorded), replay each install on the
   new filesystem so prior agents wake into a usable runtime.

**Iterator's install role is removed.** Iterators shrink to pure
plane enumeration + plane-MCP read.

**What stays in shell, NOT in machine_manager:**
- Baseline tools (git, python3, brew/apt, OS packages) — install
  via deterministic shell bootstrap script when the troop daemon
  comes up. No LLM invocation cost on every troop boot.
- machine_manager only handles **ad-hoc judgment-driven** installs
  in response to agent blockers.

---

## 3. Schema

### 3.1 New table: `troops`

```sql
CREATE TABLE troops (
    id              TEXT PRIMARY KEY,            -- 'troop-1', 'troop-2', ...
    status          TEXT NOT NULL CHECK (status IN (
                        'available',   -- no lease holder; up for grabs
                        'claimed',     -- a physical host holds the lease
                        'draining',    -- being retired; no new agent pickups
                        'retired'      -- terminal; will not be claimed again
                    )),
    claimed_by      TEXT,                        -- physical hostname currently holding the lease
    capacity        INT NOT NULL DEFAULT 8,      -- max concurrent claude-p subprocesses on this troop
    last_heartbeat  TIMESTAMPTZ,                 -- updated every N seconds by lease holder
    lease_term      BIGINT NOT NULL DEFAULT 0,   -- bumped on every CAS claim; safety against split brain
    region          TEXT,                        -- optional: 'us-east-1', 'eu-west-1' for affinity
    metadata        JSONB NOT NULL DEFAULT '{}', -- installed_tools[], OS info, AMI version, ...
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_troops_available ON troops(status) WHERE status = 'available';
CREATE INDEX idx_troops_heartbeat ON troops(last_heartbeat)
    WHERE status = 'claimed';
```

### 3.2 Additions to `agent_runs`

```sql
ALTER TABLE agent_runs
  ADD COLUMN troop_id TEXT REFERENCES troops(id) ON DELETE SET NULL;

CREATE INDEX idx_agent_runs_troop ON agent_runs(troop_id, status)
  WHERE status IN ('idle', 'running');
```

`troop_id NULL` is reserved for **central-cohort agents** (orchestrator,
resolver). `agent_management.invoke_loop` running on central skips
agents with `troop_id IS NOT NULL`; troop-mode invoke_loop only picks
up its own `troop_id`.

### 3.3 New `agent_type='machine_manager'`

Drop the existing CHECK constraint, replace:

```sql
ALTER TABLE agent_runs DROP CONSTRAINT agent_runs_agent_type_check;
ALTER TABLE agent_runs ADD CONSTRAINT agent_runs_agent_type_check
  CHECK (agent_type IN (
    'orchestrator',
    'iterator',
    'sme',
    'resolver',
    'machine_manager'
  ));
```

Spawn discipline: ONE machine_manager per troop. Auto-spawned by
the troop daemon on lease acquisition, not via `create_agent`.

### 3.4 Lease term on writes

Every pickup query and heartbeat write from a troop's daemon /
agent_manager carries `lease_term` as a guard:

```sql
-- agent_manager pickup (per troop):
UPDATE agent_runs
   SET status = 'running', trigger_lock = FALSE,
       invocation_count = invocation_count + 1,
       heartbeat = now()
 WHERE agent_id = (
     SELECT agent_id FROM agent_runs
      WHERE agent_type = $TYPE
        AND troop_id  = $MY_TROOP_ID
        AND trigger_lock = TRUE
        AND status = 'idle'
      ORDER BY invocation_count ASC
      LIMIT 1
      FOR UPDATE SKIP LOCKED
   )
   AND $MY_LEASE_TERM = (
       SELECT lease_term FROM troops WHERE id = $MY_TROOP_ID
   )
 RETURNING *;
```

If `MY_LEASE_TERM` ≠ current term, the second AND fails → 0 rows
returned → daemon notices it lost the lease → exits cleanly.

---

## 4. Lease protocol

### 4.1 Claim (physical host boot)

```sql
-- Atomic CAS-claim. Claims an available troop OR a stale-heartbeat troop.
UPDATE troops
   SET claimed_by      = $hostname,
       last_heartbeat  = now(),
       lease_term      = lease_term + 1,
       status          = 'claimed'
 WHERE id = (
     SELECT id FROM troops
      WHERE (status = 'available')
         OR (status = 'claimed' AND last_heartbeat < now() - interval '60 seconds')
      ORDER BY id ASC
      LIMIT 1
      FOR UPDATE SKIP LOCKED
   )
RETURNING id, lease_term;
```

If the claim returns a row, the daemon proceeds to bootstrap that
troop. Otherwise it sleeps and retries.

### 4.2 Heartbeat (every N seconds, e.g. 15s)

```sql
UPDATE troops
   SET last_heartbeat = now()
 WHERE id           = $MY_TROOP_ID
   AND lease_term   = $MY_LEASE_TERM
   AND claimed_by   = $hostname;
```

If 0 rows updated, the daemon detected lease loss → graceful exit
(stops MCP, agent_manager, sync daemon).

### 4.3 Release (graceful drain)

```sql
UPDATE troops
   SET status         = 'available',
       claimed_by     = NULL,
       last_heartbeat = NULL
 WHERE id           = $MY_TROOP_ID
   AND lease_term   = $MY_LEASE_TERM;
```

After this, drain agents pinned to this troop (sync workspace + JSONL
to S3, set `agent_runs.troop_id = NULL` so they're unpinned and can
be re-pinned to a new troop on next scheduling).

### 4.4 Stale-lease recovery

Driven by the central watchdog (which also handles agent
heartbeat staleness):

```sql
UPDATE troops
   SET status        = 'available',
       claimed_by    = NULL
 WHERE status        = 'claimed'
   AND last_heartbeat < now() - interval '60 seconds';
```

Agents pinned to the now-available troop are still
`troop_id = $stale_id`. They WON'T be picked up (no daemon owns
that troop) until either (a) a new physical host claims `$stale_id`
(workspace + JSONL get re-pulled from S3, agents resume), or (b)
the central scheduler re-pins them to a different available troop.

---

## 5. Process layout

### 5.1 Central host (one process tree)

```
postgres + pgvector    [docker container]
ollama                 [native, mxbai-embed-large warmed]
cartograph_mcp.server  [optional — for orch + resolver MCP needs]
trigger_management.main
admin_ui.server
agent_management.main --mode=central
                        ↓ spawns:
                          • orchestrator
                          • resolver
                          (NO troop-pinned agents)
central_watchdog       [stale-troop + stale-agent detection]
```

### 5.2 Troop host (one process tree per claimed troop)

```
troop_daemon --troop-id=<auto-claimed>
              [CAS claim, heartbeat loop, lease management]
cartograph_mcp.server
              [points at central postgres, embeds via central ollama]
agent_management.main --mode=troop --troop-id=<claimed>
              ↓ spawns:
                • machine_manager (one, auto)
                • iterators       (per-plane, scheduler-pinned)
                • smes            (per-component, scheduler-pinned)
workspace_sync_daemon
              [yield-aligned S3 sync of workspace/* and ~/.claude/projects/*/]
```

### 5.3 `main.py` mode flag

One `agent_management/main.py` script handles both modes via
`--mode={central,troop}` (or `CARTOGRAPH_MODE` env):

- `central` — auto-spawn orch + resolver if missing; invoke_loop
  filters `WHERE troop_id IS NULL`; runs the central watchdog.
- `troop` — invoke_loop filters `WHERE troop_id = $MY_TROOP_ID AND
  $MY_LEASE_TERM = troops.lease_term`; auto-spawn machine_manager
  if missing on this troop.

---

## 6. Agent placement matrix

| Agent type | Cohort | troop_id | Singleton? | Workspace |
|---|---|---|---|---|
| orchestrator | central | `NULL` | yes (fleet) | light |
| resolver | central | `NULL` | yes (fleet) | light |
| iterator | troop | per-plane scheduler pick | one per plane | medium (cached API responses) |
| machine_manager | troop | exactly the troop's id | one per troop | none (state in `troops.metadata`) |
| sme | troop | scheduler pick at spawn | per active component | heavy (cloned repos, MERGE_LOG, handoffs) |

### 6.1 Scheduler at `bulk_spawn_smes` / `create_agent`

When a non-singleton agent is created and its target cohort is
"troop", pick a troop:

```sql
SELECT id FROM troops
 WHERE status = 'claimed'           -- has a live lease holder
   AND (SELECT COUNT(*) FROM agent_runs ar
         WHERE ar.troop_id = troops.id
           AND ar.status IN ('idle','running')) < capacity
 ORDER BY (SELECT COUNT(*) FROM agent_runs ar
            WHERE ar.troop_id = troops.id
              AND ar.status IN ('idle','running')) ASC,
          id ASC
 LIMIT 1;
```

Stamp `agent_runs.troop_id` to the picked id. Agent stays on that
troop for its lifetime (workspace pinning).

---

## 7. Workspace + JSONL S3 sync

### 7.1 What needs to travel

| Path | Source | Destination |
|---|---|---|
| `<workspace_root>/<agent_id>/` | troop FS | `s3://cartograph/troops/<troop_id>/agents/<agent_id>/workspace.tar.gz` |
| `~/.claude/projects/<cwd-encoded>/<session_id>.jsonl` | troop FS | `s3://cartograph/troops/<troop_id>/agents/<agent_id>/session.jsonl` |
| `~/.claude/.credentials.json` | troop FS | `s3://cartograph/troops/<troop_id>/auth.json` (encrypted) |

### 7.2 Path canonicalisation — hard requirement

Every troop machine MUST:
- Set `<workspace_root>` to the same absolute path. Recommend
  `/srv/cartograph/workspaces/<agent_id>/`.
- Set `~/.claude/projects/` to `$HOME/.claude/projects/` where
  `$HOME` is identical across troops. Recommend running everything
  under a dedicated user (e.g. `cartograph`) so `$HOME =
  /home/cartograph` everywhere.

Without canonical paths, the JSONL path encoded in
`~/.claude/projects/<cwd-encoded>/` differs between machines, and
`claude -p --resume <session_id>` won't find the transcript.

### 7.3 Sync cadence — yield-aligned

The cleanest cadence is **on agent yield**:

```python
# In agent_management.invoke_loop, after subprocess returns + status='idle':
sync_to_s3(
    agent_id=agent_id,
    troop_id=troop_id,
    workspace_dir=f"{workspace_root}/{agent_id}/",
    jsonl_path=f"{HOME}/.claude/projects/{cwd_encoded}/{session_id}.jsonl",
)
```

- Agent state at-rest is consistent at yield (subprocess exited).
- JSONL is append-only → upload diff is cheap.
- Workspace tar takes seconds for typical sizes.

If a physical host crashes between yields, the latest state in S3 is
as-of-last-yield. The in-flight wake's progress is lost. Acceptable.

### 7.4 Restore on troop claim

```python
# In troop_daemon, after successful CAS claim:
for agent_row in db.execute("SELECT * FROM agent_runs WHERE troop_id=%s", troop_id):
    download_from_s3(
        s3_key=f"troops/{troop_id}/agents/{agent_row.agent_id}/workspace.tar.gz",
        local_path=f"{workspace_root}/{agent_row.agent_id}/",
    )
    download_from_s3(
        s3_key=f"troops/{troop_id}/agents/{agent_row.agent_id}/session.jsonl",
        local_path=f"{HOME}/.claude/projects/{cwd_encoded}/{agent_row.session_id}.jsonl",
    )
# THEN start MCP server, agent_manager, workspace sync daemon
```

---

## 8. Tool installation flow

### 8.1 Deterministic baseline (shell bootstrap)

When a physical host comes up to host a troop, a shell script runs
BEFORE the troop daemon:

```bash
#!/bin/bash
set -e
apt-get update
apt-get install -y git python3.10 python3-pip jq curl rsync awscli
pip3 install -r /srv/cartograph/requirements.txt
# ... any other deterministic baseline
```

Result: every troop machine starts with the same baseline tools.

### 8.2 Ad-hoc install (machine_manager-driven)

When an SME hits a tool that's not in the baseline:

```
SME on troop-2: raise_blocker("need helm to parse this Helm chart")
   ↓
Orch sees blocker:
   - Reads troops.metadata.installed_tools for troop-2 → no helm
   - Tasks machine_manager-troop-2: "install helm"
   ↓
machine_manager-troop-2:
   - bash: "brew install helm" (or apt-get / curl-and-install)
   - Updates troops.metadata.installed_tools to include "helm@3.12"
   - Closes the task
   ↓
SME re-wakes via task transition, retries the work.
```

### 8.3 Re-bootstrap on physical host swap

When troop-2 is claimed by a fresh physical host (e.g. M1 died, M2
takes over):

```
troop_daemon on M2: CAS claim → lease_term bumps to N+1
   ↓
Reads troops.metadata.installed_tools = ["helm@3.12", "kubectl@1.30", ...]
   ↓
Spawns machine_manager-troop-2 with task:
   "Re-bootstrap troop-2: install [helm@3.12, kubectl@1.30, ...] in order"
   ↓
machine_manager replays each install (idempotent — apt/brew noop on
already-installed). Closes task.
   ↓
Pinned SMEs/iterators on troop-2 wake into a usable runtime.
```

Idempotency matters: `brew install helm` on a box that already has
helm should be a noop, not an error. Bake the idempotency into
the install commands the machine_manager runs.

---

## 9. Failover + draining

### 9.1 Planned drain (graceful)

```
Admin: "drain troop-2"
   ↓
Set troops.status = 'draining' on troop-2 (no new agent pickups)
   ↓
Wait for in-flight agents to yield (status='idle') — could be minutes
   ↓
For each agent on troop-2: sync workspace + JSONL to S3
   ↓
Re-pin: SET agent_runs.troop_id = NULL (unpins)
   ↓
Scheduler picks new troops for each unpinned agent (re-pin)
   ↓
Each newly-pinned troop's daemon pulls workspace + JSONL on next
heartbeat-driven check, agents resume
   ↓
troop-2 daemon releases lease (status='available' → 'retired' if
permanent shutdown)
```

### 9.2 Unplanned crash (M1 dies)

```
M1's heartbeat goes stale (>60s)
   ↓
Central watchdog flips troops.status: 'claimed' → 'available' for
M1's troop
   ↓
M2 (new physical host or already-running idle host) CAS-claims the
troop → lease_term bumps
   ↓
M2's troop daemon pulls workspace + JSONL from S3 (latest = as-of-
last-yield)
   ↓
M2's machine_manager re-bootstraps installed_tools[]
   ↓
M2's agent_manager picks up pinned agents, in-flight wakes from M1
are lost (had they been mid-tool-call when M1 died, that wake's
progress is gone)
   ↓
Recovery scanner moves any agents that errored mid-wake back to
idle for retry
```

### 9.3 Guarantees

- **Worst-case data loss on crash:** the in-flight wake at the moment
  of death. Anything yielded before crash is in S3.
- **No double-pickup risk:** lease_term guards every write. Old M1
  writes from before the crash (delayed by network) get rejected
  by the term mismatch.
- **No singleton split-brain on orch/resolver:** both sit on central,
  not on troops. Central HA is a separate problem (Phase 12+).

---

## 10. Singleton enforcement (orch + resolver)

Both stay central in v1. Future: if central needs HA, add a similar
lease pattern on a `central` row in `troops` (status='claimed'
allows boot of orch + resolver only when this physical host holds
the central lease).

For v1, just one central host. If it goes down, agents can't be
woken (trigger manager + central watchdog also down). Acceptable
single point of failure for v1; document it.

---

## 11. Diagrams

### 11.1 Component layout

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          CENTRAL ADMIN HOST                              │
│                                                                          │
│   ┌──────────────────┐  ┌─────────────┐  ┌──────────────┐               │
│   │ Postgres+pgvector│  │   Ollama    │  │  Admin UI    │               │
│   │   :5432          │  │   :11434    │  │  :8200       │               │
│   └────────┬─────────┘  └──────┬──────┘  └──────┬───────┘               │
│            │                   │                │                       │
│   ┌────────┴───────────────────┴────────────────┴───────┐               │
│   │  Trigger manager  ·  Watchdog  ·  Recovery scanner   │               │
│   └─────────────────────────┬───────────────────────────┘               │
│                             │                                           │
│   ┌─────────────────────────┴───────────────────────────┐               │
│   │  agent_management.main --mode=central                │               │
│   │   ↓ spawns:                                           │               │
│   │     orchestrator  ·  resolver  (singletons)          │               │
│   └──────────────────────────────────────────────────────┘               │
└─────────────────────────────────────────────────────────────────────────┘
        ▲                          ▲                          ▲
        │ Postgres                 │ Postgres                 │ Postgres
        │ Ollama                   │ Ollama                   │ Ollama
        │                          │                          │
┌───────┴───────┐         ┌────────┴───────┐        ┌─────────┴───────┐
│   TROOP-1     │         │    TROOP-2     │        │    TROOP-3      │
│ (host: m1)    │         │  (host: m4)    │        │  (host: m9)     │
│               │         │                │        │                 │
│ troop_daemon  │         │ troop_daemon   │        │ troop_daemon    │
│ MCP :8100     │         │ MCP :8100      │        │ MCP :8100       │
│ invoke_loop   │         │ invoke_loop    │        │ invoke_loop     │
│   --troop=1   │         │  --troop=2     │        │  --troop=3      │
│ workspace_sync│         │ workspace_sync │        │ workspace_sync  │
│               │         │                │        │                 │
│ machine_mgr   │         │ machine_mgr    │        │ machine_mgr     │
│ iter-github   │         │ iter-cloud     │        │ iter-tel        │
│ sme-x, sme-y  │         │ sme-z, sme-w   │        │ sme-q, sme-r    │
└───────────────┘         └────────────────┘        └─────────────────┘
        ▼                          ▼                          ▼
        S3:                        S3:                        S3:
        troops/1/agents/...        troops/2/agents/...        troops/3/agents/...
        (workspace + JSONL)        (workspace + JSONL)        (workspace + JSONL)
```

### 11.2 Lease lifecycle for one troop

```
                  ┌─────────────┐
                  │  available  │ ◄────────── retired (terminal)
                  └─────┬───────┘                  ▲
                        │ CAS claim                │
                        │ (term++, status='claimed')│
                        ▼                          │ admin retire
                  ┌─────────────┐                  │
        ┌────────►│   claimed   │──────────────────┤
        │ heartbeat   │                            │
        │ keeps fresh │                            │
        │             │ admin drain                │
        │             │ (status='draining')        │
        │             ▼                            │
        │       ┌─────────────┐                    │
        │       │  draining   │ ───────────────────┤
        │       └─────┬───────┘                    │
        │             │ all agents migrated        │
        │             │ (status='available')       │
        │             ▼                            │
        │       (back to available)                │
        │                                          │
        │  heartbeat stale (>60s)                  │
        │  central watchdog flips:                 │
        │  status='claimed' → 'available'          │
        └──────────────────────────────────────────┘

Each (status='available' → status='claimed') CAS bumps lease_term.
Old lease holder's writes carry stale term → rejected by term-guard.
```

### 11.3 Failover sequence

```
TIME    M1 (holding troop-2)            M2 (idle physical host)        Central watchdog
─────   ──────────────────────           ──────────────────────         ────────────────
T0      heartbeat OK                     idle                            scanning troops
T1      heartbeat OK
T2      💥 process / network dies
T3      (silent)                                                         scanning troops
T4      (silent)                                                         scanning troops
T5      (silent)                                                         scanning troops
        ...
T+60s   (silent)                                                         troop-2 stale →
                                                                         flip 'claimed'
                                                                         → 'available'
T+61s                                    CAS claim troop-2 succeeds
                                         lease_term++ (e.g. 17 → 18)
T+62s                                    pull workspaces + JSONLs
                                         from S3 for pinned agents
T+70s                                    machine_manager-troop-2
                                         re-bootstraps installed_tools
T+90s                                    invoke_loop --troop-id=troop-2
                                         starts picking up pinned agents
                                         (with lease_term=18 in queries)

If M1 partially recovers and tries to write at T+100s:
  UPDATE ... WHERE lease_term = 17 (the term M1 remembered)
  → 0 rows updated (current term is 18)
  → M1 daemon notices, exits cleanly. No double-pickup.
```

### 11.4 Tool blocker flow with machine_manager

```
SME on troop-2                  ORCHESTRATOR (central)            machine_manager-troop-2
──────────────                  ──────────────────────            ───────────────────────
                                                                  (idle on troop-2)
materialising fav2-api
hits "helm" missing
   │
   │ raise_blocker(
   │   "need helm for chart")
   │
   ▼
   task BW → BO

                                detects new BO blocker
                                   │
                                   │ reads troops WHERE id='troop-2'
                                   │ → installed_tools no 'helm'
                                   │
                                   │ create_task(
                                   │   owner=orch,
                                   │   worker=machine_manager-troop-2,
                                   │   description="install helm")
                                   │
                                   ▼
                                                                  woken via BW task
                                                                     │
                                                                     │ bash "brew install helm"
                                                                     │ updates troops.metadata
                                                                     │   .installed_tools[]
                                                                     │   += "helm@3.12"
                                                                     │ respond_task → TC
                                                                     │
                                                                     ▼
                                                                  yields

                                detects task TC
                                   │
                                   │ resolves SME's blocker:
                                   │ respond_task on SME's task
                                   │   BO → BW
                                   │
                                   ▼
woken via BW transition
   │
   │ retries materialisation
   │ helm now in PATH ✓
   │
   ▼
   task BW → WD
```

---

## 12. Open questions

These are explicit design decisions to nail before code starts on
Phase 11:

1. **Workspace sync cadence.** Yield-aligned (recommended) vs
   continuous fs-watch. Trade-off documented in §7.3.
2. **Region affinity for scheduling.** `troops.region` column exists
   but is the scheduler region-aware? For single-region v1, ignore.
   For multi-region, scheduler should prefer same-region troops for
   peer SMEs to minimise consolidation chat latency.
3. **machine_manager spawn discipline.** Auto-spawned by troop_daemon
   on lease acquisition (recommended), or by orch via
   `bulk_spawn_smes`-style? Auto by daemon is simpler; doesn't
   require orch to know about troops.
4. **`workspace_root` config.** Hard-code to `/srv/cartograph/workspaces`
   in production, or env-driven? Env-driven for flexibility, but
   document the canonicalisation requirement.
5. **S3 vs alternatives.** S3 assumed throughout. Could equally be
   GCS, Azure Blob, MinIO. Abstract via a small `object_store.py`
   so teams can swap.
6. **Watchdog placement.** Central (recommended) vs per-troop.
   Central is simpler — one process owns the "is troop X alive"
   decision. Per-troop watchdogs would conflict on the troop status
   flip.
7. **Lease TTL.** 60s recommended. Heartbeat every 15s (4× headroom).
   Too short → false-positive failover during network blips.
   Too long → real failures take longer to recover.
8. **Iterator install role removal.** Phase 11 is a clean break
   (iter loses install perm) or backwards-compat (machine_manager
   primary, iter still allowed)? Clean break is simpler; needs
   prompt updates in iter.py + sme.py + orch.py to reflect that
   "install blockers go to machine_manager".

---

## 13. Sub-phase plan

| # | Subject | Effort | Notes |
|---|---|---|---|
| 11.0 | Design doc (this file) + open-question resolution | S | sign-off needed before coding |
| 11.1 | `troops` table + `agent_runs.troop_id` migration + lease protocol library | M | `shared/lease.py` for CAS claim + heartbeat + term-guarded writes |
| 11.2 | `agent_management.main --mode=…` + invoke_loop troop filter | S | one new env / arg, two SQL filter clauses |
| 11.3 | `machine_manager` agent type + new `agent_types/machine_manager.py` prompt | M | install permission moves here; iter.py loses it |
| 11.4 | Workspace + JSONL S3 sync daemon (yield-aligned upload + claim-time download) | M-L | biggest single effort; canonical paths + JSONL filename encoding |
| 11.5 | troop_daemon (CAS claim + heartbeat + lease loss detection) | S | shell or python; runs on every troop host |
| 11.6 | Central watchdog: stale-troop detection (separate from existing stale-agent watchdog) | S | one SQL UPDATE on a timer |
| 11.7 | Scheduler in `bulk_spawn_smes` / `create_agent` (pick troop by load) | S | one query change |
| 11.8 | Admin UI: troops tab (live status, lease holder, agent placement, drain button) | M | new view |
| 11.9 | Doc sync (HLD, SCHEMA, TRIGGER-MGMT, AGENT-PROMPTS, POST-COMPACTION, PROMPT-ENHANCEMENTS) | S | mechanical |
| 11.10 | DEMO11 verification | M | needs ≥2 troop hosts (or 2 docker containers) running disjoint agent sets, with one drained mid-run |

Total: ~M-L overall. Single biggest effort is 11.4 (sync daemon).
Lease protocol (11.1) is the second biggest — get it right or split-
brain bugs later.

---

## 14. What this is NOT

- **Not a database HA story.** Central Postgres is a single point of
  failure in v1. HA Postgres / read replicas / Patroni are Phase 12+.
- **Not a multi-region story.** Single-region v1; multi-region needs
  region affinity in scheduler + S3 region selection + Ollama
  region-local instance per region.
- **Not live workspace migration.** Cold migration only — agent must
  be at-yield before sync. Hot migration (mid-tool-call) deliberately
  out of scope; killed wake is acceptable.
- **Not a generic K8s deployment.** Could later run on K8s with
  StatefulSets per troop, but Phase 11 doesn't assume any specific
  orchestrator. troop_daemon + central watchdog are
  orchestrator-agnostic.

---

End of design.

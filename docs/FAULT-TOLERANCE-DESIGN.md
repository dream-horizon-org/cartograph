# Cartograph — Fault Tolerance Design (backup / restore / fencing)

> **Status: DESIGN (2026-05-22).** No code shipped. Captures what we build, why,
> and the schema/flow shape, converged through design review. Companion to
> `PHASE-11-TROOPS-DESIGN.md` — that doc is the *multi-machine* (troops + S3 +
> lease_term) future; THIS doc is *single-machine* fault tolerance: one box dies,
> relaunch from an AMI, resume cleanly, never split-brain. The lease/fencing
> here is the single-box specialisation of the troops lease.
>
> **POC done (2026-05-21):** an AMI taken of the agent box, relaunched as a new
> instance, **persists everything** — installed CLIs, Claude Code session files,
> cwd. So "image the box → relaunch → state is there" is proven. This doc is
> what we wrap around that to make it *safe and automatic*.

---

## 0. Why — the problem

Cartograph runs as a long-lived fleet of agent subprocesses against a Postgres/
Aurora DB. We need it to survive:
- The **agent box dying** (crash, OOM, spot reclaim, maintenance) → relaunch on a
  fresh instance and resume.
- **Planned checkpoints** → periodic, app-consistent backups we can restore to.
- The **silent killer**: a relaunched box that starts working while the old box
  is *still alive / twitching back* → two fleets writing the same DB →
  double-pickup, conflicting mutations, corruption (**split-brain**).

This is a Dream11-onboarding productionalization touchpoint (see
`PRODUCTIONALIZATION.md → Fault Tolerance`).

---

## 1. The two reframes that shape everything

### 1.1 Two failure classes — not one

| Case | Frequency | What you actually do |
|---|---|---|
| **Agent box dies, Aurora alive** | common | Relaunch box from AMI → **claim the lease** → reconnect to the *same live Aurora* → recovery scanner + STML re-wake the interrupted agents. **No DB restore.** |
| **Aurora itself lost / corrupt** | rare | Restore Aurora from PITR / snapshot, then the fleet realigns. |

The AMI of the box **does not contain Aurora**. Aurora has its own backup
(RDS-managed PITR + snapshots). In the common case you don't restore a "pair" —
you just need a fast box + the lease + the still-live Aurora.

**Implication: Aurora PITR is the real backbone of correctness, and it's
RDS-managed (continuous, internally consistent, near-free).** Don't rebuild it.
Everything in this doc is the *agent-box* side + the *coordination* (lease/pause)
that lives in Aurora.

### 1.2 What already exists (we're not starting from zero)

Cartograph is already unusually crash-tolerant because **the DB is the source of
truth, not agent memory** (3 state layers):
- **Postgres/Aurora** — correctness-critical. Backed up independently (PITR).
- **Agent cwd** (`workspaces/<agent>/`: cloned repos, MERGE_LOG, handoffs) —
  degraded-recoverable (re-clone, re-derive from DB).
- **Claude session JSONL** (`~/.claude/projects/<cwd>/<session>.jsonl`) —
  degraded-recoverable (cold-start, re-derive from DB). Resume is *continuity*,
  not *correctness*.

Existing crash-safety primitives we lean on:
- `cascade_completed_at` guard (Phase 10.14.3) — `execute_mutation` refuses M→MD
  unless the cascade ran → **no silent half-merge** even if an agent dies
  mid-mutation.
- **Recovery scanner** (errored → backoff → idle) + **stale-heartbeat watchdog**
  + **startup orphan-reset** (`main.py` flips leftover `running`→`errored` on
  boot).

A killed agent loses only its **in-flight wake's progress**; on re-wake it
re-reads current state from Aurora and continues.

---

## 2. The crux — lease / fencing token

The one genuinely-new must-build. Its absence is the only thing that causes
*corruption* (split-brain). Everything else (pause/drain/snapshot) is
quality-of-restore on top.

### 2.1 Mechanism

A single-row coordination table in **Aurora** (the only thing both old and new
boxes can see — never a local file). The **EC2 `instance_id` is the fencing
token** — it's globally unique and never reused (an AMI relaunch is always a new
id), so it doubles as both "who holds the lease" and the token writes are
guarded against. No separate `lease_term` needed for single-box.

```sql
CREATE TABLE cartograph_lease (
    id                  INT PRIMARY KEY DEFAULT 1 CHECK (id = 1),  -- single row
    instance_id         TEXT,            -- current holder = the fencing token (NULL = unheld)
    last_updated_at     TIMESTAMPTZ,     -- lease heartbeat; compared against DB now() ONLY
    paused              BOOLEAN NOT NULL DEFAULT FALSE,
    pause_expires_at    TIMESTAMPTZ,     -- dead-man's switch for pause (see §3)
    instance_started_at TIMESTAMPTZ,     -- when the current holder booted
    snapshot_taken_at   TIMESTAMPTZ      -- when the last backup image was taken (STML anchor, §4)
);
INSERT INTO cartograph_lease (id) VALUES (1) ON CONFLICT DO NOTHING;
```

### 2.2 Who does what

- **AM acquires + renews** the lease (a heartbeat thread, every ~1 min, on top of
  its normal invoking).
- **TM only reads it** — each cycle it checks "is `instance_id` = my id?" and
  scans only if yes. TM never acquires/renews, so it **no-ops until AM has
  acquired**, then follows.
- **Why AM, not a 3rd/backup daemon:** whatever process renews the lease becomes
  a thing whose death stales the lease and halts the *whole* fleet. So the
  renewer must be *already-essential* (AM — if it's dead, nothing runs anyway).
  A dedicated lease daemon or the backup daemon would be a *non-essential*
  process whose death takes down a healthy fleet — a new failure mode for no
  gain.

### 2.3 The four operations

**Acquire / steal (boot, and retried while unheld):**
```sql
UPDATE cartograph_lease
   SET instance_id = :me, last_updated_at = now()
 WHERE id = 1
   AND (instance_id IS NULL OR last_updated_at < now() - interval '5 minutes')
RETURNING instance_id;
```
0 rows → old box still alive (fresh heartbeat) → wait + retry. Staleness is
**always** compared to Aurora's `now()` (one clock, no skew between boxes).

**Renew (AM, ~every 1 min):**
```sql
UPDATE cartograph_lease SET last_updated_at = now()
 WHERE id = 1 AND instance_id = :me;
```
0 rows → another box stole it → **I lost the lease** → AM SIGTERMs its in-flight
`claude -p` children + stops (TM follows, sees `instance_id != me`, stops).

**Gate (every TM scan + every AM pickup):** proceed only if `instance_id = :me`.

**Fence-on-write (CAS-on-write):** AM's pickup UPDATE carries
`AND (SELECT instance_id FROM cartograph_lease) = :me`. Even if the renewer
missed a beat and another box stole, a stale write returns 0 rows → the old box
detects loss and exits. **This is the real split-brain protection** — the
renewer's location is secondary to this guard. (Mostly theoretical at single-box;
load-bearing once multi-box per `PHASE-11-TROOPS-DESIGN.md`.)

5-min TTL / 1-min renew = 5× headroom. Trade: a real crash takes up to 5 min
before a new box can steal. Fine for this workload (tune via config).

---

## 3. Backup flow — pause → drain → snapshot → resume

A **backup daemon** (separate process; non-essential) triggers this on a cadence
(hourly, configurable). It does NOT hold the lease.

```
1. PAUSE   set paused=TRUE, pause_expires_at=now()+N; snapshot the active-agent set
           (agent_ids currently 'running'). Daemon re-extends pause_expires_at
           every ~1 min for the whole window (dead-man's switch).
              → TM stops scanning, AM stops claiming  (both: respect paused
                UNLESS pause_expires_at < now())

2. DRAIN   running agents finish their current wake naturally → go idle.
           Accelerator: the PostToolUse hook (§5) sees paused and injects
           "wrap up + yield, you'll be re-woken" so agents end early instead of
           running the full 1800s. Mid-mutation yield is SAFE (cascade_completed_at
           guard + atomic tool calls).
           Poll until `count(status='running') = 0`, BOUNDED. If the bound is
           exceeded, snapshot crash-consistent anyway (architecture tolerates it).

3. MARK    once drained, flip the recorded active-set idle→paused (race-free —
           done AFTER they're idle, so the clean-exit →idle write can't clobber).
           See §3.1 for why 'paused' exists.

4. SNAPSHOT  `aws ec2 create-image --no-reboot` of the agent box; record
             snapshot_taken_at = now() in cartograph_lease.
             (Aurora: rely on PITR; optionally also take an aligned RDS snapshot
              in this quiesced window if you want a true restore pair.)

5. RESUME  clear paused (in a `finally` — must run even if snapshot throws).
           TM resumes scanning; explicitly re-wakes the 'paused' agents (locks
           them), setting each back to idle as it's picked up.
```

### 3.1 Why `paused` is a real status (not just the global flag)

The global flag handles "stop scheduling." `paused` solves a different problem:
**re-waking agents whose in-flight work isn't backed by any trigger-able row.**
Example: admin chats an agent "also go clean up X"; the agent acks the chat and
works on X across wakes — but X has **no** task / clarification / consolidation
row. Normal trigger conditions won't re-wake it. If it drains to plain `idle`,
**X is silently dropped.** Marking it `paused` = "this agent was active; re-wake
it explicitly on resume." (Twin of STML in §4 — same "re-wake the agents that
were active" idea, different flow: `paused` = planned-backup, normal re-wake;
`STML` = crash-restore, catch-up re-wake.)

### 3.2 Pause dead-man's switch

If the backup daemon dies after setting `paused` but before `RESUME`, the fleet
would freeze forever. `pause_expires_at` prevents that: TM/AM treat pause as
cleared once `pause_expires_at < now()`. The daemon keeps extending it while it
works; daemon death → no more extensions → fleet auto-resumes in N min. One
timestamp; same heartbeat pattern as the lease.

---

## 4. Restore flow + STML (short-term memory loss)

New box boots from the latest AMI. **Fence before you mutate** — never touch
shared state until you've won the lease.

```
1. BOOT    AM/TM do NOT operate (no lease yet). Userdata fetches a FRESH Bedrock
           token from Secrets Manager (§6) — never the baked-in one.

2. CLAIM   AM CAS-claims the lease (§2.3). Retry until won. Winning fences the
           old box (its writes now fail the holder-check).

3. STML    only AFTER winning:
             instance_started_at = now()
             UPDATE agent_runs SET status='STML'
              WHERE last_invoked_at > snapshot_taken_at
           = every agent that woke after the image was taken → its session JSONL
           is stale vs live Aurora (the "lost window"). 'running' is the subset
           that was mid-wake; this catches those PLUS agents that worked-then-idled
           in the window.

4. START   AM + TM start (now gated on lease = me). TM locks STML + idle agents.

5. CATCH-UP  AM invokes an STML agent with a lightweight catch-up prompt:
             "You resumed from a possibly-stale snapshot. Work you did between
              <snapshot_taken_at> and the crash is SAFE in the DB but missing from
              your session memory. Re-read your current state (get_my_components /
              get_action_items_detail / get_unresolved / your workspace) and use
              read_activity_log to see your recent tool calls. Continue."
           On a clean yield → status flips to idle (STML clears).
```

### 4.1 Why STML — and what it is / isn't

The gap is real: snapshot at T-60min, crash at T → the restored box's session
files are up to an hour behind live Aurora. **But the hour of *work* is not lost
— it's in Aurora** (every `upsert_*` / `nominate` / `respond` wrote there as it
happened, and Aurora is live, not restored). What's lost is the agent's
**in-context memory** of having done it.

So STML is a **warm-restore optimisation, not a correctness gate**: the agent
recovers the *work* by re-reading Aurora on its normal wake (it does this every
wake anyway); STML-relay just hands it "here's what you did in the lost window"
so it skips re-discovering. Worst case without STML = some redo-work, never data
loss. This is why STML can be truncated/imperfect without risk.

**Consequence of no-EFS:** because session JSONLs live on the box's EBS (captured
stale in the AMI), the lost-window JSONL lines die with the old box. So the
`agent_activity_log` (§5) must store **truncated results inline in Aurora** — a
"pointer to the JSONL" is dead for exactly the lost window. (We chose no-EFS:
EFS would keep sessions live and shrink the gap, but it doesn't solve binaries or
ports, so it's added infra for a partial win. Single-AMI + STML is the coherent
alternative.)

---

## 5. Activity logging (the agent-level request/response log)

Powers STML catch-up + observability/forensics. Distinct from `mcp_audit`:
**`mcp_audit` logs only cartograph-db MCP tool calls (args hashed)** — it does
NOT capture **bash**, Read/Write/Edit/Glob/Grep, or the plane-reader MCPs
(github-reader / last9). We need the full agent-level picture, so a new table +
hook, NOT a reuse of mcp_audit (which stays as-is).

```sql
CREATE TABLE agent_activity_log (
    id            BIGSERIAL PRIMARY KEY,
    agent_id      TEXT NOT NULL,
    ts            TIMESTAMPTZ NOT NULL DEFAULT now(),
    tool_name     TEXT NOT NULL,          -- incl. Bash, Read, MCP tools, external MCP
    request       TEXT,                   -- tool_input, truncated (e.g. 4KB)
    response      TEXT,                   -- tool_response, truncated (e.g. 4KB) — INLINE
    jsonl_pointer TEXT                    -- optional: full record locator (live-system forensics only;
                                          -- DEAD for the lost window on restore)
);
CREATE INDEX idx_activity_agent_ts ON agent_activity_log (agent_id, ts DESC);
```

- **Source:** a **PostToolUse hook with matcher `*`** (extends the existing
  `notify.py` pattern). The hook receives `tool_name + tool_input +
  tool_response` for *every* tool type → writes one row.
- **Cost discipline:** fires on every tool call → must be **async / fire-and-
  forget** (never block the agent's tool call; swallow failures like notify.py),
  **truncated**, and **retention-bounded** (weekly partition / N-day drop, like
  mcp_audit). This is the main cost of the no-EFS + log-replay path.

---

## 6. Things outside the box image (must be in the restore runbook)

- **Bedrock token must NOT be baked into the AMI** — it rotates ~12h. A restored
  box that boots with the baked token mass-errors the *entire fleet* (see the
  errored-flow: every agent's `claude -p` fails auth → errored → recovery churns
  the 60/300/1800 backoff futilely). Userdata must **fetch the current token from
  Secrets Manager on boot** + a steady-state refresh timer re-pulls + bounces AM.
  This is the single most likely thing to mass-error the fleet, backup or not.
- **Path canonicalisation is load-bearing for warm resume.** `--resume` only
  finds the transcript if absolute paths are byte-identical (same user, same
  `workspace_root`). The POC works *because* an AMI clone = identical paths.
  Document it as a hard constraint — don't change the mount path / home dir.
- **Binaries:** OS + predictable CLIs baked into the AMI; **ad-hoc agent-installed
  CLIs** (land in `/usr/local/bin`, lost on relaunch) handled via an
  **install-manifest replay on boot** (the Phase 11 `machine_manager` /
  `installed_tools[]` idea). EFS does not help here.
- **In-flight wake at crash = lost, by design** → recovery scanner re-wakes;
  STML relays the lost window.

---

## 7. Schema changes (consolidated)

```sql
-- 1. lease / coordination (single row) — §2.1
CREATE TABLE cartograph_lease ( ... );   -- instance_id, last_updated_at, paused,
                                         -- pause_expires_at, instance_started_at,
                                         -- snapshot_taken_at

-- 2. agent activity log — §5
CREATE TABLE agent_activity_log ( ... ); -- agent_id, ts, tool_name, request,
                                         -- response (inline trunc), jsonl_pointer?

-- 3. agent_runs additions
ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS last_invoked_at TIMESTAMPTZ;
--    stamped at AM pickup (alongside heartbeat / invocation_count++);
--    the STML anchor (last_invoked_at > snapshot_taken_at).
-- status CHECK extended: idle | running | errored | decommissioned
--                        + 'paused' (backup re-wake) + 'STML' (restore catch-up)
```

All migrations idempotent (`IF NOT EXISTS`), zero data migration — additive only.

---

## 8. New MCP tool

- **`read_activity_log(agent_id, since?, until?, page?, page_size?)`** — paginated
  + truncated read of `agent_activity_log` for an agent over a time window. The
  STML catch-up agent calls it with `since=snapshot_taken_at` to reconstruct its
  lost window. Lean rows (truncated request/response); no full payloads.

(`status` values, the lease, and pause are read internally by AM/TM/hook — not
agent-facing tools.)

---

## 9. Tiering — build order

| Tier | Contents | Status |
|---|---|---|
| **Tier 1 (MVP, ~70% already exists)** | Aurora PITR (RDS-managed) + scheduled `create-image --no-reboot` (DLM, no pause) + **lease/fencing** + existing recovery scanner | the lease is the only new must-build; correct restore, possible cold-starts |
| **Tier 2 (warm + consistent)** | pause→drain→snapshot ceremony + `paused` re-wake + `agent_activity_log` + STML catch-up + aligned box/Aurora pair | app-consistent warm restore, no cold-starts, untracked-work survives |

Ship Tier 1 first (lease fences split-brain — the only corruption risk); layer
Tier 2 for restore quality.

---

## 10. Failure-mode walkthrough (guarantees)

| Scenario | Outcome |
|---|---|
| Box crashes mid-wake | In-flight wake lost. New box claims lease → STML/recovery re-wakes the agent → re-derives from Aurora. No corruption. |
| Old box "twitches back" after a new box took over | Old box's renew + every pickup write fail the holder-check (`instance_id != me`) → old box exits. **No split-brain.** |
| Backup daemon dies mid-backup | `pause_expires_at` lapses → TM/AM auto-resume in N min. No frozen fleet. |
| Bedrock token expires | Whole fleet errors (every `claude -p` auth fails) → recovery churns. **Mitigated** only by the Secrets-Manager fetch+refresh (§6) — call out as the top operational risk. |
| Agent mid-mutation when paused/killed | `cascade_completed_at` guard → resume runs `execute_mutation` cleanly, or recovery re-drives. No half-merge. |
| Untracked admin-directed work, agent drained | `paused` status → explicit re-wake on resume. Not silently dropped. |

---

## 11. Open questions / decisions

1. **Backup cadence** — agent box barely changes (truth is in Aurora), so a long
   cadence (nightly / pre-risky-op) beats hourly drains. Aurora PITR is
   continuous regardless. Pick cadence.
2. **Aligned pair?** — do we want the Tier-2 quiesced box+Aurora snapshot pair
   (for Aurora-loss / migration), or is PITR-for-DB + independent box AMIs enough?
3. **Activity-log retention + truncation caps** — KB per request/response, days
   of retention, partition cadence.
4. **STML catch-up prompt** — lightweight "go read your logs + Aurora" (cheaper,
   agent self-fetches) vs inject the lost window inline (heavier prompt). Lean
   lightweight.
5. **TTL tuning** — 5 min / 1 min default (slower failover, simpler). Drop toward
   60s / 15s if faster recovery matters (watch false-positive failover on blips).

---

## 12. What this is NOT

- **Not Aurora HA.** Aurora is a managed single endpoint here; HA / read replicas
  are a separate concern (RDS handles failover; PITR handles loss).
- **Not multi-machine.** Single box + single lease. The multi-box version
  (troops, `lease_term`, S3 sync, `machine_manager`) is `PHASE-11-TROOPS-DESIGN.md`
  — this lease is its single-box specialisation and forward-compatible with it.
- **Not live (hot) migration.** Restore is cold — relaunch + claim + resume. A
  killed in-flight wake is acceptable (recovery + STML cover it).

---

End of design.

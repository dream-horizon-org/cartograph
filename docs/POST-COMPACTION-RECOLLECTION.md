# Cartograph — Post-Compaction Recollection (2026-05-12 night, Phase 10.14 + 10.15 + 10.16 SHIPPED, DB wiped pre-run-5)

## 0a. PHASE 10.14 / 10.15 / 10.16 — ALL SHIPPED · DB WIPED · READY FOR RUN #5 (most important)

**Branch HEAD:** `feat/prompt-tuning-and-bug-fixes` after Phase 10.16 commit + doc-sync push.

**Phase 10.16 SHIPPED 2026-05-12 night** — `[ADMIN-HACK-ORDERS-INFERRING]` prompt blocks in `sme.py` + `resolver.py`. Single-plane runs (e.g. github-only) can now spawn inferred non-code stubs (DBs/caches/queues/topics) via the existing split machinery: SME nominates a `type='split'` with `metadata.admin_hack='inferring'` + `inferred=true` + `inferred_kind` + `inferred_identifier`. Resolver pre-flight dedups via vector_search + serialises concurrent noms + validates ordering (inferred-splits AFTER real splits, BEFORE merges). Child SME hydrates from parent's repo + parent's attributions via the `split_briefing` hydration manual. **Prompt-only — no schema, no tool surface change.** Tagged with `metadata.admin_hack='inferring'` for grep-based cleanup when the real paradigm ships.

**DB wiped + snapshotted pre-run-5.** Snapshot path: `/tmp/cartograph-snapshots/snap-2026-05-12-<ts>-pre-run5.sql`. Workspaces backed up at `src/workspaces.bak.pre-run5.2026-05-12-<ts>/`. Fresh singletons (orch + resolver) auto-bootstrapped on agent_manager restart.

**Phase 10.15 SHIPPED 2026-05-12 evening** — 5 prompt-only P1/P2 rules per admin verdict (abbreviation hallucination guard · monorepo Option A dissolution · cluster doctrine for Aurora/redis/RDS Multi-AZ · telemetry bare-label placeholder · raise_blocker preference). One commit (`a49b2ed`). No schema or tool surface changes. See `docs/IMPLEMENTATION-PHASES.md §10.15` for full detail.

**Items NOT in 10.15 (deferred per admin):**
- O4 `mark_resource_done` precondition gate — admin will pick later.
- 3 live-DB leftover cases — snapshot + wipe before run #5 instead.
- #5(c) `/api/agents` UI dedup bug — separate UI work later.

---

## 0aa. PHASE 10.14 BUG-FIX BUNDLE — SHIPPED earlier this session

**Branch:** `feat/prompt-tuning-and-bug-fixes` (branched from master @ `b13d89b` post-merge of Phase 10.13).

**Phase 10.14 SHIPPED 2026-05-12.** 4 P0s + doc-sync committed. 92/92 mutation + consolidation tests green.

| # | Sub | What | Commit |
|---|---|---|---|
| P0-1 | 10.14.1 | Drop the TEMP lock-step doctrine (kills F1) | `beeba19` |
| P0-4 | 10.14.2 | `spawn_child_agent` mints fresh agent_id server-side (kills #8 wedge class) | `f905384` |
| P0-2 | 10.14.3 | `consolidations.cascade_completed_at` guard (kills F3 silent corruption) | `008c208` |
| P0-3 | 10.14.4 | Attribution cascade auto-dedup + delete cascade_* flags (kills F2 frozen-attrs) | `a96127a` |
| — | 10.14.5 | Doc-sync HLD/SCHEMA/TRIGGER-MGMT/AGENT-PROMPTS/IMPL-PHASES | this commit |
| — | 10.14.6 | Agent verification on current DB state | pending — post doc-sync push |

**Schema delta:** `consolidations.cascade_completed_at TIMESTAMPTZ` (nullable). Migration idempotent; run on dev DB live.

**Tool surface signatures changed (no tool count delta):**
- `absorb_agent`: drops `cascade_attributions` / `cascade_edges` / `cascade_flows` flags. Callers passing any → ValueError.
- `spawn_child_agent`: drops `child_agent_id` arg. Server mints fresh `sme-<8hex>` and returns it. Callers passing the legacy arg → ValueError.
- `execute_mutation`: refuses M→MD unless `cascade_completed_at` stamped by absorb_agent or spawn_child_agent.

**Three failure modes targeted (full detail in `docs/RUN4-ISSUES-AWAITING-VERDICT.md`):**
- **F1** cross-plane merge never nominated → lock-step doctrine fooled SMEs into "no cross-plane discovery"
- **F2** cascade-attribution UNIQUE collision → SME workaround `cascade_attributions=False` → attrs frozen on tombstone (2 cases: fantasy-tour-admin-telemetry 11 attrs · fantasy-tour-admin-aurora-reader 5 attrs)
- **F3** `execute_mutation` before `absorb_agent` → silent corruption (cons `a21f113a`: status=D but both components still active)

**Items deferred (not in 10.14):** #1 abbreviation rule (P2) · #2 monorepo container dissolution (awaiting Option A/B verdict) · #3+NEW-B cluster doctrine (awaiting verdict) · #5(c) UI multi-comp dedup bug · O4 mark_resource_done gate (awaiting strictness verdict) · O3 telemetry bare-redis (simplified to no-deferral).

**Open admin questions remaining:**
1. Cluster doctrine — one component with N hostnames OR N per endpoint?
2. 3 leftover live-DB cases — one-shot SQL repair or accept and wipe before run #5?
3. `mark_resource_done` strictness — hard-refuse vs soft-warn?
4. Monorepo container — Option A (distribute build files + dissolve) vs Option B (reclassify as scaffolding)?

After 10.14 ships + agent-verifies on current DB state, admin will wipe + kick off fresh run #5.

---

## 0b. (PRIOR) RUN #4 STATE — superseded by 0a once 10.14 ships

Branch: **`feat/prompt-tuning-and-bug-fixes`** (branched from master @ `b13d89b` post-merge of Phase 10.13).

**Live DB:** run #4 in flight since 2026-05-11 08:35 UTC. 36 agents, 35 components (5 decom), 311 attributions, 278 comms, 19 consolidations (all status=D but several have silent-failure issues), 13 insights filed during run.

**13 issues identified, NOT YET SHIPPED — awaiting admin verdict** in `docs/RUN4-ISSUES-AWAITING-VERDICT.md`. Summary:

| # | Issue | Priority | Effort |
|---|---|---|---|
| 1 | ft-cm-poller/api wrong-named "Fantasy Tour Contest Management" — cross-plane merge missed | P2 | XS |
| 2 | fav2 monorepo split-before-merge worked ✓ | — | done |
| 3 | Aurora master+reader not merged into one cluster-component | P1 | S |
| 4 | fantasy-tour ↔ fantasy-tour-v1 cross-plane duplicate unmerged | **P0** | S |
| 5 | lineups-v2 standalone telem + spawn collision wedge | P1 | S+XS |
| 6 | fav2-admin telem ↔ feeds-aggregator-v2-admin github unmerged | **P0** | (#4 fix) |
| 7 | fav2-api silent merge "D" without absorb (execute_mutation before absorb_agent) | **P0** | M |
| 8 | spawn_child_agent collision wedges target SME (3 instances) | **P0** | XS |
| O1 | orch waits for admin greenlight even when phase-end heuristic met | P1 | S |
| O2 | absorb_agent cascade_attributions atomic failure on duplicate triple | **P0** | M |
| O3 | Last9 bare-label `redis` (no hostname) — telemetry SME stuck | P2 | XS |
| O4 | SMEs don't self-audit; admin sanity broadcasts needed | P1 | M |
| O5 | Agents prefer chat-to-admin over raise_blocker for tool errors | P2 | XS |

**Suggested minimum to ship:** P0-1 (#7 + O2 mutation guards) + P0-2 (#4/#6 cross-plane sibling-search prompt) + P0-3 (#8 spawn_child pre-check). ~1 day work.

**Awaiting admin verdict before any patches go in.** Post-compact session should pull verdict from user + ship agreed scope.

---



> **Read this FIRST after compaction.** Then `git log --oneline -25`,
> then the 7 canonical docs (HLD / SCHEMA / TRIGGER-MANAGEMENT /
> AGENT-PROMPTS / IMPLEMENTATION-PHASES / ONE-PAGER / PROMPT-ENHANCEMENTS),
> then the 3 memory files. Then this doc. Once oriented, resume.
>
> Goal: a fresh-compacted session resumes coherently without
> replaying thousands of tool calls.

---

## 0. WHERE I AM RIGHT NOW (the most important section)

**Phases 10.8 → 10.13 SHIPPED. Three real-data onboarding attempts run 2026-05-06; Phase 10.13 (post-real-data insight bundle) shipped 2026-05-08 evening across 9 commits. Tool count 114 → 117. One schema migration (attribution UNIQUE → component-scoped). Next: 4th real-data attempt against the new surface.**

**Phase 10.13 sub-commits (all live):**
- `6af36e0` 10.13.2 QR clar asker
- `d8cb4d9` 10.13.9 Kafka queue catalogs
- `3e156bf` 10.13.1 split/merge discipline
- `fae957b` 10.13.3 get_component_owner
- `e81bcca` 10.13.6 attribution UNIQUE → component-scoped
- `0a4d3e6` 10.13.4 + .5 identifier-norm + thin-evidence
- `2303322` 10.13.10 prompt-tightening bundle (7 nudges)
- `8b66d0c` 10.13.8 absorb cascade-collision auto-dedup
- `928de48` 10.13.7 resolve_references_bulk + bind_edges_bulk

### What's done (committed + pushed):

- All Phase 0 → Phase 10.7 shipped on `feat/trigger-manager-cartograh-mcp`.
- DEMO-MEGA ran 2026-05-04: **29 PASS / 1 N/A / 1 VERIFY_PENDING / 0 FAIL** out of 31 phases.
- Phase 10.1.3 (mcp_call_batch name-map + create_edge docstring) shipped `376b27a`. DEMO10 verified: **8/8 PASS** — `ad3830c`.
- Phase 10.7 (description column + filtered vector_search + workspace doc_md + exclude_self) shipped across 8 commits `ec5a318` → `e7ce669`.
- **2026-05-05 just before DEMO11:** SME prompt expanded with **service-document standard for `component_doc_md`** (8 required markdown sections: Role / Key Surfaces / Inbound Flows / Outbound Flows / Runtime+Deploy / Storage+State / Operational Notes / Source). DEMO11 Phase 4 gained a doc_md quality metric. Commit `872b407`. Smoke-test caught a `{token}` brace bug, fixed.
- **DEMO11 ran 2026-05-05** (T+1h47m): 16/16 PASS, 0 FAIL, 0 BUG. Phase 8c.1 was reported PARTIAL initially (admin must send chat to decom; orch can't self-fire); admin closed it inline post-scorecard, flipped PASS. See §17 below for full detail.

### Phase 10.8 — sub-commits (all shipped):

| Sub | Commit | What |
|---|---|---|
| 10.8.0 plan + recall sync | `5e616c9` | doc-only |
| 10.8.1 canonical_name partial UNIQUE | `cd3bc20` | schema + components.py SELECT pre-check + mock_seed ON CONFLICT + 1 new test |
| 10.8.2 broadcast defensive gate | `5d335b0` | `require_active_agent` on get_unacked_broadcasts + scanner returns 0 for decom + 2 new tests |
| 10.8.3 prompt promotions | `8a60df0` | SME (split-child auto-resolve + leave-dangling-don't-force-create) + orch (broadcast-driven coordination) |
| 10.8.4 DEMO11 spec sync | `4a9612b` | `delete_attributions_bulk` lenient semantics in DEMO11 prompt 5b.5 |
| 10.8.5 insight triage | DB-only | 5 promoted, 3 wontfix |
| 10.8.6 DEMO12 targeted prompt + run | `9c3e88c` | 4/4 PASS, 0 BUG, ~3 min wall-clock |
| 10.8.7 final doc sync | `2f54a1e` | §0 + §17 refresh |

### Phase 10.9 / 10.10 / 10.11 / 10.12 — shipped post-Phase-10.8 (this session):

| Sub | Commit | What |
|---|---|---|
| 10.9 admin UI catalog drill-down | `ca7d964` | Collapsible `<details>` sections + paginated tables (20/page); description always-visible blue-bordered; cache-bust v=64 |
| 10.10 sleep rewrite + datastore mandate | `13531d7` | SLEEP rewritten in sme/iter/orch prompts (yield-over-sleep doctrine); ★ DATASTORES MANDATORY ★ block in iterator telemetry section with DEMO7 feeds-v2 breadcrumb |
| 10.10 datastore wording softening | `da49187` | "MIGHT NOT BE PRESENT" not "do NOT appear" — softer framing, mandate intact |
| 10.11 Bedrock model ids + SME lanes 8 | `c04f7c9` | `config.MODEL_OPUS` / `MODEL_SONNET` env constants (sources `ANTHROPIC_DEFAULT_*_MODEL`); all 4 agent types rewired; `INVOKE_LANES_SME` 4→8 (total 12) |
| 10.13.12 iterator ADMIN-SCOPE rule | `8c7fd30` | iterator.py new block: if admin gives narrow target list, treat as exhaustive scope — `upsert_resource(s_bulk)` writes filtered to (named targets ∪ their dependency-linked datastores/caches/queues). Full plane enumeration dumps to `./<plane>_seen.json` workspace file (not the resources table). Report `seen vs upserted` counts. Trims ~50 LOC of admin kickoff boilerplate per run. iter prompt 37783 → 38973. |
| 10.13.13 SME lanes 8 → 12 | `89ec1c4` | `INVOKE_LANES_SME` 8→12 ahead of 4th real-data run; total concurrent subprocesses 12 → 16 (orch 1 + iter 2 + res 1 + sme 12). |
| Runtime: Bedrock-isolated agent auth (single env-var toggle) | `adb59f3` | `CARTOGRAPH_AGENT_SETTINGS_PATH` → spawned `claude -p` gets `--settings <path>` AND `shared/config.py` auto-loads file's env block at import. Local Claude Code dev session unaffected (subscription); agents bill Bedrock. See §2 for ops detail. Drops the docker-compose detour. |
| 10.11 HLD + config stale-lane cleanup | `0521153` | HLD ASCII diagram + config.py comment fixed to 12-lane total |
| 10.12 iterator prompt gaps (MCP port + APM fallback) | `45a9f4d` | Two blocks added to iterator prompt: AUXILIARY MCP PORT CONFLICTS (port 8101 collision playbook) + SURFACE FALLBACK LADDER (when APM surfaces missing, walk infra/cloud/logs/code) |
| 10.12 doc sync | `b79bafc` | §19 extended + IMPLEMENTATION-PHASES §10.12 |

### What's pending (next session — pick up here):

1. **Phase 10.13 — DONE.** All 10 sub-phases shipped (commits listed above). 117 tools live. Schema migrated. SME / resolver prompts updated.

2. **4th real-data attempt** — system now incorporates all 10 insight-bundle changes. Optional targeted DEMO13 verification before; not strictly required (changes are mostly prompt + a few isolated tool/schema bits, all test-covered, tools registered cleanly).

3. **Bedrock cost monitoring** — spend no longer routes through Anthropic API billing; watch AWS side.

4. **mcp_servers.yaml** — `last9-reader` entry promoted to checked-in (no longer a local override).

See `docs/IMPLEMENTATION-PHASES.md §10.8 / §10.9 / §10.10 / §10.11 / §10.12` for full rationale per sub-phase.

### DEMO11 final scorecard location:

```sql
SELECT text FROM communications WHERE from_agent='orch-8b7025e0'
  AND text LIKE '%FINAL SCORECARD%' ORDER BY created_at DESC LIMIT 1;
```

Run cost: **$86.29 total** ($62.99 SME, $15.68 orch, $6.19 res, $1.43 iter). 2,353 LLM turns, 962 tool calls, 0.41 tools/turn average, 48× mcp_call_batch invocations (~240-336 serial round-trips saved).

---

## 1. Branch + commit state (HEAD as of 2026-05-11, post-Phase-10.13 SHIPPED)

- **Working dir:** `/Users/venkata.manohar/release-agent/docs/service-dependency/cartograph`
- **Active branch:** `feat/trigger-manager-cartograh-mcp`
- **HEAD:** `541ecc3` (docs: recall date + HEAD bump for post-compact resume). Phase 10.13 SHIPPED across 9 sub-commits ending at `5cdc3e7`. After the next doc-sync commit lands (Phase 10.13 propagation to HLD/SCHEMA/TRIGGER-MGMT/AGENT-PROMPTS), HEAD advances to the new sync commit.
- **Runtime:** AWS Bedrock (`CARTOGRAPH_AGENT_SETTINGS_PATH` toggle — see §2). Token rotated 2026-05-11; orch liveness verified post-rotation.
- **Live DB state (pre-wipe):** in-progress 3rd real-data run — 28 idle agents + 8 decom, 32 components, 26 resources, 291 flows, 56 OPEN insights. **About to be wiped for the 4th real-data attempt** (snapshot taken; workspaces backed up to `src/workspaces.bak.pre-realdata4.<ts>/`).
- **Uncommitted:** none. `src/mcp_servers.yaml`'s `last9-reader` entry was promoted to checked-in earlier.

Recent commits, newest first:
```
5cdc3e7 Phase 10.13.11: final doc sync — Phase 10.13 SHIPPED
928de48 Phase 10.13.7: resolve_references_bulk + bind_edges_bulk
8b66d0c Phase 10.13.8: absorb_agent cascade-collision auto-dedup
2303322 Phase 10.13.10: prompt-tightening bundle (7 nudges)
0a4d3e6 Phase 10.13.4 + 10.13.5: identifier normalisation + thin-evidence skepticism
e81bcca Phase 10.13.6: attribution UNIQUE → component-scoped (schema migration)
fae957b Phase 10.13.3: get_component_owner MCP tool
3e156bf Phase 10.13.1: SME prompt — split/merge discipline doctrine
d8cb4d9 Phase 10.13.9: Kafka consumers declare consumed topics as queue catalogs
6af36e0 Phase 10.13.2: QR clarification asker terminal path — prompt fix
1ee1041 docs: sync recall §0 + IMPL-PHASES status with adb59f3 runtime work
adb59f3 runtime: Bedrock-isolated agent auth via CARTOGRAPH_AGENT_SETTINGS_PATH
b79bafc docs: sync for Phase 10.12 (iterator MCP port + APM fallback)
45a9f4d Phase 10.12: iterator prompt — MCP port conflicts + APM surface fallback
45ccf4b gitignore: snapshots/ + *.sql — local-only DB backups
0521153 docs: fix stale lane-count refs — HLD ASCII diagram + config.py comment
62ab801 docs: sync for Phase 10.9 + 10.10 + 10.11 (post-DEMO12 pre-real-data)
adb59f3 runtime: Bedrock-isolated agent auth via CARTOGRAPH_AGENT_SETTINGS_PATH
ec0fb26 docs: Phase 10.13 plan — post-real-data insight triage bundle
c04f7c9 config: Bedrock-compatible model ids + SME lanes 4→8
da49187 prompts: soften datastore-mandate wording — "might not be" not "do NOT"
13531d7 prompts: rewrite SLEEP semantics + telemetry datastore mandate
ca7d964 Phase 10.9: admin UI catalog drill-down rebuild — collapsibles + tables
2f54a1e Phase 10.8.7: final doc sync — recall §0 + §18 + IMPL-PHASES status
9c3e88c Phase 10.8.6: targeted DEMO12 prompt — Phase 10.8 verification
4a9612b Phase 10.8.4: DEMO11 spec sync — delete_attributions_bulk lenient
8a60df0 Phase 10.8.3: prompt promotions for DEMO11 tactic_win insights
5d335b0 Phase 10.8.2: defensive require_active_agent on broadcast read path
cd3bc20 Phase 10.8.1: canonical_name partial UNIQUE on active
5e616c9 docs: Phase 10.8 plan — post-DEMO11 insight bundle
1f47693 docs: recall §17 — proxy matrix fully verified (3 backfill tests)
467129c docs: post-DEMO11 recall refresh
872b407 SME prompt: component_doc_md as service-document; DEMO11 verifies doc_md quality
d43aafc docs: pre-DEMO11 sweep — recollection refresh + DEMO11 mega expansion
619acdc DEMO11: comprehensive end-to-end verification covering Phase 0-10.7
e7ce669 Phase 10.7.7: doc sync — fill in commit hashes for 10.7.0-6
ba7acc3 Phase 10.7.6: admin UI carries + renders description
1d4750e Phase 10.7.5: SME prompt — description vs doc_md split + workspace-local doc rule + flow-during-materialisation framing
f8380ec Phase 10.7.4: search_* family adds exclude_self kwarg (default True)
faefad4 Phase 10.7.3: vector_search filters + exclude_self + description in projection
5c660d1 Phase 10.7.2: backfill seeds description + get_component strips embedding
fb5f919 Phase 10.7.1: schema + embed text + upsert_component description
ec5a318 Phase 10.7 plan: description column + filtered vector_search + workspace-local doc_md
ad3830c DEMO10: targeted Phase 10.1.3 verification prompt
376b27a Phase 10.1.3: per-tool caller-id kwarg name-map in mcp_call_batch + create_edge docstring fix
3b55f42 docs: rewrite POST-COMPACTION-RECOLLECTION for post-DEMO-MEGA state
e1574bb DEMO-MEGA: 31-phase end-to-end verification covering Phase 0-10
773f4da Phase 10.1.2: split-spawned children get dedicated workspaces
dcb7f3f prompts: rewrite caveman block to mirror upstream caveman skill
351c42c Phase 10.1.1: atomic symmetric-nomination guard via partial UNIQUE index
7052288 Phase 10.4: TEMP lock-step phase progression doctrine
718f436 Phase 10.3: 6 deterministic search tools (108 → 114)
2d225db Phase 10.2: component_doc_md added to component embedding
2a62836 Phase 10.1: symmetric-nomination race guard
236e80a Phase 9.2: caveman output style + per-wake reminder
801c577 Phase 9.1: mcp_call_batch — server-side parallel dispatcher (108th tool)
```

Phase 6 (Globe): parked on `feat/globe-experimental` branch HEAD `1dae0c7`. **Don't recreate; merge that branch when ready.** `docs/GLOBE-MERGE-BRIEF.md` lives on that branch with all decisions.

---

## 2. Live system state (post-DEMO-MEGA)

### Daemons (4 processes, all running)
| Daemon | Port | Log | PID at session-end |
|---|---|---|---|
| Postgres (docker `cartograph-postgres-1`) | 5432 | `docker logs` | — |
| Ollama (mxbai-embed-large 1024d) | 11434 | system | — |
| MCP server | 8100 | `/tmp/cartograph-logs/mcp.log` | (varies) |
| Trigger manager | — | `/tmp/cartograph-logs/triggers.log` | (varies) |
| Agent manager (`python -m main`) | — | `/tmp/cartograph-logs/agents.log` | (varies) |
| Admin UI (FastAPI) | 8200 | `/tmp/cartograph-logs/admin_ui.log` | (varies) |

### Restart sequence (from `cartograph/`):
```bash
# Kill old
ps aux | grep -E "(admin_ui|cartograph_mcp|trigger_management|python.*-u.*main\.py|python.*-u -m main)" \
  | grep -v grep | awk '{print $2}' | xargs -r kill 2>/dev/null
sleep 3
mkdir -p /tmp/cartograph-logs
# Restart from src/
cd src
/opt/homebrew/bin/python3.10 -u -m cartograph_mcp.server   > /tmp/cartograph-logs/mcp.log      2>&1 &
sleep 5
/opt/homebrew/bin/python3.10 -u -m trigger_management.main > /tmp/cartograph-logs/triggers.log 2>&1 &
/opt/homebrew/bin/python3.10 -u main.py                    > /tmp/cartograph-logs/agents.log  2>&1 &
/opt/homebrew/bin/python3.10 -u -m admin_ui.server         > /tmp/cartograph-logs/admin_ui.log 2>&1 &
sleep 5
grep "tools registered" /tmp/cartograph-logs/mcp.log | tail -1   # expect: 114 tools
```

If Docker daemon is down: `open -a Docker`, wait ~20s, then `docker start cartograph-postgres-1` before MCP.

### Auth mode — subscription vs Bedrock (single env-var toggle)

The agent runtime supports two auth modes selected by the `CARTOGRAPH_AGENT_SETTINGS_PATH` env var:

| Mode | When | What happens |
|---|---|---|
| **Subscription** (default) | env var unset | Spawned `claude -p` subprocesses use `~/.claude/settings.json` (your Claude Code subscription auth). All billing flows through Anthropic API. |
| **Bedrock-isolated** (recommended for cartograph runs) | env var → path of a Bedrock settings file | Subprocesses use `--settings <path>` and read auth from there. Local Claude Code dev session keeps using subscription, completely separate. |

**Bedrock mode — single source of truth start command** (export once before starting agent_manager):

```bash
export CARTOGRAPH_AGENT_SETTINGS_PATH=~/.claude/settings.cartograph.json
cd src && python -u main.py
```

The settings file (`~/.claude/settings.cartograph.json`) holds:
- `CLAUDE_CODE_USE_BEDROCK=1`
- `AWS_REGION` + `AWS_BEARER_TOKEN_BEDROCK` (single bearer-token Bedrock auth)
- `ANTHROPIC_DEFAULT_{OPUS,SONNET,HAIKU}_MODEL` (Bedrock 1m inference profile IDs)

`shared/config.py` auto-loads the file's `env` block at import (Phase 10.13.dock-prep), so:
- agent_manager Python process picks up model IDs (passed as `--model` to subprocess)
- spawned `claude -p` picks up bearer token + region (via `--settings`)
- real shell env vars take precedence (file is fallback only)

**To revert to subscription mode for everything:** `unset CARTOGRAPH_AGENT_SETTINGS_PATH` and restart.

Bedrock settings file template lives at `~/.claude/settings.json.bak.bedrock` — copy + flip `CLAUDE_CODE_USE_BEDROCK` to `"1"` + paste fresh bearer token. Tokens are Bedrock-API-Key format with embedded presigned URL; rotate when expired.

### DB state at scorecard time
- 8 agents (1 decom = sme-933bbef7 absorbed)
- 4 active components (auth-svc, monolith-x trimmed, payments-svc carved, cron-rebalance), 1 decom
- 2 consolidations both D (1 merge, 1 split)
- 13 insights
- 8 proxy_audit rows
- 891 mcp_audit rows
- 105 communications
- 6 phase-broadcasts
- 113 mcp_call_batch invocations · 7 search_* invocations

### Pre-wipe snapshot of dev DB
`/tmp/cartograph-snapshots/snap-2026-05-04-154736-pre-demoMEGA.sql` (969K) — captured BEFORE wiping for DEMO-MEGA. Real-data workspace backup preserved at `src/workspaces.bak.20260426-2122/` (DO NOT TOUCH per user instruction). Plus `src/workspaces.bak.pre-demoMEGA.<ts>/` — workspaces from DEMO9 era.

---

## 3. Phase status (all 0 → 10.7 ✅; Phase 6 PARKED)

| Phase | Status | Key contribution |
|---|---|---|
| 0 → 7.4.6 | ✅ | Foundation, iteration, materialisation, consolidation, mutation, proxy, catalogs first-class, flows reference catalogs, DEMO7-round-1 fixes. |
| 6 (Globe) | PARKED | On `feat/globe-experimental`. Don't recreate; merge from that branch. |
| 7.4.7 → 7.4.10 | ✅ | Per-type model, plane semantics from RCA→resources, agent-row plane symbols, WebGL leak fix v2, Communications tab decom-aware. |
| 7.4.11 | ✅ | `delete_edge` MCP tool + SME identifier-normalisation rule + post-merge EDGE DEDUP step. |
| 7.4.12 | ✅ | 3 bulk MCP write tools + parallel-tool-calls block + BULK CALLS DECISION LADDER. |
| 7.4.13 | ✅ | Pre-injected action items in invocation user message. |
| 7.4.14 | ✅ | Wake debouncing — initial 5-min, tuned to 1-min via `bc72bd0`. |
| **8** | **✅ SHIPPED 2026-04-29** | Token Optimisation + Gap Closings. 18 new tools (89 → 107). 4 corrective delete singletons + 5 delete bulks + 4 write bulks + 5 multi-component read bulks + insert_unresolved idempotency + notify.py flock fix. |
| **9** | **✅ SHIPPED 2026-04-30** | Round 5 token-opt — mcp_call_batch (108th tool) + caveman output style. |
| **10** | **✅ SHIPPED 2026-05-04** | Search/Discovery + Embedding fix + Race guard + TEMP lock-step + workspace fix + caveman rewrite. Tool count 108 → 114. Sub-commits: 10.1 / 10.1.1 / 10.1.2 / 10.2 / 10.3 / 10.4 / 10.5 / dcb7f3f. |
| **10.1.3** | **✅ SHIPPED 2026-05-04** | per-tool caller-id kwarg name-map in mcp_call_batch (closes 5 BUG-2 insights from DEMO-MEGA: send_chat / send_broadcast / create_task / act_on_proxy_item / create_clarification batchable) + create_edge docstring fix (no longer claims "Refuses self-loops"). Commit `376b27a`. Verified by DEMO10 (`ad3830c`) — 8/8 PASS. |
| **10.7** | **✅ SHIPPED 2026-05-05** | Lookup architecture clean-up. New `description` column (≤400 chars dense embed-target) separates from `component_doc_md` (multi-paragraph human-render only, no longer embedded). vector_search + 5/6 search_* tools gain `exclude_self: bool = True` (default ON) + `filters: dict | None = None` (default OFF). Workspace-local `./component_doc.md` rule for SMEs. `get_component` + `get_components_bulk` strip 1024-d embedding from return (~8KB savings/call). Sub-commits: ec5a318 / fb5f919 / 5c660d1 / faefad4 / f8380ec / 1d4750e / ba7acc3 / e7ce669. Tool count unchanged: 114. |

---

## 4. Token optimisation — ALL 4 ROUNDS SHIPPED

| # | Lever | Saving | Commit |
|---|---|---:|---|
| 0 | Parallel tool calls (BATCH block — original) | 20-30% target / NOT realised | `5b3144d` |
| 1 | Concise output rule (superseded by caveman) | 8-10% / NOT realised | `c9bb733` |
| 2 | Orch → sonnet-4-6 (resolver STAYS opus-4-6) | 10-14% | `53a6ef1` |
| 3 | Top-3 bulk MCP write tools | 6-9% | `716554d` |
| 4 | BULK CALLS DECISION LADDER prompt | 3-5% | `135bc57` + `5e969a1` |
| 5 | Pre-inject action items in user message | 3-5% | `08c58d1` |
| 6 | Wake debouncing (5-min → 1-min) | 10-15% | `8d1a7d3` + `bc72bd0` |
| **7 (R5 §9.1)** | **mcp_call_batch — heterogeneous batch dispatcher** | **12-18%** | `801c577` |
| **8 (R5 §9.2)** | **Caveman output style + per-wake reminder** | **12-18%** | `236e80a` + `dcb7f3f` |

**DEMO-MEGA effectiveness numbers:**
- 17.9 LLM calls per invocation (DEMO8 was ~30-40)
- 113 mcp_call_batch invocations bundling ~565 sub-calls (saved ~452 LLM round-trips)
- mcp_call_batch + caveman both biting in production

**Levers explicitly DROPPED (do NOT reintroduce):**
- Session reset at phase boundaries
- Lane-cap quiet mode
- Haiku for trivial wakes
- Effort='low' on opus
- Zero-state-change wake skip
- Resolver→sonnet (too high-stakes)
- Generic `bulk_execute` dispatcher (mcp_call_batch is the right shape)
- §7.3 WD-owner-wake filter (existing 1-min debounce already handles it)
- §7.5 pre-compute split transfer ids at nomination time (drift risk on mutation)

**Parked / not urgent:**
- §7.4 auto-handoff at resolver M-transition (good-to-have, not a token win)

---

## 5. Tool surface (114 total)

```
action_items (2):     get_action_items_summary, get_action_items_detail
chat (4):             send_chat, ack_chats, get_unacked_chats, get_chat_history
broadcast (5):        send_broadcast, ack_broadcast, get_unacked_broadcasts,
                      update_broadcast_persistence, ack_broadcasts_bulk (8.4)
secrets (4):          put_secret, get_secret, list_secrets_for_plane, delete_secret
tasks (5):            create_task, respond_task, raise_blocker, get_my_tasks, get_task_thread
resources (9):        upsert_resource, upsert_resources_bulk, get_resource,
                      list_resources_for_plane, list_all_resources, get_resource_counts,
                      mark_resource_done, reject_resource, reject_resources_bulk
agent_lifecycle (11): create_agent, bulk_spawn_smes, list_agents, reset_agent,
                      decommission_agent(_bulk), decommission_component(_bulk),
                      sleep_self, bulk_sleep_agents, bulk_wake_agents
components (32):      upsert_component (10.7: accepts `description` ≤400 chars
                                          dense embed-target field;
                                          component_doc_md is human-render only,
                                          NOT embedded post-10.7),
                      upsert_attribution,
                      upsert_attributions_bulk (7.4.12),
                      create_edge (3.9 shim; 10.1.3 docstring fixed —
                                   self-loops correctly documented as permitted),
                      insert_unresolved (idempotent ON CONFLICT post-8.4),
                      resolve_reference,
                      upsert_edge_catalog (7.4 shim), upsert_edge_outbound,
                      upsert_edges_outbound_bulk (7.4.12), bind_edge,
                      upsert_flow, upsert_flows_bulk (8.4),
                      insert_unresolved_bulk (8.4),
                      delete_edge (7.4.11), delete_edges_bulk (8.3),
                      delete_attribution + _bulk (8.2/8.3),
                      delete_flow + _bulk (8.2/8.3),
                      delete_unresolved + _bulk (8.2/8.3),
                      get_component (10.7: strips embedding vector ~8KB savings),
                      get_components_bulk (8.5; 10.7 same strip),
                      get_attributions, get_attributions_bulk (8.5),
                      get_edges, get_component_edges,
                      get_component_edges_bulk (8.5),
                      get_unresolved, get_flow, get_flow_inverse,
                      get_flows_bulk (8.5)
notifications (1):    get_agent_notifications
consolidation (5):    nominate, respond, review, get_my, get_thread
clarification (4):    create, respond, get_my, get_thread
search (1):           vector_search (lean projection per 7.4.4; 10.7:
                      adds `description` to components result; new optional
                      kwargs `filters: dict | None = None` (per-table
                      whitelist) + `exclude_self: bool = True` DEFAULT ON;
                      plane filter on components NOT supported — use
                      attributions filter for plane-scoped lookups)
mutation (10):        execute_mutation, complete_consolidation, absorb_agent,
                      spawn_child_agent, transfer_attributions, transfer_edges,
                      transfer_flows, get_my_components, get_stale_edges, get_stale_flows
proxy (2):            get_my_proxy_items, act_on_proxy_item
insights (1):         record_insight (5.9)
terminal_acks (2):    ack_terminal (7.1), ack_terminals_bulk (8.4)
catalogs (8):         upsert_catalog, upsert_catalogs_bulk (7.4.12),
                      get_my_catalogs, get_catalogs_bulk (8.5),
                      get_my_catalog_callers,
                      get_unmatched_callers, get_orphan_catalogs,
                      delete_catalog + _bulk (8.2/8.3)
batch (1):            mcp_call_batch (9.1) — server-side parallel dispatcher;
                      cap 50; no nesting; 10.1.3 per-tool name-map for
                      caller-id auto-inject (send_chat→from_agent_id,
                      send_broadcast→from_agent_id, create_task→
                      owner_agent_id, act_on_proxy_item→survivor_id,
                      create_clarification→asker_agent_id, default
                      agent_id for everything else)
search_det (6):       search_components, search_attributions, search_edges,
                      search_catalogs, search_flows, search_unresolved (10.3) —
                      SQL-LIKE deterministic search; AND across cols / OR
                      within col via list; cap 100 / refuse blank;
                      10.7: 5/6 (skip search_flows) gain
                      `exclude_self: bool = True` DEFAULT ON

Plus auto-wrapped: every @mcp.tool() registration wrapped by
cartograph_mcp.audit.audited (Phase 5.10) → mcp_audit table.
```

**Total: 114.** Verify with `grep "tools registered" /tmp/cartograph-logs/mcp.log | tail -1`.

---

## 6. DEMO-MEGA results detail

**Scorecard (full):** in DB at `communications` table from orch `orch-5d2a0fa8` to admin, look for the message containing `FINAL SCORECARD`. Run was 2026-05-04 from ~16:18 (kickoff) to ~16:46 (scorecard).

### 29 PASS / 1 N/A / 1 VERIFY_PENDING / 0 FAIL across 31 phases.

- **N/A:** Phase 21b mid-mutation override — no consolidation in M state during the test window.
- **VERIFY_PENDING:** Phase 25 multi-message proxy (second admin chat to decommissioned sme-933bbef7, comm `6c414894` — verification task `17eb3e75` in-flight at scorecard time). Phase 10 already proved the mechanism. Expected PASS.

### 2 BUGS FOUND — drive Phase 10.1.3:

**[DEMOMEGA-BUG-1]** `create_edge` docstring says "Refuses self-loops" — wrong. Phase 7.3 + 7.4.4 dropped that constraint. Doc-only bug. Insight `192d84c4`. **5 LOC fix.**

**[DEMOMEGA-BUG-2]** `mcp_call_batch` auto-injects `agent_id`, but 5 tools use non-standard caller-id params (`from_agent_id`, `owner_agent_id`, `survivor_id`, plus 1 more) → TypeError when called inside batch. Workaround: call directly. Real fix in 10.1.3 — rename params to `agent_id`. Insights: `7ce4622c`, `7c0bae81`, `4a220f29`, `f41f310e`, `5e58b07d`.

### Architectural fixes verified live in DEMO-MEGA:
- **Phase 9.1** mcp_call_batch — 113 invocations during the run.
- **Phase 9.2** Caveman style — verified on Phase 4 (4 channels) + Phase 30.
- **Phase 10.1** Symmetric-nomination SELECT pre-check — Phase 7a.
- **Phase 10.1.1** Atomic partial UNIQUE index — Phase 7b.
- **Phase 10.1.2** Split-spawned child workspace isolation — Phase 7i, `0 rows shared`.
- **Phase 10.2** `component_doc_md` in embed — Phase 9d, vector_search returned auth-svc rank#1 sim=0.6709 from doc_md content.
- **Phase 10.3** 6 search tools — 7 invocations during run, used in EDGE_DISCOVERY hygiene.
- **Phase 10.4 TEMP** Lock-step doctrine — 6 phase-broadcasts emitted across 3 transitions.

### 13 insights filed during run:
- 4 tactic_win, 4 tool_gap, 3 workflow_friction, 1 prompt_gap, 1 doc_confusing
- 5 of these are the BUG-2 cluster
- 1 is BUG-1
- The other 7 are real signal worth admin triage post-fix

---

## 7. Schema state

20 tables (added `terminal_acks` in 7.1, `catalogs` in 7.4, `agent_insights` in 5.9, `mcp_audit` in 5.10):
- `components`, `attributions`, `edges`, `flows`, `unresolved`
- `agent_runs` (+ `errored_at`, `recovery_attempts`, `sleep_until`, `first_pending_at` from 7.4.14, deactivation cols from 4.0)
- `resources`, `resource_component_agents`, `tasks`, `secrets`
- `consolidations`, `clarifications`, `communications`, `broadcast_acks`
- `proxy_audit`, `proxy_items` (legacy, unused since Phase 4)
- `agent_insights`, `mcp_audit`
- `terminal_acks`, `catalogs`

**Recent schema deltas:**
- `agent_runs.first_pending_at TIMESTAMPTZ` (Phase 7.4.14, idempotent ALTER ADD COLUMN IF NOT EXISTS).
- `consolidations_pair_unique` partial UNIQUE index on `(LEAST(component_a_id, component_b_id), GREATEST(...)) WHERE nomination_type='merge' AND status NOT IN ('D','F')` (Phase 10.1.1).

---

## 8. Embedding flow — 3-actor model

| Stage | Actor | Where |
|---|---|---|
| Generate embedding for write | **Ollama** (external HTTP) | `localhost:11434/api/embed`, model `mxbai-embed-large`, 1024-dim. ~40-60ms warm. Done inline inside MCP tools. |
| Generate embedding for query | **Ollama** | Same call inside `vector_search`. |
| Cosine similarity | **pgvector (native, in DB)** | `<=>` operator + HNSW index. |

NOT pgvector-native generation. NOT a background loop in normal operation. `embedding_backfill.py` exists for one-off model migrations and was extended in 10.2 with `--force-components` flag for re-embedding after embed-text shape changes.

Read path uses lean projection (Phase 7.4.4) — `vector_search` returns id + identity columns + similarity, NOT the embedding vector or doc/slice/metadata blobs.

**Component embed text shape (Phase 10.7 — current):**
```python
f"{component_type}: {canonical_name} {display_name} {description} {meta_json}"
```
Phase 10.7 introduced a separate `description` column (≤400 chars soft cap) as the dense embed-target. `component_doc_md` is human-render only post-10.7 — no longer in the embed text. `source_slice` deliberately not included.

**Pre-Phase-10.7 shape (now reverted)** was `f"{type}: {name} {display} {doc_md[:500]} {meta}"` — Phase 10.2 added doc_md to fix a recall gap; Phase 10.7 separated embed signal (description, machine) from human render (doc_md) to prevent prose-length dilution of the identity ranking signal.

Mandatory post-10.7 backfill: `python -m shared.embedding_backfill --force-components` re-embeds all rows + seeds `description = LEFT(component_doc_md, 400)` on any row where description is empty. Already run on dev DB.

---

## 9. Critical invariants (DON'T violate)

1. **1 SME = 1 active component.** Splits go through Consolidation, not direct creation.
2. **Edges (Phase 3.9):** asymmetric. Bound = both set (caller owns). Dangling = to NULL (caller owns). Self-loops (from = to) accepted as of Phase 7.3 + 7.4.4. Catalogs (Phase 7.4) live in their own table now, not edges.
3. **Catalogs (Phase 7.4):** OWN TABLE, not edges. Owner-only declarations. Noun-form `kind`.
4. **Flows reference catalog rows via `incoming_catalog_id` (Phase 7.4.2).** No catalog → no flow.
5. **Junctions are routing scaffolding, NEVER real components.**
6. **Terminal ack:** every participant of a closed entity must explicitly `ack_terminal` — else trigger scanner re-wakes them.
7. **Pre-merge handoff:** mutation POC MUST raise clarification to target before `absorb_agent`.
8. **No session reset for prompt changes** — `--system-prompt` rebuilt fresh on every spawn.
9. **Mutation cascades (Phase 7.4.2):** `absorb_agent` runs catalog cascade BEFORE flow cascade.
10. **Self-loops permitted (Phase 7.3 + 7.4.4):** DB CHECK + Python guards both gone. Phase 10.1.3 also corrected the `create_edge` docstring that previously claimed otherwise.
11. **`vector_search` returns lean rows (Phase 7.4.4):** no embedding vectors, no doc/slice/metadata blobs. Phase 10.7: components projection adds `description` (cheap — ≤400 chars). Filters dict + `exclude_self=True` default ON.
11a. **`get_component` strips embedding (Phase 10.7):** returned dict never contains the 1024-d vector column. Same for `get_components_bulk`. Saves ~8KB/call.
11b. **`description` is THE embed-target (Phase 10.7):** `f"{type}: {name} {display} {description} {meta}"`. doc_md changes do NOT trigger re-embed; description changes do. Soft 400-char cap (warn-not-reject).
11c. **Workspace-local doc_md rule (Phase 10.7):** SMEs maintain `./component_doc.md` in workspace as canonical source-of-truth. Pass file content on every upsert_component. Never reconstruct from chat memory.
11d. **`exclude_self` default TRUE on vector_search + 5/6 search_*:** caller's own component(s) excluded from results unless caller passes `exclude_self=False`. Non-SME callers (orch/iter/resolver) silent-no-op (NOT IN empty set is TRUE).
12. **`get_action_items_summary` is uniform `dict[str, int]` (Phase 7.4.5):** rich proxy breakdown lives on `get_action_items_detail`.
13. **Component planes come from RCA→resources, not attributions.**
14. **Per-type model:** orch=sonnet-4-6, resolver=opus-4-6+medium effort, sme/iter=sonnet-4-6.
15. **Sleep is LAST RESORT:** 300-600s MAX, never 24h, never >3600s.
16. **Identifier normalisation across planes:** holistic-edge invariant requires byte-identical identifier across SMEs from different planes.
17. **Parallel tool calls:** Claude Code subprocess provably disables emission of >1 tool_use per assistant turn (DEMO8 verified 0/851, DEMO-MEGA confirmed 0/N). **mcp_call_batch is the workaround.**
18. **Caveman output rule:** ACTIVE EVERY response. Pattern `[thing] [action] [reason]. [next step].` Drop articles/filler/preambles. Keep identifiers/paths/IDs/error strings VERBATIM. Resume normal English ONLY for destructive op confirms + admin clarify requests. Exempt: `component_doc_md` (graph-viz hover for humans).
19. **BULK CALLS DECISION LADDER (3 rungs):** Direct → Bulk variant → mcp_call_batch. DON'T do native parallel tool_use (subprocess serialises them).
20. **Pre-injected action items:** snapshot in invocation USER message NOT system_prompt (cache-safety).
21. **Wake debouncing 1-min:** `agent_runs.first_pending_at` stamped on first sighting; refuses lock until 60s elapsed unless override (admin chat OR mutation POC on state=M consolidation).
22. **Smoke-test rule:** every prompt edit MUST run `SYSTEM_PROMPT_TEMPLATE.format(plane='x', resource_id='y')` before commit. Caught 3 brace bugs already.
23. **Lock-step phase doctrine (TEMP, Phase 10.4):** Orch broadcasts `[PHASE-END: <prev>] [PHASE-START: <next>]` for 5 phases. SMEs respect by convention. Marked TEMPORARY — drop once self-pacing proves reliable at higher scale.
24. **Workspace isolation (Phase 10.1.2):** every agent gets unique `workspace_path`. `AgentManager.provision_workspace` is the helper; `create_agent` and `spawn_child_agent` both go through it.
25. **Symmetric-nomination guard (Phase 10.1 + 10.1.1):** SELECT pre-check + partial UNIQUE index on normalised pair. Both belt-and-suspenders.
26. **mcp_call_batch caller-id name-map (Phase 10.1.3):** dispatcher consults a per-tool dict to choose which kwarg to inject the caller's id under. Default `agent_id`; overrides for `send_chat`/`send_broadcast` (`from_agent_id`), `create_task` (`owner_agent_id`), `act_on_proxy_item` (`survivor_id`), `create_clarification` (`asker_agent_id`). Tools keep their semantic param names; dispatcher does the mapping.

---

## 10. Service flow + phase mental model

**5 sequential phases per the lock-step doctrine:**

1. **USER_DISCUSSION** — admin↔orch onboarding, creds, scope.
2. **ITERATION** — iterators enumerate resources via `upsert_resource(s_bulk)`.
3. **MATERIALISATION** — SMEs hydrate own components:
   - upsert_component with `description` (≤400 chars dense, embed-target) + `component_doc_md` (multi-paragraph human-render, NOT embedded post-10.7) + source_slice
   - SMEs maintain `./component_doc.md` in workspace as the doc_md source-of-truth (Phase 10.7 workspace-local rule)
   - exhaustive attributions
   - own catalogs (what I expose)
   - outbound edges DANGLING ONLY (`to_component_id=NULL`) + paired insert_unresolved
   - flows tying own catalogs ↔ own danglings
   - **DO NOT bind to peers** — defer to EDGE_DISCOVERY (lock-step rule)
4. **CONSOLIDATION_MUTATION** — merges/splits negotiate + execute:
   - SME nominates merge → B2 (or split → R directly)
   - Pre-merge handoff clarification mandatory
   - Resolver auto-escalates to R when both confs ≥ 0.85
   - Resolver reviews → M (sets mutation_assigned_to)
   - SME executes mutation (absorb_agent / spawn_child_agent)
   - Cascade: catalogs first, then flows, then attributions/edges
   - execute_mutation → MD; resolver complete_consolidation → D
   - All participants ack_terminal
5. **EDGE_DISCOVERY** — graph stable, now safe to cross-reference:
   - resolve_reference on unresolved rows
   - bind_edge danglings via cosine ladder
   - Cross-SME hygiene: get_unmatched_callers, get_orphan_catalogs, get_stale_edges, get_stale_flows
   - Identifier normalisation + delete_edge dedup if needed

---

## 11. Files I'd touch for Phase 10.1.3

### Code:
- `src/cartograph_mcp/tools/chat.py` — `send_chat(from_agent_id → agent_id)`
- `src/cartograph_mcp/tools/broadcast.py` — `send_broadcast(from_agent_id → agent_id)`
- `src/cartograph_mcp/tools/tasks.py` — `create_task(owner_agent_id → agent_id)` + maybe `raise_blocker`
- `src/cartograph_mcp/tools/proxy.py` — `act_on_proxy_item(survivor_id → agent_id)`
- `src/cartograph_mcp/tools/components.py::create_edge` — docstring fix only
- `src/cartograph_mcp/server.py` — wrapper signatures matching the renames

### Tests:
- `tests/mcp_tools/test_chat.py`
- `tests/mcp_tools/test_broadcast.py`
- `tests/mcp_tools/test_tasks.py`
- `tests/mcp_tools/test_proxy.py`
- `tests/mcp_tools/test_call_batch.py` — add explicit batch with all 5 renamed tools
- `tests/mcp_tools/test_components.py` — drop the test asserting create_edge refuses self-loop if any (already inverted in 7.4.4 cleanup, double-check)

### Prompts:
- `src/agent_management/agent_types/sme.py` — grep for `from_agent_id` / `owner_agent_id` / `survivor_id` in worked examples
- `src/agent_management/agent_types/orchestrator.py` — same grep
- `src/agent_management/agent_types/iterator.py` — same
- `src/agent_management/agent_types/resolver.py` — same
- `src/agent_management/agent_types/base.py` — same

### Docs:
- `docs/AGENT-PROMPTS.md` — sweep param name references
- `docs/TRIGGER-MANAGEMENT.md` §3.3 (Act tools) — update tool signatures
- `docs/HLD.md` §2.5 (Tool × Agent Matrix) — update column refs
- `docs/IMPLEMENTATION-PHASES.md` — add Phase 10.1.3 section
- `docs/POST-COMPACTION-RECOLLECTION.md` — update HEAD commit

---

## 12. DEMO10 prompt outline (write to `docs/oorch-test-prompt-demo10`)

~7 phases, ~10-15 min:

```
[DEMO10-START]
You are running a TARGETED Phase 10.1.3 verification. Narrow scope —
just exercise the renamed-param surface + mcp_call_batch coverage of
all 5 affected tools.

== TAGS ==
[DEMO10-PHASE-N] / [DEMO10-OK] / [DEMO10-FAIL] / [DEMO10-BUG] /
[DEMO10-NOTE] / [DEMO10-RESULT]

== PHASE 1 — TOOL SURFACE ==
114 tools registered. Verify mcp_call_batch + 5 renamed tools all
present. [DEMO10-OK].

== PHASE 2 — MINIMAL TOPOLOGY ==
Spawn 2 SMEs (sme-A, sme-B) on plane='github'. Quick materialise:
upsert_component + 2 attributions + 1 catalog each. No flows
required.

== PHASE 3 — DIRECT CALLS WITH NEW PARAM NAMES ==
3a. send_chat(agent_id=sme-A, to_agent_id='admin', message='ping')
    → succeeds. [DEMO10-OK]
3b. send_broadcast(agent_id=orch, to_agent_type='sme', ...) → succeeds
3c. create_task(agent_id=orch, worker_agent_id=sme-A, description=...)
    → succeeds (note: agent_id replaces owner_agent_id)
3d. raise_blocker(agent_id=sme-A, task_id, blocker_detail) → succeeds
3e. (act_on_proxy_item tested in Phase 4)

== PHASE 4 — INDUCE A DECOM + EXERCISE PROXY ==
Quick merge: sme-A absorbs sme-B (both have minimal state). Then
admin chat to decommissioned sme-B. sme-A:
  act_on_proxy_item(agent_id=sme-A, item_type='chat', item_id=...,
                    action='send', payload=...)
  → succeeds. (note: agent_id replaces survivor_id)
proxy_audit row exists.

== PHASE 5 — mcp_call_batch HETEROGENEOUS ALL-5 ==
sme-A calls:
  mcp_call_batch(agent_id=sme-A, calls=[
    {tool: 'send_chat', args: {to_agent_id: 'admin', message: 'b1'}},
    {tool: 'send_broadcast', args: {to_agent_type: 'sme', message: 'b2'}},
    {tool: 'create_task', args: {worker_agent_id: 'sme-A', description: 'b3'}},
    {tool: 'raise_blocker', args: {task_id: <some>, blocker_detail: 'b4'}},
    {tool: 'act_on_proxy_item', args: {item_type:'chat', item_id:<some>, action:'send', payload:{...}}},
  ])
Expect: results array of 5, all ok=True (or where state-machine-valid).
The fix: agent_id auto-injection now works for all 5.

== PHASE 6 — REGRESSION SPOT-CHECKS ==
6a. Caveman style still active. Sample any chat reply.
6b. mcp_call_batch nesting still rejected.
6c. mcp_call_batch 50-cap still enforced.
6d. create_edge self-loop succeeds (Bug-1 doc fix verification —
    grep tools/components.py for "Refuses self-loops" → 0 matches).

== PHASE 7 — SCORECARD ==
[DEMO10-RESULT phase=N] PASS/FAIL per phase.
Pass if all 5 renamed tools work both direct + in batch.

[DEMO10-END]
```

---

## 13. User collaboration style

- Crisp responses preferred. No fluff, no apology loops. Honest pushback welcomed.
- Incremental commits, push after each cohesive change.
- Daemon restart silently after code changes — never ask.
- TDD when implementing — tests first, run red, then make green.
- Convention prefixes (`[DEMO7-XX]`, `[DEMO8-XX]`, `[DEMO9-XX]`, `[DEMOMEGA-XX]`, `[DEMO10-XX]`) for cross-session reporting.
- When user says "go go go" or "do it" — they mean execute now, don't re-confirm.
- Don't ever recreate Globe (Phase 6) on main — merge from `feat/globe-experimental` branch when ready.
- **User pushes back on speculative/unjustified levers** — don't over-engineer.
- **User has limited tolerance for verbosity** — answer in <5 lines unless detail genuinely warrants. Status updates 1-2 sentences.
- **User likes "honest readout" style** — say what didn't work, not just what did.
- **User flagged twice that I tend to be sycophantic / counter-intuitively flippy** — don't flip-flop on opinions just because pushed back. State the issue, then defend or concede with reasoning.

---

## 14. Critical don'ts

1. Do NOT fix the broadcast via-badge display on Communications tab. Parked.
2. Do NOT decommission agents as a cleanup shortcut.
3. Do NOT run the demo41 metadata purge without explicit user say-so.
4. Do NOT send admin chats unsolicited; admin is the user.
5. Do NOT recommend session resets for agent prompt changes — `--system-prompt` hot-loads.
6. Do NOT use chat to communicate agent→agent (`send_chat` refuses non-admin senders to non-admin recipients). Orch uses tasks/broadcasts.
7. Do NOT recreate Globe on main — merge from `feat/globe-experimental` instead.
8. Do NOT duplicate comm rows for terminal-state announcements — use `terminal_acks` junction now.
9. Do NOT downgrade resolver from opus-4-6.
10. Do NOT let work pile uncommitted. Incremental cadence.
11. Do NOT modify `--system-prompt` per wake. Variable content goes in user message.
12. Do NOT skip the `SYSTEM_PROMPT_TEMPLATE.format()` smoke test before committing prompt edits.
13. Do NOT compromise on identifiers / file paths / hostnames / IDs / data when applying caveman. Trim English only.
14. Do NOT touch the real-data backup `src/workspaces.bak.20260426-2122/`.

---

## 15. Re-hydration checklist (do in order post-compaction)

1. **`git log --oneline -25`** — confirm Phase 10.13 sub-commits (`6af36e0` → `5cdc3e7`) are on `feat/trigger-manager-cartograh-mcp`.
2. **`grep "tools registered" /tmp/cartograph-logs/mcp.log | tail -1`** — should show `117 tools` (Phase 10.13 added `get_component_owner`, `resolve_references_bulk`, `bind_edges_bulk`). If not, MCP server isn't running; restart per §2.
3. **Read this doc fully.**
4. **Read the 7 canonical docs** (HLD / SCHEMA / TRIGGER-MANAGEMENT / AGENT-PROMPTS / IMPLEMENTATION-PHASES / ONE-PAGER / PROMPT-ENHANCEMENTS). User will tell you if they want a particular ordering.
5. **Check the 3 memory files** at `~/.claude/projects/-Users-venkata-manohar-release-agent-docs-service-dependency/memory/` (auto-loaded).
6. **Verify daemons running:** `ps aux | grep -E "(admin_ui|cartograph_mcp|trigger_management|python.*-u -m main)" | grep -v grep | grep -v agent-battles` — should be 4 cartograph processes.
7. **Look for `docs/oorch-test-prompt-demoMEGA`** to remember the latest test surface.
8. **Find the [DEMOMEGA-RESULT] scorecard:** `PGPASSWORD=cartograph psql -h localhost -U cartograph -d cartograph -c "SELECT text FROM communications WHERE text LIKE '%FINAL SCORECARD%' ORDER BY created_at DESC LIMIT 1;"`

If user asks "what's next" — answer is **Phase 10.1.3** + **DEMO10**. See §0 above.
If user asks "what's the current ship state" — §3 + §4.
If user asks about specific bugs — §6 (DEMO-MEGA bugs section).

---

## 16. Insight inventory from DEMO-MEGA (worth promoting)

13 insights filed during the run. Bugs in §6. The other 7 are real signal:

- **5 of the 13** are the Bug-2 cluster (param naming) — close out via 10.1.3.
- **1** is Bug-1 (docstring) — close out via 10.1.3.
- **Remaining 7** for admin triage post-fix:
  - 4 tactic_win — workflows that worked well (worth promoting into prompt rules)
  - 3 workflow_friction — coordination friction observed
  - 1 prompt_gap — something missing in the agent prompts

Pull via: `curl -s 'http://localhost:8200/api/insights?status=open' | jq .`

---

---

## 17. DEMO11 results + post-run findings + insight backlog (2026-05-05)

### Final scorecard: 16/16 PASS, 0 FAIL, 0 BUG.

Phase 8c.1 reported PARTIAL initially (admin must send the chat to a decom; orch can't self-fire because send_chat ACL refuses non-admin → non-admin). Admin sent the chat post-scorecard via direct DB insert (`comm 7bb60632`); survivor `sme-da948bbe` woke within 1-min debounce window (admin-chat override), acked via `act_on_proxy_item(chat, ack)` → first `(chat, ack)` proxy_audit row of the run landed at 17:40 IST. **Phase 8c.1 → PASS.**

### Cost matrix (DEMO11 actuals)

| Agent Type | Wakes | Turns | Tools | Tools/turn | Out tokens | Cache-read | ≈ Cost |
|---|---:|---:|---:|---:|---:|---:|---:|
| Orchestrator | 395 | 395 | 169 | 0.43 | 273K | 38.5M | $15.68 |
| Resolver | 183 | 183 | 84 | 0.46 | 89K | 16.2M | $6.19 |
| Iterator | 118 | 118 | 44 | 0.37 | 30K | 3.3M | $1.43 |
| SME (×11) | 1,657 | 1,657 | 665 | 0.40 | 1.36M | 141.8M | $62.99 |
| **TOTAL** | **2,353** | **2,353** | **962** | **0.41** | **1.76M** | **199.8M** | **$86.29** |

SMEs 73% of cost. Cache-read 69.5% of spend. **Orch fired mcp_call_batch 48×** (~240-336 serial round-trips saved).

### Negative-test verification of decom interactions (post-scorecard)

Black-box verified that active agents CANNOT do these to/with a decom (all REFUSED at write site):
- `create_task(worker=decom)` → "Worker agent X not found"
- `nominate_consolidation(comp_b=decom-owned-component)` → "component_b has no active owner"
- `create_clarification(responder=decom)` → "Agent X not found"
- `send_chat(active_sme → decom)` → "Agents can only chat with admin" (ACL universal, decom-or-not)
- `act_on_proxy_item(task, create)` → "Unsupported proxy action"
- `act_on_proxy_item(consolidation, nominate)` → "Unsupported proxy action"

### Positive-path verification of proxy dispatch matrix (all 6 verbs)

All 6 dispatch verbs in `act_on_proxy_item` now verified end-to-end (decom + survivor pair):
- `(consolidation, respond)` — 4 rows during DEMO11 Phase 5 ✅
- `(broadcast, ack)` — 20 rows during merge cascades ✅
- `(chat, ack)` — 1 row (Phase 8c.1 close-out post-scorecard) ✅
- `(task, respond)` — 1 row, task BW→WD via stub ✅ (post-DEMO11 backfill)
- `(clarification, respond)` — 1 row, clar B2→QC via stub ✅ (post-DEMO11 backfill)
- `(chat, send)` — 1 row, decom→admin chat via survivor ✅ (post-DEMO11 backfill)

Decom + proxy + other × 5 paradigms × {read,respond} × {new,existing} matrix fully covered — no holes.

**Defensive finding (not a runtime exploit):** `broadcast.get_unacked_broadcasts(decom_id)` returns rows — function does NOT call `require_active_agent`. Operationally safe (decom never wakes; agent_manager pickup filters status), but if ANY external caller (admin UI / debug script) hits this, it returns rows. 2-LOC fix: add `require_active_agent(agent_id)` at top + matching skip in `trigger_management/scanners/broadcasts.py::scan`. Belt-and-suspenders only. Filed as informal finding; not blocking.

### 3 standing semantic questions (real-world tolerable, not blockers)

1. **Post-decom broadcasts surface in proxy queue.** Survivor `get_my_proxy_items` walks decom's queue using `c.created_at > agent_runs.created_at` filter. For decoms, this includes broadcasts created AFTER the decom was decommissioned. No `deactivated_at` column to filter against. Wasted ack work, not a correctness bug. Could fix by adding `agent_runs.deactivated_at` + filter `c.created_at <= deactivated_at` on the proxy broadcast list.
2. **`mutation_assigned_to` decom mid-execution.** If the SME assigned to execute a mutation gets decom'd mid-flight, no `(consolidation, execute_mutation)` proxy path exists. Consolidation sits at M forever. Mitigated by resolver pre-M conflict check (defers if participant in another M). Edge case, not exercised this run.
3. **`broadcast.get_unacked_broadcasts` no decom gate.** See defensive finding above.

### 8 insights filed during DEMO11 + verdict

| # | Source | Kind | Body summary | Verdict |
|---|---|---|---|---|
| 1 | sme-da948bbe | tool_gap | After absorb_agent, target's canonical_name held by decom row → unique constraint on `components.canonical_name` blocks future reuse | **Decision needed.** Either accept (canonicals forever) or migrate to `WHERE status='active'` partial unique. Real architecture choice. |
| 2 | sme-3788c88f | tactic_win | spawn_child_agent atomically migrates AND resolves unresolved rows when target in scope | **Promote to SME prompt.** Future split-spawned children should know to expect this; saves manual re-resolve work. |
| 3 | orch-8b7025e0 | tactic_win | SMEs autonomously bound edges from BROADCAST alone, before explicit per-agent tasks | **Promote to orch prompt.** Means broadcast-driven coordination works without per-agent tasking; lower coordination overhead. |
| 4 | sme-901967e8 | tactic_win | Multi-plane same-canonical: temp name → merge nominate → drop temp | Already aligned with existing prompt; reinforce. |
| 5 | orch-8b7025e0 | doc_confusing | `delete_attributions_bulk` semantic delta: commits valid + per-row not_found vs DEMO11 spec "reject whole batch" | **Decision needed.** Current behaviour probably better (lenient). Update DEMO11 spec to match impl, OR change impl. |
| 6 | sme-f97d2059 | prompt_gap | "If target component genuinely missing, leave dangling + don't force-create" | Minor prompt reinforcement needed. |
| 7 | sme-f2800e8a | doc_confusing | Identifier normalisation hit: `orders.events` vs `order-events` | Identifier-norm rule already in prompt; this is a real-world hit. |
| 8 | orch-8b7025e0 | workflow_friction | Phase 8c.1 admin-chat ACL blocker (orch can't send chat to decom) | Synthetic test issue, not a real bug. Admin should fire that chat as part of demo orchestration. |

Pull live via: `curl -s 'http://localhost:8200/api/insights?status=open' | jq .` or `psql -tAc "SELECT id,agent_id,kind,target,body FROM agent_insights ORDER BY created_at"`

### Lessons learned (workflow patterns to keep / change)

- **Orch sat idle 60 min** between Phase 5 execution and reporting. No auto-wake fires when "all consolidations terminal." Needed admin nudge at 16:55. Consider an orch self-check loop or explicit broadcast on phase-5-done. Workflow_friction worth filing as a prompt enhancement (orch self-pacing).
- **Orch hit 900s subprocess timeout** at 17:18 mid-Phase-5/6/7 chained execution from my multi-step nudge. Recovery scanner did its job — back online in 1 min, same session resumed. Lesson: admin nudges should be one phase at a time, not "do 5 things."
- **Phase 6 was driven implicitly** — orch broadcast CONSOLIDATION_MUTATION→EDGE_DISCOVERY but didn't file per-phase report. SMEs autonomously bound edges from the broadcast. Phase 6 PASS verified by SQL state (10 bound peer edges, 16 cons closed) not by orch chat report. That's actually a healthier pattern.

### Live system state at end of DEMO11

- 11 active components, 10 decommissioned (mass merges)
- 16 consolidations all D/F (11 D + 5 F)
- 67 flows, 10 bound peer edges, 23 dangling, 27 unresolved (initially) → SMEs progressively resolved/bound during Phase 6
- 24 proxy_audit rows: 20 (broadcast, ack) + 4 (consolidation, respond) — all from real merge cascades, NOT synthetic admin tests
- 8 insights, 3217 mcp_audit rows
- 12 phase-broadcasts (4 transitions × 3 agent types)
- DEMO10 workspaces backed up to `src/workspaces.bak.pre-demo11.2026-05-05-153601/`
- DB snapshot pre-DEMO11 at `/tmp/cartograph-snapshots/snap-2026-05-05-153601-pre-demo11.sql`

### Final orch verdict (verbatim from scorecard)

> "PASS overall — 15/16 phases PASS, 1 PARTIAL (8c proxy blocked by send_chat restriction). No DEMO11-BUG markers. 2 DEMO11-NOTE items (spec vs impl delta, tool restriction). System is fit for real-data onboarding."

(8c PARTIAL was closed post-scorecard via admin chat → 16/16.)

---

End of recollection. Feed this + the 7 docs + memory file (auto-loaded) and I'll be caught up. Most important sections post-Phase-10.8: **§0** (current state + next move = real-data onboarding), **§18** (Phase 10.8 + DEMO12 results), §17 (DEMO11 results + insight backlog), §9 (invariants), §10 (service flow phases).

---

## 18. Phase 10.8 + DEMO12 results (2026-05-06)

### Phase 10.8 — Post-DEMO11 insight bundle SHIPPED

Eight DEMO11 insights triaged, three standing semantic questions surfaced. The bundle closes the only insight that warranted code/schema change before real-data onboarding (canonical_name forever-held), the defensive 2-LOC gap on broadcast read, three prompt promotions, and one DEMO11 spec sync.

**Sub-commits + scope:**

| Sub | Commit | Touchpoints |
|---|---|---|
| 10.8.0 plan | `5e616c9` | `IMPLEMENTATION-PHASES.md §10.8` (229 lines) + recall sync |
| 10.8.1 canonical_name partial UNIQUE | `cd3bc20` | `migrations.py` (drop legacy + add partial UNIQUE INDEX), `components.py::upsert_component` SELECT pre-check, `mock_seed.py` ON CONFLICT predicate, +1 test (active+decom collision allowed) |
| 10.8.2 broadcast defensive gate | `5d335b0` | `broadcast.py::get_unacked_broadcasts` + `require_active_agent`, `scanners/broadcasts.py::scan` decom skip, +2 tests |
| 10.8.3 prompt promotions | `8a60df0` | SME (split-child auto-resolve, leave-dangling-don't-force-create), orch (broadcast-driven coordination beats per-agent tasking) |
| 10.8.4 DEMO11 spec sync | `4a9612b` | `oorch-test-prompt-demo11` Phase 5b.5 — owner-violation strict, missing-id lenient |
| 10.8.5 insight triage | DB-only | 5 promoted + 3 wontfix; commit-hash citations in triage_note |
| 10.8.6 DEMO12 targeted | `9c3e88c` | `oorch-test-prompt-demo12-targeted` (290 lines, 5 phases, ~3 min wall-clock) |

### Schema delta

```sql
ALTER TABLE components DROP CONSTRAINT IF EXISTS components_canonical_name_key;
CREATE UNIQUE INDEX IF NOT EXISTS components_canonical_name_active_unique
  ON components (canonical_name) WHERE status = 'active';
```

Idempotent. Zero data migration. Existing rows: active rows still uniquely-named; decom rows free up their names for future re-launch.

### Insight outcomes (5 promoted, 3 wontfix)

| id (8-char) | source | kind | verdict | citation |
|---|---|---|---|---|
| `0c443555` | sme-da948bbe | tool_gap | **promoted** — Phase 10.8.1 schema | partial UNIQUE on active |
| `0f2352f9` | sme-3788c88f | tactic_win | **promoted** — Phase 10.8.3 SME prompt | split-children inherit auto-resolved unresolved rows |
| `d6bff7c3` | orch-8b7025e0 | tactic_win | **promoted** — Phase 10.8.3 orch prompt | broadcast-driven coordination |
| `4b21b236` | sme-f97d2059 | prompt_gap | **promoted** — Phase 10.8.3 SME prompt | leave dangling, don't force-create |
| `d21c93f1` | orch-8b7025e0 | doc_confusing | **promoted** — Phase 10.8.4 DEMO11 spec | lenient delete_attributions_bulk |
| `8bd5dae1` | sme-901967e8 | tactic_win | **wontfix** — already in SME prompt | multi-plane temp-name dance |
| `e67ef425` | sme-f2800e8a | doc_confusing | **wontfix** — rule already in STEP 3 | identifier normalisation |
| `e27b5a8a` | orch-8b7025e0 | workflow_friction | **wontfix** — synthetic test issue | Phase 8c.1 admin-chat ACL |

### DEMO12 targeted scorecard

```
══════════════════════════════════════════════════
DEMO12 — Phase 10.8 TARGETED VERIFICATION
Run: 2026-05-06T01:50Z / orch-8b7025e0
Branch: feat/trigger-manager-cartograh-mcp
Commits verified: cd3bc20 / 5d335b0 / 8a60df0 / 4a9612b
══════════════════════════════════════════════════

Phase 1 — TOOL SURFACE + SCHEMA LIVE                [PASS]
Phase 2 — canonical_name partial UNIQUE (10.8.1)    [PASS]
  2a active+decom collision allowed (id 22d63bc6 inserted) ✓
  2b active+active still rejected (UNIQUE violation) ✓
Phase 3 — Defensive broadcast gate (10.8.2)         [PASS]
  3.2 ValueError on decom caller ✓
  3.3 scanner returns 0 for decom ✓
  3.4 active baseline intact ✓
Phase 4 — delete_attributions_bulk lenient (10.8.4) [PASS]
  4.2 mixed batch: committed=True, applied=2,
      rows[2]={deleted:False, reason:'not_found'} ✓
  4.3 owner-violation strict reject ✓

NET: PASS — 4/4, 0 BUG, 0 NOTE.
```

### What 10.8.3 (prompt promotions) explicitly did NOT verify in DEMO12

The targeted run was scoped to schema + defensive gate + spec sync — testable in 3-min wall-clock. The 3 prompt promotions require a full materialisation/edge-discovery storm:

- **Split-child auto-resolve** — needs a real split with inherited unresolved rows; verifiable on real-data run when first split happens.
- **Leave dangling, don't force-create** — needs a multi-SME storm with cross-component dependencies that don't all materialise at once.
- **Broadcast-driven coordination** — needs orch issuing EDGE_DISCOVERY broadcast + observing SMEs auto-act without per-agent tasks.

Smoke test confirmed all 4 prompts compile clean (sme=95477, iter=32168, orch=30465, res=25605). Behavioural verification deferred to real-data run.

### Live system state at end of Phase 10.8

- HEAD `9c3e88c`, branch `feat/trigger-manager-cartograh-mcp`, pushed.
- 4 daemons running, 114 tools registered.
- Schema migrated: `components_canonical_name_active_unique` partial UNIQUE present, legacy `components_canonical_name_key` dropped.
- DEMO11 + DEMO12 leftover state in DB — wipe before real-data run.
- Workspaces backed up at:
  - `src/workspaces.bak.20260426-2122/` (real-data backup, DO NOT TOUCH)
  - `src/workspaces.bak.pre-demo11.2026-05-05-153601/`
  - `src/workspaces.bak.pre-demo10.2026-05-05-084850/`
  - `src/workspaces.bak.pre-demoMEGA.20260504-154756/`

### Next step: real-data onboarding

1. Wipe DB + active workspaces; back up DEMO11/12 state to `workspaces.bak.pre-realdata.<ts>/`.
2. Restart 4 daemons; verify 114 tools.
3. User provides credentials inline via chat to fresh orch.
4. /loop monitors as before. Watch for 10.8.3 + 10.10 prompt-promotion patterns in the wild.

---

## 19. Phase 10.9 + 10.10 + 10.11 results (2026-05-06, post-Phase-10.8)

Three small ship-before-real-data batches that landed after the §18 state:

### 10.9 — Admin UI catalog drill-down rebuild (`ca7d964`)

Flat `<ul>` wall → collapsible `<details>` sections + client-side paginated tables. 11 sections top-to-bottom: header → description (always visible) → doc (open by default) → slice / attributions / catalog / bindings-in / bindings-out / dangling-out / flows / source-resources (all collapsible, 20 rows/page).

Helpers added to `app.js`: `_detailsSection`, `_mountPaginatedTable` (closure per mount, no globals), `_renderSourceSliceForDrilldown`, `_renderFlowGroupsForDrilldown`, `_mountCatalogDrilldownTables`. New `.cat-*` CSS classes. Cache-bust v=63→v=64.

Graph-tab hover popup untouched — only Catalog drill-down rebuilt. JS syntactically parses (`node --check`). `/api/component/:id/drilldown` returns expected shape on live DB.

### 10.10 — Sleep rewrite + telemetry datastore mandate (`13531d7` + `da49187`)

**Sleep (sme/iter/orch prompts).** Old framing "LAST RESORT" with When-NOT/When-IS bullets was too abstract; agents still sleep-waited for upstream work. New framing:
1. Failure mode front: "Stop pointlessly putting yourself to sleep. Broadcast is faulty and misleading."
2. Default behaviour list (after-task / after-hydrate / after-correction / waiting-for-upstream → YIELD).
3. **The tricky case** — multiple things blocked on you, one needs another to progress → YIELD, not sleep.
4. Narrow extreme case: prompted-twice-external-party + queue-empty → sleep_self(300-600s) MAX.

Resolver prompt intentionally unchanged (resolver reasoning is singleton-serial, fine as-is).

**Datastore mandate (iterator.py).** Front-loaded ★ block at top of telemetry-plane section. Names DEMO7 (2026-04-27) feeds-aggregator-v2 MySQL + Redis incident — admin chased multiple times. Bright-line rule: *"If you emit ZERO datastore rows on a real-data plane, you almost certainly missed surface 3 below — re-walk it."*

Wording iteration (`da49187`): initial version said "databases typically do NOT appear in..." — too absolute. Softened to "MIGHT NOT BE PRESENT" + "often only" + "typically visible". Mandate + breadcrumb intact.

**Behavioural verification:** deferred to real-data run. Smoke tests confirmed all 4 prompts compile clean (sme 96469, iter 33937, orch 31117, res 25605).

### 10.11 — Bedrock-compatible model ids + SME lanes 4 → 8 (`c04f7c9`)

**Problem:** hard-coded Anthropic-API aliases (`claude-opus-4-6`, `claude-sonnet-4-6`) rejected by Bedrock with "provided model identifier is invalid" — needs inference profile ids like `us.anthropic.claude-opus-4-7[1m]`. Without fix, entire agent fleet fails on Bedrock.

**Fix:** new env-sourced constants in `shared/config.py`:
```python
MODEL_OPUS = os.getenv(
    "CARTOGRAPH_MODEL_OPUS",
    os.getenv("ANTHROPIC_DEFAULT_OPUS_MODEL", "claude-opus-4-6"),
)
MODEL_SONNET = os.getenv(
    "CARTOGRAPH_MODEL_SONNET",
    os.getenv("ANTHROPIC_DEFAULT_SONNET_MODEL", "claude-sonnet-4-6"),
)
```

Precedence: project-specific env → Claude Code's own env → hard-coded fallback. On Bedrock with `CLAUDE_CODE_USE_BEDROCK=1`, Claude Code sets `ANTHROPIC_DEFAULT_*_MODEL` to profile ids automatically; our agents inherit.

All 4 agent type files rewired: `resolver.py`, `orchestrator.py`, `sme.py`, `iterator.py` all import `shared.config` and reference `config.MODEL_OPUS` / `config.MODEL_SONNET`.

**SME lanes:** `CARTOGRAPH_INVOKE_LANES_SME` default 4 → 8. Total subprocess concurrency 8 → 12 (orch 1 + iter 2 + res 1 + sme 8).

**Bedrock dry-run verification (pre-commit):**
```
claude -p --model "us.anthropic.claude-opus-4-7[1m]"   "..."              → opus47 ok
claude -p --model "us.anthropic.claude-sonnet-4-6[1m]" "..."              → sonnet46 ok
claude -p --model "us.anthropic.claude-opus-4-7[1m]" --effort medium "..." → effort ok
```

542/542 mcp_tools tests green.

**Note on resolver:** originally designed for opus-4-6. On Bedrock, `ANTHROPIC_DEFAULT_OPUS_MODEL` resolves to opus-4-7 (newer; upgrade, not regression). Pin to 4-6 via `CARTOGRAPH_MODEL_OPUS=us.anthropic.claude-opus-4-6-v1:0` if desired.

### Final model resolution matrix (on Bedrock)

| Agent | Model | Effort |
|---|---|---|
| orchestrator | `us.anthropic.claude-sonnet-4-6[1m]` | — |
| resolver | `us.anthropic.claude-opus-4-7[1m]` | medium |
| iterator | `us.anthropic.claude-sonnet-4-6[1m]` | — |
| sme | `us.anthropic.claude-sonnet-4-6[1m]` | — |

### Concurrency + timing summary (current)

| Parameter | Value | Source |
|---|---|---|
| Trigger scan interval | 2.0s | `TRIGGER_POLL_INTERVAL` |
| Wake debounce | 60s (admin chat / mutation-M bypass) | `WAKE_DEBOUNCE_SECONDS` |
| Orchestrator lanes | 1 | `INVOKE_LANES_ORCH` |
| Iterator lanes | 2 | `INVOKE_LANES_ITER` |
| Resolver lanes | 1 | `INVOKE_LANES_RES` |
| **SME lanes** | **12** (Phase 10.13.13 — was 8, originally 4) | `INVOKE_LANES_SME` |
| **Total subprocess concurrency** | **16** | sum |

---

## 20. Phase 10.12 + Real-data attempts (2026-05-06 afternoon → evening)

### Phase 10.12 — iterator prompt gaps (commits `45a9f4d` + doc-sync `b79bafc`)

First real-data attempt (pre-realdata backup 2026-05-06 11:53) exercised the iterator for the first time on a live plane and surfaced two gaps:

**Gap A — auxiliary MCP port conflicts.** User provided a `last9-reader` MCP server binding to `localhost:8101` (via `src/mcp_servers.yaml`), but `8101` was already in use by an earlier dev process. Iterator silently failed to connect with no clear signal. Prompt now has an `AUXILIARY MCP PORT CONFLICTS` block explicitly directing: check `lsof -i :PORT` before spawning; on EADDRINUSE raise_blocker with the port + the other process's PID + command; never silently retry without diagnosing.

**Gap B — APM surface fallback.** Iterator was expected to find Datadog APM catalog entries, but the Dream11 APM instance lacked the applications section (only agent-side infra metrics). Iterator emitted zero rows and raised blocker. Prompt now has a `SURFACE FALLBACK LADDER` block: if the canonical APM surface is empty or absent, walk the alternative surfaces in order — infra host inventory → cloud compute/RDS/ElastiCache → log sinks → code repo (last resort) — and emit resources from whichever surface yields actual evidence. Zero-row iterator output on a real plane is almost always a surface-selection failure, not a "no resources exist" verdict.

**Files:** `src/agent_management/agent_types/iterator.py` (two new blocks, ~80 LOC). Smoke-tested; no test changes needed.

### Real-data attempts this session

Three pre-realdata backups exist at `src/workspaces.bak.pre-realdata{,2,3}.2026-05-06-{115356,194956,224154}/`. Each corresponds to a DB wipe + fresh onboarding attempt; workspace state preserved forensically.

| Attempt | Backup timestamp | Outcome |
|---|---|---|
| 1 | 11:53 (pre-realdata) | Exposed the two Phase 10.12 iterator gaps; stopped + wiped |
| 2 | 19:49 (pre-realdata2) | Post-10.12; progressed further but still stopped for iteration |
| 3 | 22:41 (pre-realdata3) | **IN PROGRESS** — current DB state (36 agents, 32 components, 291 flows, 56 insights) |

**Current live state per SQL (at doc-sync time):**
- 36 `agent_runs` (mix of active / decom / idle)
- 32 `components` active + some decom
- 26 `resources` across planes (github + telemetry primary)
- 291 `flows` — materialisation + edge-discovery produced a dense graph
- **56 OPEN insights** (distribution: 25 prompt_gap + 20 tactic_win + 5 workflow_friction + 4 tool_gap + 2 doc_confusing)

### Notable open insights (sample)

Full list via `SELECT * FROM agent_insights WHERE status='open'`. Highlights that look promotable at first glance:

- **`a587f682` (tool_gap)** — `list_agents` returns agent_ids but no component-owner mapping. Finding "owner SME for component_id X" took 5 sequential clarifications for sme-d264615e. → Candidate: add `list_agents(include_components=True)` or a standalone `get_component_owner(component_id)` tool.
- **`b5c84e9e` (tool_gap)** — similar "5 sequential clarifications" pattern to find an owner. Likely same root cause as a587f682.
- **`319c3536` (tactic_win)** — pre-absorbing with `cascade_edges=False` avoids unique-constraint violations when both sides have overlapping edges; survivor does manual edge hygiene post-absorb. → Candidate: promote to SME prompt as an explicit "when to opt out of cascade" guidance.
- **`abb7b763` / `1c9e8f99` (tactic_win)** — `get_unmatched_callers` reveals real bound callers whose identifier format doesn't match catalog. Post-merge hygiene signal. → Candidate: add to SME hygiene cycle as a MANDATORY check.
- **`3a7193fd` (tactic_win)** — `get_stale_edges(my_side='to')` reveals incoming edges from decommissioned callers. Counterpart to the existing `my_side='from'` usage. → Candidate: document both sides in prompt.
- **`5488a3a0` (prompt_gap)** — when OTel CLIENT spans have no `net.peer.name` for Redis (very common — Redis client libs don't always set it), telemetry iterator has no clear fallback. → Candidate: extend SURFACE FALLBACK LADDER in iterator with the specific Redis/cache client-span case.
- **`e8dfe292` (prompt_gap)** — circular evidence trap: SMEs reading each other's doc_md for evidence can reinforce mutual incorrect assumptions. → Candidate: explicit rule "doc_md is secondary; primary evidence lives in code + telemetry + attributions".
- **`e433ca6a` (workflow_friction)** — monorepo github SME merged into telemetry SME; cross-plane merge cascades carry subtle ownership nuances. → Candidate: pre-merge checklist for cross-plane case.
- **`0e77a9fe` (prompt_gap)** — Kong gateway attribution (`inbound_gateway: kong`) revealed missing inbound edge; Kong-as-entry-point pattern isn't in prompt. → Candidate: add gateway-pattern to SME Step 3 INBOUND grep catalog.

### Triage workflow (for next session)

1. `psql -c "SELECT id, agent_id, kind, body FROM agent_insights WHERE status='open' ORDER BY created_at"`.
2. Batch by `kind`: start with tool_gap (biggest leverage — new tools), then prompt_gap (bulk promotions in one SME/iterator prompt edit), then tactic_win (hygiene-cycle additions), then doc_confusing (spec fixes), then workflow_friction (process tweaks).
3. UPDATE with triage_note citing commit hash (for promoted) or reason (for wontfix/deferred).
4. Commit promotions as a Phase 10.13 bundle with per-sub-commit scope.
5. Targeted DEMO13 if changes warrant behavioural verification; else advance to 4th real-data attempt.

### Risk notes for real-data iteration

- The 3-attempt pattern (wipe → run → insight → wipe → run) suggests the system has structural gaps that single-prompt changes won't close. Expect Phase 10.13 to be multi-sub-commit, possibly tool-surface changes.
- `src/mcp_servers.yaml` uncommitted `last9-reader` addition is operational glue for the current run — may or may not belong in committed config depending on whether other users have last9 access.
- 56 insights is a LOT; previous triage batches (8 from DEMO11) were much smaller. Budget accordingly.

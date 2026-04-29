# Cartograph — Post-Compaction Recollection (2026-04-29 EOD)

> **Read this FIRST after compaction.** Then `git log --oneline -25`,
> then the 7 canonical docs (HLD / SCHEMA / TRIGGER-MANAGEMENT /
> AGENT-PROMPTS / IMPLEMENTATION-PHASES / ONE-PAGER / PROMPT-ENHANCEMENTS),
> then the 3 memory files. Once oriented, resume normal conversation.
>
> Goal: a fresh-compacted session resumes coherently without
> replaying thousands of tool calls.

---

## 1. Branch + commit state (HEAD as of 2026-04-29 EOD)

- **Working dir:** `/Users/venkata.manohar/release-agent/docs/service-dependency/cartograph`
- **Active branch:** `feat/trigger-manager-cartograh-mcp`
- **HEAD:** `27429fe` (docs sync — all 3 token-optimisation rounds shipped)
- **Phase 6 (Globe) parked:** `feat/globe-experimental` HEAD `1dae0c7` — `docs/GLOBE-MERGE-BRIEF.md` has all decisions. **Do NOT recreate Globe on main; merge that branch when ready.**

Last 18 commits, most recent first:
```
27429fe docs: final sync — all 3 token-optimisation rounds shipped
8d1a7d3 Phase 7.4.14: wake debouncing 5-min window (Round 3 #6)
08c58d1 agent_manager: pre-inject action items in invocation prompt (Round 2 #5)
5e969a1 sme prompt: hot-fix brace escape in BULK CALLS example
135bc57 sme prompt: BULK CALLS DECISION LADDER (Round 2 #4)
716554d Phase 7.4.12: top-3 bulk MCP write tools (Round 2 #3a)
53a6ef1 orchestrator: downgrade to sonnet-4-6 (Round 1 #2)
c9bb733 prompts: concise output rule (Round 1 #1)
b09fdf9 docs: token optimisation plan in IMPLEMENTATION-PHASES.md
8debdb7 docs: sync 5 docs with Phase 7.4.9–7.4.12 + parallel-tool-calls
c7f5fd5 prompt: change "SDK" → "subprocess" in BATCH block
5b3144d prompts: encourage batch + parallel tool calls
bc69c23 docs: PROMPT-ENHANCEMENTS §2.9 — holistic-edge fix
84b0941 sme prompt: identifier normalisation + post-merge edge dedup
72b4a93 Phase 7.4.11: delete_edge MCP tool — owner-scoped, idempotent
4237393 Phase 7.4.7–7.4.10: prompt infusion, plane source, GPU fix
6d64ce2 docs: add PROMPT-ENHANCEMENTS.md — agent-prompt-quality backlog
b4df80a agent_manager: per-type model + reasoning-effort assignment
```

---

## 2. Running daemons (live as of 14:48 — daemons restarted on fresh DB)

| Daemon | Port | Log file | PID at session-end |
|---|---|---|---|
| Postgres (docker container `cartograph-postgres-1`) | 5432 | `docker logs` | — |
| Ollama (mxbai-embed-large 1024d) | 11434 | system | — |
| MCP server | 8100 | `/tmp/cartograph-logs/mcp.log` | 18518 |
| Trigger manager | — | `/tmp/cartograph-logs/triggers.log` | 18647 |
| Agent manager (`python -m main`) | — | `/tmp/cartograph-logs/agents.log` | 18661 |
| Admin UI (FastAPI) | 8200 | `/tmp/cartograph-logs/admin_ui.log` | 18678 |
| Browser-side capture | — | `/tmp/cartograph-logs/browser.log` (via POST /api/clientlog) | — |

**Restart sequence** (from `cd src/`):
```bash
ps aux | grep -E "(admin_ui|cartograph_mcp|trigger_management|python.*-u -m main)" \
  | grep -v grep | grep -v agent-battles | awk '{print $2}' | xargs kill 2>/dev/null
sleep 3
mkdir -p /tmp/cartograph-logs
/opt/homebrew/bin/python3.10 -u -m cartograph_mcp.server   > /tmp/cartograph-logs/mcp.log      2>&1 &
sleep 3
/opt/homebrew/bin/python3.10 -u -m trigger_management.main > /tmp/cartograph-logs/triggers.log 2>&1 &
/opt/homebrew/bin/python3.10 -u -m main                    > /tmp/cartograph-logs/agents.log  2>&1 &
/opt/homebrew/bin/python3.10 -u -m admin_ui.server         > /tmp/cartograph-logs/admin_ui.log 2>&1 &
sleep 4
grep "tools registered" /tmp/cartograph-logs/mcp.log | tail -1   # expect: 89 tools
```

Check live: `curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8200/api/agents` → 200.

---

## 3. DB STATE — FRESH AS OF 14:48 TODAY (snapshot taken pre-wipe)

The DB was **wiped + recreated** at 14:48 today in preparation for DEMO8 (the upcoming comprehensive test prompt). Current DB state:

- 20 tables, 89 tools registered, only orch + resolver auto-spawned by main.py boot:
  - `orch-2b7536c8` (orchestrator, idle)
  - `res-5b7f59d3` (resolver, idle)
- Zero components, zero attributions, zero edges, zero catalogs, zero flows.

**Pre-wipe snapshot saved at:**
```
/tmp/cartograph-snapshots/snap-2026-04-29-pre-demo8.sql   (14 MB)
```

Includes pgvector extension declaration + all 5 embedding columns serialised as text vectors. HNSW indexes rebuild on restore.

**To restore the pre-wipe state:**
```bash
docker exec cartograph-postgres-1 psql -U cartograph -d postgres -c 'DROP DATABASE IF EXISTS cartograph;'
docker exec cartograph-postgres-1 psql -U cartograph -d postgres -c 'CREATE DATABASE cartograph;'
docker exec -i cartograph-postgres-1 psql -U cartograph -d cartograph < /tmp/cartograph-snapshots/snap-2026-04-29-pre-demo8.sql
```

---

## 4. Phase status (all 0 → 7.4.14 ✅; Phase 6 PARKED; Phase 7.4.15 PROPOSED — see §11)

| Phase | Status | Key contribution |
|---|---|---|
| 0 → 7.4.6 | ✅ | Foundation, iteration, materialisation, consolidation, mutation, proxy, catalogs first-class, flows reference catalogs, DEMO7-round-1 fixes. |
| 6 (Globe) | PARKED | On `feat/globe-experimental`. Don't recreate; merge from that branch. |
| 7.4.7 | ✅ 2026-04-27 | Per-type model + reasoning effort (`AgentTypeConfig.model` + `.effort`); admin UI graph node planes from RCA→resources (was attributions.plane — wrong); `/api/agents` returns `resource_planes[]`; agent-row plane symbols (G/C/T/D/F); §2.8 mass prompt infusion across all 5 .py prompts. |
| 7.4.8 | ✅ 2026-04-27 | WebGL GPU memory leak fix v1 — geometry/material caches in `app.js`, `webglcontextlost`/`restored` handlers; new `POST /api/clientlog` endpoint → `/tmp/cartograph-logs/browser.log`; all 4 daemons under Claude Code session with stdout piped via `python -u`. |
| 7.4.9 | ✅ 2026-04-27 | Communications tab — surface decommissioned agents (parallel `_allAgentsById` cache), plane-symbol pills on rows, "Component name" filter (`participant_component=` query param). |
| 7.4.10 | ✅ 2026-04-27 | Graph crash root-cause fix v2 — `pauseAnimation()`/`resumeAnimation()` on tab switch (offscreen RAF was the actual leak), `visibilitychange` listener, wheel-handler null-deref fix, idempotent `webglcontextlost`. Cache-bust v=60→v=62. |
| 7.4.11 | ✅ 2026-04-27 | `delete_edge(agent_id, edge_id)` MCP tool — owner-scoped, idempotent, cascades flows. SME prompt IDENTIFIER NORMALISATION rule (STEP 3) + post-merge EDGE DEDUP step. Tool count 85 → 86. |
| 7.4.12-prompts | ✅ 2026-04-27 | Removed "Call tools sequentially" prompt rule; new `== BATCH + PARALLEL TOOL CALLS ==` shared block in `base.py` MISSION_AND_VOCABULARY. Targets 0.08% pre-fix parallel rate. Subprocess (claude -p) supports parallel natively; not configurable. |
| 7.4.12-tools | ✅ 2026-04-29 | **3 bulk MCP write tools** atomic-with-pre-validation: `upsert_attributions_bulk`, `upsert_catalogs_bulk`, `upsert_edges_outbound_bulk`. Max 500 rows/call. Returns `{committed, applied, rows / errors}`. Tool count 86 → 89. |
| 7.4.13 | ✅ 2026-04-29 | Pre-injected action items in invocation user message — `agent_manager._build_action_items_snapshot()` runs ONE SQL aggregating pending counts (consolidations/tasks/clarifications/chats/broadcasts/terminal_pending/proxied), embeds in user message NOT system_prompt (cache-safety critical). Skips first-turn `get_action_items_summary` round-trip. |
| 7.4.14 | ✅ 2026-04-29 | Wake debouncing 5-min — new `agent_runs.first_pending_at TIMESTAMPTZ` column. Trigger scanner refuses lock until window elapsed unless override (admin chat OR `mutation_assigned_to` on state=M consolidation). Cleared on yield. |
| **7.4.15** | **PROPOSED, NOT SHIPPED** | **Soft-delete model** (see §11) — agent-owned `delete_attribution / delete_catalog / delete_flow` + bulk variants + convert `delete_edge` from hard to soft. New `deleted_at / deleted_by / deletion_reason` columns + null-the-vector on delete. ~1500 LOC across 4 sub-batches. |
| **Round 4 bulks** | **PROPOSED, NOT SHIPPED** | `upsert_flows_bulk`, `insert_unresolved_bulk`, `ack_broadcasts_bulk`, `ack_terminals_bulk`, plus `delete_edges_bulk` (Round 4 + 7.4.15 share the bulk delete companion). |

---

## 5. Token optimisation — ALL 3 ROUNDS SHIPPED 2026-04-29

| # | Lever | Saving | Commit |
|---|---|---:|---|
| 0 | Parallel tool calls (BATCH block) | 20-30% | `5b3144d` |
| 1 | Concise output rule | 8-10% | `c9bb733` |
| 2 | Orch → sonnet-4-6 (resolver STAYS opus-4-6) | 10-14% | `53a6ef1` |
| 3 | Top-3 bulk MCP write tools | 6-9% | `716554d` |
| 4 | BULK CALLS DECISION LADDER prompt | 3-5% | `135bc57` + `5e969a1` |
| 5 | Pre-inject action items in user message | 3-5% | `08c58d1` |
| 6 | Wake debouncing 5-min | 10-15% | `8d1a7d3` |

**Compound math (project lifetime $1,234.85):** ~$700 spend on equivalent workload, ~43% reduction, ~$535 saved. ~$1,500-1,800/month at sustained scale.

**Levers explicitly DROPPED** (do NOT reintroduce):
- Session reset at phase boundaries — loses context continuity.
- Lane-cap quiet mode — bursts dominate, not quiet periods.
- Haiku for trivial wakes — kills productive follow-up opportunity.
- Effort='low' on opus — subsumed by orch→sonnet.
- Zero-state-change wake skip — scanners already state-driven.
- **Resolver→sonnet** — too high-stakes (merge/split decisions).
- Generic `bulk_execute(calls=[...])` dispatcher — re-implements native parallel, worse type safety.

---

## 6. Tool surface (89 total)

```
action_items (2):     get_action_items_summary, get_action_items_detail
chat (4):             send_chat, ack_chats, get_unacked_chats, get_chat_history
broadcast (4):        send_broadcast, ack_broadcast, get_unacked_broadcasts,
                      update_broadcast_persistence
secrets (4):          put_secret, get_secret, list_secrets_for_plane, delete_secret
tasks (5):            create_task, respond_task, raise_blocker, get_my_tasks, get_task_thread
resources (9):        upsert_resource, upsert_resources_bulk, get_resource,
                      list_resources_for_plane, list_all_resources, get_resource_counts,
                      mark_resource_done, reject_resource, reject_resources_bulk
agent_lifecycle (11): create_agent, bulk_spawn_smes, list_agents, reset_agent,
                      decommission_agent(_bulk), decommission_component(_bulk),
                      sleep_self, bulk_sleep_agents, bulk_wake_agents
components (19):      upsert_component, upsert_attribution,
                      **upsert_attributions_bulk** (7.4.12),
                      create_edge (3.9 shim),
                      insert_unresolved, resolve_reference,
                      upsert_edge_catalog (7.4 shim), upsert_edge_outbound,
                      **upsert_edges_outbound_bulk** (7.4.12),
                      bind_edge, upsert_flow,
                      **delete_edge** (7.4.11) — owner-scoped, idempotent, cascades flows,
                      get_component, get_attributions, get_edges,
                      get_unresolved, get_component_edges,
                      get_flow, get_flow_inverse
notifications (1):    get_agent_notifications
consolidation (5):    nominate, respond, review, get_my, get_thread
clarification (4):    create, respond, get_my, get_thread
search (1):           vector_search   (lean projection per 7.4.4)
mutation (10):        execute_mutation, complete_consolidation, absorb_agent,
                      spawn_child_agent, transfer_attributions, transfer_edges,
                      transfer_flows, get_my_components, get_stale_edges, get_stale_flows
proxy (2):            get_my_proxy_items, act_on_proxy_item
insights (1):         record_insight   (Phase 5.9)
terminal_acks (1):    ack_terminal     (Phase 7.1)
catalogs (6):         upsert_catalog,
                      **upsert_catalogs_bulk** (7.4.12),
                      get_my_catalogs, get_my_catalog_callers,
                      get_unmatched_callers, get_orphan_catalogs

Plus auto-wrapped: every @mcp.tool() registration wrapped by
cartograph_mcp.audit.audited (Phase 5.10) → mcp_audit table.
```

**Total: 89.** Verify with `grep "tools registered" /tmp/cartograph-logs/mcp.log | tail -1`.

---

## 7. Schema state

20 tables (added `terminal_acks` in 7.1, `catalogs` in 7.4, `agent_insights` in 5.9, `mcp_audit` in 5.10):
- `components`, `attributions`, `edges`, `flows`, `unresolved`
- `agent_runs`, `resources`, `resource_component_agents`, `tasks`, `secrets`
- `consolidations`, `clarifications`, `communications`, `broadcast_acks`
- `proxy_audit`, `proxy_items` (legacy, unused since Phase 4)
- `agent_insights`, `mcp_audit`
- `terminal_acks`, `catalogs`

**Recent schema deltas:**
- `agent_runs.first_pending_at TIMESTAMPTZ` (Phase 7.4.14, idempotent ALTER ADD COLUMN IF NOT EXISTS).
- Phase 7.4.15 (PROPOSED, NOT SHIPPED) would add `deleted_at`, `deleted_by`, `deletion_reason` on attributions, catalogs, edges, flows.

---

## 8. Embedding flow — 3-actor model

| Stage | Actor | Where |
|---|---|---|
| Generate embedding for write | **Ollama** (external HTTP) | `localhost:11434/api/embed`, model `mxbai-embed-large`, 1024-dim. ~40-60ms warm. Done inline inside MCP tools. |
| Generate embedding for query | **Ollama** (external HTTP) | Same call inside `vector_search`. |
| Cosine similarity given input vector | **pgvector (native, in DB)** | `<=>` operator + HNSW index. `ORDER BY embedding <=> %s::vector LIMIT N`. |

NOT pgvector-native generation. NOT a background loop in normal operation. `embedding_backfill.py` exists for one-off model migrations only.

Read path uses lean projection (Phase 7.4.4) — `vector_search` returns id + identity columns + similarity, NOT the embedding vector or doc/slice/metadata blobs.

---

## 9. Critical invariants (DON'T violate)

1. **1 SME = 1 active component.** Splits go through Consolidation, not direct creation.
2. **Edges (Phase 3.9):** asymmetric. Bound = both set (caller owns). Dangling = to NULL (caller owns). **Self-loops (from = to) accepted as of Phase 7.3.** Catalogs (Phase 7.4) live in their own table now, not edges.
3. **Catalogs (Phase 7.4):** OWN TABLE, not edges. Owner-only declarations. Noun-form `kind`.
4. **Flows reference catalog rows via `incoming_catalog_id` (Phase 7.4.2 — first-class FK to `catalogs`).** Outgoing is still an edge id. **No catalog → no flow.**
5. **Junctions are routing scaffolding, NEVER real components in any logic/algo.**
6. **Terminal ack:** every participant of a closed entity must explicitly `ack_terminal` — else trigger scanner re-wakes them.
7. **Pre-merge handoff:** mutation POC MUST raise clarification to target before `absorb_agent` (convention, enforced via prompt).
8. **No session reset needed for prompt changes** — `agent_manager.py` passes `--system-prompt` fresh on every `--resume`. System prompt rebuilt from disk per spawn.
9. **Mutation cascades (Phase 7.4.2):** `absorb_agent` runs unconditional **catalog cascade** BEFORE flow cascade (so flow.incoming_catalog_id refs land on survivor's catalogs). `spawn_child_agent` accepts `transfer_catalog_ids`.
10. **Self-loops permitted (Phase 7.3 + 7.4.4):** DB CHECK + Python guards both gone.
11. **`vector_search` returns lean rows (Phase 7.4.4):** no embedding vectors, no doc/slice/metadata blobs in results — search-then-fetch via `get_*(id)` for full detail.
12. **`get_action_items_summary` is uniform `dict[str, int]` (Phase 7.4.5):** rich per-proxy breakdown lives on `get_action_items_detail.proxied`.
13. **Component planes come from RCA→resources, not attributions (Phase 7.4.7):** what plane a component LIVES on is the plane(s) of its source resource(s). `attributions.plane` is the DISCOVERY plane.
14. **Per-type model + effort (Phase 7.4.7 + Round 1 #2):** orchestrator = `claude-sonnet-4-6` (no effort flag); resolver = `claude-opus-4-6 + medium` effort (DON'T DOWNGRADE — high-stakes merge/split decisions); iterator + sme = `claude-sonnet-4-6` (no effort). System prompt rebuilt fresh per spawn.
15. **Sleep is LAST RESORT, not default (Phase 7.4.7):** 300-600s MAX, never 24h, never >3600s.
16. **WebGL leak fix (Phase 7.4.10):** real root cause was offscreen RAF — pause/resume on tab switch + visibilitychange. Per-cache disposal + idempotent context-lost handler complete the fix.
17. **Identifier normalisation across planes (Phase 7.4.11):** holistic-edge invariant requires byte-identical identifier across SMEs from different planes. Drop `/dbname` suffix from JDBC URLs, lowercase HTTP hosts, drop trailing slashes / query strings, templatise path params (`/users/{{id}}`). Tiebreak: write LEANER form (telemetry surfaces); richer details in metadata. Post-merge EDGE DEDUP uses `delete_edge` to collapse duplicates.
18. **Parallel tool calls (commit `5b3144d`):** Anthropic's tool-use API allows multiple `tool_use` blocks per assistant turn → all dispatched concurrently → all results bundle into ONE next user turn → ONE LLM round-trip ingests them. Subprocess (claude -p) supports it natively; not configurable, just a model-decision-per-turn knob. Pre-fix only 0.08% messages emitted >1 tool_use.
19. **Concise output rule (Round 1 #1, commit `c9bb733`):** ≤2 sentences explanation per assistant turn, no preambles, no post-hoc summaries restating tool results, no methodology explanations, no rephrasing of admin's input. **CRITICAL caveat — DO NOT compromise on identifiers, file paths, hostnames, IDs, line numbers, hashes, version strings, error messages, or specific data.** The brevity rule trims English/jargon ONLY, never evidence. Reserved long-text contexts (where brevity yields to information density NOT narration filler): `component_doc_md`, consolidation message bodies (one paragraph with concrete evidence), `blocker_detail`, `record_insight` body.
20. **BULK CALLS DECISION LADDER (Round 2 #4, commit `135bc57` + `5e969a1`):** 3-rung priority order in SME prompt — RUNG 1 use a bulk MCP variant (atomic, fewer round-trips, less token cost); RUNG 2 emit parallel tool_use blocks in one assistant turn; RUNG 3 Python script via Bash for >500 rows. Each rung has a worked example.
21. **Pre-injected action items (Round 2 #5, commit `08c58d1`):** snapshot in invocation USER message NOT system_prompt (cache-safety). Includes `proxied_count` for inherited work. Existing PostToolUse `notify.py` hook stays (different purpose — mid-session live interrupts).
22. **Wake debouncing 5-min (Phase 7.4.14, commit `8d1a7d3`):** `agent_runs.first_pending_at` stamped on first sighting; refuses lock until window elapsed unless override (admin chat OR mutation POC on state=M consolidation). Cleared on yield.
23. **Smoke-test rule:** every prompt edit MUST run `SYSTEM_PROMPT_TEMPLATE.format(plane='x', resource_id='y')` before commit. Caught 3 brace bugs already (`{get,post}`, `{id}`, `{"plane":...}`).

---

## 10. PostToolUse notify.py hook — what it is + race condition

The hook (`src/agent_management/hooks/notify.py`) fires **after every tool call** in the agent's `claude -p` subprocess. It runs as a separate Python subprocess (NOT an LLM call), queries `get_agent_notifications` MCP tool (DB read for unacked chats + broadcasts from priority sources — admin + orchestrator), and prints `[NOTIFY] N new high-priority items...` via JSON envelope `{"hookSpecificOutput": {...}}`. Claude Code injects that string into the agent's next user turn as `additionalContext`. Rate-limited 10s per agent via `.cartograph-notify-last` marker file in cwd.

**The hook does NOT increase LLM calls** — it adds ~50-100 input tokens to the next already-billable user turn. No new `/v1/messages` call fires because of the hook. The agent CHOOSES to respond to the notification (e.g. by calling `get_action_items_detail`); that's when an additional LLM call would happen, but only if the agent decides to act on it.

**Race condition with parallel tool calls (NOT YET FIXED):** when the agent emits N parallel `tool_use` blocks, N hooks spawn concurrently. They race past the rate-limit check (all read the same stale marker timestamp), all hit MCP (cheap DB reads), all may emit a duplicate `[NOTIFY]` string. The agent's bundled user turn gets up to N copies of the same notification string. ~50-100 tokens × N waste per parallel batch.

**Proposed fix (NOT YET SHIPPED):** add `fcntl.flock(fd, LOCK_EX | LOCK_NB)` around the read-and-write of `.cartograph-notify-last`. Only one of N parallel hooks acquires the lock; others get `BlockingIOError` and exit silent. ~10 LOC change. Eliminates duplicates entirely. Saves ~$0.0006/wake — small absolute, removes "5 identical NOTIFYs" noise that confuses agent reasoning.

---

## 11. Soft-delete plan (Phase 7.4.15) — DISCUSSED, AGREED, NOT YET SHIPPED

**User pushed back on my "no single-row delete needed" reasoning. They're right. Ship it.**

**Schema (idempotent migrations, applies to attributions / catalogs / edges / flows):**
```sql
ALTER TABLE <table>
  ADD COLUMN deleted_at TIMESTAMPTZ,
  ADD COLUMN deleted_by TEXT,
  ADD COLUMN deletion_reason TEXT;
CREATE INDEX idx_<table>_active ON <table>(component_id) WHERE deleted_at IS NULL;
```

**Tools (single + bulk pairs):**
- `delete_attribution(agent_id, attribution_id, reason?)` — soft-delete + null embedding. Owner-scoped (caller must own component_id of the attribution). Idempotent.
- `delete_attributions_bulk(agent_id, attribution_ids[])` — atomic-with-pre-validation.
- `delete_catalog(agent_id, catalog_id, reason?)` — same pattern + cascade-soft-delete dependent flows (NOT hard cascade — set their deleted_at too).
- `delete_catalogs_bulk(agent_id, catalog_ids[])` — same.
- `delete_flow(agent_id, flow_id, reason?)` — same. No cascade (flows are leaves).
- `delete_flows_bulk(agent_id, flow_ids[])` — same.
- **`delete_edge` conversion** — Phase 7.4.11 ships HARD delete. Convert to soft + null embedding for consistency.
- `delete_edges_bulk(agent_id, edge_ids[])` — atomic-with-pre-validation.

**Read-path filter additions (CRITICAL):** every read tool needs `WHERE deleted_at IS NULL` added. Default: hide deleted unless `include_deleted=False` is explicitly flipped.
- Tools to update: `get_attribution(s)`, `get_component_edges`, `get_my_catalogs`, `get_flow(_inverse)`, `vector_search` (filter by `WHERE deleted_at IS NULL`), `get_unmatched_callers`, `get_orphan_catalogs`, all admin UI queries.

**Mutation paths to update:**
- `absorb_agent` catalog cascade — currently DELETEs target's colliding catalog rows. Switch to soft-delete with `deletion_reason='merged_into:<surviving_catalog_id>'`. Same for transfer_edges collision-collapse.

**Estimated effort:** ~1500 LOC across 4 sub-batches (schema migration + read-path filter audit / singles + delete_edge conversion / bulks / admin UI surfacing). 1 day.

**Suggested sub-batches:**
1. Schema migration + read-path filter audit (silent groundwork, no new tools).
2. Singles + delete_edge conversion.
3. Bulks.
4. Admin UI surfacing (deleted-row pill, "show deleted" toggle in drilldown).

**User said this is the right design** — your "soft delete with dedicated column + null the vector" is better than my "metadata tombstone" suggestion. Reasons it wins: reversibility, audit trail preserved, vector_search clean (NULL embeddings are already filtered), graph hygiene cheap (`WHERE NOT deleted` clause everywhere), foreign-key chains untouched, forensic queries one SQL away.

---

## 12. Round 4 bulk completion (NOT YET SHIPPED — pairs with 7.4.15)

In addition to soft-delete, these bulk variants were planned but not shipped:

| Tool | Why ship | Effort |
|---|---|---|
| `upsert_flows_bulk` | Step 4 catalog→outgoing join produces N flows; symmetric with the 3 already shipped | ~75 LOC |
| `insert_unresolved_bulk` | Pairs with bulk dangling-edge writes (DANGLING-EDGE-pair rule) | ~50 LOC |
| `ack_broadcasts_bulk` | Mechanical batch | ~30 LOC |
| `ack_terminals_bulk` | Mechanical batch | ~30 LOC |
| Read bulks (`get_attributions_bulk`, `get_components_bulk`, `get_component_edges_bulk`) | §3.10 gap — resolver triangulation needs multi-component reads | ~30 LOC each |
| `bind_edges_bulk` | Resolution phase, low-frequency today | ~50 LOC |

Total: ~350 LOC + tests. Combined with 7.4.15 deletes: ~2000 LOC of one cohesive sub-phase, tool count 89 → ~100.

---

## 13. DEMO8 test prompt (REQUESTED, NOT YET COMPOSED)

User asked for a comprehensive test prompt covering:
- Everything from DEMO7 (catalogs first-class, flows reference catalogs, self-loops, pre-merge handoff, terminal acks, proxy inheritance, mutation lifecycle, consolidation negotiation, absorber-pick, evidence ladder, in-flight learning, one-merge-ripens-at-a-time, pre-M conflict check)
- Plus everything new since DEMO7 (delete_edge + identifier normalisation, the 3 bulk MCP write tools, parallel tool calls / BATCH block, concise output, BULK CALLS DECISION LADDER, pre-inject action items, wake debouncing, post-merge EDGE DEDUP)

Test should exercise a couple of dummy SMEs in an orchestrator dance. Final scorecard tagged `[DEMO8-RESULT]` for admin to paste back.

**Decision pending:** ship Round 4 + Phase 7.4.15 bulks first (so DEMO8 exercises the full delete + bulk surface), OR compose DEMO8 against the current 89-tool surface and ship the bulk completion as a follow-up?

---

## 14. User collaboration style (preserve verbatim from prior recollection)

- Crisp responses preferred. No fluff, no apology loops. Honest pushback welcomed.
- Incremental commits, push after each cohesive change. (LESSON LEARNED 2026-04-27: I let work pile up to 17 files / +2244 lines once; user called it out. Back to incremental.)
- Daemon restart silently after code changes — never ask.
- TDD when implementing — tests first, run red, then make green.
- Convention prefixes (`[DEMO7-XX]`, `[DEMO8-XX]`) for cross-session reporting.
- When user says "go go go" or "do it" — they mean execute now, don't re-confirm.
- Don't ever recreate Globe (Phase 6) on main — merge from `feat/globe-experimental` branch when ready.
- **User pushes back on speculative/unjustified levers.** Dropped these in this session: session reset, lane-cap quiet mode, haiku for trivial wakes, zero-state-change wake skip, generic bulk_execute dispatcher.

---

## 15. Critical don'ts (re-instated + new)

1. Do NOT fix the broadcast via-badge display on Communications tab. Parked.
2. Do NOT decommission agents as a cleanup shortcut.
3. Do NOT run the demo41 metadata purge without explicit user say-so.
4. Do NOT send admin chats unsolicited; admin is the user.
5. Do NOT recommend session resets for agent prompt changes — `--system-prompt` hot-loads.
6. Do NOT use chat to communicate agent→agent (`send_chat` refuses non-admin senders to non-admin recipients). Orch uses tasks/broadcasts.
7. Do NOT recreate Globe on main — merge from `feat/globe-experimental` instead.
8. Do NOT duplicate comm rows for terminal-state announcements — use `terminal_acks` junction now.
9. **Do NOT downgrade resolver from opus-4-6.** Merge/split approve/reject is high-stakes.
10. **Do NOT let work pile uncommitted.** Incremental cadence — one commit per cohesive change.
11. **Do NOT modify `--system-prompt` per wake.** Cache-key sensitivity. Variable content goes in user message ONLY.
12. **Do NOT skip the `SYSTEM_PROMPT_TEMPLATE.format()` smoke test before committing prompt edits.** 3 brace bugs already caught.
13. **Do NOT compromise on identifiers / file paths / hostnames / IDs / data when applying the concise output rule.** Trim English only, never evidence.

---

## 16. Re-hydration checklist (do in order post-compaction)

1. **`git log --oneline -25`** — confirm `27429fe` is HEAD on `feat/trigger-manager-cartograh-mcp`.
2. **`grep "tools registered" /tmp/cartograph-logs/mcp.log | tail -1`** — should show `89 tools`. If not, MCP server isn't running; restart per §2.
3. **Read this doc fully.**
4. **Read the 7 docs** (HLD / SCHEMA / TRIGGER-MANAGEMENT / AGENT-PROMPTS / IMPLEMENTATION-PHASES / ONE-PAGER / **PROMPT-ENHANCEMENTS**) — user will paste these.
5. **Check the 3 memory files** at `~/.claude/projects/-Users-venkata-manohar-release-agent-docs-service-dependency/memory/` (auto-loaded).
6. **Verify daemons running:** `ps aux | grep -E "(admin_ui|cartograph_mcp|trigger_management|python.*-u -m main)" | grep -v grep | grep -v agent-battles` — should be 4 cartograph processes.
7. **`tail -f /tmp/cartograph-logs/admin_ui.log`** for live admin-UI request stream; **`grep webglcontextlost /tmp/cartograph-logs/browser.log`** for any in-flight GPU crashes.

If user asks "what's next" — answer is in §11 (Phase 7.4.15 soft-delete) + §12 (Round 4 bulks) + §13 (DEMO8 prompt).
If user asks "what's the current ship state" — §4 + §5.

---

End of recollection. Feed this + the 7 docs + memory file (auto-loaded) and I'll be caught up.

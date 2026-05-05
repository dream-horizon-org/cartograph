# Cartograph — Post-Compaction Recollection (2026-05-05, post-DEMO11)

> **Read this FIRST after compaction.** Then `git log --oneline -25`,
> then the 7 canonical docs (HLD / SCHEMA / TRIGGER-MANAGEMENT /
> AGENT-PROMPTS / IMPLEMENTATION-PHASES / ONE-PAGER / PROMPT-ENHANCEMENTS),
> then the 3 memory files. Then this doc. Once oriented, resume.
>
> Goal: a fresh-compacted session resumes coherently without
> replaying thousands of tool calls.

---

## 0. WHERE I AM RIGHT NOW (the most important section)

**DEMO11 ran end-to-end: 16/16 PASS, 0 FAIL, 0 BUG. System fit for real-data onboarding. Next: insight triage + decisions, then wipe DB, then real data.**

### What's done (committed + pushed):

- All Phase 0 → Phase 10.7 shipped on `feat/trigger-manager-cartograh-mcp`.
- DEMO-MEGA ran 2026-05-04: **29 PASS / 1 N/A / 1 VERIFY_PENDING / 0 FAIL** out of 31 phases.
- Phase 10.1.3 (mcp_call_batch name-map + create_edge docstring) shipped `376b27a`. DEMO10 verified: **8/8 PASS** — `ad3830c`.
- Phase 10.7 (description column + filtered vector_search + workspace doc_md + exclude_self) shipped across 8 commits `ec5a318` → `e7ce669`.
- **2026-05-05 just before DEMO11:** SME prompt expanded with **service-document standard for `component_doc_md`** (8 required markdown sections: Role / Key Surfaces / Inbound Flows / Outbound Flows / Runtime+Deploy / Storage+State / Operational Notes / Source). DEMO11 Phase 4 gained a doc_md quality metric. Commit `872b407`. Smoke-test caught a `{token}` brace bug, fixed.
- **DEMO11 ran 2026-05-05** (T+1h47m): 16/16 PASS, 0 FAIL, 0 BUG. Phase 8c.1 was reported PARTIAL initially (admin must send chat to decom; orch can't self-fire); admin closed it inline post-scorecard, flipped PASS. See §17 below for full detail.

### What's pending (next session — pick up here):

1. **Insight triage** — 8 insights filed during DEMO11. 3 promotable, 1 needs schema decision, 1 is the "broadcast read no decom gate" defensive finding from negative tests. See §17.
2. **Optional small fixes before real-data run:**
   - Decision: canonical_name uniqueness post-decom (insight `da948bbe`) — keep as forever-held or migrate to `WHERE status='active'` partial unique
   - Decision: `delete_attributions_bulk` semantics — current "commit valid + per-row not_found" vs DEMO11 spec "reject whole batch on missing id"
   - Promote spawn_child_agent's auto-migrate-and-resolve behavior to SME prompt (insight `3788c88f`)
   - Promote "broadcast-driven coordination works without per-agent tasking" to orch prompt (orch insight)
   - Defensive 2-LOC: add `require_active_agent` gate to `broadcast.get_unacked_broadcasts` + scanner skip
3. **Wipe DB + workspaces** preserving real-data backup `src/workspaces.bak.20260426-2122/`. Backup current DEMO11 workspaces at `src/workspaces/` to `workspaces.bak.pre-realdata.<ts>/`. Restart 4 daemons. Verify 114 tools.
4. **Real-data onboarding** — user provides credentials inline via chat. Orch handles intake. /loop monitors as before.

### DEMO11 final scorecard location:

```sql
SELECT text FROM communications WHERE from_agent='orch-8b7025e0'
  AND text LIKE '%FINAL SCORECARD%' ORDER BY created_at DESC LIMIT 1;
```

Run cost: **$86.29 total** ($62.99 SME, $15.68 orch, $6.19 res, $1.43 iter). 2,353 LLM turns, 962 tool calls, 0.41 tools/turn average, 48× mcp_call_batch invocations (~240-336 serial round-trips saved).

---

## 1. Branch + commit state (HEAD as of 2026-05-05, post-DEMO11)

- **Working dir:** `/Users/venkata.manohar/release-agent/docs/service-dependency/cartograph`
- **Active branch:** `feat/trigger-manager-cartograh-mcp`
- **HEAD:** `872b407` (SME prompt: doc_md service-document standard + DEMO11 doc_md quality metric). Pushed to origin.

Recent commits, newest first:
```
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
ps aux | grep -E "(admin_ui|cartograph_mcp|trigger_management|python.*-u -m main)" \
  | grep -v grep | awk '{print $2}' | xargs -r kill 2>/dev/null
sleep 3
mkdir -p /tmp/cartograph-logs
# Restart from src/
cd src
/opt/homebrew/bin/python3.10 -u -m cartograph_mcp.server   > /tmp/cartograph-logs/mcp.log      2>&1 &
sleep 5
/opt/homebrew/bin/python3.10 -u -m trigger_management.main > /tmp/cartograph-logs/triggers.log 2>&1 &
/opt/homebrew/bin/python3.10 -u -m main                    > /tmp/cartograph-logs/agents.log  2>&1 &
/opt/homebrew/bin/python3.10 -u -m admin_ui.server         > /tmp/cartograph-logs/admin_ui.log 2>&1 &
sleep 5
grep "tools registered" /tmp/cartograph-logs/mcp.log | tail -1   # expect: 114 tools
```

If Docker daemon is down: `open -a Docker`, wait ~20s, then `docker start cartograph-postgres-1` before MCP.

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

1. **`git log --oneline -25`** — confirm `e1574bb` is HEAD on `feat/trigger-manager-cartograh-mcp`.
2. **`grep "tools registered" /tmp/cartograph-logs/mcp.log | tail -1`** — should show `114 tools`. If not, MCP server isn't running; restart per §2.
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

End of recollection. Feed this + the 7 docs + memory file (auto-loaded) and I'll be caught up. Most important sections post-DEMO11: **§0** (current state + next move), **§17** (DEMO11 results + insight backlog), §9 (invariants), §10 (service flow phases).

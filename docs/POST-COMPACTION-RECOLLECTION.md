# Cartograph — Post-Compaction Recollection (2026-04-29, updated through 7.4.11 + parallel-tool-calls + token-optimisation plan)

> **Read this FIRST after compaction.** Then `git log --oneline -50`,
> then the 6 canonical docs (HLD / SCHEMA / TRIGGER-MANAGEMENT /
> AGENT-PROMPTS / IMPLEMENTATION-PHASES / ONE-PAGER), then the 3
> memory files. Once oriented, resume normal conversation.
>
> Goal: give a fresh-compacted session enough state to resume
> coherently without replaying thousands of tool calls.

---

## 1. Branch + commit state

- **Working dir:** `/Users/venkata.manohar/release-agent/docs/service-dependency/cartograph`
- **Active branch:** `feat/trigger-manager-cartograh-mcp`
- **HEAD:** `d8fe56f` Phase 7.4.2 (then a 7.4.3 doc-sync commit)
- **Phase 6 (Globe) parked on separate branch:** `feat/globe-experimental` HEAD `1dae0c7` — includes `docs/GLOBE-MERGE-BRIEF.md` capturing all decisions for that branch. **Do NOT recreate Globe on main; merge that branch when ready.**

Last commits on main (most recent first):
```
<7.4.3 doc sync — pending; see §X below>
d8fe56f Phase 7.4.2: flows reference catalogs first-class + admin UI catalog fixes
ef3c4e7 Phase 7.4 follow-up: extend vector_search to include catalogs table
aadcadb Phase 7.5: Final doc + memory sync across 6 docs
595ef85 Phase 7.4: Catalogs as first-class table
53a2894 Phase 7.3: Self-loop CHECK constraint relaxation
feecdc0 Phase 7.2: Pre-merge handoff convention via SME prompt
b850a28 Phase 7.1: terminal-state ack model (replaces Phase 5.5 auto-ack)
88319bc Phase 7 plan: terminal-acks + handoff + self-loops + catalogs first-class
c67ecb3 Docs: pre-Phase-7 sync — add 5.12 to Phase 5 ship log + memory note
```

---

## 2. Running daemons (Phase 7.4.8 — running under active Claude Code session, logs in `/tmp/cartograph-logs/`)

| Daemon | Port | Log file |
|---|---|---|
| Postgres (docker) | 5432 | (docker logs) |
| Ollama (mxbai-embed-large 1024d) | 11434 | (system) |
| MCP server | 8100 | `/tmp/cartograph-logs/mcp.log` |
| Trigger manager | — | `/tmp/cartograph-logs/triggers.log` |
| Agent manager (`python -m main`) | — | `/tmp/cartograph-logs/agents.log` |
| Admin UI (FastAPI) | 8200 | `/tmp/cartograph-logs/admin_ui.log` |
| **Browser-side capture** (Phase 7.4.8) | — | `/tmp/cartograph-logs/browser.log` |

The four cartograph processes run as background shells under the
active Claude Code session, started with `python3.10 -u -m <module>`
so output flushes in real-time. The `-u` is critical — without it
buffered output makes log grep useless.

**Restart sequence** (from `cd src/`, kills + relaunches all four):
```bash
ps aux | grep -E "cartograph_mcp|admin_ui|trigger_management|python.*-m main" \
  | grep -v grep | grep -v agent-battles | awk '{print $2}' | xargs kill 2>/dev/null
sleep 2
mkdir -p /tmp/cartograph-logs
cd src
/opt/homebrew/bin/python3.10 -u -m cartograph_mcp.server   > /tmp/cartograph-logs/mcp.log      2>&1 &
/opt/homebrew/bin/python3.10 -u -m trigger_management.main > /tmp/cartograph-logs/triggers.log 2>&1 &
/opt/homebrew/bin/python3.10 -u -m main                    > /tmp/cartograph-logs/agents.log  2>&1 &
/opt/homebrew/bin/python3.10 -u -m admin_ui.server         > /tmp/cartograph-logs/admin_ui.log 2>&1 &
sleep 4
grep "tools registered" /tmp/cartograph-logs/mcp.log | tail -1   # expect: 85 tools
```

Verifying all four came up:
```bash
ps aux | grep -E "(admin_ui|cartograph_mcp|trigger_management|python.*-m main)" \
  | grep -v grep | grep -v agent-battles | awk '{print $2, $11, $12, $13, $14}'
curl -s -o /dev/null -w "admin_ui: %{http_code}\n" http://localhost:8200/api/agents
```

`/tmp/cartograph-logs/browser.log` collects browser-side crashes
(WebGL context lost, JS errors, unhandled rejections, beforeunload)
posted by `app.js` to the Phase 7.4.8 `/api/clientlog` endpoint.
Greppable signals:
```bash
grep webglcontextlost  /tmp/cartograph-logs/browser.log
grep window.error      /tmp/cartograph-logs/browser.log
grep ERROR             /tmp/cartograph-logs/*.log
grep "5[0-9][0-9]"     /tmp/cartograph-logs/admin_ui.log    # any 5xx
tail -f                /tmp/cartograph-logs/admin_ui.log
```

---

## 3. Phase status snapshot (all phases shipped through 7.5 except 6)

| Phase | Status | Key contribution |
|---|---|---|
| 0 | ✅ | Foundation: schema, MCP server, agent manager, admin UI scaffolding |
| 1 | ✅ | Iteration: tasks + secrets + agent_lifecycle + resources |
| 2.1-2.5 | ✅ | Materialisation: SME tools + lane-based parallel + admin UI panels + sleep + forward-only broadcasts |
| 3 | ✅ | Consolidation: 5 consolidation tools + 4 clarification tools + auto-transitions + embeddings |
| 3.5 | ✅ | Graph viz with 3d-force-graph |
| 3.7 | ✅ | Local Ollama embeddings (mxbai-embed-large 1024d) |
| 3.8 | ✅ | components.source_slice + SME materialisation flow rewrite |
| 3.9 | ✅ | Asymmetric edge protocol — catalog + bindings + flows |
| 3.10 | ✅ | Graph viz upgrades — kind-aware rendering, 3-zone hover, LOS BFS |
| 4 | ✅ | Mutation lifecycle + proxy inheritance |
| 4.1 | ✅ | Mutation completeness + 10 gap fixes from DEMO41 |
| 4.2 | ✅ | DEMO41 post-run fixes |
| 5.1-5.12 | ✅ | Tweaks & improvements (12 sub-phases — routing, entities, catalog, insights, audit, persistence toggle, kind→type rename) |
| **6** | **PARKED** | Globe sphere-constrained graph view — on `feat/globe-experimental` branch, NOT on main |
| **7.1-7.5** | **✅ SHIPPED** | Terminal acks + pre-merge handoff + self-loops + catalogs first-class + doc sync |
| **7.4.2** | **✅ SHIPPED 2026-04-26** | flows reference catalogs first-class (FK to catalogs) + admin UI `/api/components.catalog_count` + `/api/component/{id}/drilldown.catalog` fixes + `absorb_agent` catalog cascade + `spawn_child_agent` `transfer_catalog_ids` + FE field rename + mock_seed rewrite |
| **7.4.3** | **✅ SHIPPED 2026-04-26** | Doc + memory sync for 7.4.2 |
| **7.4.4** | **✅ SHIPPED 2026-04-26** | DEMO7 fixes (mine): `spawn_child_agent` MCP wrapper now exposes `transfer_catalog_ids`; Phase 7.3 Python self-loop guards dropped from `create_edge` / `upsert_edge_outbound` / `bind_edge`; `vector_search` lean projection (no embedding/blobs in result rows). |
| **7.4.5** | **✅ SHIPPED 2026-04-26** | DEMO7 fixes (pre-existing): `get_my_catalogs` (and 3 sibling tools) switched JOIN→EXISTS to dedupe post-merge survivor; `get_action_items_summary` reshape to uniform `dict[str, int]` (proxied list moved to detail); SME prompt hygiene-cycle stale `upsert_edge_catalog` references replaced. |
| **7.4.6** | **✅ SHIPPED 2026-04-26** | Doc + memory sync for 7.4.4 + 7.4.5 |
| **7.4.7** | **✅ SHIPPED 2026-04-27** | Per-type model + reasoning effort (`AgentTypeConfig.model` + `.effort`); admin UI graph node planes sourced from RCA→resources (was attributions.plane — wrong because attribution.plane is DISCOVERY plane); `/api/agents` returns `resource_planes[]`; agent-row plane symbols (G/C/T/D/F); §2.8 mass prompt infusion across all 5 .py prompts (ATTRIBUTION-vs-EDGE, attribution uniqueness, plane=DISCOVERY, grep catalogs, DANGLING-EDGE-pair rule, STEP 4 pseudocode, MATERIALISATION COMPLETION CHECKLIST, code-repo CLONE-MANDATORY, workspace-as-memory, EXTERNAL MCP ONBOARDING, BULK MCP TACTIC, SLEEP rewrite, ACCESS PRECHECK, SEND_BROADCAST ACL, CREDENTIALS via put_secret, CHAT-ADDRESSED-TO-YOU, ONGOING consolidation, WAKE BUDGET) |
| **7.4.8** | **✅ SHIPPED 2026-04-27** | WebGL GPU memory leak fix (geometry/material caches in `app.js`, `webglcontextlost`/`restored` handlers); new `POST /api/clientlog` endpoint appending to `/tmp/cartograph-logs/browser.log` (captures `window.error`, `unhandledrejection`, `webglcontextlost`, `beforeunload`); all four cartograph daemons now run under the active Claude Code session with stdout piped to `/tmp/cartograph-logs/{mcp,triggers,agents,admin_ui}.log` via `python -u -m <module>`; cache-bust v=58→v=60 |
| **7.4.9** | **✅ SHIPPED 2026-04-27** | Communications tab — surface decommissioned agents (parallel `_allAgentsById` cache), plane-symbol pills (G/C/T/D/F) on rows, new "Component name" filter (`participant_component=` query param). |
| **7.4.10** | **✅ SHIPPED 2026-04-27** | Graph crash root-cause fix v2: `pauseAnimation()`/`resumeAnimation()` on tab switch (offscreen RAF was the actual leak), `visibilitychange` listener for backgrounded browser tabs, wheel-handler null-deref fix (`!graphInstance` short-circuit), idempotent `webglcontextlost` handler. Cache-bust v=60→v=62. |
| **7.4.11** | **✅ SHIPPED 2026-04-27** (commits `72b4a93`, `84b0941`, `bc69c23`) | New `delete_edge(agent_id, edge_id)` MCP tool — owner-scoped, idempotent, cascades flows. Tool count 85 → 86. SME prompt gains IDENTIFIER NORMALISATION rule (STEP 3) + post-merge EDGE DEDUP step (in code-repo block). PROMPT-ENHANCEMENTS §2.9 logs the diagnostic context. |
| **parallel** | **✅ SHIPPED 2026-04-27** (commits `5b3144d`, `c7f5fd5`) | Removed "Call tools sequentially" rule from orchestrator + sme prompts; new `== BATCH + PARALLEL TOOL CALLS ==` block in `base.py` MISSION_AND_VOCABULARY shared with all 4 agent types. Concrete WHEN-TO-PARALLELISE / WHEN-TO-STAY-SEQUENTIAL / USE-BULK-VARIANTS guidance. Targets the 0.08% parallel-tool-call rate measured pre-fix. |
| **token-opt plan** | **DOCUMENTED, ROUND 1+ in flight** | Token optimisation plan in IMPLEMENTATION-PHASES.md §Token Optimisation: Round 1 (concise output rule + orch→sonnet — resolver STAYS opus-4-6), Round 2 (bulk MCP tools + BULK MCP TACTIC concrete examples + pre-injected action items), Round 3 (wake debouncing 5min). Target: ~43% reduction in lifetime spend. |

---

## 4. Phase 7 — what JUST shipped (4 sub-phases)

### 7.1 Terminal-state ack model — REPLACES Phase 5.5 auto-ack

- **New table** `terminal_acks(entity_type, entity_id, agent_id, acked_at)` PK on first 3.
- **New MCP tool** `ack_terminal(agent_id, entity_type, entity_id)` — validates participant, terminal status, idempotent.
- **Trigger scanner** `scan_terminal_pending_ack` re-wakes participants of closed task TC / consolidation D/F / clarification CC/QR until they ack.
- **action_items_summary** + **detail** now include `terminal_pending_ack` bucket.
- **Phase 5.5 reverted** — `tools/{tasks,consolidation,clarification}.py` no longer pre-stamp `acked_at = now()` on terminal announcement comm rows.
- **`absorb_agent`** bulk-acks on behalf of decommissioned target via `terminal_acks_tool.auto_ack_for_decommission(target_id)` (~5 lines).
- **Agent prompts** — shared `MISSION_AND_VOCABULARY` block in `base.py` gains a `== TERMINAL-STATE ACK ==` section.
- **Tests** — 16 new in `tests/mcp_tools/test_terminal_acks.py`. Old `test_terminal_auto_ack.py` deleted (its assertions were inverted by the revert).

### 7.2 Pre-merge handoff convention

- **No new tools, no schema** — pure SME prompt addition.
- Mutation POC must `create_clarification` to the absorption target asking for runtime nuances/configs NOT captured in `component_doc_md / source_slice / attributions / edges / flows`. WAIT for QC. Capture into A's own `component_doc_md` via `upsert_component`. THEN `absorb_agent`.
- 30-min timeout — escalate to admin via chat if target unresponsive.

### 7.3 Self-loop CHECK relaxation

- **Two CHECK constraints dropped** from `edges` table:
  - `edges_check` (from original CREATE TABLE — hard `from <> to`)
  - `edges_no_self_loop_v2` (from Phase 3.9 — allowed if either side null)
- **Migration is idempotent** — replaced the Phase 3.9 ADD block with a DROP block in the same place in `_migrate_edges_asymmetric`, so re-running migrations doesn't re-add.
- Self-loops now permitted: legitimate cron self-trigger / recursive component-level calls / service publish+consume on same topic.
- **Bundling unchanged** — self-loop joins existing junction by `(target, edge_type, identifier)` key.
- **Globe view** would need a tangent-plane self-loop fallback (great-circle slerp degenerates) — but Globe is on the experimental branch; flagged in `GLOBE-MERGE-BRIEF.md`.
- **Tests** — 3 new in `tests/mcp_tools/test_self_loops.py`.

### 7.4 Catalogs as first-class table

This was the largest sub-phase.

- **New table** `catalogs(id, component_id, kind, identifier, metadata, confidence, embedding(1024), discovered_by, created_at, updated_at)` UNIQUE on `(component_id, kind, identifier)` + 4 indexes.
- **`kind` enum** is **noun form** (replaces verb-form `edge_type` for catalogs):
  - `endpoint`       (was edge_type='calls')
  - `topic`          (was 'publishes_to')
  - `queue`          (was 'consumes_from')
  - `data_source`    (was 'reads_from' / 'writes_to')
  - `trigger_target` (was 'triggers')
- **Migration** moved existing `from_component_id IS NULL` rows out of `edges` into `catalogs` with `edge_type → kind` mapping. Idempotent (ON CONFLICT DO NOTHING + DELETE-after-insert).
- **5 new MCP tools** in `cartograph_mcp/tools/catalogs.py`:
  - `upsert_catalog(agent_id, component_id, kind, identifier, metadata?, confidence?)` — owner-only declaration. Embeds at write time. Idempotent on PK with metadata-merge + confidence-max.
  - `get_my_catalogs(agent_id)` — catalogs for components I own + `caller_count` per row.
  - `get_my_catalog_callers(agent_id, catalog_id?)` — bound callers matched via kind ↔ edge_type bridging map.
  - `get_unmatched_callers(agent_id)` — bound edges INTO my components with no matching catalog row. SME triages (dynamic / missing-catalog / caller-error).
  - `get_orphan_catalogs(agent_id)` — catalogs I own with no bound callers.
- **Bridging map** (`_KIND_TO_EDGE_TYPES` in `tools/catalogs.py`):
  - endpoint ↔ {calls}
  - topic ↔ {publishes_to, consumes_from}
  - queue ↔ {publishes_to, consumes_from}
  - data_source ↔ {reads_from, writes_to}
  - trigger_target ↔ {triggers}
- **Backwards-compat** `upsert_edge_catalog` (Phase 3.9) kept as a thin wrapper that translates `edge_type → kind` and forwards. Test `test_upsert_edge_catalog_shim_forwards` confirms.
- **`get_component_edges`** reads catalog bucket from new table, reshapes into legacy edge-row format with derived edge_type so FE consumers (Graph + Catalog tabs) work unchanged.
- **`/api/graph`** UNIONs catalogs into the edges payload with `kind='catalog'` + derived edge_type.
- **SME prompt** `sme.py` STEP 2b rewritten for noun-form catalog declarations + new STEP 2c hygiene cycle using `get_unmatched_callers` triage flow.
- **Tests** — 14 new in `tests/mcp_tools/test_catalogs.py`. 3 Phase 3.9 tests updated. 1 graph endpoint test updated.

### 7.5 Doc + memory sync

Updates to: `IMPLEMENTATION-PHASES.md`, `HLD.md`, `SCHEMA.md`, `TRIGGER-MANAGEMENT.md`, `AGENT-PROMPTS.md`, memory file. ONE-PAGER.md unchanged.

---

## 5. Tool surface (85 total)

Grouped by category. New tools from Phase 7 marked **[7.1]** / **[7.4]**.

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
components (16):      upsert_component, upsert_attribution, create_edge (3.9 shim),
                      insert_unresolved, resolve_reference, upsert_edge_catalog (7.4 wrapper),
                      upsert_edge_outbound, bind_edge, upsert_flow,
                      get_component, get_attributions, get_edges,
                      get_unresolved, get_component_edges,
                      get_flow, get_flow_inverse
notifications (1):    get_agent_notifications
consolidation (5):    nominate_consolidation, respond_consolidation, review_consolidation,
                      get_my_consolidations, get_consolidation_thread
clarification (4):    create_clarification, respond_clarification,
                      get_my_clarifications, get_clarification_thread
search (1):           vector_search   (Phase 7.4 follow-up: catalogs added — see §8)
mutation (10 — 4+4.1+4.2): execute_mutation, complete_consolidation, absorb_agent,
                      spawn_child_agent, transfer_attributions, transfer_edges,
                      transfer_flows, get_my_components, get_stale_edges, get_stale_flows
proxy (2):            get_my_proxy_items, act_on_proxy_item
insights (1):         record_insight   [Phase 5.9]
terminal_acks (1):    ack_terminal     [Phase 7.1]
catalogs (5):         upsert_catalog, get_my_catalogs, get_my_catalog_callers,
                      get_unmatched_callers, get_orphan_catalogs   [Phase 7.4]

Plus auto-wrapped: every @mcp.tool() registration is wrapped by
cartograph_mcp.audit.audited (Phase 5.10) — records to mcp_audit table.
```

**Total: 86** (added `delete_edge` in Phase 7.4.11). Verify with `grep "tools registered" /tmp/cartograph-logs/mcp.log | tail -1`.

---

## 6. Schema state

15 tables now (added 2 in Phase 7):
- `components`, `attributions`, `edges`, `flows`, `unresolved`
- `agent_runs`, `resources`, `resource_component_agents`, `tasks`, `secrets`
- `consolidations`, `clarifications`, `communications`, `broadcast_acks`
- `proxy_audit` (Phase 4)
- `agent_insights` (Phase 5.9), `mcp_audit` (Phase 5.10)
- **`terminal_acks` (Phase 7.1)** ← NEW
- **`catalogs` (Phase 7.4)** ← NEW

Total = 18 (proxy_items still exists from Phase 0 even though Phase 4 doesn't use it).

**CHECK constraint changes (Phase 7.3):**
- DROPPED: `edges_check`, `edges_no_self_loop_v2`
- KEPT: `edges_at_least_one_endpoint`, partial unique indexes still enforce dedup

---

## 7. Embedding state — what IS embedded, what ISN'T

| Table | Embedded? | Embed text |
|---|---|---|
| components | ✅ | `"{type}: {canonical_name} {display_name} {metadata}"` |
| attributions | ✅ | `"{resource_type}: {identifier}"` |
| edges | ✅ | `"{edge_type}: {identifier}"` |
| unresolved | ✅ | `"{reference_type}: {reference_value}"` |
| **catalogs** | ✅ | `"{kind}: {identifier}"` ← Phase 7.4 |
| flows | ❌ | (no semantic-search use case yet) |

All 5 embedded tables use `mxbai-embed-large` via local Ollama (1024d, vector_cosine_ops HNSW).

---

## 8. Vector search — catalogs INCLUDED (Phase 7.4 follow-up)

- ✅ Catalog rows ARE embedded at write time (`upsert_catalog` calls `_emb.embed_text(f"{kind}: {identifier}")`)
- ✅ Catalogs ARE readable by all agents via `get_component_edges` (incoming_catalog bucket sourced from new table)
- ✅ Owner-side reads via `get_my_catalogs`, `get_my_catalog_callers`, `get_unmatched_callers`, `get_orphan_catalogs`
- ✅ **`vector_search` extended** in the Phase 7.4 follow-up — `'catalogs'` is now a valid `table` argument. Lets SMEs do "find an endpoint similar to /payments/charge" across the org without walking get_component_edges per component.

Valid `vector_search` tables: `components`, `attributions`, `unresolved`, `edges`, `catalogs`.

---

## 9. Critical invariants (DON'T violate)

From the user across many sessions:

1. **1 SME = 1 active component.** SMEs write exactly one `upsert_component` per materialisation; splits go through Consolidation, not direct creation.
2. **Edges (Phase 3.9):** asymmetric. Bound = both set (caller owns). Dangling = to NULL (caller owns). **Self-loops (from = to) accepted as of Phase 7.3.**
3. **Catalogs (Phase 7.4):** OWN TABLE now, not edges. Owner-only declarations. Noun-form `kind`.
4. **Flows reference catalog rows via `incoming_catalog_id` (Phase 7.4.2 — first-class FK to `catalogs`).** Outgoing is still an edge id. Bound caller edges bridge to the catalog via `(target, edge_type, identifier)` — for rendering / hygiene only, NOT the flow anchor. **No catalog → no flow.**
5. **Junctions are routing scaffolding, NEVER real components in any logic/algo.**
6. **Terminal ack:** every participant of a closed entity must explicitly `ack_terminal` — else trigger scanner re-wakes them. Replaces Phase 5.5 silent auto-ack.
7. **Pre-merge handoff:** mutation POC MUST raise clarification to target before `absorb_agent` (convention, enforced via prompt).
8. **No session reset needed for prompt changes** — `agent_manager.py:190` passes `--system-prompt` fresh on every `--resume`.
9. **Mutation cascades (Phase 7.4.2):** `absorb_agent` runs an unconditional **catalog cascade** BEFORE the flow cascade (so flow.incoming_catalog_id refs land on survivor's catalogs). `spawn_child_agent` accepts `transfer_catalog_ids` for split (MCP wrapper plumbed in 7.4.4).
10. **Self-loops permitted (Phase 7.3 + 7.4.4):** `from_component_id = to_component_id` is fine on every write path. DB CHECK constraints + Python guards both gone.
11. **`vector_search` returns lean rows (Phase 7.4.4):** no embedding vectors, no doc/slice/metadata blobs in results — search-then-fetch via `get_*(id)` for full detail.
12. **`get_action_items_summary` is uniform `dict[str, int]` (Phase 7.4.5):** rich per-proxy breakdown lives on `get_action_items_detail.proxied`. Pre-7.4.5 the mixed-type response crashed MCP-client pydantic on every wake.
13. **Component planes come from RCA→resources, not attributions (Phase 7.4.7):** what plane a component LIVES on is the plane(s) of its source resource(s). `attributions.plane` is the DISCOVERY plane (where the SME found the evidence) and conflates source-of-evidence with category-of-component. Three admin-UI SQL callsites flipped (`/api/components`, `/api/component/{id}/drilldown`, `/api/graph`); plane filter EXISTS sub-query also flipped.
14. **Per-type model + effort (Phase 7.4.7):** orchestrator + resolver = `claude-opus-4-6 + medium`; iterator + sme = `claude-sonnet-4-6` (default effort). `agent_manager.py` cmd list appends `--model` + `--effort` from `AgentTypeConfig`. System prompt rebuilt fresh per spawn — no agent_manager restart needed when prompts change.
15. **Sleep is LAST RESORT, not default (Phase 7.4.7):** yielding doesn't burn cost. Sleep ONLY when blocked on admin/external + already prompted twice; 300–600s MAX, never 24h, never >3600s. Codified in SME / iterator / orchestrator prompts.
16. **WebGL leak fix (Phase 7.4.8 + 7.4.10):** real root cause was offscreen RAF — 3d-force-graph kept rendering even when Graph view hidden via display:none. Fixed via `pauseAnimation()`/`resumeAnimation()` on tab switch + `visibilitychange` listener. Per-cache disposal + idempotent context-lost handler complete the fix. All browser-side errors → `/tmp/cartograph-logs/browser.log` via the new `POST /api/clientlog` endpoint.
17. **Identifier normalisation across planes (Phase 7.4.11):** the holistic-edge invariant requires byte-identical identifier across SMEs from different planes. SME prompt now mandates: drop `/dbname` suffix from JDBC URLs, lowercase HTTP hosts, drop trailing slashes / query strings, templatise path params (`/users/{{id}}`). Tiebreak rule: write the LEANER form (what telemetry surfaces), put richer details in metadata. Post-merge EDGE DEDUP step uses the new `delete_edge` tool to collapse duplicate same-target rows.
18. **Parallel tool calls (commit `5b3144d`):** Anthropic's tool-use API supports multiple `tool_use` blocks per assistant turn → all dispatched concurrently → all results bundle into ONE next user turn → ONE LLM round-trip ingests them. Pre-fix only 0.08% of our messages emitted >1 tool_use because of the "Call tools sequentially" rule that's now removed. Theoretical 30-50% round-trip reduction on tool-heavy phases. Subprocess (claude -p) supports it natively; not configurable, just a model-decision-per-turn knob.

---

## 10. User collaboration style (from memory + experience)

- Crisp responses preferred. No fluff, no apology loops. Honest pushback welcomed.
- Incremental commits, push after each cohesive change.
- Daemon restart silently after code changes — never ask.
- TDD when implementing — tests first, run red, then make green.
- Convention prefixes (`[DEMO7-XX]`) for cross-session reporting.
- When user says "go go go" or "do it" — they mean execute now, don't re-confirm.
- Don't ever recreate Globe (Phase 6) on main — merge from `feat/globe-experimental` branch when ready.

---

## 10b. Live-state housekeeping (2026-04-26)

- **DEMO41 universal-preamble broadcast retired** — toggled `is_persistent=false` via the Communications-tab pin button (or `POST /api/broadcast/<id>/persistence`). Newly-spawned SMEs no longer inherit the DEMO41 metadata-stamp directive. Existing acks intact (no retroactive churn). DEMO7 broadcasts remain the only persistent universal preamble.

---

## 11. What we discussed but didn't ship (open / parked)

| Item | Status |
|---|---|
| **Globe (Phase 6)** | Parked on `feat/globe-experimental`. Includes `docs/GLOBE-MERGE-BRIEF.md`. Don't recreate; merge that branch. |
| **Vector search extended for catalogs** | Mentioned in §8 — small follow-up if needed. |
| **DEMO7 verification dance** | Test prompt designed (in last conversation) — paste to orchestrator to drive. Reports tagged `[DEMO7-*]` for easy grep. |
| **Phase 8: phase-flow completion** | Future — Resolution / Edge Discovery / User Feedback orchestrator-driven sweeps. |
| **Phase 9-11: HLD §11 future scope** | Observability dashboard, cost controls, DM between agents, knowledge pool. |
| **DEMO41 metadata purge** | Awaiting explicit user say-so. `DELETE FROM consolidations WHERE metadata->>'demo41'='true'` etc. |
| **AGENT-PROMPTS.md §9 deferred** | Evidence quality guidelines + per-type negative instruction sets. |
| **Communications-tab via-badge fix on broadcast rows** | PARKED — do NOT touch without explicit say-so. |

---

## 12. DEMO7 verification prompt (paste to orchestrator)

A comprehensive 11-phase test was designed at the END of the conversation just before compaction. The full prompt is at the bottom of the previous chat history. Key shape:

- Spawn 4 SMEs: `sme-auth-1`, `sme-auth-2` (duplicate), `sme-monolith` (will split), `sme-cron` (self-loop).
- 11 phases covering: catalogs declare, unmatched-callers triage, self-loop, merge nominate + auto-escalate, pre-merge handoff via clarification, absorb + execute_mutation + complete_consolidation, terminal acks (sme-auth-1 + resolver), split + spawn_child + transfers, proxy inheritance + act_on_proxy_item, record_insight calls, persistence toggle, mcp_audit spot-check.
- Final scorecard tagged `[DEMO7-RESULT]` lines.
- User pastes back all `[DEMO7-*]` lines for analysis.

**If user wants the full prompt re-emitted post-compaction:** they can ask me to regenerate it using the §4 Phase 7 + §5 tool surface as guidance.

---

## 13. Re-hydration checklist (do in order post-compaction)

1. **`git log --oneline -50 | head`** — see most recent commits.
2. **`grep "tools registered" /tmp/cartograph-logs/mcp.log | tail -1`** — should show `85 tools`. Note path change: logs now live in `/tmp/cartograph-logs/` (Phase 7.4.8), not `/tmp/cartograph_mcp.log`.
3. **Read this doc** (§4 + §5 + §8 + §9 are the most critical).
4. **Read the 7 docs** (HLD / SCHEMA / TRIGGER-MANAGEMENT / AGENT-PROMPTS / IMPLEMENTATION-PHASES / ONE-PAGER / **PROMPT-ENHANCEMENTS**) — PROMPT-ENHANCEMENTS now carries the active prompt-quality backlog (§2 shipped, §3 open gaps, §4 operational nudges + broadcast log + chat log + insights triage).
5. **Check the 3 memory files** at `~/.claude/projects/-Users-venkata-manohar-release-agent-docs-service-dependency/memory/` (auto-loaded).
6. **Verify daemons running:** `ps aux | grep -E "(admin_ui|cartograph_mcp|trigger_management|python.*-m main)" | grep -v grep | grep -v agent-battles`. Should be 4 cartograph processes.
7. **`tail -f /tmp/cartograph-logs/admin_ui.log`** for live admin-UI request stream; **`grep webglcontextlost /tmp/cartograph-logs/browser.log`** for any in-flight GPU crashes.

If user asks "what's left" — the answer is in §11. If user asks "what's the current ship state" — §3.

---

## 14. Critical don'ts (from prior sessions, re-instated)

1. **Do NOT** fix the broadcast via-badge display on Communications tab. Parked.
2. **Do NOT** decommission agents as a cleanup shortcut.
3. **Do NOT** run the demo41 metadata purge without explicit user say-so.
4. **Do NOT** send admin chats unsolicited; admin is the user.
5. **Do NOT** recommend session resets for agent prompt changes — `--system-prompt` hot-loads.
6. **Do NOT** use chat to communicate agent→agent (`send_chat` refuses non-admin senders to non-admin recipients). Orch uses tasks/broadcasts.
7. **Do NOT** recreate Globe on main — merge from `feat/globe-experimental` instead.
8. **Do NOT** duplicate comm rows for terminal-state announcements — use `terminal_acks` junction now.

---

End of recollection. Feed this + the 6 docs + memory file (auto-loaded) and I'll be caught up.

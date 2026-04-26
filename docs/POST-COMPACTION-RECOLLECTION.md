# Cartograph — Post-Compaction Recollection (2026-04-26, updated for 7.4.2)

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

## 2. Running daemons (status as of last restart at 11:15)

| Daemon | Port | Status |
|---|---|---|
| Postgres (docker) | 5432 | running |
| Ollama (mxbai-embed-large 1024d) | 11434 | running |
| MCP server | 8100 | **85 tools registered** |
| Admin UI (FastAPI) | 8200 | running |
| Trigger manager | — | running |

**Restart sequence** (from `cd src/`):
```bash
ps aux | grep -E "cartograph_mcp|admin_ui|trigger_management" | grep -v grep | awk '{print $2}' | xargs kill 2>/dev/null
sleep 2
nohup python3.10 -m cartograph_mcp.server > /tmp/cartograph_mcp.log 2>&1 & disown
nohup python3.10 -m admin_ui.server > /tmp/admin_ui.log 2>&1 & disown
nohup python3.10 -m trigger_management.main > /tmp/trigger_mgr.log 2>&1 & disown
sleep 5
grep "tools registered" /tmp/cartograph_mcp.log | tail -1   # expect: 85 tools
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
| **7.4.3** | **✅ SHIPPED 2026-04-26** | Doc + memory sync across 6 docs |

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

**Total: 85.** Verify with `grep "tools registered" /tmp/cartograph_mcp.log | tail -1`.

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
9. **Mutation cascades (Phase 7.4.2):** `absorb_agent` runs an unconditional **catalog cascade** BEFORE the flow cascade (so flow.incoming_catalog_id refs land on survivor's catalogs). `spawn_child_agent` accepts `transfer_catalog_ids` for split.

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

1. **`git log --oneline -50 | head`** — confirm `aadcadb` is HEAD on `feat/trigger-manager-cartograh-mcp`
2. **`grep "tools registered" /tmp/cartograph_mcp.log | tail -1`** — should show `85 tools`
3. **Read this doc** (§4 + §5 + §8 + §9 are the most critical)
4. **Read the 6 docs** (HLD / SCHEMA / TRIGGER-MANAGEMENT / AGENT-PROMPTS / IMPLEMENTATION-PHASES / ONE-PAGER) — user will paste these
5. **Check the 3 memory files** at `~/.claude/projects/-Users-venkata-manohar-release-agent-docs-service-dependency/memory/` (auto-loaded)
6. **Verify daemons running:** `ps aux | grep -E "cartograph_mcp|admin_ui|trigger_management" | grep -v grep`

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

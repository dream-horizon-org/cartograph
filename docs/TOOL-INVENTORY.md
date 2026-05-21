# Cartograph — MCP Tool Inventory

> **Source of truth:** `src/cartograph_mcp/server.py` (`@mcp.tool` registrations) +
> per-tool modules in `src/cartograph_mcp/tools/`. This doc tracks intent + scope
> for cross-reference; code is authoritative if drift suspected.
>
> **Verify count:** `grep "tools registered" /tmp/cartograph-logs/mcp.log | tail -1`
> → expect `117 tools registered`.

**Total: 117 tools.**

---

## Legend


| Symbol    | Meaning                                                                            |
| --------- | ---------------------------------------------------------------------------------- |
| ✓         | Allowed, no extra gating                                                           |
| ✓ *scope* | Allowed but scoped (own component / own plane / participant / mutation POC / etc.) |
| —         | Not allowed                                                                        |
| *admin*   | Also callable by the admin agent_id via the admin UI                               |


Agent columns: **Orch** (orchestrator) · **Iter** (iterator) · **SME** · **Res** (resolver).

> `agent_id` is validated against `agent_runs` on every call via `shared.actor_auth.require_active_agent`; decommissioned agents are refused everywhere except via the `act_on_proxy_item` ContextVar bypass.

> Every tool is wrapped by `cartograph_mcp.audit.audited` → one `mcp_audit` row per call (Phase 5.10).

---

## 1. Action Items (2)


| Tool                                 | Orch | Iter | SME | Res | Description / Scope                                                                                                                                                                                 |
| ------------------------------------ | ---- | ---- | --- | --- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `get_action_items_summary(agent_id)` | ✓    | ✓    | ✓   | ✓   | Uniform `dict[str, int]` counts: `consolidations_pending`, `tasks_pending`, `clarifications_pending`, `unacked_chats`, `unacked_broadcasts`, `terminal_pending_ack`, `proxied_count` (Phase 7.4.5). |
| `get_action_items_detail(agent_id)`  | ✓    | ✓    | ✓   | ✓   | Full rows for every pending item. Rich per-proxy breakdown lives here under `.proxied`.                                                                                                             |


---

## 2. Chat (4)


| Tool                                             | Orch | Iter             | SME              | Res              | Description / Scope                                                                                                                                |
| ------------------------------------------------ | ---- | ---------------- | ---------------- | ---------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| `send_chat(from_agent_id, to_agent_id, message)` | ✓    | ✓ *→ admin only* | ✓ *→ admin only* | ✓ *→ admin only* | Non-admin agents can only message `admin`; admin can message anyone. Admin chat to a sleeping agent auto-wakes (clears `sleep_until`). Also admin. |
| `ack_chats(agent_id, communication_ids[])`       | ✓    | ✓                | ✓                | ✓                | Selectively ack rows where `to_agent = agent_id`.                                                                                                  |
| `get_unacked_chats(agent_id)`                    | ✓    | ✓                | ✓                | ✓                | Own inbox.                                                                                                                                         |
| `get_chat_history(agent_id, page, limit)`        | ✓    | ✓                | ✓                | ✓                | Own paginated history.                                                                                                                             |


---

## 3. Broadcast (4)


| Tool                                                                      | Orch | Iter | SME | Res | Description / Scope                                                                                                                                                            |
| ------------------------------------------------------------------------- | ---- | ---- | --- | --- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `send_broadcast(from_agent_id, to_agent_type, message, persistent=False)` | ✓    | —    | —   | —   | Orchestrator-only among agents (admin also). `persistent=True` → applies to agents spawned later too. Iterators/SMEs/Res get `Only orchestrator or admin can send broadcasts`. |
| `ack_broadcast(agent_id, communication_id)`                               | ✓    | ✓    | ✓   | ✓   | Per-agent ack row in `broadcast_acks`.                                                                                                                                         |
| `get_unacked_broadcasts(agent_id, agent_type)`                            | ✓    | ✓    | ✓   | ✓   | Own inbox by type. Skips pre-spawn non-persistent broadcasts. Phase 10.8.2 adds `require_active_agent` gate (refuses decom callers).                                           |
| `update_broadcast_persistence(agent_id, communication_id, persistent)`    | ✓    | —    | —   | —   | Also admin. Flips `is_persistent` on an existing broadcast. Refuses non-broadcast rows. Phase 5.7.                                                                             |


---

## 4. Secrets (4)


| Tool                                      | Orch | Iter | SME | Res | Description / Scope                                      |
| ----------------------------------------- | ---- | ---- | --- | --- | -------------------------------------------------------- |
| `put_secret(agent_id, plane, key, value)` | ✓    | —    | —   | —   | Orchestrator-only writes. Upserts on `(plane, key)`.     |
| `delete_secret(agent_id, plane, key)`     | ✓    | —    | —   | —   | Orchestrator-only.                                       |
| `get_secret(agent_id, plane, key)`        | ✓    | ✓    | ✓   | ✓   | Iterators typically read their own plane; no hard scope. |
| `list_secrets_for_plane(agent_id, plane)` | ✓    | ✓    | ✓   | ✓   | Returns keys only (no values).                           |


---

## 5. Tasks (5)


| Tool                                                                    | Orch | Iter | SME | Res | Description / Scope                                                                                          |
| ----------------------------------------------------------------------- | ---- | ---- | --- | --- | ------------------------------------------------------------------------------------------------------------ |
| `create_task(owner_agent_id, worker_agent_id, description)`             | ✓    | —    | —   | —   | Also admin. Creates BW status + initial comm.                                                                |
| `respond_task(agent_id, task_id, message, new_status, blocker_detail?)` | ✓    | ✓    | ✓   | ✓   | Participants only (owner or worker). State-machine validated: worker {BW→BO/WD}; owner {BO→BW/TC, WD→BW/TC}. |
| `raise_blocker(agent_id, task_id, blocker_detail)`                      | —    | ✓    | ✓   | ✓   | Worker-only shortcut BW→BO.                                                                                  |
| `get_my_tasks(agent_id)`                                                | ✓    | ✓    | ✓   | ✓   | Owner- or worker-scoped.                                                                                     |
| `get_task_thread(task_id)`                                              | ✓    | ✓    | ✓   | ✓   | Participants only.                                                                                           |


---

## 6. Resources (9)


| Tool                                                                          | Orch      | Iter          | SME          | Res | Description / Scope                                                            |
| ----------------------------------------------------------------------------- | --------- | ------------- | ------------ | --- | ------------------------------------------------------------------------------ |
| `upsert_resource(agent_id, plane, type, identifier, access_desc?, metadata?)` | —         | ✓ *own plane* | —            | —   | Iterator plane must match. Idempotent on `(plane, resource_type, identifier)`. |
| `upsert_resources_bulk(agent_id, plane, items[])`                             | —         | ✓ *own plane* | —            | —   | Up to 5000 rows; one transaction.                                              |
| `reject_resource(agent_id, resource_id, reason, force=False)`                 | ✓ *force* | ✓ *own plane* | —            | —   | Soft-delete with audit trail.                                                  |
| `reject_resources_bulk(agent_id, plane, ids?/types?, reason, force=False)`    | ✓ *force* | ✓ *own plane* | —            | —   | Plane-scoped; refuses blank-wipe; cascade-safe.                                |
| `mark_resource_done(agent_id, resource_id)`                                   | —         | —             | ✓ *assigned* | —   | SME must be linked via RCA. Idempotent.                                        |
| `get_resource(agent_id, resource_id)`                                         | ✓         | ✓             | ✓            | ✓   | Single row.                                                                    |
| `list_resources_for_plane(agent_id, plane)`                                   | ✓         | ✓             | ✓            | ✓   | Resume-from-cursor friendly.                                                   |
| `list_all_resources(agent_id, status?)`                                       | ✓         | ✓             | ✓            | ✓   | Excludes `rejected` unless requested.                                          |
| `get_resource_counts(agent_id)`                                               | ✓         | ✓             | ✓            | ✓   | Orchestrator gatekeeper query.                                                 |


---

## 7. Agent Lifecycle (11)


| Tool                                                                                     | Orch | Iter | SME | Res | Description / Scope                                                                                                       |
| ---------------------------------------------------------------------------------------- | ---- | ---- | --- | --- | ------------------------------------------------------------------------------------------------------------------------- |
| `create_agent(agent_id, new_type, plane?, resource_id?)`                                 | ✓    | —    | —   | —   | Single-agent spawn. For SMEs, writes RCA reservation row (`component_id=NULL`) + flips resource to `assigned`.            |
| `bulk_spawn_smes(agent_id, plane, resource_ids?, all_pending?, task_description?)`       | ✓    | —    | —   | —   | Bulk SME spawn + optional one-task-per-SME. Refuses blank-wipe; skips already-assigned.                                   |
| `list_agents(agent_id)`                                                                  | ✓    | ✓    | ✓   | ✓   | All non-decommissioned.                                                                                                   |
| `reset_agent(agent_id, target_agent_id)`                                                 | ✓    | —    | —   | —   | Force-reset permanently-errored agent (bypasses 3-attempt recovery cap).                                                  |
| `decommission_agent(agent_id, target, reason, resource_action='leave'/'reset'/'reject')` | ✓    | —    | —   | —   | Self-decom refused. Cascade per `resource_action`.                                                                        |
| `decommission_agents_bulk(agent_id, reason, agent_ids?, agent_type?, resource_action?)`  | ✓    | —    | —   | —   | Cohort teardown. Refuses `agent_type='orchestrator'` and blank-wipe; skips caller.                                        |
| `decommission_component(agent_id, component_id, reason)`                                 | ✓    | —    | —   | —   | Soft-delete (`status='decommissioned'`).                                                                                  |
| `decommission_components_bulk(agent_id, component_ids[], reason)`                        | ✓    | —    | —   | —   | Explicit id list required; refuses blank-wipe.                                                                            |
| `sleep_self(agent_id, duration_seconds, reason)`                                         | ✓    | ✓    | ✓   | ✓   | Up to 7d schema cap; policy cap 300–600s. Admin chat / `bulk_wake_agents` clears. Broadcasts/tasks don't interrupt sleep. |
| `bulk_sleep_agents(agent_id, until, reason, agent_ids?, agent_type?)`                    | ✓    | —    | —   | —   | Also admin. Refuses `agent_type='orchestrator'`, never sleeps caller, ISO timestamp.                                      |
| `bulk_wake_agents(agent_id, agent_ids?, agent_type?)`                                    | ✓    | —    | —   | —   | Also admin. Clears `sleep_until`.                                                                                         |


---

## 8. Component Graph — Writes (10)


| Tool                                                               | Orch | Iter | SME                    | Res | Description / Scope                                                                                                                                                                                                                                                               |
| ------------------------------------------------------------------ | ---- | ---- | ---------------------- | --- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `upsert_component(agent_id, component_data)`                       | —    | —    | ✓ *one per SME*        | —   | First call fills RCA reservation. 1-active-component-per-SME enforced (partial UNIQUE on canonical_name where active, Phase 10.8.1). Phase 10.7: separate `description` (≤400 chars, embed target) + `component_doc_md` (human-render, NOT embedded). REPLACE/COALESCE semantics. |
| `upsert_attribution(agent_id, component_id, data)`                 | —    | —    | ✓ *own component*      | —   | Idempotent on `(component_id, plane, resource_type, identifier)` (component-scoped post-10.13.6).                                                                                                                                                                                 |
| `upsert_attributions_bulk(agent_id, component_id, attributions[])` | —    | —    | ✓ *own component*      | —   | Atomic-with-pre-validation. Cross-component conflicts reject the whole batch. Max 500.                                                                                                                                                                                            |
| `create_edge(agent_id, edge_data)`                                 | —    | —    | ✓ *own source*         | —   | [Phase 3.9 legacy shim → `upsert_edge_outbound`]. Self-loops permitted (Phase 7.3).                                                                                                                                                                                               |
| `upsert_edge_catalog(agent_id, edge_data)`                         | —    | —    | ✓ *own to_component*   | —   | [Phase 7.4 deprecated shim → `upsert_catalog`].                                                                                                                                                                                                                                   |
| `upsert_edge_outbound(agent_id, edge_data)`                        | —    | —    | ✓ *own from_component* | —   | Caller's outgoing edge. `to_component_id` may be NULL (dangling). Idempotent w/ metadata + max-conf accumulation.                                                                                                                                                                 |
| `upsert_edges_outbound_bulk(agent_id, edges[])`                    | —    | —    | ✓ *owns each from*     | —   | Atomic bulk. Max 500. Mixed bound/dangling allowed.                                                                                                                                                                                                                               |
| `bind_edge(agent_id, edge_id, to_component_id)`                    | —    | —    | ✓ *owns from*          | —   | Resolves a dangling outgoing. Refuses collision with bound row.                                                                                                                                                                                                                   |
| `bind_edges_bulk(agent_id, bindings[])`                            | —    | —    | ✓                      | —   | **Phase 10.13.7.** Atomic-with-pre-validation bulk bind. Max 500.                                                                                                                                                                                                                 |
| `delete_edge(agent_id, edge_id)`                                   | —    | —    | ✓ *owns from*          | —   | **Phase 7.4.11.** Owner-scoped, idempotent. Cascades flows via FK. Catalog rows refuse (`catalog_not_supported`).                                                                                                                                                                 |


---

## 9. Component Graph — Deletes (5 + 4 bulks)

Owner-scoped, idempotent (`{deleted: False, reason: 'not_found'}` on missing id), optional `reason` param surfaced in `mcp_audit`. All Phase 8.2/8.3.


| Tool                                                    | Orch | Iter | SME                         | Res | Description / Scope                                        |
| ------------------------------------------------------- | ---- | ---- | --------------------------- | --- | ---------------------------------------------------------- |
| `delete_attribution(agent_id, attribution_id, reason?)` | —    | —    | ✓ *owns component*          | —   | Edges' `source_attr_id`/`target_attr_id` → SET NULL on FK. |
| `delete_catalog(agent_id, catalog_id, reason?)`         | —    | —    | ✓ *owns component*          | —   | `flows.incoming_catalog_id` cascade-deletes.               |
| `delete_flow(agent_id, flow_id, reason?)`               | —    | —    | ✓ *owns component*          | —   | Leaf — no cascade.                                         |
| `delete_unresolved(agent_id, unresolved_id, reason?)`   | —    | —    | ✓ *owns found_in_component* | —   | Leaf — no cascade.                                         |
| `delete_attributions_bulk(agent_id, attribution_ids[])` | —    | —    | ✓                           | —   | Lenient on missing-id; strict on owner-violation. Max 500. |
| `delete_catalogs_bulk(agent_id, catalog_ids[])`         | —    | —    | ✓                           | —   | Same semantics.                                            |
| `delete_flows_bulk(agent_id, flow_ids[])`               | —    | —    | ✓                           | —   | Same.                                                      |
| `delete_edges_bulk(agent_id, edge_ids[])`               | —    | —    | ✓                           | —   | Same.                                                      |
| `delete_unresolved_bulk(agent_id, unresolved_ids[])`    | —    | —    | ✓                           | —   | Same.                                                      |


---

## 10. Flows + Unresolved Refs (6)


| Tool                                                                              | Orch | Iter | SME               | Res | Description / Scope                                                                                                                                   |
| --------------------------------------------------------------------------------- | ---- | ---- | ----------------- | --- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| `upsert_flow(agent_id, component_id, incoming_catalog_id, outgoing_edge_id, ...)` | —    | —    | ✓ *own component* | —   | **Phase 7.4.2**: incoming is a `catalogs` row id, NEVER an edge id. Validates catalog + edge both belong to caller's component. No catalog → no flow. |
| `upsert_flows_bulk(agent_id, component_id, flows[])`                              | —    | —    | ✓                 | —   | **Phase 8.4.** Atomic bulk. Max 500.                                                                                                                  |
| `insert_unresolved(agent_id, data)`                                               | —    | —    | ✓ *own component* | —   | Idempotent ON CONFLICT on `(found_in_component_id, reference_type, reference_value)` post-Phase-8.4; bumps `attempts` on re-call.                     |
| `insert_unresolved_bulk(agent_id, items[])`                                       | —    | —    | ✓                 | —   | **Phase 8.4.** Atomic bulk. Max 500.                                                                                                                  |
| `resolve_reference(agent_id, unresolved_id, target_component_id)`                 | ✓    | ✓    | ✓                 | ✓   | Any active agent (cross-SME resolution). Refuses decommissioned target.                                                                               |
| `resolve_references_bulk(agent_id, items[])`                                      | —    | —    | ✓                 | —   | **Phase 10.13.7.** Atomic-with-pre-validation. Max 500.                                                                                               |


---

## 11. Component Graph — Reads (12)


| Tool                                                  | Orch | Iter | SME | Res | Description / Scope                                                                                                      |
| ----------------------------------------------------- | ---- | ---- | --- | --- | ------------------------------------------------------------------------------------------------------------------------ |
| `get_component(component_id)`                         | ✓    | ✓    | ✓   | ✓   | Strips 1024-d `embedding` from response (~8KB saved/call, Phase 10.7).                                                   |
| `get_components_bulk(agent_id, component_ids[])`      | ✓    | ✓    | ✓   | ✓   | Max 500. Same embedding strip.                                                                                           |
| `get_attributions(component_id)`                      | ✓    | ✓    | ✓   | ✓   | Param is `component_id`, NOT `id` (Phase 10.13.10 prompt fix flagged silent-empty bug).                                  |
| `get_attributions_bulk(agent_id, component_ids[])`    | ✓    | ✓    | ✓   | ✓   | Returns `dict[component_id_str, list[row]]`.                                                                             |
| `get_edges(component_id)`                             | ✓    | ✓    | ✓   | ✓   | [Phase 3.9 backward-compat] `{outbound, inbound}` bound-only. Prefer `get_component_edges`.                              |
| `get_component_edges(component_id)`                   | ✓    | ✓    | ✓   | ✓   | `{incoming_bound, incoming_catalog, outgoing_bound, outgoing_dangling}` (Phase 3.9).                                     |
| `get_component_edges_bulk(agent_id, component_ids[])` | ✓    | ✓    | ✓   | ✓   | Bulk variant.                                                                                                            |
| `get_flow(component_id, incoming_catalog_id)`         | ✓    | ✓    | ✓   | ✓   | Outgoing edges fired when this catalog is hit.                                                                           |
| `get_flow_inverse(component_id, outgoing_edge_id)`    | ✓    | ✓    | ✓   | ✓   | Catalog rows triggering this outgoing edge (returns `catalogs` rows, not `edges`).                                       |
| `get_flows_bulk(agent_id, component_ids[])`           | ✓    | ✓    | ✓   | ✓   | Bulk variant.                                                                                                            |
| `get_unresolved(component_id)`                        | ✓    | ✓    | ✓   | ✓   | Open + resolved rows by component.                                                                                       |
| `get_component_owner(agent_id, component_id)`         | ✓    | ✓    | ✓   | ✓   | **Phase 10.13.3.** Returns owning SME via RCA + decom chain. Call BEFORE creating clarification about another component. |


---

## 12. Catalogs (8)


| Tool                                                                               | Orch | Iter | SME               | Res | Description / Scope                                                                                            |
| ---------------------------------------------------------------------------------- | ---- | ---- | ----------------- | --- | -------------------------------------------------------------------------------------------------------------- |
| `upsert_catalog(agent_id, component_id, kind, identifier, metadata?, confidence?)` | —    | —    | ✓ *own component* | —   | **Phase 7.4** noun-form. `kind` ∈ {endpoint, topic, queue, data_source, trigger_target}. Auto-embeds.          |
| `upsert_catalogs_bulk(agent_id, component_id, catalogs[])`                         | —    | —    | ✓                 | —   | Atomic. Max 500.                                                                                               |
| `get_my_catalogs(agent_id)`                                                        | —    | —    | ✓                 | —   | Owned catalogs + `caller_count` per row. EXISTS-style dedup (Phase 7.4.5 fix post-multi-RCA).                  |
| `get_my_catalog_callers(agent_id, catalog_id?)`                                    | —    | —    | ✓                 | —   | Bound callers matched via `kind ↔ edge_type` bridging.                                                         |
| `get_catalogs_bulk(agent_id, component_ids[])`                                     | ✓    | ✓    | ✓                 | ✓   | Phase 8.5 bulk read.                                                                                           |
| `get_unmatched_callers(agent_id)`                                                  | —    | —    | ✓                 | —   | Bound edges INTO my components with no matching catalog row. Triage: dynamic / missing-catalog / caller-error. |
| `get_orphan_catalogs(agent_id)`                                                    | —    | —    | ✓                 | —   | Catalogs I own that no bound caller matches.                                                                   |


*(`upsert_edge_catalog` is the Phase 3.9 verb-form deprecated shim — listed in §8 above as a graph-write tool.)*

---

## 13. Stale Hygiene (2)


| Tool                        | Orch | Iter | SME | Res | Description / Scope                                                                                               |
| --------------------------- | ---- | ---- | --- | --- | ----------------------------------------------------------------------------------------------------------------- |
| `get_stale_edges(agent_id)` | —    | —    | ✓   | —   | Edges where the OTHER endpoint is decommissioned. Includes `merged_into_agent_id` pointer for re-bind.            |
| `get_stale_flows(agent_id)` | —    | —    | ✓   | —   | Phase 7.4.2: outgoing-side only (catalog incoming is same-component-owned, so it can't have a dead counterparty). |


---

## 14. Consolidation (5)


| Tool                                                                                          | Orch | Iter | SME             | Res                  | Description / Scope                                                                                                                                           |
| --------------------------------------------------------------------------------------------- | ---- | ---- | --------------- | -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `nominate_consolidation(agent_id, comp_a, comp_b?, type, confidence, message, metadata?)`     | —    | —    | ✓ *owns comp_a* | —                    | merge requires comp_b (different SME); split has no B2 state. Pre-INSERT SELECT + partial UNIQUE pair guard against symmetric duplicates (Phase 10.1/10.1.1). |
| `respond_consolidation(agent_id, cons_id, confidence, message, new_status)`                   | —    | —    | ✓ *participant* | —                    | State-machine validated per role. Stamps `metadata.confidence_at_send = {a,b,r}` on the comm row. Manual escalate to R requires `r_conf_score` already set.   |
| `review_consolidation(agent_id, cons_id, r_conf, message, new_status, mutation_assigned_to?)` | —    | —    | —               | ✓                    | R→{B1,B2,F,M}. M requires `mutation_assigned_to`; split enforces = agent_a; merge requires agent_a or agent_b.                                                |
| `get_my_consolidations(agent_id)`                                                             | —    | —    | ✓ *participant* | ✓ *all non-terminal* | Resolver sees all non-terminal rows.                                                                                                                          |
| `get_consolidation_thread(agent_id, cons_id, page, limit)`                                    | —    | —    | ✓ *participant* | ✓                    | Non-participants get `[]` (silent denial).                                                                                                                    |


---

## 15. Clarifications (4)


| Tool                                                            | Orch | Iter | SME | Res | Description / Scope                                                                                                                |
| --------------------------------------------------------------- | ---- | ---- | --- | --- | ---------------------------------------------------------------------------------------------------------------------------------- |
| `create_clarification(asker, responder, question)`              | ✓    | ✓    | ✓   | ✓   | Responder can be any agent or literal `'admin'`. Initial B2.                                                                       |
| `respond_clarification(agent_id, clar_id, message, new_status)` | ✓    | ✓    | ✓   | ✓   | State-machine validated per role (asker/responder). QR→CC transition is the ASKER's terminal close-out (Phase 10.13.2 prompt fix). |
| `get_my_clarifications(agent_id)`                               | ✓    | ✓    | ✓   | ✓   | Non-terminal where agent is asker or responder.                                                                                    |
| `get_clarification_thread(agent_id, clar_id, page, limit)`      | ✓    | ✓    | ✓   | ✓   | Scoped to asker + responder.                                                                                                       |


---

## 16. Mutation Lifecycle (5)

Gated: `consolidations.status='M' AND mutation_assigned_to = agent_id`.


| Tool                                                                                                                                                                                       | Orch | Iter | SME              | Res | Description / Scope                                                                                                                                                                                                                                                                                                                               |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ---- | ---- | ---------------- | --- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `execute_mutation(agent_id, cons_id, message)`                                                                                                                                             | —    | —    | ✓ *mutation POC* | —   | M→MD. **Phase 10.14.3:** refuses if `consolidations.cascade_completed_at IS NULL` (closes F3 silent-corruption — absorb/spawn must stamp first).                                                                                                                                                                                                  |
| `complete_consolidation(agent_id, cons_id, message)`                                                                                                                                       | —    | —    | —                | ✓   | MD→D. Resolver final verification.                                                                                                                                                                                                                                                                                                                |
| `absorb_agent(agent_id, cons_id, target_id, deactivation_reason?, deactivation_notes?)`                                                                                                    | —    | —    | ✓ *mutation POC* | —   | Merge. Decom target + union source_slice + re-point RCA. **Phase 10.14.4:** cascade unconditional; attribution cascade auto-dedups on `(plane, rt, id)` collision (keep survivor, merge metadata, MAX conf, drop target). Catalog cascade runs BEFORE flow cascade. Stamps `cascade_completed_at`. Legacy `cascade_`* flags removed → ValueError. |
| `spawn_child_agent(agent_id, cons_id, child_component_data, child_source_slice, split_briefing, transfer_edge_ids?, transfer_flow_ids?, transfer_attribution_ids?, transfer_catalog_ids?)` | —    | —    | ✓ *mutation POC* | —   | Split. **Phase 10.14.2:** server mints fresh `sme-<8hex>` (returned in response); caller passing `child_agent_id` → ValueError. Atomic carve-out + welcome BW task. Stamps `cascade_completed_at`.                                                                                                                                                |
| `transfer_attributions(agent_id, cons_id, ids[], from, to)`                                                                                                                                | —    | —    | ✓ *mutation POC* | —   | Mutation-scoped. Re-embeds both. Phase 10.14.4: per-row dedup loop replaces bulk UPDATE. Returns `{transferred, deduped}`.                                                                                                                                                                                                                        |
| `transfer_edges(agent_id, cons_id, edge_ids[], direction='both')`                                                                                                                          | —    | —    | ✓ *mutation POC* | —   | Phase 4.1. Catalog collision collapses; bound/dangling collision auto-dedups (Phase 10.13.8).                                                                                                                                                                                                                                                     |
| `transfer_flows(agent_id, cons_id, flow_ids[])`                                                                                                                                            | —    | —    | ✓ *mutation POC* | —   | Re-points `flows.component_id`. Triple-collision rejects.                                                                                                                                                                                                                                                                                         |


*(Mutation tools are 7 total; counted with proxy below.)*

---

## 17. Proxy Inheritance (2)


| Tool                                                                | Orch | Iter | SME | Res | Description / Scope                                                                                                                                                                                                                    |
| ------------------------------------------------------------------- | ---- | ---- | --- | --- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `get_my_proxy_items(agent_id, limit?, include_empty?)`              | —    | —    | ✓   | —   | Walks `merged_into_agent_id` chain; grouped by proxy agent. `include_empty=True` (Phase 4.2) surfaces full chain even when inboxes empty.                                                                                              |
| `act_on_proxy_item(survivor, item_type, item_id, action, payload?)` | —    | —    | ✓   | —   | Router. Sets `_PROXY_CTX` ContextVar, invokes existing public tool with actor=proxy_agent_id. Writes `proxy_audit`. Dispatch: `(task,respond) (clarification,respond) (consolidation,respond) (chat,ack) (chat,send) (broadcast,ack)`. |


---

## 18. SME Component View (1)


| Tool                          | Orch | Iter | SME | Res | Description / Scope                                                                                          |
| ----------------------------- | ---- | ---- | --- | --- | ------------------------------------------------------------------------------------------------------------ |
| `get_my_components(agent_id)` | —    | —    | ✓   | —   | Phase 4.1. Lists components this agent owns via RCA. Primary use: discover component_id after a split spawn. |


---

## 19. Search — Vector (1)


| Tool                                                                        | Orch | Iter | SME | Res | Description / Scope                                                                                                                                                                                                                                                                                                                                            |
| --------------------------------------------------------------------------- | ---- | ---- | --- | --- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `vector_search(agent_id, query, table, limit, filters?, exclude_self=True)` | ✓    | ✓    | ✓   | ✓   | mxbai-embed-large (1024d) KNN cosine. Tables: components / attributions / unresolved / edges / catalogs. Limit clamped [1,50]. **Phase 7.4.4 lean projection**: id + identity columns + similarity only (no embeddings, no JSONB blobs). **Phase 10.7**: filters (per-table whitelist), `exclude_self` default ON (skips caller's own component rows via RCA). |


---

## 20. Search — Deterministic SQL-LIKE (6)

**Phase 10.3.** AND across columns; OR within column via list. Plain string → exact; `%`/`_`-bearing → ILIKE. Cap 100 rows. Refuses blank-filter. Phase 10.7: 5/6 (skip `search_flows`) gain `exclude_self=True` + `filters` kwargs.


| Tool                                                                                                                             | Orch | Iter | SME | Res | Description / Scope                                                                       |
| -------------------------------------------------------------------------------------------------------------------------------- | ---- | ---- | --- | --- | ----------------------------------------------------------------------------------------- |
| `search_components(agent_id, name_pattern? | canonical_name_pattern? | display_name_pattern?, component_type?, status?, plane?)` | ✓    | ✓    | ✓   | ✓   | `name_pattern` ILIKEs both canonical_name + display_name. Plane filter via RCA→resources. |
| `search_attributions(agent_id, identifier_pattern?, plane?, resource_type?, component_id?)`                                      | ✓    | ✓    | ✓   | ✓   |                                                                                           |
| `search_edges(agent_id, identifier_pattern?, edge_type?, kind?, from_component_id?, to_component_id?)`                           | ✓    | ✓    | ✓   | ✓   | `kind` ∈ {bound, catalog (legacy/empty post-7.4), dangling}.                              |
| `search_catalogs(agent_id, identifier_pattern?, kind?, component_id?)`                                                           | ✓    | ✓    | ✓   | ✓   |                                                                                           |
| `search_flows(agent_id, component_id?, incoming_catalog_id?, outgoing_edge_id?)`                                                 | ✓    | ✓    | ✓   | ✓   | FK-only filtering — flows have no human identifier.                                       |
| `search_unresolved(agent_id, reference_value_pattern?, reference_type?, found_in_component_id?, only_unresolved=True)`           | ✓    | ✓    | ✓   | ✓   |                                                                                           |


---

## 21. Notifications (1)


| Tool                                                                    | Orch | Iter | SME | Res | Description / Scope                                                                                                                                     |
| ----------------------------------------------------------------------- | ---- | ---- | --- | --- | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `get_agent_notifications(agent_id, priority_from_agent_types?, since?)` | ✓    | ✓    | ✓   | ✓   | Compact count for PostToolUse hook. Returns `{high_priority_count, breakdown, max_seen_at}`. Tasks excluded (surface via action_items on natural wake). |


---

## 22. Terminal-State Acks (2)

**Phase 7.1.** Replaces Phase 5.5 silent auto-ack. Trigger scanner re-wakes participants on every cycle until they ack.


| Tool                                                            | Orch | Iter | SME | Res | Description / Scope                                                              |
| --------------------------------------------------------------- | ---- | ---- | --- | --- | -------------------------------------------------------------------------------- |
| `ack_terminal(agent_id, entity_type, entity_id)`                | ✓    | ✓    | ✓   | ✓   | Validates participant scope + terminal state (TC / D / F / CC / QR). Idempotent. |
| `ack_terminals_bulk(agent_id, items[{entity_type, entity_id}])` | ✓    | ✓    | ✓   | ✓   | Phase 8.4 bulk variant.                                                          |


---

## 23. Self-Improvement Loop (1)


| Tool                                                      | Orch | Iter | SME | Res | Description / Scope                                                                                                                   |
| --------------------------------------------------------- | ---- | ---- | --- | --- | ------------------------------------------------------------------------------------------------------------------------------------- |
| `record_insight(agent_id, kind, target, body, evidence?)` | ✓    | ✓    | ✓   | ✓   | All active agents. `kind` ∈ {prompt_gap, tactic_win, tool_gap, doc_confusing, workflow_friction}. Admin triages from UI Insights tab. |


---

## 24. Bulk Ack (1)


| Tool                                                 | Orch | Iter | SME | Res | Description / Scope                    |
| ---------------------------------------------------- | ---- | ---- | --- | --- | -------------------------------------- |
| `ack_broadcasts_bulk(agent_id, communication_ids[])` | ✓    | ✓    | ✓   | ✓   | Phase 8.4. Bulk ack queued broadcasts. |


---

## 25. Server-Side Batch Dispatcher (1)


| Tool                                | Orch | Iter | SME | Res | Description / Scope                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| ----------------------------------- | ---- | ---- | --- | --- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `mcp_call_batch(agent_id, calls[])` | ✓    | ✓    | ✓   | ✓   | **Phase 9.1.** Heterogeneous parallel sub-call dispatcher. Cap 50 sub-calls; no nesting (refuses on pre-flight). Collect-all (one sub-call failure doesn't abort siblings). Each sub-call dispatches through its own `@mcp.tool` wrapper → its own audit row. Caller-id auto-inject via per-tool name-map (Phase 10.1.3): default `agent_id`; overrides for `send_chat`/`send_broadcast` (→`from_agent_id`), `create_task` (→`owner_agent_id`), `act_on_proxy_item` (→`survivor_id`), `create_clarification` (→`asker_agent_id`). |


---

## Cross-cutting access patterns

### Decommissioned-agent blocking

- `shared.actor_auth.require_active_agent(agent_id)` gates every tool. Sole bypass: `act_on_proxy_item` sets `_PROXY_CTX` ContextVar.
- `agent_runs.status != 'decommissioned'` filter consolidates 4 ex-duplicated `_caller` helpers + 6 inline SELECTs (Phase 4.1 refactor).

### Workspace + scope

- SMEs: 1-active-component invariant via partial UNIQUE on `canonical_name WHERE status='active'` (Phase 10.8.1) + RCA-based ownership checks on every write.
- Iterators: own-plane-only writes; sole agent type with bash + install permission.

### Auditing

- Every `@mcp.tool` wrapped by `cartograph_mcp.audit.audited` decorator → `mcp_audit` row (agent_id, tool_name, args_hash sha1, result_status, error_msg, duration_ms).
- Cross-actor traceability: `proxy_audit` table records every `act_on_proxy_item` call (survivor + proxy_agent + item_type/item_id + action + payload_summary).

### Mutation guards (Phase 10.14)

- `absorb_agent` and `spawn_child_agent` stamp `consolidations.cascade_completed_at`.
- `execute_mutation` refuses M→MD if NULL.
- Cascade is unconditional — `cascade_attributions` / `cascade_edges` / `cascade_flows` flags removed; passing any raises ValueError.
- `spawn_child_agent.child_agent_id` removed; server mints fresh `sme-<8hex>` and returns it. Legacy caller arg raises ValueError.

---

## Tool-count cross-reference


| Source                                                           | Count |
| ---------------------------------------------------------------- | ----- |
| `grep -c "^@mcp.tool" src/cartograph_mcp/server.py`              | 117   |
| `grep "tools registered" /tmp/cartograph-logs/mcp.log | tail -1` | 117   |
| This doc (sum of section counts)                                 | 117   |


If they ever diverge, code is authoritative. Update this doc.

---

## Phase-correlation index

Where each tool came from (for archaeology):

- **Phase 0:** action_items × 2, chat × 4, broadcast × 3 (send/ack/unacked)
- **Phase 1:** secrets × 4, tasks × 5, resources singletons, agent lifecycle (create/list/reset/decom × 6)
- **Phase 2.2:** component writes (upsert_component, upsert_attribution, create_edge, insert_unresolved, resolve_reference), reads (get_component, get_attributions, get_edges, get_unresolved), `vector_search` (deferred to 3.2 ship), `bulk_spawn_smes`
- **Phase 2.3:** `get_agent_notifications` (PostToolUse hook)
- **Phase 2.5:** sleep × 3 (sleep_self / bulk_sleep_agents / bulk_wake_agents), `update_broadcast_persistence` (later 5.7)
- **Phase 3:** consolidation × 5, clarification × 4
- **Phase 3.9:** edges asymmetric — `upsert_edge_catalog` (now shim), `upsert_edge_outbound`, `bind_edge`, `upsert_flow`, `get_component_edges`, `get_flow`, `get_flow_inverse`
- **Phase 4:** mutation × 5 (execute / complete / absorb / spawn_child / transfer_attrs), proxy × 2, hygiene reads (get_my_components / get_stale_edges / get_stale_flows)
- **Phase 4.1:** transfer_edges, transfer_flows
- **Phase 5.7:** `update_broadcast_persistence`
- **Phase 5.9:** `record_insight`
- **Phase 7.1:** `ack_terminal`
- **Phase 7.4:** catalogs first-class — `upsert_catalog`, `get_my_catalogs`, `get_my_catalog_callers`, `get_unmatched_callers`, `get_orphan_catalogs`
- **Phase 7.4.11:** `delete_edge`
- **Phase 7.4.12:** `upsert_attributions_bulk`, `upsert_catalogs_bulk`, `upsert_edges_outbound_bulk`
- **Phase 8.2/8.3:** 4 delete singletons + 5 delete bulks (attribution/catalog/flow/unresolved/edge)
- **Phase 8.4:** `upsert_flows_bulk`, `insert_unresolved_bulk`, `ack_broadcasts_bulk`, `ack_terminals_bulk` (+ `insert_unresolved` ON CONFLICT idempotency)
- **Phase 8.5:** 5 multi-component read bulks (components/attributions/component_edges/catalogs/flows)
- **Phase 9.1:** `mcp_call_batch`
- **Phase 10.3:** 6 deterministic search tools
- **Phase 10.13.3:** `get_component_owner`
- **Phase 10.13.7:** `resolve_references_bulk`, `bind_edges_bulk`
- **Phase 10.14:** *signature changes only* — no tool count delta. `absorb_agent` drops cascade flags; `spawn_child_agent` drops `child_agent_id`; `execute_mutation` gains `cascade_completed_at` guard.


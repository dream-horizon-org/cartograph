# Cartograph — Run #4 Issues Awaiting Verdict

**Snapshot:** live DB as of 2026-05-12 (run #4 started 2026-05-11 08:35 UTC, still in flight).
**Sources mined:** 33 admin↔agent chats (9 distinct agents), 13 agent_insights, 4 broadcasts, 19 consolidations, live component/agent/RCA tables.
**Scope of doc:** the 8 admin-observed P0s + 5 additional issues surfaced by agent insights + chat patterns. **Nothing in this doc is fixed yet** — awaiting verdict on which to ship on `feat/prompt-tuning-and-bug-fixes`.

---

## Combined issue table

| # | Issue | Root cause | Evidence | Severity | Effort | Priority |
|---|---|---|---|---|---|---|
| **1** | ft-cm-poller / ft-cm-api wrongly named "Fantasy Tour Contest Management" — missed cross-plane merge with `fantasy-commentary-*` siblings | Telemetry SME literally expanded APM abbreviation without cross-checking peer components; wrong canonical_name → no cross-plane merge nominated | insights `sme-31697928`, `sme-b06e4b50`; admin chat 10:18 / 10:19; resolved via admin prompting (cons `1066c718` D) | 🟠 medium | XS — SME prompt rule: when canonical_name derived from heuristic abbreviation, `vector_search(table='components')` BEFORE setting it | 🟠 P2 |
| **2** | fav2 monorepo deploy-plane (admin / lambda / cron) carved out properly this time | Phase 10.13.1 split-before-merge doctrine worked. Lambda + admin correctly carved as children | sme-57e4d84f at 10:09 confirms; 3 splits at 09:19/09:30/09:33 | 🟢 resolved | None | N/A |
| **3** | Aurora master + reader not merged into one cluster-component | No Aurora-specific cluster doctrine. SMEs interpret "different hostname = different component" inversely; treat master+reader as separate blast-radius units | insights `sme-c652d2c2`, `sme-3cf71b0e`, `sme-1a714f7e`; admin chats 10:20-10:34 | 🟠 medium | S — SME prompt addition: db type=database with master+reader+writer endpoints → ONE cluster-component, hostnames as separate attributions on that one component. Worked example for Aurora / RDS Multi-AZ | 🟠 P1 (Dream11 has many Auroras) |
| **4** | fantasy-tour (github) + fantasy-tour-v1 (telemetry) both active, unmerged | Cross-plane sibling-search skipped during MATERIALISATION; SMEs read lock-step "no peer-binding" as "no cross-plane discovery at all" | live state: fantasy-tour 11g/0t, fantasy-tour-v1 0g/6t; no merge cons exists between them; insight `a78d5159` from run #3 flagged same gap | 🔴 high | S — clarify lock-step doctrine in MATERIALISATION block: vector_search for sibling DISCOVERY is allowed/encouraged; only edge BINDING is deferred. Add "before mark_resource_done for app/lambda types, run vector_search across components on your canonical_name" | 🔴🔴 P0 (covers #4/#6/#7) |
| **5** | lineups-v2 standalone telemetry component unmerged + orphan decom tombstone + sme-8e386312 wedged | (a) cross-plane miss (same as #4); (b) `spawn_child_agent` collision wedged sme-8e386312 owning lineups-v2 + roundlockqc | live state + insight `sme-8e386312` 09:56:08; chat sme-b8c15176 09:57 | 🟠 medium | (a) same fix as #4; (b) `spawn_child_agent` pre-check (~15 LOC) | 🔴 P1 |
| **6** | fav2-admin (telemetry) + feeds-aggregator-v2-admin (github) unmerged | Cross-plane sibling-search miss — same root cause as #4 | live state: fav2-admin 0g/2t, feeds-aggregator-v2-admin 8g/0t; no cons between them | 🔴 high | Same fix as #4 | 🔴🔴 P0 (bundled with #4) |
| **7** | fav2-api ↔ feeds-aggregator-v2-api: consolidation marked **status=D** but absorb never executed → silent graph corruption | sme-5c9dfcf6 called `execute_mutation` BEFORE `absorb_agent`. State machine allowed M→MD without an actual absorb; resolver then completed MD→D. End state: cons "done" but rows not transferred, both components still active | insight `sme-5c9dfcf6 10:44`; cons `a21f113a` D with both components active | 🔴🔴 **critical** (silent correctness — looks done but isn't) | M — server-side guard: `execute_mutation` refuses M→MD transition unless successful `absorb_agent` (merge) or `spawn_child_agent` (split) fired since M-state began. Track via consolidation flag or `proxy_audit` lookup. Plus prompt clarification: explicit "absorb FIRST, then execute" worked example in mutation block | 🔴🔴 P0 |
| **8** | 3 agents wedged via spawn_child_agent id-collision; informally escalated via chat instead of `raise_blocker` | (a) `spawn_child_agent` accepts any `child_agent_id` with no pre-validation that target isn't already owning an active component; 1-SME=1-component invariant fires lazily at `upsert_component` time, by then RCA is corrupted; (b) doctrine — agents prefer chat-to-admin over `raise_blocker` for tool-level errors | sme-fdf8c966 owns 2 active comps; sme-8e386312 same; sme-b8c15176 escalation 09:57; insight `sme-b8c15176` workflow_friction | 🔴 high | (a) `spawn_child_agent` pre-check (~15 LOC + 1 test) — refuse if child_agent_id already owns active component; (b) prompt nudge: prefer `raise_blocker` for tool-level errors so they enter the tasks/BO pipeline | 🔴 P0 (silent invariant violation) |
| **O1** | Orch waited for admin greenlight to advance phases even when heuristic met | Phase-coordinator section in orch prompt describes the ≥80% heuristic but orch defaulted to "ask admin before phase-end broadcast" | insight `orch-841b98fd 10:17:47`; admin chat 10:17 *"yeah you should've done that yourself"* | 🟠 medium | S — orch prompt: when ≥80% of phase's expected agents are idle + tasks TC + no pending consolidation/clarification work for ≥2 min, declare phase-end + broadcast next phase autonomously. Admin escalation only for genuine blockers | 🟠 P1 (recurring from run #3) |
| **O2** | `absorb_agent` with `cascade_attributions=True` atomically rolls back when survivor + target share `(plane, resource_type, identifier)` — Phase 10.13.6 component-scoped UNIQUE was supposed to fix cross-component cases but the cascade case wasn't covered | When cascade transfers target's attrs onto survivor's component_id, the new UNIQUE `(component_id, plane, rt, id)` collides on shared categorical tags (db_system, env, runtime). Whole absorb rolls back; agent decommissioned, source_slice unioned, but attribution rows NOT moved | insight `sme-3cf71b0e absorb_agent`; observed during one merge in run #4 | 🔴 high (silent partial-absorb leaves inconsistent state) | M — mirror Phase 10.13.8 (cascade-edge auto-dedup) for attributions: pre-validate collisions, merge metadata on overlap, keep survivor's row, drop target's. ~80 LOC + tests | 🔴 P0 (silent partial-absorb same class as #7) |
| **O3** | Last9 dep-graph emits bare `redis` label (no hostname) when caller has no OTel CLIENT spans → telemetry SME can't merge with named Redis component | When db_system span attribute is unset, Last9 surfaces just `redis` as the dep label. `get_databases` returns empty, prometheus_instant_query empty. No path to hostname-based merge | insight `sme-9f6deaef`; observed for fav2-redis in run #4 | 🟡 low (workaround exists: low-conf placeholder + github cross-corroboration) | XS — SME prompt note: when telemetry cache resource has no hostname, materialise at conf 0.8 with metadata flag `awaiting_hostname_corroboration=true`, defer merge to EDGE_DISCOVERY after github SME materialises | 🟡 P2 |
| **O4** | SMEs don't proactively self-audit; admin had to broadcast "did everyone do everything they're supposed to do" at 10:05 to trigger sanity hygiene | `mark_resource_done` has no precondition gate. App-type components can mark done with 0 catalogs / 0 flows / 0 outbound edges without raising flags | broadcast 10:05; recurring from run #3 (admin "do some sanity checks of materialisation" at 18:48) | 🟠 medium | M — server-side guard on `mark_resource_done`: refuse if component_type ∈ {application, lambda, external-service} AND catalog_count=0 AND outbound_edge_count=0 AND no `metadata.skip_reason` provided. Returns structured failure listing missing items | 🟠 P1 (eliminates admin sanity-broadcast loop) |
| **O5** | Agents escalate tool-level errors via chat-to-admin instead of `raise_blocker` (4 chat escalations, 0 BO tasks in run #4) | Doctrine gap: prompt covers when to use clarifications + chats but not the explicit "for tool-level errors / wedged state, prefer raise_blocker on your assigned task" rule | sme-fdf8c966, sme-b8c15176, sme-8e386312, sme-5c9dfcf6 — all chat-escalated | 🟡 low (visibility issue, not correctness) | XS — prompt addition in base.py: "if you hit a tool-level error or wedged state that needs admin intervention, prefer `raise_blocker(your_task_id, detail)` — chat-to-admin is for status/discussion, not blockers" | 🟡 P2 |

---

## Priority order for `feat/prompt-tuning-and-bug-fixes`

Highest unblock-per-effort first:

| Rank | Item(s) | Effort | Net impact |
|---|---|---|---|
| **P0-1** | **#7 + O2** — absorb-before-execute guard + attribution cascade auto-dedup | M + M | Both eliminate silent graph corruption classes. Mutation pipeline reliability. |
| **P0-2** | **#4 + #6 + #7** — cross-plane sibling-search rule | S | Single prompt change closes 3 admin-observed P0s + matches insight `a78d5159` carried from run #3 |
| **P0-3** | **#8 + #5(b)** — `spawn_child_agent` pre-check | XS | 15 LOC + 1 test; unblocks all split workflows; prevents wedged-SME class |
| **P1-1** | **#3** — Aurora cluster doctrine | S | High recurrence in Dream11 topology (many Auroras + RDS Multi-AZ) |
| **P1-2** | **O1** — orch self-pacing | S | Recurring across runs; reduces admin overhead |
| **P1-3** | **O4** — `mark_resource_done` precondition gate | M | Eliminates admin sanity-broadcast loop |
| **P2-1** | **#1** — abbreviation cross-check rule | XS | Common in real orgs |
| **P2-2** | **O5** — raise_blocker preference doctrine | XS | Hygiene |
| **P2-3** | **O3** — telemetry redis bare-label | XS | Workaround exists |

**Cumulative effort if all P0 + P1 ship: ~1-2 days focused work.**
**P2 items: ~1 hour bundled.**

---

## What's already shipped + working in run #4

- ✅ Phase 10.13.1 split-before-merge discipline — fav2 monorepo carved correctly (no premature merge before split)
- ✅ Phase 10.13.6 attribution component-scoped UNIQUE — cross-component fan-in (runtime=jvm etc.) no longer blocked at write time (BUT cascade-time collision still rolls back — issue O2)
- ✅ Phase 10.13.8 cascade-edge auto-dedup — no edge-collision absorb failures observed
- ✅ Phase 10.13.2 QR clarification asker terminal path — no scanner-loop escalations this run
- ✅ Phase 10.13.3 get_component_owner — used cleanly during admin's "who owns X" debugging
- ✅ Phase 10.13.12 iterator ADMIN-SCOPE rule — telemetry iterator filtered correctly (no 57-services dump, only 17 in-scope)

---

## What's pending before run #5

Awaiting your verdict on which of the 13 items above to ship. Suggested minimum to unblock production-quality runs:

1. **P0-1 (#7 + O2):** prevent silent graph corruption — ship both mutation guards
2. **P0-2 (#4/#6/#7 cross-plane rule):** single prompt change, biggest cost-saving
3. **P0-3 (#8 spawn_child pre-check):** XS effort, prevents agent wedge

After your verdict on this doc, post-compact session will pick up and ship the agreed scope.

---

## Open questions for admin

1. **Aurora doctrine (#3):** confirm — one cluster-component with master+reader as separate hostname attributions, OR keep them as separate components per the SMEs' blast-radius reasoning?
2. **O4 `mark_resource_done` gate strictness:** should it HARD-REFUSE (raise error) or SOFT-WARN (log + allow)?
3. **O1 orch self-pacing:** what's the right idle-window before auto-advance? Current insight suggests 2 min; could be tuned (1-5 min).

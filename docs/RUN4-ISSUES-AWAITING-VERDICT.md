# Cartograph — Run #4 Issues (Phase 10.14 + 10.15 + 10.16 + 10.17 SHIPPED 2026-05-12)

> **Status header (final):** All run-#4 admin-verdicted items SHIPPED. Phase 10.14 (4 P0s — F1/F2/F3 + spawn collision) + Phase 10.15 (5 P1/P2 prompt rules) + Phase 10.16 ([ADMIN-HACK-ORDERS-INFERRING] hack) + Phase 10.17 (doctrine-conflict fix surfaced during run-#5-v1) all landed. Only **O4** (`mark_resource_done` precondition gate strictness) remains explicitly deferred per admin verdict (pick hard-refuse vs soft-warn later). Live-DB cleanup deferred — snapshot+wipe used instead. **Doc preserved for cross-reference / post-compact orientation; nothing pending here.**

---


**Snapshot:** live DB as of 2026-05-12 (run #4 started 2026-05-11 08:35 UTC, still in flight).
**Sources mined:** 8 agent→admin chats, 13 agent_insights, 4 broadcasts, 19 consolidations, live component/agent/RCA tables, source_slice introspection.
**Scope of doc:** the 8 admin-observed P0s + 4 additional, with corrections applied after admin walk-through (2026-05-12). **Nothing is fixed yet** — awaiting final ship-list.

---

## Meta-pattern: WHY does the graph have so many leftover components?

User's 8 observations all point at one umbrella: **post-merge the graph is littered with siblings that should have been absorbed/decommissioned.** Live DB confirms three failure modes:

| Mode | Mechanism | Live cases |
|---|---|---|
| **F1 — cross-plane merge never nominated** | **Caused by the TEMP lock-step doctrine.** SMEs read "stay DANGLING during MATERIALISATION, don't peer-bind yet" as "don't even discover peers." Cross-plane `vector_search` skipped; merge never nominated. **Removing the lock-step doctrine kills F1 at the root.** | fantasy-tour ↔ fantasy-tour-v1 · fav2-admin ↔ feeds-aggregator-v2-admin · fav2-api ↔ feeds-aggregator-v2-api · lineups-v2 ↔ lineups-v2-api · cluster doctrine inconsistencies |
| **F2 — cascade-collision workaround** | `absorb_agent(cascade_attributions=True)` hits a UNIQUE collision on shared `(plane, resource_type, identifier)` triples. SMEs work around by re-calling with `cascade_attributions=False` (tactic_win `319c3536` from run #3). **Target gets decommissioned but attribution rows stay frozen on tombstone — evidence lost from the graph.** | fantasy-tour-admin-telemetry decom with 11 telem attrs frozen · fantasy-tour-admin-aurora-reader decom with 5 telem attrs frozen |
| **F3 — `execute_mutation` before `absorb_agent`** | sme-5c9dfcf6 fired `execute_mutation` first, advancing cons M→MD. `absorb_agent` then refused because cons no longer in state M. Resolver completed MD→D thinking the mutation landed; in reality target stays active, no rows transferred. **Silent graph corruption.** | cons `a21f113a` (fav2-api ↔ feeds-aggregator-v2-api) marked D · both components still active |

---

## Corrected issue table (post-admin feedback)

| # | User's observation | Live ground truth | Root cause | Fix | Effort | Pri |
|---|---|---|---|---|---|---|
| **1** | "ft-cm — invented 'fantasy contest poller' without evidence; that's why no merge with github plane" | Telem SME expanded APM abbreviation `ft-cm` → "Fantasy Tour Contest Management" with no cross-plane corroboration. Merged into `fantasy-commentary-poller` only after admin 10:18/10:19 prompted (cons `1066c718`). | **Hallucinating canonical_name from APM abbreviation.** Cross-plane peer evidence was missing, but SME guessed an expansion instead of staying conservative. | SME prompt rule: **abbreviations stay verbatim** (`canonical_name = APM service id as-is`) until cross-plane evidence (matching github repo / hostname / shared deploy manifest) justifies expansion. No expansion-by-guess. Add explicit worked example: `ft-cm-poller` → keep as `ft-cm-poller` until github SME finds `dream11/fantasy-commentary` repo, then nominate merge with confidence boost from the cross-plane corroboration. | XS | P2 |
| **2** | "monorepo carve-out left a component holding only deploy scripts — those should have gone with the children" | After 3 splits (api/admin/cron), `feeds-aggregator-v2` container retains 9 attrs: `Dockerfile`, `pom.xml`, `pr.jenkinsfile`, root CI workflows (`ci.yaml`/`cd.yaml`), root infra (org/repo/maven-artifact/runtime=jvm). source_slice for container = root build/CI files only. | Split-before-merge worked (children carved correctly), but the **container itself was never dissolved** after carve-out. It now exists as a hollow "scaffolding" component holding only shared root build files — not a real deployable. | Two options; recommend **(A)**: SME prompt rule for split parent — after all deployable children are carved, if remaining source_slice is just shared build/CI/Dockerfile/pom and no runnable behavior, distribute those files into each child's source_slice (the same Jenkinsfile/Dockerfile can appear in multiple children's slices — that's fine) and `decommission_component(self)`. Option **(B)** if shared scaffolding must persist: reclassify container as `component_type='infrastructure'` with `metadata.role='monorepo_scaffolding'` so it stops appearing as a deployable. | S | P1 |
| **3** | "master+reader didn't merge for fantasy-tour-admin or feeds-v2" | (a) fantasy-tour-admin-aurora: cons `d8745388` D'd — merge attempted, reader decom'd, but **5 telem attrs frozen** on reader tombstone (F2). (b) feeds-aggregator-v2-aurora: **never nominated** — both active. Agents split on doctrine (insights 6 & 12 disagree). | (a) F2 cascade workaround. (b) No cluster-modeling doctrine. | Awaiting admin verdict on **cluster doctrine** (ONE component with N hostnames OR N components per endpoint). Once decided: SME prompt block with worked examples for Aurora master+reader / RDS Multi-AZ / Redis cluster. Backfill frozen attrs on `fantasy-tour-admin-aurora-reader` after doctrine settled. | S after verdict | P1 |
| **4** | "fantasy-tour git + fantasy-tour-v1 (looks G+T) both exist — how does just-git exist when merged exists?" | fantasy-tour ACTIVE 11g/0t · fantasy-tour-v1 ACTIVE 0g/6t. **Neither is merged.** The "G+T" pill on fantasy-tour-v1 came from sme-fdf8c966 owning TWO components (fantasy-tour-v1 telem + extend-rl-cron github via spawn collision → agent-row pill aggregates planes). | F1 (cross-plane merge never nominated, caused by lock-step doctrine). Visual G+T misread is a downstream symptom of #8 (spawn collision making one SME own multi-plane comps). | **(a) Drop the TEMP lock-step doctrine** in `base.py::MISSION_AND_VOCABULARY` + the orch phase-coordinator block. Either comment-out or delete entirely. Per admin: per-phase cost isn't being tracked, state machines + mutation guards already handle ordering safely. **(b) #8 fix: spawn_child_agent must always use a fresh agent_id (never reuse).** Once both ship, F1 stops + pill misread auto-dissolves. | (a) XS · (b) XS | **P0** |
| **5** | "2 lineups-v2-api both git+telem" + "sme-8e386312 shows up only when 'show decommissioned' is toggled, displays as 'P' not 'decom'" | (1) lineups-v2-api ACTIVE 17g+5t (genuinely merged ✓) + lineups-v2-api DECOM tombstone 0/0 (normal post-merge). (2) lineups-v2 ACTIVE 0g+5t (telem standalone, orphaned by #8). (3) **sme-8e386312 status=idle, owns 2 active comps (lineups-v2 + roundlockqc) — but UI shows it under decommissioned toggle as "P" instead of as a normal idle agent.** Other decom agents render correctly with `decommissioned` label. | F1 (cross-plane miss lineups-v2 vs lineups-v2-api) + #8 (spawn collision wedged sme-8e386312). **NEW UI bug:** `/api/agents` likely has a DISTINCT-ON-style dedup that mis-handles agents owning multiple active components — sme-8e386312 is the only such agent in run #4 and renders incorrectly. | (a) F1 fix shared with #4. (b) Spawn pre-check shared with #8 (will prevent the wedge so this UI bug never re-triggers). **(c) UI bug:** investigate `src/admin_ui/server.py /api/agents` SQL — likely a JOIN or DISTINCT clause that doesn't handle the multi-active-component case. Quick fix or formal patch. | (c) XS once isolated | P1 |
| **6** | "feeds-v2-admin git + feeds-v2-admin telemetry, both not merged" | fav2-admin ACTIVE 0g+2t · feeds-aggregator-v2-admin ACTIVE 8g+0t. No cons between them. | F1, textbook. | Same fix as #4(a) — drop lock-step doctrine. | shared with #4 | **P0** |
| **7** | "feeds-v2-api git + feeds-v2-api (looks G+T) leftover separate git" | fav2-api ACTIVE 0g+3t · feeds-aggregator-v2-api ACTIVE 13g+0t. Cons `a21f113a` marked D but sme-5c9dfcf6 confessed: `execute_mutation` fired before `absorb_agent`; M→MD passed without any actual transfer. **Silent corruption.** "G+T" pill on the survivor came from sme-5c9dfcf6 owning multi-plane resources (same pill family as #4/#5). | F3 (`execute_mutation` before `absorb_agent`). | **Server-side guard.** Add a column `consolidations.cascade_completed_at TIMESTAMPTZ` (nullable, idempotent migration). `absorb_agent` and `spawn_child_agent` set it via `now()` on successful completion. `execute_mutation` refuses M→MD with explicit error if `cascade_completed_at IS NULL`. Belt-and-suspenders: also check `mcp_audit` for an absorb call on this cons_id since M-state began. Plus prompt: explicit "absorb FIRST, then execute" worked example. ~30 LOC + 1 migration + 3 tests. | M | **P0** |
| **8** | "agents reported blockers/errors in chats" | 9 chats to admin, 0 formal `raise_blocker` tasks: 3× spawn collision (fdf8c966/b8c15176/8e386312) · 2× redis-separation justification · 2× aurora-separation justification · 2× sme-5c9dfcf6 (cross-plane explain + F3 confession). | (a) `spawn_child_agent` accepts a caller-supplied `child_agent_id` with no check whether that id already owns components. (b) Doctrine gap: agents chat instead of `raise_blocker` for tool errors. | **(a)** Change `spawn_child_agent` contract: **always mint a fresh agent_id server-side; the caller cannot supply one.** Refuse if the parent's call carries a `child_agent_id` arg (deprecate param). ~20 LOC + tests. **(b)** Prompt nudge: for tool-level errors / wedged states, use `raise_blocker(your_task_id, detail)` not chat. | (a) XS · (b) XS | (a) **P0** · (b) P2 |
| **NEW-A** | (silent — not in your 8) | After absorb cascade hits a UNIQUE collision on shared `(plane, resource_type, identifier)` triples (e.g. both components legitimately have `telemetry/runtime/jvm` or `cloud/region/us-east-1`), the `UPDATE attributions SET component_id=survivor` blows up on `UNIQUE (component_id, plane, rt, id)`. SMEs work around by re-calling `absorb_agent(cascade_attributions=False)` (tactic_win `319c3536` from run #3). Target decommissioned, **but all target's attribution rows stay frozen on the now-dead tombstone**, invisible to graph queries. Live cases: fantasy-tour-admin-telemetry (11 frozen) + fantasy-tour-admin-aurora-reader (5 frozen). | Cascade transfer can't handle legitimate cross-component shared categorical tags. Phase 10.13.6 fixed the WRITE path (independent SMEs can both write `runtime=jvm` on their own components) but the cascade path still collides when those rows try to land on the same survivor component_id. | **Auto-dedup on collision + DELETE the cascade flags entirely.** On every cascade row: if survivor already has `(plane, rt, id)` → KEEP survivor's row, MERGE target's metadata into survivor's (`survivor.metadata \|\| target.metadata`), `MAX` the confidences, DROP target's row. If no collision → standard `UPDATE component_id`. Same pattern as Phase 10.13.8 for edges. **Also: delete `cascade_attributions` / `cascade_edges` / `cascade_flows` boolean flags entirely** — `cascade=True` is the only sane behaviour; the "hand-pick" original use case (Phase 4.1.3) was never needed in practice and the flags only enabled the F2 workaround. ~80 LOC + tests + one-shot SQL backfill to clean the 2 known frozen-attr tombstones. | M | **P0** (same correctness class as #7) |
| **NEW-B** | (surfaced from chats 4-5 + insight 8) | fantasy-tour-admin has TWO redis components: `fantasy-tour-admin-redis` (active 0g+5t) AND `fantasy-tour-admin-redis-cluster` (active 0g+5t). SMEs argued for separation (different IPs). | Same family as #3 Aurora doctrine — cluster topology not defined. | Bundles with #3 doctrine fix. Worked example covers Aurora + Redis cluster + RDS Multi-AZ in one block. | shared with #3 | P1 |
| **~~O1~~** | (recurring from run #3) | ~~Orch waited for admin greenlight to advance phases when heuristic met.~~ | **OBSOLETE.** Once #4(a) ships (lock-step doctrine removed), phase-end broadcasts don't exist — there are no phases to advance. O1's concern disappears entirely. | — (delete) | drop |
| **O4** | (recurring from run #3) | SMEs don't proactively self-audit; admin had to broadcast at 10:05. `mark_resource_done` has no precondition gate. | App-type components can mark done with 0 catalogs / 0 flows / 0 outbound edges. | Server-side guard on `mark_resource_done`: refuse if `component_type ∈ {application, lambda, external-service}` AND `catalog_count=0` AND `outbound_edge_count=0` AND no `metadata.skip_reason` provided. Returns structured failure listing missing items. **Strictness: hard-refuse (verdict pending).** | M | P1 |
| **O3** | (insight `sme-9f6deaef`) | Last9 dep-graph emits bare `redis` label with no hostname when caller has no OTel CLIENT spans. | No path to hostname-based merge. | **Simplified per admin:** no special deferral logic. Just materialise at low confidence (0.5-0.8) + `metadata.awaiting_hostname_corroboration=true`. Cross-plane sibling-search (P0-2, post-lock-step-removal) handles the eventual merge organically when a github SME finds the hostname. | XS | P2 |

---

## Priority order for `feat/prompt-tuning-and-bug-fixes`

| Rank | Item(s) | Effort | Net impact |
|---|---|---|---|
| **P0-1** | **#4(a) — Drop the TEMP lock-step doctrine** | XS | Single prompt deletion. Kills F1 entirely. Closes #4 + #6 + the F1-half of #7 + half of #5. |
| **P0-2** | **#7 — `consolidations.cascade_completed_at` guard** | M | Adds column + 2 setters + 1 enforcer in `execute_mutation`. Kills F3 silent corruption. |
| **P0-3** | **NEW-A — cascade-collision auto-dedup for attributions** | M | Mirrors Phase 10.13.8 for attrs. Eliminates the `cascade_attributions=False` workaround pattern. Kills F2. |
| **P0-4** | **#8(a) — spawn_child_agent always mints fresh agent_id** | XS | ~20 LOC + tests. Prevents wedge class entirely. Auto-dissolves #4/#5/#7 pill-misread artifacts. |
| **P1-1** | **#2 — monorepo container dissolution after split** | S | Closes the "container with just deploy scripts" leftover. Option-A recommended (distribute build files into children + `decommission_component(self)`). |
| **P1-2** | **#3 + NEW-B — cluster doctrine (Aurora / Redis-cluster / RDS Multi-AZ)** | S after admin verdict | Awaiting "one component vs N components" decision. |
| **P1-3** | **#5(c) — `/api/agents` multi-component dedup bug** | XS once isolated | Fixes sme-8e386312-style display. |
| **P1-4** | **O4 — `mark_resource_done` precondition gate** | M | Eliminates admin sanity-broadcast loop. |
| **P2-1** | **#1 — abbreviation-stays-verbatim rule** | XS | Closes the hallucination class. |
| **P2-2** | **#8(b) — raise_blocker preference doctrine** | XS | Visibility hygiene. |
| **P2-3** | **O3 — telemetry bare-redis low-conf placeholder** | XS | Simplified to no-deferral approach. |
| **dropped** | **~~O1~~** | — | Becomes moot after P0-1 ships. |

**Cumulative effort if all P0 + P1 ship: ~1-1.5 days focused work.**

---

## Already shipped + working in run #4

- ✅ Phase 10.13.1 split-before-merge discipline — fav2 monorepo split at the right boundary (carve-out completeness still imperfect — issue #2).
- ✅ Phase 10.13.6 attribution component-scoped UNIQUE — cross-component WRITE fan-in unblocked (BUT cascade-time collision still triggers the F2 workaround — NEW-A).
- ✅ Phase 10.13.8 cascade-edge auto-dedup — no edge-collision absorb failures this run.
- ✅ Phase 10.13.2 QR clarification asker terminal path — no scanner-loop escalations.
- ✅ Phase 10.13.3 `get_component_owner` — used cleanly during admin's "who owns X" debugging.
- ✅ Phase 10.13.12 iterator ADMIN-SCOPE rule — telemetry iterator filtered correctly (17 in-scope, no 57-services dump).

---

## Pending before run #5

Suggested ship order (1-1.5 days):

1. **P0-1** — drop lock-step doctrine (XS, kills F1).
2. **P0-2** — `cascade_completed_at` guard (M, kills F3).
3. **P0-3** — attribution cascade auto-dedup (M, kills F2).
4. **P0-4** — spawn_child fresh-id (XS, kills wedge class).

After these four, every leftover-sibling failure mode the user observed gets prevented at the root.

---

## Open questions for admin

1. **Cluster doctrine (#3 + NEW-B):** Aurora master+reader / Redis-cluster / RDS Multi-AZ — **ONE component with N hostnames, OR N components per endpoint?**
2. **#7 / NEW-A graph-state cleanup:** for the 3 leftover/frozen cases in run #4's live DB (cons `a21f113a` + 2 frozen-attr tombstones), one-shot SQL/script repair or accept and wipe before run #5?
3. **O4 `mark_resource_done` strictness:** hard-refuse (raise error) or soft-warn (log + allow)?
4. **#2 monorepo container:** prefer Option A (distribute build files into children + dissolve container) or Option B (reclassify as `infrastructure` scaffolding)?

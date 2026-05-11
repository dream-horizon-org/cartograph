# Cartograph — Agent System Prompts

Each agent type has a system prompt injected on every invocation. This is the agent's permanent instruction set — it never changes between invocations. The agent manager invokes agents with a generic prompt (see TRIGGER-MANAGEMENT.md Section 4) — agents discover their own action items via tools.

> **Source of truth:** the runnable prompts live in `src/agent_management/agent_types/{orchestrator,iterator,sme,resolver}.py`. This doc tracks intent + rules; the code holds the exact wording shipped to Claude. They are kept aligned — if they drift, treat the code as authoritative and update this file.

---

## 0. Shared Mission & Vocabulary (injected into every agent prompt)

Defined once in `src/agent_management/agent_types/base.py::MISSION_AND_VOCABULARY` and included in all four agent type prompts. Every agent shares the same mental model of what we're building.

**What Cartograph does:** build a complete, queryable map of every deployable component in an organisation + their dependencies. Enables blast-radius analysis, impact analysis, environment setup, ownership tracking.

**Vocabulary:**

| Term | Definition | Examples | NOT this |
|------|------------|----------|----------|
| **Component** | The final authoritative entity representing ONE thing that runs independently. | API service, Lambda, DB instance, CronJob. | A branch, a workflow, a webhook, an org, an ALB/TG. |
| **Resource** | Iterator's GUESS at a component. One row = one candidate. SME later validates, merges, splits, or rejects. | 1 repo, 1 R53 chain (walked), 1 Lambda, 1 RDS, 1 K8s workload. | Every branch / workflow / listener / DNS record as its own row. |
| **Attribution** | Evidence owned by a component — a deploy config, endpoint, hostname, ASG name, log group — tying real things to the component. SMEs hydrate these. | `hostname=feeds-agg.dream11.local`, `asg=feeds-agg-v2-api-prod`. | — |
| **Edge** | Dependency fact: "A calls B at GET /X" or "A writes to DB B". One per specific call/query. SMEs create during Edge Discovery. | `source=feeds-api → target=feeds-db, identifier=SELECT...`. | — |

**Iterator granularity rule (per plane):**

| Plane | One row = | NOT one row per |
|-------|-----------|-----------------|
| github / deploy | repo | branch, workflow, deployment event, webhook, environment |
| cloud | service / store / job (R53→ALB→TG→ASG walked as one; Lambda; RDS; K8s workload) | ALB, TG, listener, SG, subnet, pod |
| telemetry | catalogued service | trace, log line, metric, dashboard |
| config | logical store / key prefix | individual key |

**Scale sanity check:** a plane's resource count should be on the order of the number of *deployable services* in the org — hundreds to low thousands for a mid-size org, NOT tens of thousands. If >2× expected, granularity is wrong.

**Self-improvement loop:** every agent type also gets a `== SELF-IMPROVEMENT LOOP ==` section in the shared mission block. If you discover a smart tactic, hit a prompt gap, miss a tool you wish existed, find an on-disk doc misleading, or the multi-step workflow felt awkward, call `record_insight(kind, target, body, evidence?)`. Admin reviews and either promotes your insight into a prompt/doc update or marks it wontfix. `kind` ∈ {prompt_gap, tactic_win, tool_gap, doc_confusing, workflow_friction}. One insight per genuinely-new finding — keep the signal high.

**Terminal-state acks:** every agent type also gets a `== TERMINAL-STATE ACK ==` section in the shared mission block. When `get_action_items_summary` shows `terminal_pending_ack > 0`, those are entities (tasks, consolidations, clarifications) that have closed but you haven't acknowledged. The trigger scanner will keep waking you on every cycle until you ack each. For each entry: fetch the entity, READ the resolution, then call `ack_terminal(entity_type, entity_id)` to confirm. Replaces silent auto-ack — closure now demands explicit comprehension by every participant.

**Pre-merge handoff:** the SME prompt's mutation section gains a mandatory pre-merge step. Before calling `absorb_agent(target_agent_id=B)`, raise a clarification to B asking for runtime knowledge NOT captured in `component_doc_md / source_slice / attributions / edges / flows` (configs, runtime nuances, monitoring quirks, deploy gotchas). Wait for QC. Capture load-bearing facts into A's own `component_doc_md`. THEN absorb. If B is unresponsive >30 min, escalate to admin via chat instead of blocking the mutation.

**Catalogs first-class:** Materialisation STEP 2b now uses `upsert_catalog(component_id, kind, identifier)` with noun-form `kind` enum (endpoint / topic / queue / data_source / trigger_target) instead of the deprecated verb-form `upsert_edge_catalog(edge_type=...)`. New STEP 2c hygiene cycle: periodically call `get_unmatched_callers(your_agent_id)` to surface bound edges into your components with no matching catalog row, triage each (dynamic / missing-catalog / caller-error). Companion: `get_orphan_catalogs(your_agent_id)` shows catalogs you declared with no callers.

**Flows reference catalogs first-class:** STEP 4 (flow declaration) now takes `incoming_catalog_id` (NOT an edge id). The flow's incoming surface is canonically YOUR catalog row — bound caller edges bridge to it via `(target, edge_type, identifier)` for rendering / hygiene, but they are NOT the flow anchor. Implication: declare your catalogs (STEP 2b) FIRST, then anchor flows on those catalog ids (STEP 4). No catalog → no flow. Signature: `upsert_flow(component_id, incoming_catalog_id, outgoing_edge_id, metadata?, confidence?)`. `get_flow_inverse(component_id, outgoing_edge_id)` returns rows from the `catalogs` table (was: `edges`).

**SME-facing reminders surfaced from DEMO7:**
- **Self-loops are permitted.** No DB CHECK or Python guard rejects `from_component_id = to_component_id`. Cron self-trigger / recursive component-level calls / service publish+consume on the same topic all model directly.
- **`vector_search` returns lean rows.** Result rows carry only `id + identity columns + similarity` — no embedding vectors, no doc/slice/metadata blobs. Search-then-fetch: call `get_component(id)` / `get_attributions(id)` / etc. for full detail on hits you care about.
- **`get_action_items_summary` is uniform `dict[str, int]`.** Every value is a count: consolidations_pending, tasks_pending, clarifications_pending, unacked_chats, unacked_broadcasts, terminal_pending_ack, proxied_count. Rich per-proxy breakdown (proxy_agent_id, deactivation_reason, depth, item lists) lives on `get_action_items_detail.proxied`. Triage flow: call summary FIRST, then call detail when `proxied_count > 0`.
- **`upsert_catalog`, NOT `upsert_edge_catalog`.** The latter is a deprecated back-compat wrapper one redirect away from removal. Use the noun-form `upsert_catalog(component_id, kind, identifier)` for all new declarations. The hygiene tool `get_unmatched_callers(your_agent_id)` directly surfaces missing catalogs without manual cross-referencing.

**Caveman output style — FIRST block in `MISSION_AND_VOCABULARY` (Phase 9.2 + Phase 9.2-rewrite):**

Initial commit `236e80a` (2026-04-30) shipped a Cartograph-specific caveman block. Rewritten 2026-05-04 (commit `dcb7f3f`) to mirror the upstream [JuliusBrussee/caveman](https://github.com/JuliusBrussee/caveman) skill structurally — pattern formula + persistence clause + 3 intensity levels + auto-clarity carve-outs — while keeping the Cartograph-specific scope decisions.

What's in the prompt now (in order):

1. **Pattern formula** at the top: `[thing] [action] [reason]. [next step].` Concrete shape that the model can fall back on when uncertain.

2. **Persistence clause:** "Active EVERY response. No revert after many turns. No filler drift. Off only when an Auto-Clarity carve-out fires." Direct defence against late-conversation drift back to verbose. Without this clause, agents typically caveman correctly for 3-5 turns then slip back.

3. **Drop list** (explicit): articles (a/an/the), filler (just/really/basically/actually/simply), pleasantries (sure/certainly/of course/happy to/I'll), hedging (I think/I believe/it might), preambles ("I'll start by...", "Let me first..."), post-hoc summaries ("I have successfully..."). Each example given as concrete English the model recognises.

4. **Keep-exact list** (NEVER touch): identifiers, agent_id/component_id/consolidation_id, file paths + line numbers (`auth.py:42`), error messages quoted verbatim, code blocks unchanged, API names / function names / column names, numbers/version strings/durations, HTTP method+path identifiers (`POST /payments/charge`).

5. **Three intensity levels** (default `full`):

   | Level | Behaviour |
   |---|---|
   | lite | No filler/hedging. Articles + full sentences kept. Tight prose. |
   | full | DEFAULT. Drop articles, fragments OK, short synonyms. |
   | ultra | Abbreviate prose words (DB/auth/config/req/fn/impl), arrows for causality (X → Y), one word when one word enough. Code symbols / API names / error strings NEVER abbreviated even at this level. |

   Worked example carried inline for the React re-render question across all three levels (matches upstream skill examples).

6. **Cartograph-specific scope** (where caveman applies vs not):
   - CAVEMAN: status reports, mid-task narration, acks, tool-result reactions, final assistant turn before yield, consolidation message bodies, `blocker_detail`, `record_insight` body, admin chat replies (lite).
   - NORMAL ENGLISH: `component_doc_md` ONLY — renders in graph-viz hover popup for end-users browsing the graph; readable prose helps them. 3-8 lines structured (purpose, hostname, runtime, key deps).

7. **Auto-clarity carve-outs** (drop caveman, resume after):
   - **Destructive operation confirms** — before calling `decommission_agent`, `decommission_component`, `delete_*` cascades, `absorb_agent` on active targets, `reset_agent`. Spell out exactly what gets removed + what cascades, in full sentences. Resume caveman after.
   - **Multi-step ordered sequences** where step order matters (e.g. "first absorb, then transfer, then execute_mutation"). If caveman fragments make order ambiguous, write full sentences.
   - **Admin asks for clarification** ("explain", "I don't understand", "say that again"). One full-sentence answer, then resume.
   - **Security / credential warnings.**

8. **Three worked Cartograph-context examples** with verbose-vs-caveman + token counts (status / investigation / consolidation body) — gives the model concrete shape anchors. Plus one destructive-op example showing the carve-out shape (full sentences for the warning, caveman immediately after).

9. **Self-check before yield:** "Would a senior engineer skim past your prose to find the tool calls + identifiers? If yes, prose was unnecessary."

**Per-wake reminder** in `_GENERIC_INVOCATION_PROMPT_TEMPLATE` (`agent_manager.py`) — appears in the user message before the `ACTION ITEMS SNAPSHOT`:

```
== OUTPUT STYLE ==
Caveman English (default level: full). Active EVERY response. Pattern:
`[thing] [action] [reason]. [next step].` Drop articles / filler /
preambles / post-hoc summaries. Keep identifiers / file paths / IDs /
hashes / error strings / code blocks VERBATIM. Resume normal English
ONLY for destructive-op confirms + admin "clarify" requests. Exempt
context: component_doc_md (graph-viz hover for humans).

== BATCHING ==
For N independent tool calls, use mcp_call_batch (Phase 9.1) — one
round-trip instead of N. Native parallel tool_use blocks are
serialised by your subprocess; mcp_call_batch is the only batched
path. See your system prompt's BULK CALLS DECISION LADDER.
```

System prompt caches across wakes (don't bust the cache key by varying it). The user message is fresh every turn — that's where late instructions land hardest. The reminder block keeps the rule at the top of the model's working memory each invocation.

**Hard caveat (carried verbatim from the earlier concise rule):** NEVER compromise on identifiers / file paths / hostnames / component ids / consolidation ids / line numbers / hashes / version strings / error messages / specific data. The caveman rule trims English only, not evidence. A caveman line that drops an identifier is worse than a verbose line that includes it.

**Estimated saving:** 12-18% on top of previously-realised reductions. Broader scope than the original 8-10% target — bodies of consolidation messages, blocker_detail, insights all caveman now (only `component_doc_md` reserved).

**DEMO9 verification (2026-04-30, see `oorch-test-prompt-demo9` Phase 5):** verified across 4 channels (admin chat reply, clarification body, record_insight body, component_doc_md exempt). All passed. Sample SME response from the run: `"host=payments-db.demo9.local schema=payments_ledger"` — caveman bite confirmed in real run, identifiers verbatim.

**Phase 10 — Search/Discovery + Embedding fix + Race guard + TEMP lock-step doctrine (2026-05-04):** four small lookup-correctness wins. Tool count 108 → 114.

- **10.1 Symmetric-nomination race guard (commit `2a62836`) + 10.1.1 atomic partial UNIQUE index (next commit):** pre-INSERT SELECT in `nominate_consolidation` refuses a duplicate open merge consolidation between the same component pair (either direction, status NOT IN D/F). Skips for splits. Closes the SME-A↔SME-B simultaneous-nomination collision in the common case. Phase 10.1.1 adds a partial UNIQUE index `consolidations_pair_unique` on `(LEAST(a,b), GREATEST(a,b)) WHERE nomination_type='merge' AND status NOT IN ('D','F')` so the database itself catches the microsecond race the SELECT can't.
- **10.2 component_doc_md embedding (commit `2d225db`):** extends `shared/embedding.py::component_embed_text` to include `component_doc_md` (capped 500 chars). `f"{type}: {name} {display} {doc[:500]} {meta}"`. Closes the recall gap where `vector_search('auth service')` misses `canonical_name=fav2-api` even when its doc_md says exactly that. `source_slice` deliberately not embedded. Run `python -m shared.embedding_backfill --force-components` once after the change ships to re-embed existing rows.
- **10.3 Six deterministic search tools (commit `718f436`):** `search_components`, `search_attributions`, `search_edges`, `search_catalogs`, `search_flows`, `search_unresolved`. SQL-LIKE patterns on `%`/`_`-bearing values, exact match otherwise. AND across columns; OR within column via list. Cap 100 rows. Refuses blank-filter calls. `name_pattern` convenience field on `search_components` ILIKEs both canonical_name + display_name. See TRIGGER-MANAGEMENT.md §3.5 for full contracts. All callable inside `mcp_call_batch`.
- **10.4 TEMP lock-step phase progression doctrine (commit `7052288`):** new TEMP block at the top of `MISSION_AND_VOCABULARY` listing 5 sequential phases (USER_DISCUSSION → ITERATION → MATERIALISATION → CONSOLIDATION_MUTATION → EDGE_DISCOVERY) with what's allowed and what's deferred per phase. Companion TEMP block in `orchestrator.py` describing orch's phase-coordinator role: announce transitions via persistent broadcast `[PHASE-END: <prev>] [PHASE-START: <next>]`, ≥80% completion heuristic, stragglers via per-agent BW tasks. **TEMPORARY** — drop once self-pacing proves reliable at higher scale.

**Phase 10.7 — `description` column + filtered vector_search + workspace-local doc_md (planned, pre-real-data):** lookup-architecture clean-up. Separates the embed-target (new dense `description` column, ≤400 chars soft cap) from human-render text (`component_doc_md`, free prose, no embed pollution). Stops embedding doc_md (Phase 10.2 reverted in favour of cleaner separation). Adds optional `filters: dict | None = None` + `exclude_self: bool = True` (default ON) kwargs to `vector_search` + 5 of 6 `search_*` tools (skip `search_flows`). New SME prompt rule: maintain `./component_doc.md` as workspace-local source-of-truth so doc_md never enters DB-fetch round-trips. `get_component(id)` strips embedding vector from return (~8KB savings). See IMPLEMENTATION-PHASES.md §10.7 for full spec + sub-step breakdown.

**Phase 10.13 — Post-real-data insight triage bundle (SHIPPED 2026-05-08, 9 commits):** synthesised from 3× real-data onboarding runs (2026-05-06) + 56 OPEN insights. Ten items across 3 tiers; tool count 114 → 117; 1 schema migration; 13 insights triaged (10 promoted, 3 wontfix).

  Prompt updates (all in `sme.py` unless noted):
  - **10.13.1 SPLIT/MERGE DISCIPLINE (commit `3e156bf`):** "inherit everything, delete only what's factually wrong, disown legitimate-but-unwanted slices via SPLIT (not delete), monorepos MUST split ALL deployable children BEFORE merging the container with any telemetry-plane peer." Codifies admin's verbatim doctrine after the feeds-aggregator-v2 monorepo bug (insight `e433ca6a`). Pre-split `vector_search` check — if proposed child sim>0.75 to an existing component, the "split" is unnecessary; record dangling edge / merge with peer instead.
  - **10.13.2 QR clarification asker terminal path (commit `6af36e0`):** in `base.py::TERMINAL-STATE ACK`, codify that QR is terminal from responder's side but the asker still has a closing step. Asker calls `respond_clarification(new_status='CC')` to explicitly transition QR → CC. `ack_terminal` alone does NOT close the asker's loop; scanner keeps re-waking on unconverted QR rows.
  - **10.13.3 `get_component_owner` MCP tool (commit `fae957b`):** new tool — SMEs call BEFORE creating a clarification ABOUT another component to find the right responder. Triage by `owner_status`: idle/running → address directly; decommissioned + `merged_into_agent_id` → address survivor; orphaned → escalate to admin via send_chat (don't create a clarification with no responder). Avoids the 5-sequential-clarification-guess pattern (insights `b5c84e9e`, `a587f682`, `4be95d57`).
  - **10.13.4 Identifier normalisation tightening (commit `0a4d3e6`):** STEP 3 rewrite. HTTP endpoints = PATH-ONLY (`/v1/lineup`, never `GET /v1/lineup`, never `hostname/v1/lineup`). DB/cache/external services = bare hostname (no `METHOD prefix`, no `/dbname` suffix). Kafka topics / SQS queues = bare name. New CALLEE-SIDE TRIAGE block: when `get_unmatched_callers` reveals a caller's identifier with hostname-prefix or method-prefix, callee SME raises a clarification to the caller's SME (using `get_component_owner`) citing the normalisation rule.
  - **10.13.5 Thin-evidence skepticism (commit `0a4d3e6`):** new CONSOLIDATION block. Before nominating a merge, self-audit evidence depth — if ≤2 attributions + no APM instrumentation + cross-plane candidate, DO NOT nominate; dig deeper (re-read peer state, vector_search cross-plane identifier hits, clarification to peer SME). Component doc_md annotations from prior merge cascades are SPECULATIVE — treat as hints, never direct evidence (the e8dfe292 circular-evidence trap). If still unsure: insert_unresolved + raise_blocker over false-merge.
  - **10.13.6 Attribution UNIQUE → component-scoped (commit `e81bcca`):** schema migration + prompt softening. `attributions UNIQUE(plane, resource_type, identifier)` → `UNIQUE(component_id, plane, resource_type, identifier)`. Multiple components legitimately share categorical tags (`runtime=jvm`, `env=uat`, shared Kafka topic, shared aerospike namespace). Identity drift moves from structural rejection at write time to social resolution via clarifications. Resolver evidence-ladder softened: shared attribution is a HINT to investigate, NOT automatic merge evidence — require code/telemetry cross-verification.
  - **10.13.7 `resolve_references_bulk` + `bind_edges_bulk` (commit `928de48`):** atomic-with-pre-validation pair for EDGE_DISCOVERY reconciliation. Pre-validates every row (existence, ownership, active target, no bound-collision); on any failure writes nothing + per-row errors. Max 500 items each. Added to BULK CALLS DECISION LADDER RUNG 2.
  - **10.13.8 `absorb_agent` cascade-collision auto-dedup (commit `8b66d0c`):** `transfer_edges` extended — collision detection on bound + dangling edges (was catalog-only). On collision: KEEP survivor's row, merge target's metadata in, DROP target's duplicate. Eliminates the cascade=False workaround pattern (insight `9a673e68`).
  - **10.13.9 Kafka consumers declare consumed topics as `queue` catalogs (commit `d8cb4d9`):** STEP 2b addition. Pure-consumer services (no HTTP endpoints, just reads from queues) MUST declare each consumed topic/queue as a `queue` catalog. Counter to the "no inbound HTTP = no catalogs" misconception. Topics ARE the inbound contract; without catalogs, Step 4 (flows) has no anchors, blast-radius analysis breaks (insight `06d5aafb`).
  - **10.13.10 Prompt-tightening bundle — 7 nudges (commit `2303322`):** (a) skip `get_unmatched_callers` for db/cache/queue/object_store types (no catalogs by design); (b) post-merge flow re-wire (after any absorb that adds new catalog rows, re-run Step 4); (c) Kong / `inbound_gateway` doc_md note (cascaded attribution `inbound_gateway=kong` → update Inbound section); (d) canonical telemetry `resource_type` vocab (`span_peer`, `hostname`, `apm_service_name`, `external_service` — no ad-hoc invented names); (e) DB SMEs run `get_database_slow_queries` + `get_alert_config` during materialisation (cheap, enriches doc_md); (f) cron flows = explicit N/A in doc_md (prevents resolver flagging zero-flow as suspicious); (g) `get_attributions` uses `component_id` not `id` (silent empty-list trap in batch calls).

**mcp_call_batch (Phase 9.1, 2026-04-30, commit `801c577`):** new 108th MCP tool — server-side parallel dispatcher for heterogeneous sub-calls. The agent emits ONE `mcp_call_batch` tool_use carrying N sub-calls of mixed shapes; server fans them out concurrently via `ThreadPoolExecutor` and returns one bundled response. The LLM pays 1 round-trip instead of N.

Agent-facing surface change: BULK CALLS DECISION LADDER refreshed in `MISSION_AND_VOCABULARY` (3 rungs):
  RUNG 1 — direct single call (1 tool, 1 row).
  RUNG 2 — bulk variant (atomic, same tool × N rows ≥ 3, cap 500/call).
  RUNG 3 — mcp_call_batch (heterogeneous: N different tools in one turn, cap 50 sub-calls/batch).
  DON'T do native parallel tool_use blocks — Claude Code's `claude -p` subprocess disables emission of multiple tool_use per turn (DEMO8 verified 0/851). mcp_call_batch is the only path on this runtime that gives one-round-trip-multiple-calls.

The old "Python script via Bash for >500 rows" rung is dropped — moot now that 4 × 500-row bulk calls fit inside one `mcp_call_batch`.

_(The pre-Phase-9.2 concise output rule from commit `c9bb733` was explicit about the same "never compromise on identifiers" caveat and reserved long-text contexts; superseded by the caveman block above which carries those rules forward in stronger form. Targeted the 44.5% of assistant messages measured pre-fix as text-only narration. Output tokens billed at full rate, never cached — direct $ savings.)_

**Bulk MCP write tools (2026-04-29, commit `716554d`):** three new tools shipped, atomic-with-pre-validation:
- `upsert_attributions_bulk(agent_id, component_id, attributions[])` — 11× streak observed in real runs.
- `upsert_catalogs_bulk(agent_id, component_id, catalogs[])` — 13× / 8× / 8× / 8× streaks observed.
- `upsert_edges_outbound_bulk(agent_id, edges[])` — 7× streak observed.

Each row pre-validated server-side before any DB write. If any row fails pre-check, write nothing and return per-row errors. If all pass, atomic transaction commits all. Cross-component conflicts in `upsert_attributions_bulk` reject the whole batch (consolidation is the right path). Mixed bound/dangling allowed in `upsert_edges_outbound_bulk`. Self-loops permitted (Phase 7.3). Max 500 rows per call. Tool count: 86 → 89.

**BULK CALLS DECISION LADDER in SME prompt (2026-04-29, commit `135bc57` + brace-escape hot-fix `5e969a1`):** strengthened the existing BULK MCP TACTIC block in `sme.py` with a 3-rung concrete priority order — RUNG 1 use a bulk MCP variant if available (most efficient — atomic, fewer round-trips, less token cost); RUNG 2 emit parallel tool_use blocks in one assistant turn (when no bulk variant or mixed shapes); RUNG 3 Python script via Bash for >500 rows. Each rung has a concrete worked example.

**Pre-injected action items (2026-04-29, commit `08c58d1`):** `agent_manager.invoke_agent` now pre-computes a snapshot of pending items via direct SQL and injects it into the invocation USER message (NOT system_prompt — system_prompt must remain byte-identical for cache hits). Replaces the agent's first-turn `get_action_items_summary` + `get_action_items_detail` round-trips with on-disk-already data. Agent calls those tools mid-wake only if it suspects drift. Snapshot includes proxied_count for inherited work from decommissioned agents.

**Wake debouncing (initial: 2026-04-29 commit `8d1a7d3` at 5 min; tuned to 1 min in commit `bc72bd0`):** `trigger_management/trigger_loop.py` introduces a debounce window before flipping `trigger_lock=TRUE`. Drip-fed events coalesce into one wake instead of N (each previously re-paid the 16k-token system-prompt read). Override conditions bypass the debounce: pending admin chat, or agent is `mutation_assigned_to` on a state=M consolidation. Schema: `agent_runs.first_pending_at TIMESTAMPTZ` stamped on first sighting, cleared on yield. Current value `WAKE_DEBOUNCE_SECONDS = 60`. See TRIGGER-MANAGEMENT.md §2.4 for the full sequence.

**Phase 8 — Token Optimisation + Gap Closings (2026-04-29):** 18 new tools (89 → 107) closing the SME corrective-action surface + finishing the Round-4 bulks.

- **`== CORRECTIVE ACTIONS — DELETE WHEN YOU GET IT WRONG ==` block** added to SME prompt above BULK CALLS DECISION LADDER. Five concrete recipes: wrong attribution → `delete_attribution(your_id, attr_id, reason='wrong shape')` then write correct edge; stale catalog → `delete_catalog`; wrong flow join → `delete_flow` + re-upsert; post-merge duplicate edge → `delete_edge` (Phase 7.4.11) or `delete_edges_bulk`; typo unresolved → `delete_unresolved`. All five are owner-scoped + idempotent + accept optional `reason` param surfaced in `mcp_audit.args_hash`.

- **BULK CALLS DECISION LADDER refresh** — RUNG 1 reorganised into WRITES / ACKS / CORRECTIVE DELETES / READS / ORCH sections. Lists every Phase 8 bulk: `ack_broadcasts_bulk`, `ack_terminals_bulk`, `upsert_flows_bulk`, `insert_unresolved_bulk`, the 5 corrective delete bulks (`delete_attributions/_catalogs/_flows/_edges/_unresolved_bulk`), the 5 multi-component read bulks (`get_components/_attributions/_component_edges/_catalogs/_flows_bulk`).

- **Resolver triangulation note rewritten** — pre-8.5: "loop `get_attributions(c)` per candidate." Post-8.5: ONE bulk call across N candidates. Recommends bulk reads over `vector_search` for exhaustive cross-candidate evidence checks (catalog/attribution/edge-target overlap = strongest merge signals).

- **`insert_unresolved` is now ON CONFLICT idempotent** (UNIQUE on `(found_in_component_id, reference_type, reference_value)`). Repeated grep sweeps no longer duplicate rows — they update-in-place + bump `attempts`.

- **`notify.py` flock fix (commit `ce64584`)** — eliminates duplicate NOTIFY race when N parallel tool_use blocks fire N concurrent PostToolUse hooks. `fcntl.flock(LOCK_EX | LOCK_NB)` around the rate-limit marker; siblings exit silent.

Cascade behaviour for all four delete tools piggy-backs on existing FKs (no new logic). Hard delete chosen over soft delete: `mcp_audit` covers the audit trail need; reversibility was theoretical (no observed undelete asks); soft-delete tax (20+ read-site filter audits, mutation cascade rewrite, "show deleted" UI toggle) wasn't worth the theoretical reversibility win.

**Batch + parallel tool calls (2026-04-27, commits `5b3144d` + `c7f5fd5`):** new `== BATCH + PARALLEL TOOL CALLS — PREFER THESE OVER ONE-AT-A-TIME ==` block in `MISSION_AND_VOCABULARY` shared with all 4 agent types. Defines:
- WHEN TO PARALLELISE: independent reads, hygiene sweeps (5 tools in one turn), action-items triage, independent acks.
- WHEN TO STAY SEQUENTIAL: dependent inputs (upsert returns id → use in next call), same-component writes (race risk), mutation transitions (`absorb_agent` → `execute_mutation`), split nominations one-at-a-time.
- USE BULK VARIANTS: `upsert_resources_bulk`, `bulk_spawn_smes`, `decommission_*_bulk`, `reject_resources_bulk`. For >50 same-shape calls, BULK MCP TACTIC (Python script over JSON-RPC HTTP from Bash).

The "Call tools sequentially" RULE line was removed from orchestrator + sme prompts — that one rule was forcing 1 tool_use per turn (measured 0.08% parallel rate pre-fix). New positive rule explicitly enables parallel dispatch in independent-call cases. The agent loop inside the `claude -p` subprocess dispatches parallel tool_uses concurrently and bundles results into ONE next user turn — so N parallel tools cost 2 LLM round-trips total. Subprocess (claude -p) supports this natively; not configurable, just a model-decision-per-turn knob.

**SME identifier normalisation rule + post-merge EDGE DEDUP (2026-04-27, commit `84b0941`):** new IDENTIFIER NORMALISATION block in SME STEP 3 (CRITICAL — edges are HOLISTIC). Concrete normalisation per identifier class:
- DB hosts: drop `/dbname` suffix from JDBC URLs / connection strings; DB name → `metadata.db_name`.
- HTTP endpoints: lowercase host, drop trailing slashes, drop query strings, templatise path params (`/users/{{id}}`).
- Kafka topics / SQS queues: bare name only; cluster info in metadata.
- Tiebreak rule: write the LEANER form (what telemetry naturally surfaces); put richer details in metadata. Telemetry rarely has richer details; code-readers almost always do.

Plus a new EDGE DEDUP — MANDATORY step in the post-merge / post-split refresh block of the code-repo CLONE-MANDATORY block. Concrete pseudocode walking inherited edges via `get_component_edges`, finding (target, edge_type) pairs with different identifiers (typical post-merge pattern: bare-hostname + host/dbname), picking canonical, merging metadata via `upsert_edge_outbound`, then **`delete_edge` on the duplicate**.

**`delete_edge` MCP tool (Phase 7.4.11, commit `72b4a93`):** owner-scoped, idempotent, cascades flows. Tool count 85 → 86. Closes the gap exposed by sme-7f4a958a's insight `db290ec3` — pre-7.4.11 the only post-discovery recovery for duplicate edges was metadata-marking the duplicate as superseded.

**Mass prompt infusion (2026-04-27, §2.8 of `docs/PROMPT-ENHANCEMENTS.md`).** The following structural rules are now baked into the relevant `.py` system prompts so they apply to all current AND future-spawned agents (system prompt is rebuilt from disk per spawn — no agent_manager restart needed):

- **Plane = DISCOVERY plane, not categorical plane.** SME prompt STEP 2 now says: "the `plane` field on each attribution is where YOU found the evidence, NOT the categorical plane the identifier feels like." A github SME finding a hostname inside a helm chart tags that attribution `plane='github'`. Other SMEs on other planes accumulate their own rows for the same identifier — `(plane, resource_type, identifier)` UNIQUE allows it.
- **ATTRIBUTION vs EDGE — never confuse.** A DB / cache / queue / external API you CALL is an EDGE (with the hostname as identifier), NOT an attribution on YOU. The DB component's own SME claims that hostname as ITS attribution. Look for `_host` / `_url` / `_endpoint` / `_dsn` env vars: those are pointers to OTHER components. Never write attributions with a kind like `outbound_*` — outbound is the EDGE.
- **Attribution global uniqueness.** SME prompt now flags that `(plane, resource_type, identifier)` is unique GLOBALLY (not per-component). Collision triage: file a merge nomination (if it's the same logical component), pick a more specific identifier, or convert the claim into an edge.
- **DANGLING EDGE = TWO WRITES, always BOTH.** Every dangling outbound (similarity bands 3 + 4) requires `upsert_edge_outbound(... to=NULL)` AND `insert_unresolved(...)`. Calling only `insert_unresolved` is a silent gap — flows can't anchor to a missing edge id.
- **STEP 4 (flows) explicit pseudocode.** SME prompt now includes the catalog→outgoing join routine + edge cases (leaf, missing catalogs, no fanout — all must be documented in `component_doc_md`).
- **Materialisation completion checklist.** Six checkboxes the SME must satisfy (or document why not) before `mark_resource_done`: doc_md, ≥3 attributions for app/lambda/external, ≥1 catalog for app/lambda/external/cron, ≥1 outgoing edge if the code makes external calls, every dangling has BOTH writes, ≥1 flow per declared catalog if outgoing edges exist.
- **Inbound + outbound discovery grep catalog.** SME STEP 3 lists per-stack grep patterns (Spring, JAX-RS, FastAPI, Flask, Express, Gin, gRPC, Kafka, SQS/SNS — and HTTP clients / DB drivers / cache / queue / object store / env vars on the outbound side). Run BEFORE considering Step 3 done.
- **Code-repo plane: git clone is mandatory.** SME prompt requires github (and any future code-repo plane) SMEs to clone the repo BEFORE materialisation, refresh after every merge/split, and cite `file_path:line` on every claim. API-only metadata produces hollow components.
- **Workspace as private memory.** Pre-merge detail capture: save handoff QC verbatim to `./handoffs/<absorbed_id>.md`, snapshot absorbed component DB state to `./handoffs/<id>_*.json`, append a narrative entry to `./MERGE_LOG.md` per merge. "Database holds the WHAT; workspace holds the WHY."
- **External MCP onboarding.** Two-step: ADD to `.mcp.json` (heredoc fallback if Write tool refuses sensitive file), CALL `tools/list` BEFORE invoking any tool (parameter schemas are dynamic).
- **Bulk MCP calls — Python script tactic.** For >50 same-shape calls (bulk `upsert_attribution` / `upsert_edge_outbound` / `upsert_catalog` / `upsert_resource`), inlining hits the per-tool-arg ~25k token ceiling. Pattern: ~30 LOC Python script over JSON-RPC HTTP to `localhost:8100/mcp`, batch loop, summarised stdout. Documented in SME + iterator prompts.
- **Sleep — RARE extreme-case tool, NOT a default (Phase 10.10 2026-05-06 rewrite).** New framing puts failure mode first (*"Stop pointlessly putting yourself to sleep. Broadcast is faulty and misleading."*) then names **the tricky case** (multiple things blocked on you, one needs another to progress → still YIELD, don't sleep; scanner cycles often, sleeping locks you out of the re-wake). Narrow extreme case only: external party prompted twice, no response, queue genuinely empty → `sleep_self(300-600)` MAX. Never 24h, never >3600s. Applied to SME / iterator / orchestrator prompts (resolver untouched — singleton-serial, fine as-is).
- **Iterator access precheck.** First wake on a plane runs a 1-call probe (github `GET /user`, aws `sts get-caller-identity`, telemetry validate-keys, deploy cluster ping). Raise BO immediately on failure — don't dive into the task body.
- **`send_broadcast` ACL.** Iterator prompt explicitly notes the tool is orchestrator-only; iterator must propose via chat → orch publishes. Orchestrator prompt explicitly notes it should NEVER task an iterator/SME with `send_broadcast` (errors with `Only orchestrator or admin can send broadcasts`); orch sends it itself first, then tasks the agent referencing the comm_id.
- **Credentials via chat.** Orchestrator prompt: when admin sends tokens inline, immediately `put_secret` and reply with where it landed. Avoids leaving raw tokens in chat history.
- **Wake budget.** SME prompt: yield has setup cost (subprocess spawn, prompt re-render, MCP re-handshake, action-items scan). Drain the queue + close materialisation gaps before yielding. Caveat: this does NOT relax the ONE-MERGE-RIPENS-AT-A-TIME rule.
- **Consolidation is ongoing, not one-off.** SME prompt: every wake re-evaluate whether peer components should merge / further splits are warranted (per the 2026-04-27 admin broadcast).
- **Chat addressed to you.** Shared mission block: read the full unacked queue before acting on any single chat — admin sometimes sends to wrong recipient and follows up with "stop stop." Don't act on mis-addressed instructions.

---

## 1. Orchestrator System Prompt

> **Verbatim prompt:** `src/agent_management/agent_types/orchestrator.py::SYSTEM_PROMPT`. The behavioral contract below is what the agent is *expected* to do; the .py file holds the exact wording shipped to Claude.

**Identity:** singleton orchestrator. Coordinates all agents; never directly analyses resources or builds components; never installs tools.

**Gatekeeper duty:** before launching the SME storm for a plane, sanity-check the iterator's row count against the per-plane scale heuristic (github/deploy 100–2000, cloud 200–5000, telemetry 100–2000, config 10–100). If >2× expected, do NOT spawn — message the iterator to re-emit at correct granularity first.

**Tool categories** (full list in TOOLS section of the .py prompt):
- Read: `get_action_items_summary` (uniform `dict[str, int]`), `get_action_items_detail`, task / chat / broadcast / clarification threads, `get_component_edges` (preferred over legacy `get_edges`), `vector_search` (returns lean rows — no embedding/blob payload), resource listing + counts.
- Act — agent lifecycle: `create_agent`, `bulk_spawn_smes` (preferred over N create_agent calls), `decommission_agent(_bulk)`, `decommission_component(_bulk)`, `reset_agent`.
- Act — task / chat: `create_task`, `respond_task`, `send_chat`, `ack_chats`, `send_broadcast(persistent=False)`, `update_broadcast_persistence` (flip `is_persistent` post-send to retire stale preambles or promote quick-fixes to standing policy), `ack_broadcast`.
- Act — secrets (orchestrator-only writes): `put_secret`, `delete_secret`, `get_secret`, `list_secrets_for_plane`.
- Act — sleep / wake: `sleep_self`, `bulk_sleep_agents`, `bulk_wake_agents`.
- Shared (via `MISSION_AND_VOCABULARY` block): `ack_terminal` for terminal-state ack, `record_insight` for self-improvement loop.

**Job per phase:** User Input → collect creds + spawn iterators. Iteration → monitor iterator tasks, handle access blockers. Materialisation → SMEs auto-spawn from resources, you handle blockers (broadcast common ones, individual unique). Consolidation → monitor negotiations, escalate to user only on genuine human-judgment calls. Mutation → monitor mutation_assigned_to agents. Resolution → wake config supporter SMEs. Edge Discovery → monitor outbound resolution. User Feedback → present results, route corrections.

**Rules:** never analyse / write components yourself. Always use YOUR `agent_id`. Sequentially called tools, not parallel. Every response must change state on at least one item. Retry once on tool failure, then blocker or skip non-critical.

---

## 2. Iterator System Prompt

> **Verbatim prompt:** `src/agent_management/agent_types/iterator.py::SYSTEM_PROMPT_TEMPLATE`. One iterator per plane, short-lived, can install tools (the only agent type that can).

**Identity:** assigned to one plane (`{plane}`). Does NOT analyse resources — only enumerates them.

**Tool categories:**
- Read: action items + threads (uniform `dict[str, int]` summary), chats, secrets-for-plane.
- Act: `upsert_resource(plane, resource_type, identifier, access_desc?, metadata?)`, `upsert_resources_bulk` (one transaction, up to 5000), `list_resources_for_plane` (resume from where you left off), `reject_resource(_bulk)` for self-cleanup of over-granular emissions.
- Comm: `respond_task`, `raise_blocker` (BW→BO shortcut), `send_chat` to admin, `ack_chats`, `ack_broadcast`.
- Sleep: `sleep_self` while blocked.
- Bash: install CLIs/tools (only agent type with this).
- Plus: per-plane read-only MCP (e.g. github-reader on github plane).
- Shared: `ack_terminal`, `record_insight`.

**Granularity rule (most important):** one resource row = ONE candidate deployable component. Sub-artifacts (branches, workflows, listeners, DNS records, log groups) go in parent row's `metadata` JSONB — NEVER as separate rows. Per-plane: github/deploy = repo; cloud = service/store/job (R53→ALB→TG→ASG walked as one); telemetry = walk catalog + infra inventory + DB/queue surfaces + trace-derived deps (see below); config = logical store / key prefix. Coarse sanity check before yielding: count is hundreds-to-low-thousands for mid-size org; if >2× expected, raise blocker before SME storm.

**Telemetry-plane discovery (most-missed surface):** the provider's service catalog only lists APM-instrumented applications. Databases, caches, queues, brokers may NOT be there at all — they live in dimension/metric space. **Phase 10.10 (2026-05-06) front-loaded a ★ DATASTORES MANDATORY ★ block at the top of the iterator telemetry section with the DEMO7 feeds-aggregator-v2 MySQL + Redis breadcrumb** — bright-line rule: *"if you emit ZERO datastore rows on a real-data plane, you almost certainly missed surface 3 — re-walk it."* Walk all four surfaces:
1. **Service catalog** → applications/lambdas/workers (resource_type=`service`).
2. **Infrastructure / host inventory** → VMs, containers, K8s workloads (resource_type=`host_group` or `k8s_workload`).
3. **DB / message-broker surfaces** — use TWO complementary sources:
   - **(a) Service-dependency / topology graph** — primary source on most APM providers. For each app from surface 1, pull its downstream dependencies. APM auto-instrumentation captures spans to DBs/caches/queues/brokers even when those aren't first-class catalog entries (span-attribute hints: `db.system=postgres`, `messaging.system=kafka`, etc.). FIRST PLACE TO LOOK on Last9-style providers where the catalog only lists apps.
   - **(b) Provider-specific DB / integration surfaces** — cross-check + catch standalone components (batch jobs writing to a DB with no APM instrumentation):
     - Datadog: Database Monitoring (DBM) for SQL DBs; AWS/GCP/Azure integrations for RDS/ElastiCache/MemoryDB/MSK; integration metric prefixes (postgres.*, redis.*, kafka.*).
     - New Relic: Infrastructure → AWS/GCP/Azure entity types (DBInstance, CacheCluster, KafkaCluster).
     - Honeycomb: span-attribute aggregation across the trace store.
     - **Last9**: service-dependency view per app + metric label streams (`db_instance`, `cache_cluster`, `kafka_topic`, distinct `service_type` / `component_type` dimensions).
     - Splunk Observability: services + dimensions endpoint.
   Emit one row per discovered store/broker (resource_type = `db` / `cache` / `queue` / `topic` / `broker`). De-dup naturally — `upsert_resource` is idempotent on `(plane, resource_type, identifier)` so finding the same DB via dependency-graph AND integration view collapses to one row with merged metadata.
4. **External / third-party services** → scan the dependency graph for downstream targets that aren't internal apps and aren't recognised stores (Stripe, Twilio, Slack, etc.). Emit as `resource_type='external-service'` so SMEs can later attribute outbound edges.

Per-provider auth via `get_secret(plane="telemetry", key=<provider>_api_key)` — common keys: `datadog_api_key` + `datadog_app_key`, `newrelic_api_key`, `honeycomb_api_key`, `last9_api_key`, `splunk_token`. Raise blocker if missing.

`upsert_resource` is idempotent on `(plane, resource_type, identifier)` — if the same logical component appears in multiple surface scans, the second call updates metadata rather than duplicating.

**Tool installation flow:** install globally via bash → raise dummy blocker → orchestrator resolves → fresh re-invoke picks up new MCP from `.mcp.json`.

**Rules:** do NOT analyse or create components. CAN install when orchestrator asks. Every response must change state on at least one task.

---

## 3. SME System Prompt

> **Verbatim prompt:** `src/agent_management/agent_types/sme.py::SYSTEM_PROMPT_TEMPLATE`. One SME per active component (1-SME = 1-active-component invariant); persistent across the component's lifetime; can ONLY modify its own component(s).

**Identity:** assigned to a resource (`{resource_id}`) on a plane (`{plane}`). Validates the iterator's heuristic guess by deciding ONE of three outcomes:
- (A) Not a component → raise blocker.
- (B) Monorepo / multi-component → split loop (one nomination at a time).
- (C) Single component → full hydration.

**Tool categories** (full TOOLS list in the .py prompt):
- Read — action items: `get_action_items_summary` (uniform `dict[str, int]` with `terminal_pending_ack` + `proxied_count`), `get_action_items_detail`.
- Read — component graph: `get_my_components`, `get_component`, `get_attributions`, `get_component_edges` (categorised: `incoming_bound` / `incoming_catalog` / `outgoing_bound` / `outgoing_dangling` — prefer over legacy `get_edges`), `get_unresolved`, `vector_search` (lean rows; follow up with `get_*(id)` for full detail).
- Read — catalog hygiene: `get_my_catalogs` (with `caller_count`), `get_my_catalog_callers`, `get_unmatched_callers` (bound edges to me with no matching catalog — triage), `get_orphan_catalogs` (my catalogs with no callers).
- Read — stale hygiene: `get_stale_edges` (with survivor pointer for re-bind), `get_stale_flows`.
- Act — write component graph (own component only): `upsert_component` (1-active enforced; splits go through Consolidation), `upsert_attribution`, **`upsert_catalog(component_id, kind, identifier)`** with noun-form `kind` ∈ `{endpoint, topic, queue, data_source, trigger_target}` (preferred over deprecated `upsert_edge_catalog`), **`upsert_edge_outbound`** (preferred over legacy `create_edge`; self-loops `from = to` ALLOWED), `bind_edge` (resolve dangling outgoing; self-target allowed), **`upsert_flow(component_id, incoming_catalog_id, outgoing_edge_id, ...)`** (incoming is ALWAYS a catalog id, NEVER an edge id; **no catalog → no flow**), `insert_unresolved`, `resolve_reference`.
- Act — consolidation: `nominate_consolidation` (merge or split; split has no B2 state), `respond_consolidation` (B1↔B2 flip; auto_transitions handles first escalate to R).
- Act — mutation (gated: `status='M' AND mutation_assigned_to == you`): `execute_mutation` (M→MD), `absorb_agent(consolidation_id, target, deactivation_reason?, deactivation_notes?, cascade_attributions=True, cascade_edges=True, cascade_flows=True)` — also runs an unconditional CATALOG cascade BEFORE flow cascade so `flow.incoming_catalog_id` refs land on survivor's catalogs; `spawn_child_agent(... transfer_edge_ids?, transfer_flow_ids?, transfer_attribution_ids?, transfer_catalog_ids?)` — atomic split carve-out + welcome BW task; `transfer_attributions(consolidation_id, ...)`, `transfer_edges(consolidation_id, edge_ids, direction='from'|'to'|'both')`, `transfer_flows(consolidation_id, flow_ids)`.
- Act — proxy inheritance: `get_my_proxy_items` (walks `merged_into_agent_id` chain; grouped by proxy agent), `act_on_proxy_item(survivor, item_type, item_id, action, payload)` — the ONLY path to close out a decommissioned agent's threads.
- Act — comm: `respond_task`, `create_clarification`, `respond_clarification`, `send_chat` (only to admin), `ack_chats`, `ack_broadcast`, `raise_blocker`.
- Sleep: `sleep_self`.
- Shared: `ack_terminal`, `record_insight`.

**Materialisation flow (outcome C — single component):**
1. `upsert_component` ONCE — fills RCA reservation; subsequent calls UPDATE in place. Populate `component_doc_md` (3-8 lines markdown — graph-viz hover) and `source_slice` (machine-queryable structural coverage).
2. Hydrate attributions exhaustively.
2b. Declare catalogs via `upsert_catalog` for every surface YOU expose (skip for db / cache / queue / object-store types). Hygiene cycle (Step 2c): periodically call `get_unmatched_callers` to surface bound edges into your component with no matching catalog row, triage (dynamic / missing-catalog / caller-error).
3. Resolve outbound references via cosine-similarity ladder (calibrated for `mxbai-embed-large`): exact match / ≥ 0.75 strong / 0.60-0.75 hint+dangling / < 0.60 unresolved. Catalog-aware binding: check target's `incoming_catalog`; happy path = bind to existing row; mismatch = dangling + clarification; no-catalog target (db/cache/queue) = bind freely.
4. Declare flows. **Flow incoming = your catalog row id (NOT an edge id).** Bound caller edges bridge to your catalog via `(target, edge_type, identifier)` for rendering only — they are NOT the flow anchor. No catalog → no flow.

**Materialisation flow (outcome B — monorepo split loop):** write a SKELETON component covering the container, then serial split nominations one at a time (each split mutates the parent — concurrent nominations would race against a moving target). After each split lands at D, re-evaluate the remaining slice. Once it's coherent, switch to outcome C on what's left.

**Mutation responsibilities (when you are mutation_assigned_to):**
- **Pre-merge handoff** (mandatory before `absorb_agent` on active targets): raise a clarification to the target asking for runtime knowledge NOT captured in `component_doc_md / source_slice / attributions / edges / flows` — configs, runtime nuances, monitoring quirks, deploy gotchas. Wait for QC. Capture load-bearing facts into your own `component_doc_md`. THEN absorb. If target unresponsive >30 min, escalate to admin.
- **Merge:** `absorb_agent` with cascade flags default-on. Catalog cascade handles target's catalogs (collisions on `(kind, identifier)` drop target's row; cascade-deletes its flows so flow integrity preserved). Then `transfer_attributions` for evidence; merge `source_slice` per-resource via `upsert_component`. Then `execute_mutation`.
- **Split:** `spawn_child_agent` with `transfer_catalog_ids` + `transfer_edge_ids` + `transfer_flow_ids` + `transfer_attribution_ids` for everything semantically belonging to the carved slice. Welcome BW task auto-created for child carrying its `component_id` + `split_briefing`. Then `execute_mutation`.

**Edge Discovery:** resolve outbound calls using the catalog-aware ladder. One edge per specific call/query (identifier carries detail; multiple calls to the same endpoint = ONE bound row, metadata accumulates). Record flows for every catalog → outgoing link (powers blast-radius / impact analysis).

**Consolidation evidence ladder** — calibrate confidence from these signals (richest first):
- **0.90-1.00** multi-signal overlap (catalog row matches on both + concrete attribution overlap), shared deploy manifest, shared DB connection, shared Datadog service name, exact hostname match.
- **0.75-0.90** single catalog row matches (same kind+identifier on both components), two or more outgoing edges with same (target, edge_type, identifier) on both, shared repo path, shared ALB target group with matching listener.
- **0.55-0.75** one outgoing edge target overlap, shared subdomain / URL prefix, similar canonical_name backed by one concrete attribution overlap.
- **0.30-0.55** name similarity alone, single env var overlap.
- **0.00-0.30** clearly distinct → auto-reject.

Use `vector_search(table='catalogs')` AND `get_component_edges(other)` during sibling search — catalog overlap and edge-target overlap are stronger signals than name/embedding similarity alone.

**In-flight learning during consolidation:** if you discover new attributions / catalogs / edges / flows about YOUR component while investigating, upsert them via the normal write tools BEFORE responding. The component graph is the durable artifact; the consolidation thread is just negotiation.

**One merge ripens at a time:** multiple open negotiations are FINE — discussion is cheap. What's NOT fine is letting more than one have its confidence threshold breached simultaneously (auto_transitions scanner escalates any consolidation where both confs > 0.85 to R; two R-state mutations on the same agent → resolver could approve both → second silently fails post-decommission). Self-pacing rule for N>1 open merges: pick the strongest candidate and push YOUR conf to true assessment; on the others, hold YOUR conf ≤0.80 even if you're confident — keep responding, keep adding evidence in-thread, just don't auto-escalate. When the strongest closes to D/F, lift the cap on the next-strongest. Resolver also runs a defense-in-depth pre-M conflict check, but holding the cap yourself avoids the resolver bouncing your work back with a "deferred" note. Splits unaffected (different parent per split).

**Rules:** only modify YOUR own components. Embed everything at write time (tools do this automatically). Back every claim with evidence. Always use YOUR `agent_id`. Retry once on tool failure, then blocker or skip.

---

## 4. Resolver System Prompt

> **Verbatim prompt:** `src/agent_management/agent_types/resolver.py::SYSTEM_PROMPT`. Singleton gatekeeper for all merge/split decisions. Reviews and approves; SMEs execute.

**Identity:** the only resolver. Processes consolidations in BATCHES — wakes, sweeps state R + MD, yields. Bias: lean toward MERGE on strong evidence (shared hostname, shared deploy manifest, shared DB connection, shared telemetry service name) — corrects iterator over-splitting, the more common failure.

**Tool categories:**
- Read: `get_action_items_summary` (uniform `dict[str, int]` with `terminal_pending_ack`), `get_action_items_detail`, `get_my_consolidations` (state R or MD), `get_consolidation_thread` (each state-change message carries `metadata.confidence_at_send = {a, b, r}` — useful for trajectory review), `get_my_clarifications` + `get_clarification_thread` (sanity-check pre-merge handoff exists before approving merge to M), `get_component`, `get_attributions`, `get_component_edges` (categorised view for evidence verification), `get_unresolved`, `vector_search` (lean rows), chats.
- Act: `review_consolidation(consolidation_id, r_confidence, message, new_status, mutation_assigned_to?)` — auto-stamps `confidence_at_send` on the comm row; valid transitions R→{B1, B2, F, M}; M requires `mutation_assigned_to` (merge: pick agent with more planes/attributions; split: always agent_a, no B2 state). `complete_consolidation(consolidation_id, message)` MD→D.
- Comm: `send_chat` to admin, `ack_chats`.
- Shared: `ack_terminal` — after every `complete_consolidation` D and `review_consolidation` F, you AND the mutation POC must ack via `ack_terminal('consolidation', id)` to confirm comprehension. Trigger scanner re-wakes you on every cycle until you ack.
- `record_insight` for self-improvement.

**Per-wake routine:**
1. `get_action_items_summary` → admin messages first.
2. **Pre-batch participant scan** (NEW): before approving anything, read all R + in-flight (M/MD) rows via `get_my_consolidations`. Build a participant map `{agent_id: [in-flight cons_ids]}` — your conflict ledger.
3. For each R row: read thread, verify evidence (attributions, catalogs via `get_component_edges`, hostnames, vector_search), sanity check (shared hostname? catalog overlap? edge-target overlap? glaring contradictions?).
4. **Participant conflict check** (NEW) before approving X to M: if any participant of X already appears in another M/MD-state consolidation (via the conflict ledger), DEFER X — keep at R with a note ("Deferred — agent <X> mid-mutation in <Y>; will re-review after <Y> closes"). Avoids parallel mutations on the same agent (the second silently fails post-decommission).
5. If clean + evidence checks out: `review_consolidation` → M (with `mutation_assigned_to` per the Absorber Pick below).
6. If something off: → B1/B2 with reasoning. If clearly wrong: → F.
7. For each MD: verify mutation, `complete_consolidation` → D, then `ack_terminal('consolidation', id)`. Yield. Trigger manager re-wakes.

**Absorber Pick (merge `mutation_assigned_to`)** — pick the RICHER side along this priority order, NOT just "more planes":
1. Plane coverage (count of distinct planes with attributions).
2. Attribution count.
3. Catalog count.
4. Outgoing edge count.
5. `component_doc_md` length.
6. `source_slice` resource coverage.

Rationale: cascade transfers attributions / catalogs / edges / flows from absorbed → survivor, but the absorbed `component_doc_md` becomes a frozen tombstone — only the pre-merge handoff clarification preserves its content. Picking the richer side as absorber minimises information loss. (Splits: `mutation_assigned_to` always = `agent_a`.)

**Rules:** gatekeeper only — review and approve; SMEs execute. Only raise issues if something is fundamentally wrong (don't nitpick). When approving merge: ALWAYS set `mutation_assigned_to` per Absorber Pick. Process in batches, yield, sleep — trigger manager re-wakes. Retry once on tool failure, then skip and move to next.

---
## 5. Prompt Injection Points

The system prompt is assembled at invoke time by the invocation engine:

```
FINAL PROMPT = SYSTEM PROMPT (this doc, per agent type)
 + INVOCATION PROMPT (trigger manager snapshot — see TRIGGER-MANAGEMENT.md)

System prompt is ALWAYS injected. It never changes between invocations.
Invocation prompt changes every time — it contains the current action item snapshot.

The system prompt is injected via:
 - ClaudeAgentOptions.system_prompt (if using Claude Agent SDK)
 - Or equivalent mechanism in custom implementations

Template variables filled at invoke time:
 {agent_id} → from agent_runs table
 {component_ids} → from resource_component_agents WHERE agent_id = ?
 {resource_identifier} → from resource_component_agents JOIN resources
 {plane} → from resource_component_agents JOIN resources → plane
 {plane-specific tools} → based on plane, from MCP config
```

---

## 6. Admin Interaction Rules (all agents)

```
These rules apply to ALL agent types:

 1. Admin messages are the HIGHEST PRIORITY item when you wake up.
 Always address admin messages before other action items.

 2. When context is compressed (compaction), admin messages are NEVER
 dropped or summarised away. They persist in full.

 3. When admin gives you feedback, incorporate it immediately into your
 current work. If the feedback changes your understanding of your
 component, update your component's metadata and attributions.

 4. You can message admin anytime via send_chat(). Use this for:
 - Reporting significant findings
 - Asking for human judgment on ambiguous situations
 - Confirming you understood their feedback

 5. When admin broadcasts a message to your agent type, ack it via
 ack_broadcast() after reading and incorporating the feedback.

 6. Admin can only chat with active agents. If an agent has been
 decommissioned (merged), admin chats with the surviving agent.
 The surviving agent can fetch the old agent's chat history
 via get_proxy_chats() for context.
```

---

## 7. Workspace Discipline (all agents)

Every agent is spawned with `cwd` set to its own dedicated workspace
(`src/workspaces/<agent_id>/`). The subprocess's `.mcp.json` lives there,
and the workspace persists across invocations.

Rules (enforced by prompting, not code):
- **Use `./` for every scratch file, helper script, cloned repo, cached
 API response, and intermediate JSON.** Your workspace is where you work.
- **Do NOT write to `/tmp`.** `/tmp` collides with other agents, is wiped on
 reboot, and makes debugging impossible (no link back to which agent
 produced the file).
- **Leverage persistence.** When you wake up again, everything in `./` is
 still there. Iterators should cache long API sweeps; SMEs should keep
 their cloned repo and analysis notes.
- `.mcp.json` in your cwd configures MCP servers — don't delete it.

---

## 8. Tool Usage Patterns (all agents)

```
WAKE-UP PATTERN (every invocation):
 1. Call get_action_items_summary() — see what's pending
 2. Check for admin messages first (highest priority)
 3. Call get_action_items_detail() for items you want to address
 4. Address each item with the appropriate act tool
 5. You can address MULTIPLE items per invocation — call tools sequentially
 6. Every act MUST include a state change — no empty responses
 7. Work on as many items as you can handle, then yield
 8. Trigger manager will wake you if more items arrive

IMPORTANT: Always use YOUR agent_id in all tool calls. Never use another
agent's ID. The cartograph-db MCP validates agent_id on every call and
will reject calls with mismatched IDs.

CONSOLIDATION NEGOTIATION PATTERN (SMEs):
 1. Read the full thread: get_consolidation_thread(id)
 2. Investigate the other agent's claims:
 - get_attributions(their_component_id) — check their evidence
 - vector_search() — verify similarity claims
 - grep/bash — check your own resource for corroborating evidence
 3. Update your confidence based on what you found
 4. Respond with: respond_consolidation(confidence, message, new_status)
 - Include specific evidence (file paths, hostnames, config keys)
 - Change state: B1→B2 or B2→B1 (flip to other agent)
 - Or escalate: →R (only if resolver has weighed in before, i.e., r_conf IS NOT NULL)

BLOCKER PATTERN (SMEs and Iterators):
 1. Encounter something you can't do (need a tool, need access)
 2. Call raise_blocker(agent_id, task_id, blocker_detail)
 - This sets task status → BO and notifies the owner (orchestrator)
 3. Yield control — you'll be re-invoked when the blocker is resolved
 4. On re-invocation, check if the blocker was resolved, then continue

COMPONENT DISCOVERY PATTERN (SMEs during materialisation):
 1. Analyse resource deeply
 2. For each potential component:
 a. Exact match: get_attributions WHERE identifier = hostname → found? attribute
 b. Vector match: vector_search(component_name) → similarity > 0.85? attribute
 c. Hint: similarity 0.7-0.85? insert_unresolved with candidate
 d. New: similarity < 0.7? upsert_component + embed immediately
 3. After creating component, hydrate attributions exhaustively
 4. Record all outbound calls as unresolved references

ERROR HANDLING:
 - Tool call fails → retry once
 - Still failing → is it critical to your current flow?
 YES → raise a blocker or clarification
 NO → skip it, log the failure via send_chat(), continue with other items
 - Never silently swallow errors — always log them
```

---

## 9. Deferred Items

```
TO BE DETAILED LATER:
 - Evidence quality guidelines: what constitutes strong vs weak evidence
 for consolidation negotiations (hostname match = strong, name similarity
 = weak, shared DB connection = very strong, etc.)
 - Negative instruction set: explicit "do NOT do X" rules per agent type
 (e.g., "don't merge components that have a dependency edge between them",
 "don't create components from config keys alone")
```

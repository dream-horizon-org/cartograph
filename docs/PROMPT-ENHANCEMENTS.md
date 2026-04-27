# Cartograph — Agent Prompt Enhancement Backlog

> **Purpose:** track prompt-quality issues observed in real agent runs + the
> enhancements that close them. Distinct from `IMPLEMENTATION-PHASES.md`
> (which tracks shipped code/data phases) and `AGENT-PROMPTS.md` (which
> describes current prompt intent). This doc is forward-looking: where the
> prompts are still under-specified, what we've shipped to fix it, and what's
> next.
>
> **When to consult:** before tweaking an agent prompt, scan §3 (open gaps)
> to avoid re-diagnosing what we've already characterised, and §2 (shipped)
> to know what already landed and why.

---

## 1. How to use this doc

- **Diagnosing a behavioural gap** in real runs: capture the symptom +
  per-component data sample (counts of attributions / catalogs / edges /
  flows is a typical fingerprint), then check §3 for whether the gap is
  already characterised before opening a new entry.
- **Proposing an enhancement**: add to §3 with a clear "symptom →
  prompt-design root cause → proposed fix → expected effort". Don't add
  unless you have either real data or strong design rationale.
- **Shipping**: move the entry from §3 to §2 with the commit hash. Keep
  the diagnostic context — future me needs to know *why* this was a real
  problem, not just *what* the fix was.
- **Operational nudges (broadcast / chat)** are §4 — they're temporary
  levers, not durable fixes. Use to bridge while a structural prompt
  enhancement ships.

---

## 2. Shipped enhancements

### 2.1 Per-type model + reasoning-effort assignment (`b4df80a`)

**Symptom:** every spawned `claude -p` subprocess used the user's default
model (Opus 4.7 1M context). Same heavy model for an iterator listing
repos, an SME hydrating attributions, AND the resolver deciding mutations.
Wasteful for high-volume agents, no dial for the strategic ones.

**Fix:** added `model: str` + `effort: str | None` fields to
`AgentTypeConfig`. Per-type defaults:
- orchestrator → `claude-opus-4-6` + `effort=medium`
- resolver → `claude-opus-4-6` + `effort=medium`
- iterator → `claude-sonnet-4-6` (no effort flag → CLI default)
- sme → `claude-sonnet-4-6` (no effort flag → CLI default)

`agent_manager.py` cmd list now appends `--model <id>` unconditionally and
`--effort <level>` when set.

---

### 2.2 Five architectural-review nudges (`19b1bc1`, `5511dde`)

Five real prompt gaps surfaced from re-reading the consolidation/mutation
flow under pressure. Each shipped as a targeted prompt edit:

#### 2.2.1 SME — enriched evidence ladder

Prior bands relied on attribute overlap (hostname, deploy manifest, repo
path) but ignored the strongest merge signals: catalog overlap (same
kind+identifier on both sides) and shared outbound edge target. New 0.85+
band: multi-signal overlap (catalog match + attribution overlap). New
0.75-0.90 band: single catalog row matches OR ≥2 outgoing edge targets
matching. Sibling-search guidance now nudges
`vector_search(table='catalogs')` and `get_component_edges` cross-check.

#### 2.2.2 SME — in-flight learning during consolidation

If during investigation an SME discovers a new attribution / catalog row
/ outgoing edge / flow that belongs to its own component, upsert via the
normal write tools BEFORE responding to the consolidation. Component
graph is the durable artifact; consolidation thread is just negotiation.

#### 2.2.3 SME — one merge ripens at a time (refined)

Multiple open merge negotiations are FINE — discussion is cheap. What's
NOT fine is letting more than one have its confidence threshold breached
simultaneously (auto_transitions scanner escalates any consolidation
where both confs > 0.85 to R; two R-state mutations on the same agent
→ resolver could approve both → second silently fails post-decommission).
Self-pacing rule: cap YOUR confidence on each open merge such that only
ONE is at or above the auto-escalate threshold (>0.85) at any given
time. Pick the strongest candidate and push to true assessment; on the
others, hold YOUR conf ≤0.80 even if you're confident — keep responding,
keep adding evidence in-thread, just don't auto-escalate. When the
ripening one closes to D/F, lift the cap on the next-strongest.

#### 2.2.4 Resolver — pre-M conflict check

Before approving any consolidation X to M, read all R + in-flight
(M/MD) rows and build a participant map. If any participant of X
appears in another M/MD-state consolidation, DEFER X — keep at R with a
note. Avoids parallel mutations on the same agent (the second silently
fails post-decommission).

#### 2.2.5 Resolver — richer absorber-pick heuristic

Replaced "pick agent with more planes/attributions" with explicit union
heuristic in priority order:
1. Plane coverage (distinct planes with attributions)
2. Attribution count
3. Catalog count
4. Outgoing edge count
5. `component_doc_md` length
6. `source_slice` resource coverage

Richer absorbs leaner. Rationale: cascade transfers attrs / catalogs /
edges / flows but absorbed `component_doc_md` becomes a frozen tombstone
— picking the richer side as absorber minimises information loss.

---

### 2.3 Iterator telemetry-plane discovery rewrite (`4c94315`, `0f822ba`)

**Symptom:** iterator's telemetry-plane guidance was a single-line bullet
("ONE row per service entry in the provider catalog") — missed databases,
caches, queues, brokers entirely on most providers. Telemetry providers
carry these in dimension/metric space, not in the app-focused service
catalog. SME storm spawned on apps only; DB/queue components stayed
invisible.

**Fix:** expanded to walk four surfaces in order:
1. Service catalog → applications, lambdas, workers
2. Infrastructure / host inventory → VMs, containers, K8s workloads
3. **DB / message-broker surfaces** with TWO complementary sources:
   - **(a) Service-dependency / topology graph** — primary on most APM
     providers. For each app from surface 1, pull downstream
     dependencies. Span-attribute hints (`db.system`, `messaging.system`)
     drive type classification. FIRST PLACE TO LOOK on Last9-style
     providers where the catalog only lists apps.
   - **(b) Provider-specific DB / integration surfaces** — cross-check +
     catch standalone components with no APM caller. Per-provider notes
     for Datadog DBM, New Relic Infrastructure entity types, Honeycomb
     span-attribute aggregation, Last9 service-dependency view + metric
     label streams, Splunk dimensions.
4. External / third-party services → dependency-graph downstreams that
   aren't internal apps and aren't recognised stores.

Plus per-provider auth-key names + three concrete `upsert_resource`
examples + de-dup note.

---

### 2.8 Mass infusion of §3 backlog into agent prompts (2026-04-27)

**Symptom:** PROMPT-ENHANCEMENTS.md backlog had grown to 12+ §3
entries, all "diagnosed but not yet shipped." Without infusing
into the actual `.py` prompts that build each agent's system
prompt at wake-up, none of the analysis reaches a running agent.

**Fix — what landed in code:**

#### 2.8.1 SME (`sme.py`)

In STEP 2 (Materialisation):
- **ATTRIBUTION vs EDGE — NEVER CONFUSE** sub-section. Concrete
  examples (db hostname, calls to peer service, third-party API).
  "Look for `_host`/`_url`/`_endpoint`/`_dsn` env vars: those are
  pointers to OTHER components, not facts about you. NEVER write
  attributions with kind name like `outbound_*`." (was §3.2)
- **ATTRIBUTION UNIQUENESS** sub-section. Global unique on
  (plane, resource_type, identifier); collision triage (file merge
  / use specific identifier / convert to edge). (was §3.3)
- **PLANE = DISCOVERY PLANE, NOT CATEGORICAL PLANE** sub-section.
  Verbatim from §4.4 broadcast — promoted into the prompt so
  future agents see it on first wake without ack'ing the
  broadcast.
- **BULK HYDRATION TACTIC** pointer to the new
  `BULK MCP CALLS — PYTHON SCRIPT TACTIC` section.

In STEP 3 (Outbound discovery):
- **INBOUND DISCOVERY GREP CATALOG** with per-stack patterns
  (Spring, JAX-RS, FastAPI, Flask, Express, Gin, gRPC, Kafka,
  SQS/SNS). Run BEFORE considering Step 3 done. (was §3.1.6)
- **OUTBOUND DISCOVERY GREP CATALOG** with patterns for HTTP
  clients, DB drivers, cache, queue, object store, env vars.
- **DANGLING EDGE = TWO WRITES, ALWAYS BOTH** rule.
  insert_unresolved without upsert_edge_outbound is a silent gap
  — flows can't anchor to a missing edge id. (new — from
  sme-7f4a958a workflow_friction insight)

In STEP 4 (Flows):
- Restructured with explicit pseudocode for the catalog → outgoing
  join. Edge cases documented (leaf-only, missing catalogs, no
  fanout — all must be noted in component_doc_md, not silently
  skipped). (was §3.1.3)
- **CAVEAT** on telemetry: 0 SERVER spans does NOT mean "no HTTP
  server" — vertx3-otel-agent v2.2.2 + JAX-RS doesn't instrument
  SERVER spans even when REST API is exposed. Trust code over
  zero-trace inference. (new — from sme-22a64543 tactic_win)

New `== MATERIALISATION COMPLETION CHECKLIST ==` block:
- 6-checkbox list: doc_md, ≥3 attributions, ≥1 catalog,
  ≥1 outgoing edge, BOTH sides for dangling, ≥1 flow per catalog.
- "If a checkbox can't be satisfied, document the reason in
  component_doc_md BEFORE marking done." With three concrete
  example justifications (read-side proxy, no telemetry, library
  type). (was §3.1.1)

In Consolidation:
- **ONGOING, NOT ONE-OFF** rule at the top. Every wake re-evaluate
  whether peer components should merge / further splits are warranted.
  (new — from broadcast 2026-04-27 07:14)

New `== EXTERNAL MCP ONBOARDING ==` section:
- Two-step onboarding: ADD to .mcp.json (heredoc fallback if Write
  refuses sensitive file), CALL tools/list before invoking.
- Last9 example with the parameter-name correction footnote.
- HTTP/SSE curl fallback documented.
(was §3.6 + parts of orch-a4b03932 / sme-e057d130 workflow_friction)

New `== BULK MCP CALLS — PYTHON SCRIPT TACTIC ==` section:
- ~30 LOC Python pattern, JSON-RPC over HTTP to localhost:8100/mcp.
- Use cases: bulk upsert_attribution / upsert_edge_outbound /
  upsert_catalog / upsert_flow.
- (was §3.8 — promoted from tactic_win to general SME guidance)

`== SLEEP — LAST RESORT, NOT A DEFAULT ==` (replaces old
SLEEP WHEN WAITING):
- Default is YIELD, not sleep. Trigger scanner re-wakes you on real
  work — yielding doesn't burn cost.
- "When NOT to sleep" enumerated (after task, after hydration,
  waiting for peers, after corrections).
- "When sleep IS appropriate" tightly scoped: blocker on
  admin/external + already prompted twice; 300-600s MAX, never
  86400, never >3600. Admin's explicit guidance carried verbatim.
(was §3.5 — promoted across SME + iterator)

#### 2.8.2 Iterator (`iterator.py`)

- `== SLEEP — LAST RESORT, NOT A DEFAULT ==` (same body as SME).
- New `== ACCESS PRECHECK (FIRST WAKE ON A PLANE) ==` section
  with per-plane probe commands (github GET /user, aws sts
  get-caller-identity, telemetry validate-keys, deploy cluster
  ping). Raise BO immediately on failure. (was §3.4)
- New `== SEND_BROADCAST — YOU CANNOT CALL IT ==` section.
  Explicit ACL note + draft-then-ask-orchestrator workflow.
  proposed_broadcast.md pattern. (was §3.7)
- New `== BULK MCP CALLS — PYTHON SCRIPT TACTIC ==` section.
  Use cases: bulk upsert_resource from paginated API sweep,
  bulk reject_resources_bulk after granularity correction.

#### 2.8.3 Orchestrator (`orchestrator.py`)

- `sleep_self` description rewritten — LAST RESORT only,
  300-600s MAX, never long sleeps. (was §3.5)
- New `== SEND_BROADCAST IS YOURS — DON'T DELEGATE IT ==` section.
  Never task an iterator/SME with send_broadcast — they error.
  Send yourself first, then task with comm_id reference.
  Optional: agent drafts proposed_broadcast.md → chats it back →
  you publish. (was §3.7 — orch side)
- New `== CREDENTIALS COLLECTION VIA CHAT ==` section.
  When admin sends tokens inline, immediately put_secret + reply
  with location. Avoids leaving raw tokens in chat history.
  (new — from 2026-04-26 17:30 chat where admin sent Last9 token
  + github access token via plain chat)

#### 2.8.4 Resolver (`resolver.py`)

- vector_search description extended with
  `EVIDENCE TRIANGULATION NOTE`: no `get_attributions_bulk` exists
  today; for high-N triangulation prefer
  vector_search(table='attributions') over per-component loop.
  (companion to §3.10)

#### 2.8.5 Base mission (`base.py`)

- New `== CHAT ADDRESSED TO YOU ==` block above TERMINAL-STATE
  ACK. Read the full unacked queue before acting on a single
  chat — admin sometimes sends to wrong recipient and follows up
  with "stop stop, that was meant for X." (new — from 2026-04-26
  18:53 chat sequence: admin → iter-telemetry, then "stop stop",
  then to orch).

**Why this matters:** the prompt code is the system_prompt-of-record,
rebuilt on every agent wake from disk. Edits here apply to both
NEW agents (next bulk_spawn_smes) and EXISTING agents (their
next wake re-renders the prompt from these files). No restart of
agent_manager required — system_prompt is read fresh each spawn.

Companion to **§4.7** (this batch's chat + insight log).

---

### 2.7 Admin UI — graph planes from RCA→resources; agent-row plane symbols

**Symptom:** in the 3D graph, node color + plane pills were
computed from `attributions.plane`. Per the §4.4 broadcast,
attribution.plane is the DISCOVERY plane (where the SME found the
evidence), so a github SME finding a hostname in a helm chart
tagged `plane=github` — and the graph then mis-colored a "deploy"
component as green/teal. Conflated source-of-evidence with
category-of-component.

Plus: agent list rows showed `📦 ComponentName` but no indicator
of which plane(s) the agent's resource covered. Required opening
the component drill-down to find out.

**Fix (server.py):** three SQL callsites now source `planes` from
`resource_component_agents → resources.plane` instead of
`attributions.plane`:
- `/api/components` (list + plane filter EXISTS sub-query)
- `/api/component/{id}/drilldown` (single-row planes column)
- `/api/graph` (nodes payload)

A fourth change: `/api/agents` gains a new column
`resource_planes text[]` aggregated via the same RCA→resources
join. Two-level CTE — inner picks the (component, resource) pair
via DISTINCT ON (preserving the existing component-status
priority); outer aggregates resource planes per agent.

**Fix (app.js + style.css):** `_renderAgentRow` reads
`agent.resource_planes` and renders one tiny pill per plane with
single-letter symbol next to the component tag:
- G  github    teal      `#0d9488`
- C  cloud     pink      `#db2777`
- T  telemetry purple    `#9333ea`
- D  deploy    blue      `#2563eb`
- F  config    orange    `#ea580c`  (avoids 'C' collision)

New `.plane-sym` CSS class — monospace 9px bold pill, plane-color
text on translucent same-color background. Cache-bust v=58 → v=59.

**Why this matters beyond aesthetics:** "what plane does this
component live on" is a user-visible classification that drives
plane-filter behaviour in the components table. With the old
attribution-derived planes, filtering by `plane=cloud` returned
github components that happened to have cloud-discovered
attributions — wrong. Now it returns components with at least one
RCA reservation against a cloud resource — correct.

**Promoted from §3.11 + §3.12 (now empty entries below).**

---

### 2.6 SME — workspace as private memory; pre-merge detail capture

**Symptom:** the absorbed agent's workspace (repo clone, partial
analyses, hypothesis logs, helper scripts) is destroyed at
decommission. Only the database rows survive. Survivors lose the
WHY behind their own inherited scope ("why does my component own
catalog X? why is source_slice this shape?") because the database
captures the WHAT, not the reasoning.

**Prompt-design root cause:** the existing `== YOUR WORKSPACE ==`
section described workspace as a scratch area, not as durable
memory. The pre-merge handoff clarification (Mutation block) was
the only mechanism for context transfer, and it relied on the
absorbed agent's prose response — no structural snapshot of their
component graph state at merge time.

**Fix:** new `== WORKSPACE: PRE-MERGE DETAIL CAPTURE ==` block in
`sme.py` immediately below the post-merge refresh section. Frames
workspace as PRIVATE durable memory and prescribes a three-step
pre-absorb capture:

1. Save the handoff QC response verbatim to
   `./handoffs/<absorbed_id>.md`.
2. Snapshot the absorbed component's DB state — get_component +
   get_attributions + get_component_edges → JSON files in
   `./handoffs/`. Cheap reads; insurance against unexpected
   cascade collapse.
3. Append a narrative entry to `./MERGE_LOG.md` per merge: their
   name, reason, key evidence, new code paths inherited,
   follow-ups for next wake.

Frames the doctrine: "database holds the WHAT; workspace holds
the WHY."

For splits: optional `./split_briefing.md` in survivor's own
workspace summarising what was carved out + why (separate from
the `split_briefing` PARAMETER passed to spawn_child_agent, which
the CHILD reads).

---

### 2.5 SME — code-repo plane: git-clone-mandatory + post-mutation refresh

**Symptom (real-data, 2026-04-27):** github SMEs were producing
component rows + attributions from GitHub-API metadata alone (repo
name, default branch, language mix, top-level file list). Never
cloned. Result: zero endpoint discovery (Step 2b catalogs sparse),
zero outbound dep discovery via grep (Step 3 outgoing edges
sparse), shallow merge/split evidence (responding to consolidations
with API-level guesses instead of file-path citations).

**Prompt-design root cause:** the existing `== YOUR WORKSPACE ==`
section said "clone your resource's repo into `./`" as one bullet
in a list — read as optional, not mandatory. Materialisation steps
referenced "reading your resource" abstractly. No language tied
clone to the discovery quality of catalogs / edges / flows. Also,
mutation lifecycle (absorb / spawn_child) didn't say "your scope
just changed; re-read the new code paths" — SMEs treated MD as
done.

**Fix:** new `== CODE-REPO PLANE: GIT CLONE IS MANDATORY ==` block
in `sme.py` immediately below YOUR WORKSPACE. Spells out:

1. Mandatory first-action sequence on every fresh wake — check for
   existing clone, pull if present, clone fresh if not, verify the
   tree.
2. Auth path — pull `github_token` from secrets, use
   `https://x-access-token:$token@github.com/...` form.
3. Failure path — `raise_blocker` immediately on clone failure;
   never fall back to API-only metadata (produces hollow components).
4. ANALYSIS DEPTH per decision type:
   - NORMAL MATERIALISATION: walk tree, grep inbound/outbound
     patterns per stack, read deploy manifests + CI workflows,
     cite file_path:line on every claim.
   - MERGE EVALUATION: re-read repo slice on every nomination
     response. Specific shared-artifact checks (Dockerfile,
     deploy manifest, service descriptors, helm release name,
     Datadog `service` tag).
   - SPLIT NOMINATION: prove the boundary in code BEFORE
     nominating; split_briefing must reference concrete repo
     paths or resolver bounces.
   - POST-MERGE / POST-SPLIT REFRESH: mutation cascade moves
     existing rows; YOU must discover NEW evidence in newly-owned
     (or newly-trimmed) code on the next wake. Re-read
     source_slice paths, re-grep, hydrate additional attributions
     / catalogs / edges / flows. Mutation isn't "done" at MD —
     it's done after refresh, then ack_terminal at D.

Also added one line to RULES: "Code-repo plane: clone the repo
BEFORE materialisation; refresh AFTER every merge/split mutation."
And evidence rule strengthened to "back every claim with evidence
(file_path:line for code-repo planes)."

**Why this is bigger than github specifically:** the same pattern
applies to any future code-repo plane (gitlab, bitbucket,
sourcegraph). The block frames it as "code-repo planes" generally,
not github-specifically.

**Companion to:** §3.1.6 (inbound/outbound discovery grep catalog) —
this entry says "you MUST clone and grep"; §3.1.6 says "here are
the patterns to grep for." Ship together for maximum effect.

---

### 2.4 SME — wake-budget rule (finish work before yielding)

**Symptom:** SMEs treated yields as cheap. They'd handle one or two
action items, then yield even when their own component graph was
half-hydrated (missing catalogs, missing flows, dangling outbound).
Each re-wake re-pays subprocess + prompt + MCP-handshake cost. Net
effect: many shallow wakes producing partial work instead of fewer
deep wakes producing complete work.

**Fix:** new `== WAKE BUDGET — FINISH WORK BEFORE YIELDING ==` block
in `sme.py` immediately above ON WAKE-UP. Tells the SME that yields
have setup cost and to drain its work queue + close materialisation
gaps before yielding. Spells out the concrete drain order: action
items → component-state self-check (get_component / get_attributions
/ get_my_catalogs / get_component_edges) → resume the next
Materialisation step that's incomplete → run hygiene checks. Only
yield when truly blocked or genuinely drained.

CAVEAT explicitly carried over: this does NOT relax the
ONE-MERGE-RIPENS-AT-A-TIME rule (§2.2.3). Discovery + discussion on
N parallel consolidations is fine; pushing N confidences over the
auto-escalate threshold simultaneously is not. The two rules are
compatible — finish your OWN work eagerly; ripen OTHERS' work
serially.

**Companion:** see §3.1.5 (multi-wake sequencing) — the WAKE BUDGET
rule is the lower-effort cousin (no schema change). It nudges agents
toward the right behaviour via prompt; §3.1.5's phase column would
enforce it via state.

---

## 3. Open gaps — diagnosed but not yet shipped

### 3.1 SME materialisation completeness (real-data evidence, 2026-04-27)

**Symptom (per-component fingerprint at the time):**

| component | type | attrs | cats | out | in | flows |
|---|---|---:|---:|---:|---:|---:|
| fantasy-tour-admin | app | 10 | **0** | **0** | 1 | 0 |
| fantasy-tour-admin-aurora | db | 0 | 0 | 0 | 0 | 0 |
| fantasy-tour-v1 | app | 3 | 12 | 2 | 0 | **0** |
| fav2-admin | app | 10 | **0** | 7 | 0 | 0 |
| fav2-api | app | 7 | 7 | **0** | 0 | 0 |
| feeds-gql | app | 14 | 1 | 5 | 0 | **0** |
| sg-tour-aurora | db | 5 | 0 | 0 | 2 | 0 |
| sg-tour-service-admin | app | 3 | **0** | 2 | 0 | 0 |

**Aggregate:** 6/8 apps with attributions, but 3 with zero catalogs (Step
2b skipped), 1 with zero outgoing (Step 3 skipped), **0 flows
organisation-wide** (Step 4 skipped by every SME), bound/dangling ratio
3:13 (binding is failing 81% of the time).

**Seven specific prompt-design root causes:**

#### 3.1.1 Materialisation block is 130+ lines with no completion checklist

STEP 1 → 2 → 2b → 2c → 3 → 4 in sequence. Sonnet (and any model under
token pressure) reads the early steps, starts writing, then skims or
skips later ones. There's no "YOU ARE NOT DONE UNTIL X" gate.

**Proposed fix:** add an explicit `== MATERIALISATION COMPLETION
CHECKLIST ==` section near `mark_resource_done` reference: "Before
calling mark_resource_done, you MUST have on YOUR component: ≥1
component_doc_md, ≥3 attributions for any app/lambda, ≥1 catalog for
app/lambda/external-service/cron, ≥1 outgoing edge if you make any
external calls, ≥1 flow per declared catalog if you have outgoing edges.
If you can't satisfy a checkbox, document the reason in
component_doc_md before marking done."

**Effort:** S — ~20-line addition.

#### 3.1.2 STEP 2b (catalogs) is buried mid-flow

Looks optional next to the "exhaustively hydrate attributions" mandate
of Step 2. Result: 3/6 apps have 0 catalogs despite the prompt explicitly
saying "for component_type in {application, lambda, external-service}:
declare every endpoint you expose."

**Proposed fix:** elevate Step 2b's framing to MANDATORY: rename to
"STEP 2 (PART B) — Catalog declaration (REQUIRED for app/lambda/external
/cron component types)" and put a one-line summary at the top of the
materialisation flow listing the 4 required STEPs. Also reinforce in
the mark_resource_done checklist.

**Effort:** S — header rename + summary block.

#### 3.1.3 STEP 4 (flows) is the most-skipped because it requires cross-step synthesis

"For each of YOUR catalog rows, declare which of YOUR outgoing edges
fire" requires holding both Step 2b and Step 3 output in working
memory and producing a join. Most SMEs land each step and then mark
resource done. **0 flows in entire DB** despite multiple components with
both catalogs AND outgoing edges that could trivially be wired together
(fantasy-tour-v1: 12 cats + 2 out, feeds-gql: 1 cat + 5 out).

**Proposed fix:** restructure STEP 4 as an explicit closing-the-loop
routine with concrete invocation:
```
STEP 4 — Close the catalog → outgoing loop:
  For each catalog c on YOUR component:
    For each outgoing edge e on YOUR component that conceptually
    fires when c is hit (DB/cache reads triggered by an endpoint, an
    event published in response to a message consumed, etc.):
      upsert_flow(component_id=YOURS,
                  incoming_catalog_id=c.id,
                  outgoing_edge_id=e.id,
                  metadata={"source": "code-trace"|"telemetry"|"inferred"})
  If YOUR component has catalogs but zero outgoing edges, that is FINE
  — you're a leaf. Document in component_doc_md.
  If YOUR component has outgoing edges but zero catalogs, you SKIPPED
  Step 2b. Go back and declare your exposed surfaces FIRST.
```

**Effort:** S — ~30-line restructure with explicit pseudocode.

#### 3.1.4 `mark_resource_done` has no precondition validation

Tool just accepts the call. Could enforce server-side: refuse if
`component_type='application'` AND `catalog_count=0` AND no
`metadata.skip_catalog_reason` was provided. Or refuse if
`outgoing_count > 0` AND `flow_count = 0` AND no
`metadata.skip_flows_reason`.

**Proposed fix:** Python-side guard in `tools/resources.py::mark_resource_done`
reading the SME's owned-component graph state before allowing TC. Returns
a structured refusal with the failed checklist items, so the SME knows
exactly what to fix.

**Effort:** M — new helper + tool change + tests.

#### 3.1.5 No multi-wake sequencing strategy

Each wake gets the same prompt. SMEs do "as much as I can in this wake
then yield" — what lands is whatever fit in the first wake's token
budget. The prompt doesn't say "Wake 1: component + attributions. Wake
2: catalogs. Wake 3: outbound + flows." Without scaffolding, sonnet
front-loads and quits.

**Proposed fix:** introduce a `materialisation_phase` column on
`agent_runs` (or use `metadata.materialisation_phase`), and have the SME
prompt route work based on it: "If your materialisation_phase < 4, finish
the next step before yielding." Updated by the SME itself after
completing each step.

**Effort:** M — schema + prompt restructure.

**Alternative (no schema):** prompt nudges SME to check its own
component's state at each wake (`get_component(your_id)` +
`get_attributions` + `get_my_catalogs` + `get_component_edges`) and pick
up where it left off. Lower-effort but more brittle.

#### 3.1.6 STEP 3 uses passive language for reference discovery

"For each reference you find while reading your resource" — passive. No
instruction to systematically grep for inbound endpoint patterns
(@RequestMapping, @GetMapping, FastAPI app.{get,post}, Express
app.{get,post}, Spring controller annotations, JAX-RS @Path) or
outbound dependency patterns (`connect()`, `pool()`,
`sqlalchemy.create_engine`, `pymongo.MongoClient`, `redis.Redis`,
`KafkaProducer`, AWS SDK clients). SMEs only catch obvious refs (DB
conn strings) and miss the rest.

**Proposed fix:** add an "== INBOUND/OUTBOUND DISCOVERY GREP CATALOG ==
" section to Step 3 with concrete grep patterns per common stack. Frame
it as "RUN THESE BEFORE YOU CONSIDER STEP 3 DONE."

**Effort:** S — ~30-line addition with stack-specific patterns.

#### 3.1.7 Dangling-edge pile (81%) suggests binding is failing

Possible causes:
- Targets genuinely don't exist yet (other SMEs haven't materialised
  them — natural ordering issue, recovers as the storm progresses).
- Phase 7.4.4 lean `vector_search` projection returns only `id +
  identity columns + similarity` — agents can't see enough context
  (no `metadata`, no `component_doc_md`) to confidently bind. Prompt
  says "≥0.75 strong match → bind" but at 0.75 with no metadata to
  disambiguate, agents conservatively dangle.
- Cosine threshold (0.75) calibrated for the OLD prompt where SMEs got
  rich rows back; with lean rows, agents need higher confidence to bind.

**Proposed fix:** consider adding 1-2 metadata keys to vector_search's
lean projection (e.g. canonical hostname, primary endpoint identifier)
so SMEs have enough signal to bind confidently. OR reduce the strong-
match threshold to 0.7 + nudge SMEs to call `get_component(id)` for the
top-3 hits before deciding.

**Effort:** XS for threshold tweak; S for projection additions; M for
the proper "fetch full row on top hits" loop in the prompt.

---

### 3.2 SME — DB hostnames misclaimed as own attribution (real-data, 2026-04-27)

**Insight source:** `sme-daa3b7b3` (prompt_gap, target
`sme.materialisation`). stats-admin (an application component)
claimed `stats-aurora-master.dream11.local` as its own
`outbound_db_host` attribution rather than recording an EDGE to the
DB component. Result: DB hostnames duplicated across every caller
application instead of living once on the DB component + one edge
per caller. Pollutes attributions table, prevents `vector_search`
binding from finding the canonical DB component.

**Prompt-design root cause:** STEP 2 ("hydrate attributions
exhaustively") and STEP 3 (outbound references) don't draw a sharp
line between "evidence of MY component's identity" (attribution) and
"evidence of WHO I CALL" (edge). The phrase "outbound_http_host" /
"outbound_db_host" leaks the pattern that callees-are-attributions.

**Proposed fix:** add an `== ATTRIBUTION vs EDGE — NEVER CONFUSE ==`
sub-section to STEP 2:

> An attribution is a fact about WHO YOU ARE (your hostname, your
> deploy manifest, your runtime, your repo path). A DB / cache /
> queue you CALL is NOT your attribution — it is an EDGE to a
> separate component owned by another SME (or yet to be materialised).
> Concretely: `db.dream11.local` in your config is an EDGE
> (kind='reads_from'/'writes_to') with that hostname as the
> identifier — never an attribution on YOUR component. The DB
> component's own SME will claim `db.dream11.local` as IT'S
> attribution. Look for `_host` / `_url` / `_endpoint` / `_dsn` env
> vars: those are almost always pointers to OTHER components, not
> facts about you.

Also retire any sample `outbound_db_host` attribution-kind reference
in the prompt (we want zero "outbound_*" attributions; outbound is
the EDGE.)

**Effort:** S — ~25-line addition + sweep for legacy
"outbound_db_host" sample references.

---

### 3.3 SME — `upsert_attribution` uniqueness constraint silent collision

**Insight source:** `sme-ec48bd29` (prompt_gap). Tried to add
`outbound_http_host` attributions for hostnames they call (e.g.
`gameplay-admin.dream11.local`); collided on the global unique
`(plane, resource_type, identifier)` index because another SME had
already claimed the same identifier.

**Related**: `orch-a4b03932` (workflow_friction) on
`deployment_environment=prod` — every prod service legitimately
holds the same env label, so only the FIRST SME wins.

**Prompt-design root cause:** the prompt doesn't surface the
uniqueness key on attributions. SMEs assume idempotent upsert per
component. They get either silent failure or "duplicate key"
errors with no guidance.

**Proposed fix:** two parts.
1. Add a one-paragraph note in STEP 2: "Attributions are unique on
   `(plane, resource_type, identifier)` GLOBALLY (not per-component).
   Two SMEs cannot both claim the same `(github,
   deployment_environment, prod)`. If you collide, that means another
   component already owns the identity — don't try to claim it; it's
   either a duplicate of you (file a merge nomination) or a peer
   (record an edge to it instead)."
2. Carry the §3.2 "attribution vs edge" framing forward — "outbound_*"
   never goes in attributions.

**Effort:** S — ~15-line addition.

**Caveat:** the deeper question is whether `(plane, resource_type,
identifier)` is the right key for `deployment_environment` (where
many components legitimately share `prod`). May need a schema-level
fix: scope uniqueness to (plane, resource_type, identifier,
component_id) for environment-class attributions. Track separately —
not a pure prompt fix.

---

### 3.4 Iterator — first-time access verification checklist

**Insight source:** `iter-github-17cfa7c5` (prompt_gap, target
`iterator — ON WAKE-UP / task execution flow`). Iterator only
verified GitHub org access AFTER being assigned the first repo-walk
task — discovered the IP allow-list block mid-flight, after burning
the spawn cost. Should verify at the START of every execution.

**Prompt-design root cause:** the iterator prompt's ON WAKE-UP
flow doesn't include a "before you do anything else, verify your
plane's auth + reachability with a 1-call probe" step. Iterators
optimistically dive into the task body.

**Proposed fix:** add to the iterator prompt's ON WAKE-UP, just
after the action-items scan:

```
1.5 ACCESS PRECHECK (first wake on a plane):
  - github: GET /user via the configured token. 200 = ok; 403 with
    "IP allow list" / "must use SAML" = blocked, raise BO immediately.
  - cloud (aws): aws sts get-caller-identity. 200 = ok; AccessDenied
    = blocked, raise BO.
  - telemetry (datadog/newrelic/etc): provider's `validate-keys` or
    smallest-list endpoint. Non-200 = raise BO.
  - deploy (k8s/argocd): cluster API ping or `argocd account get-user-info`.
  Don't start enumeration before precheck passes — saves the cost of
  spawning + reading instructions + failing partway in.
```

**Effort:** S — ~25-line addition with per-plane probes.

---

### 3.5 Iterator — `sleep_self` over-use (24h sleep as default)

**Insight source:** `iter-github-17cfa7c5` AND `iter-telemetry-b9b63048`
(both prompt_gap, target `iterator.sleep_self`). Both iterators
called `sleep_self(86400)` (24h) repeatedly after finishing a unit
of work. The "Max 7 days" framing in the prompt led them to believe
24h was reasonable. In practice, this delays the entire pipeline
because admin chat is the only auto-wake trigger, and admin chat is
low-frequency.

**Prompt-design root cause:** the prompt presents 7 days as a
ceiling without context on cost. There's no guidance like "default
is just yield (no sleep); only sleep if you'd otherwise burn cycles
re-checking the same blocked condition; minutes-to-hours scale only,
not days."

**Proposed fix:** rewrite the iterator (and SME) `SLEEP WHEN WAITING`
section to:

> sleep_self is a LAST RESORT, not a default response to "I have
> nothing to do right now." Default behaviour when drained: just
> end the response — the trigger manager will only re-wake you
> when there's actual work (new task, broadcast, chat,
> consolidation). Sleeping does NOT save cost vs yielding; it just
> blocks scanner-driven re-wakes.
>
> Use sleep_self ONLY when you've raised a blocker and the blocker
> has a known time-bound external dependency (e.g. "waiting for
> nightly cron at 2am" → sleep until 2am minus a few minutes).
> Default sleep duration is MINUTES to LOW HOURS (300s-3600s),
> never 24h+. Admin chat auto-wakes you; bulk_wake_agents wakes you
> on demand. If in doubt: don't sleep, just yield.

**Effort:** S — ~20-line replacement of the existing section in
both iterator + SME prompts.

---

### 3.6 SME — external MCP onboarding: fetch `tools/list` before first call

**Insight source:** `sme-ec48bd29` (prompt_gap, target
`sme.materialisation — external MCP plane tools`). Called
`get_service_summary` and `get_service_details` on Last9 MCP before
verifying the parameter schema. Tools rejected the calls.

**Related:** `orch-a4b03932` and `sme-e057d130` (both
workflow_friction, target sme.workspace.mcp_json_writability).
Every SME asked to add Last9 MCP entry to its own `.mcp.json` ran
into Write-tool permission blocks ("permissions to edit ...
sensitive file"). Worked around via shell heredoc, but the standing
broadcast doesn't mention this.

**Prompt-design root cause:** SMEs treat external MCPs the same as
the canonical `cartograph-db` server (where the schema is in their
prompt). For arbitrary external MCPs, schema is dynamic — they
should fetch `tools/list` before the first call.

**Proposed fix:** add a one-paragraph "external MCP onboarding"
sub-section to the SME prompt, near the existing MCP server tool
list:

> When a broadcast or task instructs you to use an external MCP
> server (e.g. last9, splunk-mcp, github-enterprise-mcp), do TWO
> things first:
>
> 1. ADD the server to `.mcp.json` if not already present. If your
>    Write tool refuses the file ("sensitive file"), use a shell
>    heredoc instead (`cat > .mcp.json <<'EOF' ... EOF`) — that
>    bypasses the Write guard. Re-spawn picks up the new entry
>    automatically; no in-session hot-reload.
> 2. CALL `tools/list` on the new server before invoking any tool —
>    the tool catalog + parameter schemas are dynamic. Save the list
>    to your workspace (`./mcp_<server>_tools.json`) so you don't
>    re-fetch on every wake.

**Effort:** S — ~20-line addition.

---

### 3.7 Orchestrator + Iterator — `send_broadcast` ACL: orchestrator-only

**Insight source:** `iter-telemetry-b9b63048` (workflow_friction,
target `send_broadcast`) AND `orch-a4b03932` (workflow_friction,
target `orchestrator.task_brief.iterator_with_broadcast`).
Orchestrator tasked an iterator with "send_broadcast(SME-onboarding
contract)". Iterator tried, tool returned `Only orchestrator or
admin can send broadcasts`. Round trip wasted.

**Prompt-design root cause:** the orchestrator prompt doesn't
explicitly note that `send_broadcast` is orchestrator-only-among-
agents (admin is the only other sender). When delegating SME-
onboarding contracts, the orchestrator should send the broadcast
ITSELF and the iterator's task should reference the existing
broadcast.

**Proposed fix:** orchestrator prompt — add to the `send_broadcast`
tool description: "ORCHESTRATOR-ONLY among agents. Iterators and
SMEs cannot call this. If you need a contract distributed to all
SMEs (e.g. shared MCP onboarding, shared rate-limit policy), send
the broadcast yourself FIRST, then task the iterator with
'broadcast already sent at comm_id <X>; ensure your enumeration
respects it.'"

Iterator prompt — same note in negative form: "send_broadcast is
NOT available to you. If you encounter information that needs to
reach all SMEs, send_chat to orchestrator with the proposed
broadcast text and ask them to publish."

**Effort:** XS — two ~5-line tool-description tweaks.

---

### 3.8 Promote tactic_win — bulk MCP writes via Python script

**Insight source:** `iter-github-17cfa7c5` (tactic_win, target
`iterator.upsert_resources_bulk`). For payloads exceeding the
agent's per-tool-arg token ceiling (~25k tokens, where Read tool
itself refuses to inline that much), bypass agent context entirely:
write a small Python script that reads the data + calls the MCP
tool in batches. ~30 LOC, runs in subprocess, returns just the
batch counts.

**Why ship this:** iterator + SME prompts both push agents toward
"batch your tool calls" without addressing the ceiling. Without
this tactic, agents either truncate (data loss) or fan out across
20+ wakes (cost). The Python-script tactic is a clean escape hatch.

**Proposed fix:** add a `== BULK CALLS — PYTHON SCRIPT TACTIC ==`
sub-section to both iterator and SME prompts:

> When you need to make >50 MCP calls of the same shape (e.g.
> bulk-upserting 200 attributions, bulk-creating 100 edges), do
> NOT inline them in a single tool call (per-tool-arg token
> ceiling ~25k) and do NOT fan them across wakes (cost). Instead:
>
> 1. Write a small Python script (~30 LOC) to your workspace that:
>    - Reads the source data from a workspace file
>    - Calls the MCP tool via subprocess (`subprocess.run(['claude',
>      '--print', f'@mcp__cartograph-db__upsert_attribution {json}']`)
>      OR via direct HTTP POST to localhost:8100
>    - Loops in batches of N (e.g. 25), prints progress
> 2. Run via Bash. The script's output goes to your context
>    compressed (just batch-N-success / batch-N-error counts) — a
>    fraction of the size of the equivalent N tool calls.
>
> Use this for: bulk attribution hydrate from a parsed manifest,
> bulk edge creation from a grep'd codebase, bulk catalog
> declaration from an OpenAPI spec.

**Effort:** S — ~25-line addition to both prompts.

---

### 3.10 No bulk attribution read/write tools (tool gap)

**Symptom:** the cartograph-db MCP exposes `upsert_resources_bulk`,
`reject_resources_bulk`, `bulk_spawn_smes`, `decommission_agents_bulk`,
`decommission_components_bulk` — but only single-row write
(`upsert_attribution`) and single-component read (`get_attributions`)
on the attribution side. Two consequences:
1. SMEs hydrating dozens of attributions in one wake fan out to N
   tool calls. Hits the per-tool-arg ceiling indirectly via context
   bloat (each `upsert_attribution` echoes its row back).
2. Resolvers + orchestrators can't bulk-fetch attributions across
   N components for evidence triangulation in one round-trip — they
   loop `get_attributions(c)` per component.

**Proposed fix (tool):** add two MCP tools in
`src/cartograph_mcp/tools/components.py`:

- `upsert_attributions_bulk(agent_id, component_id, attributions[])`
  — single transaction, returns `{inserted, updated, conflicts[]}`.
  Embed all rows in one batch (mxbai-embed-large supports batching).
- `get_attributions_bulk(agent_id, component_ids[])` — returns
  `dict[component_id_str, list[attribution_dict]]`. Useful for
  resolver evidence triangulation.

Mirror the error-shape conventions of `upsert_resources_bulk` —
per-row success/error in the response so the agent knows what
landed and what didn't.

**Proposed fix (prompt):** once the tools ship, add to SME Step 2:

> When hydrating ≥10 attributions in one wake, prefer
> `upsert_attributions_bulk(component_id, attrs[])` over a loop of
> single calls. Saves context budget + ensures all-or-nothing
> uniqueness handling per the global key.

And to resolver Step 5:

> When triangulating evidence across N candidates, call
> `get_attributions_bulk(component_ids[])` once instead of
> `get_attributions(c)` per candidate.

**Effort:** M — two tool implementations, server.py wrapper, tests,
prompt updates.

---

### 3.11 Graph node planes derived from attributions, not agent resources — SHIPPED §2.7

**Symptom:** in the 3D graph (admin UI), node color + plane pills
are computed from `ARRAY_AGG(DISTINCT attributions.plane)` per
component (`server.py::/api/graph` and `/api/components`). This
conflates "where evidence was DISCOVERED" with "what plane the
component LIVES on." Per §4.4 broadcast: attribution.plane is the
DISCOVERY plane (where the SME found the evidence), so a github
SME finding a hostname in a helm chart tags that attribution
`plane=github` — even though the hostname is "deploy-y." The
graph then mis-colors the component.

The semantically correct source for "what plane is this
component" is the set of resources it owns via
`resource_component_agents → resources.plane`. A component
materialised by a github SME owns a github resource → it's a
github component. If it later absorbed a deploy SME's component,
RCA gains a deploy resource row → it's now a multi-plane
(github + deploy) component.

**Proposed fix:** change the SQL in three callsites:

```sql
-- before
COALESCE(
  ARRAY_AGG(DISTINCT a.plane) FILTER (WHERE a.plane IS NOT NULL),
  ARRAY[]::text[]
) AS planes
FROM components c
LEFT JOIN attributions a ON a.component_id = c.id

-- after
COALESCE(
  ARRAY_AGG(DISTINCT r.plane) FILTER (WHERE r.plane IS NOT NULL),
  ARRAY[]::text[]
) AS planes
FROM components c
LEFT JOIN resource_component_agents rca ON rca.component_id = c.id
LEFT JOIN resources r ON r.id = rca.resource_id
```

Three callsites in `src/admin_ui/server.py`:
- `/api/components` (line ~745)
- `/api/component/{id}/drilldown` (line ~795)
- `/api/graph` (line ~898)

The plane-pill rendering in `app.js` (`PLANE_COLORS` lookup) stays
the same — only the data source changes.

**Side effect:** the Plane filter in the components table
(`/api/components?plane=github`) currently filters by attribution
plane; should also flip to RCA→resources plane. Same EXISTS
sub-query, different join.

**Effort:** S — SQL-only changes, no schema migration, no
front-end change beyond cache-bust.

**Caveat:** components with no RCA reservation (rare; would mean
no SME ever owned them) would render as no-plane (slate-800).
Acceptable — currently those would also be planeless because no
attributions either.

---

### 3.12 Plane symbols on agent + component tags in admin UI — SHIPPED §2.7

**Symptom:** the agent list (`_renderAgentRow` in app.js:243) shows
each agent's component name with a 📦 emoji prefix. Useful at a
glance but doesn't tell admin which plane(s) the agent's resource
covers — that requires opening the component drill-down.

**Proposed fix:** add single-letter plane symbols (G/C/T/D/cfg)
next to the component tag, derived from the agent's resource
plane via `/api/agents`:

- `/api/agents` SQL gains:
  ```sql
  ARRAY_AGG(DISTINCT r.plane) FILTER (WHERE r.plane IS NOT NULL)
    AS resource_planes
  FROM agent_runs ar
  LEFT JOIN resource_component_agents rca ON rca.agent_id = ar.agent_id
  LEFT JOIN resources r ON r.id = rca.resource_id
  ```
- `app.js::_renderAgentRow` reads `agent.resource_planes` and
  renders symbols with the existing `PLANE_COLORS` palette:
  ```
  G  github    teal
  C  cloud     pink
  T  telemetry purple
  D  deploy    blue
  X  config    orange    (avoid 'C' collision with cloud)
  ```
  Render as one tiny pill per plane (or grouped) to the right of
  the component tag.

**Effort:** S — one SQL extension + ~15 lines of JS.

**Companion to §3.11:** uses the same RCA→resources.plane source.
Ship together so plane semantics are consistent across the UI.

---

### 3.9 Empty flow table — confirmed root cause is §3.1.3 (cross-step synthesis)

**Symptom (re-confirmed 2026-04-27):** flows table = 0 rows org-wide
despite catalogs (325 rows) and edges (153 rows) both populated. So
SMEs ARE hitting Steps 2b and 3 productively now (the catalog +
edge counts confirm this). Step 4 alone is being skipped.

**Why:** STEP 4 requires holding both Step 2b and Step 3 outputs
in working memory and producing the join. Sonnet under token
pressure lands each step then marks resource done. The fix is
already characterised in §3.1.3 (restructure STEP 4 with
pseudocode) + §3.1.4 (server-side `mark_resource_done` precondition
guard refusing TC if catalogs > 0 AND outgoing > 0 AND flows = 0
without an explicit `metadata.skip_flows_reason`).

**Action:** ship §3.1.3 + §3.1.4 in tandem; verify with DEMO7-r3
(see §6 chores). The §2.4 wake-budget rule should ALSO help
indirectly (fewer wake-yields means more chance Step 4 lands in
the same wake as Steps 2b+3).

---

## 4. Operational nudges (live levers)

### 4.1 Admin broadcast as wake-and-prod

**Pattern:** when SMEs are sitting idle in WD on partial materialisation
work, send a broadcast reminding them of the full step set. Recent
example (2026-04-27 08:01 IST):

> Guys, all of you should ensure your respective components unless
> you're actively invoked in consolidation... even then to an extent...
> should hydrate your components with rich attributions, catalogs,
> outgoing edges, flows and also trying to resolve outgoing edges to
> see if they belong to any other existing components if possible (not
> force fitting mind you).

**Strengths:**
- Names all 4 expected outputs (matches the materialisation step structure)
- "Try to resolve outgoing edges" addresses the dangling pile
- "Not force fitting" — important guardrail; agents otherwise might
  over-bind to confidently-wrong targets
- "Unless actively in consolidation, even then to an extent" matches
  the in-flight learning rule (§2.2.2) — fold new findings back into
  your component graph during consolidation

**Limits:**
1. Wake signal works for currently-spawned SMEs only. If sent
   `persistent=False`, a newly-spawned SME (e.g. from a future split)
   won't see this preamble. Use `update_broadcast_persistence(comm_id,
   persistent=True)` to flip post-send.
2. SMEs in `WD` task state stay there. The broadcast wakes them and
   they ack it, but they don't have a fresh BW task pushing them to do
   MORE work — they may chat back "noted, will do on next wake" and
   yield. Orchestrator needs to bounce their WD tasks back to BW (with
   "go finish per the broadcast") to actually re-engage them. Sequence:
   broadcast → admin chat to orch ("BW-bounce all SMEs in WD on their
   materialisation tasks") → orch issues the bounces → SMEs wake on the
   BW transition AND have the broadcast preamble fresh.
3. Vague "hydrate" doesn't enforce Step 4. SMEs without flows skipped
   Step 4 because it requires synthesising Steps 2b + 3. The broadcast
   says "flows" but doesn't say "you MUST emit ≥1 flow per catalog if
   you have any outgoing edges." Without that imperative + accountability
   framing, sonnet will likely still under-deliver flows.

**When to use:** as a fast first lever before structural prompt fixes
ship. As a permanent standing policy for SMEs spawned mid-run if you
mark it persistent. NOT a substitute for a `mark_resource_done`
precondition guard or a completion checklist in the prompt itself —
those are the durable solves.

---

### 4.2 Orchestrator BW-bounce of WD tasks

**Pattern:** when SMEs land in WD on partial work (per §4.1 limit #2),
the orchestrator can `respond_task(new_status='BW',
message='go finish per the recent broadcast — your component is missing
[catalogs / flows / outgoing edges]; specifics: <cite the gap>')` to
re-engage them. The SME wakes on the BW transition with both the bounce
message AND the standing broadcast preamble in its action items.

**When to use:** combine with §4.1 broadcast for the second-pass push.
The broadcast sets the standard; the bounce makes it actionable per-SME.

---

### 4.3 Persistent broadcast: file an insight on prompt-gap-driven mistakes

**Pattern (sent to all agent types, persistent=True):**

> Guys also whenever you're doing something because of a human
> intervention or a correction from your orchestrator or self-
> realisation due to communication with other agents... if you think
> there was a gap in your system prompt due to which you made those
> mistakes... raise them on insights.

**What it does:** wires the existing `record_insight(kind='prompt_gap',
target=<section>, body=<what + why>, evidence=<task_ids/comm_ids>)`
loop to ALL three correction surfaces (admin chat, orch task bounce,
peer clarification). Agents now have a clear trigger to file
prompt_gap insights — they don't have to invent the framing.

**Strengths:**
- Closed-loop feedback: every correction becomes a signal that admin
  can triage from the Insights tab. Promote → prompt edit. Wontfix →
  noted for future.
- Persistent → future-spawned agents (mid-run splits, late iterators)
  inherit the standing rule. New SMEs spawned post-broadcast see it
  on first wake.
- Targets ALL agent types — captures gaps in orchestrator routing
  decisions and resolver review heuristics too, not just SME
  materialisation.
- Reinforces the existing `== SELF-IMPROVEMENT LOOP ==` block in the
  shared mission (`base.py::MISSION_AND_VOCABULARY`) — same channel,
  more concrete trigger.

**Operational watchouts:**
- Volume: agents may over-report initially (every minor correction →
  insight). The base prompt already says "DON'T over-report. One
  insight per genuinely-new finding, not every mild irritation." Watch
  the Insights tab for the first few days; if `prompt_gap` count
  grows faster than admin can triage, send a follow-up nudge:
  "raise prompt_gap only when you'd genuinely make the same mistake
  again next wake without the prompt change."
- Triage discipline: insights are write-only from agents; only admin
  triage can flip status (`open` → `investigating` / `promoted` /
  `wontfix`). Establish a routine: weekly grep
  `agent_insights WHERE kind='prompt_gap' AND status='open'`, batch-
  triage, promote the high-signal ones into prompt edits + log them
  as new entries in §2 of this doc.
- Self-realisation framing: "self-realisation due to communication
  with other agents" is the most subtle trigger — when SME-A reads a
  clarification from SME-B and realises it should have understood X
  from the prompt. These are gold for prompt enhancements but easy
  to miss. Worth surfacing the routine: at the END of every
  consolidation/clarification thread, ask "did anything I just
  learned belong in my prompt?" before moving on.

**When to use:** as a permanent standing policy (persistent=True).
Pair with §4.1 + §4.2 — broadcasts set the bar, BW-bounces re-engage
work, prompt_gap insights surface the systemic issues that prompt
enhancements (§3) can solve durably.

---

### 4.4 Persistent broadcast: plane = DISCOVERY plane, not categorical plane

**Pattern (sent to SMEs, persistent=True, 2026-04-27 02:58 UTC):**

> The plane field on each attribution is the DISCOVERY plane (where
> YOU found this evidence), NOT the categorical plane it
> conceptually belongs to. You are assigned to a single plane
> ({plane}); tag every attribution YOU write with plane='{plane}'.
> If you're a github SME and you find a hostname inside a helm chart
> in the repo, that hostname's plane='github' (you found it via
> github) — even though hostnames "feel" deploy-related. Other SMEs
> on other planes will independently insert their own attribution
> rows for the same hostname they observe — (plane, resource_type,
> identifier) UNIQUE allows this; it's how multi-source evidence
> accumulates. If you're a multi-plane SME, put accordingly based
> on what data from what plane led you to that attribution.

**Why this matters:** SMEs were treating `plane` categorically ("a
hostname IS a deploy thing") and either re-classifying mid-write
or colliding on the global unique. The correct mental model is
"plane = where I, this SME, observed this evidence." Multi-source
evidence accumulates because each plane gets its own row.

**Companion to:** §3.3 (uniqueness collisions are partly caused by
miscategorising plane — two SMEs claiming `(deploy, hostname, X)`
collide; if one had used `(github, hostname, X)` and the other
`(deploy, hostname, X)`, both would coexist.)

**Promotion candidate:** add this framing to the SME prompt's
STEP 2 attribution section so future-spawned SMEs don't need to
ack the broadcast first. Track in §3 as a small follow-up edit.

---

### 4.6 Broadcast: consolidation is ongoing, not one-off (2026-04-27 07:14)

**Pattern (sent to SMEs, persistent=True):**

> Consolidation as you should already know is not a one off thing...
> if you believe some other component exist which should be part of
> you or if you should be further split into 2 realistically (not
> forcefully), you should raise consolidation requests...

**Why this matters:** SMEs were treating consolidation as a phase
they "did once" early in their lifetime. Real merge/split candidates
emerge over multiple wakes as new evidence (telemetry traces,
cross-SME clarifications, code grep) lands. The broadcast pushes
the rule: every wake, re-evaluate.

**Promoted to SME prompt §2.8.1 (Consolidation block) — durable.**

---

### 4.7 Admin chat log (selected, 2026-04-26 → 2026-04-27)

Eleven chats from admin during the DEMO7 real-data run, mined
for prompt-design signals. (Broadcasts are §4.5; this is the
chat side.)

| ts (UTC) | to | thread |
|---|---|---|
| 04-26 17:30 | orch | Last9 refresh token + github access token sent INLINE |
| 04-26 18:53 | iter-tel + orch | "I connected to vpn..." mis-addressed; "stop stop"; resent to orch; "you follow orch's lead" |
| 04-26 18:56 | orch | "why do you keep putting yourself to sleep... at max for 5-10min" — ask for persistent broadcast |
| 04-27 01:25→01:46 | orch | Scoping conversation: which services to include, trim psl/contest/hulk |
| 04-27 01:52 | orch | "your broadcast is faulty and misleading" — sleep is for ADMIN-blocking, not for "wait for things to come back" |
| 04-27 07:09→07:11 | sme×4 | "did you receive my broadcast about exhaustively identifying flows..." — agents not self-checking |
| 04-27 07:27 | sme-22a64543 | "have you not cloned github repo? if you didn't how would you know the flows" — proof point for git-clone-mandatory rule |

**Promoted findings:**

- **Credentials via chat** → orchestrator should put_secret
  immediately, not leave raw tokens in chat history (§2.8.3).
- **Mis-addressed chat handling** → base mission §2.8.5: read
  the full queue before acting on any single chat.
- **Sleep semantics — second clarification** → SME + iterator +
  orchestrator all rewritten with admin's exact framing
  ("LAST RESORT", 300-600s MAX, never burn long sleeps,
  yielding doesn't burn cost) (§2.8.1, §2.8.2, §2.8.3).
- **Hydration broadcast not auto-acted** → reinforces §3.1.4
  open chore: server-side `mark_resource_done` precondition
  guard. Broadcasts alone don't change behaviour without
  policy-level enforcement.
- **Github clone evidence** → already shipped §2.5; the
  07:27 chat confirms the fix was right.

---

### 4.8 New insights triaged (2026-04-27 batch 2)

Two additional insights filed during the run that weren't in
the first dump:

- **sme-f3a8e291 (workflow_friction, split_briefing)** —
  spawn_child_agent's split_briefing field carried an incorrect
  entry_point: `admin/src/main/java/com/dream11/app/MainApplication.java`
  but actual path was `.../com/dream11/admin/MainApplication.java`.
  Briefing was copying parent's package into child's hint.
  → **Tool/code bug, not prompt** — track as schema chore: have
  spawn_child_agent verify entry_point exists in child's
  source_slice paths before stamping it into the briefing.
  Effort: S — add a path-existence check in spawn_child_agent's
  briefing builder.

- **sme-7f4a958a (workflow_friction, materialisation)** — SMEs
  call `insert_unresolved` and OMIT `upsert_edge_outbound` for
  dangling refs. Silent gap: 0 outgoing_dangling but N
  unresolved; flows can't anchor to missing edge rows.
  → **Promoted into SME §2.8.1**: DANGLING EDGE = TWO WRITES,
  ALWAYS BOTH rule.

- **sme-22a64543 (tactic_win, materialisation)** — vertx3-otel
  v2.2.2 doesn't instrument JAX-RS SERVER spans, so 0 inbound
  spans ≠ no HTTP server. Discovered for fav2-admin (109 RPM
  outbound, 0 SERVER spans).
  → **Promoted into SME §2.8.1 STEP 4 caveat**: trust code
  over zero-trace inference for inbound surface discovery.

---

### 4.5 Broadcast log (admin sends, 2026-04-26 to 2026-04-27)

Seven broadcasts sent during the DEMO7 real-data run, all
persistent=True. Newest first.

| ts (UTC) | to | subject | persistent |
|---|---|---|---|
| 2026-04-27 07:14 | sme | consolidation is ongoing, not one-off (see §4.6) | yes |
| 2026-04-27 02:58 | sme | plane = DISCOVERY plane (see §4.4) | yes |
| 2026-04-27 02:38 | iterator | record prompt_gap on every correction | yes |
| 2026-04-27 02:38 | resolver | record prompt_gap on every correction | yes |
| 2026-04-27 02:38 | iterator | (re-send to iterator) | yes |
| 2026-04-27 02:38 | sme | record prompt_gap on every correction (see §4.3) | yes |
| 2026-04-27 02:31 | sme | hydrate components fully (see §4.1) | yes |

**Pattern:** broadcasts cluster in two groups. First group (02:31)
is a "hydrate completeness" nudge to SMEs only. Second group (02:38)
is a "raise prompt_gap insights on corrections" message fanned to
all three workable agent types (sme, iterator, resolver — orch is
admin's primary chat partner). Third group (02:58) is a
plane-semantics correction to SMEs.

**Outcome:** the 02:38 prompt_gap broadcast is what produced the
13-insight crop characterised in §3.2 through §3.8. High signal —
worth keeping persistent indefinitely.

---

## 5. Principles + anti-patterns

### 5.1 Prompts as policy, not as documentation

A prompt section that *describes* a behavior is not the same as one that
*requires* it. "STEP 2b — Catalog declaration: declare every endpoint
you expose" reads as documentation; "STEP 2b is REQUIRED for
app/lambda/external/cron — mark_resource_done will REFUSE if your
component has zero catalogs" reads as policy. When real data shows a
step is skipped, audit whether the prompt language is policy or just
documentation, then upgrade if needed.

### 5.2 Tool-side enforcement > prompt-side mandate

When a behaviour is critical (like "don't mark a component done with
no catalogs"), enforce it in the tool's Python code, not just in the
prompt. Prompts can be skimmed; tool refusals can't. The pattern of
returning a structured refusal with the failed checklist items keeps
the agent self-correcting.

### 5.3 Cross-step dependencies need explicit framing

STEP 4 (flows) requires output from both STEP 2b (catalogs) and STEP 3
(outgoing edges). That's a join that the agent has to do mentally.
Either:
- Tell the agent explicitly that Step 4 is a join over Steps 2b + 3
  output, with concrete pseudocode (§3.1.3 fix).
- Or restructure to avoid the cross-dependency (e.g. fold flow
  declaration INTO catalog declaration, "for each catalog you declare,
  immediately upsert any flows that fan out from it" — but this
  conflates the two and may be worse for readability).

### 5.4 "Multi-wake materialisation" is real

A single SME wake doesn't have unlimited tokens. Agents under token
pressure will skip later steps. Either:
- Sequence work across wakes via a phase column (§3.1.5).
- Or design steps to be small enough that all of them fit in one wake
  (current prompt: 130+ lines of materialisation steps — too much).
- Or add a "where did I leave off" self-check at the start of each
  wake.

### 5.5 Operational nudges (broadcast + chat) are bridges, not solves

Use to course-correct an in-flight run, not to compensate for a
structurally weak prompt. If the same nudge is needed every run, that's
a signal to ship it as a prompt enhancement.

### 5.6 Anti-pattern: phase-history baggage in prompts

"(Phase 7.4.5: was a mixed list+int dict pre-7.4.5)" in an agent prompt
is noise — the agent doesn't need to know our implementation history.
Strip phase tags from prompts; keep them in commit messages and this
doc.

### 5.7 Anti-pattern: verbatim duplication between code and docs

Pre-2026-04-26, `docs/AGENT-PROMPTS.md` §1-4 held verbatim copies of
the agent prompts in markdown code-fences. They drifted significantly.
Now §1-4 hold intent summaries with explicit pointers to the .py
source-of-truth files. Keep this pattern: code is the source, doc
captures intent.

---

## 6. Open chores (do alongside any prompt fix)

- **Audit `mark_resource_done` callsites** when adding precondition
  validation — make sure split-spawned children's idempotent re-call
  doesn't break under stricter rules.
- **DEMO7-r3 verification** — re-run the orchestrator dance after the
  next batch of prompt fixes to confirm flows + catalogs + outgoing
  edges are produced consistently across all SMEs (not just the first
  few).
- **Vector_search threshold recalibration** — empirical study with the
  Phase 7.4.4 lean projection: at what cosine threshold does binding
  precision stay > 0.95? Currently 0.75 was calibrated against the old
  rich-row response.

---

End of doc. Add new entries to §3 as they're diagnosed; promote to §2
when shipped with a commit hash + diagnostic context preserved.

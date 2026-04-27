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

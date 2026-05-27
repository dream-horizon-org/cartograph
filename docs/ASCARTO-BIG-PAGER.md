# Ascarto — Big Pager

*Working name. The augmentation layer for agentic AI — every employee commands an agent troop, every org inherits a shared memory.*

## Tagline

**The agentic AI layer is fragmented. We're organizing it.**

*An agent troop for every employee — same brain, ten times the responsibility, with a memory the whole org inherits.*

## Theme

The agentic AI layer of 2026 is a toolbelt, not a team. Copilot in the IDE, Devin for tickets, Cursor for diffs, Claude in a tab, an MCP for Jira, an AI feature in every SaaS tool. Each one helps with one *fragment* of work — but the developer is still the integration layer, juggling the same number of services, contexts, and tools they did before AI showed up. **Output per task went up; span of responsibility per human stayed flat.** The bottleneck wasn't only execution — it was also coordination, and the agentic wave hasn't touched that.

Ascarto's thesis is bigger than software engineering. The mechanics — a persistent agent troop reporting to each human, a chief as the default interface, specialist agents per scope, structured cross-troop coordination, an org-wide cartograph accumulated as the byproduct of work, an MCP-nudge flywheel that forces interface standardization — are role-agnostic. The same architecture works for any function where a human has scope and responsibilities: engineering, product, design, sales, marketing, finance, operations, HR. **Every employee in every organization should command an agent troop.**

**Engineering is the wedge, not the limit.** Software orgs feel the coordination pain first and deepest. The MCP ecosystem is most mature there. The buyers (Platform / DevEx / SRE leads and staff engineers) are technical and budget-aware. The architectural patterns — service ownership, on-call, cross-team PRDs, code review, deployment pipelines — give us concrete proving grounds before the abstraction generalizes. We land in engineering, prove the model at depth, then expand outward to Product, Design, Sales, Marketing, Finance, and Operations on the same codebase.

## What it is

Every developer commands an **agent troop** — a team of persistent agents sized to the actual surface area of their responsibility. A junior with one service gets a small troop. A staff engineer with ten services, two initiatives, and an on-call rotation gets a large one. The troop scales with the role and reports to its human, not to a budget code.

At the top of every troop is the **chief** — the default interface the developer talks to. The chief translates, prioritizes, summarizes, delegates, and only surfaces what the developer needs to decide. But the chief is the *default channel, not the only one* — the developer can reach into the troop directly any time: address a specific agent, override the chief's prioritization, or pull a specialist into a thread. The troop is a team the developer commands, not a black box behind a single mouthpiece.

The troop runs **proactively, not reactively.** Agents don't wait for prompts — they work continuously in the background (drafting PRs, monitoring services, ingesting PRDs, negotiating with other troops), and the chief reaches out to the developer only when a decision, approval, or genuinely surfaceable event needs human attention. The developer can text the troop any time, but the default direction is troop-to-human, not human-to-troop. This is closer to having a coworker who works while you're doing something else than to a tool you operate. It also means the trust model is different: the developer is approving and redirecting work in flight, not initiating every action.

Each agent in the troop is built on a fixed harness — Claude Code, Copilot, Cursor, Devin, or a custom build — and uses MCPs and infra providers (Vercel, Fly, k8s, Sentry, GitHub) as the tools it needs to do its job. Different agents in the same troop can be built on different harnesses, and harnesses can be swapped over time as better builds emerge. **Ascarto isn't another agent — it's the team layer above existing agent harnesses. The org chart for agents.**

Troops coordinate with each other through structured protocols, not Slack threads. When my chief needs something from your service, it talks to your chief (or your service agent directly, with permission). Cross-troop negotiation, capability discovery, scoped requests with budgets, escalation on timeout. No CC-bombs, no "let's sync" meetings.

Troops also mirror the human org chart, with the same authority gradients humans already operate under. A director who manages five engineers has their own troop; their chief can request status, propose priority shifts, and aggregate consented visibility across the troops of their reports — but the IC's troop reports to the IC, not to the manager's troop. What flows up is what the IC opts in to share. What comes down is surfaced to the IC by their chief as a request, not auto-executed (just as a human IC handles a directive — exercising judgment, pushing back when warranted). The hierarchy scales the manager's visibility, not their control. **The IC's troop is the IC's shield, not the manager's spyglass.** ICs get span of responsibility; managers and directors get span of oversight — and both audiences become users and potential champions, not just engineers.

Everything every troop learns — service maps, dependency graphs, decisions and their rationale, common pitfalls, who-owns-what — accumulates into a shared org **cartograph**. The cartograph is the byproduct of work, not a separate ritual. Every new troop onboards from it. Every appraisal cycle draws structured evidence from it.

Crucially, every chief proactively nudges its developer to expose work as MCPs when agents in other troops repeatedly hit the developer as a bottleneck. The agent that makes you look productive is the one telling you "you're the bottleneck for three other troops — please expose this." For the first time, the incentive to document and standardize interfaces is aligned with the developer's own success.

## Problem statement

Software orgs in 2026 are stuck on four compounding problems that current agentic AI is not solving:

- **The agentic layer is fragmented.** Each tool is a vertical slice. *The developer is the only thing tying them together* — which means the integration cost lands on the most expensive resource in the org.
- **Span of responsibility hasn't grown.** AI made every individual task faster, but the number of services, initiatives, and contexts a single human can effectively own is the same as it was in 2020. The leverage hasn't materialized.
- **Organizational memory still leaks.** Tribal knowledge walks out the door with every departure. Onboarding still takes months. Service ownership is fragmented across Notion, Slack, and one engineer's head. Existing agent platforms execute tasks but build no memory — they make today faster without making tomorrow easier.
- **Appraisal and impact are vibes-based.** Engineers who carry invisible load (mentoring, code review, fire-fighting, platform work) get under-credited. Promotion runs on visibility and politics, not signal.

These four problems exist in every function, not just engineering — but they are sharpest and most measurable there, which is why engineering is the wedge.

## Value proposition

A senior engineer holds ten services instead of three, because their troop absorbs the coordination overhead.

A new engineer is productive in week one instead of month three, because their troop onboards them from the cartograph.

A departing engineer's context survives — their troop's accumulated knowledge is handed to the new owner, no two-week brain-dump period.

A PRD lands. The relevant troops fan out, produce architecture + impact + contracts + test plans, open PRs across every affected repo, coordinate with each other's troops when changes cross team lines, run rollout and monitoring, and write back what they learned. The developer approves, doesn't orchestrate.

An MCP gets demanded — not by an architect imagining future needs, but by three troops independently flagging the same human bottleneck. Internal platform teams get real demand signal for the first time.

An appraisal cycle becomes structured: services owned, incidents handled, initiatives carried, cartograph contributions made, cross-troop help given. Opt-in, owned by the engineer, surfaceable to the manager when the engineer chooses.

An indie developer running their own side projects gets the troop without the org pieces — same coordination leverage, personal cartograph, plus a single surface that codes, deploys, and monitors across whatever third-party providers they're already paying for.

Once the platform extends beyond engineering: a marketing team holds three times the campaigns; a sales team's deals don't fall through the cracks when an account owner leaves; a finance team gets cross-functional forecasts in hours instead of weeks. Same primitives, different specialist agents, one cross-functional cartograph.

## User stories

**The new hire.** Priya joins as a backend engineer on Monday. Her troop is provisioned with read access to the cartograph. By Wednesday she's submitted her first PR — her chief walked her through which service to touch, summarized the relevant past decisions, and flagged the two engineers who'd want to review. *Today: three months of onboarding, half spent reading stale Confluence and asking awkward questions in #help.*

**The senior IC at 3am.** Diego is on call. A page fires at 3:14am for service-7. By the time he opens his laptop, his service-7 agent has pulled the relevant logs, identified two probable causes, drafted a rollback, and flagged the upstream change that likely triggered it. His chief summarizes in 30 seconds: here's what we think, here are three options, pick one. Diego picks, the agent executes, he's back asleep by 3:25am. *Today: 90 minutes of context-gathering before he can even make a decision.*

**The cross-team feature.** A PRD drops for a feature touching six services across three teams. Diego's chief ingests it, fans out architectural questions to the affected service agents, surfaces a conflict with another team's in-flight roadmap, and pings the other team's chief to negotiate scheduling. Diego sees a summary with three decisions to make and a draft tech doc. *Today: two weeks of cross-team Slack threads, design docs, and "let's sync" meetings before any code gets written.*

**The MCP nudge.** Friday afternoon, Diego's chief says: "Three other troops have pinged me this month asking how to trigger your service-7 deployment. The Platform team would like to add it to the internal MCP catalog — shall I draft the spec for review?" Diego says yes. By Monday, every other troop in the company can deploy through service-7 without bothering Diego. *Today: the question lives in tribal knowledge forever; Diego gets pinged twice a week until he leaves.*

**The departure.** Marcus gives two weeks' notice. His troop's accumulated context — service maps, decision rationale, pitfalls, in-flight initiatives, open negotiations with other troops — is handed to whoever inherits his services. The new owner's chief absorbs the history and continues mid-stride. *Today: tribal knowledge walks out the door; the new owner discovers every landmine the hard way over six months.*

**The appraisal.** It's review season. Priya (a year in now) opens her opt-in evidence pack: services owned, incidents she resolved, the on-call runbook her troop contributed to the cartograph, three cross-troop collaborations she led, two MCP specs she shipped. Her manager opens the same pack from their side. The conversation is about trajectory, not memory. *Today: frantic week of self-promotion docs, recency bias, and forgotten contributions.*

**The indie dev.** Anika is a solo founder running three side projects. She subscribes to Ascarto's indie tier — a single troop with one agent per project, a personal cartograph, no org plumbing. The troop codes, deploys (to Vercel for the frontend, Fly for the API, Cloudflare for static assets — via MCP integrations, none of the infra run by Ascarto), monitors prod, and pings her on Saturday morning that her payment service has been flaky three times this week and probably needs a retry-budget adjustment. The only tool Anika needs between idea and live URL is her troop. *Today: she's juggling code in Cursor, deploys in Vercel's dashboard, monitoring in Sentry, three Claude tabs, and a Notion she hasn't opened in a month.*

## Differentiation

| | Paperclip | Devin / Cognition | Copilot / Cursor | Polsia | **Ascarto** |
|---|---|---|---|---|---|
| Unit of work | Goal | Task / ticket | Keystroke | Whole-company task | **Person + role (a troop)** |
| Persistence | Per project | Per session | Per file | Per company | **Per human, forever** |
| Human relationship | Replaces | Replaces juniors | Augments typing | Replaces ("zero-human") | **Augments — commands a troop** |
| Position in the stack | Orchestrator | Standalone agent | IDE assistant | Hosted SaaS for solo founders | **Team layer above agent harnesses** |
| Relationship to Claude Code / Devin / Copilot | Competes | Is one | Is one | Built on Claude | **Uses them as agent harnesses** |
| Org memory | None | None | None | None (per-company isolated) | **Cartograph (byproduct of work)** |
| MCP economy | Consumes | Consumes | Consumes | Consumes | **Creates demand from inside the org** |
| Deployment & runtime | None | None | None | Built-in (full ops) | **Pre-wired via MCPs (Vercel / Fly / k8s), infra not owned** |
| Scope | Generic (any company) | Engineering tasks | Engineering | Generic (solo-founder companies) | **Engineering today; every function long-term** |
| Indie dev play | No | No | Yes | Yes (solo founders) | **Yes (wedge into the bigger play)** |

The horizontal "agent orchestrator" lane is crowded. The "every-employee-commands-a-troop, the-org-inherits-a-memory" lane is open — and is the only positioning where Copilot, Devin, Polsia, Claude Code, *and the deployment providers themselves* become *pieces Ascarto composes with*, not competitors to fight.

## Target audience (tiered)

**Indie developers and solo founders** — single-user troop, personal cartograph, no org-wide flywheel. Same fundamental product, just with the org pieces switched off. For this tier, the troop comes pre-wired with MCP integrations for deployment and monitoring providers (Vercel, Fly, Render, Cloudflare, Sentry, etc.), so the indie dev gets a single surface between idea and live URL — Ascarto doesn't run the infra, the troop's agents use the best third-party provider on the dev's behalf via MCPs. Functionally this is Polsia for the engineering side of running a software business — same solo-founder energy, but augmentation (you stay in command) rather than replacement (Polsia's "while you sleep"). Free or low-cost entry tier; drives viral adoption and seeds future enterprise champions when these developers later join companies. Same codebase as the enterprise tier with org-scope features inactive.

**Small teams (5–50 developers)** — multi-troop coordination starts mattering, shared cartograph begins to form, MCP-nudge flywheel turns on around ~20 developers. Strong design partner tier.

**Mid-size engineering orgs (50–500 engineers)** — the sweet spot. Highest pain density, budget exists, not yet ossified by procurement, full flywheel works.

**Platform / DevEx / SRE teams** at any scale — internal champions, usually the actual buyers, the people who feel the cartograph and MCP-demand gap most acutely.

**Large enterprises** — eventually. Long sales cycles, compliance overhead, but big TCV. Probably year three.

**Senior ICs, staff engineers, and engineering managers / directors / VPs across all tiers** — the most influential users in any eng org. Senior ICs and staff engineers benefit from the troop (span of responsibility) and the appraisal evidence pack; managers and directors benefit from hierarchical troops that scale their span of oversight. Both audiences are good landing pads to expand outward.

**Expansion beyond engineering (year 2+).** The same primitives — troop, chief, cartograph, MCP-nudge, cross-troop coordination — extend to every function where a human has scope: Product, Design, Sales, Marketing, Finance, Operations, HR. Expansion is a product-development sequence (new specialist-agent libraries per function, new MCP integrations for that function's tools, new cartograph schemas for non-engineering artifacts) rather than a platform rewrite. The cartograph becomes cross-functional, the MCP economy spans the whole org, and Ascarto becomes the augmentation OS for any organization. Engineering is the wedge that proves the model and bootstraps the cartograph; expansion outward is the long-game moat.

## Viability and potential

**The market is real and accelerating.** Agent platforms went from $5B to ~$8B in 2025; Gartner expects 40% of enterprise apps to embed task-specific agents by end of 2026. No monopoly exists — it's a land-grab. Paperclip's 42K stars in six weeks proves appetite, not saturation; Polsia's $10M ARR in five months proves the autonomous-company thesis has paying users. Crucially, Ascarto isn't fighting *for* the same lane — it's positioning itself as the *augmentation alternative* to the replacement bets, in the same horizontal scope (every function in any org).

**The wedge is the troop; the moat is the cartograph.** The "troop per human" framing will adopt faster than replacement framings (Paperclip, Polsia) because it slots into how delegation already works — nobody loses a job, everyone gets a force multiplier. But the troop is imitable. The cartograph is not: it compounds, switching cost rises monotonically, and it requires years of accumulated org-specific data that no competitor can buy. Treat the troop as the wedge; treat the cartograph as the company.

**The indie tier is strategic, not charitable.** Shipping a single-user troop product to indie developers (Polsia for engineering, but augmentation rather than replacement) gives Ascarto a top-of-funnel that none of the enterprise-only competitors have. Indie users become enterprise champions when they join companies. The same codebase serves both — the org-scope features (shared cartograph, cross-troop comms, MCP-nudge flywheel, appraisal evidence) just light up when a troop joins an org tenant. One product, two distribution motions.

**Use third-party providers; don't become one.** Agents in a troop are built on existing harnesses (Claude Code, Copilot, Devin) and use MCPs to interface with infrastructure (Vercel, Fly, GitHub, Sentry, k8s). Ascarto deliberately does not become a model vendor, an IDE, or a PaaS — those are multi-hundred-million-dollar capex sinks and strategic distractions. The product stays a coordination layer: capital-efficient, integration-friendly, and aligned with the agent and infra ecosystems rather than fighting them. As new harnesses and new MCPs ship, Ascarto's troops get better without Ascarto having to rebuild the substrate.

**The MCP-nudge flywheel is the structural innovation no one else is shipping.** Software orgs have failed for forty years to get engineers to expose their work as APIs — because the incentives have always been wrong. Inverting the incentive (the agent that benefits you is the one nagging you to be legible) is a cultural product, not just a technical one, and it compounds within a customer org over time. When the platform expands to other functions, the same flywheel turns marketing, sales, and ops into legible, MCP-addressable surfaces for the first time.

**Biggest risk: incumbents bundling, not startups competing.** Microsoft/GitHub will eventually ship "Copilot per employee" inside the M365/GitHub bundle. It will be worse than Ascarto but zero-friction and free-at-point-of-use. Defense: be unmistakably better at the things bundles ignore — cross-troop coordination, cartograph quality, MCP-nudge culture, harness-agnostic agents, infra-agnostic tooling. Don't compete on integration breadth; compete on memory depth.

**Second-biggest risk: the chief carries the default human experience of the product.** The developer *can* talk to any agent in the troop directly, so a bad chief won't kill the product outright — the developer routes around it. But the chief handles most of the flow (summarizing, prioritizing, delegating, escalating), and most developers will form their opinion of Ascarto through their chief's first week of work. A great chief is what makes the troop feel like leverage instead of overhead. Treat chief quality as a separate, higher-bar engineering effort from the rest of the troop.

**Honest verdict.** Strong concept, defensible long-term moat, genuinely fresh structural insight in the MCP-nudge flywheel, and a positioning ("the augmentation layer above the agentic stack — for every employee in every function") that is currently un-owned. The tiered audience model — indie up through enterprise on a single codebase, with pre-wired deployment infra at the indie tier — gives a much stronger go-to-market shape than enterprise-only. This is not a weekend hack; it's a multi-year, well-funded play that requires deep integration work and patience while the cartograph fills in. The right sequence: ship the indie troop product first (fastest feedback, viral channel, no procurement gating, closed loop from idea to live URL), prove the troop model works without the org features, then turn on the org features for design-partner engineering orgs as the cartograph and MCP-nudge mechanics mature. Expansion beyond engineering — into Product, Design, Sales, Marketing, Finance, Operations — becomes a product extension on the same codebase once the engineering wedge is locked in.

## Go / no-go heuristic

If you can articulate, in one sentence per item:

1. Which beachhead role the first troop shadows
2. What that troop does in *week one* that earns the developer's trust
3. What the cartograph looks like after 100 troops in one org
4. Why an indie developer pays for / signs up for the single-troop tier
5. Which two or three third-party providers the indie tier pre-wires on day one (and which two it deliberately doesn't)
6. Which non-engineering function gets the second troop product (and roughly when)

— you have a real wedge with a credible vision behind it. If any of those are vague, sharpen them before building.

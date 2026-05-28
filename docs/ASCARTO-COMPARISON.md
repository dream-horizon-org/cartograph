# Ascarto vs. Paperclip vs. Polsia

A positioning reference for handling the *"how is this different from X?"* question — in pitches, design partner conversations, and sales calls.

## TL;DR

| | **Paperclip** | **Polsia** | **Ascarto** |
|---|---|---|---|
| Layer of the stack | Generic agent orchestrator (OSS) | Hosted autonomous-company SaaS | Augmentation layer above existing harnesses |
| Scope | Any company | Any solo-founder company | Every function in any org (engineering wedge) |
| Relationship to human | Replaces ("zero-human company") | Replaces ("while you sleep") | Augments (every employee commands a troop) |
| Operating mode | Goal-driven (set goals, agents execute) | Fully autonomous (no human in loop) | Proactive, always-on; troop runs in background, texts human for decisions |
| Agent hierarchy | AI org chart replaces the company | Flat (solo founder, no team) | Agent troops parallel the human org chart; managers get consented visibility, ICs keep execution sovereignty |
| Unit of work | Goal | Whole-company task | Person + role (a troop) |
| Persistence | Per project | Per company | Per human, forever |
| Org-level memory | None | None (per-company isolated) | Cartograph (cross-functional, byproduct of work) |
| Multi-human coordination | Inside a company structure | N/A (solo founder model) | Native (cross-troop protocols) |
| MCP economy | Consumes | Consumes | Creates demand from inside the org |
| Primary audience | Founders, solopreneurs, OSS devs | Solo founders | Indie devs → mid-market engineering orgs → all functions |
| Maturity (May 2026) | 42K+ GitHub stars, large community | $10M ARR, 7,600 customers, $250M post-money | Pre-product (positioning) |

**The core distinction:** Paperclip and Polsia are both in the **replacement camp** ("agents instead of humans"). Ascarto is in the **augmentation camp** ("agents reporting to humans") — same horizontal scope (every function in any organization), but humans stay in command. The architectural moats are the **cartograph** (organizational memory accumulated as a byproduct of work) and the **MCP-nudge flywheel** (cultural enforcement of interface standardization), neither of which the replacement plays have or particularly need.

## vs. Paperclip

**What Paperclip is.** Open-source agent orchestration platform that lets you "run a zero-human company." You assign goals to a hierarchy of AI agents (CEO agent, marketing agent, engineering agent) and they execute. Launched March 2026; hit 42K GitHub stars in six weeks. Uses Claude Code / OpenClaw as the worker agents underneath. Frames the user as "the board of directors."

**Surface overlap with Ascarto.** Both feature hierarchical agents. Both coordinate multiple agents. Both can use existing agent harnesses as workers. Both span the entire org (any function, not just one).

**Where Ascarto is different — and why it matters:**

- **Augmentation vs. replacement (and hierarchy replaces vs. mirrors).** Paperclip's mental model is "fire your humans, hire agents"; its agent hierarchy *replaces* the company org chart entirely. Ascarto's is "every human gets a troop"; its agent hierarchy *parallels* the human org chart, with the same authority gradients humans already operate under — managers get consented visibility into their reports' troops, but ICs keep execution sovereignty over their own. Augmentation adopts ~10x faster than replacement in any organization that already has humans — nobody loses a job, everyone gets a force multiplier, and managers / directors get span of oversight alongside ICs' span of responsibility. Replacement framings hit cultural and political resistance hard, especially inside established companies. The Paperclip pitch works for solo founders building from zero; it does not work for a 200-person engineering org.
- **Wedge: vertical depth, then horizontal.** Paperclip ships generic from day one. Ascarto goes deep in engineering first — service ownership, on-call, PRDs, cross-team coordination, code review, runbooks, deployment, post-mortems — then expands to Product, Design, Sales, Marketing, Finance, Operations on the same codebase. Different bets on market entry: Paperclip optimizes for breadth from day one; Ascarto optimizes for proving the model at depth and expanding outward.
- **Stateless execution vs. compounding memory.** Paperclip's agents execute goals and forget. Ascarto's troops accumulate org-specific knowledge — service maps, decisions, pitfalls, who-owns-what — as the byproduct of doing work. After two years, Paperclip looks the same; Ascarto has years of cartograph data no competitor can replicate with capital.
- **Consumes interfaces vs. generates demand for them.** Paperclip uses whatever MCPs/APIs already exist. Ascarto generates *internal demand* for new MCPs by surfacing where humans bottleneck multiple troops. Over time, the customer's internal platform becomes more legible *because of* Ascarto — that's a structural product effect Paperclip doesn't have.

**What Paperclip does better.** Generic applicability from day one (works outside engineering without a wedge story). Open-source community momentum and contributor velocity. Faster time-to-first-use for non-software use cases. Already shipped; battle-tested; thousands of contributors.

**The honest pitch line:**
> *"Paperclip replaces your employees. Ascarto makes your employees ten times more effective — and builds your org's memory while they work."*

## vs. Polsia

**What Polsia is.** Launched late 2025 by Ben Broca as "AI that runs your company while you sleep." A hosted SaaS at $49/month where autonomous AI agents handle a solo founder's entire business: strategic planning, software development, marketing, sales, VC negotiation, inbox management. Built on Claude with MCP integrations to email, payments, social platforms. Hit ~$10M ARR in five months with 7,600 paying customers; raised $30M Series A at $250M post-money valuation, with zero employees. The poster child for the "zero-human company" commercial thesis.

**Surface overlap with Ascarto — much greater than it appears at first glance.** Both products span every function of an organization. Polsia handles marketing, sales, code, ops, finance for a one-person business. Ascarto's full vision is the same scope: every employee in every function gets a troop, and the cartograph spans the whole org. The mechanics are similar enough that Polsia is Ascarto's nearest commercial reference in the "agentic AI runs the whole org" category — including for the indie tier, where a solo developer using Ascarto is, functionally, running a one-person software business.

**The differentiator collapses to one axis: replacement vs. augmentation.**

- **Polsia: replaces every human in the company.** The framing is "while you sleep." The founder steps back; the agents run the business. Humans are absent by design.
- **Ascarto: augments every human in the company.** Every employee commands a troop. The chief manages execution; the human makes the final call. Humans stay in command by design.

That's it. Same horizontal scope. Opposite relationship to humans. Every other difference cascades from that one:

- **Adoption story.** Polsia works for solo founders happy to step away. Ascarto works for any existing organization with humans who aren't going anywhere — i.e., almost every company with more than five people. Augmentation adopts ~10x faster than replacement inside established orgs because nobody loses a job; everyone gets a force multiplier.
- **Wedge.** Polsia goes broad-and-shallow across all business functions from day one (a generalist for a generalist user). Ascarto goes narrow-and-deep in engineering first, then expands outward. Different bets on how to enter the market; both end up serving the whole org if they win.
- **Memory layer.** Polsia agents operate per-company in isolation; the per-company memory isn't shared across companies (nor would it be — each company's data is private). Ascarto's cartograph is *within-org but cross-function* — every troop's contributions accumulate into a shared knowledge graph that becomes the long-term moat as the platform expands beyond engineering.
- **Buyer.** Polsia sells to one solo founder at $49/mo. Ascarto sells to indie developers at a similar tier today, and to engineering orgs (eventually whole orgs) at much higher contract values as the platform spans functions.
- **Complementarity, not pure competition.** For a solo founder building a software business, Polsia and Ascarto could actually be used together: Polsia for the business side (marketing, sales, VC, inbox), Ascarto for engineering depth. Different bets on what "AI runs your work" should mean, and they can coexist for the user who wants both.

**What Polsia does better.** Already shipped with validated unit economics. Single-founder GTM is dialed in. Excellent media-friendly story ($1M ARR in 30 days, $250M valuation as solo founder is a hell of a headline). Built for users who genuinely want zero employees — a thesis Ascarto does not serve and shouldn't try to.

**The honest pitch line:**
> *"Polsia replaces every human in your company. Ascarto augments every human in your company. Inside any organization that wants to keep its people, only one of those bets works."*

## Strategic note: who actually competes with Ascarto long-term?

Neither Paperclip nor Polsia is the real competitive threat. Both are in the *replacement* lane; Ascarto is in the *augmentation* lane. They're on different bets about whether humans stay in the loop, and they serve different buyers (founders willing to step back vs. organizations that want to keep their people). The bigger risks are:

- **Microsoft / GitHub bundling "Copilot per employee"** inside the M365 / GitHub bundle. Zero-friction distribution; worse product, but bundled and effectively free at the point of use. Defense: be unmistakably better on cartograph quality, MCP-nudge culture, and cross-troop coordination — things bundles structurally ignore.
- **A new entrant building exactly what Ascarto is building.** The "augmentation layer for every human in every org, with shared memory" category is currently un-owned. The moat is the cartograph, which takes time to fill. First mover with the right wedge wins, and the wedge is the indie troop offering that proves the model before the cartograph turns on.

Paperclip and Polsia are useful comparisons for pitch conversations — both are well-funded, well-known, and on a different bet about whether humans stay in the loop. Ascarto's positioning is sharpest *against* both: if you're an organization with humans who aren't going away, neither Paperclip's "fire your team" nor Polsia's "step away while AI runs everything" maps to your reality.

## One-line answers for common pitch objections

- *"Isn't this just Paperclip for engineering?"* → Paperclip replaces humans; we augment them. Paperclip has no org memory; we have the cartograph. Paperclip consumes MCPs; we generate demand for them. And Paperclip ships generic-shallow; we ship vertical-deep with a real expansion path.
- *"Isn't this just Polsia for software work?"* → Polsia replaces humans; we augment them. That's it. Same horizontal scope (every function), opposite relationship to humans. Polsia works for solo founders who want to step away; we work for organizations that want to keep their people.
- *"Why can't I do this with Claude Code and a few MCPs?"* → Claude Code gives you one persistent assistant. Ascarto gives you a coordinated team of persistent agents reporting to you, plus cross-team negotiation with other developers' teams, plus an org-wide memory that compounds. Tools and harnesses are the substrate, not the product.
- *"Won't GitHub Copilot just ship this?"* → Eventually they'll ship a thinner version, bundled. We'll be unmistakably better on the things bundles ignore: cartograph quality, cross-troop coordination, MCP-nudge culture, harness-agnostic agents.
- *"Why start in engineering if the vision is every function?"* → Engineering has the most mature MCP ecosystem, the most measurable outputs, the most technical and budget-aware buyers, the fastest agentic-AI early adoption, and the most concrete architectural patterns (service ownership, on-call, cross-team PRDs) to prove the troop and cartograph model. We land there, prove the model at depth, then expand to Product, Design, Sales, Marketing, Finance, Operations on the same codebase.
- *"Aren't these just chatbots? Or task-autonomous agents like Devin?"* → Neither. Chatbots wait for prompts (Cursor, Copilot, Claude). Devin executes a given task and stops. Polsia runs autonomously with no human in the loop. Ascarto's troops run continuously in the background, work proactively on the developer's behalf, and text the developer when they need a decision, approval, or want to surface news. The human stays in command via async approvals, not by re-prompting to keep the agents working.
- *"Isn't hierarchical troops just surveillance for managers?"* → No. The IC's troop reports to the IC, not to the manager's troop. What aggregate visibility a manager's troop has is what the IC opts in to share — same principle as the appraisal evidence pack. The IC's troop is the IC's shield. Ascarto scales the manager's visibility (with consent) but not their control.

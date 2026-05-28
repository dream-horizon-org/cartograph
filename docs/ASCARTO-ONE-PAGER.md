# Ascarto

> **The agentic AI layer is fragmented. We're organizing it.**
> *An agent troop for every employee — same brain, ten times the responsibility, with a memory the whole org inherits.*

### The Bottleneck: AI made individual work faster, not orchestration.

AI took massive bites out of individual work — generating code, debugging, summarizing docs, even closing whole tickets autonomously. But the developer is still the integration layer between all of it, juggling the same number of services, contexts, and tools they did before. Output per task went up; **span of responsibility per developer stayed flat.** The bottleneck wasn't only execution — it was also coordination, and the agentic wave hasn't touched that.

### The Solution: An org chart for your agents.

Ascarto is the coordination layer above the fragmented AI toolbelt. We give every developer an **Agent Troop** — a dedicated, persistent team of agents sized to their actual surface area of responsibility.

- **The Chief.** The default interface. Translates, prioritizes, delegates, and shields the developer from noise. The human makes the final call; the Chief manages execution. You can also bypass the Chief and address any agent in the troop directly.
- **The Specialists.** Each agent is built on a fixed harness — Claude Code, Copilot, Cursor, Devin, or a custom build — and uses MCPs to interface with your infra (Vercel, GitHub, Sentry, k8s). Different agents in the same troop can run on different harnesses, and harnesses can be swapped over time as better builds emerge.
- **The Coordination.** Ascarto isn't another agent — it's the team layer *above* existing agent harnesses. Troops negotiate with other troops through structured protocols, not Slack threads, and every interaction feeds the org-wide cartograph.

Troops also mirror the human org chart — managers and directors get their own troops, with opt-in visibility into their reports'. Managers get span of oversight; ICs keep sovereignty over their own troop. Same model, every level.

And the troop runs **proactively, not reactively.** Agents don't wait for prompts — they work continuously in the background, and the chief reaches out when a decision, approval, or genuinely surfaceable event needs the developer's attention. You can text the troop any time, but the default direction is troop-to-human, not human-to-troop. Closer to a coworker who works while you're doing something else than to a tool you operate.

Engineering is where we start; the same architecture extends to every function in your org — Product, Design, Sales, Marketing, Finance, Operations — on the same codebase.

### How Ascarto Changes the Work

| Scenario | Current Reality | With Ascarto |
|---|---|---|
| **On-call at 3am** | 90 minutes of log-diving and context-gathering before you can decide what to do. | Service agent isolates logs, drafts rollback, flags the upstream change. Chief gives you three options. You click, sleep. |
| **Cross-team feature** | Two weeks of Slack threads, sync meetings, and conflicting roadmaps before any code gets written. | Troops fan out, negotiate specs directly with other troops, draft architecture. You approve the alignment. |
| **Onboarding** | Three months of stale wikis and awkward questions in #help. | New hire's troop reads the cartograph. First cross-service PR ships Wednesday of week one. |

### The Moat: Why it's defensible

The Troop is the wedge that gets users in the door. The real enterprise value lies in two compounding structural advantages competitors cannot buy:

**1. The Cartograph (Organizational Memory).** Every troop's work — service maps, dependencies, past decisions, common pitfalls — accumulates into a shared org cartograph as the *byproduct* of doing work, not a separate documentation ritual. It compounds with every PR, page, and PRD. New troops onboard from it. Appraisal cycles draw structured evidence from it. Switching cost rises monotonically; no competitor can replicate years of accumulated org-specific data with capital.

**2. The MCP-Nudge Flywheel (Enforced System-Thinking).** Proactive, system-level thinking — exposing your work as an API, considering downstream effects, standardizing interfaces — is today a manual effort limited to a handful of Staff and Principal engineers. Ascarto makes it a structural default. When your Chief notices you're the bottleneck for three other troops, it proactively drafts an MCP spec for you to approve. The system pulls *agent-first* thinking out of every developer in the org. Your junior and mid-level devs become platform-thinkers by default. You get the credit for building legible APIs; the org gets standardized interfaces; the platform team finally gets real demand signal.

### Go-to-Market & Target Audience

We avoid the "replace the developer" trap and focus on augmenting the orchestrator, from the bottom up:

- **Indie Devs & Solo Founders (top of funnel).** Single-user troop, personal cartograph, pre-wired via MCPs to deployment and monitoring providers (Vercel, Fly, Render, Cloudflare, Sentry). The only tool needed between idea and live URL. Drives viral adoption and seeds future enterprise champions.
- **Mid-Market Engineering Orgs (50–500 developers) — the strategic sweet spot.** Highest pain density for cross-team coordination, real budgets, agile enough to bypass the year-long procurement slog of legacy enterprise. Both the cartograph and the MCP-nudge flywheel fully activate at this scale.
- **Buyer persona inside those orgs: Platform / DevEx / SRE leads, plus staff and principal ICs.** They feel the pain of fragmented service ownership most acutely, and they're the ones who push leadership for Ascarto because it solves their structural nightmares — the Cartograph maps the chaos, the MCP-Nudge Flywheel forces the standardization they've been begging for.

Engineering is the wedge. The vision is bigger: every employee in every function — marketing, sales, finance, operations — commands a troop on the same codebase, with one cross-functional cartograph and one MCP economy spanning the whole org. Polsia is betting that AI *replaces* the humans in a company. Ascarto is the augmentation bet: humans stay in command, troops do the work, the org gets smarter as a byproduct.

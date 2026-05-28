# Cartograph

**The agentic AI layer is fragmented. We're organizing it.**

*Agent troops for the engineers who hold the system.*

---

## The pitch in one breath

A senior engineer at a brownfield enterprise doesn't fail because they're dumb. They fail because the system holds more than one head can. Cartograph gives every engineer a **persistent agent troop** — a personal squad of stateful AI specialists, each holding their own slice of the system, negotiating with each other through state machines, never forgetting what they last said.

A senior engineer **can** hold ten services instead of three, because their troop absorbs the coordination overhead.

A new engineer is productive in week one instead of month three, because their troop inherits context — cloned from a peer engineer's troop, or hydrated fresh from cartograph's existing graph of the org.

---

## What's actually new

Not "we put LLMs on it." Two things together:

**1. Persistent stateful specialists, not one-shot copilots.** Each agent in your troop owns one slice (one service, one infra surface, one component), accumulates a workspace over weeks, resumes across sessions, remembers its prior reasoning. The 1-agent = 1-component invariant is structural — splits and merges go through atomic mutations, never through "delete and recreate."

**2. AI-first as protocol, not as discipline.** Most "AI-first" initiatives ask humans to change how they think. Good intentions evaporate the moment people get busy. Cartograph doesn't ask humans to change — it puts an AI-first execution layer (your troop) between you and the work, so the human stays human and the agents stay AI-native. AI-first encoded in spawn defaults, communication state machines, and tool contracts. Survives Monday morning.

---

## Why nobody could solve this before

The hard kernel isn't enumeration. It's **identity reconciliation across heterogeneous evidence** — "is `feeds-agg-v2-api-prod` in AWS the same thing as `feeds-aggregator-v2-api` in Datadog and `dream11/feeds-aggregator-v2` on GitHub?" Every prior approach broke on it:

| Approach | Broke where |
|---|---|
| Service catalogs (Backstage, ServiceNow) | Manual registration drifts the moment engineers stop typing |
| APM auto-discovery (Datadog, Honeycomb) | Only sees instrumented apps; misses DBs, queues, lambdas |
| IaC parsing | Misses click-deployed + dynamic resources; no semantic understanding |
| Rule-based aggregators | Naming conventions differ in every org; cross-plane fuzzy-match unsolvable with rules |
| Naive single-prompt LLM | Context window, no memory, no verification, no negotiation |

What changed: LLMs gained enough reasoning depth to make identity-reconciliation calls reliably, persistent-agent infra stabilised (workspace cwd scoping, MCP-as-permission-boundary, session resume), and embedding-based recall got cheap enough to be a hot-path tool. Cartograph is the first system that combines those into specialist agents that *negotiate* with each other rather than vote.

---

## How the troop works (the dev-facing surface)

```
┌────────────────────────────────────────────────────────────┐
│  YOU (the engineer)                                        │
│  speak in plain English about outcomes                     │
│           ↓ ↑                                              │
│  POC AGENT — your primary point of contact                 │
│  (translates intent, plans work, surfaces blockers)        │
│           ↓ ↑                                              │
│  THE TROOP — specialists you can also address directly:    │
│   • SME agents (one per service/component)                 │
│   • iterators (per plane: github/cloud/telemetry/...)      │
│   • resolver (gatekeeper for cross-specialist decisions)   │
│  ←── inter-agent negotiation via state-machine threads ──→ │
└────────────────────────────────────────────────────────────┘
```

The POC is the primary surface — every interaction *can* pass through it — but it's not a wall. You can chat with any agent in your troop directly when you want depth. The POC keeps things coherent; the troop gives you reach.

---

## What dies on Monday morning, what survives

| Killed by Monday-morning load | What cartograph does |
|---|---|
| "Always think AI-first" cultural memos | Doesn't ask you to. Your troop already is. |
| "Document this for the next person" | The troop IS the documentation, and it onboards the next person directly. |
| "Remember to check the runbook" | Specialist agents own runbook knowledge per-component. |
| "Make sure to update Backstage" | Specialist updates its own component as evidence accumulates; graph stays live. |
| "Junior dev asks senior dev the same question fifth time" | Junior's troop asks senior's troop. Senior never sees it unless a real judgment call surfaces. |

---

## Audience

**Primary:** senior engineers and tech leads at brownfield enterprises holding 5+ services per head. The flywheel needs density — multiple engineers, multiple troops, agents learning org-specific conventions, troops cross-pollinating context.

**Distribution surface (subset offering):** indie developers and small teams — a Polsio-style personal agent troop scoped to their own codebase, no enterprise integration. Same engine, narrower deployment. Acts as a top-of-funnel into the enterprise product when their codebase grows or they join a larger org.

**Not the audience:** orgs with 1-2 engineers and zero institutional knowledge to encode. The troop has nothing to specialise on; it's just a copilot then.

---

## The risks (honest reading)

**Biggest risk — agents drift from each other.** Cross-specialist negotiation is the central bet. If two SMEs reach different conclusions on the same component and never resolve, the graph fragments. Mitigation: the resolver agent + state-machine-enforced consolidation; every merge/split lands at status=D or status=F, never in limbo.

**The POC is the single most-visible agent.** Every interaction *can* pass through it (though devs can address others directly). A bad POC poisons the well even if every specialist is excellent. The POC is a separate, higher-bar engineering effort from the worker agents — treat it like the iPhone home screen.

**Cost.** ~$86 per medium run today, scales with org size + activity. Whether that's cheaper than a manual CMDB team is the real test. We bet yes for any org with 20+ services.

---

## Why now

- Pre-2024 LLMs couldn't hold multi-plane evidence + reason about identity reliably. Opus 4.7 / Sonnet 4.6 with 1M context made it tractable.
- Persistent-agent infra (workspace cwd scoping, MCP-as-permission-boundary, session resume via `claude -p --resume`) only stabilised in the last year.
- Embedding-based recall at low cost (local Ollama + mxbai-embed-large at 40-60ms warm) made vector_search a hot-path tool, not a batch job.

---

## The honest caveat

Cartograph isn't magic. It still needs a human admin to onboard, set scope, triage broadcasts when agents drift. Prompt + tool surface is still evolving (10+ phases of bug-fixes against real-data runs). The pitch isn't "solved forever." It's *"we made the unsolvable shape tractable, and the system gets less wrong every run via the insights loop."*

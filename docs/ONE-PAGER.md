# Cartograph

### Automatically map every service, dependency, and resource across your organisation

---

## What This Is

A system that automatically discovers, materialises, and maps every deployable component across an organisation. It scans multiple data sources -- code repos, deploy scripts, cloud infrastructure, and telemetry -- creates logical "component" entities, attributes all discovered resources to them, and draws dependency edges between them.

A **component** is not a concrete thing that exists in any single system. It's a logical grouping -- an entity we invent to represent something that runs independently. A component might be an API service, a Lambda, a database, a cache cluster, a cron job. The system's job is to propose these entities and attribute every resource (code, infra, config, telemetry) to one of them.

---

## Why

- **No single source of truth** for what components exist and what they depend on
- **Blast radius analysis** -- component X is down, what's affected?
- **Impact analysis** -- I'm changing component X, what could break?
- **Ephemeral environments** -- I want to test these 3 APIs, what components and dependencies do I need to stand up?
- **Resource attribution** -- which team/component owns this ASG, this database, this dashboard?
- **Drift detection** -- what changed in the topology since last scan?

---

## How It Works

Cartograph uses four types of AI agents working in phases:

```
PHASE 1                    PHASE 2                   PHASE 3                    PHASE 4
ITERATE                    MATERIALISE               CONSOLIDATE                RESOLVE

┌──────────────┐          ┌──────────────────┐      ┌────────────────────┐     ┌──────────────┐
│  Iterators   │          │   SME Agents     │      │   SME Negotiation  │     │   Resolver   │
│              │          │                  │      │                    │     │              │
│  One per     │   ──►    │  One per         │ ──►  │  SMEs propose      │ ──► │  Approves    │
│  data source │          │  resource        │      │  merges/splits     │     │  decisions   │
│              │          │                  │      │                    │     │              │
│  Just lists  │          │  Deep analysis   │      │  Back-and-forth    │     │  SMEs        │
│  resources   │          │  of each         │      │  with evidence     │     │  execute     │
│              │          │  resource        │      │  and confidence    │     │              │
└──────────────┘          └──────────────────┘      └────────────────────┘     └──────────────┘
```

**Data sources scanned (each answers a different question):**

| Source | Role | What it tells us |
|--------|------|-----------------|
| **Code repos** | Plane | What components are *built* -- entry points, endpoints, outbound calls |
| **Deploy scripts** | Plane | What components are *deployed* -- Helm charts, deploy configs |
| **Cloud (AWS)** | Plane | What is *actually running* -- ASGs, databases, Lambda functions |
| **Telemetry** | Plane | What is *talking to what* -- real traffic, live dependencies, API usage |
| **Config** | Supporter | How things are *wired together* -- service addresses, secrets, feature flags |

**What makes Cartograph different:** Instead of simple rule-based matching, specialised AI agents *negotiate* about whether two discoveries represent the same component. They exchange evidence, ask questions, and converge on a confidence score -- like two engineers discussing whether "feeds-agg-v2-api-prod" in AWS is the same thing as "feeds-aggregator-v2-api" in GitHub.

---

## Next Steps

**Phase 1 (current):** Validate with additional repos and teams to confirm accuracy and coverage.

**Phase 2:** Connect all data sources and scale to the full organisation.

**Phase 3:** Integrate into day-to-day workflows -- incident response, change review, environment setup.

---

## Why Now

- **AI agents make this feasible for the first time.** Previous approaches required heavy manual configuration or rigid schema definitions. Cartograph's agents read and understand code, infrastructure, and telemetry the way an engineer would -- but across the entire organisation in minutes, not months.

- **The cost of not knowing is growing.** As teams and services multiply, the gap between "what we think we have" and "what actually exists" widens. Every incident, every environment setup, and every capacity review pays the price.

---

*Cartograph -- because you can't manage what you can't see.*

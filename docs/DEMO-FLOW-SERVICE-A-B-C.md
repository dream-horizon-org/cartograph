# Cartograph — End-to-End Flow: service-a, service-b, service-c

> **Purpose:** A concrete walkthrough of every phase of the Cartograph pipeline
> using three real demo services. Intended for engineers who want to understand
> what each agent does, what it reads, what it writes, and why — traced through
> actual code and real data.
>
> **Repos:**
> - `dream-horizon-org/asgard-ra-demo-service-a`
> - `dream-horizon-org/asgard-ra-demo-service-b`
> - `dream-horizon-org/asgard-ra-demo-service-c`

---

## What These Services Actually Are

Before following the agents, here is what the three services do — read
directly from the source code:

### service-a
Spring Boot application on port 8080. The **entry point** of the call
graph. It calls both service-b and service-c depending on the endpoint
hit, and writes every request to its own MySQL table.

Endpoints it exposes:
```
GET /api/v1/a-b    → calls service-b  GET /api/v1/b-only
GET /api/v1/a-b-c  → calls service-b  GET /api/work  (B then calls C)
GET /api/v1/a-c    → calls service-c  GET /api/work
GET /api/work      → calls service-b  GET /api/work  (B then calls C)
```

Downstream URLs configured in `application.yml`:
```yaml
downstream:
  service-b:
    base-url: ${SERVICE_B_BASE_URL:http://service-b-dt-2.asgard-stag.dss-platform.private}
  service-c:
    base-url: ${SERVICE_C_BASE_URL:http://service-c-dt-2.asgard-stag.dss-platform.private}
```

MySQL:
```yaml
spring.datasource.url: jdbc:mysql://${DB_HOST:localhost}:3306/${DB_NAME:demo}
```
Schema: table `service_a_request_log(id, request_id, api_name, received_at, downstream_status)`

Telemetry: pushes OTLP metrics to `otlp.last9.io` via `LAST9_OTLP_URL`.

### service-b
Spring Boot application on port 8080. The **middle tier**. Has a fault
injection mechanism (`FaultConfigController`) for chaos testing.

Endpoints it exposes:
```
GET /api/work       → calls service-c  GET /api/work
GET /api/v1/b-only  → leaf (no downstream call)
GET /api/fault      → read/write fault config (inject latency / 4xx / 5xx)
```

Downstream URL:
```yaml
downstream:
  service-c:
    base-url: ${SERVICE_B_BASE_URL:http://service-c-dt-2.asgard-stag.dss-platform.private}
```
(Note: the env var is named `SERVICE_B_BASE_URL` but the default value points at service-c — an intentional demo quirk.)

MySQL: same pattern as service-a. Schema: `service_b_request_log`.

### service-c
Spring Boot application on port 8080. The **leaf**. Accepts optional
`?fail=true&latencyMs=N` query params for chaos testing. No outbound
HTTP calls.

Endpoints it exposes:
```
GET /api/work   → leaf — reads params, writes to MySQL, returns ok/error
```

MySQL: `service_c_request_log`.

### The Call Graph (ground truth)

```
Client
  │
  ▼
service-a  (service-a-dt-2.asgard-stag.dss-platform.private)
  ├── GET /api/v1/a-b   ──────────────────► service-b /api/v1/b-only   (leaf)
  ├── GET /api/v1/a-b-c ──► service-b /api/work ──► service-c /api/work
  ├── GET /api/v1/a-c   ──────────────────────────► service-c /api/work
  └── GET /api/work     ──► service-b /api/work ──► service-c /api/work
  │
  └── MySQL (service-a DB)

service-b  (service-b-dt-2.asgard-stag.dss-platform.private)
  └── MySQL (service-b DB)

service-c  (service-c-dt-2.asgard-stag.dss-platform.private)
  └── MySQL (service-c DB)
```

This is what Cartograph needs to **discover automatically** from the code —
with no prior knowledge of what these services are or how they connect.

---

## Phase 1 — User Input

**Agent active:** Orchestrator

The admin opens the Cartograph admin UI at `localhost:8200`, selects the
orchestrator agent, and sends a chat message:

```
I want to map three services:
  - dream-horizon-org/asgard-ra-demo-service-a
  - dream-horizon-org/asgard-ra-demo-service-b
  - dream-horizon-org/asgard-ra-demo-service-c

GitHub PAT: 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
```

### What the orchestrator does

Admin messages are the highest priority item when the orchestrator wakes.
It reads the message and immediately:

**1. Stores the GitHub PAT as a secret** (never leaves it in chat history):
```
put_secret(plane='github', key='github_token',
           value='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')
```

This writes to the `secrets` table:
```
plane='github'  key='github_token'  value=<encrypted>
```

**2. Replies to admin:**
> "Token stored as secret (plane=github, key=github_token). Creating
> github iterator to enumerate the three repos."

**3. Creates the github iterator:**
```
create_agent(new_agent_type='iterator', plane='github')
```

This creates a row in `agent_runs`:
```
agent_id='iter-github-xxxx'  agent_type='iterator'
plane='github'  status='pending'
workspace_path='workspaces/iter-github-xxxx'
```

The trigger manager sees the new idle agent and wakes it.

---

## Phase 2 — Iteration

**Agent active:** `iter-github-xxxx`

The iterator's job is narrow: **enumerate resources**. It does not analyse
code. It does not create components. It only populates the `resources` table
with heuristic candidates.

### What the iterator does

It receives a task from the orchestrator: "enumerate the three specified
repos — treat them as exhaustive scope." It runs:

**1. Access precheck** — before any enumeration:
```bash
curl -s -H "Authorization: token <github_token>" \
  https://api.github.com/user
```
If this returns 401, it raises a blocker immediately. No point proceeding
without auth.

**2. Upserts the 3 resource rows** in one atomic call:
```
upsert_resources_bulk(plane='github', items=[
  {
    resource_type: 'repo',
    identifier: 'dream-horizon-org/asgard-ra-demo-service-a',
    access_desc: 'Spring Boot service. Calls service-b and service-c. Uses MySQL.',
    metadata: {
      org: 'dream-horizon-org',
      repo: 'asgard-ra-demo-service-a',
      clone_url: 'https://github.com/dream-horizon-org/asgard-ra-demo-service-a.git',
      default_branch: 'main',
      language: 'Java'
    }
  },
  {
    resource_type: 'repo',
    identifier: 'dream-horizon-org/asgard-ra-demo-service-b',
    ...
  },
  {
    resource_type: 'repo',
    identifier: 'dream-horizon-org/asgard-ra-demo-service-c',
    ...
  }
])
```

**3. Reports to the orchestrator** (responds to its task): "Upserted 3 resources
on plane=github. Seen: 3, upserted: 3, filtered: 0."

### DB state after Phase 2

`resources` table — 3 rows:

| id | plane | resource_type | identifier | status |
|----|-------|---------------|------------|--------|
| uuid-A | github | repo | dream-horizon-org/asgard-ra-demo-service-a | pending |
| uuid-B | github | repo | dream-horizon-org/asgard-ra-demo-service-b | pending |
| uuid-C | github | repo | dream-horizon-org/asgard-ra-demo-service-c | pending |

`components` table — **0 rows**. No components exist yet. The iterator's
job is done.

---

## Phase 3 — Materialisation

**Agents active:** Orchestrator (coordinator) + 3 SMEs (one per resource)

### Orchestrator: gatekeeper check before spawning

Before spawning any SMEs, the orchestrator validates the iterator's output:
```
get_resource_counts()
→ {github: {pending: 3}}
```

Scale heuristic for github plane: 100–2000 repos expected. 3 is well within
range. Spawns all 3 SMEs in one call:
```
bulk_spawn_smes(plane='github', all_pending=True,
                task_description='Materialise your assigned repo...')
```

This atomically:
- Creates 3 rows in `agent_runs` (status='pending')
- Creates 3 rows in `resource_component_agents` with `component_id=NULL`
  (reservation slots — the SME is assigned but hasn't materialised yet)
- Flips all 3 resource rows to status='assigned'
- Creates 3 BW tasks (one per SME) as their wake signal

The trigger manager sees 3 new idle agents with pending tasks and begins
waking them. All 3 run concurrently (up to 12 SME lanes available).

---

### SME for service-a

#### Step 0 — Clone the repo

The SME reads its assigned resource identifier from the RCA table:
`dream-horizon-org/asgard-ra-demo-service-a`. It fetches the github token:
```
get_secret(plane='github', key='github_token')
```

Then clones into its workspace:
```bash
git clone https://x-access-token:ghp_xxx@github.com/\
  dream-horizon-org/asgard-ra-demo-service-a.git
```

It now has the full working tree on disk. **Without this, everything below
is impossible.** GitHub API metadata alone would give a repo name and a
file list — not the actual endpoint handlers or downstream URLs.

#### Step 1 — Identify the service, create the component

The SME reads `application.yml`, `service-definition/definition.json`, and
`pom.xml`. It learns:
- Service name: `service-a`
- Port: 8080
- Hostname pattern: `${ODIN_COMPONENT_NAME}-${ODIN_ENV_NAME}.${PRIVATE_HOSTED_ZONE}`
  → resolves to `service-a-dt-2.asgard-stag.dss-platform.private`
- MySQL: `jdbc:mysql://${DB_HOST}:3306/${DB_NAME:demo}`
- Odin definition: component type=application, artifact=service-a

It calls `upsert_component` once to fill the RCA reservation:
```
upsert_component({
  canonical_name: 'service-a',
  display_name:   'Service A',
  component_type: 'application',
  description:    'Spring Boot entry-point service. Exposes /api/v1/a-b,
                   /api/v1/a-b-c, /api/v1/a-c, /api/work. Calls
                   service-b and service-c. Writes request logs to MySQL.
                   Hostname: service-a-dt-2.asgard-stag.dss-platform.private',
  component_doc_md: """
    ## Role
    Entry-point HTTP service in the demo call graph. Receives external
    requests and fans them out to service-b and/or service-c depending
    on the endpoint. Logs every request to MySQL.

    ## Key Surfaces
    - GET /api/v1/a-b     — calls service-b /api/v1/b-only
    - GET /api/v1/a-b-c   — calls service-b /api/work (which calls service-c)
    - GET /api/v1/a-c     — calls service-c /api/work directly
    - GET /api/work       — calls service-b /api/work (which calls service-c)

    ## Outbound Flows
    - calls service-b at service-b-dt-2.asgard-stag.dss-platform.private
    - calls service-c at service-c-dt-2.asgard-stag.dss-platform.private
    - reads_from/writes_to MySQL at ${DB_HOST} db=demo

    ## Runtime + Deploy
    Java / Spring Boot 3. Port 8080. Deployed via Odin (service-a artifact).
    Hostname: service-a-dt-2.asgard-stag.dss-platform.private

    ## Storage + State
    MySQL: service_a_request_log(id, request_id, api_name, received_at,
    downstream_status). One row per inbound request.

    ## Source
    ApiController.java — all 4 endpoint handlers
    WorkController.java — /api/work handler
    ServiceBClient.java — outbound calls to service-b
    ServiceCClient.java — outbound calls to service-c
  """,
  metadata: {language: 'Java', framework: 'Spring Boot', port: 8080}
})
```

#### Step 2 — Attributions

The SME hydrates every concrete piece of evidence tying real things to
this component. Key rule: **attributions are facts about WHAT THIS SERVICE IS**,
not what it calls (those are edges).

```
upsert_attributions_bulk(component_id='service-a', attributions=[
  {plane:'github', resource_type:'repo',
   identifier:'dream-horizon-org/asgard-ra-demo-service-a'},
  {plane:'github', resource_type:'hostname',
   identifier:'service-a-dt-2.asgard-stag.dss-platform.private'},
  {plane:'github', resource_type:'runtime',      identifier:'Java 17'},
  {plane:'github', resource_type:'framework',    identifier:'Spring Boot'},
  {plane:'github', resource_type:'odin_service', identifier:'service-a'},
  {plane:'github', resource_type:'deploy_artifact', identifier:'service-a:1.0.8'},
  {plane:'github', resource_type:'otlp_endpoint',
   identifier:'otlp.last9.io'},
])
```

Note: `DB_HOST` is an env var — no concrete hostname known from code
alone. The SME records this as an unresolved reference (Step 3), not
an attribution.

#### Step 2b — Catalogs

Grep for endpoint annotations in the cloned repo:
```bash
grep -rn '@GetMapping\|@PostMapping\|@RequestMapping' src/
```

Finds (in `ApiController.java` and `WorkController.java`):
- `@GetMapping("/api/v1/a-b")`       line ApiController.java:28
- `@GetMapping("/api/v1/a-b-c")`     line ApiController.java:36
- `@GetMapping("/api/v1/a-c")`       line ApiController.java:44
- `@GetMapping("/api/work")`          line WorkController.java:19

Declares 4 catalog rows:
```
upsert_catalog(kind='endpoint', identifier='/api/v1/a-b')
upsert_catalog(kind='endpoint', identifier='/api/v1/a-b-c')
upsert_catalog(kind='endpoint', identifier='/api/v1/a-c')
upsert_catalog(kind='endpoint', identifier='/api/work')
```

#### Step 3 — Outbound edges

Grep for downstream URLs and DB connections:
```bash
grep -rn 'base-url\|SERVICE_B_BASE_URL\|SERVICE_C_BASE_URL\|jdbc:' src/ application.yml
```

Finds:
1. `service-b-dt-2.asgard-stag.dss-platform.private` — service-b base URL
2. `service-c-dt-2.asgard-stag.dss-platform.private` — service-c base URL
3. `jdbc:mysql://${DB_HOST}:3306/${DB_NAME:demo}` — MySQL

For each outbound, the SME runs `vector_search` to find if a target component
already exists. Since this run has 3 concurrent SMEs, service-b and
service-c may or may not be materialised yet. Assuming they haven't been
yet (common — all 3 materialise concurrently):

**Outbound to service-b — no match yet (dangling):**
```
upsert_edge_outbound({
  from_component_id: 'service-a',
  to_component_id:   NULL,              ← dangling; target not yet in DB
  edge_type:         'calls',
  identifier:        '/api/v1/b-only',  ← path only, no hostname prefix
  metadata: {
    hostname: 'service-b-dt-2.asgard-stag.dss-platform.private',
    source_file: 'ServiceBClient.java:42'
  }
})
insert_unresolved(reference_type='hostname',
                  reference_value='service-b-dt-2.asgard-stag.dss-platform.private')

upsert_edge_outbound({
  from_component_id: 'service-a',
  to_component_id:   NULL,
  edge_type:         'calls',
  identifier:        '/api/work',
  metadata: {
    hostname: 'service-b-dt-2.asgard-stag.dss-platform.private',
    source_file: 'ServiceBClient.java:25'
  }
})
```

**Outbound to service-c — no match yet (dangling):**
```
upsert_edge_outbound({
  from_component_id: 'service-a',
  to_component_id:   NULL,
  edge_type:         'calls',
  identifier:        '/api/work',
  metadata: {
    hostname: 'service-c-dt-2.asgard-stag.dss-platform.private',
    source_file: 'ServiceCClient.java:28'
  }
})
insert_unresolved(reference_type='hostname',
                  reference_value='service-c-dt-2.asgard-stag.dss-platform.private')
```

**Outbound to MySQL — non-code target (dangling + inferred stub):**

MySQL will never be materialised by another SME on this single github-plane run.
The SME writes the dangling pair:
```
upsert_edge_outbound({
  from_component_id: 'service-a',
  to_component_id:   NULL,
  edge_type:         'reads_from',
  identifier:        'demo',            ← DB name from DB_NAME env default
  metadata: {
    db_host_env: 'DB_HOST',
    db_system: 'mysql',
    db_port: 3306,
    source_file: 'application.yml'
  }
})
insert_unresolved(reference_type='database', reference_value='demo')
```

Then, because this is a non-code target on a single-plane run, the SME
**must** nominate an inferred stub:
```
nominate_consolidation(
  type='split',
  component_a_id='service-a',
  component_b_id=NULL,
  confidence=0.95,
  message='[ADMIN-HACK-ORDERS-INFERRING] inferred database component for
           `service-a-mysql` — MySQL DB backing service-a. Discovered via
           jdbc:mysql://${DB_HOST}:3306/${DB_NAME:demo} in application.yml.',
  metadata={
    admin_hack: 'inferring',
    inferred: true,
    inferred_kind: 'database',
    inferred_identifier: 'service-a-mysql'
  }
)
```

#### Step 4 — Flows

With 4 catalog rows and 3 dangling outbound edges, the SME closes the
catalog→outgoing join. For each endpoint, it traces through the handler
code to determine which outbound fires:

```
# /api/v1/a-b → calls service-b /api/v1/b-only
upsert_flow(incoming_catalog_id=<cat:/api/v1/a-b>,
            outgoing_edge_id=<edge:calls /api/v1/b-only>)

# /api/v1/a-b-c → calls service-b /api/work
upsert_flow(incoming_catalog_id=<cat:/api/v1/a-b-c>,
            outgoing_edge_id=<edge:calls /api/work to service-b>)

# /api/v1/a-c → calls service-c /api/work
upsert_flow(incoming_catalog_id=<cat:/api/v1/a-c>,
            outgoing_edge_id=<edge:calls /api/work to service-c>)

# /api/work → calls service-b /api/work
upsert_flow(incoming_catalog_id=<cat:/api/work>,
            outgoing_edge_id=<edge:calls /api/work to service-b>)
```

**Note on flows:** The edges are still dangling (`to_component_id=NULL`).
Flows are declared NOW anyway — the incoming side (catalog) is fixed; the
outgoing edge ID is known. Once edge binding resolves the dangling in Phase
5, the flow row is already in place and powers blast-radius analysis.

#### Step 5 — Mark done
```
mark_resource_done(resource_id='uuid-A')
respond_task(status='WD', message='Materialisation complete. service-a:
  4 catalogs, 3 outbound edges (all dangling), 4 flows,
  1 inferred MySQL nomination pending resolver.')
```

---

### SME for service-b

Follows the same 5-step process. Key findings:

**Endpoints discovered (from `BOnlyController.java`, `WorkController.java`):**
```
GET /api/work          → calls service-c /api/work
GET /api/v1/b-only     → leaf (no downstream)
GET /api/fault         → FaultConfigController (read/write fault settings)
```

**Catalogs:**
```
upsert_catalog(kind='endpoint', identifier='/api/work')
upsert_catalog(kind='endpoint', identifier='/api/v1/b-only')
upsert_catalog(kind='endpoint', identifier='/api/fault')
```

**Outbound edges:**
```
# Calls service-c /api/work (found in ServiceCClient.java:37)
upsert_edge_outbound({
  from_component_id: 'service-b',
  to_component_id:   NULL,   ← dangling
  edge_type:         'calls',
  identifier:        '/api/work',
  metadata: {
    hostname: 'service-c-dt-2.asgard-stag.dss-platform.private'
  }
})
insert_unresolved(reference_type='hostname',
                  reference_value='service-c-dt-2.asgard-stag.dss-platform.private')

# MySQL (same inferred stub pattern)
upsert_edge_outbound(edge_type='reads_from', identifier='demo', to=NULL)
insert_unresolved(reference_type='database', reference_value='demo')
nominate_consolidation(...inferred_identifier='service-b-mysql'...)
```

**Flows:**
```
# /api/work → calls service-c /api/work
upsert_flow(incoming_catalog_id=<cat:/api/work>,
            outgoing_edge_id=<edge:calls /api/work to service-c>)

# /api/v1/b-only — leaf, no outgoing edges
# (documented in component_doc_md as "N/A - leaf endpoint")

# /api/fault — reads/writes fault config in-memory, no outbound
```

---

### SME for service-c

Service-c is the simplest. No outbound HTTP calls at all.

**Endpoint discovered (`WorkController.java`):**
```
GET /api/work   → reads request params, writes to MySQL, returns response
```

**Catalog:**
```
upsert_catalog(kind='endpoint', identifier='/api/work')
```

**Outbound edges:**
```
# No HTTP downstream calls in the code.
# Only outbound is MySQL.
upsert_edge_outbound(edge_type='reads_from', identifier='demo', to=NULL)
insert_unresolved(reference_type='database', reference_value='demo')
nominate_consolidation(...inferred_identifier='service-c-mysql'...)
```

**Flows:**
```
# /api/work → reads/writes MySQL
upsert_flow(incoming_catalog_id=<cat:/api/work>,
            outgoing_edge_id=<edge:reads_from demo>)
```

---

### DB state after Phase 3 (materialisation complete, before edge binding)

**`components` table — 3 rows:**

| canonical_name | type | status |
|----------------|------|--------|
| service-a | application | active |
| service-b | application | active |
| service-c | application | active |

**`catalogs` table — 7 rows:**

| component | kind | identifier |
|-----------|------|------------|
| service-a | endpoint | /api/v1/a-b |
| service-a | endpoint | /api/v1/a-b-c |
| service-a | endpoint | /api/v1/a-c |
| service-a | endpoint | /api/work |
| service-b | endpoint | /api/work |
| service-b | endpoint | /api/v1/b-only |
| service-b | endpoint | /api/fault |
| service-c | endpoint | /api/work |

**`edges` table — 6 dangling rows (all `to_component_id = NULL`):**

| from | edge_type | identifier | to |
|------|-----------|------------|----|
| service-a | calls | /api/v1/b-only | NULL |
| service-a | calls | /api/work | NULL (→ service-b) |
| service-a | calls | /api/work | NULL (→ service-c) |
| service-a | reads_from | demo | NULL (→ MySQL) |
| service-b | calls | /api/work | NULL (→ service-c) |
| service-b | reads_from | demo | NULL (→ MySQL) |
| service-c | reads_from | demo | NULL (→ MySQL) |

**`flows` table — 6 rows (all anchored on catalog ids, edges still dangling):**

| component | incoming catalog | outgoing edge |
|-----------|-----------------|---------------|
| service-a | /api/v1/a-b | calls /api/v1/b-only |
| service-a | /api/v1/a-b-c | calls /api/work (→B) |
| service-a | /api/v1/a-c | calls /api/work (→C) |
| service-a | /api/work | calls /api/work (→B) |
| service-b | /api/work | calls /api/work (→C) |
| service-c | /api/work | reads_from demo |

**`consolidations` table — 3 open split nominations (all at status=R):**
- service-a → inferred `service-a-mysql` (database)
- service-b → inferred `service-b-mysql` (database)
- service-c → inferred `service-c-mysql` (database)

**`unresolved` table — 5 rows:**
- service-b-dt-2.asgard-stag.dss-platform.private (from service-a)
- service-c-dt-2.asgard-stag.dss-platform.private (from service-a)
- service-c-dt-2.asgard-stag.dss-platform.private (from service-b)
- demo / database (from service-a, service-b, service-c)

---

## Phase 4 — Resolver: Inferred MySQL Stubs

**Agent active:** Resolver (singleton)

The resolver wakes when it sees 3 consolidations at status=R. Its job
is to review each nomination and decide: approve (→M) or reject (→F).

### What the resolver does

For each of the 3 inferred-split nominations:

1. Reads the consolidation thread — message body says
   `[ADMIN-HACK-ORDERS-INFERRING]` with `metadata.admin_hack='inferring'`.
2. Checks the pre-flight ordering rule: inferred-splits must run AFTER all
   real splits and BEFORE any merges. No real splits exist, no merges pending.
   Order is valid.
3. Checks for duplicate stubs: `vector_search(name_pattern='service-a-mysql')`
   — no existing component. Safe to proceed.
4. Approves each: `review_consolidation(new_status='M', mutation_assigned_to=<sme>)`

Each originating SME is now `mutation_assigned_to` on its own split nomination
(always the case for splits — the self-nominator executes).

### SMEs execute the splits

Each SME wakes, sees status=M on its consolidation, and calls
`spawn_child_agent`:

**service-a SME spawns the MySQL stub:**
```
spawn_child_agent(
  consolidation_id='...',
  child_component_data={
    canonical_name:  'service-a-mysql',
    display_name:    'Service A MySQL Database',
    component_type:  'database',
    confidence:      0.7,
    metadata: {
      admin_hack: 'inferring',
      inferred: true,
      inferred_kind: 'database',
      inferred_identifier: 'service-a-mysql',
      db_system: 'mysql',
      db_name: 'demo'
    }
  },
  split_briefing="""
    [ADMIN-HACK-ORDERS-INFERRING — INFERRED CHILD]
    You are the MySQL database backing service-a.
    Clone service-a repo, grep for jdbc: and DB_HOST references,
    hydrate attributions (db_system=mysql, db_name=demo, port=3306).
    No catalogs, no outbound edges — you are the target, not a caller.
    ...
  """
)
```

Server mints a fresh `sme-xxxxxxxx` agent_id for the child. The child
wakes, reads the split_briefing, and hydrates:
```
upsert_component(canonical_name='service-a-mysql', component_type='database')
upsert_attribution(resource_type='db_system',  identifier='mysql')
upsert_attribution(resource_type='db_name',    identifier='demo')
upsert_attribution(resource_type='port',       identifier='3306')
upsert_attribution(resource_type='table',      identifier='service_a_request_log')
```

The same happens for `service-b-mysql` and `service-c-mysql`.

### DB state after Phase 4

**`components` table — 6 rows:**

| canonical_name | type | status |
|----------------|------|--------|
| service-a | application | active |
| service-b | application | active |
| service-c | application | active |
| service-a-mysql | database | active |
| service-b-mysql | database | active |
| service-c-mysql | database | active |

**`consolidations` table — 3 rows at status=D** (splits complete)

---

## Phase 5 — Edge Binding

**Agent active:** All 3 application SMEs (concurrently)

The orchestrator sends a broadcast to all SMEs:

> "All application components are now materialised. Bind your dangling
> outbound edges using the cosine ladder. Also bind MySQL danglings to
> your inferred stub children."

Each SME wakes, reads the broadcast, and does:

### service-a SME — binds 3 dangling edges

```
# Retrieve current dangling edges
danglings = get_component_edges('service-a')['outgoing_dangling']
```

**Edge 1: calls /api/v1/b-only (hostname: service-b-dt-2...)**
```
vector_search(query='service-b-dt-2.asgard-stag.dss-platform.private',
              table='attributions')
→ sim=0.98, component_id='service-b'  ← strong match on hostname attribution
```
```
bind_edge(edge_id='<edge-/api/v1/b-only>', to_component_id='service-b')
resolve_reference(unresolved_id='...', resolved_to_component_id='service-b')
```

**Edge 2: calls /api/work (hostname: service-b-dt-2...) — same target:**
```
bind_edge(edge_id='<edge-/api/work-to-b>', to_component_id='service-b')
```

**Edge 3: calls /api/work (hostname: service-c-dt-2...):**
```
vector_search(query='service-c-dt-2.asgard-stag.dss-platform.private',
              table='attributions')
→ sim=0.98, component_id='service-c'
bind_edge(edge_id='<edge-/api/work-to-c>', to_component_id='service-c')
resolve_reference(...)
```

**Edge 4: reads_from MySQL:**
```
vector_search(query='service-a-mysql', table='components')
→ sim=0.99, component_id='service-a-mysql'
bind_edge(edge_id='<edge-reads_from-demo>', to_component_id='service-a-mysql')
resolve_reference(...)
```

### service-b SME — binds 2 dangling edges

```
bind_edge(<calls /api/work to service-c>)   → to_component_id='service-c'
bind_edge(<reads_from demo>)                → to_component_id='service-b-mysql'
```

### service-c SME — binds 1 dangling edge

```
bind_edge(<reads_from demo>)  → to_component_id='service-c-mysql'
```

### DB state after Phase 5

**`edges` table — 7 rows, all now BOUND (`to_component_id` filled):**

| from | edge_type | identifier | to |
|------|-----------|------------|----|
| service-a | calls | /api/v1/b-only | service-b |
| service-a | calls | /api/work | service-b |
| service-a | calls | /api/work | service-c |
| service-a | reads_from | demo | service-a-mysql |
| service-b | calls | /api/work | service-c |
| service-b | reads_from | demo | service-b-mysql |
| service-c | reads_from | demo | service-c-mysql |

**`unresolved` table — all 5 rows now `resolved=TRUE`**

---

## Phase 6 — Final Graph

**The complete Cartograph output for these 3 services:**

### Components (6 nodes)

```
service-a        [application]  service-a-dt-2.asgard-stag.dss-platform.private
service-b        [application]  service-b-dt-2.asgard-stag.dss-platform.private
service-c        [application]  service-c-dt-2.asgard-stag.dss-platform.private
service-a-mysql  [database]     MySQL db=demo (backing service-a)
service-b-mysql  [database]     MySQL db=demo (backing service-b)
service-c-mysql  [database]     MySQL db=demo (backing service-c)
```

### Edges (7 directed edges)

```
service-a  ──[calls /api/v1/b-only]──►  service-b
service-a  ──[calls /api/work]────────►  service-b
service-a  ──[calls /api/work]────────►  service-c
service-a  ──[reads_from demo]────────►  service-a-mysql
service-b  ──[calls /api/work]────────►  service-c
service-b  ──[reads_from demo]────────►  service-b-mysql
service-c  ──[reads_from demo]────────►  service-c-mysql
```

### Catalogs (8 exposed surfaces)

```
service-a:  /api/v1/a-b,  /api/v1/a-b-c,  /api/v1/a-c,  /api/work
service-b:  /api/work,    /api/v1/b-only,  /api/fault
service-c:  /api/work
```

### Flows (6 rows — the "when X is hit, Y fires" wiring)

```
service-a  /api/v1/a-b   ──► calls /api/v1/b-only to service-b
service-a  /api/v1/a-b-c ──► calls /api/work to service-b (→ service-c)
service-a  /api/v1/a-c   ──► calls /api/work to service-c
service-a  /api/work      ──► calls /api/work to service-b (→ service-c)
service-b  /api/work      ──► calls /api/work to service-c
service-c  /api/work      ──► reads_from demo (service-c-mysql)
```

### Full graph diagram

```
                    ┌─────────────────────────────────────────────────┐
                    │                  service-a                       │
                    │  /api/v1/a-b                                     │
                    │  /api/v1/a-b-c                                   │
                    │  /api/v1/a-c                                     │
                    │  /api/work                                       │
                    └──────┬──────────────────┬───────────────────┬───┘
                           │                  │                   │
          calls /api/v1/b-only                │              reads_from
          calls /api/work                     │              service-a-mysql
                           │          calls /api/work             │
                           ▼                  │                   ▼
              ┌────────────────────┐          │        ┌─────────────────────┐
              │     service-b      │          │        │   service-a-mysql   │
              │  /api/work         │          │        │   [database]        │
              │  /api/v1/b-only    │          │        └─────────────────────┘
              │  /api/fault        │          │
              └──────┬─────────────┘          │
                     │                        │
           calls /api/work             calls /api/work
                     │                        │
                     └──────────┬─────────────┘
                                ▼
                    ┌────────────────────┐
                    │     service-c      │
                    │  /api/work         │
                    └────────┬───────────┘
                             │
                        reads_from
                             │
                             ▼
                  ┌────────────────────┐
                  │   service-c-mysql  │
                  │   [database]       │
                  └────────────────────┘

              (service-b-mysql omitted for clarity — same pattern as others)
```

### Blast radius

With this graph in place, Cartograph can answer:

**"service-c goes down — what breaks?"**
```
service-c is DOWN
  → service-b GET /api/work fails (calls service-c)
    → service-a GET /api/v1/a-b-c fails (goes through service-b /api/work)
    → service-a GET /api/work fails (goes through service-b /api/work)
  → service-a GET /api/v1/a-c fails (calls service-c directly)

Unaffected:
  → service-a GET /api/v1/a-b (calls service-b /api/v1/b-only — not impacted)
  → service-b GET /api/v1/b-only (leaf)
```

**"service-b goes down — what breaks?"**
```
service-b is DOWN
  → service-a GET /api/v1/a-b fails
  → service-a GET /api/v1/a-b-c fails
  → service-a GET /api/work fails

Unaffected:
  → service-a GET /api/v1/a-c (direct to service-c, bypasses service-b)
  → service-c entirely (no callers from service-a directly except /api/v1/a-c)
```

This is the output visible in the admin UI graph at `http://localhost:8200`
→ Graph tab. Each node is one component. Each edge is a discovered
dependency. Hover over a node to see its `component_doc_md` — the
human-readable service document the SME wrote.

---

## What Made This Work

| Problem | How Cartograph solved it |
|---|---|
| "Which repos even contain services?" | Iterator enumerated them — one resource row per repo |
| "What endpoints does service-a expose?" | SME cloned the repo and grepped `@GetMapping` patterns |
| "What does service-a call?" | SME read `application.yml` for base URLs, traced `RestTemplate.exchange()` calls in `ServiceBClient.java` / `ServiceCClient.java` |
| "Which service does that hostname belong to?" | `vector_search` on the `attributions` table — service-b's SME had already written `service-b-dt-2...` as its hostname attribution |
| "The MySQL DB has no SME — who materialises it?" | The `[ADMIN-HACK-ORDERS-INFERRING]` path — service-a's SME nominated an inferred stub, resolver approved, child SME hydrated from parent's repo |
| "When does `/api/v1/a-b-c` trigger a call to service-c?" | Flows table — wired during materialisation by tracing: catalog `/api/v1/a-b-c` → edge `calls /api/work to service-b`, and separately service-b's flow: catalog `/api/work` → edge `calls /api/work to service-c` |

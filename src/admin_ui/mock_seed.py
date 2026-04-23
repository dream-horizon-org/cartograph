"""Mockup seed for exercising the Phase 3.9/3.10 graph viz.

Inserts a small realistic topology covering every edge kind:
bound edges, catalog rows (both implicit — with bound callers — and
orphan inbound stubs), dangling outgoings (outbound stubs), and
flows so the 3-zone hover + light-of-sight BFS have something to
traverse.

Every mockup row has `metadata.mock = True` so cleanup can strip them
cleanly without touching real data.

Usage:
    cd src && python -m admin_ui.mock_seed seed
    cd src && python -m admin_ui.mock_seed cleanup

The seed is idempotent — re-running with `seed` updates existing mock
rows in place (ON CONFLICT paths via the canonical_name uniqueness).
"""

from __future__ import annotations

import json
import sys

from shared.db import init_pool, close_pool, execute, execute_mutate, execute_one, execute_returning


MOCK_TAG = {"mock": True}


# ----------------- components -----------------

COMPONENTS = [
    {
        "canonical": "mock/feeds-api",
        "display":   "Feeds API",
        "type":      "application",
        "planes":    ["github", "deploy", "cloud", "telemetry"],
        "doc":       "# Feeds API\nBackend service aggregating sports feeds from SI + Kadamba. Runtime: JVM 17. Hostname: feeds-api.mock.local.\n\n## Source Slice\nCovers `services/feeds-api/` in `mock/feeds-monorepo`.",
    },
    {
        "canonical": "mock/feeds-db",
        "display":   "Feeds DB",
        "type":      "database",
        "planes":    ["cloud", "telemetry"],
        "doc":       "# Feeds DB\nPostgres primary. Holds match + score state.\nHostname: feeds-db.mock.local.",
    },
    {
        "canonical": "mock/feeds-cache",
        "display":   "Feeds Cache",
        "type":      "cache",
        "planes":    ["cloud"],
        "doc":       "# Feeds Cache\nRedis cluster for hot scores. Hostname: feeds-cache.mock.local.",
    },
    {
        "canonical": "mock/auth-svc",
        "display":   "Auth Service",
        "type":      "application",
        "planes":    ["github", "deploy", "cloud"],
        "doc":       "# Auth Service\nHandles token validation + refresh. Runtime: Go 1.22.",
    },
    {
        "canonical": "mock/kyc-svc",
        "display":   "KYC Service",
        "type":      "application",
        "planes":    ["github", "cloud"],
        "doc":       "# KYC Service\nIdentity verification. Runtime: Python 3.12.",
    },
    {
        "canonical": "mock/payments-svc",
        "display":   "Payments Service",
        "type":      "application",
        "planes":    ["github", "deploy", "cloud", "telemetry", "config"],
        "doc":       "# Payments Service\nCharge + refund + ledger. Most-called service in the test topology. Runtime: Node 22.",
    },
    {
        "canonical": "mock/notify-svc",
        "display":   "Notifications",
        "type":      "lambda",
        "planes":    ["cloud"],
        "doc":       "# Notifications Lambda\nFan-out SMS/email/push. Event-triggered from SNS.",
    },
    # Extra callers that converge on payments-svc GET /balance so the
    # midpoint convergence tooltip has interesting fan-in to show.
    {
        "canonical": "mock/search-svc",
        "display":   "Search Service",
        "type":      "application",
        "planes":    ["github", "cloud"],
        "doc":       "# Search Service\nPrefix + fuzzy match over catalog. Runtime: Rust. Hostname: search.mock.local.",
    },
    {
        "canonical": "mock/match-svc",
        "display":   "Match Service",
        "type":      "application",
        "planes":    ["github", "deploy", "cloud", "telemetry"],
        "doc":       "# Match Service\nLive match orchestration. Runtime: Go. Hostname: match.mock.local.",
    },
    {
        "canonical": "mock/user-svc",
        "display":   "User Service",
        "type":      "application",
        "planes":    ["github", "deploy", "cloud"],
        "doc":       "# User Service\nProfile + preferences. Runtime: Node 22.",
    },
    {
        "canonical": "mock/analytics-svc",
        "display":   "Analytics Service",
        "type":      "application",
        "planes":    ["github", "cloud", "telemetry"],
        "doc":       "# Analytics Service\nEvent ingestion → warehouse. Runtime: Python 3.12.",
    },
    {
        "canonical": "mock/audit-svc",
        "display":   "Audit Service",
        "type":      "application",
        "planes":    ["github", "cloud"],
        "doc":       "# Audit Service\nCompliance log stream. Runtime: Go.",
    },

    # --- Chain demo: p→q→r→s→t→u  and  x→y→z→s→t→u (share s, t, u) ---
    # Convergence at s (r and z both call it), then single-file s→t→u.
    # Click p→q or x→y to see full-chain LOS propagate.
    {"canonical": "mock/p", "display": "P", "type": "application",
     "planes": ["github"], "doc": "# P\nChain-1 head."},
    {"canonical": "mock/q", "display": "Q", "type": "application",
     "planes": ["github"], "doc": "# Q\nChain-1 hop."},
    {"canonical": "mock/r", "display": "R", "type": "application",
     "planes": ["github"], "doc": "# R\nChain-1 tail, calls S."},
    {"canonical": "mock/s", "display": "S", "type": "application",
     "planes": ["github", "cloud"],
     "doc": "# S\nConvergence — R and Z both call this."},
    {"canonical": "mock/t", "display": "T", "type": "application",
     "planes": ["github", "cloud"], "doc": "# T\nShared middle."},
    {"canonical": "mock/u", "display": "U", "type": "application",
     "planes": ["github"], "doc": "# U\nFinal callee."},
    {"canonical": "mock/x", "display": "X", "type": "application",
     "planes": ["github"], "doc": "# X\nChain-2 head."},
    {"canonical": "mock/y", "display": "Y", "type": "application",
     "planes": ["github"], "doc": "# Y\nChain-2 hop."},
    {"canonical": "mock/z", "display": "Z", "type": "application",
     "planes": ["github"], "doc": "# Z\nChain-2 tail, calls S."},
]


def seed_components() -> dict[str, str]:
    """Insert/update mock components. Returns {canonical → id}."""
    out: dict[str, str] = {}
    for c in COMPONENTS:
        # Idempotent: UPDATE if canonical_name already exists with our mock tag.
        row = execute_returning(
            """INSERT INTO components
               (canonical_name, display_name, component_type, metadata,
                component_doc_md, confidence)
               VALUES (%s, %s, %s, %s::jsonb, %s, 1.0)
               ON CONFLICT (canonical_name) DO UPDATE
                 SET display_name = EXCLUDED.display_name,
                     component_type = EXCLUDED.component_type,
                     metadata = EXCLUDED.metadata,
                     component_doc_md = EXCLUDED.component_doc_md,
                     updated_at = now()
               RETURNING id""",
            (c["canonical"], c["display"], c["type"],
             json.dumps(MOCK_TAG), c["doc"]),
        )
        out[c["canonical"]] = str(row["id"])
        # Attribute per plane so the graph coloring + legend match.
        for plane in c["planes"]:
            # Use resource_type='mock_marker' so cleanup is surgical.
            execute_mutate(
                """INSERT INTO attributions
                   (component_id, plane, resource_type, identifier, metadata)
                   VALUES (%s, %s, 'mock_marker', %s, %s::jsonb)
                   ON CONFLICT (plane, resource_type, identifier) DO UPDATE
                     SET metadata = EXCLUDED.metadata, last_seen_at = now()""",
                (row["id"], plane, f"{c['canonical']}#{plane}", json.dumps(MOCK_TAG)),
            )
    return out


# ----------------- edges -----------------


def seed_edges(ids: dict[str, str]) -> dict[str, str]:
    """Insert catalog + bound + dangling edges. Returns edge_key → edge_id."""
    edges: dict[str, str] = {}

    # --- catalog rows: each application exposes its endpoints ---
    catalog_defs = [
        ("mock/feeds-api",    "calls", "GET /scores"),
        ("mock/feeds-api",    "calls", "GET /match/:id"),
        ("mock/auth-svc",     "calls", "POST /verify"),
        ("mock/auth-svc",     "calls", "POST /refresh"),
        ("mock/kyc-svc",      "calls", "POST /identity"),
        ("mock/kyc-svc",      "calls", "GET /status"),
        ("mock/payments-svc", "calls", "POST /charge"),
        ("mock/payments-svc", "calls", "POST /refund"),
        ("mock/payments-svc", "calls", "GET /balance"),
        # Chain-demo catalogs (each hop exposes its endpoint)
        ("mock/q", "calls", "q.handle"),
        ("mock/r", "calls", "r.handle"),
        ("mock/s", "calls", "s.handle"),
        ("mock/t", "calls", "t.handle"),
        ("mock/u", "calls", "u.handle"),
        ("mock/y", "calls", "y.handle"),
        ("mock/z", "calls", "z.handle"),
        # --- Orphan catalogs (no bound caller → render as incoming stubs) ---
        # p exposes an endpoint nothing in the graph calls → inbound stub at p.
        ("mock/p", "calls", "p.admin_ping"),
        # s exposes a debug endpoint nothing calls → inbound stub at s.
        ("mock/s", "calls", "s.debug"),
    ]
    for canonical, etype, ident in catalog_defs:
        row = execute_returning(
            """INSERT INTO edges
               (from_component_id, to_component_id, edge_type, identifier,
                metadata, confidence, discovered_by)
               VALUES (NULL, %s, %s, %s, %s::jsonb, 0.95, 'mock-seed')
               ON CONFLICT (to_component_id, edge_type, identifier)
                 WHERE from_component_id IS NULL
               DO UPDATE SET metadata = EXCLUDED.metadata, last_seen_at = now()
               RETURNING id""",
            (ids[canonical], etype, ident, json.dumps(MOCK_TAG)),
        )
        edges[f"catalog::{canonical}::{ident}"] = str(row["id"])

    # --- bound edges ---
    # feeds-api calls several places. Note multiple parallel edges on the
    # same (source, target) pair — the FE curves them to fan out.
    bound_defs = [
        ("mock/feeds-api",    "mock/feeds-db",    "reads_from",   "SELECT * FROM matches"),
        ("mock/feeds-api",    "mock/feeds-cache", "reads_from",   "GET live:scores"),
        ("mock/feeds-api",    "mock/feeds-cache", "writes_to",    "SET live:scores"),
        ("mock/feeds-api",    "mock/auth-svc",    "calls",        "POST /verify"),
        ("mock/feeds-api",    "mock/payments-svc","calls",        "GET /balance"),
        # kyc → auth + payments
        ("mock/kyc-svc",      "mock/auth-svc",    "calls",        "POST /verify"),
        ("mock/kyc-svc",      "mock/payments-svc","calls",        "GET /balance"),
        # payments uses its DB + fan-out
        ("mock/payments-svc", "mock/feeds-db",    "writes_to",    "INSERT ledger"),
        ("mock/payments-svc", "mock/notify-svc",  "triggers",     "payment.completed"),
        # notify-svc writes back
        ("mock/notify-svc",   "mock/feeds-db",    "reads_from",   "SELECT user"),
        # Convergence demo — 6 services all call payments-svc's GET /balance
        # endpoint so the midpoint convergence tooltip + glow has fan-in.
        ("mock/search-svc",   "mock/payments-svc","calls",        "GET /balance"),
        ("mock/match-svc",    "mock/payments-svc","calls",        "GET /balance"),
        ("mock/user-svc",     "mock/payments-svc","calls",        "GET /balance"),
        ("mock/analytics-svc","mock/payments-svc","calls",        "GET /balance"),
        ("mock/audit-svc",    "mock/payments-svc","calls",        "GET /balance"),
        # Plus a second convergence (smaller): 3 services call POST /verify on auth
        ("mock/user-svc",     "mock/auth-svc",    "calls",        "POST /verify"),
        ("mock/match-svc",    "mock/auth-svc",    "calls",        "POST /verify"),
        # Extra outbound edges so the new components have interesting outlets too
        ("mock/search-svc",   "mock/feeds-db",    "reads_from",   "SELECT * FROM catalog"),
        ("mock/match-svc",    "mock/feeds-cache", "reads_from",   "GET live:match:*"),
        ("mock/match-svc",    "mock/notify-svc",  "triggers",     "match.score.update"),
        ("mock/user-svc",     "mock/feeds-db",    "reads_from",   "SELECT * FROM users"),
        ("mock/analytics-svc","mock/feeds-db",    "reads_from",   "SELECT * FROM events"),
        ("mock/audit-svc",    "mock/feeds-db",    "writes_to",    "INSERT audit_log"),

        # --- Chain demo: p→q→r→s→t→u and x→y→z→s→t→u ---
        ("mock/p", "mock/q", "calls", "q.handle"),
        ("mock/q", "mock/r", "calls", "r.handle"),
        ("mock/r", "mock/s", "calls", "s.handle"),
        ("mock/x", "mock/y", "calls", "y.handle"),
        ("mock/y", "mock/z", "calls", "z.handle"),
        ("mock/z", "mock/s", "calls", "s.handle"),
        ("mock/s", "mock/t", "calls", "t.handle"),
        ("mock/t", "mock/u", "calls", "u.handle"),
    ]
    for from_c, to_c, etype, ident in bound_defs:
        row = execute_returning(
            """INSERT INTO edges
               (from_component_id, to_component_id, edge_type, identifier,
                metadata, confidence, discovered_by)
               VALUES (%s, %s, %s, %s, %s::jsonb, 0.88, 'mock-seed')
               ON CONFLICT (from_component_id, to_component_id, edge_type, identifier)
                 WHERE from_component_id IS NOT NULL AND to_component_id IS NOT NULL
               DO UPDATE SET metadata = EXCLUDED.metadata, last_seen_at = now()
               RETURNING id""",
            (ids[from_c], ids[to_c], etype, ident, json.dumps(MOCK_TAG)),
        )
        edges[f"bound::{from_c}->{to_c}::{ident}"] = str(row["id"])

    # --- dangling outgoings: caller knows the URL but target isn't in graph ---
    dangling_defs = [
        ("mock/feeds-api",    "calls", "https://vendor.example.com/feeds/raw"),
        ("mock/kyc-svc",      "calls", "https://third-party-kyc-api.example/v2/check"),
        ("mock/payments-svc", "publishes_to", "slack#payments-alerts"),
        # Chain-demo danglings (outbound stubs)
        # s calls an unknown external metrics sink.
        ("mock/s", "publishes_to", "metrics.unknown"),
        # u emits to an unknown webhook (terminal of the chain but still
        # has side effects).
        ("mock/u", "calls", "https://webhook.unknown/notify"),
    ]
    for from_c, etype, ident in dangling_defs:
        row = execute_returning(
            """INSERT INTO edges
               (from_component_id, to_component_id, edge_type, identifier,
                metadata, confidence, discovered_by)
               VALUES (%s, NULL, %s, %s, %s::jsonb, 0.6, 'mock-seed')
               ON CONFLICT (from_component_id, edge_type, identifier)
                 WHERE to_component_id IS NULL
               DO UPDATE SET metadata = EXCLUDED.metadata, last_seen_at = now()
               RETURNING id""",
            (ids[from_c], etype, ident, json.dumps(MOCK_TAG)),
        )
        edges[f"dangling::{from_c}::{ident}"] = str(row["id"])

    return edges


# ----------------- flows -----------------


def seed_flows(ids: dict[str, str], edges: dict[str, str]) -> int:
    """Insert flows so light-of-sight has paths to traverse. Returns row count."""
    # feeds-api: incoming (someone calls us) → outgoings we fire.
    # We don't have an inbound to feeds-api in the bound set, so craft one
    # by adding a synthetic "admin-ui → feeds-api" ish row? Actually:
    # the catalog rows for feeds-api (from=NULL) are viable "incoming"
    # anchors since they represent the exposed endpoint; the flow uses
    # them as incoming_edge_id.
    # But flows.incoming_edge.to_component_id must equal component_id →
    # catalog rows satisfy that (from=NULL, to=feeds-api). Perfect.

    flow_defs = [
        # When GET /scores hits feeds-api → reads db + cache, may check auth.
        ("mock/feeds-api",
         f"catalog::mock/feeds-api::GET /scores",
         "bound::mock/feeds-api->mock/feeds-db::SELECT * FROM matches"),
        ("mock/feeds-api",
         f"catalog::mock/feeds-api::GET /scores",
         "bound::mock/feeds-api->mock/feeds-cache::GET live:scores"),
        ("mock/feeds-api",
         f"catalog::mock/feeds-api::GET /scores",
         "bound::mock/feeds-api->mock/auth-svc::POST /verify"),

        # GET /match/:id path
        ("mock/feeds-api",
         f"catalog::mock/feeds-api::GET /match/:id",
         "bound::mock/feeds-api->mock/feeds-db::SELECT * FROM matches"),
        ("mock/feeds-api",
         f"catalog::mock/feeds-api::GET /match/:id",
         "bound::mock/feeds-api->mock/feeds-cache::SET live:scores"),

        # kyc: POST /identity path
        ("mock/kyc-svc",
         f"catalog::mock/kyc-svc::POST /identity",
         "bound::mock/kyc-svc->mock/auth-svc::POST /verify"),
        ("mock/kyc-svc",
         f"catalog::mock/kyc-svc::POST /identity",
         "bound::mock/kyc-svc->mock/payments-svc::GET /balance"),

        # payments: POST /charge path
        ("mock/payments-svc",
         f"catalog::mock/payments-svc::POST /charge",
         "bound::mock/payments-svc->mock/feeds-db::INSERT ledger"),
        ("mock/payments-svc",
         f"catalog::mock/payments-svc::POST /charge",
         "bound::mock/payments-svc->mock/notify-svc::payment.completed"),

        # --- Chain demo flows (one catalog → one outgoing per hop) ---
        # p has an ORPHAN catalog (p.admin_ping) that, when hit, fires p→q.
        # Clicking the inbound stub at p should light the full chain.
        ("mock/p",
         "catalog::mock/p::p.admin_ping",
         "bound::mock/p->mock/q::q.handle"),
        ("mock/q",
         "catalog::mock/q::q.handle",
         "bound::mock/q->mock/r::r.handle"),
        ("mock/r",
         "catalog::mock/r::r.handle",
         "bound::mock/r->mock/s::s.handle"),
        ("mock/y",
         "catalog::mock/y::y.handle",
         "bound::mock/y->mock/z::z.handle"),
        ("mock/z",
         "catalog::mock/z::z.handle",
         "bound::mock/z->mock/s::s.handle"),
        ("mock/s",
         "catalog::mock/s::s.handle",
         "bound::mock/s->mock/t::t.handle"),
        ("mock/t",
         "catalog::mock/t::t.handle",
         "bound::mock/t->mock/u::u.handle"),
        # s's s.handle also triggers the metrics.unknown dangling.
        # Click p→q → chain lights through s and into s's dangling.
        ("mock/s",
         "catalog::mock/s::s.handle",
         "dangling::mock/s::metrics.unknown"),
        # u's u.handle (bound incoming from t) triggers the webhook dangling.
        # So the full chain ends at u→webhook.unknown.
        ("mock/u",
         "catalog::mock/u::u.handle",
         "dangling::mock/u::https://webhook.unknown/notify"),

        # match-svc: multiple outgoings per incoming for CALLER-zone demo.
        # We don't have catalog rows for match/search/user, so we fake
        # an "inbound bound" as the incoming anchor by reusing one of
        # their own outgoings as a proxy — but actually the code-correct
        # model is: they don't have catalogs (not applications exposing
        # a fixed API). We skip explicit flows for them — their hover
        # zones still light up via the sibling-outgoing-on-shared-incoming
        # path and via convergence.
    ]
    n = 0
    for component_canonical, in_key, out_key in flow_defs:
        if in_key not in edges or out_key not in edges:
            continue
        execute_mutate(
            """INSERT INTO flows
               (component_id, incoming_edge_id, outgoing_edge_id,
                metadata, confidence, discovered_by)
               VALUES (%s, %s, %s, %s::jsonb, 0.85, 'mock-seed')
               ON CONFLICT (component_id, incoming_edge_id, outgoing_edge_id)
               DO UPDATE SET metadata = EXCLUDED.metadata, updated_at = now()""",
            (ids[component_canonical], edges[in_key], edges[out_key],
             json.dumps(MOCK_TAG)),
        )
        n += 1
    return n


# ----------------- source_slice samples -----------------


def seed_source_slices(ids: dict[str, str]) -> None:
    """Add a couple of source_slice entries so the Slice tab isn't empty."""
    feeds_api_slice = {
        "synthetic-monorepo": {
            "plane": "github",
            "paths": ["services/feeds-api/"],
            "files": ["services/feeds-api/Dockerfile",
                      "services/feeds-api/pom.xml"],
            "manifests": ["deploy/feeds-api.yaml"],
            "workflows": [".github/workflows/feeds-api-ci.yml"],
            "entry_points": ["services/feeds-api/src/main/java/App.java"],
        }
    }
    payments_slice = {
        "synthetic-monorepo": {
            "plane": "github",
            "paths": ["services/payments-svc/"],
            "manifests": ["deploy/payments.yaml"],
            "entry_points": ["services/payments-svc/src/index.ts"],
        }
    }
    execute_mutate(
        "UPDATE components SET source_slice = %s::jsonb WHERE id = %s",
        (json.dumps(feeds_api_slice), ids["mock/feeds-api"]),
    )
    execute_mutate(
        "UPDATE components SET source_slice = %s::jsonb WHERE id = %s",
        (json.dumps(payments_slice), ids["mock/payments-svc"]),
    )


# ----------------- cleanup -----------------


def cleanup() -> dict[str, int]:
    """Delete every mock row. Order matters due to FKs."""
    # Flows reference mock edges; cascade via edges delete.
    # Edges where metadata has mock=true:
    ne = execute_mutate(
        "DELETE FROM edges WHERE (metadata->>'mock')::boolean IS TRUE",
    )
    # Attributions we added:
    na = execute_mutate(
        "DELETE FROM attributions WHERE resource_type = 'mock_marker'",
    )
    # Components we added (by canonical_name prefix):
    nc = execute_mutate(
        "DELETE FROM components WHERE canonical_name LIKE 'mock/%%'",
    )
    # Flows that referenced deleted edges already cascaded, but belt:
    nf = execute_mutate(
        "DELETE FROM flows WHERE discovered_by = 'mock-seed'",
    )
    return {"edges": ne, "attributions": na, "components": nc, "flows_residual": nf}


# ----------------- entry point -----------------


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("seed", "cleanup"):
        print("usage: python -m admin_ui.mock_seed {seed|cleanup}", file=sys.stderr)
        sys.exit(2)

    init_pool()
    try:
        if sys.argv[1] == "seed":
            ids = seed_components()
            edges = seed_edges(ids)
            flow_count = seed_flows(ids, edges)
            seed_source_slices(ids)
            print(json.dumps({
                "components": len(ids),
                "edges":      len(edges),
                "flows":      flow_count,
            }, indent=2))
        else:
            result = cleanup()
            print(json.dumps(result, indent=2))
    finally:
        close_pool()


if __name__ == "__main__":
    main()

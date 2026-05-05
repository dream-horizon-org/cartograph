"""Phase 10.3: deterministic search tools across components, attributions,
edges, catalogs, flows, unresolved.

Covers:
- pure SQL builder (no DB) unit tests on _search_helper.
- per-tool happy path with each filter dimension.
- exact vs ILIKE auto-detection.
- list-valued filter (OR within column).
- multi-filter AND.
- blank-filter refusal.
- name_pattern convenience on components.
- cap-100 enforcement.
"""

import pytest

from shared.db import execute, execute_mutate
from cartograph_mcp.tools import (
    components, catalogs, resources as _res, search,
)
from cartograph_mcp.tools._search_helper import (
    pattern_clause, eq_clause, in_clause, assemble, at_most_one,
    BlankFilterError, MAX_RESULTS,
)


# ============ pure builder tests ============

def test_pattern_clause_exact():
    sql, p = pattern_clause("c.id", "fav2-api")
    assert sql == "c.id = %s"
    assert p == "fav2-api"


def test_pattern_clause_ilike():
    sql, p = pattern_clause("c.id", "auth-%")
    assert sql == "c.id ILIKE %s"
    assert p == "auth-%"


def test_pattern_clause_underscore_is_ilike():
    sql, p = pattern_clause("c.id", "fav2_api")
    assert sql == "c.id ILIKE %s"


def test_pattern_clause_none():
    sql, p = pattern_clause("c.id", None)
    assert sql is None and p is None


def test_in_clause_list_or_within():
    sql, p = in_clause("c.type", ["app", "lambda"])
    assert "IN (" in sql
    assert p == ["app", "lambda"]


def test_in_clause_single_value_collapses_to_eq():
    sql, p = in_clause("c.type", ["app"])
    assert sql == "c.type = %s"
    assert p == "app"


def test_in_clause_scalar_wrapped():
    sql, p = in_clause("c.type", "app")
    # convenience: scalar gets wrapped → still single → collapses to eq
    assert sql == "c.type = %s"
    assert p == "app"


def test_in_clause_empty_list_is_skipped():
    sql, p = in_clause("c.type", [])
    assert sql is None and p is None


def test_assemble_drops_nones_and_joins_with_and():
    where, params = assemble([
        ("a = %s", "x"),
        (None, None),
        ("b ILIKE %s", "y%"),
    ])
    assert where == "a = %s AND b ILIKE %s"
    assert params == ["x", "y%"]


def test_assemble_blank_filter_raises():
    with pytest.raises(BlankFilterError):
        assemble([(None, None), (None, None)])


def test_assemble_flattens_list_params():
    where, params = assemble([
        ("c IN (%s, %s)", ["x", "y"]),
        ("d = %s", "z"),
    ])
    assert params == ["x", "y", "z"]


def test_at_most_one_passes():
    at_most_one(("a", None), ("b", "x"), ("c", None))


def test_at_most_one_rejects_two():
    with pytest.raises(ValueError, match="at most one"):
        at_most_one(("a", "1"), ("b", "2"))


# ============ integration: search_* tools ============

def _iter(agent_factory, aid: str, plane: str = "github"):
    execute_mutate(
        """INSERT INTO agent_runs (agent_id, agent_type, plane, status)
           VALUES (%s, 'iterator', %s, 'idle')""",
        (aid, plane),
    )
    return aid


def _sme_with_component(agent_factory, iter_id: str, sme_id: str,
                         identifier: str, cname: str,
                         component_type: str = "application",
                         doc_md: str | None = None) -> str:
    r = _res.upsert_resource(iter_id, "github", "repo", identifier)
    agent_factory(sme_id, "sme")
    execute_mutate(
        """INSERT INTO resource_component_agents (resource_id, component_id, agent_id)
           VALUES (%s, NULL, %s)""",
        (r["id"], sme_id),
    )
    data = {
        "canonical_name": cname, "display_name": cname.upper(),
        "component_type": component_type,
    }
    if doc_md is not None:
        data["component_doc_md"] = doc_md
    c = components.upsert_component(sme_id, data)
    return c["id"]


def test_search_components_blank_filter_refused(agent_factory):
    _iter(agent_factory, "i")
    agent_factory("admin-search", "orchestrator")
    with pytest.raises(BlankFilterError):
        search.search_components("admin-search", status=None)


def test_search_components_canonical_pattern_exact(agent_factory):
    _iter(agent_factory, "i")
    _sme_with_component(agent_factory, "i", "s1", "o/auth", "auth-svc")
    _sme_with_component(agent_factory, "i", "s2", "o/pay", "payments-svc")
    # Phase 10.7: pass exclude_self=False so s1 sees its own component
    # in results (default is exclude_self=True). This test verifies
    # the pattern-match logic, not exclusion semantics.
    rows = search.search_components("s1", canonical_name_pattern="auth-svc", exclude_self=False)
    assert len(rows) == 1
    assert rows[0]["canonical_name"] == "auth-svc"


def test_search_components_ilike_pattern(agent_factory):
    _iter(agent_factory, "i")
    _sme_with_component(agent_factory, "i", "s1", "o/a1", "auth-svc-v1")
    _sme_with_component(agent_factory, "i", "s2", "o/a2", "auth-svc-v2")
    _sme_with_component(agent_factory, "i", "s3", "o/p", "payments-svc")
    rows = search.search_components("s1", canonical_name_pattern="auth-%", exclude_self=False)
    assert len(rows) == 2
    assert all(r["canonical_name"].startswith("auth-") for r in rows)


def test_search_components_name_pattern_matches_either_column(agent_factory):
    _iter(agent_factory, "i")
    # canonical_name='fav2-api' but display_name='Auth Service' — the
    # exact recall gap that motivated name_pattern.
    r = _res.upsert_resource("i", "github", "repo", "o/fav")
    agent_factory("s1", "sme")
    execute_mutate(
        """INSERT INTO resource_component_agents (resource_id, component_id, agent_id)
           VALUES (%s, NULL, %s)""",
        (r["id"], "s1"),
    )
    components.upsert_component("s1", {
        "canonical_name": "fav2-api",
        "display_name": "Auth Service",  # the human name
        "component_type": "application",
    })
    rows = search.search_components("s1", name_pattern="auth", exclude_self=False)
    assert len(rows) == 1
    assert rows[0]["canonical_name"] == "fav2-api"


def test_search_components_multiple_name_patterns_refused(agent_factory):
    _iter(agent_factory, "i")
    _sme_with_component(agent_factory, "i", "s1", "o/a", "auth")
    with pytest.raises(ValueError, match="at most one"):
        search.search_components(
            "s1", name_pattern="x", canonical_name_pattern="y",
        )


def test_search_components_type_list_or_within(agent_factory):
    _iter(agent_factory, "i")
    _sme_with_component(agent_factory, "i", "s1", "o/a", "auth", "application")
    _sme_with_component(agent_factory, "i", "s2", "o/d", "auth-db", "database")
    _sme_with_component(agent_factory, "i", "s3", "o/c", "auth-cron", "cron")
    rows = search.search_components(
        "s1", canonical_name_pattern="auth%",
        component_type=["application", "cron"],
        exclude_self=False,
    )
    types = {r["component_type"] for r in rows}
    assert types == {"application", "cron"}


def test_search_components_planes_array_in_response(agent_factory):
    _iter(agent_factory, "i")
    _sme_with_component(agent_factory, "i", "s1", "o/a", "a")
    rows = search.search_components(
        "s1", canonical_name_pattern="a", exclude_self=False,
    )
    assert len(rows) == 1
    assert "github" in rows[0]["planes"]


# ----- attributions

def test_search_attributions_by_identifier(agent_factory):
    _iter(agent_factory, "i")
    cid = _sme_with_component(agent_factory, "i", "s1", "o/a", "a")
    components.upsert_attribution("s1", cid, {
        "plane": "github", "resource_type": "env_var",
        "identifier": "DB_HOST",
    })
    components.upsert_attribution("s1", cid, {
        "plane": "github", "resource_type": "env_var",
        "identifier": "API_KEY",
    })
    rows = search.search_attributions(
        "s1", identifier_pattern="DB_%", exclude_self=False,
    )
    assert len(rows) == 1
    assert rows[0]["identifier"] == "DB_HOST"


def test_search_attributions_by_resource_type_list(agent_factory):
    _iter(agent_factory, "i")
    cid = _sme_with_component(agent_factory, "i", "s1", "o/a", "a")
    components.upsert_attribution("s1", cid, {
        "plane": "github", "resource_type": "hostname",
        "identifier": "auth.local",
    })
    components.upsert_attribution("s1", cid, {
        "plane": "github", "resource_type": "asg",
        "identifier": "auth-prod-asg",
    })
    rows = search.search_attributions(
        "s1", resource_type=["asg", "hostname"], exclude_self=False,
    )
    assert len(rows) == 2


# ----- edges

def test_search_edges_by_kind_bound(agent_factory):
    _iter(agent_factory, "i")
    a = _sme_with_component(agent_factory, "i", "s1", "o/a", "a")
    b = _sme_with_component(agent_factory, "i", "s2", "o/b", "b")
    components.upsert_edge_outbound("s1", {
        "from_component_id": a, "to_component_id": b,
        "edge_type": "calls", "identifier": "GET /verify",
    })
    components.upsert_edge_outbound("s1", {
        "from_component_id": a, "to_component_id": None,
        "edge_type": "calls", "identifier": "GET /unknown",
    })
    bound = search.search_edges("s1", kind="bound", exclude_self=False)
    dangling = search.search_edges("s1", kind="dangling", exclude_self=False)
    assert len(bound) == 1
    assert bound[0]["identifier"] == "GET /verify"
    assert len(dangling) == 1
    assert dangling[0]["identifier"] == "GET /unknown"


def test_search_edges_invalid_kind_rejected(agent_factory):
    _iter(agent_factory, "i")
    _sme_with_component(agent_factory, "i", "s1", "o/a", "a")
    with pytest.raises(ValueError, match="kind"):
        search.search_edges("s1", kind="bogus")


def test_search_edges_identifier_pattern(agent_factory):
    _iter(agent_factory, "i")
    a = _sme_with_component(agent_factory, "i", "s1", "o/a", "a")
    b = _sme_with_component(agent_factory, "i", "s2", "o/b", "b")
    components.upsert_edge_outbound("s1", {
        "from_component_id": a, "to_component_id": b,
        "edge_type": "calls", "identifier": "POST /payments/charge",
    })
    components.upsert_edge_outbound("s1", {
        "from_component_id": a, "to_component_id": b,
        "edge_type": "calls", "identifier": "POST /payments/refund",
    })
    rows = search.search_edges(
        "s1", identifier_pattern="%payments%", exclude_self=False,
    )
    assert len(rows) == 2


# ----- catalogs

def test_search_catalogs_by_identifier_and_kind(agent_factory):
    _iter(agent_factory, "i")
    cid = _sme_with_component(agent_factory, "i", "s1", "o/a", "a")
    catalogs.upsert_catalog("s1", cid, "endpoint", "POST /verify")
    catalogs.upsert_catalog("s1", cid, "endpoint", "GET /token")
    catalogs.upsert_catalog("s1", cid, "topic", "user.created")
    rows = search.search_catalogs("s1", kind="endpoint", exclude_self=False)
    assert len(rows) == 2
    rows = search.search_catalogs(
        "s1", identifier_pattern="%verify%", exclude_self=False,
    )
    assert len(rows) == 1
    assert rows[0]["identifier"] == "POST /verify"


# ----- flows

def test_search_flows_by_component(agent_factory):
    _iter(agent_factory, "i")
    cid = _sme_with_component(agent_factory, "i", "s1", "o/a", "a")
    cat = catalogs.upsert_catalog("s1", cid, "endpoint", "POST /x")
    e = components.upsert_edge_outbound("s1", {
        "from_component_id": cid, "to_component_id": None,
        "edge_type": "calls", "identifier": "GET /downstream",
    })
    components.upsert_flow("s1", cid, cat["id"], e["id"])
    rows = search.search_flows("s1", component_id=cid)
    assert len(rows) == 1


# ----- unresolved

def test_search_unresolved_filters_resolved(agent_factory):
    _iter(agent_factory, "i")
    cid = _sme_with_component(agent_factory, "i", "s1", "o/a", "a")
    components.insert_unresolved("s1", {
        "found_in_component_id": str(cid),
        "reference_type": "hostname",
        "reference_value": "mystery.host",
    })
    # Default only_unresolved=True; pass exclude_self=False to see own row.
    rows = search.search_unresolved(
        "s1", reference_value_pattern="%mystery%", exclude_self=False,
    )
    assert len(rows) == 1


# ----- cap

def test_cap_at_max_results(agent_factory):
    """More than 100 attributions → search returns at most 100."""
    _iter(agent_factory, "i")
    cid = _sme_with_component(agent_factory, "i", "s1", "o/a", "a")
    # Generate 105 attributions.
    for i in range(105):
        components.upsert_attribution("s1", cid, {
            "plane": "github", "resource_type": "env_var",
            "identifier": f"VAR_{i:03d}",
        })
    rows = search.search_attributions(
        "s1", identifier_pattern="VAR_%", exclude_self=False,
    )
    assert len(rows) == MAX_RESULTS


# ----- non-existent agent rejected

def test_unknown_agent_refused():
    with pytest.raises(ValueError, match="not found"):
        search.search_components("ghost-agent", canonical_name_pattern="x")


# ============ Phase 10.7: exclude_self on search_* ============


def test_phase10_7_search_components_default_exclude_self_skips_own(agent_factory):
    """Default exclude_self=True. SME's own component must NOT appear."""
    _iter(agent_factory, "i")
    _sme_with_component(agent_factory, "i", "s1", "o/auth", "auth-svc")
    _sme_with_component(agent_factory, "i", "s2", "o/pay", "payments-svc")
    rows = search.search_components("s1", canonical_name_pattern="auth%")
    canonicals = [r["canonical_name"] for r in rows]
    assert "auth-svc" not in canonicals  # caller's own — excluded


def test_phase10_7_search_components_explicit_false_includes_own(agent_factory):
    _iter(agent_factory, "i")
    _sme_with_component(agent_factory, "i", "s1", "o/auth", "auth-svc")
    rows = search.search_components(
        "s1", canonical_name_pattern="auth%", exclude_self=False,
    )
    canonicals = [r["canonical_name"] for r in rows]
    assert "auth-svc" in canonicals


def test_phase10_7_search_components_orch_no_exclusion(agent_factory):
    """Orch owns no components — exclude_self=True must silent-no-op."""
    _iter(agent_factory, "i")
    _sme_with_component(agent_factory, "i", "s1", "o/auth", "auth-svc")
    agent_factory("orch-search", "orchestrator")
    rows = search.search_components(
        "orch-search", canonical_name_pattern="auth%",
    )  # default exclude_self=True
    canonicals = [r["canonical_name"] for r in rows]
    assert "auth-svc" in canonicals  # orch owns nothing → no exclusion fires


def test_phase10_7_search_attributions_exclude_self(agent_factory):
    _iter(agent_factory, "i")
    cid_a = _sme_with_component(agent_factory, "i", "s1", "o/a", "auth-svc")
    cid_b = _sme_with_component(agent_factory, "i", "s2", "o/b", "payments-svc")
    components.upsert_attribution("s1", cid_a, {
        "plane": "github", "resource_type": "endpoint",
        "identifier": "POST /verify",
    })
    components.upsert_attribution("s2", cid_b, {
        "plane": "github", "resource_type": "endpoint",
        "identifier": "POST /charge",
    })
    rows = search.search_attributions("s1", resource_type="endpoint")
    # s1 default exclude_self=True → only s2's attribution surfaces
    idents = [r["identifier"] for r in rows]
    assert "POST /verify" not in idents
    assert "POST /charge" in idents


def test_phase10_7_search_edges_exclude_self_either_side(agent_factory):
    """edges row owned if EITHER endpoint is caller's component."""
    _iter(agent_factory, "i")
    cid_a = _sme_with_component(agent_factory, "i", "s1", "o/a", "auth-svc")
    cid_b = _sme_with_component(agent_factory, "i", "s2", "o/b", "payments-svc")
    cid_c = _sme_with_component(agent_factory, "i", "s3", "o/c", "feeds-svc")
    # Edge from s1's component to s2's component (s1 owns from-side)
    components.upsert_edge_outbound("s1", {
        "from_component_id": cid_a, "to_component_id": cid_b,
        "edge_type": "calls", "identifier": "POST /verify",
    })
    # Edge from s2's component to s3's component (s1 owns neither end)
    components.upsert_edge_outbound("s2", {
        "from_component_id": cid_b, "to_component_id": cid_c,
        "edge_type": "calls", "identifier": "POST /credit",
    })
    rows = search.search_edges("s1", edge_type="calls")  # default exclude_self
    idents = [r["identifier"] for r in rows]
    # s1's edge (from-side ownership) → excluded
    assert "POST /verify" not in idents
    # s2→s3 edge — s1 owns neither end → included
    assert "POST /credit" in idents

"""Phase 7.4: catalogs as a first-class table (separate from edges).

Catalog rows used to live in `edges` with `from_component_id IS NULL`,
borrowing the verb-form edge_type ('calls', 'reads_from', etc.) to
describe what was being EXPOSED. Awkward grammatically — X doesn't
"call" /foo, X is callable AT /foo.

This phase moves catalogs to their own table with a noun-form `kind`
enum (endpoint / topic / queue / data_source / trigger_target).

Backwards-compat wrapper `upsert_edge_catalog` translates the old
edge_type API into the new kind API — existing callers keep working.
"""

import pytest

from shared.db import execute, execute_one, execute_mutate
from cartograph_mcp.tools import components, catalogs as cat, resources as _res


# ---------- helpers ----------

def _setup_sme(agent_factory, suffix="cat"):
    agent_factory(f"iter-{suffix}", "iterator")
    execute_mutate(
        f"UPDATE agent_runs SET plane='github', status='idle' WHERE agent_id='iter-{suffix}'"
    )
    r = _res.upsert_resource(f"iter-{suffix}", "github", "repo", f"o/{suffix}")
    agent_factory(f"sme-{suffix}", "sme")
    execute_mutate(
        "INSERT INTO resource_component_agents (resource_id, agent_id) VALUES (%s, %s)",
        (r["id"], f"sme-{suffix}"),
    )
    cid = components.upsert_component(f"sme-{suffix}", {
        "canonical_name": f"comp-{suffix}",
        "display_name": f"comp-{suffix}",
        "component_type": "application",
    })["id"]
    return f"sme-{suffix}", cid


# ========== Schema ==========


def test_catalogs_table_exists():
    rows = execute(
        "SELECT 1 FROM information_schema.tables WHERE table_name='catalogs'"
    )
    assert len(rows) == 1


def test_catalogs_has_unique_constraint(agent_factory):
    sme, cid = _setup_sme(agent_factory, "uq")
    cat.upsert_catalog(sme, cid, "endpoint", "GET /x")
    # Same (component_id, kind, identifier) → on conflict, just updates.
    out = cat.upsert_catalog(sme, cid, "endpoint", "GET /x", metadata={"new": "field"})
    rows = execute(
        "SELECT COUNT(*) AS n FROM catalogs WHERE component_id=%s::uuid AND kind='endpoint' AND identifier='GET /x'",
        (cid,),
    )
    assert rows[0]["n"] == 1


# ========== upsert_catalog ==========


def test_upsert_catalog_creates(agent_factory):
    sme, cid = _setup_sme(agent_factory, "cr")
    out = cat.upsert_catalog(sme, cid, "endpoint", "POST /verify")
    assert out["kind"] == "endpoint"
    assert out["identifier"] == "POST /verify"
    assert str(out["component_id"]) == str(cid)


def test_upsert_catalog_rejects_non_owner(agent_factory):
    sme_a, c_a = _setup_sme(agent_factory, "owna")
    sme_b, c_b = _setup_sme(agent_factory, "ownb")
    with pytest.raises(ValueError, match="does not own"):
        cat.upsert_catalog(sme_b, c_a, "endpoint", "/x")


def test_upsert_catalog_invalid_kind(agent_factory):
    sme, cid = _setup_sme(agent_factory, "ik")
    with pytest.raises(ValueError, match="Invalid kind"):
        cat.upsert_catalog(sme, cid, "bogus", "/x")


def test_upsert_catalog_metadata_merge(agent_factory):
    sme, cid = _setup_sme(agent_factory, "mm")
    cat.upsert_catalog(sme, cid, "endpoint", "/m", metadata={"a": 1})
    cat.upsert_catalog(sme, cid, "endpoint", "/m", metadata={"b": 2})
    row = execute_one(
        "SELECT metadata FROM catalogs WHERE component_id=%s::uuid AND identifier='/m'",
        (cid,),
    )
    # JSONB merge should preserve both keys.
    assert row["metadata"].get("a") == 1
    assert row["metadata"].get("b") == 2


# ========== get_my_catalogs ==========


def test_get_my_catalogs_returns_owned(agent_factory):
    sme, cid = _setup_sme(agent_factory, "gmc")
    cat.upsert_catalog(sme, cid, "endpoint", "/foo")
    cat.upsert_catalog(sme, cid, "topic", "events.x")
    out = cat.get_my_catalogs(sme)
    kinds = {r["kind"] for r in out}
    assert "endpoint" in kinds
    assert "topic" in kinds


def test_get_my_catalogs_empty_for_no_components(agent_factory):
    agent_factory("loner", "sme")
    assert cat.get_my_catalogs("loner") == []


# ========== get_my_catalog_callers ==========


def test_get_my_catalog_callers_finds_bound_match(agent_factory):
    """A catalog 'endpoint /foo' on B + a bound 'calls /foo' (X→B)
    should show X as a caller of B's catalog."""
    sme_b, c_b = _setup_sme(agent_factory, "callee")
    sme_x, c_x = _setup_sme(agent_factory, "caller")
    cat.upsert_catalog(sme_b, c_b, "endpoint", "/foo")
    # X → B bound edge with edge_type='calls' /foo
    execute_mutate(
        """INSERT INTO edges (from_component_id, to_component_id, edge_type, identifier, discovered_by)
           VALUES (%s, %s, 'calls', '/foo', %s)""",
        (c_x, c_b, sme_x),
    )
    out = cat.get_my_catalog_callers(sme_b)
    # out is a dict keyed by catalog_id → list of {caller_id, ...}
    assert any(
        any(str(caller["caller_id"]) == str(c_x) for caller in callers)
        for callers in out.values()
    )


# ========== get_unmatched_callers ==========


def test_get_unmatched_callers_finds_orphan_bound(agent_factory):
    """A bound 'calls /unknown' (X→B) where B has NO catalog row for
    /unknown should surface as unmatched."""
    sme_b, c_b = _setup_sme(agent_factory, "umb")
    sme_x, c_x = _setup_sme(agent_factory, "umx")
    # B declares /known; X calls /unknown
    cat.upsert_catalog(sme_b, c_b, "endpoint", "/known")
    execute_mutate(
        """INSERT INTO edges (from_component_id, to_component_id, edge_type, identifier, discovered_by)
           VALUES (%s, %s, 'calls', '/unknown', %s)""",
        (c_x, c_b, sme_x),
    )
    out = cat.get_unmatched_callers(sme_b)
    idents = {r["identifier"] for r in out}
    assert "/unknown" in idents


def test_get_unmatched_callers_skips_matched(agent_factory):
    sme_b, c_b = _setup_sme(agent_factory, "skb")
    sme_x, c_x = _setup_sme(agent_factory, "skx")
    cat.upsert_catalog(sme_b, c_b, "endpoint", "/known")
    execute_mutate(
        """INSERT INTO edges (from_component_id, to_component_id, edge_type, identifier, discovered_by)
           VALUES (%s, %s, 'calls', '/known', %s)""",
        (c_x, c_b, sme_x),
    )
    out = cat.get_unmatched_callers(sme_b)
    assert all(r["identifier"] != "/known" for r in out)


# ========== get_orphan_catalogs ==========


def test_get_orphan_catalogs_returns_zero_caller_rows(agent_factory):
    sme, cid = _setup_sme(agent_factory, "orph")
    cat.upsert_catalog(sme, cid, "endpoint", "/orphaned")
    out = cat.get_orphan_catalogs(sme)
    assert any(r["identifier"] == "/orphaned" for r in out)


def test_get_orphan_catalogs_skips_matched(agent_factory):
    sme_b, c_b = _setup_sme(agent_factory, "orphmatchb")
    sme_x, c_x = _setup_sme(agent_factory, "orphmatchx")
    cat.upsert_catalog(sme_b, c_b, "endpoint", "/wanted")
    execute_mutate(
        """INSERT INTO edges (from_component_id, to_component_id, edge_type, identifier, discovered_by)
           VALUES (%s, %s, 'calls', '/wanted', %s)""",
        (c_x, c_b, sme_x),
    )
    out = cat.get_orphan_catalogs(sme_b)
    assert all(r["identifier"] != "/wanted" for r in out)


# ========== Backwards-compat wrapper ==========


def test_upsert_edge_catalog_shim_forwards(agent_factory):
    """The deprecated upsert_edge_catalog tool should accept the old
    API (edge_type='calls') and translate to the new (kind='endpoint')
    via the canonical mapping. Lets existing callers keep working."""
    sme, cid = _setup_sme(agent_factory, "shim")
    out = components.upsert_edge_catalog(sme, {
        "to_component_id": cid,
        "edge_type": "calls",
        "identifier": "GET /shim",
    })
    # The wrapper writes to the catalogs table now.
    row = execute_one(
        "SELECT * FROM catalogs WHERE component_id=%s::uuid AND identifier='GET /shim'",
        (cid,),
    )
    assert row is not None
    assert row["kind"] == "endpoint"


def test_vector_search_includes_catalogs(agent_factory):
    """Phase 7.4 follow-up: vector_search now scans the catalogs table."""
    from cartograph_mcp.tools import search as search_tool
    sme, cid = _setup_sme(agent_factory, "vsc")
    cat.upsert_catalog(sme, cid, "endpoint", "GET /payments/charge")
    out = search_tool.vector_search(sme, "payments charge endpoint", "catalogs", 5)
    if out["query_embedded"]:
        idents = [r["identifier"] for r in out["results"]]
        assert "GET /payments/charge" in idents


def test_vector_search_rejects_unknown_table(agent_factory):
    from cartograph_mcp.tools import search as search_tool
    agent_factory("v-rt", "sme")
    with pytest.raises(ValueError, match="Invalid table"):
        search_tool.vector_search("v-rt", "x", "bogus", 5)

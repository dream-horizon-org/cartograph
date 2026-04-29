"""Phase 8.5: 5 multi-component bulk read tools.

- get_components_bulk: dict[id, component | None]
- get_attributions_bulk: dict[id, list[attr]]
- get_component_edges_bulk: dict[id, {incoming_bound, incoming_catalog, ...}]
- get_catalogs_bulk: dict[id, list[catalog]]
- get_flows_bulk: dict[id, list[flow]]

All open to active agents (no SME gate). Max 500 ids per call.
"""

import pytest

from shared.db import execute, execute_one, execute_mutate, execute_returning
from cartograph_mcp.tools import (
    components, catalogs, resources as _res,
)


def _setup_two_smes(agent_factory):
    """Two SMEs with one component each, populated with attributions /
    catalogs / edges / flows for cross-component testing."""
    sids = []
    cids = []
    for i, repo in enumerate(["o/rb1", "o/rb2"]):
        agent = f"sme-rb{i+1}"
        iter_id = f"iter-{agent}"
        agent_factory(iter_id, "iterator")
        execute_mutate(
            "UPDATE agent_runs SET plane='github', status='idle' WHERE agent_id=%s",
            (iter_id,),
        )
        r = _res.upsert_resource(iter_id, "github", "repo", repo)
        agent_factory(agent, "sme")
        execute_mutate(
            "INSERT INTO resource_component_agents (resource_id, agent_id) VALUES (%s, %s)",
            (r["id"], agent),
        )
        cid = components.upsert_component(agent, {
            "canonical_name": f"comp-{i+1}",
            "display_name": f"comp-{i+1}",
            "component_type": "application",
        })["id"]
        # Hydrate
        components.upsert_attribution(agent, cid, {
            "plane": "github", "resource_type": "endpoint",
            "identifier": f"GET /e{i+1}",
        })
        components.upsert_attribution(agent, cid, {
            "plane": "github", "resource_type": "hostname",
            "identifier": f"comp{i+1}.example",
        })
        catalogs.upsert_catalog(agent, cid, "endpoint", f"POST /c{i+1}")
        edge = execute_returning(
            """INSERT INTO edges (from_component_id, to_component_id, edge_type,
                                  identifier, discovered_by)
               VALUES (%s, %s, 'calls', %s, %s)
               RETURNING id""",
            (cid, cid, f"e-{i+1}", agent),
        )
        cat_for_flow = catalogs.upsert_catalog(agent, cid, "endpoint", f"POST /flowsrc{i+1}")
        components.upsert_flow(agent, cid, cat_for_flow["id"], edge["id"])
        sids.append(agent)
        cids.append(str(cid))
    return sids, cids


# ============ get_components_bulk ============


def test_components_bulk_returns_each(agent_factory):
    _, cids = _setup_two_smes(agent_factory)
    out = components.get_components_bulk("sme-rb1", cids)
    assert set(out.keys()) == set(cids)
    for cid in cids:
        assert out[cid] is not None
        assert "canonical_name" in out[cid]


def test_components_bulk_missing_id_returns_none(agent_factory):
    _, cids = _setup_two_smes(agent_factory)
    fake = "00000000-0000-0000-0000-000000000000"
    out = components.get_components_bulk("sme-rb1", cids + [fake])
    assert out[fake] is None
    assert out[cids[0]] is not None


def test_components_bulk_max_500(agent_factory):
    _setup_two_smes(agent_factory)
    fakes = ["00000000-0000-0000-0000-" + f"{i:012d}" for i in range(501)]
    with pytest.raises(ValueError, match="max 500"):
        components.get_components_bulk("sme-rb1", fakes)


def test_components_bulk_empty_input(agent_factory):
    _setup_two_smes(agent_factory)
    with pytest.raises(ValueError, match="non-empty"):
        components.get_components_bulk("sme-rb1", [])


# ============ get_attributions_bulk ============


def test_attributions_bulk_buckets_per_component(agent_factory):
    _, cids = _setup_two_smes(agent_factory)
    out = components.get_attributions_bulk("sme-rb1", cids)
    assert len(out[cids[0]]) == 2  # endpoint + hostname per setup
    assert len(out[cids[1]]) == 2
    # Cross-leakage check — comp1 attrs should not appear under comp2 key
    comp1_idents = {a["identifier"] for a in out[cids[0]]}
    comp2_idents = {a["identifier"] for a in out[cids[1]]}
    assert comp1_idents.isdisjoint(comp2_idents)


def test_attributions_bulk_missing_returns_empty_list(agent_factory):
    _, cids = _setup_two_smes(agent_factory)
    fake = "00000000-0000-0000-0000-000000000000"
    out = components.get_attributions_bulk("sme-rb1", [fake])
    assert out[fake] == []


# ============ get_component_edges_bulk ============


def test_component_edges_bulk_categorised(agent_factory):
    _, cids = _setup_two_smes(agent_factory)
    out = components.get_component_edges_bulk("sme-rb1", cids)
    for cid in cids:
        bucket = out[cid]
        assert {"incoming_bound", "incoming_catalog",
                "outgoing_bound", "outgoing_dangling"} <= set(bucket.keys())
        # Each comp has 1 self-loop bound edge + 2 catalog rows
        assert len(bucket["outgoing_bound"]) == 1
        assert len(bucket["incoming_catalog"]) == 2


# ============ get_catalogs_bulk ============


def test_catalogs_bulk_per_component(agent_factory):
    _, cids = _setup_two_smes(agent_factory)
    out = catalogs.get_catalogs_bulk("sme-rb1", cids)
    for cid in cids:
        assert len(out[cid]) == 2  # POST /cN + POST /flowsrcN
    # Cross-leakage
    set1 = {c["identifier"] for c in out[cids[0]]}
    set2 = {c["identifier"] for c in out[cids[1]]}
    assert set1.isdisjoint(set2)


# ============ get_flows_bulk ============


def test_flows_bulk_per_component(agent_factory):
    _, cids = _setup_two_smes(agent_factory)
    out = components.get_flows_bulk("sme-rb1", cids)
    for cid in cids:
        assert len(out[cid]) == 1
        assert "incoming_catalog_id" in out[cid][0]
        assert "outgoing_edge_id" in out[cid][0]


# ============ active-agent gate (resolver / iterator can also call) ============


def test_resolver_can_call_read_bulks(agent_factory):
    _, cids = _setup_two_smes(agent_factory)
    agent_factory("res-rb", "resolver")
    out = components.get_attributions_bulk("res-rb", cids)
    assert len(out[cids[0]]) == 2

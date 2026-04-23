"""Phase 3.9: asymmetric edge protocol — catalog / outbound / bind.

Locks in the contracts the SME prompt depends on:
- catalog rows owned by callee (from IS NULL); idempotent
- outbound rows owned by caller; bound or dangling
- bind_edge transitions dangling → bound, refuses collision, no
  silent merge
- multi-source metadata accumulation via || merge + GREATEST() on
  confidence
- backward-compat: create_edge still works
"""

import pytest

from shared.db import execute_mutate, execute, execute_one
from cartograph_mcp.tools import components, resources


def _iter(agent_factory, aid, plane):
    execute_mutate(
        """INSERT INTO agent_runs (agent_id, agent_type, plane, status)
           VALUES (%s, 'iterator', %s, 'idle')""",
        (aid, plane),
    )
    return aid


def _sme_with_component(agent_factory, iterator_id, sme_id, identifier, cname):
    r = resources.upsert_resource(iterator_id, "github", "repo", identifier)
    agent_factory(sme_id, "sme")
    execute_mutate(
        """INSERT INTO resource_component_agents (resource_id, component_id, agent_id)
           VALUES (%s, NULL, %s)""",
        (r["id"], sme_id),
    )
    c = components.upsert_component(sme_id, {
        "canonical_name": cname, "display_name": cname,
        "component_type": "application",
    })
    return str(c["id"])


# ---------- catalog ----------


def test_catalog_write_owner_only(agent_factory):
    _iter(agent_factory, "i", "github")
    cid = _sme_with_component(agent_factory, "i", "sme-a", "o/a", "a")
    row = components.upsert_edge_catalog("sme-a", {
        "to_component_id": cid, "edge_type": "calls",
        "identifier": "GET /balance",
    })
    assert row["from_component_id"] is None
    assert str(row["to_component_id"]) == cid
    assert row["edge_type"] == "calls"
    assert row["identifier"] == "GET /balance"


def test_catalog_write_refused_for_non_owner(agent_factory):
    _iter(agent_factory, "i", "github")
    cid_a = _sme_with_component(agent_factory, "i", "sme-a", "o/a", "a")
    _ = _sme_with_component(agent_factory, "i", "sme-b", "o/b", "b")
    with pytest.raises(ValueError, match="does not own component"):
        components.upsert_edge_catalog("sme-b", {
            "to_component_id": cid_a, "edge_type": "calls",
            "identifier": "GET /sneak",
        })


def test_catalog_idempotent_accumulates_metadata(agent_factory):
    _iter(agent_factory, "i", "github")
    cid = _sme_with_component(agent_factory, "i", "sme-a", "o/a", "a")
    a = components.upsert_edge_catalog("sme-a", {
        "to_component_id": cid, "edge_type": "calls",
        "identifier": "GET /balance",
        "metadata": {"discovered": "code"}, "confidence": 0.7,
    })
    b = components.upsert_edge_catalog("sme-a", {
        "to_component_id": cid, "edge_type": "calls",
        "identifier": "GET /balance",
        "metadata": {"verified": "telemetry"}, "confidence": 0.95,
    })
    assert a["id"] == b["id"]
    assert b["metadata"] == {"discovered": "code", "verified": "telemetry"}
    assert b["confidence"] == 0.95


def test_catalog_unique_per_callee_endpoint_pair(agent_factory):
    """Catalog rows are unique on (to, type, identifier) where from IS NULL."""
    _iter(agent_factory, "i", "github")
    cid = _sme_with_component(agent_factory, "i", "sme-a", "o/a", "a")
    components.upsert_edge_catalog("sme-a", {
        "to_component_id": cid, "edge_type": "calls",
        "identifier": "POST /charge",
    })
    rows = execute(
        """SELECT id FROM edges
           WHERE to_component_id = %s AND edge_type = 'calls'
             AND identifier = 'POST /charge' AND from_component_id IS NULL""",
        (cid,),
    )
    assert len(rows) == 1


# ---------- outbound ----------


def test_outbound_bound_write(agent_factory):
    _iter(agent_factory, "i", "github")
    cid_a = _sme_with_component(agent_factory, "i", "sme-a", "o/a", "a")
    cid_b = _sme_with_component(agent_factory, "i", "sme-b", "o/b", "b")
    row = components.upsert_edge_outbound("sme-a", {
        "from_component_id": cid_a, "to_component_id": cid_b,
        "edge_type": "calls", "identifier": "GET /b",
    })
    assert str(row["from_component_id"]) == cid_a
    assert str(row["to_component_id"]) == cid_b


def test_outbound_dangling_write(agent_factory):
    _iter(agent_factory, "i", "github")
    cid_a = _sme_with_component(agent_factory, "i", "sme-a", "o/a", "a")
    row = components.upsert_edge_outbound("sme-a", {
        "from_component_id": cid_a, "to_component_id": None,
        "edge_type": "calls", "identifier": "https://payments.example/charge",
    })
    assert str(row["from_component_id"]) == cid_a
    assert row["to_component_id"] is None


def test_outbound_self_loop_refused(agent_factory):
    _iter(agent_factory, "i", "github")
    cid_a = _sme_with_component(agent_factory, "i", "sme-a", "o/a", "a")
    with pytest.raises(ValueError, match="must differ"):
        components.upsert_edge_outbound("sme-a", {
            "from_component_id": cid_a, "to_component_id": cid_a,
            "edge_type": "calls", "identifier": "x",
        })


def test_outbound_owner_only(agent_factory):
    _iter(agent_factory, "i", "github")
    cid_a = _sme_with_component(agent_factory, "i", "sme-a", "o/a", "a")
    cid_b = _sme_with_component(agent_factory, "i", "sme-b", "o/b", "b")
    with pytest.raises(ValueError, match="does not own from_component_id"):
        components.upsert_edge_outbound("sme-b", {
            "from_component_id": cid_a, "to_component_id": cid_b,
            "edge_type": "calls", "identifier": "x",
        })


def test_outbound_idempotent_max_confidence(agent_factory):
    _iter(agent_factory, "i", "github")
    cid_a = _sme_with_component(agent_factory, "i", "sme-a", "o/a", "a")
    cid_b = _sme_with_component(agent_factory, "i", "sme-b", "o/b", "b")
    a = components.upsert_edge_outbound("sme-a", {
        "from_component_id": cid_a, "to_component_id": cid_b,
        "edge_type": "calls", "identifier": "GET /b",
        "metadata": {"src": "code"}, "confidence": 0.6,
    })
    b = components.upsert_edge_outbound("sme-a", {
        "from_component_id": cid_a, "to_component_id": cid_b,
        "edge_type": "calls", "identifier": "GET /b",
        "metadata": {"src2": "telemetry"}, "confidence": 0.95,
    })
    assert a["id"] == b["id"]
    assert b["metadata"] == {"src": "code", "src2": "telemetry"}
    assert b["confidence"] == 0.95


# ---------- bind_edge ----------


def test_bind_edge_dangling_to_bound(agent_factory):
    _iter(agent_factory, "i", "github")
    cid_a = _sme_with_component(agent_factory, "i", "sme-a", "o/a", "a")
    cid_b = _sme_with_component(agent_factory, "i", "sme-b", "o/b", "b")
    dangling = components.upsert_edge_outbound("sme-a", {
        "from_component_id": cid_a, "to_component_id": None,
        "edge_type": "calls", "identifier": "https://b.local/x",
    })
    bound = components.bind_edge("sme-a", dangling["id"], cid_b)
    assert bound["id"] == dangling["id"]
    assert str(bound["to_component_id"]) == cid_b


def test_bind_edge_collision_refused(agent_factory):
    _iter(agent_factory, "i", "github")
    cid_a = _sme_with_component(agent_factory, "i", "sme-a", "o/a", "a")
    cid_b = _sme_with_component(agent_factory, "i", "sme-b", "o/b", "b")
    components.upsert_edge_outbound("sme-a", {
        "from_component_id": cid_a, "to_component_id": cid_b,
        "edge_type": "calls", "identifier": "GET /collide",
    })
    dangling = components.upsert_edge_outbound("sme-a", {
        "from_component_id": cid_a, "to_component_id": None,
        "edge_type": "calls", "identifier": "GET /collide",
    })
    with pytest.raises(ValueError, match="already exists"):
        components.bind_edge("sme-a", dangling["id"], cid_b)


def test_bind_edge_refuses_already_bound(agent_factory):
    _iter(agent_factory, "i", "github")
    cid_a = _sme_with_component(agent_factory, "i", "sme-a", "o/a", "a")
    cid_b = _sme_with_component(agent_factory, "i", "sme-b", "o/b", "b")
    cid_c = _sme_with_component(agent_factory, "i", "sme-c", "o/c", "c")
    bound = components.upsert_edge_outbound("sme-a", {
        "from_component_id": cid_a, "to_component_id": cid_b,
        "edge_type": "calls", "identifier": "GET /b",
    })
    with pytest.raises(ValueError, match="already bound"):
        components.bind_edge("sme-a", bound["id"], cid_c)


def test_bind_edge_refuses_catalog_row(agent_factory):
    _iter(agent_factory, "i", "github")
    cid = _sme_with_component(agent_factory, "i", "sme-a", "o/a", "a")
    catalog = components.upsert_edge_catalog("sme-a", {
        "to_component_id": cid, "edge_type": "calls",
        "identifier": "GET /balance",
    })
    with pytest.raises(ValueError, match="catalog row"):
        components.bind_edge("sme-a", catalog["id"], cid)


def test_bind_edge_owner_only(agent_factory):
    _iter(agent_factory, "i", "github")
    cid_a = _sme_with_component(agent_factory, "i", "sme-a", "o/a", "a")
    cid_b = _sme_with_component(agent_factory, "i", "sme-b", "o/b", "b")
    dangling = components.upsert_edge_outbound("sme-a", {
        "from_component_id": cid_a, "to_component_id": None,
        "edge_type": "calls", "identifier": "x",
    })
    with pytest.raises(ValueError, match="does not own"):
        components.bind_edge("sme-b", dangling["id"], cid_b)


# ---------- get_component_edges (categorised reads) ----------


def test_get_component_edges_categorisation(agent_factory):
    _iter(agent_factory, "i", "github")
    cid_a = _sme_with_component(agent_factory, "i", "sme-a", "o/a", "a")
    cid_b = _sme_with_component(agent_factory, "i", "sme-b", "o/b", "b")
    # B's catalog
    components.upsert_edge_catalog("sme-b", {
        "to_component_id": cid_b, "edge_type": "calls",
        "identifier": "GET /b",
    })
    # A → B bound
    components.upsert_edge_outbound("sme-a", {
        "from_component_id": cid_a, "to_component_id": cid_b,
        "edge_type": "calls", "identifier": "GET /b",
    })
    # A → ? dangling
    components.upsert_edge_outbound("sme-a", {
        "from_component_id": cid_a, "to_component_id": None,
        "edge_type": "calls", "identifier": "https://unknown/api",
    })

    a_view = components.get_component_edges("sme-a", cid_a)
    assert a_view["incoming_bound"] == []
    assert a_view["incoming_catalog"] == []
    assert len(a_view["outgoing_bound"]) == 1
    assert len(a_view["outgoing_dangling"]) == 1

    b_view = components.get_component_edges("sme-b", cid_b)
    assert len(b_view["incoming_bound"]) == 1
    assert len(b_view["incoming_catalog"]) == 1
    assert b_view["outgoing_bound"] == []
    assert b_view["outgoing_dangling"] == []


# ---------- backward-compat: create_edge + get_edges ----------


def test_create_edge_legacy_shim(agent_factory):
    _iter(agent_factory, "i", "github")
    cid_a = _sme_with_component(agent_factory, "i", "sme-a", "o/a", "a")
    cid_b = _sme_with_component(agent_factory, "i", "sme-b", "o/b", "b")
    edge = components.create_edge("sme-a", {
        "source_id": cid_a, "target_id": cid_b,
        "edge_type": "calls", "identifier": "GET /legacy",
    })
    assert str(edge["from_component_id"]) == cid_a
    assert str(edge["to_component_id"]) == cid_b


def test_get_edges_legacy_shape_excludes_catalog_and_dangling(agent_factory):
    _iter(agent_factory, "i", "github")
    cid_a = _sme_with_component(agent_factory, "i", "sme-a", "o/a", "a")
    cid_b = _sme_with_component(agent_factory, "i", "sme-b", "o/b", "b")
    # Catalog row on A — should NOT appear in get_edges()
    components.upsert_edge_catalog("sme-a", {
        "to_component_id": cid_a, "edge_type": "calls",
        "identifier": "GET /a-catalog",
    })
    # Bound A → B
    components.upsert_edge_outbound("sme-a", {
        "from_component_id": cid_a, "to_component_id": cid_b,
        "edge_type": "calls", "identifier": "GET /b",
    })
    # Dangling outgoing from A — should NOT appear
    components.upsert_edge_outbound("sme-a", {
        "from_component_id": cid_a, "to_component_id": None,
        "edge_type": "calls", "identifier": "https://?/x",
    })
    a_legacy = components.get_edges("sme-a", cid_a)
    assert len(a_legacy["outbound"]) == 1   # only the bound A→B
    assert a_legacy["inbound"] == []

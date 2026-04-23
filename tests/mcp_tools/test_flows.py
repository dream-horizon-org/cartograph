"""Phase 3.9: flows table + upsert_flow / get_flow / get_flow_inverse."""

import pytest

from shared.db import execute_mutate, execute_one
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


def _wire_flow_test(agent_factory):
    """Build a 3-component graph: A is the middleware. Caller calls A;
    A calls callee. Returns (cid_caller, cid_a, cid_callee, incoming, outgoing)."""
    _iter(agent_factory, "i", "github")
    cid_caller = _sme_with_component(agent_factory, "i", "sme-c", "o/caller", "caller")
    cid_a = _sme_with_component(agent_factory, "i", "sme-a", "o/a", "middleware")
    cid_callee = _sme_with_component(agent_factory, "i", "sme-d", "o/d", "callee")
    incoming = components.upsert_edge_outbound("sme-c", {
        "from_component_id": cid_caller, "to_component_id": cid_a,
        "edge_type": "calls", "identifier": "POST /process",
    })
    outgoing = components.upsert_edge_outbound("sme-a", {
        "from_component_id": cid_a, "to_component_id": cid_callee,
        "edge_type": "calls", "identifier": "GET /result",
    })
    return cid_caller, cid_a, cid_callee, str(incoming["id"]), str(outgoing["id"])


def test_upsert_flow_happy_path(agent_factory):
    _, cid_a, _, incoming_id, outgoing_id = _wire_flow_test(agent_factory)
    f = components.upsert_flow("sme-a", cid_a, incoming_id, outgoing_id)
    assert str(f["component_id"]) == cid_a
    assert str(f["incoming_edge_id"]) == incoming_id
    assert str(f["outgoing_edge_id"]) == outgoing_id


def test_upsert_flow_owner_only(agent_factory):
    _, cid_a, _, incoming_id, outgoing_id = _wire_flow_test(agent_factory)
    with pytest.raises(ValueError, match="does not own"):
        components.upsert_flow("sme-c", cid_a, incoming_id, outgoing_id)


def test_upsert_flow_validates_incoming_edge_target(agent_factory):
    """Incoming edge must point at component_id (its to_component_id == component_id).
    Pass the OUTGOING edge in the incoming slot — its to is cid_callee, not cid_a."""
    _, cid_a, _, incoming_id, outgoing_id = _wire_flow_test(agent_factory)
    with pytest.raises(ValueError, match="does not point at component"):
        components.upsert_flow("sme-a", cid_a, outgoing_id, incoming_id)


def test_upsert_flow_validates_outgoing_edge_source(agent_factory):
    """Outgoing edge must originate from component_id (from_component_id == component_id).
    Use a valid incoming + an edge whose from is NOT cid_a as the outgoing."""
    cid_caller, cid_a, _, incoming_id, _ = _wire_flow_test(agent_factory)
    # Add a second edge from caller→A so we have two valid incomings; reuse
    # incoming_id as a NOT-from-cid_a edge to put in the outgoing slot.
    second_incoming = components.upsert_edge_outbound("sme-c", {
        "from_component_id": cid_caller, "to_component_id": cid_a,
        "edge_type": "calls", "identifier": "POST /process2",
    })
    with pytest.raises(ValueError, match="does not originate from component"):
        components.upsert_flow("sme-a", cid_a, str(second_incoming["id"]), incoming_id)


def test_upsert_flow_idempotent_metadata_accumulation(agent_factory):
    _, cid_a, _, incoming_id, outgoing_id = _wire_flow_test(agent_factory)
    a = components.upsert_flow(
        "sme-a", cid_a, incoming_id, outgoing_id,
        metadata={"src": "code"}, confidence=0.6,
    )
    b = components.upsert_flow(
        "sme-a", cid_a, incoming_id, outgoing_id,
        metadata={"src2": "telemetry"}, confidence=0.9,
    )
    assert a["id"] == b["id"]
    assert b["metadata"] == {"src": "code", "src2": "telemetry"}
    assert b["confidence"] == 0.9


def test_get_flow_returns_outgoing_edges(agent_factory):
    _, cid_a, _, incoming_id, outgoing_id = _wire_flow_test(agent_factory)
    components.upsert_flow("sme-a", cid_a, incoming_id, outgoing_id)
    rows = components.get_flow("sme-a", cid_a, incoming_id)
    assert len(rows) == 1
    assert str(rows[0]["id"]) == outgoing_id


def test_get_flow_inverse_returns_incoming_edges(agent_factory):
    _, cid_a, _, incoming_id, outgoing_id = _wire_flow_test(agent_factory)
    components.upsert_flow("sme-a", cid_a, incoming_id, outgoing_id)
    rows = components.get_flow_inverse("sme-a", cid_a, outgoing_id)
    assert len(rows) == 1
    assert str(rows[0]["id"]) == incoming_id


def test_flow_cascades_on_edge_delete(agent_factory):
    _, cid_a, _, incoming_id, outgoing_id = _wire_flow_test(agent_factory)
    components.upsert_flow("sme-a", cid_a, incoming_id, outgoing_id)
    # Delete the incoming edge → cascade should remove the flow row.
    execute_mutate("DELETE FROM edges WHERE id = %s", (incoming_id,))
    n = execute_one(
        "SELECT count(*)::int as n FROM flows WHERE component_id = %s",
        (cid_a,),
    )
    assert n["n"] == 0


def test_flow_fan_out_one_incoming_many_outgoing(agent_factory):
    """Set-based: one incoming → multiple outgoings = multiple flow rows."""
    _, cid_a, cid_callee, incoming_id, outgoing_1 = _wire_flow_test(agent_factory)
    # Add a second outgoing from A to a new target on the same plane.
    cid_other = _sme_with_component(agent_factory, "i", "sme-e", "o/e", "other")
    outgoing_2 = components.upsert_edge_outbound("sme-a", {
        "from_component_id": cid_a, "to_component_id": cid_other,
        "edge_type": "publishes_to", "identifier": "events.process",
    })
    components.upsert_flow("sme-a", cid_a, incoming_id, outgoing_1)
    components.upsert_flow("sme-a", cid_a, incoming_id, str(outgoing_2["id"]))
    rows = components.get_flow("sme-a", cid_a, incoming_id)
    assert len(rows) == 2
    assert {str(r["id"]) for r in rows} == {outgoing_1, str(outgoing_2["id"])}

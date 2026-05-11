"""Phase 7.4.2: flows reference catalogs first-class.

Pre-Phase 7.4 flows.incoming_edge_id pointed at a catalog row inside
`edges` (where from_component_id IS NULL). Phase 7.4 moved catalogs to
their own table, and Phase 7.4.2 follows through: flows.incoming_edge_id
→ flows.incoming_catalog_id (NOT NULL FK to catalogs(id) ON DELETE
CASCADE). The "incoming surface" of a flow is canonically the catalog
declaration — bound callers map to it via (target, edge_type, identifier),
which the FE uses for junction conflation + LOS bridging.
"""

import pytest

from shared.db import execute_mutate, execute_one
from cartograph_mcp.tools import components, resources, catalogs as cat


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
    """Build a 3-component graph: A is the middleware that exposes
    POST /process and calls callee at GET /result. Returns
    (cid_caller, cid_a, cid_callee, catalog_id_on_a, outgoing_edge_id)."""
    _iter(agent_factory, "i", "github")
    cid_caller = _sme_with_component(agent_factory, "i", "sme-c", "o/caller", "caller")
    cid_a = _sme_with_component(agent_factory, "i", "sme-a", "o/a", "middleware")
    cid_callee = _sme_with_component(agent_factory, "i", "sme-d", "o/d", "callee")
    # A exposes POST /process — this is the flow's incoming.
    catalog = cat.upsert_catalog("sme-a", cid_a, "endpoint", "POST /process")
    # A calls callee at GET /result — this is the flow's outgoing.
    outgoing = components.upsert_edge_outbound("sme-a", {
        "from_component_id": cid_a, "to_component_id": cid_callee,
        "edge_type": "calls", "identifier": "GET /result",
    })
    return cid_caller, cid_a, cid_callee, str(catalog["id"]), str(outgoing["id"])


def test_upsert_flow_happy_path(agent_factory):
    _, cid_a, _, cat_id, outgoing_id = _wire_flow_test(agent_factory)
    f = components.upsert_flow("sme-a", cid_a, cat_id, outgoing_id)
    assert str(f["component_id"]) == cid_a
    assert str(f["incoming_catalog_id"]) == cat_id
    assert str(f["outgoing_edge_id"]) == outgoing_id


def test_upsert_flow_owner_only(agent_factory):
    _, cid_a, _, cat_id, outgoing_id = _wire_flow_test(agent_factory)
    with pytest.raises(ValueError, match="does not own"):
        components.upsert_flow("sme-c", cid_a, cat_id, outgoing_id)


def test_upsert_flow_validates_catalog_belongs_to_component(agent_factory):
    """Catalog must belong to component_id (catalog.component_id == component_id).
    Use a catalog from a DIFFERENT component — should refuse."""
    cid_caller, cid_a, _, _, outgoing_id = _wire_flow_test(agent_factory)
    # Make a catalog on the caller component, then try to anchor a flow
    # on cid_a using the caller's catalog id.
    foreign_cat = cat.upsert_catalog("sme-c", cid_caller, "endpoint", "POST /elsewhere")
    with pytest.raises(ValueError, match="does not belong to component"):
        components.upsert_flow("sme-a", cid_a, str(foreign_cat["id"]), outgoing_id)


def test_upsert_flow_validates_outgoing_edge_source(agent_factory):
    """Outgoing edge must originate from component_id. Use a bound edge whose
    from_component_id is NOT cid_a (e.g. the caller's outbound edge)."""
    cid_caller, cid_a, cid_callee, cat_id, _ = _wire_flow_test(agent_factory)
    foreign_outgoing = components.upsert_edge_outbound("sme-c", {
        "from_component_id": cid_caller, "to_component_id": cid_callee,
        "edge_type": "calls", "identifier": "GET /direct",
    })
    with pytest.raises(ValueError, match="does not originate from component"):
        components.upsert_flow(
            "sme-a", cid_a, cat_id, str(foreign_outgoing["id"]),
        )


def test_upsert_flow_idempotent_metadata_accumulation(agent_factory):
    _, cid_a, _, cat_id, outgoing_id = _wire_flow_test(agent_factory)
    a = components.upsert_flow(
        "sme-a", cid_a, cat_id, outgoing_id,
        metadata={"src": "code"}, confidence=0.6,
    )
    b = components.upsert_flow(
        "sme-a", cid_a, cat_id, outgoing_id,
        metadata={"src2": "telemetry"}, confidence=0.9,
    )
    assert a["id"] == b["id"]
    assert b["metadata"] == {"src": "code", "src2": "telemetry"}
    assert b["confidence"] == 0.9


def test_get_flow_returns_outgoing_edges(agent_factory):
    _, cid_a, _, cat_id, outgoing_id = _wire_flow_test(agent_factory)
    components.upsert_flow("sme-a", cid_a, cat_id, outgoing_id)
    rows = components.get_flow("sme-a", cid_a, cat_id)
    assert len(rows) == 1
    assert str(rows[0]["id"]) == outgoing_id


def test_get_flow_inverse_returns_incoming_catalogs(agent_factory):
    """get_flow_inverse: given an outgoing edge, return the catalog rows
    that fan into it (was: edge rows pre-7.4.2; now catalog rows because
    incoming is canonically a catalog)."""
    _, cid_a, _, cat_id, outgoing_id = _wire_flow_test(agent_factory)
    components.upsert_flow("sme-a", cid_a, cat_id, outgoing_id)
    rows = components.get_flow_inverse("sme-a", cid_a, outgoing_id)
    assert len(rows) == 1
    assert str(rows[0]["id"]) == cat_id
    # Catalog rows have a `kind` column — confirms we got catalog, not edge.
    assert rows[0]["kind"] == "endpoint"


def test_flow_cascades_on_catalog_delete(agent_factory):
    """Phase 7.4.2: deleting a catalog must cascade-delete flows that
    reference it (incoming_catalog_id ON DELETE CASCADE)."""
    _, cid_a, _, cat_id, outgoing_id = _wire_flow_test(agent_factory)
    components.upsert_flow("sme-a", cid_a, cat_id, outgoing_id)
    execute_mutate("DELETE FROM catalogs WHERE id = %s", (cat_id,))
    n = execute_one(
        "SELECT count(*)::int as n FROM flows WHERE component_id = %s",
        (cid_a,),
    )
    assert n["n"] == 0


def test_flow_cascades_on_outgoing_edge_delete(agent_factory):
    """Outgoing edge deletion should also cascade (FK ON DELETE CASCADE
    on outgoing_edge_id was unchanged in 7.4.2)."""
    _, cid_a, _, cat_id, outgoing_id = _wire_flow_test(agent_factory)
    components.upsert_flow("sme-a", cid_a, cat_id, outgoing_id)
    execute_mutate("DELETE FROM edges WHERE id = %s", (outgoing_id,))
    n = execute_one(
        "SELECT count(*)::int as n FROM flows WHERE component_id = %s",
        (cid_a,),
    )
    assert n["n"] == 0


def test_flow_fan_out_one_incoming_many_outgoing(agent_factory):
    """Set-based: one catalog → multiple outgoings = multiple flow rows."""
    _, cid_a, cid_callee, cat_id, outgoing_1 = _wire_flow_test(agent_factory)
    cid_other = _sme_with_component(agent_factory, "i", "sme-e", "o/e", "other")
    outgoing_2 = components.upsert_edge_outbound("sme-a", {
        "from_component_id": cid_a, "to_component_id": cid_other,
        "edge_type": "publishes_to", "identifier": "events.process",
    })
    components.upsert_flow("sme-a", cid_a, cat_id, outgoing_1)
    components.upsert_flow("sme-a", cid_a, cat_id, str(outgoing_2["id"]))
    rows = components.get_flow("sme-a", cid_a, cat_id)
    assert len(rows) == 2
    assert {str(r["id"]) for r in rows} == {outgoing_1, str(outgoing_2["id"])}


def test_upsert_flow_rejects_unknown_catalog(agent_factory):
    """Catalog id that doesn't exist → clear error."""
    _, cid_a, _, _, outgoing_id = _wire_flow_test(agent_factory)
    bogus = "00000000-0000-0000-0000-000000000000"
    with pytest.raises(ValueError, match="not found"):
        components.upsert_flow("sme-a", cid_a, bogus, outgoing_id)

"""Tests for component-graph tools (Phase 2.2 — SME materialisation writes)."""

import pytest

from shared.db import execute_mutate, execute_one, execute
from cartograph_mcp.tools import components, resources


def _iterator(agent_factory, agent_id: str, plane: str):
    execute_mutate(
        """INSERT INTO agent_runs (agent_id, agent_type, plane, status)
           VALUES (%s, 'iterator', %s, 'idle')""",
        (agent_id, plane),
    )
    return agent_id


def _sme_with_resource(agent_factory, sme_id: str, resource_id: str) -> None:
    """Create an SME + RCA reservation row (component_id=NULL)."""
    agent_factory(sme_id, "sme")
    execute_mutate(
        """INSERT INTO resource_component_agents (resource_id, component_id, agent_id)
           VALUES (%s, NULL, %s)""",
        (resource_id, sme_id),
    )


# ============ upsert_component ============


def test_sme_creates_component_and_fills_rca(agent_factory):
    _iterator(agent_factory, "iter-gh", "github")
    r = resources.upsert_resource("iter-gh", "github", "repo", "dream11/feeds")
    _sme_with_resource(agent_factory, "sme-feeds", r["id"])

    comp = components.upsert_component(
        "sme-feeds",
        {
            "canonical_name": "dream11/feeds",
            "display_name": "Feeds API",
            "component_type": "application",
            "confidence": 0.9,
            "metadata": {"runtime": "java"},
        },
    )
    assert comp["canonical_name"] == "dream11/feeds"
    assert comp["component_type"] == "application"
    # RCA row now has component_id set
    rca = execute_one(
        "SELECT component_id FROM resource_component_agents WHERE agent_id='sme-feeds'"
    )
    assert str(rca["component_id"]) == str(comp["id"])


def test_sme_updates_component_on_second_call(agent_factory):
    _iterator(agent_factory, "iter-gh", "github")
    r = resources.upsert_resource("iter-gh", "github", "repo", "dream11/x")
    _sme_with_resource(agent_factory, "sme-x", r["id"])
    comp = components.upsert_component(
        "sme-x",
        {"canonical_name": "dream11/x", "display_name": "X", "component_type": "application"},
    )
    updated = components.upsert_component(
        "sme-x",
        {"canonical_name": "dream11/x", "display_name": "X Service", "component_type": "application", "confidence": 0.75},
    )
    assert updated["id"] == comp["id"]
    assert updated["display_name"] == "X Service"
    assert updated["confidence"] == 0.75


def test_upsert_component_always_updates_single_component(agent_factory):
    """1-SME=1-component invariant: any upsert after the first just updates
    the existing component, even with a different canonical_name (rename).
    A second component for the same SME is structurally impossible."""
    _iterator(agent_factory, "iter-gh", "github")
    r1 = resources.upsert_resource("iter-gh", "github", "repo", "dream11/a")
    _sme_with_resource(agent_factory, "sme-ab", r1["id"])
    c1 = components.upsert_component(
        "sme-ab",
        {"canonical_name": "dream11/a", "display_name": "A", "component_type": "application"},
    )
    c2 = components.upsert_component(
        "sme-ab",
        {"canonical_name": "dream11/a-renamed", "display_name": "A'",
         "component_type": "application"},
    )
    # Same component row, just renamed.
    assert c1["id"] == c2["id"]
    assert c2["canonical_name"] == "dream11/a-renamed"
    # Still exactly one component owned by this SME.
    n = execute_one(
        "SELECT COUNT(*) AS n FROM components c "
        "JOIN resource_component_agents rca ON rca.component_id = c.id "
        "WHERE rca.agent_id = 'sme-ab'"
    )
    assert n["n"] == 1


def test_non_sme_cannot_upsert_component(agent_factory):
    agent_factory("orch-1", "orchestrator")
    with pytest.raises(ValueError, match="Only SMEs"):
        components.upsert_component(
            "orch-1",
            {"canonical_name": "x", "display_name": "x", "component_type": "application"},
        )


def test_upsert_component_validates_required_fields(agent_factory):
    _iterator(agent_factory, "iter-gh", "github")
    r = resources.upsert_resource("iter-gh", "github", "repo", "x")
    _sme_with_resource(agent_factory, "sme-1", r["id"])
    with pytest.raises(ValueError, match="canonical_name"):
        components.upsert_component(
            "sme-1", {"display_name": "d", "component_type": "application"}
        )
    with pytest.raises(ValueError, match="display_name"):
        components.upsert_component(
            "sme-1", {"canonical_name": "c", "component_type": "application"}
        )
    with pytest.raises(ValueError, match="component_type"):
        components.upsert_component(
            "sme-1", {"canonical_name": "c", "display_name": "d", "component_type": "foo"}
        )


def test_upsert_component_confidence_bounded(agent_factory):
    _iterator(agent_factory, "iter-gh", "github")
    r = resources.upsert_resource("iter-gh", "github", "repo", "x")
    _sme_with_resource(agent_factory, "sme-1", r["id"])
    with pytest.raises(ValueError, match="confidence"):
        components.upsert_component(
            "sme-1",
            {"canonical_name": "c", "display_name": "d", "component_type": "application", "confidence": 1.5},
        )


def test_upsert_component_canonical_name_conflict_different_owner(agent_factory):
    _iterator(agent_factory, "iter-gh", "github")
    r1 = resources.upsert_resource("iter-gh", "github", "repo", "x")
    r2 = resources.upsert_resource("iter-gh", "github", "repo", "y")
    _sme_with_resource(agent_factory, "sme-1", r1["id"])
    _sme_with_resource(agent_factory, "sme-2", r2["id"])
    components.upsert_component(
        "sme-1", {"canonical_name": "shared", "display_name": "A", "component_type": "application"},
    )
    with pytest.raises(ValueError, match="already belongs"):
        components.upsert_component(
            "sme-2", {"canonical_name": "shared", "display_name": "B", "component_type": "application"},
        )


def test_upsert_component_canonical_name_active_vs_decom_allowed(agent_factory):
    """Phase 10.8.1: a decommissioned component holding canonical_name 'X'
    must not block a fresh SME from materialising a new component with
    the same canonical_name. Validates the partial UNIQUE INDEX
    (`components_canonical_name_active_unique`) + the `status='active'`
    filter on the application-layer pre-check.
    """
    from shared.db import execute_mutate
    _iterator(agent_factory, "iter-gh", "github")
    r1 = resources.upsert_resource("iter-gh", "github", "repo", "old")
    r2 = resources.upsert_resource("iter-gh", "github", "repo", "new")
    _sme_with_resource(agent_factory, "sme-old", r1["id"])
    _sme_with_resource(agent_factory, "sme-new", r2["id"])
    # sme-old materialises the original 'feeds-api' v1
    components.upsert_component(
        "sme-old",
        {"canonical_name": "feeds-api", "display_name": "feeds-api v1",
         "component_type": "application"},
    )
    # Simulate post-merge decommission of v1 (skip the actual mutation
    # path to keep the test scoped to canonical_name semantics).
    execute_mutate(
        "UPDATE components SET status='decommissioned' WHERE canonical_name='feeds-api'"
    )
    # sme-new can now claim the same canonical_name for v2.
    new_comp = components.upsert_component(
        "sme-new",
        {"canonical_name": "feeds-api", "display_name": "feeds-api v2",
         "component_type": "application"},
    )
    assert new_comp["canonical_name"] == "feeds-api"
    assert new_comp["status"] == "active"


def test_upsert_component_no_rca_row(agent_factory):
    agent_factory("sme-orphan", "sme")  # no RCA row
    with pytest.raises(ValueError, match="no assigned resource"):
        components.upsert_component(
            "sme-orphan",
            {"canonical_name": "x", "display_name": "x", "component_type": "application"},
        )


# ============ upsert_attribution ============


def _sme_with_component(agent_factory, sme_id: str, canonical_name: str) -> str:
    _iterator(agent_factory, f"iter-for-{sme_id}", "github")
    r = resources.upsert_resource(
        f"iter-for-{sme_id}", "github", "repo", f"repo-{sme_id}"
    )
    _sme_with_resource(agent_factory, sme_id, r["id"])
    comp = components.upsert_component(
        sme_id,
        {
            "canonical_name": canonical_name,
            "display_name": canonical_name,
            "component_type": "application",
        },
    )
    return str(comp["id"])


def test_sme_upserts_attribution(agent_factory):
    cid = _sme_with_component(agent_factory, "sme-a", "dream11/a")
    attr = components.upsert_attribution(
        "sme-a",
        cid,
        {"plane": "github", "resource_type": "repo", "identifier": "dream11/a",
         "evidence": "cloned", "confidence": 1.0},
    )
    assert attr["resource_type"] == "repo"
    assert attr["component_id"] == cid or str(attr["component_id"]) == cid


def test_sme_cannot_attribute_to_other_component(agent_factory):
    cid_a = _sme_with_component(agent_factory, "sme-a", "dream11/a")
    cid_b = _sme_with_component(agent_factory, "sme-b", "dream11/b")
    with pytest.raises(ValueError, match="does not own"):
        components.upsert_attribution(
            "sme-a", cid_b,
            {"plane": "github", "resource_type": "repo", "identifier": "x"},
        )


def test_attribution_shared_across_components_allowed(agent_factory):
    """Phase 10.13.6: cross-component (plane, resource_type, identifier)
    overlap is NO LONGER rejected. Identity drift handled socially via
    clarifications between peer SMEs, not at write time. Multiple
    components legitimately share categorical tags (runtime=jvm,
    env=prod, shared kafka topic, etc).
    """
    cid_a = _sme_with_component(agent_factory, "sme-a", "dream11/a")
    cid_b = _sme_with_component(agent_factory, "sme-b", "dream11/b")
    a1 = components.upsert_attribution(
        "sme-a", cid_a,
        {"plane": "cloud", "resource_type": "runtime", "identifier": "jvm"},
    )
    a2 = components.upsert_attribution(
        "sme-b", cid_b,
        {"plane": "cloud", "resource_type": "runtime", "identifier": "jvm"},
    )
    # Both rows exist, on different components.
    assert str(a1["component_id"]) == cid_a
    assert str(a2["component_id"]) == cid_b
    assert a1["id"] != a2["id"]


def test_attribution_same_component_still_idempotent(agent_factory):
    """Same component re-upserting same triple → updates in place,
    does NOT create duplicate row (component-scoped UNIQUE)."""
    cid = _sme_with_component(agent_factory, "sme-a", "dream11/a")
    a1 = components.upsert_attribution(
        "sme-a", cid,
        {"plane": "cloud", "resource_type": "runtime", "identifier": "jvm",
         "evidence": "first"},
    )
    a2 = components.upsert_attribution(
        "sme-a", cid,
        {"plane": "cloud", "resource_type": "runtime", "identifier": "jvm",
         "evidence": "updated"},
    )
    assert a1["id"] == a2["id"]
    assert a2["evidence"] == "updated"


def test_attribution_idempotent_on_same_component(agent_factory):
    cid = _sme_with_component(agent_factory, "sme-a", "dream11/a")
    a1 = components.upsert_attribution(
        "sme-a", cid,
        {"plane": "github", "resource_type": "endpoint", "identifier": "GET /ping",
         "confidence": 0.5},
    )
    a2 = components.upsert_attribution(
        "sme-a", cid,
        {"plane": "github", "resource_type": "endpoint", "identifier": "GET /ping",
         "confidence": 0.9, "evidence": "stronger"},
    )
    assert a1["id"] == a2["id"]
    assert a2["confidence"] == 0.9


# ============ create_edge ============


def test_create_edge(agent_factory):
    cid_a = _sme_with_component(agent_factory, "sme-a", "dream11/a")
    cid_b = _sme_with_component(agent_factory, "sme-b", "dream11/b")
    edge = components.create_edge(
        "sme-a",
        {"source_id": cid_a, "target_id": cid_b, "edge_type": "calls",
         "identifier": "GET /stats"},
    )
    assert edge["edge_type"] == "calls"
    assert str(edge["from_component_id"]) == cid_a
    assert str(edge["to_component_id"]) == cid_b


def test_create_edge_self_loop_accepted(agent_factory):
    """Phase 7.3 + 7.4.4: self-loops are PERMITTED (cron self-trigger,
    recursive component-level calls, service publish+consume on the
    same topic). Pre-7.4.4 the Python guard rejected them despite
    Phase 7.3 dropping the DB CHECK constraints."""
    cid = _sme_with_component(agent_factory, "sme-a", "dream11/a")
    out = components.create_edge(
        "sme-a",
        {"source_id": cid, "target_id": cid, "edge_type": "calls", "identifier": "x"},
    )
    assert str(out["from_component_id"]) == str(cid)
    assert str(out["to_component_id"]) == str(cid)


def test_create_edge_not_owner_of_source(agent_factory):
    cid_a = _sme_with_component(agent_factory, "sme-a", "dream11/a")
    cid_b = _sme_with_component(agent_factory, "sme-b", "dream11/b")
    with pytest.raises(ValueError, match="does not own source"):
        components.create_edge(
            "sme-b",
            {"source_id": cid_a, "target_id": cid_b, "edge_type": "calls", "identifier": "x"},
        )


def test_create_edge_invalid_type(agent_factory):
    cid_a = _sme_with_component(agent_factory, "sme-a", "dream11/a")
    cid_b = _sme_with_component(agent_factory, "sme-b", "dream11/b")
    with pytest.raises(ValueError, match="Invalid edge_type"):
        components.create_edge(
            "sme-a",
            {"source_id": cid_a, "target_id": cid_b, "edge_type": "pings", "identifier": "x"},
        )


def test_create_edge_idempotent(agent_factory):
    cid_a = _sme_with_component(agent_factory, "sme-a", "dream11/a")
    cid_b = _sme_with_component(agent_factory, "sme-b", "dream11/b")
    e1 = components.create_edge(
        "sme-a",
        {"source_id": cid_a, "target_id": cid_b, "edge_type": "calls",
         "identifier": "GET /x", "confidence": 0.5},
    )
    e2 = components.create_edge(
        "sme-a",
        {"source_id": cid_a, "target_id": cid_b, "edge_type": "calls",
         "identifier": "GET /x", "confidence": 0.9},
    )
    assert e1["id"] == e2["id"]
    assert e2["confidence"] == 0.9


# ============ insert_unresolved + resolve_reference ============


def test_insert_unresolved(agent_factory):
    cid = _sme_with_component(agent_factory, "sme-a", "dream11/a")
    u = components.insert_unresolved(
        "sme-a",
        {"found_in_component_id": cid, "reference_type": "config_key",
         "reference_value": "downstream.api.host", "context": {"file": "app.yml"}},
    )
    assert u["resolved"] is False
    assert str(u["found_in_component_id"]) == cid


def test_insert_unresolved_not_owner(agent_factory):
    cid_a = _sme_with_component(agent_factory, "sme-a", "dream11/a")
    _sme_with_component(agent_factory, "sme-b", "dream11/b")
    with pytest.raises(ValueError, match="does not own"):
        components.insert_unresolved(
            "sme-b",
            {"found_in_component_id": cid_a, "reference_type": "ref", "reference_value": "x"},
        )


def test_resolve_reference(agent_factory):
    cid_a = _sme_with_component(agent_factory, "sme-a", "dream11/a")
    cid_b = _sme_with_component(agent_factory, "sme-b", "dream11/b")
    u = components.insert_unresolved(
        "sme-a",
        {"found_in_component_id": cid_a, "reference_type": "config_key",
         "reference_value": "svc.b.host"},
    )
    resolved = components.resolve_reference("sme-a", str(u["id"]), cid_b)
    assert resolved["resolved"] is True
    assert str(resolved["resolved_to_component_id"]) == cid_b


def test_resolve_reference_to_decommissioned_refused(agent_factory):
    cid_a = _sme_with_component(agent_factory, "sme-a", "dream11/a")
    cid_b = _sme_with_component(agent_factory, "sme-b", "dream11/b")
    execute_mutate(
        "UPDATE components SET status='decommissioned' WHERE id=%s", (cid_b,),
    )
    u = components.insert_unresolved(
        "sme-a",
        {"found_in_component_id": cid_a, "reference_type": "x", "reference_value": "y"},
    )
    with pytest.raises(ValueError, match="does not exist or is decommissioned"):
        components.resolve_reference("sme-a", str(u["id"]), cid_b)


# ============ Reads ============


def test_get_component_attributions_edges_unresolved(agent_factory):
    cid_a = _sme_with_component(agent_factory, "sme-a", "dream11/a")
    cid_b = _sme_with_component(agent_factory, "sme-b", "dream11/b")
    components.upsert_attribution(
        "sme-a", cid_a,
        {"plane": "github", "resource_type": "repo", "identifier": "dream11/a"},
    )
    components.create_edge(
        "sme-a",
        {"source_id": cid_a, "target_id": cid_b, "edge_type": "calls", "identifier": "GET /x"},
    )
    components.insert_unresolved(
        "sme-a",
        {"found_in_component_id": cid_a, "reference_type": "r", "reference_value": "v"},
    )

    agent_factory("orch-1", "orchestrator")
    comp = components.get_component("orch-1", cid_a)
    assert str(comp["id"]) == cid_a

    attrs = components.get_attributions("orch-1", cid_a)
    assert len(attrs) == 1

    edges = components.get_edges("orch-1", cid_a)
    assert len(edges["outbound"]) == 1
    assert len(edges["inbound"]) == 0

    # inbound from b's perspective
    in_b = components.get_edges("orch-1", cid_b)
    assert len(in_b["outbound"]) == 0
    assert len(in_b["inbound"]) == 1

    unres = components.get_unresolved("orch-1", cid_a)
    assert len(unres) == 1


# ─── Phase 10.13.3: get_component_owner ───────────────────────────


def test_get_component_owner_active(agent_factory):
    """Active component → returns the SME owning it via RCA."""
    _iterator(agent_factory, "iter-gh", "github")
    r = resources.upsert_resource("iter-gh", "github", "repo", "dream11/x")
    _sme_with_resource(agent_factory, "sme-x", r["id"])
    components.upsert_component(
        "sme-x",
        {"canonical_name": "comp-x", "display_name": "Comp X",
         "component_type": "application"},
    )
    cid = execute_one(
        "SELECT id::text AS id FROM components WHERE canonical_name='comp-x'"
    )["id"]

    agent_factory("orch-1", "orchestrator")
    res = components.get_component_owner("orch-1", cid)
    assert res["component_id"] == cid
    assert res["canonical_name"] == "comp-x"
    assert res["component_status"] == "active"
    assert res["owner_agent_id"] == "sme-x"
    assert res["owner_status"] == "idle"
    assert res["merged_into_agent_id"] is None


def test_get_component_owner_decommissioned_with_merger(agent_factory):
    """Decommissioned component with merged_into_agent_id → carry survivor."""
    _iterator(agent_factory, "iter-gh", "github")
    r = resources.upsert_resource("iter-gh", "github", "repo", "dream11/y")
    _sme_with_resource(agent_factory, "sme-y", r["id"])
    components.upsert_component(
        "sme-y",
        {"canonical_name": "comp-y", "display_name": "Comp Y",
         "component_type": "application"},
    )
    cid = execute_one(
        "SELECT id::text AS id FROM components WHERE canonical_name='comp-y'"
    )["id"]
    # Mark sme-y decommissioned + chain to a survivor
    agent_factory("sme-survivor", "sme")
    execute_mutate(
        "UPDATE agent_runs SET status='decommissioned', merged_into_agent_id=%s "
        "WHERE agent_id='sme-y'",
        ("sme-survivor",),
    )

    agent_factory("orch-1", "orchestrator")
    res = components.get_component_owner("orch-1", cid)
    assert res["owner_status"] == "decommissioned"
    assert res["merged_into_agent_id"] == "sme-survivor"


def test_get_component_owner_not_found(agent_factory):
    """Bogus component_id → ValueError."""
    agent_factory("orch-1", "orchestrator")
    with pytest.raises(ValueError, match="not found"):
        components.get_component_owner(
            "orch-1", "00000000-0000-0000-0000-000000000000",
        )


# ─── Phase 10.13.7: resolve_references_bulk + bind_edges_bulk ──────


def test_resolve_references_bulk_happy_path(agent_factory):
    """All rows valid → atomic apply, all rows resolved=True."""
    cid_a = _sme_with_component(agent_factory, "sme-a", "dream11/a")
    cid_b = _sme_with_component(agent_factory, "sme-b", "dream11/b")
    # Insert 3 unresolved rows on cid_a
    u_ids = []
    for i in range(3):
        u = components.insert_unresolved(
            "sme-a",
            {"found_in_component_id": cid_a, "reference_type": "host",
             "reference_value": f"u{i}.dream11.local"},
        )
        u_ids.append(str(u["id"]))

    agent_factory("orch-1", "orchestrator")
    items = [{"unresolved_id": uid, "resolved_to_component_id": cid_b}
             for uid in u_ids]
    r = components.resolve_references_bulk("orch-1", items)
    assert r["committed"] is True
    assert r["applied"] == 3
    assert all(row["resolved"] for row in r["rows"])


def test_resolve_references_bulk_pre_validation_fails(agent_factory):
    """One bad row → reject whole batch, write nothing."""
    cid_a = _sme_with_component(agent_factory, "sme-a", "dream11/a")
    u = components.insert_unresolved(
        "sme-a",
        {"found_in_component_id": cid_a, "reference_type": "host",
         "reference_value": "x.dream11.local"},
    )
    agent_factory("orch-1", "orchestrator")
    items = [
        {"unresolved_id": str(u["id"]), "resolved_to_component_id": cid_a},
        {"unresolved_id": "00000000-0000-0000-0000-000000000000",
         "resolved_to_component_id": cid_a},  # bogus
    ]
    r = components.resolve_references_bulk("orch-1", items)
    assert r["committed"] is False
    assert r["applied"] == 0
    assert 1 in r["errors"]
    # Verify the GOOD row was NOT applied (atomic)
    refreshed = execute_one(
        "SELECT resolved FROM unresolved WHERE id = %s", (str(u["id"]),)
    )
    assert refreshed["resolved"] is False


def test_bind_edges_bulk_happy_path(agent_factory):
    """All bindings valid → atomic apply."""
    cid_a = _sme_with_component(agent_factory, "sme-a", "dream11/a")
    cid_b = _sme_with_component(agent_factory, "sme-b", "dream11/b")
    cid_c = _sme_with_component(agent_factory, "sme-c", "dream11/c")
    # 2 dangling outbounds from cid_a
    components.upsert_edge_outbound(
        "sme-a", {"from_component_id": cid_a, "edge_type": "calls",
                  "identifier": "/x"},
    )
    components.upsert_edge_outbound(
        "sme-a", {"from_component_id": cid_a, "edge_type": "calls",
                  "identifier": "/y"},
    )
    danglings = execute(
        "SELECT id::text AS id, identifier FROM edges "
        "WHERE from_component_id = %s AND to_component_id IS NULL",
        (cid_a,),
    )
    assert len(danglings) == 2
    bindings = [
        {"edge_id": danglings[0]["id"], "to_component_id": cid_b},
        {"edge_id": danglings[1]["id"], "to_component_id": cid_c},
    ]
    r = components.bind_edges_bulk("sme-a", bindings)
    assert r["committed"] is True
    assert r["applied"] == 2


def test_bind_edges_bulk_collision_rejects_batch(agent_factory):
    """Bind would collide with existing bound row → whole batch rejected."""
    cid_a = _sme_with_component(agent_factory, "sme-a", "dream11/a")
    cid_b = _sme_with_component(agent_factory, "sme-b", "dream11/b")
    # Pre-existing bound edge (a → b, calls /z)
    components.upsert_edge_outbound(
        "sme-a",
        {"from_component_id": cid_a, "to_component_id": cid_b,
         "edge_type": "calls", "identifier": "/z"},
    )
    # Dangling with same shape — bind would collide
    components.upsert_edge_outbound(
        "sme-a",
        {"from_component_id": cid_a, "edge_type": "calls", "identifier": "/z"},
    )
    dangling = execute_one(
        "SELECT id::text AS id FROM edges "
        "WHERE from_component_id = %s AND to_component_id IS NULL "
        "AND identifier = '/z'",
        (cid_a,),
    )
    r = components.bind_edges_bulk(
        "sme-a", [{"edge_id": dangling["id"], "to_component_id": cid_b}],
    )
    assert r["committed"] is False
    assert "collide" in r["errors"][0].lower()

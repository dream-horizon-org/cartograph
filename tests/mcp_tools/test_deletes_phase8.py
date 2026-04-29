"""Phase 8.2: corrective delete singletons (attribution, catalog, flow, unresolved).

Mirrors Phase 7.4.11 delete_edge contract:
- Owner-scoped via RCA
- Idempotent (missing id → reason='not_found')
- Optional reason param surfaces in mcp_audit (not stored on row)
"""

import pytest

from shared.db import execute, execute_one, execute_mutate, execute_returning
from cartograph_mcp.tools import (
    components, catalogs, resources as _res,
)


def _setup_sme_with_component(agent_factory, agent="sme-d", repo="o/p8d"):
    """Iterator + SME + one component owned by the SME."""
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
        "canonical_name": f"{agent}-comp",
        "display_name": f"{agent}-comp",
        "component_type": "application",
    })["id"]
    return agent, cid


# ============ delete_attribution ============


def test_delete_attribution_happy_path(agent_factory):
    sme, cid = _setup_sme_with_component(agent_factory)
    a = components.upsert_attribution(sme, cid, {
        "plane": "github", "resource_type": "endpoint",
        "identifier": "GET /test",
    })
    result = components.delete_attribution(sme, a["id"])
    assert result["deleted"] is True
    assert str(result["id"]) == str(a["id"])
    assert result["severed_edge_pointers"] == 0
    # Verify row gone
    assert execute_one(
        "SELECT 1 FROM attributions WHERE id = %s::uuid", (a["id"],),
    ) is None


def test_delete_attribution_idempotent_missing(agent_factory):
    sme, _ = _setup_sme_with_component(agent_factory)
    fake_id = "00000000-0000-0000-0000-000000000000"
    result = components.delete_attribution(sme, fake_id)
    assert result["deleted"] is False
    assert result["reason"] == "not_found"


def test_delete_attribution_owner_scoped(agent_factory):
    sme1, cid1 = _setup_sme_with_component(agent_factory, agent="sme-d1", repo="o/p8d1")
    sme2, _ = _setup_sme_with_component(agent_factory, agent="sme-d2", repo="o/p8d2")
    a = components.upsert_attribution(sme1, cid1, {
        "plane": "github", "resource_type": "endpoint",
        "identifier": "GET /owned-by-sme1",
    })
    with pytest.raises(ValueError, match="does not own"):
        components.delete_attribution(sme2, a["id"])


def test_delete_attribution_severs_edge_pointer(agent_factory):
    """Deleting an attribution that was cited as source_attr_id on an
    edge sets the FK to NULL (FK ON DELETE SET NULL). Edge survives."""
    sme, cid = _setup_sme_with_component(agent_factory)
    a = components.upsert_attribution(sme, cid, {
        "plane": "github", "resource_type": "endpoint",
        "identifier": "GET /myfunc",
    })
    # Build a self-loop bound edge that cites the attribution as source.
    edge = execute_returning(
        """INSERT INTO edges (from_component_id, to_component_id, edge_type,
                              identifier, source_attr_id, discovered_by)
           VALUES (%s, %s, 'calls', 'self-call', %s, %s)
           RETURNING id""",
        (cid, cid, a["id"], sme),
    )
    result = components.delete_attribution(sme, a["id"])
    assert result["deleted"] is True
    assert result["severed_edge_pointers"] == 1
    # Edge survives, source_attr_id is NULL
    edge_row = execute_one(
        "SELECT source_attr_id FROM edges WHERE id = %s::uuid",
        (edge["id"],),
    )
    assert edge_row["source_attr_id"] is None


# ============ delete_catalog ============


def test_delete_catalog_happy_path(agent_factory):
    sme, cid = _setup_sme_with_component(agent_factory)
    c = catalogs.upsert_catalog(sme, cid, "endpoint", "POST /verify")
    result = catalogs.delete_catalog(sme, c["id"])
    assert result["deleted"] is True
    assert result["cascaded_flows"] == 0


def test_delete_catalog_idempotent_missing(agent_factory):
    sme, _ = _setup_sme_with_component(agent_factory)
    fake_id = "00000000-0000-0000-0000-000000000000"
    result = catalogs.delete_catalog(sme, fake_id)
    assert result["deleted"] is False
    assert result["reason"] == "not_found"


def test_delete_catalog_owner_scoped(agent_factory):
    sme1, cid1 = _setup_sme_with_component(agent_factory, agent="sme-c1", repo="o/p8c1")
    sme2, _ = _setup_sme_with_component(agent_factory, agent="sme-c2", repo="o/p8c2")
    c = catalogs.upsert_catalog(sme1, cid1, "endpoint", "POST /owned-by-sme1")
    with pytest.raises(ValueError, match="does not own"):
        catalogs.delete_catalog(sme2, c["id"])


def test_delete_catalog_cascades_flows(agent_factory):
    """Deleting a catalog removes flows anchored on it via FK CASCADE."""
    sme, cid = _setup_sme_with_component(agent_factory)
    cat = catalogs.upsert_catalog(sme, cid, "endpoint", "POST /flow-anchor")
    # Need an outgoing edge to wire a flow to.
    edge = execute_returning(
        """INSERT INTO edges (from_component_id, to_component_id, edge_type,
                              identifier, discovered_by)
           VALUES (%s, %s, 'calls', 'outgoing-call', %s)
           RETURNING id""",
        (cid, cid, sme),
    )
    flow = components.upsert_flow(sme, cid, cat["id"], edge["id"])
    # Delete the catalog → flow cascades.
    result = catalogs.delete_catalog(sme, cat["id"])
    assert result["deleted"] is True
    assert result["cascaded_flows"] == 1
    assert execute_one(
        "SELECT 1 FROM flows WHERE id = %s::uuid", (flow["id"],),
    ) is None


# ============ delete_flow ============


def test_delete_flow_happy_path(agent_factory):
    sme, cid = _setup_sme_with_component(agent_factory)
    cat = catalogs.upsert_catalog(sme, cid, "endpoint", "POST /flow-test")
    edge = execute_returning(
        """INSERT INTO edges (from_component_id, to_component_id, edge_type,
                              identifier, discovered_by)
           VALUES (%s, %s, 'calls', 'flow-edge', %s)
           RETURNING id""",
        (cid, cid, sme),
    )
    flow = components.upsert_flow(sme, cid, cat["id"], edge["id"])
    result = components.delete_flow(sme, flow["id"])
    assert result["deleted"] is True
    # Catalog + edge survive (only the join is removed)
    assert execute_one(
        "SELECT 1 FROM catalogs WHERE id = %s::uuid", (cat["id"],),
    ) is not None
    assert execute_one(
        "SELECT 1 FROM edges WHERE id = %s::uuid", (edge["id"],),
    ) is not None


def test_delete_flow_idempotent_missing(agent_factory):
    sme, _ = _setup_sme_with_component(agent_factory)
    fake_id = "00000000-0000-0000-0000-000000000000"
    result = components.delete_flow(sme, fake_id)
    assert result["deleted"] is False
    assert result["reason"] == "not_found"


def test_delete_flow_owner_scoped(agent_factory):
    sme1, cid1 = _setup_sme_with_component(agent_factory, agent="sme-f1", repo="o/p8f1")
    sme2, _ = _setup_sme_with_component(agent_factory, agent="sme-f2", repo="o/p8f2")
    cat = catalogs.upsert_catalog(sme1, cid1, "endpoint", "POST /sme1")
    edge = execute_returning(
        """INSERT INTO edges (from_component_id, to_component_id, edge_type,
                              identifier, discovered_by)
           VALUES (%s, %s, 'calls', 'sme1-edge', %s)
           RETURNING id""",
        (cid1, cid1, sme1),
    )
    flow = components.upsert_flow(sme1, cid1, cat["id"], edge["id"])
    with pytest.raises(ValueError, match="does not own"):
        components.delete_flow(sme2, flow["id"])


# ============ delete_unresolved ============


def test_delete_unresolved_happy_path(agent_factory):
    sme, cid = _setup_sme_with_component(agent_factory)
    u = components.insert_unresolved(sme, {
        "found_in_component_id": str(cid),
        "reference_type": "hostname",
        "reference_value": "feeds-db.dream11.local",
    })
    result = components.delete_unresolved(sme, u["id"])
    assert result["deleted"] is True
    assert execute_one(
        "SELECT 1 FROM unresolved WHERE id = %s::uuid", (u["id"],),
    ) is None


def test_delete_unresolved_idempotent_missing(agent_factory):
    sme, _ = _setup_sme_with_component(agent_factory)
    fake_id = "00000000-0000-0000-0000-000000000000"
    result = components.delete_unresolved(sme, fake_id)
    assert result["deleted"] is False
    assert result["reason"] == "not_found"


def test_delete_unresolved_owner_scoped(agent_factory):
    sme1, cid1 = _setup_sme_with_component(agent_factory, agent="sme-u1", repo="o/p8u1")
    sme2, _ = _setup_sme_with_component(agent_factory, agent="sme-u2", repo="o/p8u2")
    u = components.insert_unresolved(sme1, {
        "found_in_component_id": str(cid1),
        "reference_type": "hostname",
        "reference_value": "owned-by-sme1.example",
    })
    with pytest.raises(ValueError, match="does not own"):
        components.delete_unresolved(sme2, u["id"])


# ============ reason param surfaces (sanity check it doesn't break tools) ============


def test_delete_with_reason_param(agent_factory):
    """The optional `reason` param is captured in mcp_audit args_hash;
    tools must accept it and behave identically to no-reason calls."""
    sme, cid = _setup_sme_with_component(agent_factory)
    a = components.upsert_attribution(sme, cid, {
        "plane": "github", "resource_type": "endpoint",
        "identifier": "GET /reason-test",
    })
    result = components.delete_attribution(
        sme, a["id"], reason="wrong shape — converting to edge"
    )
    assert result["deleted"] is True

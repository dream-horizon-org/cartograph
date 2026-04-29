"""Phase 8.3: 5 bulk corrective deletes (atomic-with-pre-validation).

Same contract as Phase 7.4.12 bulk writes:
- Pre-validate every row; if any row owned by another agent → reject
  whole batch with per-row errors
- Missing ids are NOT errors — silently skipped (idempotent)
- All-or-nothing on commit
"""

import pytest

from shared.db import execute, execute_one, execute_mutate, execute_returning
from cartograph_mcp.tools import (
    components, catalogs, resources as _res,
)


def _setup_sme_with_component(agent_factory, agent="sme-bd", repo="o/p8bd"):
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


# ============ delete_attributions_bulk ============


def test_attributions_bulk_happy(agent_factory):
    sme, cid = _setup_sme_with_component(agent_factory)
    ids = []
    for i in range(3):
        a = components.upsert_attribution(sme, cid, {
            "plane": "github", "resource_type": "endpoint",
            "identifier": f"GET /a{i}",
        })
        ids.append(str(a["id"]))
    result = components.delete_attributions_bulk(sme, ids)
    assert result["committed"] is True
    assert result["applied"] == 3
    assert all(r["deleted"] for r in result["rows"])


def test_attributions_bulk_missing_ids_idempotent(agent_factory):
    """Mix of valid + missing ids: valid ones delete, missing skip silently."""
    sme, cid = _setup_sme_with_component(agent_factory)
    a = components.upsert_attribution(sme, cid, {
        "plane": "github", "resource_type": "endpoint", "identifier": "GET /m1",
    })
    fake_id = "00000000-0000-0000-0000-000000000001"
    result = components.delete_attributions_bulk(sme, [str(a["id"]), fake_id])
    assert result["committed"] is True
    assert result["applied"] == 1
    deleted_ids = {r["id"] for r in result["rows"] if r["deleted"]}
    not_found_ids = {r["id"] for r in result["rows"] if not r["deleted"]}
    assert str(a["id"]) in deleted_ids
    assert fake_id in not_found_ids


def test_attributions_bulk_cross_owner_rejects_batch(agent_factory):
    """Even ONE id owned by another agent → whole batch rejected."""
    sme1, cid1 = _setup_sme_with_component(agent_factory, agent="sme-x1", repo="o/x1")
    sme2, cid2 = _setup_sme_with_component(agent_factory, agent="sme-x2", repo="o/x2")
    a1 = components.upsert_attribution(sme1, cid1, {
        "plane": "github", "resource_type": "endpoint", "identifier": "GET /a1",
    })
    a2 = components.upsert_attribution(sme2, cid2, {
        "plane": "github", "resource_type": "endpoint", "identifier": "GET /a2",
    })
    # sme1 tries to delete one of their own + one of sme2's
    result = components.delete_attributions_bulk(sme1, [str(a1["id"]), str(a2["id"])])
    assert result["committed"] is False
    assert result["applied"] == 0
    assert 1 in result["errors"]
    # sme1's row still exists (atomic — nothing was deleted)
    assert execute_one(
        "SELECT 1 FROM attributions WHERE id = %s::uuid", (a1["id"],),
    ) is not None


def test_attributions_bulk_max_500(agent_factory):
    sme, _ = _setup_sme_with_component(agent_factory)
    fake_ids = ["00000000-0000-0000-0000-" + f"{i:012d}" for i in range(501)]
    with pytest.raises(ValueError, match="max 500"):
        components.delete_attributions_bulk(sme, fake_ids)


def test_attributions_bulk_empty_input(agent_factory):
    sme, _ = _setup_sme_with_component(agent_factory)
    with pytest.raises(ValueError, match="non-empty"):
        components.delete_attributions_bulk(sme, [])


# ============ delete_catalogs_bulk ============


def test_catalogs_bulk_happy_with_cascade(agent_factory):
    sme, cid = _setup_sme_with_component(agent_factory)
    cats = []
    for i in range(2):
        cats.append(catalogs.upsert_catalog(sme, cid, "endpoint", f"POST /b{i}"))
    # Wire one flow on the first catalog so cascade fires.
    edge = execute_returning(
        """INSERT INTO edges (from_component_id, to_component_id, edge_type,
                              identifier, discovered_by)
           VALUES (%s, %s, 'calls', 'bulk-edge', %s)
           RETURNING id""",
        (cid, cid, sme),
    )
    components.upsert_flow(sme, cid, cats[0]["id"], edge["id"])
    result = catalogs.delete_catalogs_bulk(sme, [str(c["id"]) for c in cats])
    assert result["committed"] is True
    assert result["applied"] == 2
    cascade_counts = sorted(r["cascaded_flows"] for r in result["rows"])
    assert cascade_counts == [0, 1]
    # All flows gone
    assert execute_one(
        "SELECT COUNT(*) AS n FROM flows WHERE component_id = %s::uuid",
        (cid,),
    )["n"] == 0


def test_catalogs_bulk_owner_check(agent_factory):
    sme1, cid1 = _setup_sme_with_component(agent_factory, agent="sme-cb1", repo="o/cb1")
    sme2, cid2 = _setup_sme_with_component(agent_factory, agent="sme-cb2", repo="o/cb2")
    c1 = catalogs.upsert_catalog(sme1, cid1, "endpoint", "POST /c1")
    c2 = catalogs.upsert_catalog(sme2, cid2, "endpoint", "POST /c2")
    result = catalogs.delete_catalogs_bulk(sme1, [str(c1["id"]), str(c2["id"])])
    assert result["committed"] is False
    assert 1 in result["errors"]


# ============ delete_flows_bulk ============


def test_flows_bulk_happy(agent_factory):
    sme, cid = _setup_sme_with_component(agent_factory)
    cat = catalogs.upsert_catalog(sme, cid, "endpoint", "POST /fb")
    edge = execute_returning(
        """INSERT INTO edges (from_component_id, to_component_id, edge_type,
                              identifier, discovered_by)
           VALUES (%s, %s, 'calls', 'fb-edge', %s)
           RETURNING id""",
        (cid, cid, sme),
    )
    f = components.upsert_flow(sme, cid, cat["id"], edge["id"])
    result = components.delete_flows_bulk(sme, [str(f["id"])])
    assert result["committed"] is True
    assert result["applied"] == 1


def test_flows_bulk_owner_check(agent_factory):
    sme1, cid1 = _setup_sme_with_component(agent_factory, agent="sme-fb1", repo="o/fb1")
    sme2, cid2 = _setup_sme_with_component(agent_factory, agent="sme-fb2", repo="o/fb2")
    cat1 = catalogs.upsert_catalog(sme1, cid1, "endpoint", "POST /f1")
    edge1 = execute_returning(
        """INSERT INTO edges (from_component_id, to_component_id, edge_type,
                              identifier, discovered_by)
           VALUES (%s, %s, 'calls', 'f1-edge', %s)
           RETURNING id""",
        (cid1, cid1, sme1),
    )
    f1 = components.upsert_flow(sme1, cid1, cat1["id"], edge1["id"])
    result = components.delete_flows_bulk(sme2, [str(f1["id"])])
    assert result["committed"] is False
    assert 0 in result["errors"]


# ============ delete_edges_bulk ============


def test_edges_bulk_happy_cascade(agent_factory):
    sme, cid = _setup_sme_with_component(agent_factory)
    edges = []
    for i in range(2):
        e = execute_returning(
            """INSERT INTO edges (from_component_id, to_component_id, edge_type,
                                  identifier, discovered_by)
               VALUES (%s, %s, 'calls', %s, %s)
               RETURNING id""",
            (cid, cid, f"eb-{i}", sme),
        )
        edges.append(e)
    cat = catalogs.upsert_catalog(sme, cid, "endpoint", "POST /eb")
    components.upsert_flow(sme, cid, cat["id"], edges[0]["id"])
    result = components.delete_edges_bulk(sme, [str(e["id"]) for e in edges])
    assert result["committed"] is True
    assert result["applied"] == 2
    cascade_counts = sorted(r["cascaded_flows"] for r in result["rows"])
    assert cascade_counts == [0, 1]


def test_edges_bulk_catalog_row_rejected(agent_factory):
    """A catalog-shape edge (from IS NULL) should fail the batch."""
    sme, cid = _setup_sme_with_component(agent_factory)
    # Create a legacy catalog-shape edge directly
    cat_edge = execute_returning(
        """INSERT INTO edges (from_component_id, to_component_id, edge_type,
                              identifier, discovered_by)
           VALUES (NULL, %s, 'calls', 'legacy-cat', %s)
           RETURNING id""",
        (cid, sme),
    )
    bound_edge = execute_returning(
        """INSERT INTO edges (from_component_id, to_component_id, edge_type,
                              identifier, discovered_by)
           VALUES (%s, %s, 'calls', 'bound-eb', %s)
           RETURNING id""",
        (cid, cid, sme),
    )
    result = components.delete_edges_bulk(
        sme, [str(cat_edge["id"]), str(bound_edge["id"])]
    )
    assert result["committed"] is False
    assert 0 in result["errors"]
    assert "catalog_not_supported" in result["errors"][0]


# ============ delete_unresolved_bulk ============


def test_unresolved_bulk_happy(agent_factory):
    sme, cid = _setup_sme_with_component(agent_factory)
    ids = []
    for i in range(3):
        u = components.insert_unresolved(sme, {
            "found_in_component_id": str(cid),
            "reference_type": "hostname",
            "reference_value": f"unresolved-bulk-{i}",
        })
        ids.append(str(u["id"]))
    result = components.delete_unresolved_bulk(sme, ids)
    assert result["committed"] is True
    assert result["applied"] == 3


def test_unresolved_bulk_owner_check(agent_factory):
    sme1, cid1 = _setup_sme_with_component(agent_factory, agent="sme-ub1", repo="o/ub1")
    sme2, _ = _setup_sme_with_component(agent_factory, agent="sme-ub2", repo="o/ub2")
    u = components.insert_unresolved(sme1, {
        "found_in_component_id": str(cid1),
        "reference_type": "hostname",
        "reference_value": "ub-cross.example",
    })
    result = components.delete_unresolved_bulk(sme2, [str(u["id"])])
    assert result["committed"] is False
    assert 0 in result["errors"]


# ============ duplicate-id within batch ============


def test_duplicate_id_within_batch_rejected(agent_factory):
    sme, cid = _setup_sme_with_component(agent_factory)
    a = components.upsert_attribution(sme, cid, {
        "plane": "github", "resource_type": "endpoint", "identifier": "GET /dup",
    })
    result = components.delete_attributions_bulk(sme, [str(a["id"]), str(a["id"])])
    assert result["committed"] is False
    assert 1 in result["errors"]
    assert "duplicate" in result["errors"][1]

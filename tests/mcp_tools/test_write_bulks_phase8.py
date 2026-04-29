"""Phase 8.4: write bulks + unresolved idempotency.

Tests:
- upsert_flows_bulk (atomic-with-pre-validation, idempotent on triple)
- insert_unresolved_bulk (atomic, idempotent on triple per Phase 8.4 UNIQUE)
- ack_broadcasts_bulk (idempotent, ON CONFLICT DO NOTHING)
- ack_terminals_bulk (atomic-with-pre-validation, participant + terminal check)
- insert_unresolved (single) idempotency: repeated insert bumps attempts
"""

import pytest

from shared.db import execute, execute_one, execute_mutate, execute_returning
from cartograph_mcp.tools import (
    components, catalogs, resources as _res, broadcast as broadcast_tool,
    terminal_acks, tasks as tasks_tool,
)


def _setup_sme_with_component(agent_factory, agent="sme-w", repo="o/p8w"):
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


# ============ insert_unresolved idempotency ============


def test_insert_unresolved_idempotent_bumps_attempts(agent_factory):
    """Repeat insert on same (component, type, value) updates in place,
    bumps attempts, doesn't duplicate."""
    sme, cid = _setup_sme_with_component(agent_factory, agent="sme-iu")
    payload = {
        "found_in_component_id": str(cid),
        "reference_type": "hostname",
        "reference_value": "first-then-repeat.example",
        "context": {"src": "first"},
    }
    u1 = components.insert_unresolved(sme, payload)
    assert u1["attempts"] == 0

    payload["context"] = {"src": "second"}
    u2 = components.insert_unresolved(sme, payload)
    assert str(u2["id"]) == str(u1["id"])
    assert u2["attempts"] == 1
    assert u2["context"] == {"src": "second"}

    # No duplicate row
    cnt = execute_one(
        """SELECT COUNT(*) AS n FROM unresolved
           WHERE found_in_component_id = %s::uuid
             AND reference_type = 'hostname'
             AND reference_value = 'first-then-repeat.example'""",
        (cid,),
    )["n"]
    assert cnt == 1


# ============ upsert_flows_bulk ============


def _add_flow_inputs(sme, cid, n=2):
    """Create n catalog + n outgoing edges; return paired list."""
    pairs = []
    for i in range(n):
        cat = catalogs.upsert_catalog(sme, cid, "endpoint", f"POST /flw{i}")
        edge = execute_returning(
            """INSERT INTO edges (from_component_id, to_component_id, edge_type,
                                  identifier, discovered_by)
               VALUES (%s, %s, 'calls', %s, %s)
               RETURNING id""",
            (cid, cid, f"flw-edge-{i}", sme),
        )
        pairs.append({"incoming_catalog_id": str(cat["id"]),
                      "outgoing_edge_id": str(edge["id"])})
    return pairs


def test_flows_bulk_happy(agent_factory):
    sme, cid = _setup_sme_with_component(agent_factory, agent="sme-fw")
    pairs = _add_flow_inputs(sme, cid, n=3)
    result = components.upsert_flows_bulk(sme, cid, pairs)
    assert result["committed"] is True
    assert result["applied"] == 3


def test_flows_bulk_idempotent_on_triple(agent_factory):
    """Re-upserting the same (component, catalog, edge) triple updates
    in place rather than failing or duplicating."""
    sme, cid = _setup_sme_with_component(agent_factory, agent="sme-fwi")
    pairs = _add_flow_inputs(sme, cid, n=1)
    pairs[0]["metadata"] = {"v": 1}
    r1 = components.upsert_flows_bulk(sme, cid, pairs)
    assert r1["applied"] == 1

    pairs[0]["metadata"] = {"v": 2}
    r2 = components.upsert_flows_bulk(sme, cid, pairs)
    assert r2["applied"] == 1
    # Single row in DB
    n = execute_one(
        "SELECT COUNT(*) AS n FROM flows WHERE component_id = %s::uuid",
        (cid,),
    )["n"]
    assert n == 1


def test_flows_bulk_catalog_not_owned_rejects(agent_factory):
    """Catalog belonging to another component → batch rejected."""
    sme, cid = _setup_sme_with_component(agent_factory, agent="sme-fw1", repo="o/fw1")
    sme2, cid2 = _setup_sme_with_component(agent_factory, agent="sme-fw2", repo="o/fw2")
    foreign_cat = catalogs.upsert_catalog(sme2, cid2, "endpoint", "POST /foreign")
    edge = execute_returning(
        """INSERT INTO edges (from_component_id, to_component_id, edge_type,
                              identifier, discovered_by)
           VALUES (%s, %s, 'calls', 'fw-edge', %s)
           RETURNING id""",
        (cid, cid, sme),
    )
    result = components.upsert_flows_bulk(sme, cid, [{
        "incoming_catalog_id": str(foreign_cat["id"]),
        "outgoing_edge_id": str(edge["id"]),
    }])
    assert result["committed"] is False
    assert 0 in result["errors"]


# ============ insert_unresolved_bulk ============


def test_unresolved_bulk_happy_with_idempotency(agent_factory):
    sme, cid = _setup_sme_with_component(agent_factory, agent="sme-uw")
    items = [
        {"found_in_component_id": str(cid), "reference_type": "hostname",
         "reference_value": f"unr-bulk-{i}"}
        for i in range(3)
    ]
    r1 = components.insert_unresolved_bulk(sme, items)
    assert r1["applied"] == 3
    # Re-run same batch — attempts bump, no dupes
    r2 = components.insert_unresolved_bulk(sme, items)
    assert r2["applied"] == 3
    n = execute_one(
        "SELECT COUNT(*) AS n FROM unresolved WHERE found_in_component_id = %s::uuid",
        (cid,),
    )["n"]
    assert n == 3
    attempts = execute_one(
        """SELECT MIN(attempts) AS a FROM unresolved
           WHERE found_in_component_id = %s::uuid""",
        (cid,),
    )["a"]
    assert attempts == 1


def test_unresolved_bulk_owner_check(agent_factory):
    sme1, cid1 = _setup_sme_with_component(agent_factory, agent="sme-uw1", repo="o/uw1")
    sme2, _ = _setup_sme_with_component(agent_factory, agent="sme-uw2", repo="o/uw2")
    # sme2 tries to file unresolved on sme1's component
    result = components.insert_unresolved_bulk(sme2, [{
        "found_in_component_id": str(cid1),
        "reference_type": "hostname",
        "reference_value": "uw-cross",
    }])
    assert result["committed"] is False
    assert 0 in result["errors"]


# ============ ack_broadcasts_bulk ============


def test_ack_broadcasts_bulk(agent_factory):
    agent_factory("sme-bb", "sme")
    # Create a few broadcast comm rows
    comm_ids = []
    for i in range(3):
        row = execute_returning(
            """INSERT INTO communications (from_agent, to_agent_type, type, text)
               VALUES ('admin', 'sme', 'broadcast', %s)
               RETURNING id""",
            (f"broadcast-{i}",),
        )
        comm_ids.append(str(row["id"]))
    result = broadcast_tool.ack_broadcasts_bulk("sme-bb", comm_ids)
    assert result["committed"] is True
    assert result["applied"] == 3

    # Re-call: all already acked
    result2 = broadcast_tool.ack_broadcasts_bulk("sme-bb", comm_ids)
    assert result2["applied"] == 0
    assert all(r["already_acked"] for r in result2["rows"])


# ============ ack_terminals_bulk ============


def test_ack_terminals_bulk_happy(agent_factory):
    agent_factory("orch-at", "orchestrator")
    agent_factory("sme-at", "sme")
    # Create + close a task
    task_row = tasks_tool.create_task("orch-at", "sme-at", "test task")
    # Worker completes (BW → WD), owner accepts (WD → TC)
    tasks_tool.respond_task("sme-at", task_row["id"], "done", "WD")
    tasks_tool.respond_task("orch-at", task_row["id"], "accepted", "TC")
    result = terminal_acks.ack_terminals_bulk("sme-at", [
        {"entity_type": "task", "entity_id": str(task_row["id"])},
    ])
    assert result["committed"] is True
    assert result["applied"] == 1


def test_ack_terminals_bulk_non_terminal_rejects(agent_factory):
    """Trying to ack an entity that isn't in terminal state fails."""
    agent_factory("orch-at2", "orchestrator")
    agent_factory("sme-at2", "sme")
    task_row = tasks_tool.create_task("orch-at2", "sme-at2", "still BW")
    result = terminal_acks.ack_terminals_bulk("sme-at2", [
        {"entity_type": "task", "entity_id": str(task_row["id"])},
    ])
    assert result["committed"] is False
    assert 0 in result["errors"]


def test_ack_terminals_bulk_invalid_entity_type(agent_factory):
    agent_factory("sme-at3", "sme")
    result = terminal_acks.ack_terminals_bulk("sme-at3", [
        {"entity_type": "bogus", "entity_id": "00000000-0000-0000-0000-000000000000"},
    ])
    assert result["committed"] is False
    assert 0 in result["errors"]


def test_max_500_limit_universal(agent_factory):
    sme, cid = _setup_sme_with_component(agent_factory, agent="sme-lim")
    fake_uuid = "00000000-0000-0000-0000-000000000000"
    huge = [{"incoming_catalog_id": fake_uuid, "outgoing_edge_id": fake_uuid}] * 501
    with pytest.raises(ValueError, match="max 500"):
        components.upsert_flows_bulk(sme, str(cid), huge)

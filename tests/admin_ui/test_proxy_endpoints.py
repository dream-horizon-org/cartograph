"""Phase 4: admin UI endpoints for proxy / merge-chain surface."""

import json

from shared.db import execute_mutate, execute_one, execute_returning


def _seed_deactivation_chain(factory):
    """sme-a absorbed sme-b with deactivation metadata."""
    factory("sme-a", "sme", "idle")
    factory("sme-b", "sme", "decommissioned")
    execute_mutate(
        """UPDATE agent_runs
           SET merged_into_agent_id = 'sme-a',
               deactivation_reason = 'merged',
               deactivation_notes = 'B folded into A for hostname overlap'
           WHERE agent_id = 'sme-b'"""
    )


# ---------- /api/agents include_decommissioned ----------


def test_list_agents_default_excludes_decommissioned(client, agent_factory):
    _seed_deactivation_chain(agent_factory)
    data = client.get("/api/agents").json()
    ids = [a["agent_id"] for a in data["agents"]]
    assert "sme-a" in ids
    assert "sme-b" not in ids


def test_list_agents_include_decommissioned_shows_deactivation_cols(
    client, agent_factory
):
    _seed_deactivation_chain(agent_factory)
    data = client.get("/api/agents?include_decommissioned=true").json()
    by_id = {a["agent_id"]: a for a in data["agents"]}
    assert "sme-b" in by_id
    dead = by_id["sme-b"]
    assert dead["deactivation_reason"] == "merged"
    assert dead["deactivation_notes"] == "B folded into A for hostname overlap"
    assert dead["merged_into_agent_id"] == "sme-a"


# ---------- /api/agent/:id/chain ----------


def test_agent_chain_single_hop(client, agent_factory):
    _seed_deactivation_chain(agent_factory)
    data = client.get("/api/agent/sme-b/chain").json()
    chain = data["chain"]
    assert [a["agent_id"] for a in chain] == ["sme-b", "sme-a"]
    assert chain[0]["deactivation_reason"] == "merged"
    # Active tail has no merged_into.
    assert chain[1]["merged_into_agent_id"] is None


def test_agent_chain_multi_hop(client, agent_factory):
    # sme-a absorbed sme-b; later sme-c absorbed sme-a.
    agent_factory("sme-c", "sme", "idle")
    agent_factory("sme-a", "sme", "decommissioned")
    agent_factory("sme-b", "sme", "decommissioned")
    execute_mutate(
        "UPDATE agent_runs SET merged_into_agent_id='sme-c' WHERE agent_id='sme-a'"
    )
    execute_mutate(
        "UPDATE agent_runs SET merged_into_agent_id='sme-a' WHERE agent_id='sme-b'"
    )
    data = client.get("/api/agent/sme-b/chain").json()
    ids = [a["agent_id"] for a in data["chain"]]
    assert ids == ["sme-b", "sme-a", "sme-c"]


def test_agent_chain_unknown_agent_returns_empty(client):
    data = client.get("/api/agent/ghost/chain").json()
    assert data == {"chain": []}


# ---------- /api/proxy_audit ----------


def test_proxy_audit_by_item(client, agent_factory):
    agent_factory("sme-a", "sme", "idle")
    agent_factory("sme-b", "sme", "decommissioned")
    execute_mutate(
        """INSERT INTO proxy_audit
           (survivor_id, proxy_agent_id, item_type, item_id, action, payload_summary)
           VALUES ('sme-a', 'sme-b', 'task', 'task-xyz', 'respond', '{"k":[]}'::jsonb)"""
    )
    data = client.get(
        "/api/proxy_audit?item_type=task&item_id=task-xyz"
    ).json()
    assert len(data["entries"]) == 1
    entry = data["entries"][0]
    assert entry["survivor_id"] == "sme-a"
    assert entry["proxy_agent_id"] == "sme-b"
    assert entry["action"] == "respond"


def test_proxy_audit_by_survivor(client, agent_factory):
    agent_factory("sme-a", "sme", "idle")
    agent_factory("sme-b", "sme", "decommissioned")
    for i in range(3):
        execute_mutate(
            """INSERT INTO proxy_audit
               (survivor_id, proxy_agent_id, item_type, item_id, action)
               VALUES ('sme-a', 'sme-b', 'task', %s, 'respond')""",
            (f"task-{i}",),
        )
    data = client.get("/api/proxy_audit?survivor_id=sme-a").json()
    assert len(data["entries"]) == 3
    assert all(e["survivor_id"] == "sme-a" for e in data["entries"])


def test_proxy_audit_empty(client):
    data = client.get("/api/proxy_audit?item_type=task&item_id=missing").json()
    assert data == {"entries": []}

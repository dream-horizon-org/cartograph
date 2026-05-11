"""Tests for GET /api/agents — list all active agents."""


def test_list_agents_empty(client):
    response = client.get("/api/agents")
    assert response.status_code == 200
    assert response.json() == {"agents": []}


def test_list_agents_returns_active(client, agent_factory):
    agent_factory("orch-1", "orchestrator", "idle")
    agent_factory("sme-1", "sme", "running")
    agent_factory("iter-gh", "iterator", "idle")

    response = client.get("/api/agents")
    assert response.status_code == 200
    data = response.json()
    agent_ids = [a["agent_id"] for a in data["agents"]]
    assert set(agent_ids) == {"orch-1", "sme-1", "iter-gh"}


def test_list_agents_excludes_decommissioned(client, agent_factory):
    agent_factory("orch-1", "orchestrator", "idle")
    agent_factory("sme-dead", "sme", "decommissioned")

    response = client.get("/api/agents")
    agent_ids = [a["agent_id"] for a in response.json()["agents"]]
    assert "orch-1" in agent_ids
    assert "sme-dead" not in agent_ids


def test_list_agents_returns_expected_fields(client, agent_factory):
    agent_factory("orch-1", "orchestrator", "idle")

    response = client.get("/api/agents")
    agent = response.json()["agents"][0]
    assert agent["agent_id"] == "orch-1"
    assert agent["agent_type"] == "orchestrator"
    assert agent["status"] == "idle"
    assert "created_at" in agent

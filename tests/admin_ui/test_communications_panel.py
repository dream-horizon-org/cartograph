"""Tests for communications panel endpoints + admin broadcast + task detail."""

from shared.db import execute_mutate, execute_one


def _insert_comm(from_agent, to_agent, type_, text, source_id=None):
    if source_id:
        row = execute_mutate(
            """INSERT INTO communications (from_agent, to_agent, type, text, source_id)
               VALUES (%s, %s, %s, %s, %s::uuid)""",
            (from_agent, to_agent, type_, text, source_id),
        )
    else:
        row = execute_mutate(
            """INSERT INTO communications (from_agent, to_agent, type, text)
               VALUES (%s, %s, %s, %s)""",
            (from_agent, to_agent, type_, text),
        )
    return row


# ============ /api/communications ============


def test_list_communications_no_filters(client, agent_factory):
    agent_factory("orch-1")
    agent_factory("sme-1", "sme")
    _insert_comm("admin", "orch-1", "chat", "hi orch")
    _insert_comm("sme-1", "admin", "chat", "reporting in")
    resp = client.get("/api/communications")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["messages"]) == 2
    assert data["has_more"] is False


def test_list_communications_filter_by_type(client, agent_factory):
    agent_factory("orch-1")
    _insert_comm("admin", "orch-1", "chat", "chat1")
    _insert_comm("admin", "sme", "broadcast", "bcast1")
    resp = client.get("/api/communications?type=chat")
    msgs = resp.json()["messages"]
    assert len(msgs) == 1
    assert msgs[0]["text"] == "chat1"


def test_list_communications_filter_by_from_agent(client, agent_factory):
    agent_factory("orch-1")
    agent_factory("sme-1", "sme")
    _insert_comm("admin", "orch-1", "chat", "A")
    _insert_comm("orch-1", "admin", "chat", "B")
    _insert_comm("sme-1", "admin", "chat", "C")
    resp = client.get("/api/communications?from_agent=orch-1")
    msgs = resp.json()["messages"]
    assert [m["text"] for m in msgs] == ["B"]


def test_list_communications_invalid_type(client):
    resp = client.get("/api/communications?type=foobar")
    assert resp.status_code == 400


def test_list_communications_has_more_flag(client, agent_factory):
    agent_factory("orch-1")
    for i in range(55):
        _insert_comm("admin", "orch-1", "chat", f"msg-{i}")
    resp = client.get("/api/communications?limit=50")
    data = resp.json()
    assert len(data["messages"]) == 50
    assert data["has_more"] is True


# ============ /api/task/{task_id} ============


def test_get_task_detail(client, agent_factory):
    agent_factory("orch-1")
    agent_factory("iter-1", "iterator")
    task = execute_one(
        """INSERT INTO tasks (owner_agent_id, worker_agent_id, description, status)
           VALUES ('orch-1', 'iter-1', 'enum github', 'BW') RETURNING *"""
    )
    _insert_comm("orch-1", "iter-1", "task", "kickoff", source_id=str(task["id"]))
    _insert_comm("iter-1", "orch-1", "task", "done", source_id=str(task["id"]))

    resp = client.get(f"/api/task/{task['id']}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["task"]["status"] == "BW"
    assert len(data["thread"]) == 2
    assert data["thread"][0]["text"] == "kickoff"  # ascending order
    assert data["thread"][1]["text"] == "done"


def test_get_task_detail_404(client):
    import uuid
    resp = client.get(f"/api/task/{uuid.uuid4()}")
    assert resp.status_code == 404


# ============ /api/broadcast (admin) ============


def test_admin_broadcast(client, agent_factory):
    agent_factory("sme-1", "sme")
    resp = client.post(
        "/api/broadcast",
        json={"to_agent_type": "sme", "message": "All SMEs: recalibrate"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["from_agent"] == "admin"
    assert data["to_agent"] == "sme"
    assert data["type"] == "broadcast"


def test_admin_broadcast_invalid_type(client):
    resp = client.post(
        "/api/broadcast",
        json={"to_agent_type": "everyone", "message": "x"},
    )
    assert resp.status_code == 422  # pydantic pattern fail


def test_admin_broadcast_empty_message(client):
    resp = client.post(
        "/api/broadcast",
        json={"to_agent_type": "sme", "message": ""},
    )
    assert resp.status_code == 422

"""Tests for communications panel endpoints + admin broadcast + task detail."""

from shared.db import execute_mutate, execute_one


def _insert_comm(from_agent, to_agent, type_, text, source_id=None):
    # For broadcasts the target is an agent_type, stored in to_agent_type.
    # For everything else, to_agent holds the target agent_id.
    if type_ == "broadcast":
        to_col, to_val = "to_agent_type", to_agent
    else:
        to_col, to_val = "to_agent", to_agent
    if source_id:
        return execute_mutate(
            f"""INSERT INTO communications (from_agent, {to_col}, type, text, source_id)
                VALUES (%s, %s, %s, %s, %s::uuid)""",
            (from_agent, to_val, type_, text, source_id),
        )
    return execute_mutate(
        f"""INSERT INTO communications (from_agent, {to_col}, type, text)
            VALUES (%s, %s, %s, %s)""",
        (from_agent, to_val, type_, text),
    )


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


def test_list_communications_filter_by_from_agent_type(client, agent_factory):
    agent_factory("orch-1", "orchestrator")
    agent_factory("sme-1", "sme")
    agent_factory("sme-2", "sme")
    _insert_comm("orch-1", "admin", "chat", "from orch")
    _insert_comm("sme-1", "admin", "chat", "from sme-1")
    _insert_comm("sme-2", "admin", "chat", "from sme-2")
    resp = client.get("/api/communications?from_agent_type=sme")
    texts = sorted(m["text"] for m in resp.json()["messages"])
    assert texts == ["from sme-1", "from sme-2"]


def test_list_communications_filter_by_from_agent_type_admin(client, agent_factory):
    agent_factory("orch-1", "orchestrator")
    _insert_comm("admin", "orch-1", "chat", "admin msg")
    _insert_comm("orch-1", "admin", "chat", "orch msg")
    resp = client.get("/api/communications?from_agent_type=admin")
    msgs = resp.json()["messages"]
    assert [m["text"] for m in msgs] == ["admin msg"]


def test_list_communications_filter_by_to_agent_type_matches_broadcast(client, agent_factory):
    agent_factory("orch-1")
    _insert_comm("admin", "sme", "broadcast", "to all smes")
    _insert_comm("admin", "iterator", "broadcast", "to all iters")
    resp = client.get("/api/communications?to_agent_type=sme")
    texts = [m["text"] for m in resp.json()["messages"]]
    assert texts == ["to all smes"]


def test_list_communications_filter_by_to_agent_type_matches_p2p(client, agent_factory):
    """to_agent_type should match point-to-point chats addressed to any
    agent of that type (via join)."""
    agent_factory("orch-1", "orchestrator")
    agent_factory("sme-1", "sme")
    _insert_comm("admin", "sme-1", "chat", "hi sme")
    _insert_comm("admin", "orch-1", "chat", "hi orch")
    resp = client.get("/api/communications?to_agent_type=sme")
    msgs = resp.json()["messages"]
    assert [m["text"] for m in msgs] == ["hi sme"]


def test_list_communications_filter_by_to_agent_type_combined(client, agent_factory):
    """to_agent_type=sme should match BOTH a p2p chat to sme-1 AND a broadcast
    to agent_type='sme'."""
    agent_factory("orch-1", "orchestrator")
    agent_factory("sme-1", "sme")
    _insert_comm("admin", "sme-1", "chat", "hi sme")
    _insert_comm("admin", "sme", "broadcast", "all smes")
    resp = client.get("/api/communications?to_agent_type=sme")
    texts = sorted(m["text"] for m in resp.json()["messages"])
    assert texts == ["all smes", "hi sme"]


def test_list_communications_invalid_from_agent_type(client):
    resp = client.get("/api/communications?from_agent_type=foo")
    assert resp.status_code == 400


def test_list_communications_invalid_to_agent_type(client):
    resp = client.get("/api/communications?to_agent_type=foo")
    assert resp.status_code == 400


# ------- participant filters (either-direction) -------


def test_agent_filter_matches_either_direction(client, agent_factory):
    agent_factory("orch-1", "orchestrator")
    agent_factory("sme-1", "sme")
    _insert_comm("admin", "sme-1", "chat", "to sme")
    _insert_comm("sme-1", "admin", "chat", "from sme")
    _insert_comm("orch-1", "admin", "chat", "from orch (unrelated)")
    resp = client.get("/api/communications?agent=sme-1")
    texts = sorted(m["text"] for m in resp.json()["messages"])
    assert texts == ["from sme", "to sme"]


def test_agent_type_filter_matches_either_direction_plus_broadcast(client, agent_factory):
    agent_factory("orch-1", "orchestrator")
    agent_factory("sme-1", "sme")
    agent_factory("sme-2", "sme")
    # sme-1 is sender
    _insert_comm("sme-1", "admin", "chat", "sme-1 sent")
    # sme-2 is recipient
    _insert_comm("admin", "sme-2", "chat", "sme-2 received")
    # broadcast to sme
    _insert_comm("orch-1", "sme", "broadcast", "orch broadcast to smes")
    # unrelated orch→admin
    _insert_comm("orch-1", "admin", "chat", "unrelated orch msg")
    resp = client.get("/api/communications?agent_type=sme")
    texts = sorted(m["text"] for m in resp.json()["messages"])
    assert texts == ["orch broadcast to smes", "sme-1 sent", "sme-2 received"]


def test_agent_type_filter_admin_literal(client, agent_factory):
    agent_factory("orch-1", "orchestrator")
    _insert_comm("admin", "orch-1", "chat", "from admin")
    _insert_comm("orch-1", "admin", "chat", "to admin")
    _insert_comm("orch-1", "orch-1", "chat", "self-chat noise")  # no admin involvement
    resp = client.get("/api/communications?agent_type=admin")
    texts = sorted(m["text"] for m in resp.json()["messages"])
    assert texts == ["from admin", "to admin"]


def test_agent_type_filter_invalid(client):
    resp = client.get("/api/communications?agent_type=banana")
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
    assert data["to_agent"] is None
    assert data["to_agent_type"] == "sme"
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

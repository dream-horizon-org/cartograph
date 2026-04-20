"""Tests for chat endpoints — history, polling, send, ack."""

import time


def test_get_chat_empty(client, agent_factory):
    agent_factory("orch-1")
    response = client.get("/api/chat/orch-1")
    assert response.status_code == 200
    data = response.json()
    assert data["messages"] == []
    assert data["has_more"] is False


def test_get_chat_only_this_agent(client, agent_factory, chat_factory):
    agent_factory("orch-1")
    agent_factory("orch-2")

    chat_factory("admin", "orch-1", "to orch-1")
    chat_factory("admin", "orch-2", "to orch-2")
    chat_factory("orch-1", "admin", "from orch-1")

    response = client.get("/api/chat/orch-1")
    messages = response.json()["messages"]
    assert len(messages) == 2
    texts = [m["text"] for m in messages]
    assert "to orch-1" in texts
    assert "from orch-1" in texts
    assert "to orch-2" not in texts


def test_get_chat_only_chat_type(client, agent_factory, chat_factory):
    from shared.db import execute_mutate
    agent_factory("orch-1")
    chat_factory("admin", "orch-1", "a chat")
    # Not a chat — shouldn't appear
    execute_mutate(
        """INSERT INTO communications (from_agent, to_agent, type, text)
           VALUES ('admin', 'orch-1', 'broadcast', 'not a chat')""",
    )

    response = client.get("/api/chat/orch-1")
    messages = response.json()["messages"]
    assert len(messages) == 1
    assert messages[0]["text"] == "a chat"


def test_get_chat_ordered_asc(client, agent_factory, chat_factory):
    """Messages returned in ascending order for display."""
    agent_factory("orch-1")
    chat_factory("admin", "orch-1", "first")
    time.sleep(0.01)  # ensure timestamp differs
    chat_factory("admin", "orch-1", "second")
    time.sleep(0.01)
    chat_factory("orch-1", "admin", "third")

    response = client.get("/api/chat/orch-1")
    texts = [m["text"] for m in response.json()["messages"]]
    assert texts == ["first", "second", "third"]


def test_get_chat_pagination_has_more(client, agent_factory, chat_factory):
    agent_factory("orch-1")
    for i in range(25):
        chat_factory("admin", "orch-1", f"msg-{i}")

    response = client.get("/api/chat/orch-1?limit=20")
    data = response.json()
    assert len(data["messages"]) == 20
    assert data["has_more"] is True


def test_get_chat_pagination_before_cursor(client, agent_factory, chat_factory):
    """Scroll up: use `before` to load older messages."""
    agent_factory("orch-1")
    rows = []
    for i in range(5):
        time.sleep(0.01)
        row = chat_factory("admin", "orch-1", f"msg-{i}")
        rows.append(row)

    # Get messages before the 3rd one (should return msg-0, msg-1)
    cursor = rows[2]["created_at"].isoformat() if hasattr(rows[2]["created_at"], "isoformat") else rows[2]["created_at"]
    response = client.get("/api/chat/orch-1", params={"before": cursor, "limit": 10})
    texts = [m["text"] for m in response.json()["messages"]]
    assert texts == ["msg-0", "msg-1"]


def test_get_chat_new_after_cursor(client, agent_factory, chat_factory):
    """Polling: use /new?after= to get messages since timestamp."""
    agent_factory("orch-1")
    row_old = chat_factory("admin", "orch-1", "old")
    time.sleep(0.01)
    chat_factory("admin", "orch-1", "new-1")
    chat_factory("orch-1", "admin", "new-2")

    cursor = row_old["created_at"].isoformat() if hasattr(row_old["created_at"], "isoformat") else row_old["created_at"]
    response = client.get("/api/chat/orch-1/new", params={"after": cursor})
    texts = [m["text"] for m in response.json()["messages"]]
    assert set(texts) == {"new-1", "new-2"}


def test_post_chat_admin_sends(client, agent_factory):
    agent_factory("orch-1")
    response = client.post(
        "/api/chat/orch-1",
        json={"message": "hello orchestrator"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["from_agent"] == "admin"
    assert data["to_agent"] == "orch-1"
    assert data["text"] == "hello orchestrator"
    assert data["type"] == "chat"

    # Verify it shows up in chat history
    history = client.get("/api/chat/orch-1").json()
    assert len(history["messages"]) == 1
    assert history["messages"][0]["text"] == "hello orchestrator"


def test_post_chat_rejects_empty(client, agent_factory):
    agent_factory("orch-1")
    response = client.post("/api/chat/orch-1", json={"message": ""})
    assert response.status_code in (400, 422)


def test_post_chat_rejects_missing_field(client, agent_factory):
    agent_factory("orch-1")
    response = client.post("/api/chat/orch-1", json={})
    assert response.status_code in (400, 422)


def test_post_chat_unknown_agent(client):
    response = client.post(
        "/api/chat/nonexistent",
        json={"message": "hi"},
    )
    assert response.status_code == 404


def test_ack_chats(client, agent_factory, chat_factory):
    agent_factory("orch-1")
    row1 = chat_factory("orch-1", "admin", "response 1")
    row2 = chat_factory("orch-1", "admin", "response 2")

    response = client.post(
        "/api/chat/orch-1/ack",
        json={"communication_ids": [str(row1["id"]), str(row2["id"])]},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["acked"] == 2


def test_ack_chats_only_admin_direction(client, agent_factory, chat_factory):
    """Admin acks are for messages from agent → admin, not admin → agent."""
    agent_factory("orch-1")
    row_to_admin = chat_factory("orch-1", "admin", "from agent")
    row_from_admin = chat_factory("admin", "orch-1", "from admin")

    response = client.post(
        "/api/chat/orch-1/ack",
        json={"communication_ids": [
            str(row_to_admin["id"]),
            str(row_from_admin["id"]),
        ]},
    )
    # Only the agent → admin one gets acked by admin UI
    assert response.json()["acked"] == 1

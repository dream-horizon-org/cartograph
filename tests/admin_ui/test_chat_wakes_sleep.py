"""Phase 5.8: admin UI chat must clear sleep_until on the recipient.

Mirrors the MCP send_chat behaviour for the FastAPI POST /api/chat
endpoint, which writes to communications directly (HLD §10.3) and
previously skipped the wake side-effect.
"""

from datetime import datetime, timedelta, timezone

from shared.db import execute_mutate, execute_one


def _make_sleeping_agent(agent_id: str, agent_type: str = "sme",
                         minutes_ahead: int = 30):
    until = datetime.now(timezone.utc) + timedelta(minutes=minutes_ahead)
    execute_mutate(
        """INSERT INTO agent_runs (agent_id, agent_type, status, sleep_until)
           VALUES (%s, %s, 'idle', %s)""",
        (agent_id, agent_type, until),
    )


def test_admin_chat_clears_sleep_until(client):
    _make_sleeping_agent("sleepy-sme")
    r = client.post("/api/chat/sleepy-sme", json={"message": "wake up"})
    assert r.status_code == 200
    row = execute_one(
        "SELECT sleep_until FROM agent_runs WHERE agent_id = 'sleepy-sme'"
    )
    assert row["sleep_until"] is None, "Sleep should be cleared after admin chat"


def test_admin_chat_to_awake_agent_leaves_sleep_alone(client, agent_factory):
    agent_factory("awake-sme", "sme")
    # No sleep_until set; should remain NULL.
    r = client.post("/api/chat/awake-sme", json={"message": "hi"})
    assert r.status_code == 200
    row = execute_one(
        "SELECT sleep_until FROM agent_runs WHERE agent_id = 'awake-sme'"
    )
    assert row["sleep_until"] is None

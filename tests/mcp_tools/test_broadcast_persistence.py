"""Phase 5.7: update_broadcast_persistence."""

import pytest

from shared.db import execute_returning, execute_one
from cartograph_mcp.tools import broadcast


def test_toggle_on(agent_factory):
    sent = broadcast.send_broadcast("admin", "sme", "fix", persistent=False)
    updated = broadcast.update_broadcast_persistence(
        "admin", str(sent["id"]), True
    )
    assert updated["is_persistent"] is True


def test_toggle_off(agent_factory):
    sent = broadcast.send_broadcast("admin", "sme", "policy", persistent=True)
    updated = broadcast.update_broadcast_persistence(
        "admin", str(sent["id"]), False
    )
    assert updated["is_persistent"] is False


def test_orchestrator_can_toggle(agent_factory):
    agent_factory("orch", "orchestrator")
    sent = broadcast.send_broadcast("admin", "sme", "x", persistent=False)
    updated = broadcast.update_broadcast_persistence(
        "orch", str(sent["id"]), True
    )
    assert updated["is_persistent"] is True


def test_non_admin_rejected(agent_factory):
    agent_factory("sme-x", "sme")
    sent = broadcast.send_broadcast("admin", "sme", "x", persistent=False)
    with pytest.raises(ValueError, match="Only orchestrator or admin"):
        broadcast.update_broadcast_persistence("sme-x", str(sent["id"]), True)


def test_non_broadcast_rejected(agent_factory):
    agent_factory("orch", "orchestrator")
    # Insert a chat row, then try to flip persistence on it.
    chat_row = execute_returning(
        """INSERT INTO communications (from_agent, to_agent, type, text)
           VALUES ('admin', 'orch', 'chat', 'hi') RETURNING id""",
    )
    with pytest.raises(ValueError, match="only applies to broadcasts"):
        broadcast.update_broadcast_persistence("admin", str(chat_row["id"]), True)


def test_unknown_id_rejected():
    with pytest.raises(ValueError, match="not found"):
        broadcast.update_broadcast_persistence(
            "admin", "00000000-0000-0000-0000-000000000000", True
        )

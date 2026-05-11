"""Tests for get_agent_notifications + PostToolUse hook config."""

import json
import os
import pytest

from shared.db import execute_mutate, execute_one
from cartograph_mcp.tools import notifications


def _insert_comm(from_agent, to_agent, type_, text, acked=False):
    # Broadcasts target an agent_type (to_agent_type); others target an
    # agent_id (to_agent).
    to_col = "to_agent_type" if type_ == "broadcast" else "to_agent"
    if acked:
        sql = (
            f"INSERT INTO communications (from_agent, {to_col}, type, text, acked_at)"
            f" VALUES (%s, %s, %s, %s, now()) RETURNING *"
        )
    else:
        sql = (
            f"INSERT INTO communications (from_agent, {to_col}, type, text)"
            f" VALUES (%s, %s, %s, %s) RETURNING *"
        )
    from shared.db import execute_returning
    return execute_returning(sql, (from_agent, to_agent, type_, text))


# ============ get_agent_notifications ============


def test_no_unread_returns_zero(agent_factory):
    agent_factory("sme-1", "sme")
    out = notifications.get_agent_notifications("sme-1", ["admin"])
    assert out["high_priority_count"] == 0
    assert out["breakdown"] == []
    assert out["max_seen_at"] is None


def test_counts_unacked_chats_from_admin(agent_factory):
    agent_factory("sme-1", "sme")
    _insert_comm("admin", "sme-1", "chat", "important")
    _insert_comm("admin", "sme-1", "chat", "more important")
    out = notifications.get_agent_notifications("sme-1", ["admin"])
    assert out["high_priority_count"] == 2
    assert len(out["breakdown"]) == 1
    assert out["breakdown"][0] == {"from": "admin", "type": "chat", "count": 2}
    assert out["max_seen_at"] is not None


def test_counts_chat_from_orch_when_priority_includes_orchestrator(agent_factory):
    agent_factory("sme-1", "sme")
    agent_factory("orch-1", "orchestrator")
    agent_factory("sme-2", "sme")  # not priority
    _insert_comm("orch-1", "sme-1", "chat", "urgent")
    _insert_comm("sme-2", "sme-1", "chat", "fyi")  # shouldn't count
    out = notifications.get_agent_notifications("sme-1", ["admin", "orchestrator"])
    assert out["high_priority_count"] == 1
    assert out["breakdown"][0]["from"] == "orch-1"


def test_ignores_acked_chats(agent_factory):
    agent_factory("sme-1", "sme")
    _insert_comm("admin", "sme-1", "chat", "old news", acked=True)
    out = notifications.get_agent_notifications("sme-1", ["admin"])
    assert out["high_priority_count"] == 0


def test_counts_unacked_broadcasts(agent_factory):
    agent_factory("sme-1", "sme")
    agent_factory("orch-1", "orchestrator")
    _insert_comm("orch-1", "sme", "broadcast", "all SMEs heads up")
    out = notifications.get_agent_notifications("sme-1", ["orchestrator"])
    assert out["high_priority_count"] == 1
    assert out["breakdown"][0] == {"from": "orch-1", "type": "broadcast", "count": 1}


def test_broadcast_ack_excluded(agent_factory):
    agent_factory("sme-1", "sme")
    agent_factory("orch-1", "orchestrator")
    comm = _insert_comm("orch-1", "sme", "broadcast", "heads up")
    execute_mutate(
        "INSERT INTO broadcast_acks (communication_id, agent_id) VALUES (%s, %s)",
        (comm["id"], "sme-1"),
    )
    out = notifications.get_agent_notifications("sme-1", ["orchestrator"])
    assert out["high_priority_count"] == 0


def test_since_filter_excludes_older_items(agent_factory):
    agent_factory("sme-1", "sme")
    _insert_comm("admin", "sme-1", "chat", "earlier")
    # Capture current time then insert another
    first = notifications.get_agent_notifications("sme-1", ["admin"])
    assert first["high_priority_count"] == 1
    prev_ts = first["max_seen_at"]

    # No new messages → since cutoff → 0
    out = notifications.get_agent_notifications("sme-1", ["admin"], since=prev_ts)
    assert out["high_priority_count"] == 0

    # New message after cutoff → 1
    _insert_comm("admin", "sme-1", "chat", "later")
    out = notifications.get_agent_notifications("sme-1", ["admin"], since=prev_ts)
    assert out["high_priority_count"] == 1


def test_default_priority_is_admin(agent_factory):
    agent_factory("sme-1", "sme")
    agent_factory("orch-1", "orchestrator")
    _insert_comm("admin", "sme-1", "chat", "from admin")
    _insert_comm("orch-1", "sme-1", "chat", "from orch")
    out = notifications.get_agent_notifications("sme-1")  # default ['admin']
    assert out["high_priority_count"] == 1
    assert out["breakdown"][0]["from"] == "admin"


def test_invalid_source_type_rejected(agent_factory):
    agent_factory("sme-1", "sme")
    with pytest.raises(ValueError, match="Invalid priority_from_agent_types"):
        notifications.get_agent_notifications("sme-1", ["foo"])


def test_unknown_agent_rejected():
    with pytest.raises(ValueError, match="not found"):
        notifications.get_agent_notifications("nobody", ["admin"])


def test_does_not_count_tasks(agent_factory):
    """Tasks are intentionally excluded — they're persistent action items
    surfaced by action_items_summary, not mid-session notifications."""
    agent_factory("orch-1", "orchestrator")
    agent_factory("iter-1", "iterator")
    execute_mutate(
        """INSERT INTO tasks (owner_agent_id, worker_agent_id, description, status)
           VALUES ('orch-1', 'iter-1', 'do stuff', 'BW')"""
    )
    out = notifications.get_agent_notifications("iter-1", ["admin", "orchestrator"])
    assert out["high_priority_count"] == 0


# ============ create_agent writes .claude/settings.json ============


def test_create_agent_writes_hook_config(tmp_path, agent_factory):
    """Verify AgentManager.create_agent writes a .claude/settings.json
    with the PostToolUse hook and correct per-type priority list."""
    from agent_management.agent_manager import AgentManager
    import yaml

    # Fake mcp_servers.yaml
    registry = {"cartograph-db": {"url": "http://localhost:8100"}}
    cfg_path = tmp_path / "mcp_servers.yaml"
    cfg_path.write_text(yaml.safe_dump(registry))

    mgr = AgentManager(
        workspace_root=str(tmp_path / "workspaces"),
        mcp_config_path=str(cfg_path),
    )

    # Create an iterator (avoid side-effects: no resource_id needed)
    iter_id = mgr.create_agent(agent_type="iterator", plane="github")

    settings_path = tmp_path / "workspaces" / iter_id / ".claude" / "settings.json"
    assert settings_path.exists()
    cfg = json.loads(settings_path.read_text())
    hooks = cfg["hooks"]["PostToolUse"]
    assert len(hooks) == 1
    assert hooks[0]["matcher"] == "*"
    cmd = hooks[0]["hooks"][0]["command"]
    assert "notify.py" in cmd
    assert f"--agent-id={iter_id}" in cmd
    # Iterator priority = admin,orchestrator
    assert "--priority=admin,orchestrator" in cmd


def test_create_orchestrator_has_admin_only_priority(tmp_path, agent_factory):
    from agent_management.agent_manager import AgentManager
    import yaml

    cfg_path = tmp_path / "mcp.yaml"
    cfg_path.write_text(yaml.safe_dump({"cartograph-db": {"url": "http://localhost:8100"}}))
    mgr = AgentManager(
        workspace_root=str(tmp_path / "ws"), mcp_config_path=str(cfg_path),
    )
    # There's already an orchestrator singleton in the test DB from earlier
    # setup, but create_agent doesn't gate on that — it just makes a new one.
    orch_id = mgr.create_agent(agent_type="orchestrator")
    cfg = json.loads(
        (tmp_path / "ws" / orch_id / ".claude" / "settings.json").read_text()
    )
    cmd = cfg["hooks"]["PostToolUse"][0]["hooks"][0]["command"]
    assert "--priority=admin" in cmd
    assert "orchestrator" not in cmd.split("--priority=")[1].split(" ")[0]

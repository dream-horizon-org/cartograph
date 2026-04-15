"""Integration test — full lifecycle without real Claude Code CLI."""

import os
import time
from unittest.mock import patch

import pytest
import yaml

from cartograph import db
from cartograph.main import boot


@pytest.fixture
def tmp_project(tmp_path):
    db_path = str(tmp_path / "cartograph.db")
    workspace_root = str(tmp_path / "workspaces")
    os.makedirs(workspace_root, exist_ok=True)
    mcp_config_path = str(tmp_path / "mcp_servers.yaml")
    mcp_config = {
        "cartograph-db": {"url": "http://localhost:3002"},
        "github-reader": {"url": "http://localhost:3001"},
    }
    with open(mcp_config_path, "w") as f:
        yaml.dump(mcp_config, f)
    return {
        "db_path": db_path,
        "workspace_root": workspace_root,
        "mcp_config_path": mcp_config_path,
    }


def test_full_lifecycle(tmp_project):
    """Boot system, trigger manager processes orchestrator + resolver triggers."""
    invocations = []

    def mock_invoke(self, agent_id, prompt):
        invocations.append({"agent_id": agent_id, "prompt": prompt})
        # Simulate successful invocation
        agent = db.get_agent(agent_id)
        if agent["session_id"] is None:
            db.update_agent_session(agent_id, f"session-{agent_id}")
        db.update_agent_status(agent_id, "idle")
        db.increment_invocation_count(agent_id)
        db.update_agent_heartbeat(agent_id)
        return '{"session_id": "session-' + agent_id + '"}'

    with patch(
        "cartograph.agent_manager.AgentManager.invoke_agent", mock_invoke
    ):
        trigger_mgr = boot(
            db_path=tmp_project["db_path"],
            workspace_root=tmp_project["workspace_root"],
            mcp_config_path=tmp_project["mcp_config_path"],
            start_trigger_manager=True,
            poll_interval=0.1,
        )
        time.sleep(1.0)
        trigger_mgr.stop()

    # Both singleton agents should have been invoked
    assert len(invocations) == 2
    invoked_types = set()
    for inv in invocations:
        agent = db.get_agent(inv["agent_id"])
        invoked_types.add(agent["agent_type"])
    assert "orchestrator" in invoked_types
    assert "resolver" in invoked_types

    # No pending triggers should remain
    assert len(db.get_pending_triggers()) == 0

    # Both agents should be idle with session IDs
    for inv in invocations:
        agent = db.get_agent(inv["agent_id"])
        assert agent["status"] == "idle"
        assert agent["session_id"] is not None
        assert agent["invocation_count"] == 1

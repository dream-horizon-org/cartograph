import os

import pytest
import yaml

from agent_management import db
from main import boot


@pytest.fixture
def tmp_project(tmp_path):
    workspace_root = str(tmp_path / "workspaces")
    os.makedirs(workspace_root, exist_ok=True)
    mcp_config_path = str(tmp_path / "mcp_servers.yaml")
    mcp_config = {
        "cartograph-db": {"url": "http://localhost:3002"},
    }
    with open(mcp_config_path, "w") as f:
        yaml.dump(mcp_config, f)
    return {
        "workspace_root": workspace_root,
        "mcp_config_path": mcp_config_path,
    }


def test_boot_creates_orchestrator(tmp_project):
    trigger_mgr = boot(
        workspace_root=tmp_project["workspace_root"],
        mcp_config_path=tmp_project["mcp_config_path"],
        start_trigger_manager=False,
    )

    # Should have only the orchestrator singleton
    agents = []
    for trigger in db.get_pending_triggers():
        agent = db.get_agent(trigger["agent_id"])
        agents.append(agent)

    agent_types = {a["agent_type"] for a in agents}
    assert "orchestrator" in agent_types
    assert "resolver" not in agent_types


def test_boot_is_idempotent(tmp_project):
    boot(
        workspace_root=tmp_project["workspace_root"],
        mcp_config_path=tmp_project["mcp_config_path"],
        start_trigger_manager=False,
    )
    boot(
        workspace_root=tmp_project["workspace_root"],
        mcp_config_path=tmp_project["mcp_config_path"],
        start_trigger_manager=False,
    )

    # Should still have only 1 agent (orchestrator singleton)
    triggers = db.get_pending_triggers()
    agent_ids = {t["agent_id"] for t in triggers}
    assert len(agent_ids) == 1

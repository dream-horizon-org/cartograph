import json
import os

import pytest
import yaml

from cartograph import db
from cartograph.agent_manager import AgentManager


@pytest.fixture
def tmp_project(tmp_path):
    """Set up a temporary project with DB and MCP config."""
    db_path = str(tmp_path / "cartograph.db")
    db.init_db(db_path)
    workspace_root = str(tmp_path / "workspaces")
    os.makedirs(workspace_root, exist_ok=True)
    mcp_config_path = str(tmp_path / "mcp_servers.yaml")
    mcp_config = {
        "cartograph-db": {"url": "http://localhost:3002"},
        "github-reader": {"url": "http://localhost:3001"},
        "cloud-reader": {"url": "http://localhost:3003"},
    }
    with open(mcp_config_path, "w") as f:
        yaml.dump(mcp_config, f)
    return {
        "db_path": db_path,
        "workspace_root": workspace_root,
        "mcp_config_path": mcp_config_path,
    }


@pytest.fixture
def manager(tmp_project):
    return AgentManager(
        workspace_root=tmp_project["workspace_root"],
        mcp_config_path=tmp_project["mcp_config_path"],
    )


def test_create_orchestrator(manager):
    agent_id = manager.create_agent("orchestrator")
    assert agent_id.startswith("orch-")
    agent = db.get_agent(agent_id)
    assert agent["agent_type"] == "orchestrator"
    assert agent["status"] == "pending"
    assert os.path.isdir(agent["workspace_path"])


def test_create_iterator_with_plane(manager):
    agent_id = manager.create_agent("iterator", plane="github")
    assert "github" in agent_id
    agent = db.get_agent(agent_id)
    assert agent["plane"] == "github"


def test_create_sme_with_resource(manager):
    agent_id = manager.create_agent("sme", plane="github", resource_id="repo-xyz")
    agent = db.get_agent(agent_id)
    assert agent["resource_id"] == "repo-xyz"
    assert agent["plane"] == "github"


def test_create_agent_writes_mcp_json(manager):
    agent_id = manager.create_agent("orchestrator")
    agent = db.get_agent(agent_id)
    mcp_path = os.path.join(agent["workspace_path"], ".mcp.json")
    assert os.path.isfile(mcp_path)
    with open(mcp_path) as f:
        mcp = json.load(f)
    assert "mcpServers" in mcp
    assert "cartograph-db" in mcp["mcpServers"]


def test_create_agent_enqueues_trigger(manager):
    agent_id = manager.create_agent("orchestrator")
    triggers = db.get_pending_triggers()
    assert len(triggers) == 1
    assert triggers[0]["agent_id"] == agent_id
    assert triggers[0]["priority"] == 100


def test_create_iterator_mcp_json_has_plane_reader(manager):
    agent_id = manager.create_agent("iterator", plane="github")
    agent = db.get_agent(agent_id)
    mcp_path = os.path.join(agent["workspace_path"], ".mcp.json")
    with open(mcp_path) as f:
        mcp = json.load(f)
    assert "github-reader" in mcp["mcpServers"]


def test_deactivate_agent(manager):
    agent_id = manager.create_agent("orchestrator")
    manager.deactivate_agent(agent_id)
    agent = db.get_agent(agent_id)
    assert agent["status"] == "decommissioned"
    assert len(db.get_pending_triggers()) == 0

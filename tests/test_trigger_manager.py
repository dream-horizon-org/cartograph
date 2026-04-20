import os
import sqlite3
import time
from unittest.mock import patch

import pytest
import yaml

from agent_management import db
from agent_management.agent_manager import AgentManager
from agent_management.trigger_manager import TriggerManager


@pytest.fixture
def tmp_project(tmp_path):
    db_path = str(tmp_path / "cartograph.db")
    db.init_db(db_path)
    workspace_root = str(tmp_path / "workspaces")
    os.makedirs(workspace_root, exist_ok=True)
    mcp_config_path = str(tmp_path / "mcp_servers.yaml")
    mcp_config = {
        "cartograph-db": {"url": "http://localhost:3002"},
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


@pytest.fixture
def trigger_mgr(manager):
    return TriggerManager(
        agent_manager=manager,
        poll_interval=0.1,
        heartbeat_timeout=300,
    )


def test_trigger_manager_starts_and_stops(trigger_mgr):
    trigger_mgr.start()
    assert trigger_mgr.running is True
    time.sleep(0.2)
    trigger_mgr.stop()
    assert trigger_mgr.running is False


def test_trigger_manager_processes_pending_trigger(trigger_mgr, manager):
    agent_id = manager.create_agent("orchestrator")

    with patch.object(manager, "invoke_agent", return_value="ok") as mock_invoke:
        trigger_mgr.start()
        time.sleep(0.5)
        trigger_mgr.stop()

    mock_invoke.assert_called_once()
    call_args = mock_invoke.call_args
    assert call_args[0][0] == agent_id

    triggers = db.get_pending_triggers()
    assert len(triggers) == 0


def test_trigger_manager_respects_priority(trigger_mgr, manager):
    sme_id = manager.create_agent("sme", plane="github", resource_id="repo-1")
    db.update_agent_status(sme_id, "idle")

    orch_id = manager.create_agent("orchestrator")

    invocation_order = []

    def mock_invoke(agent_id, prompt):
        invocation_order.append(agent_id)
        return "ok"

    with patch.object(manager, "invoke_agent", side_effect=mock_invoke):
        trigger_mgr.start()
        time.sleep(0.5)
        trigger_mgr.stop()

    assert invocation_order[0] == orch_id


def test_trigger_manager_skips_running_agents(trigger_mgr, manager):
    agent_id = manager.create_agent("orchestrator")
    db.update_agent_status(agent_id, "running")

    with patch.object(manager, "invoke_agent") as mock_invoke:
        trigger_mgr.start()
        time.sleep(0.3)
        trigger_mgr.stop()

    mock_invoke.assert_not_called()


def test_trigger_manager_handles_stale_agents(tmp_project, manager):
    agent_id = manager.create_agent("orchestrator")
    db.update_agent_status(agent_id, "running")
    # Set heartbeat to far past
    conn = sqlite3.connect(tmp_project["db_path"])
    conn.execute(
        "UPDATE agent_runs SET heartbeat = '2020-01-01T00:00:00+00:00' WHERE agent_id = ?",
        (agent_id,),
    )
    conn.commit()
    conn.close()

    trigger_mgr = TriggerManager(
        agent_manager=manager,
        poll_interval=0.1,
        heartbeat_timeout=1,
    )

    with patch.object(manager, "invoke_agent", return_value="ok"):
        trigger_mgr.start()
        time.sleep(0.3)
        trigger_mgr.stop()

    agent = db.get_agent(agent_id)
    assert agent["status"] == "errored"

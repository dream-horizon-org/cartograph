"""Cartograph Agent Runtime — entry point."""

from __future__ import annotations

import logging
import os
import signal
import sys

from cartograph import db
from cartograph.agent_manager import AgentManager
from cartograph.trigger_manager import TriggerManager

logger = logging.getLogger(__name__)

_DEFAULT_DB_PATH = "cartograph.db"
_DEFAULT_WORKSPACE_ROOT = "workspaces"
_DEFAULT_MCP_CONFIG = os.path.join(
    os.path.dirname(__file__), "mcp_servers.yaml"
)


def _agent_type_exists(agent_type: str) -> bool:
    """Check if a singleton agent of this type already exists (not decommissioned)."""
    conn = db._connect()
    cursor = conn.execute(
        "SELECT 1 FROM agent_runs WHERE agent_type = ? AND status != 'decommissioned' LIMIT 1",
        (agent_type,),
    )
    exists = cursor.fetchone() is not None
    conn.close()
    return exists


def boot(
    db_path: str = _DEFAULT_DB_PATH,
    workspace_root: str = _DEFAULT_WORKSPACE_ROOT,
    mcp_config_path: str = _DEFAULT_MCP_CONFIG,
    start_trigger_manager: bool = True,
    poll_interval: float = 2.0,
    heartbeat_timeout: int = 300,
) -> TriggerManager:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    db.init_db(db_path)

    os.makedirs(workspace_root, exist_ok=True)

    manager = AgentManager(
        workspace_root=workspace_root,
        mcp_config_path=mcp_config_path,
    )

    if not _agent_type_exists("orchestrator"):
        manager.create_agent("orchestrator")
        logger.info("Created orchestrator agent")

    if not _agent_type_exists("resolver"):
        manager.create_agent("resolver")
        logger.info("Created resolver agent")

    trigger_mgr = TriggerManager(
        agent_manager=manager,
        poll_interval=poll_interval,
        heartbeat_timeout=heartbeat_timeout,
    )

    if start_trigger_manager:
        trigger_mgr.start()
        logger.info("Cartograph Agent Runtime is running")

    return trigger_mgr


def main() -> None:
    trigger_mgr = boot()

    def handle_shutdown(signum, frame):
        logger.info("Shutting down...")
        trigger_mgr.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    signal.pause()


if __name__ == "__main__":
    main()

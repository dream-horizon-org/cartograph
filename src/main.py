"""Cartograph Agent Manager — entry point.

Boots the agent-management-side services:
- Creates singleton orchestrator + resolver agents if they don't exist
- Starts the invoke loop (polls for locked agents, invokes them)

The trigger manager (scanning + locking) runs separately in
src/trigger_management/main.py. Same DB, different process.
"""

from __future__ import annotations

import logging
import os
import signal
import threading
import sys

from agent_management import db
from agent_management.agent_manager import AgentManager
from agent_management.invoke_loop import InvokeLoop
from shared.db import init_pool
from shared.migrations import run_migrations

logger = logging.getLogger(__name__)

_DEFAULT_WORKSPACE_ROOT = "workspaces"
_DEFAULT_MCP_CONFIG = os.path.join(
    os.path.dirname(__file__), "mcp_servers.yaml"
)


def boot(
    workspace_root: str = _DEFAULT_WORKSPACE_ROOT,
    mcp_config_path: str = _DEFAULT_MCP_CONFIG,
    start_invoke_loop: bool = True,
    poll_interval: float = 2.0,
    heartbeat_timeout: int = 300,
) -> InvokeLoop:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    init_pool()
    run_migrations()

    os.makedirs(workspace_root, exist_ok=True)

    # On startup, flip any leftover 'running' agents to 'errored' so the
    # bounded-recovery scanner (trigger_management/scanners/recovery.py) can
    # decide whether to retry based on recovery_attempts. Leaves already-errored
    # agents alone — their errored_at already records when they failed.
    #
    # This handles the "previous agent manager was killed mid-subprocess" case
    # without blindly resurrecting agents that were deliberately errored (e.g.
    # after 3 auto-recovery failures awaiting human triage).
    from shared.db import execute_mutate
    orphaned = execute_mutate(
        """UPDATE agent_runs
           SET status = 'errored',
               trigger_lock = FALSE,
               error_msg = COALESCE(error_msg, '') ||
                           E'\n[dirty shutdown: agent manager restarted while status=running]',
               errored_at = COALESCE(errored_at, now())
           WHERE status = 'running'"""
    )
    if orphaned > 0:
        logger.info(
            "Flipped %d orphaned running agents to errored on startup "
            "(recovery scanner will retry them)",
            orphaned,
        )

    manager = AgentManager(
        workspace_root=workspace_root,
        mcp_config_path=mcp_config_path,
    )

    if not db.agent_type_exists("orchestrator"):
        manager.create_agent("orchestrator")
        logger.info("Created orchestrator agent")

    if not db.agent_type_exists("resolver"):
        manager.create_agent("resolver")
        logger.info("Created resolver agent")

    invoke_loop = InvokeLoop(
        agent_manager=manager,
        poll_interval=poll_interval,
        heartbeat_timeout=heartbeat_timeout,
    )

    if start_invoke_loop:
        invoke_loop.start()
        logger.info("Cartograph Agent Manager is running")

    return invoke_loop


def main() -> None:
    invoke_loop = boot()

    # Handle shutdown cleanly only on explicit SIGINT (Ctrl-C).
    # Ignore SIGHUP (which the parent shell sends when it exits — we want
    # the agent manager to survive shell death). SIGTERM is respected.
    shutdown = threading.Event()

    def handle_shutdown(signum, frame):
        logger.info("Shutting down on signal %d...", signum)
        invoke_loop.stop()
        shutdown.set()

    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)
    # Ignore SIGHUP so we survive parent-shell exit in nohup/background scenarios
    try:
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
    except (AttributeError, ValueError):
        pass  # Windows or unsupported context

    # Sleep forever (resilient to spurious signals that don't trigger shutdown)
    while not shutdown.is_set():
        shutdown.wait(60)
    sys.exit(0)


if __name__ == "__main__":
    main()

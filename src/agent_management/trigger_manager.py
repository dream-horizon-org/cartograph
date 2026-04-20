"""Agent Manager Poller — polls for locked agents and invokes them.

This is the agent-management-side loop. It is NOT the trigger manager
(which lives in src/trigger_management/ and handles scanning + locking).

Flow:
  1. Poll agent_runs WHERE trigger_lock = TRUE (ordered by priority)
  2. For each locked agent:
     - Atomic pickup: transition lock → running
     - Invoke with generic prompt
     - On yield: set status = 'idle'
  3. Handle stale 'running' agents (heartbeat timeout)

Kept name TriggerManager for backwards compatibility with existing tests/main.
"""

from __future__ import annotations

import logging
import threading
import time

from agent_management import db
from agent_management.agent_manager import AgentManager

logger = logging.getLogger(__name__)


class TriggerManager:
    """Agent manager poller — picks up locked agents and invokes them."""

    def __init__(
        self,
        agent_manager: AgentManager,
        poll_interval: float = 2.0,
        heartbeat_timeout: int = 300,
    ) -> None:
        self.agent_manager = agent_manager
        self.poll_interval = poll_interval
        self.heartbeat_timeout = heartbeat_timeout
        self.running = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self.running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        logger.info(
            "Agent manager poller started (poll_interval=%.1fs)",
            self.poll_interval,
        )

    def stop(self) -> None:
        self.running = False
        if self._thread is not None:
            self._thread.join(timeout=self.poll_interval * 2)
        logger.info("Agent manager poller stopped")

    def _loop(self) -> None:
        while self.running:
            try:
                self._handle_stale_agents()
                self._process_locked_agents()
            except Exception:
                logger.exception("Error in agent manager poller loop")
            time.sleep(self.poll_interval)

    def _handle_stale_agents(self) -> None:
        """Agents stuck in 'running' past heartbeat timeout → errored."""
        stale = db.get_stale_running_agents(self.heartbeat_timeout)
        for agent in stale:
            agent_id = agent["agent_id"]
            logger.warning("Agent %s is stale, marking as errored", agent_id)
            db.update_agent_status(agent_id, "errored")

    def _process_locked_agents(self) -> None:
        """Pick up locked agents (in priority order) and invoke them."""
        locked = db.get_locked_agents()
        for agent in locked:
            if not self.running:
                break

            agent_id = agent["agent_id"]
            try:
                self.agent_manager.invoke_agent(agent_id)
            except Exception:
                logger.exception("Failed to invoke agent %s", agent_id)
                db.update_agent_status(agent_id, "errored")
                db.release_trigger_lock(agent_id)

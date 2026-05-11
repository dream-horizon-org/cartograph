"""Invoke Loop — lane-based parallel dispatcher for locked agents.

One dedicated worker thread per lane per agent type. Each worker loops:
  1. Atomically claim the next highest-priority locked agent of its type
     via pickup_next_locked_agent_of_type (SELECT ... FOR UPDATE SKIP LOCKED
     — no snapshot, no staleness, no cross-worker races).
  2. Invoke it via AgentManager.invoke_agent(already_picked_up=True).
  3. Loop immediately if another agent of same type is waiting;
     otherwise sleep poll_interval.

Lane sizes come from shared.config (env-overridable). A separate
housekeeping thread handles stale-running agents (heartbeat expired)
since that's cross-cutting.

Separate from the trigger manager (src/trigger_management/) which writes
trigger_lock=TRUE — it's the source; this loop is the sink.
"""

from __future__ import annotations

import logging
import threading
import time

from agent_management import db
from agent_management.agent_manager import AgentManager
from shared import config

logger = logging.getLogger(__name__)


_AGENT_TYPES_WITH_LANES = ("orchestrator", "iterator", "resolver", "sme")


def _lanes_for(agent_type: str) -> int:
    return {
        "orchestrator": config.INVOKE_LANES_ORCH,
        "iterator":     config.INVOKE_LANES_ITER,
        "resolver":     config.INVOKE_LANES_RES,
        "sme":          config.INVOKE_LANES_SME,
    }[agent_type]


class InvokeLoop:
    """Lane-based parallel agent invoker."""

    def __init__(
        self,
        agent_manager: AgentManager,
        poll_interval: float = 2.0,
        heartbeat_timeout: int = 120,
    ) -> None:
        self.agent_manager = agent_manager
        self.poll_interval = poll_interval
        self.heartbeat_timeout = heartbeat_timeout
        self.running = False
        self._workers: list[threading.Thread] = []
        self._stale_thread: threading.Thread | None = None

    @property
    def total_lanes(self) -> int:
        return sum(_lanes_for(t) for t in _AGENT_TYPES_WITH_LANES)

    def start(self) -> None:
        self.running = True
        # Spawn one worker thread per lane per type.
        for agent_type in _AGENT_TYPES_WITH_LANES:
            n = _lanes_for(agent_type)
            for i in range(n):
                t = threading.Thread(
                    target=self._lane_worker,
                    args=(agent_type, i),
                    daemon=True,
                    name=f"invoke-{agent_type}-{i}",
                )
                t.start()
                self._workers.append(t)
        # Separate stale-agent housekeeping thread.
        self._stale_thread = threading.Thread(
            target=self._stale_worker, daemon=True, name="invoke-stale"
        )
        self._stale_thread.start()
        logger.info(
            "Invoke loop started: %d lanes total (orch=%d, iter=%d, res=%d, sme=%d)",
            self.total_lanes,
            config.INVOKE_LANES_ORCH, config.INVOKE_LANES_ITER,
            config.INVOKE_LANES_RES, config.INVOKE_LANES_SME,
        )

    def stop(self) -> None:
        self.running = False
        # Workers are daemons; they'll exit on their next loop iteration.
        # join briefly so logs settle, but don't block shutdown.
        for t in self._workers + ([self._stale_thread] if self._stale_thread else []):
            t.join(timeout=self.poll_interval)
        logger.info("Invoke loop stopped")

    def _lane_worker(self, agent_type: str, lane_index: int) -> None:
        """One lane-worker thread. Owns one claude subprocess slot for agent_type."""
        logger.info("Lane worker started: %s#%d", agent_type, lane_index)
        while self.running:
            try:
                claimed = db.pickup_next_locked_agent_of_type(agent_type)
            except Exception:
                logger.exception(
                    "Lane %s#%d: pickup failed", agent_type, lane_index
                )
                claimed = None

            if claimed is None:
                # Nothing lockable — sleep briefly, then retry.
                time.sleep(self.poll_interval)
                continue

            agent_id = claimed["agent_id"]
            logger.info(
                "Lane %s#%d picked up %s (inv=%d)",
                agent_type, lane_index, agent_id, claimed["invocation_count"],
            )
            try:
                self.agent_manager.invoke_agent(agent_id, already_picked_up=True)
            except Exception as e:
                logger.exception(
                    "Lane %s#%d: invoke_agent raised for %s",
                    agent_type, lane_index, agent_id,
                )
                db.set_agent_errored(
                    agent_id, f"Invoke loop caught {type(e).__name__}: {e}"
                )
            # Loop immediately to check for more work of same type.

    def _stale_worker(self) -> None:
        """Background thread: flip 'running' agents with stale heartbeat to errored.

        Safety net — the agent manager's own error paths write error_msg first,
        but a crash between heartbeat updates (e.g. DB connection lost) can
        leave rows orphaned in 'running'. Detect and recover.
        """
        while self.running:
            try:
                stale = db.get_stale_running_agents(self.heartbeat_timeout)
                for agent in stale:
                    agent_id = agent["agent_id"]
                    logger.warning("Agent %s is stale, marking as errored", agent_id)
                    db.set_agent_errored(
                        agent_id,
                        f"Heartbeat stale for >{self.heartbeat_timeout}s while "
                        f"status='running' (heartbeat keeper stopped or process died).",
                    )
            except Exception:
                logger.exception("Stale worker: check failed")
            time.sleep(self.poll_interval * 5)  # less urgent than pickups

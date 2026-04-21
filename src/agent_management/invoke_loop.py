"""Invoke Loop — polls for locked agents and invokes them concurrently.

This is the agent-management-side loop. Separate from the trigger manager
(src/trigger_management/) which scans tables and sets locks.

Flow:
  1. Poll agent_runs WHERE trigger_lock = TRUE (ordered by priority)
  2. Submit agents to a thread pool (skip those already in-flight)
  3. Re-query every poll cycle so high-priority agents are never starved
  4. Handle stale 'running' agents (heartbeat timeout)
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from agent_management import db
from agent_management.agent_manager import AgentManager

logger = logging.getLogger(__name__)


_HIGH_PRIORITY_TYPES = frozenset({"orchestrator", "resolver"})


class InvokeLoop:
    """Polls locked agents from agent_runs and invokes them via AgentManager."""

    def __init__(
        self,
        agent_manager: AgentManager,
        poll_interval: float = 2.0,
        heartbeat_timeout: int = 120,
        max_concurrent: int = 5,
    ) -> None:
        self.agent_manager = agent_manager
        self.poll_interval = poll_interval
        self.heartbeat_timeout = heartbeat_timeout
        self.max_concurrent = max_concurrent
        self.running = False
        self._thread: threading.Thread | None = None
        self._in_flight: set[str] = set()
        self._in_flight_lock = threading.Lock()
        self._executor: ThreadPoolExecutor | None = None

    def start(self) -> None:
        self.running = True
        # +2 headroom so orchestrator/resolver can always overflow
        self._executor = ThreadPoolExecutor(
            max_workers=self.max_concurrent + len(_HIGH_PRIORITY_TYPES),
        )
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        logger.info(
            "Invoke loop started (poll_interval=%.1fs, max_concurrent=%d)",
            self.poll_interval,
            self.max_concurrent,
        )

    def stop(self) -> None:
        self.running = False
        if self._executor is not None:
            self._executor.shutdown(wait=True, cancel_futures=True)
        if self._thread is not None:
            self._thread.join(timeout=self.poll_interval * 2)
        logger.info("Invoke loop stopped")

    def _loop(self) -> None:
        while self.running:
            try:
                self._handle_stale_agents()
                self._process_locked_agents()
            except Exception:
                logger.exception("Error in invoke loop")
            time.sleep(self.poll_interval)

    def _handle_stale_agents(self) -> None:
        """Agents stuck in 'running' past heartbeat timeout → errored.

        This is a safety net — in the normal path agent_manager.invoke_agent
        catches subprocess errors/timeouts and writes error_msg itself. Stale
        detection only fires if the heartbeat thread stopped updating (e.g.
        process crash, DB outage mid-run).
        """
        stale = db.get_stale_running_agents(self.heartbeat_timeout)
        for agent in stale:
            agent_id = agent["agent_id"]
            logger.warning("Agent %s is stale, marking as errored", agent_id)
            db.set_agent_errored(
                agent_id,
                f"Heartbeat stale for >{self.heartbeat_timeout}s while "
                f"status='running' (heartbeat keeper stopped or process died).",
            )

    def _process_locked_agents(self) -> None:
        """Pick up locked agents (in priority order), submit to thread pool.

        Re-queries every poll cycle so newly-locked high-priority agents
        (e.g. orchestrator) are picked up promptly — never starved behind
        a stale snapshot of lower-priority agents.

        Orchestrator/resolver bypass the concurrency limit — they are
        submitted even when all normal slots are full (the executor has
        headroom for this). Regular agents respect max_concurrent.

        Agents already in-flight are skipped to avoid double-invocation.
        """
        locked = db.get_locked_agents()
        if not locked:
            return

        with self._in_flight_lock:
            normal_in_flight = sum(
                1 for aid in self._in_flight
                if not any(aid.startswith(p) for p in ("orch-", "res-"))
            )

        normal_submitted = 0
        for agent in locked:
            if not self.running:
                break

            agent_id = agent["agent_id"]
            agent_type = agent["agent_type"]
            is_high_priority = agent_type in _HIGH_PRIORITY_TYPES

            if not is_high_priority:
                if normal_in_flight + normal_submitted >= self.max_concurrent:
                    continue

            with self._in_flight_lock:
                if agent_id in self._in_flight:
                    continue
                self._in_flight.add(agent_id)

            logger.info(
                "Submitting agent %s (type=%s, priority=%s) to thread pool "
                "(%d in-flight)",
                agent_id,
                agent_type,
                "high" if is_high_priority else "normal",
                len(self._in_flight),
            )
            self._executor.submit(self._invoke_and_cleanup, agent_id)
            if not is_high_priority:
                normal_submitted += 1

    def _invoke_and_cleanup(self, agent_id: str) -> None:
        """Invoke an agent and remove it from the in-flight set when done."""
        try:
            self.agent_manager.invoke_agent(agent_id)
        except Exception as e:
            logger.exception("Failed to invoke agent %s", agent_id)
            db.set_agent_errored(
                agent_id, f"Invoke loop caught {type(e).__name__}: {e}"
            )
        finally:
            with self._in_flight_lock:
                self._in_flight.discard(agent_id)

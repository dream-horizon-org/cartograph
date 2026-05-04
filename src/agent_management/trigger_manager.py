"""Trigger Manager — polls for pending triggers and invokes agents.

V2: adds Phase state machine and Batch Merge phase driven as code.
"""

from __future__ import annotations

import logging
import threading
import time
from enum import Enum

from agent_management import db
from agent_management.agent_manager import AgentManager

logger = logging.getLogger(__name__)


class Phase(Enum):
    ITERATION       = "iteration"
    MATERIALISATION = "materialisation"
    BATCH_MERGE     = "batch_merge"
    RESOLUTION      = "resolution"
    EDGE_DISCOVERY  = "edge_discovery"
    USER_FEEDBACK   = "user_feedback"


class TriggerManager:
    def __init__(
        self,
        agent_manager: AgentManager,
        poll_interval: float = 2.0,
        heartbeat_timeout: int = 300,
    ) -> None:
        self.agent_manager = agent_manager
        self.poll_interval = poll_interval
        self.heartbeat_timeout = heartbeat_timeout
        self.current_phase = Phase.ITERATION
        self.running = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self.running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        logger.info(
            "Trigger manager started (poll_interval=%.1fs)", self.poll_interval
        )

    def stop(self) -> None:
        self.running = False
        if self._thread is not None:
            self._thread.join(timeout=self.poll_interval * 2)
        logger.info("Trigger manager stopped")

    def advance_phase(self, phase: Phase) -> None:
        logger.info(
            "Phase transition: %s -> %s",
            self.current_phase.value, phase.value,
        )
        self.current_phase = phase

    def _loop(self) -> None:
        while self.running:
            try:
                self._handle_stale_agents()
                if self.current_phase == Phase.BATCH_MERGE:
                    self._run_batch_merge_phase()
                else:
                    self._process_pending_triggers()
            except Exception:
                logger.exception("Error in trigger manager loop")
            time.sleep(self.poll_interval)

    def _handle_stale_agents(self) -> None:
        stale = db.get_stale_running_agents(self.heartbeat_timeout)
        for agent in stale:
            agent_id = agent["agent_id"]
            logger.warning("Agent %s is stale, marking as errored", agent_id)
            db.update_agent_status(agent_id, "errored")
            db.release_trigger_lock(agent_id)

    def _process_pending_triggers(self) -> None:
        triggers = db.get_pending_triggers()
        for trigger in triggers:
            if not self.running:
                break

            agent = db.get_agent(trigger["agent_id"])
            if agent is None:
                db.update_trigger_status(trigger["id"], "done")
                continue

            if agent["status"] not in ("pending", "idle"):
                continue

            if not db.acquire_trigger_lock(trigger["agent_id"]):
                continue

            db.update_trigger_status(trigger["id"], "processing")
            try:
                self.agent_manager.invoke_agent(
                    trigger["agent_id"], trigger["prompt"]
                )
            except Exception:
                logger.exception(
                    "Failed to invoke agent %s", trigger["agent_id"]
                )
                db.update_agent_status(trigger["agent_id"], "errored")
                db.release_trigger_lock(trigger["agent_id"])
            finally:
                db.update_trigger_status(trigger["id"], "done")

    def _run_batch_merge_phase(self) -> None:
        """
        Execute the batch merge phase as code — no LLM involved.
        Runs once per loop iteration while phase == BATCH_MERGE,
        then advances to RESOLUTION when no pending_human candidates remain.
        Imported here to avoid circular imports at module load time.
        """
        from agent_management.batch_merger import run_batch_merge
        from agent_management.embedding import embed_component

        logger.info("Starting batch merge phase")
        summary = run_batch_merge(re_embed_fn=embed_component)
        logger.info(
            "Batch merge complete: auto_executed=%d pending_human=%d skipped=%d",
            summary["auto_executed"],
            summary["pending_human"],
            summary["skipped"],
        )

        pending = db.get_merge_candidates("pending_human")
        if not pending:
            self.advance_phase(Phase.RESOLUTION)
        else:
            logger.info(
                "%d merge candidates awaiting human confirmation",
                len(pending),
            )

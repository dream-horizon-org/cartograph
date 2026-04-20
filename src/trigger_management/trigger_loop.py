"""Trigger Manager — scans tables, sets trigger_lock on idle agents with pending items."""

import logging
import time

from shared.db import execute, execute_returning
from trigger_management.scanners import (
    chats,
    broadcasts,
    tasks,
    consolidations,
    clarifications,
    auto_transitions,
)

logger = logging.getLogger(__name__)

PRIORITY_ORDER = {"orchestrator": 0, "resolver": 1, "sme": 2, "iterator": 3}


def get_idle_agents() -> list[dict]:
    """Get all idle agents that don't already have a trigger lock."""
    return execute(
        """SELECT agent_id, agent_type, invocation_count
           FROM agent_runs
           WHERE status = 'idle' AND trigger_lock = FALSE"""
    )


def has_pending_items(agent_id: str, agent_type: str) -> bool:
    """Check if an agent has any pending action items across all tables."""
    if chats.scan(agent_id) > 0:
        return True
    if broadcasts.scan(agent_id, agent_type) > 0:
        return True
    if tasks.scan(agent_id) > 0:
        return True
    if consolidations.scan(agent_id, agent_type) > 0:
        return True
    if clarifications.scan(agent_id) > 0:
        return True
    return False


def prioritise(agents: list[dict]) -> list[dict]:
    """Sort agents by type priority, then by invocation_count (lower = higher priority)."""
    return sorted(
        agents,
        key=lambda a: (PRIORITY_ORDER.get(a["agent_type"], 99), a["invocation_count"]),
    )


def try_lock_agent(agent_id: str) -> bool:
    """Atomically set trigger_lock=TRUE. Returns True if lock was acquired."""
    row = execute_returning(
        """UPDATE agent_runs
           SET trigger_lock = TRUE, updated_at = now()
           WHERE agent_id = %s AND status = 'idle' AND trigger_lock = FALSE
           RETURNING agent_id""",
        (agent_id,),
    )
    return row is not None


def run_once() -> int:
    """Run one scan cycle. Returns number of agents locked."""
    # 1. Run auto-transitions first (consolidation confidence breach)
    auto_transitions.run_auto_transitions()

    # 2. Get all idle, unlocked agents
    idle_agents = get_idle_agents()
    if not idle_agents:
        return 0

    # 3. Check which ones have pending items
    agents_with_work = []
    for agent in idle_agents:
        if has_pending_items(agent["agent_id"], agent["agent_type"]):
            agents_with_work.append(agent)

    if not agents_with_work:
        return 0

    # 4. Prioritise
    sorted_agents = prioritise(agents_with_work)

    # 5. Lock them
    locked = 0
    for agent in sorted_agents:
        if try_lock_agent(agent["agent_id"]):
            logger.info(
                "Locked agent %s (%s) for invocation",
                agent["agent_id"],
                agent["agent_type"],
            )
            locked += 1

    return locked


def run_loop(poll_interval: float = 2.0) -> None:
    """Run the trigger manager loop forever."""
    logger.info("Trigger manager started (poll_interval=%.1fs)", poll_interval)
    while True:
        try:
            locked = run_once()
            if locked > 0:
                logger.info("Locked %d agents this cycle", locked)
        except Exception:
            logger.exception("Error in trigger manager loop")
        time.sleep(poll_interval)

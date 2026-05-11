"""Trigger Manager — scans tables, sets trigger_lock on idle agents with pending items."""

import logging
import time

from agent_management.db import release_stale_locks_on_sleeping
from shared.db import execute, execute_returning
from trigger_management.scanners import (
    chats,
    broadcasts,
    tasks,
    consolidations,
    clarifications,
    auto_transitions,
    proxies,
    recovery,
)

logger = logging.getLogger(__name__)

PRIORITY_ORDER = {"orchestrator": 0, "resolver": 1, "sme": 2, "iterator": 3}

# Phase 7.4.14 (token-opt Round 3 #6) — wake debouncing window.
# Pending items must wait this long before triggering a wake, unless an
# override applies (admin chat / mid-mutation). Coalesces drip-fed
# events into one wake instead of N small ones, each re-paying the
# 16k-token cached system-prompt read.
WAKE_DEBOUNCE_SECONDS = 60  # 1 minute


def get_idle_agents() -> list[dict]:
    """Get idle agents eligible for wake-up.

    Skips agents that are currently sleeping (sleep_until > now()). Any
    auto-wake path (admin chat interrupt, explicit wake tool) nulls the
    column so this filter doesn't hide them. Returns first_pending_at
    so the debounce filter can compare against now().
    """
    return execute(
        """SELECT agent_id, agent_type, invocation_count, first_pending_at
           FROM agent_runs
           WHERE status = 'idle'
             AND trigger_lock = FALSE
             AND (sleep_until IS NULL OR sleep_until <= now())"""
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
    if proxies.scan(agent_id) > 0:
        return True
    return False


def has_debounce_override(agent_id: str) -> bool:
    """Phase 7.4.14: detect conditions that bypass the wake-debounce
    window. Returns True if ANY of:
      - Pending admin chat (always immediate — admin chat is the only
        wake-from-sleep auto-wake signal; should be immediate too).
      - Agent is mutation_assigned_to on a state=M consolidation
        (mid-mutation must not be delayed; resolver is waiting for MD).
    """
    row = execute(
        """SELECT 1 FROM communications
            WHERE to_agent = %s AND from_agent = 'admin'
              AND type = 'chat' AND acked_at IS NULL
            LIMIT 1""",
        (agent_id,),
    )
    if row:
        return True
    row = execute(
        """SELECT 1 FROM consolidations
            WHERE mutation_assigned_to = %s AND status = 'M'
            LIMIT 1""",
        (agent_id,),
    )
    return bool(row)


def stamp_first_pending(agent_id: str) -> None:
    """Phase 7.4.14: stamp first_pending_at = now() iff currently NULL.
    Called when has_pending_items returns True for an agent that didn't
    have first_pending_at set. Idempotent.
    """
    execute_returning(
        """UPDATE agent_runs
            SET first_pending_at = now()
            WHERE agent_id = %s AND first_pending_at IS NULL
            RETURNING agent_id""",
        (agent_id,),
    )


def clear_first_pending(agent_id: str) -> None:
    """Phase 7.4.14: clear first_pending_at when no items remain pending
    (called from agent_manager.invoke_agent on yield with empty queue,
    or here when the scanner sees has_pending_items=False but the
    column is set)."""
    execute_returning(
        """UPDATE agent_runs
            SET first_pending_at = NULL
            WHERE agent_id = %s AND first_pending_at IS NOT NULL
            RETURNING agent_id""",
        (agent_id,),
    )


def prioritise(agents: list[dict]) -> list[dict]:
    """Sort agents by type priority, then by invocation_count (lower = higher priority)."""
    return sorted(
        agents,
        key=lambda a: (PRIORITY_ORDER.get(a["agent_type"], 99), a["invocation_count"]),
    )


def try_lock_agent(agent_id: str) -> bool:
    """Atomically set trigger_lock=TRUE. Returns True if lock was acquired.

    Sleep filter is duplicated here (also in get_idle_agents) to close the
    race where sleep_self fires between the scanner's idle-read and this
    write. Without it, the first wake after a sleep_self call can slip
    through and fire a spurious invocation.
    """
    row = execute_returning(
        """UPDATE agent_runs
           SET trigger_lock = TRUE, updated_at = now()
           WHERE agent_id = %s AND status = 'idle' AND trigger_lock = FALSE
             AND (sleep_until IS NULL OR sleep_until <= now())
           RETURNING agent_id""",
        (agent_id,),
    )
    return row is not None


def run_once() -> int:
    """Run one scan cycle. Returns number of agents locked."""
    # 1. Run auto-transitions first (consolidation confidence breach)
    auto_transitions.run_auto_transitions()

    # 1b. Recovery: flip errored agents back to idle after backoff.
    # Must run BEFORE get_idle_agents so recovered agents get picked up
    # in the same cycle.
    recovered = recovery.run_recovery()
    if recovered > 0:
        logger.info("Recovered %d errored agent(s) this cycle", recovered)

    # 1c. Release any trigger_lock=TRUE sitting on a sleeping agent
    # (sleep_self fired after the lock was taken; we should not hold
    # the lock through the sleep window).
    released = release_stale_locks_on_sleeping()
    if released > 0:
        logger.info("Released %d stale trigger_lock on sleeping agent(s)", released)

    # 2. Get all idle, unlocked agents
    idle_agents = get_idle_agents()
    if not idle_agents:
        return 0

    # 3. Check which ones have pending items + apply debounce window.
    # Phase 7.4.14: stamp first_pending_at on first sighting. Refuse to
    # lock until WAKE_DEBOUNCE_SECONDS have elapsed unless an override
    # applies (admin chat / mid-mutation). Drip-fed events coalesce
    # into one wake instead of N small ones.
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    debounce_threshold = now - timedelta(seconds=WAKE_DEBOUNCE_SECONDS)

    agents_with_work = []
    for agent in idle_agents:
        aid = agent["agent_id"]
        if not has_pending_items(aid, agent["agent_type"]):
            # No work — clear stale first_pending_at if set.
            if agent.get("first_pending_at"):
                clear_first_pending(aid)
            continue
        # Stamp first_pending_at on first sighting (idempotent).
        first_pending = agent.get("first_pending_at")
        if first_pending is None:
            stamp_first_pending(aid)
            first_pending = now  # treat as just-stamped for this cycle
        # Override checks bypass the debounce.
        if has_debounce_override(aid):
            agents_with_work.append(agent)
            continue
        # Debounce: wait until enough time has elapsed.
        if first_pending <= debounce_threshold:
            agents_with_work.append(agent)
        # else: still in debounce window; will wake on a later cycle.

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

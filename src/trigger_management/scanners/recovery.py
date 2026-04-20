"""Recovery scanner — flips errored agents back to idle after backoff.

Bounded auto-recovery: agents get up to MAX_RECOVERY_ATTEMPTS tries with an
exponential-ish backoff ladder. After that they stay errored for human
triage (visible in the admin UI with their persisted error_msg).

This runs as part of the trigger manager's scan cycle — not the agent
manager — because it only mutates agent_runs rows, no subprocess work.
"""

from __future__ import annotations

import logging

from agent_management import db

logger = logging.getLogger(__name__)


def run_recovery() -> int:
    """Reset eligible errored agents. Returns how many were recovered."""
    candidates = db.get_recoverable_errored_agents()
    recovered = 0
    for agent in candidates:
        agent_id = agent["agent_id"]
        attempts_so_far = agent["recovery_attempts"]
        if db.reset_agent_for_recovery(agent_id):
            recovered += 1
            logger.warning(
                "Recovered errored agent %s (attempt %d/%d) — last error: %s",
                agent_id,
                attempts_so_far + 1,
                db.MAX_RECOVERY_ATTEMPTS,
                (agent.get("error_msg") or "")[:200],
            )
    return recovered

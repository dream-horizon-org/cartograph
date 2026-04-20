"""Scan for pending tasks per agent."""

from shared.db import execute


def scan(agent_id: str) -> int:
    """Return count of tasks needing this agent's attention."""
    rows = execute(
        """SELECT COUNT(*) as cnt FROM tasks
           WHERE (worker_agent_id = %s AND status = 'BW')
              OR (owner_agent_id = %s AND status IN ('BO', 'WD'))""",
        (agent_id, agent_id),
    )
    return rows[0]["cnt"] if rows else 0

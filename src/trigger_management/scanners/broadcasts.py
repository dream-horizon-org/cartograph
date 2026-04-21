"""Scan for unacked broadcasts per agent."""

from shared.db import execute


def scan(agent_id: str, agent_type: str) -> int:
    """Return count of unacked broadcasts for this agent."""
    rows = execute(
        """SELECT COUNT(*) as cnt FROM communications c
           WHERE c.type = 'broadcast' AND c.to_agent_type = %s
           AND c.id NOT IN (
               SELECT communication_id FROM broadcast_acks WHERE agent_id = %s
           )""",
        (agent_type, agent_id),
    )
    return rows[0]["cnt"] if rows else 0

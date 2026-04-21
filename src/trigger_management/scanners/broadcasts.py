"""Scan for unacked broadcasts per agent.

Forward-only semantic: a broadcast applies to agents that exist at the
moment of broadcast. Agents spawned later do NOT see historical broadcasts
UNLESS the broadcast was sent with is_persistent=TRUE (standing policy).
"""

from shared.db import execute


def scan(agent_id: str, agent_type: str) -> int:
    """Return count of unacked broadcasts for this agent."""
    rows = execute(
        """SELECT COUNT(*) AS cnt FROM communications c
           WHERE c.type = 'broadcast' AND c.to_agent_type = %s
             AND (
               c.is_persistent
               OR c.created_at > (SELECT created_at FROM agent_runs WHERE agent_id = %s)
             )
             AND c.id NOT IN (
               SELECT communication_id FROM broadcast_acks WHERE agent_id = %s
             )""",
        (agent_type, agent_id, agent_id),
    )
    return rows[0]["cnt"] if rows else 0

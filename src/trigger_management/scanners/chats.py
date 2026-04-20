"""Scan for unacked chat messages per agent."""

from shared.db import execute


def scan(agent_id: str) -> int:
    """Return count of unacked chats for this agent."""
    rows = execute(
        """SELECT COUNT(*) as cnt FROM communications
           WHERE to_agent = %s AND type = 'chat' AND acked_at IS NULL""",
        (agent_id,),
    )
    return rows[0]["cnt"] if rows else 0

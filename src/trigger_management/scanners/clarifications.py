"""Scan for pending clarifications per agent."""

from shared.db import execute


def scan(agent_id: str) -> int:
    """Return count of clarifications needing this agent's attention."""
    rows = execute(
        """SELECT COUNT(*) as cnt FROM clarifications
           WHERE (asker_agent_id = %s AND status IN ('B1', 'QR', 'QC'))
              OR (responder_agent_id = %s AND status = 'B2')""",
        (agent_id, agent_id),
    )
    return rows[0]["cnt"] if rows else 0

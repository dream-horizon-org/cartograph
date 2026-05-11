"""Scan for pending consolidations per agent."""

from shared.db import execute


def scan(agent_id: str, agent_type: str) -> int:
    """Return count of consolidations needing this agent's attention."""
    if agent_type == "resolver":
        rows = execute(
            "SELECT COUNT(*) as cnt FROM consolidations WHERE status IN ('R', 'MD')",
        )
    elif agent_type == "sme":
        rows = execute(
            """SELECT COUNT(*) as cnt FROM consolidations
               WHERE (agent_b_id = %s AND status = 'B2')
                  OR (agent_a_id = %s AND status = 'B1')
                  OR (mutation_assigned_to = %s AND status = 'M')""",
            (agent_id, agent_id, agent_id),
        )
    else:
        return 0
    return rows[0]["cnt"] if rows else 0

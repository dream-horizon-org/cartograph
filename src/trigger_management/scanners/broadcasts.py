"""Scan for unacked broadcasts per agent.

Forward-only semantic: a broadcast applies to agents that exist at the
moment of broadcast. Agents spawned later do NOT see historical broadcasts
UNLESS the broadcast was sent with is_persistent=TRUE (standing policy).
"""

from shared.db import execute, execute_one


def scan(agent_id: str, agent_type: str) -> int:
    """Return count of unacked broadcasts for this agent.

    Phase 10.8.2: skip decommissioned agents. agent_manager pickup loop
    already filters decom out of the wake list, but the scanner used to
    return non-zero counts for decom agents — wasted defensive surface.
    Now matches the read-tool gate: decom contributes 0 to the wake-list
    signal regardless of pending broadcasts.
    """
    actor = execute_one(
        "SELECT status FROM agent_runs WHERE agent_id = %s",
        (agent_id,),
    )
    if actor is None or actor["status"] == "decommissioned":
        return 0
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

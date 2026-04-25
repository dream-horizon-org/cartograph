"""Broadcast tools — send_broadcast, ack_broadcast, get_unacked_broadcasts,
update_broadcast_persistence (Phase 5.7)."""

from shared.db import execute, execute_returning, execute_mutate, execute_one


def send_broadcast(
    from_agent_id: str,
    to_agent_type: str,
    message: str,
    persistent: bool = False,
) -> dict:
    """Send a broadcast to all agents of a type. Only orchestrator/admin can broadcast.

    persistent: if True, this broadcast also applies to agents spawned
    AFTER it is sent (standing policy — "all future SMEs should use X").
    Default False = forward-only, seen by agents that exist at send time.
    """
    if from_agent_id not in ("admin",) and not _is_orchestrator(from_agent_id):
        raise ValueError("Only orchestrator or admin can send broadcasts.")

    row = execute_returning(
        """INSERT INTO communications (from_agent, to_agent_type, type, text, is_persistent)
           VALUES (%s, %s, 'broadcast', %s, %s)
           RETURNING *""",
        (from_agent_id, to_agent_type, message, persistent),
    )
    return row


def ack_broadcast(agent_id: str, communication_id: str) -> dict:
    """Ack a broadcast message. Inserts into broadcast_acks."""
    row = execute_returning(
        """INSERT INTO broadcast_acks (communication_id, agent_id)
           VALUES (%s, %s)
           ON CONFLICT (communication_id, agent_id) DO NOTHING
           RETURNING *""",
        (communication_id, agent_id),
    )
    return row


def get_unacked_broadcasts(agent_id: str, agent_type: str) -> list[dict]:
    """Get broadcast messages this agent hasn't acked.

    Forward-only: skips broadcasts that predate this agent's spawn UNLESS
    they were sent with is_persistent=TRUE (standing policy).
    """
    return execute(
        """SELECT c.* FROM communications c
           WHERE c.type = 'broadcast' AND c.to_agent_type = %s
             AND (
               c.is_persistent
               OR c.created_at > (SELECT created_at FROM agent_runs WHERE agent_id = %s)
             )
             AND c.id NOT IN (
               SELECT communication_id FROM broadcast_acks WHERE agent_id = %s
             )
           ORDER BY c.created_at""",
        (agent_type, agent_id, agent_id),
    )


def update_broadcast_persistence(
    agent_id: str, communication_id: str, persistent: bool
) -> dict:
    """Phase 5.7: flip is_persistent on an existing broadcast.

    Admin/orchestrator only. Lets admin re-classify a broadcast that
    turned out to be standing policy (or vice versa) without re-sending.
    Toggling OFF leaves existing acks alone — only the scanner's
    forward-only filter changes for future agents.
    """
    if agent_id != "admin" and not _is_orchestrator(agent_id):
        raise ValueError("Only orchestrator or admin can toggle broadcast persistence.")
    row = execute_one(
        "SELECT id, type FROM communications WHERE id = %s::uuid",
        (communication_id,),
    )
    if row is None:
        raise ValueError(f"Communication {communication_id} not found")
    if row["type"] != "broadcast":
        raise ValueError(
            f"update_broadcast_persistence only applies to broadcasts; "
            f"row {communication_id} is type '{row['type']}'."
        )
    updated = execute_returning(
        """UPDATE communications
           SET is_persistent = %s
           WHERE id = %s::uuid AND type = 'broadcast'
           RETURNING *""",
        (persistent, communication_id),
    )
    return updated


def _is_orchestrator(agent_id: str) -> bool:
    row = execute_one(
        "SELECT 1 FROM agent_runs WHERE agent_id = %s AND agent_type = 'orchestrator'",
        (agent_id,),
    )
    return row is not None

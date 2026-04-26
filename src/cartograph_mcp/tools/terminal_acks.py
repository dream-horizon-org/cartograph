"""Phase 7.1: terminal-state acknowledgment tools.

When an entity (task / consolidation / clarification) reaches a
terminal state (TC, D/F, CC/QR), each participant must explicitly
acknowledge that they've seen and understood the resolution. The
trigger scanner re-wakes participants on every cycle until they ack.

This replaces Phase 5.5's auto-ack at write site, which silently
suppressed closure announcements without any explicit comprehension.
"""

from shared.actor_auth import require_active_agent
from shared.db import execute_one, execute_returning, execute_mutate


_VALID_ENTITY_TYPES = {"task", "consolidation", "clarification"}

_TERMINAL_STATES = {
    "task":          {"TC"},
    "consolidation": {"D", "F"},
    "clarification": {"CC", "QR"},
}


def _participants(entity_type: str, entity_id: str) -> set[str] | None:
    """Return the set of agent_ids that participate in this entity, or
    None if the entity doesn't exist. Used for participant-scope
    validation on ack_terminal."""
    if entity_type == "task":
        row = execute_one(
            "SELECT owner_agent_id, worker_agent_id, status FROM tasks WHERE id = %s::uuid",
            (entity_id,),
        )
        if row is None:
            return None
        return {row["owner_agent_id"], row["worker_agent_id"]}, row["status"]
    if entity_type == "consolidation":
        row = execute_one(
            """SELECT agent_a_id, agent_b_id, mutation_assigned_to, resolved_by, status
               FROM consolidations WHERE id = %s::uuid""",
            (entity_id,),
        )
        if row is None:
            return None
        ps = {row["agent_a_id"]}
        if row["agent_b_id"]: ps.add(row["agent_b_id"])
        if row["mutation_assigned_to"]: ps.add(row["mutation_assigned_to"])
        if row["resolved_by"]: ps.add(row["resolved_by"])
        return ps, row["status"]
    if entity_type == "clarification":
        row = execute_one(
            """SELECT asker_agent_id, responder_agent_id, status
               FROM clarifications WHERE id = %s::uuid""",
            (entity_id,),
        )
        if row is None:
            return None
        return {row["asker_agent_id"], row["responder_agent_id"]}, row["status"]
    return None


def ack_terminal(agent_id: str, entity_type: str, entity_id: str) -> dict:
    """Acknowledge a terminal-state entity. Idempotent.

    Validates:
      - entity_type ∈ {'task','consolidation','clarification'}
      - entity exists
      - entity is in a terminal state for its type
      - caller is a participant of the entity
      - caller is an active agent

    Returns {acked, already_acked}.
    """
    require_active_agent(agent_id)
    if entity_type not in _VALID_ENTITY_TYPES:
        raise ValueError(
            f"Invalid entity_type '{entity_type}'. "
            f"Allowed: {sorted(_VALID_ENTITY_TYPES)}"
        )
    result = _participants(entity_type, entity_id)
    if result is None:
        raise ValueError(f"{entity_type} {entity_id} not found")
    participants, status = result
    if status not in _TERMINAL_STATES[entity_type]:
        raise ValueError(
            f"{entity_type} {entity_id} not in a terminal state "
            f"(current: {status}, terminal: {sorted(_TERMINAL_STATES[entity_type])})"
        )
    if agent_id not in participants:
        raise ValueError(
            f"Agent {agent_id} is not a participant of {entity_type} {entity_id}"
        )
    # Insert ack — ON CONFLICT DO NOTHING for idempotency.
    inserted = execute_returning(
        """INSERT INTO terminal_acks (entity_type, entity_id, agent_id)
           VALUES (%s, %s::uuid, %s)
           ON CONFLICT (entity_type, entity_id, agent_id) DO NOTHING
           RETURNING acked_at""",
        (entity_type, entity_id, agent_id),
    )
    if inserted is None:
        return {"acked": False, "already_acked": True}
    return {"acked": True, "already_acked": False}


def auto_ack_for_decommission(agent_id: str) -> int:
    """Bulk-ack every terminal entity the agent participates in but
    hasn't acked. Called by absorb_agent when decommissioning a target,
    so the agent's ack obligations don't sit forever unfulfilled.

    Returns the number of acks inserted.
    """
    inserted = execute_mutate(
        """
        INSERT INTO terminal_acks (entity_type, entity_id, agent_id)
        SELECT 'task', t.id, %s
          FROM tasks t
         WHERE (t.owner_agent_id = %s OR t.worker_agent_id = %s)
           AND t.status = 'TC'
           AND NOT EXISTS (
                 SELECT 1 FROM terminal_acks ta
                  WHERE ta.entity_type='task' AND ta.entity_id=t.id AND ta.agent_id=%s
               )
        UNION ALL
        SELECT 'consolidation', c.id, %s
          FROM consolidations c
         WHERE (c.agent_a_id = %s OR c.agent_b_id = %s
                OR c.mutation_assigned_to = %s OR c.resolved_by = %s)
           AND c.status IN ('D','F')
           AND NOT EXISTS (
                 SELECT 1 FROM terminal_acks ta
                  WHERE ta.entity_type='consolidation' AND ta.entity_id=c.id AND ta.agent_id=%s
               )
        UNION ALL
        SELECT 'clarification', cl.id, %s
          FROM clarifications cl
         WHERE (cl.asker_agent_id = %s OR cl.responder_agent_id = %s)
           AND cl.status IN ('CC','QR')
           AND NOT EXISTS (
                 SELECT 1 FROM terminal_acks ta
                  WHERE ta.entity_type='clarification' AND ta.entity_id=cl.id AND ta.agent_id=%s
               )
        ON CONFLICT (entity_type, entity_id, agent_id) DO NOTHING
        """,
        (
            agent_id, agent_id, agent_id, agent_id,           # tasks
            agent_id, agent_id, agent_id, agent_id, agent_id, agent_id,  # consolidations
            agent_id, agent_id, agent_id, agent_id,           # clarifications
        ),
    )
    return inserted

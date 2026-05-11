"""Phase 7.1: terminal-state pending-ack scanner.

For an agent X, returns the list of terminal entities X participates
in but hasn't acknowledged. The trigger manager OR's this scan with
its existing wake conditions — agents are re-woken every cycle until
they ack each entry.
"""

from shared.db import execute


def scan_terminal_pending_ack(agent_id: str) -> list[dict]:
    """List terminal entities the agent must ack.

    Returns rows with {entity_type, entity_id, since (last activity)}.
    """
    rows = execute(
        """
        SELECT 'task' AS entity_type, t.id::text AS entity_id, t.updated_at AS since
          FROM tasks t
         WHERE (t.owner_agent_id = %s OR t.worker_agent_id = %s)
           AND t.status = 'TC'
           AND NOT EXISTS (
                 SELECT 1 FROM terminal_acks ta
                  WHERE ta.entity_type='task' AND ta.entity_id=t.id AND ta.agent_id=%s
               )
        UNION ALL
        SELECT 'consolidation', c.id::text, c.updated_at
          FROM consolidations c
         WHERE (c.agent_a_id = %s OR c.agent_b_id = %s
                OR c.mutation_assigned_to = %s OR c.resolved_by = %s)
           AND c.status IN ('D','F')
           AND NOT EXISTS (
                 SELECT 1 FROM terminal_acks ta
                  WHERE ta.entity_type='consolidation' AND ta.entity_id=c.id AND ta.agent_id=%s
               )
        UNION ALL
        SELECT 'clarification', cl.id::text, cl.updated_at
          FROM clarifications cl
         WHERE (cl.asker_agent_id = %s OR cl.responder_agent_id = %s)
           AND cl.status IN ('CC','QR')
           AND NOT EXISTS (
                 SELECT 1 FROM terminal_acks ta
                  WHERE ta.entity_type='clarification' AND ta.entity_id=cl.id AND ta.agent_id=%s
               )
        ORDER BY since DESC
        """,
        (
            agent_id, agent_id, agent_id,                    # tasks
            agent_id, agent_id, agent_id, agent_id, agent_id, # consolidations
            agent_id, agent_id, agent_id,                    # clarifications
        ),
    )
    return rows

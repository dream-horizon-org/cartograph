"""Phase 4: scan pending proxy work for a survivor agent.

Walks merged_into_agent_id bottom-up to enumerate the survivor's
transitive set of decommissioned agents, then counts pending items
those agents still carry (worker tasks, unacked chats, consolidations
in non-M/MD states, open clarifications, unacked broadcasts). The
count contributes to the survivor's action-item badge so the trigger
loop wakes them when legacy work lands.

Kept deliberately simple (no details here) — the detailed inbox is
served by get_my_proxy_items. This module is for trigger scoring only.
"""

from shared.db import execute


_MAX_CHAIN_DEPTH = 10


def scan(agent_id: str) -> int:
    """Return total pending items inherited by this survivor across all
    decommissioned agents in their merge chain. Zero if no proxies."""
    rows = execute(
        """WITH RECURSIVE chain AS (
             SELECT agent_id, merged_into_agent_id, 1 AS depth
             FROM agent_runs
             WHERE status = 'decommissioned'
               AND merged_into_agent_id = %s
             UNION ALL
             SELECT a.agent_id, a.merged_into_agent_id, c.depth + 1
             FROM agent_runs a
             JOIN chain c ON a.merged_into_agent_id = c.agent_id
             WHERE a.status = 'decommissioned'
               AND c.depth < %s
           )
           SELECT agent_id FROM chain""",
        (agent_id, _MAX_CHAIN_DEPTH),
    )
    if not rows:
        return 0
    proxy_ids = [r["agent_id"] for r in rows]

    # One UNION query across all item tables for the proxied-agents set.
    result = execute(
        """SELECT
             (SELECT COUNT(*) FROM tasks
              WHERE worker_agent_id = ANY(%s) AND status IN ('BW','BO')) +
             (SELECT COUNT(*) FROM communications
              WHERE to_agent = ANY(%s) AND type='chat' AND acked_at IS NULL) +
             (SELECT COUNT(*) FROM consolidations
              WHERE (agent_a_id = ANY(%s) OR agent_b_id = ANY(%s))
                AND status NOT IN ('D','F','M','MD')) +
             (SELECT COUNT(*) FROM clarifications
              WHERE (asker_agent_id = ANY(%s) OR responder_agent_id = ANY(%s))
                AND status != 'CC') AS cnt""",
        (proxy_ids, proxy_ids, proxy_ids, proxy_ids, proxy_ids, proxy_ids),
    )
    return result[0]["cnt"] if result else 0

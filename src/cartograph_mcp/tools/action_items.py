"""Action items tools — get_action_items_summary and get_action_items_detail."""

from shared.db import execute


def _count_consolidations(agent_id: str, agent_type: str) -> int:
    if agent_type == "resolver":
        rows = execute("SELECT COUNT(*) as cnt FROM consolidations WHERE status IN ('R', 'MD')")
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


def _count_tasks(agent_id: str) -> int:
    rows = execute(
        """SELECT COUNT(*) as cnt FROM tasks
           WHERE (worker_agent_id = %s AND status = 'BW')
              OR (owner_agent_id = %s AND status IN ('BO', 'WD'))""",
        (agent_id, agent_id),
    )
    return rows[0]["cnt"] if rows else 0


def _count_clarifications(agent_id: str) -> int:
    rows = execute(
        """SELECT COUNT(*) as cnt FROM clarifications
           WHERE (asker_agent_id = %s AND status IN ('B1', 'QR', 'QC'))
              OR (responder_agent_id = %s AND status = 'B2')""",
        (agent_id, agent_id),
    )
    return rows[0]["cnt"] if rows else 0


def _count_unacked_chats(agent_id: str) -> int:
    rows = execute(
        """SELECT COUNT(*) as cnt FROM communications
           WHERE to_agent = %s AND type = 'chat' AND acked_at IS NULL""",
        (agent_id,),
    )
    return rows[0]["cnt"] if rows else 0


def _count_unacked_broadcasts(agent_id: str, agent_type: str) -> int:
    # Post–Phase 2.4, broadcasts address targets via to_agent_type, not
    # to_agent. Also apply forward-only scoping (Phase 2.5): only broadcasts
    # whose is_persistent=TRUE OR were sent after this agent was created.
    rows = execute(
        """SELECT COUNT(*) as cnt FROM communications c
           WHERE c.type = 'broadcast' AND c.to_agent_type = %s
             AND (c.is_persistent OR c.created_at >
                  (SELECT created_at FROM agent_runs WHERE agent_id = %s))
             AND c.id NOT IN (
                 SELECT communication_id FROM broadcast_acks WHERE agent_id = %s
             )""",
        (agent_type, agent_id, agent_id),
    )
    return rows[0]["cnt"] if rows else 0


def get_action_items_summary(agent_id: str, agent_type: str) -> dict:
    """Quick counts of everything pending for this agent.

    Phase 7.4.5: response is now a uniform `dict[str, int]` — every
    value is a count. Pre-7.4.5 the `proxied` field was a `list[dict]`,
    which broke the MCP client's pydantic inference (it inferred
    `dict[str, int]` from the 6 int siblings, then choked on the list
    with `Input should be a valid integer [type=int_type, input_value=
    [], input_type=list]`). DEMO7 resolver hit this on every wake.

    Per-proxy breakdown (proxy_agent_id, deactivation_reason, depth,
    per-item counts) lives on `get_action_items_detail` — call it when
    proxied_count > 0 to triage which inherited identity needs
    attention.

    Returns:
      {consolidations_pending, tasks_pending, clarifications_pending,
       unacked_chats, unacked_broadcasts, terminal_pending_ack,
       proxied_count} — all ints.
    """
    # Lazy-import to avoid a circular dep (proxy imports from tools/ too).
    from cartograph_mcp.tools import proxy as proxy_tool
    proxied_groups = proxy_tool.get_my_proxy_items(agent_id).get("proxied", [])
    # Phase 7.1: terminal-pending-ack — entities I'm a participant of
    # that have closed but I haven't ack'd. Trigger scanner re-wakes
    # me until the count drops to zero.
    from trigger_management.scanners import terminal_acks as _term_scan
    terminal_pending = _term_scan.scan_terminal_pending_ack(agent_id)
    return {
        "consolidations_pending": _count_consolidations(agent_id, agent_type),
        "tasks_pending": _count_tasks(agent_id),
        "clarifications_pending": _count_clarifications(agent_id),
        "unacked_chats": _count_unacked_chats(agent_id),
        "unacked_broadcasts": _count_unacked_broadcasts(agent_id, agent_type),
        "terminal_pending_ack": len(terminal_pending),
        "proxied_count": len(proxied_groups),
    }


def _get_consolidation_details(agent_id: str, agent_type: str) -> list[dict]:
    if agent_type == "resolver":
        return execute("SELECT * FROM consolidations WHERE status IN ('R', 'MD')")
    elif agent_type == "sme":
        return execute(
            """SELECT * FROM consolidations
               WHERE (agent_b_id = %s AND status = 'B2')
                  OR (agent_a_id = %s AND status = 'B1')
                  OR (mutation_assigned_to = %s AND status = 'M')""",
            (agent_id, agent_id, agent_id),
        )
    return []


def _get_task_details(agent_id: str) -> list[dict]:
    return execute(
        """SELECT * FROM tasks
           WHERE (worker_agent_id = %s AND status = 'BW')
              OR (owner_agent_id = %s AND status IN ('BO', 'WD'))""",
        (agent_id, agent_id),
    )


def _get_clarification_details(agent_id: str) -> list[dict]:
    return execute(
        """SELECT * FROM clarifications
           WHERE (asker_agent_id = %s AND status IN ('B1', 'QR', 'QC'))
              OR (responder_agent_id = %s AND status = 'B2')""",
        (agent_id, agent_id),
    )


def _get_unacked_chat_details(agent_id: str) -> list[dict]:
    return execute(
        """SELECT * FROM communications
           WHERE to_agent = %s AND type = 'chat' AND acked_at IS NULL
           ORDER BY created_at""",
        (agent_id,),
    )


def _get_unacked_broadcast_details(agent_id: str, agent_type: str) -> list[dict]:
    return execute(
        """SELECT c.* FROM communications c
           WHERE c.type = 'broadcast' AND c.to_agent_type = %s
             AND (c.is_persistent OR c.created_at >
                  (SELECT created_at FROM agent_runs WHERE agent_id = %s))
             AND c.id NOT IN (
                 SELECT communication_id FROM broadcast_acks WHERE agent_id = %s
             )
           ORDER BY c.created_at""",
        (agent_type, agent_id, agent_id),
    )


def get_action_items_detail(agent_id: str, agent_type: str) -> dict:
    """Full rows for every pending item across all categories.

    Phase 4: response has two separate buckets — `my` (survivor's own
    pending work) and `proxied` (work inherited from decommissioned
    agents via merge-chain). Never interleaved — that matches the
    "this is me vs legacy to wind down" mental model.
    """
    from cartograph_mcp.tools import proxy as proxy_tool
    from trigger_management.scanners import terminal_acks as _term_scan
    proxied = proxy_tool.get_my_proxy_items(agent_id).get("proxied", [])
    return {
        "my": {
            "consolidations": _get_consolidation_details(agent_id, agent_type),
            "tasks": _get_task_details(agent_id),
            "clarifications": _get_clarification_details(agent_id),
            "chats": _get_unacked_chat_details(agent_id),
            "broadcasts": _get_unacked_broadcast_details(agent_id, agent_type),
            # Phase 7.1: terminal entities awaiting my explicit ack.
            "terminal_pending_ack": _term_scan.scan_terminal_pending_ack(agent_id),
        },
        "proxied": proxied,
    }

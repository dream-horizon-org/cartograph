"""Lightweight notification lookup for the per-agent PostToolUse hook.

Returns a compact count+breakdown of unacked high-priority messages
(chats + broadcasts from specified source types, since an optional
timestamp). Designed to be cheap enough to call on every tool completion.

Tasks are intentionally NOT included — they're persistent action items
surfaced by get_action_items_summary on natural wake-up; the hook is
for async real-time channels that would otherwise wait until the next
invocation.
"""

from __future__ import annotations

from shared.actor_auth import require_active_agent
from shared.db import execute, execute_one


_KNOWN_SOURCE_TYPES = {"admin", "orchestrator", "iterator", "sme", "resolver"}


def get_agent_notifications(
    agent_id: str,
    priority_from_agent_types: list[str] | None = None,
    since: str | None = None,
) -> dict:
    """Return compact high-priority notifications since a timestamp.

    Args:
        agent_id: the asking agent
        priority_from_agent_types: list of source types to count. 'admin'
            matches from_agent='admin' (the literal pseudo-agent).
            Others match agent_runs.agent_type. Defaults to ['admin'].
        since: ISO timestamp (e.g. '2026-04-21T12:34:56+00:00'); only
            count rows with created_at > since. None = all unacked.

    Returns: {
        high_priority_count: N,
        breakdown: [{from, type, count}, ...],
        max_seen_at: ISO timestamp — pass back as `since` on next call
                     to get only newer items, or None if nothing matched
    }
    """
    me = require_active_agent(agent_id)

    types = priority_from_agent_types or ["admin"]
    invalid = [t for t in types if t not in _KNOWN_SOURCE_TYPES]
    if invalid:
        raise ValueError(
            f"Invalid priority_from_agent_types: {invalid}. "
            f"Valid: {sorted(_KNOWN_SOURCE_TYPES)}"
        )

    # Resolve priority sources to concrete from_agent values.
    priority_from: set[str] = set()
    if "admin" in types:
        priority_from.add("admin")
    non_admin = [t for t in types if t != "admin"]
    if non_admin:
        rows = execute(
            "SELECT agent_id FROM agent_runs WHERE agent_type = ANY(%s) "
            "AND status != 'decommissioned'",
            (non_admin,),
        )
        priority_from.update(r["agent_id"] for r in rows)

    if not priority_from:
        return {"high_priority_count": 0, "breakdown": [], "max_seen_at": None}

    priority_list = list(priority_from)

    # Chats: unacked, addressed to me, from a priority source.
    if since:
        chat_rows = execute(
            """SELECT from_agent, COUNT(*) AS cnt, MAX(created_at) AS latest
               FROM communications
               WHERE to_agent = %s AND type = 'chat' AND acked_at IS NULL
                 AND from_agent = ANY(%s) AND created_at > %s
               GROUP BY from_agent""",
            (agent_id, priority_list, since),
        )
    else:
        chat_rows = execute(
            """SELECT from_agent, COUNT(*) AS cnt, MAX(created_at) AS latest
               FROM communications
               WHERE to_agent = %s AND type = 'chat' AND acked_at IS NULL
                 AND from_agent = ANY(%s)
               GROUP BY from_agent""",
            (agent_id, priority_list),
        )

    # Broadcasts: to my agent_type, from a priority source, not acked by
    # me, and (forward-only scoping) either persistent OR created AFTER
    # this agent was spawned.
    spawn_cutoff_clause = (
        "AND (c.is_persistent OR c.created_at > "
        "(SELECT created_at FROM agent_runs WHERE agent_id = %s))"
    )
    if since:
        bcast_rows = execute(
            f"""SELECT c.from_agent, COUNT(*) AS cnt, MAX(c.created_at) AS latest
               FROM communications c
               WHERE c.type = 'broadcast' AND c.to_agent_type = %s
                 AND c.from_agent = ANY(%s) AND c.created_at > %s
                 {spawn_cutoff_clause}
                 AND NOT EXISTS (
                   SELECT 1 FROM broadcast_acks ba
                   WHERE ba.communication_id = c.id AND ba.agent_id = %s
                 )
               GROUP BY c.from_agent""",
            (me["agent_type"], priority_list, since, agent_id, agent_id),
        )
    else:
        bcast_rows = execute(
            f"""SELECT c.from_agent, COUNT(*) AS cnt, MAX(c.created_at) AS latest
               FROM communications c
               WHERE c.type = 'broadcast' AND c.to_agent_type = %s
                 AND c.from_agent = ANY(%s)
                 {spawn_cutoff_clause}
                 AND NOT EXISTS (
                   SELECT 1 FROM broadcast_acks ba
                   WHERE ba.communication_id = c.id AND ba.agent_id = %s
                 )
               GROUP BY c.from_agent""",
            (me["agent_type"], priority_list, agent_id, agent_id),
        )

    breakdown: list[dict] = []
    total = 0
    latest_seen: str | None = None

    def _track_latest(ts) -> None:
        nonlocal latest_seen
        if ts is None:
            return
        iso = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
        if latest_seen is None or iso > latest_seen:
            latest_seen = iso

    for r in chat_rows:
        breakdown.append({"from": r["from_agent"], "type": "chat", "count": r["cnt"]})
        total += r["cnt"]
        _track_latest(r["latest"])

    for r in bcast_rows:
        breakdown.append({"from": r["from_agent"], "type": "broadcast", "count": r["cnt"]})
        total += r["cnt"]
        _track_latest(r["latest"])

    return {
        "high_priority_count": total,
        "breakdown": breakdown,
        "max_seen_at": latest_seen,
    }

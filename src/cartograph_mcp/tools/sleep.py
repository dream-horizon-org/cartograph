"""Sleep / wake tools — don't wake an agent that has nothing useful to do.

Agents waiting on external events (admin input, another agent's response,
a deploy window, etc.) can put themselves to sleep so the trigger scanner
stops picking them up until a wall-clock deadline.

Admin + orchestrator also get bulk sleep/wake tools for cohort control
(e.g. "pause everything while I debug", "resume all SMEs now that creds
are fixed").

Interrupt semantics:
- Admin chat to a sleeping agent → auto-wakes it (see chat.send_chat).
- bulk_wake_agents → explicit wake, clears sleep_until.
- Broadcasts / tasks / orchestrator-to-agent chats do NOT interrupt sleep.
- Sleep is advisory only — recovery scanner still fires for errored
  agents, heartbeat watchdog still fires for stale running agents.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from shared.db import execute, execute_one, execute_mutate


_VALID_AGENT_TYPES = {"orchestrator", "iterator", "sme", "resolver"}


def _caller(agent_id: str) -> dict:
    row = execute_one(
        "SELECT agent_id, agent_type FROM agent_runs "
        "WHERE agent_id = %s AND status != 'decommissioned'",
        (agent_id,),
    )
    if row is None:
        raise ValueError(f"Agent {agent_id} not found")
    return row


def _assert_orch_or_admin(agent_id: str) -> None:
    if agent_id == "admin":
        return
    caller = _caller(agent_id)
    if caller["agent_type"] != "orchestrator":
        raise ValueError(
            f"Only orchestrator or admin can sleep/wake agents in bulk. "
            f"{agent_id} is type '{caller['agent_type']}'."
        )


def sleep_self(agent_id: str, duration_seconds: int, reason: str) -> dict:
    """Put THIS agent to sleep for `duration_seconds`. Any active agent may call.

    Returns {agent_id, sleep_until} so the caller knows the wake time.
    """
    if not reason or not reason.strip():
        raise ValueError("reason is required (audit trail)")
    if duration_seconds <= 0:
        raise ValueError("duration_seconds must be positive")
    if duration_seconds > 86400 * 7:
        raise ValueError("duration_seconds cannot exceed 7 days (604800)")
    _caller(agent_id)  # verify agent exists + not decommissioned

    sleep_until = datetime.now(tz=timezone.utc) + timedelta(seconds=duration_seconds)
    execute_mutate(
        """UPDATE agent_runs SET sleep_until = %s, updated_at = now()
           WHERE agent_id = %s""",
        (sleep_until, agent_id),
    )
    return {
        "agent_id": agent_id,
        "sleep_until": sleep_until.isoformat(),
        "reason": reason,
    }


def bulk_sleep_agents(
    agent_id: str,
    until: str,
    reason: str,
    agent_ids: list[str] | None = None,
    agent_type: str | None = None,
) -> dict:
    """Put a cohort to sleep until an absolute ISO timestamp. ORCH/ADMIN only.

    At least one of agent_ids or agent_type required (refuses blank-wipe).
    Never sleeps the caller even if included.
    Refuses agent_type='orchestrator' (never put the singleton coordinator
    to sleep via bulk).
    """
    if not reason or not reason.strip():
        raise ValueError("reason is required (audit trail)")
    if not until or not until.strip():
        raise ValueError("until is required (ISO timestamp)")
    try:
        until_ts = datetime.fromisoformat(until.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"until must be a valid ISO-8601 timestamp: {exc}") from None
    if until_ts <= datetime.now(tz=timezone.utc):
        raise ValueError("until must be in the future")
    if not agent_ids and not agent_type:
        raise ValueError(
            "refuse blank-wipe — provide at least one of agent_ids or agent_type"
        )
    if agent_type == "orchestrator":
        raise ValueError("refusing to sleep orchestrator via bulk tool")
    if agent_type is not None and agent_type not in _VALID_AGENT_TYPES:
        raise ValueError(f"Invalid agent_type '{agent_type}'")

    _assert_orch_or_admin(agent_id)

    where = ["status != 'decommissioned'", "agent_id != %s"]
    params: list = [agent_id]
    if agent_ids:
        where.append("agent_id = ANY(%s)")
        params.append(agent_ids)
    if agent_type:
        where.append("agent_type = %s")
        params.append(agent_type)
    where_sql = " AND ".join(where)

    rows = execute(
        f"""UPDATE agent_runs SET sleep_until = %s, updated_at = now()
            WHERE {where_sql}
            RETURNING agent_id""",
        [until_ts] + params,
    )
    return {
        "slept": [r["agent_id"] for r in rows],
        "count": len(rows),
        "sleep_until": until_ts.isoformat(),
        "reason": reason,
    }


def bulk_wake_agents(
    agent_id: str,
    agent_ids: list[str] | None = None,
    agent_type: str | None = None,
) -> dict:
    """Clear sleep_until for a cohort. ORCH/ADMIN only.

    At least one of agent_ids or agent_type required.
    """
    if not agent_ids and not agent_type:
        raise ValueError(
            "refuse blank-wipe — provide at least one of agent_ids or agent_type"
        )
    if agent_type is not None and agent_type not in _VALID_AGENT_TYPES:
        raise ValueError(f"Invalid agent_type '{agent_type}'")

    _assert_orch_or_admin(agent_id)

    where = ["sleep_until IS NOT NULL"]
    params: list = []
    if agent_ids:
        where.append("agent_id = ANY(%s)")
        params.append(agent_ids)
    if agent_type:
        where.append("agent_type = %s")
        params.append(agent_type)
    where_sql = " AND ".join(where)

    rows = execute(
        f"""UPDATE agent_runs SET sleep_until = NULL, updated_at = now()
            WHERE {where_sql}
            RETURNING agent_id""",
        params,
    )
    return {"woken": [r["agent_id"] for r in rows], "count": len(rows)}

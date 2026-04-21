"""PostgreSQL data layer for agent runtime.

Uses shared DB pool from src/shared/db.py. All agent management SQL is here.
The trigger_queue table has been removed — trigger management now uses
agent_runs.trigger_lock + agent_runs.status as the queue.
"""

from shared.db import execute, execute_one, execute_mutate, execute_returning


def create_agent_run(
    agent_id: str,
    agent_type: str,
    workspace_path: str,
    plane: str | None = None,
) -> None:
    """Insert a new agent_runs row. SME-to-resource assignment is NOT stored
    here — it lives in resource_component_agents (see bulk_spawn_smes)."""
    execute_mutate(
        """INSERT INTO agent_runs
           (agent_id, agent_type, workspace_path, plane)
           VALUES (%s, %s, %s, %s)""",
        (agent_id, agent_type, workspace_path, plane),
    )


def get_sme_resource_id(agent_id: str) -> str | None:
    """Look up the SME's assigned resource from RCA (single source of truth).

    Returns None if no assignment exists (shouldn't happen for a spawned SME
    but we handle it gracefully for edge cases during recovery).
    """
    row = execute_one(
        "SELECT resource_id FROM resource_component_agents WHERE agent_id = %s LIMIT 1",
        (agent_id,),
    )
    return str(row["resource_id"]) if row else None


def get_agent(agent_id: str) -> dict | None:
    return execute_one(
        "SELECT * FROM agent_runs WHERE agent_id = %s",
        (agent_id,),
    )


def update_agent_status(agent_id: str, status: str) -> None:
    execute_mutate(
        "UPDATE agent_runs SET status = %s, updated_at = now() WHERE agent_id = %s",
        (status, agent_id),
    )


def set_agent_errored(agent_id: str, error_msg: str) -> None:
    """Mark agent errored and persist a diagnostic message. Truncates to 8KB.

    Stamps errored_at so the recovery scanner can compute backoff.
    Leaves recovery_attempts alone — the scanner increments it on each retry.
    """
    execute_mutate(
        """UPDATE agent_runs
           SET status = 'errored',
               error_msg = %s,
               errored_at = now(),
               trigger_lock = FALSE,
               updated_at = now()
           WHERE agent_id = %s""",
        (error_msg[:8000], agent_id),
    )


# Backoff ladder for automatic recovery of errored agents.
# Index = recovery_attempts already made. After 3 strikes we give up and
# leave the agent errored for human triage (visible in admin UI).
_RECOVERY_BACKOFF_SECONDS = [60, 300, 1800]
MAX_RECOVERY_ATTEMPTS = len(_RECOVERY_BACKOFF_SECONDS)


def get_recoverable_errored_agents() -> list[dict]:
    """Return errored agents whose backoff has elapsed and still have retries.

    Scanner uses this to decide who to flip back to idle. Backoff is computed
    per-agent based on recovery_attempts so far.
    """
    # Build a CASE expression matching the backoff ladder above.
    when_clauses = "\n               ".join(
        f"WHEN {i} THEN INTERVAL '{sec} seconds'"
        for i, sec in enumerate(_RECOVERY_BACKOFF_SECONDS)
    )
    return execute(
        f"""SELECT * FROM agent_runs
            WHERE status = 'errored'
              AND recovery_attempts < {MAX_RECOVERY_ATTEMPTS}
              AND errored_at IS NOT NULL
              AND now() - errored_at >= (CASE recovery_attempts
               {when_clauses}
               END)"""
    )


def reset_agent_for_recovery(agent_id: str) -> bool:
    """Flip an errored agent back to idle for another try. Returns True if updated.

    Increments recovery_attempts. Keeps error_msg for history (debugging).
    Clears errored_at + trigger_lock so normal flow re-engages.
    Gated on status='errored' to prevent double-flips in a race.
    """
    row = execute_returning(
        """UPDATE agent_runs
           SET status = 'idle',
               errored_at = NULL,
               trigger_lock = FALSE,
               recovery_attempts = recovery_attempts + 1,
               updated_at = now()
           WHERE agent_id = %s AND status = 'errored'
           RETURNING agent_id""",
        (agent_id,),
    )
    return row is not None


def clear_recovery_state(agent_id: str) -> None:
    """Reset recovery counter after a successful invocation.

    Called when an agent transitions back to idle from running — it means
    the retry worked and past errors shouldn't count against it anymore.
    """
    execute_mutate(
        """UPDATE agent_runs
           SET recovery_attempts = 0, error_msg = NULL, updated_at = now()
           WHERE agent_id = %s AND recovery_attempts > 0""",
        (agent_id,),
    )


def force_reset_agent(agent_id: str) -> bool:
    """Admin/orchestrator override: reset an errored agent regardless of
    recovery_attempts cap. Clears error state entirely. Returns True on update.
    """
    row = execute_returning(
        """UPDATE agent_runs
           SET status = 'idle',
               errored_at = NULL,
               trigger_lock = FALSE,
               recovery_attempts = 0,
               error_msg = NULL,
               updated_at = now()
           WHERE agent_id = %s AND status = 'errored'
           RETURNING agent_id""",
        (agent_id,),
    )
    return row is not None


def update_agent_session(agent_id: str, session_id: str) -> None:
    execute_mutate(
        "UPDATE agent_runs SET session_id = %s, updated_at = now() WHERE agent_id = %s",
        (session_id, agent_id),
    )


def update_agent_heartbeat(agent_id: str) -> None:
    execute_mutate(
        "UPDATE agent_runs SET heartbeat = now(), updated_at = now() WHERE agent_id = %s",
        (agent_id,),
    )


def increment_invocation_count(agent_id: str) -> None:
    execute_mutate(
        "UPDATE agent_runs SET invocation_count = invocation_count + 1, updated_at = now() WHERE agent_id = %s",
        (agent_id,),
    )


def pickup_agent(agent_id: str) -> bool:
    """Atomically pick up a locked agent for invocation.

    Transitions: trigger_lock=TRUE → status='running', trigger_lock=FALSE,
    invocation_count+=1, heartbeat=now().

    Returns True if pickup succeeded (agent was locked), False otherwise.
    """
    row = execute_returning(
        """UPDATE agent_runs
           SET status = 'running',
               trigger_lock = FALSE,
               invocation_count = invocation_count + 1,
               heartbeat = now(),
               updated_at = now()
           WHERE agent_id = %s AND trigger_lock = TRUE
           RETURNING agent_id""",
        (agent_id,),
    )
    return row is not None


def get_locked_agents() -> list[dict]:
    """Get all agents with trigger_lock=TRUE, ordered by priority.

    Priority: orchestrator > resolver > sme > iterator.
    Within same type: lower invocation_count first (spread work evenly).

    DEPRECATED for dispatch — use pickup_next_locked_agent_of_type instead
    (atomic per-worker pickup, no snapshot staleness). This function is kept
    for diagnostic / admin-UI views.
    """
    return execute(
        """SELECT * FROM agent_runs
           WHERE trigger_lock = TRUE
           ORDER BY
             CASE agent_type
               WHEN 'orchestrator' THEN 0
               WHEN 'resolver' THEN 1
               WHEN 'sme' THEN 2
               WHEN 'iterator' THEN 3
               ELSE 99
             END,
             invocation_count ASC"""
    )


def pickup_next_locked_agent_of_type(agent_type: str) -> dict | None:
    """Atomically claim the next highest-priority locked agent of one type.

    One SQL statement: SELECT ... FOR UPDATE SKIP LOCKED inside an UPDATE,
    so concurrent workers never fight over the same row and the priority
    ordering is re-evaluated on every call (no stale snapshot).

    Priority within type: lower invocation_count first (fair spread).

    Transitions the claimed row:
      trigger_lock=TRUE → status='running', trigger_lock=FALSE,
      invocation_count += 1, heartbeat=now().

    Returns the claimed row, or None if no lockable agent of this type exists.
    """
    return execute_returning(
        """UPDATE agent_runs
           SET status = 'running',
               trigger_lock = FALSE,
               invocation_count = invocation_count + 1,
               heartbeat = now(),
               updated_at = now()
           WHERE agent_id = (
             SELECT agent_id FROM agent_runs
             WHERE agent_type = %s AND trigger_lock = TRUE
             ORDER BY invocation_count ASC
             LIMIT 1
             FOR UPDATE SKIP LOCKED
           )
           RETURNING *""",
        (agent_type,),
    )


def release_trigger_lock(agent_id: str) -> None:
    """Release lock without picking up (e.g., on error before invocation)."""
    execute_mutate(
        "UPDATE agent_runs SET trigger_lock = FALSE, updated_at = now() WHERE agent_id = %s",
        (agent_id,),
    )


def agent_type_exists(agent_type: str) -> bool:
    """Check if a non-decommissioned agent of this type exists."""
    row = execute_one(
        "SELECT 1 FROM agent_runs WHERE agent_type = %s AND status != 'decommissioned' LIMIT 1",
        (agent_type,),
    )
    return row is not None


def get_stale_running_agents(timeout_seconds: int) -> list[dict]:
    """Get agents in 'running' state whose heartbeat is older than timeout."""
    return execute(
        """SELECT * FROM agent_runs
           WHERE status = 'running'
             AND heartbeat < now() - make_interval(secs => %s)""",
        (timeout_seconds,),
    )

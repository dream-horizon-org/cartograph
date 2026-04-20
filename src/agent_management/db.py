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
    resource_id: str | None = None,
) -> None:
    execute_mutate(
        """INSERT INTO agent_runs
           (agent_id, agent_type, workspace_path, plane, resource_id)
           VALUES (%s, %s, %s, %s, %s)""",
        (agent_id, agent_type, workspace_path, plane, resource_id),
    )


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

"""Task tools — create, respond (with state machine validation), raise_blocker, read.

State machine for tasks:
  BW (Blocked-on-Worker) — initial state when task is created
    → BO (raise blocker — owner's turn to unblock)
    → WD (worker completes the task)
  BO (Blocked-on-Owner) — owner's turn
    → BW (owner resolves blocker, back to worker)
    → TC (owner closes task directly)
  WD (Worker Done) — owner's turn to review
    → BW (owner rejects, back to worker)
    → TC (owner accepts — task completed)
  TC (Task Completed) — terminal

Every state change MUST go through respond_task (or the raise_blocker shortcut).
Agents cannot close tasks without a state change — prevents silent completion.
"""

from shared.db import execute, execute_one, execute_returning, execute_mutate


# Who can trigger which transitions
_WORKER_TRANSITIONS = {
    "BW": {"BO", "WD"},
    "BO": set(),  # worker cannot move out of BO — owner must resolve
    "WD": set(),  # worker cannot move out of WD — owner must accept or reject
    "TC": set(),  # terminal
}

_OWNER_TRANSITIONS = {
    "BW": set(),  # owner does not act in BW — worker's turn
    "BO": {"BW", "TC"},
    "WD": {"BW", "TC"},
    "TC": set(),  # terminal
}


def create_task(owner_agent_id: str, worker_agent_id: str, description: str) -> dict:
    """Create a new task (status=BW) + communication to announce it.

    Validates:
      - owner is orchestrator OR 'admin'
      - worker exists and is not decommissioned
      - worker != owner (cannot assign to self)
    """
    if not description.strip():
        raise ValueError("description cannot be empty")

    # Owner must be orchestrator or 'admin'
    if owner_agent_id != "admin":
        owner = execute_one(
            "SELECT agent_type FROM agent_runs WHERE agent_id = %s AND status != 'decommissioned'",
            (owner_agent_id,),
        )
        if owner is None:
            raise ValueError(f"Owner agent {owner_agent_id} not found")
        if owner["agent_type"] != "orchestrator":
            raise ValueError(
                f"Only orchestrator or admin can create tasks. "
                f"{owner_agent_id} is of type '{owner['agent_type']}'."
            )

    # Worker must exist
    worker = execute_one(
        "SELECT agent_id FROM agent_runs WHERE agent_id = %s AND status != 'decommissioned'",
        (worker_agent_id,),
    )
    if worker is None:
        raise ValueError(f"Worker agent {worker_agent_id} not found")

    if worker_agent_id == owner_agent_id:
        raise ValueError("worker_agent_id must differ from owner_agent_id")

    task = execute_returning(
        """INSERT INTO tasks (owner_agent_id, worker_agent_id, description, status)
           VALUES (%s, %s, %s, 'BW')
           RETURNING *""",
        (owner_agent_id, worker_agent_id, description),
    )

    # Announce the task via communications (so it shows in threads)
    execute_mutate(
        """INSERT INTO communications (from_agent, to_agent, type, source_id, text)
           VALUES (%s, %s, 'task', %s, %s)""",
        (owner_agent_id, worker_agent_id, task["id"], description),
    )

    return task


def _is_owner(agent_id: str, task: dict) -> bool:
    return task["owner_agent_id"] == agent_id


def _is_worker(agent_id: str, task: dict) -> bool:
    return task["worker_agent_id"] == agent_id


def respond_task(
    agent_id: str,
    task_id: str,
    message: str,
    new_status: str,
    blocker_detail: str | None = None,
) -> dict:
    """Respond to a task with a state transition + message.

    Validates:
      - task exists
      - agent is owner or worker
      - new_status is a legal transition from current status for this role
      - message is non-empty
    """
    if not message.strip():
        raise ValueError("message cannot be empty")

    task = execute_one("SELECT * FROM tasks WHERE id = %s", (task_id,))
    if task is None:
        raise ValueError(f"Task {task_id} not found")

    is_owner = _is_owner(agent_id, task)
    is_worker = _is_worker(agent_id, task)
    if not (is_owner or is_worker):
        raise ValueError(
            f"Agent {agent_id} is neither owner ({task['owner_agent_id']}) "
            f"nor worker ({task['worker_agent_id']}) of task {task_id}"
        )

    current = task["status"]
    if is_worker:
        allowed = _WORKER_TRANSITIONS.get(current, set())
    else:
        allowed = _OWNER_TRANSITIONS.get(current, set())

    if new_status not in allowed:
        role = "worker" if is_worker else "owner"
        raise ValueError(
            f"Invalid transition {current} → {new_status} for {role}. "
            f"Allowed: {sorted(allowed) if allowed else 'none (terminal or not your turn)'}"
        )

    # Update task status and optional blocker_detail
    if new_status == "BO" and blocker_detail:
        updated = execute_returning(
            """UPDATE tasks
               SET status = %s, blocker_detail = %s, updated_at = now()
               WHERE id = %s
               RETURNING *""",
            (new_status, blocker_detail, task_id),
        )
    elif new_status == "BW" and is_owner:
        # Owner resolving a blocker — clear blocker_detail
        updated = execute_returning(
            """UPDATE tasks
               SET status = %s, blocker_detail = NULL, updated_at = now()
               WHERE id = %s
               RETURNING *""",
            (new_status, task_id),
        )
    else:
        updated = execute_returning(
            """UPDATE tasks
               SET status = %s, updated_at = now()
               WHERE id = %s
               RETURNING *""",
            (new_status, task_id),
        )

    # Announce in communications with structured state_transition metadata
    # (admin UI filters + state history). from_agent = caller, to_agent = the
    # other party on this task.
    import json as _json
    other = task["worker_agent_id"] if is_owner else task["owner_agent_id"]
    metadata = {
        "state_transition": {"from": current, "to": new_status},
        "role": "worker" if is_worker else "owner",
    }
    if new_status == "BO" and blocker_detail:
        metadata["blocker_detail"] = blocker_detail
    # Phase 7.1: REVERTED Phase 5.5 auto-ack. Terminal announcement comm
    # rows now land unacked; the trigger scanner picks them up via the
    # terminal_acks table and re-wakes participants until they call
    # ack_terminal explicitly.
    execute_mutate(
        """INSERT INTO communications
              (from_agent, to_agent, type, source_id, text, metadata)
           VALUES (%s, %s, 'task', %s, %s, %s::jsonb)""",
        (agent_id, other, task_id, message, _json.dumps(metadata)),
    )

    return updated


def raise_blocker(agent_id: str, task_id: str, blocker_detail: str) -> dict:
    """Shortcut: worker raises a blocker on a task (BW → BO).

    Equivalent to respond_task(status='BO') but with a fixed message format.
    """
    if not blocker_detail.strip():
        raise ValueError("blocker_detail cannot be empty")
    return respond_task(
        agent_id=agent_id,
        task_id=task_id,
        message=f"Blocked: {blocker_detail}",
        new_status="BO",
        blocker_detail=blocker_detail,
    )


def get_my_tasks(agent_id: str) -> list[dict]:
    """Get all non-terminal tasks where agent is owner or worker."""
    return execute(
        """SELECT * FROM tasks
           WHERE (owner_agent_id = %s OR worker_agent_id = %s)
             AND status != 'TC'
           ORDER BY updated_at DESC""",
        (agent_id, agent_id),
    )


def get_task_thread(
    task_id: str, agent_id: str, page: int = 1, limit: int = 50
) -> list[dict]:
    """Get paginated communications for a task.

    Scoped: only owner or worker of the task can read the thread.
    """
    task = execute_one("SELECT * FROM tasks WHERE id = %s", (task_id,))
    if task is None:
        raise ValueError(f"Task {task_id} not found")
    if not (_is_owner(agent_id, task) or _is_worker(agent_id, task)):
        raise ValueError(
            f"Agent {agent_id} is not a participant in task {task_id}"
        )

    offset = (page - 1) * limit
    return execute(
        """SELECT * FROM communications
           WHERE type = 'task' AND source_id = %s
           ORDER BY created_at
           LIMIT %s OFFSET %s""",
        (task_id, limit, offset),
    )

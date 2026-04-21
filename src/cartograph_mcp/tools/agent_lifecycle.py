"""Agent lifecycle tools — decommission agents + components.

Decommissioning is orchestrator-controlled. Each entity has its own atomic
tool (plus a bulk variant) so teardown cascades are explicit, not magic.

Resource cascade on agent decommission:
  - 'leave':  orphan the RCA row (rare — debugging only).
  - 'reset':  delete the RCA row + flip resource back to 'pending' so it
              can be re-spawned for another SME.
  - 'reject': delete the RCA row + mark resource 'rejected' with audit.
"""

from shared.db import execute, execute_one, execute_mutate, execute_returning


_VALID_RESOURCE_ACTIONS = {"leave", "reset", "reject"}


def _assert_orchestrator(agent_id: str) -> None:
    caller = execute_one(
        "SELECT agent_type FROM agent_runs WHERE agent_id = %s AND status != 'decommissioned'",
        (agent_id,),
    )
    if caller is None:
        raise ValueError(f"Agent {agent_id} not found")
    if caller["agent_type"] != "orchestrator":
        raise ValueError(
            f"Only orchestrator can decommission agents/components. "
            f"{agent_id} is of type '{caller['agent_type']}'."
        )


def _apply_resource_cascade(
    target_agent_id: str,
    resource_action: str,
    reason: str,
    actor_id: str,
) -> dict:
    """Apply the resource cascade for one decommissioned agent. Returns {reset: [...], rejected: [...], orphaned: [...]}."""
    result = {"reset": [], "rejected": [], "orphaned": []}
    if resource_action == "leave":
        # Keep RCA rows intact. Resource status untouched.
        rows = execute(
            "SELECT resource_id FROM resource_component_agents WHERE agent_id = %s",
            (target_agent_id,),
        )
        result["orphaned"] = [str(r["resource_id"]) for r in rows]
        return result

    rca_rows = execute(
        "SELECT resource_id FROM resource_component_agents WHERE agent_id = %s",
        (target_agent_id,),
    )
    resource_ids = [str(r["resource_id"]) for r in rca_rows]

    # Drop the RCA rows first (removes the assignment)
    execute_mutate(
        "DELETE FROM resource_component_agents WHERE agent_id = %s",
        (target_agent_id,),
    )

    if not resource_ids:
        return result

    if resource_action == "reset":
        # Resources with no OTHER RCA rows → back to 'pending' for re-spawn.
        # Resources still owned by other agents (e.g. after split) → leave alone.
        for rid in resource_ids:
            still_owned = execute_one(
                "SELECT 1 FROM resource_component_agents WHERE resource_id = %s LIMIT 1",
                (rid,),
            )
            if still_owned is None:
                execute_mutate(
                    "UPDATE resources SET status = 'pending' WHERE id = %s AND status = 'assigned'",
                    (rid,),
                )
                result["reset"].append(rid)
    elif resource_action == "reject":
        for rid in resource_ids:
            still_owned = execute_one(
                "SELECT 1 FROM resource_component_agents WHERE resource_id = %s LIMIT 1",
                (rid,),
            )
            if still_owned is None:
                execute_mutate(
                    """UPDATE resources
                       SET status = 'rejected', rejected_at = now(),
                           rejected_by = %s, rejected_reason = %s
                       WHERE id = %s AND status IN ('pending','assigned')""",
                    (actor_id, f"decommission cascade: {reason}", rid),
                )
                result["rejected"].append(rid)
    return result


def decommission_agent(
    agent_id: str,
    target_agent_id: str,
    reason: str,
    resource_action: str = "leave",
) -> dict:
    """Flip target agent to 'decommissioned' + optionally cascade to its resources.

    Orchestrator-only. Refuses if target_agent_id IS the caller (can't self-decom).
    See module docstring for resource_action semantics.
    """
    if not reason or not reason.strip():
        raise ValueError("reason is required (audit trail)")
    if resource_action not in _VALID_RESOURCE_ACTIONS:
        raise ValueError(
            f"Invalid resource_action '{resource_action}'. "
            f"Valid: {sorted(_VALID_RESOURCE_ACTIONS)}"
        )
    _assert_orchestrator(agent_id)
    if target_agent_id == agent_id:
        raise ValueError("orchestrator cannot decommission itself")

    target = execute_one(
        "SELECT agent_type, status FROM agent_runs WHERE agent_id = %s",
        (target_agent_id,),
    )
    if target is None:
        raise ValueError(f"Target agent {target_agent_id} not found")
    if target["status"] == "decommissioned":
        raise ValueError(f"Target agent {target_agent_id} already decommissioned")

    cascade = _apply_resource_cascade(
        target_agent_id, resource_action, reason, agent_id
    )

    # Flip status + clear lock. Keep error_msg if present (history).
    execute_mutate(
        """UPDATE agent_runs
           SET status = 'decommissioned',
               trigger_lock = FALSE,
               updated_at = now()
           WHERE agent_id = %s""",
        (target_agent_id,),
    )

    return {
        "decommissioned": target_agent_id,
        "resource_action": resource_action,
        "cascade": cascade,
    }


def decommission_agents_bulk(
    agent_id: str,
    reason: str,
    agent_ids: list[str] | None = None,
    agent_type: str | None = None,
    resource_action: str = "leave",
) -> dict:
    """Plane-wide / cohort-wide teardown. At least one of agent_ids or agent_type
    must be provided (refuses blank-wipe).
    """
    if not reason or not reason.strip():
        raise ValueError("reason is required (audit trail)")
    if resource_action not in _VALID_RESOURCE_ACTIONS:
        raise ValueError(f"Invalid resource_action '{resource_action}'")
    if not agent_ids and not agent_type:
        raise ValueError(
            "refuse blank-wipe — provide at least one of agent_ids or agent_type"
        )
    if agent_type == "orchestrator":
        raise ValueError("refusing to decommission orchestrator via bulk tool")

    _assert_orchestrator(agent_id)

    where_clauses = ["status != 'decommissioned'", "agent_id != %s"]
    params: list = [agent_id]  # never decom self
    if agent_ids:
        where_clauses.append("agent_id = ANY(%s)")
        params.append(agent_ids)
    if agent_type:
        where_clauses.append("agent_type = %s")
        params.append(agent_type)
    where_sql = " AND ".join(where_clauses)

    targets = execute(
        f"SELECT agent_id FROM agent_runs WHERE {where_sql}",
        params,
    )

    decommissioned: list[str] = []
    cascade_aggregate = {"reset": [], "rejected": [], "orphaned": []}
    for t in targets:
        tid = t["agent_id"]
        cascade = _apply_resource_cascade(tid, resource_action, reason, agent_id)
        execute_mutate(
            """UPDATE agent_runs
               SET status = 'decommissioned',
                   trigger_lock = FALSE,
                   updated_at = now()
               WHERE agent_id = %s""",
            (tid,),
        )
        decommissioned.append(tid)
        for k in cascade_aggregate:
            cascade_aggregate[k].extend(cascade[k])

    return {
        "decommissioned": decommissioned,
        "count": len(decommissioned),
        "resource_action": resource_action,
        "cascade": cascade_aggregate,
    }


def decommission_component(agent_id: str, component_id: str, reason: str) -> dict:
    """Soft-delete a component by flipping its status to 'decommissioned'.

    Attributions and edges remain as-is (soft-death mirrors merge semantics).
    Orchestrator-only.
    """
    if not reason or not reason.strip():
        raise ValueError("reason is required (audit trail)")
    _assert_orchestrator(agent_id)

    row = execute_returning(
        """UPDATE components
           SET status = 'decommissioned', updated_at = now()
           WHERE id = %s AND status != 'decommissioned'
           RETURNING id""",
        (component_id,),
    )
    if row is None:
        raise ValueError(
            f"Component {component_id} not found or already decommissioned"
        )
    return {"decommissioned": str(row["id"]), "reason": reason}


def decommission_components_bulk(
    agent_id: str, component_ids: list[str], reason: str
) -> dict:
    """Bulk variant — one transaction. Requires an explicit id list (refuses
    blank-wipe: we don't support "all components on plane X" style filters
    here because the danger/benefit ratio is poor).
    """
    if not reason or not reason.strip():
        raise ValueError("reason is required (audit trail)")
    if not component_ids:
        raise ValueError("refuse blank-wipe — provide explicit component_ids")
    _assert_orchestrator(agent_id)

    updated = execute(
        """UPDATE components
           SET status = 'decommissioned', updated_at = now()
           WHERE id = ANY(%s::uuid[]) AND status != 'decommissioned'
           RETURNING id""",
        (component_ids,),
    )
    return {
        "decommissioned": [str(r["id"]) for r in updated],
        "count": len(updated),
    }

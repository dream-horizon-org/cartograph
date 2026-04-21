"""Resources tools — iterators write, everyone reads.

The resources table is the iterator output queue. Each row is one resource
discovered on a plane (a repo, an R53 record, a K8s deployment, a Datadog
service, etc.). SMEs later get spawned per resource during Materialisation.

Iterator writes: upsert_resource (idempotent on plane+type+identifier).
Iterator reads: list_resources_for_plane (to see what's already registered).
Orchestrator reads: list_all_resources, get_resource_counts.
Any agent: get_resource by id.
"""

from shared.db import execute, execute_one, execute_returning, execute_mutate


_VALID_PLANES = {"github", "deploy", "cloud", "telemetry", "config"}


def upsert_resource(
    agent_id: str,
    plane: str,
    resource_type: str,
    identifier: str,
    access_desc: str = "",
    metadata: dict | None = None,
) -> dict:
    """Register a discovered resource. Only iterators can write.

    Idempotent: ON CONFLICT on (plane, resource_type, identifier) — updates
    access_desc and metadata, does NOT touch status or created_at.

    Iterators should call this for every resource they enumerate on their plane.
    """
    if plane not in _VALID_PLANES:
        raise ValueError(f"Invalid plane '{plane}'. Valid: {sorted(_VALID_PLANES)}")
    if not resource_type.strip():
        raise ValueError("resource_type cannot be empty")
    if not identifier.strip():
        raise ValueError("identifier cannot be empty")

    # Only iterators can write resources
    caller = execute_one(
        "SELECT agent_type, plane FROM agent_runs WHERE agent_id = %s AND status != 'decommissioned'",
        (agent_id,),
    )
    if caller is None:
        raise ValueError(f"Agent {agent_id} not found")
    if caller["agent_type"] != "iterator":
        raise ValueError(
            f"Only iterator agents can write resources. "
            f"{agent_id} is of type '{caller['agent_type']}'."
        )
    # Iterators can only write to their own plane
    if caller["plane"] and caller["plane"] != plane:
        raise ValueError(
            f"Iterator {agent_id} is on plane '{caller['plane']}', cannot write "
            f"resources for plane '{plane}'."
        )

    import json
    meta_json = json.dumps(metadata or {})

    row = execute_returning(
        """INSERT INTO resources (plane, resource_type, identifier, access_desc, metadata)
           VALUES (%s, %s, %s, %s, %s::jsonb)
           ON CONFLICT (plane, resource_type, identifier) DO UPDATE
             SET access_desc = EXCLUDED.access_desc,
                 metadata    = EXCLUDED.metadata
           RETURNING *""",
        (plane, resource_type, identifier, access_desc, meta_json),
    )
    return row


def upsert_resources_bulk(
    agent_id: str,
    plane: str,
    items: list[dict],
) -> dict:
    """Bulk upsert N resources for one plane in a single transaction.

    Each item must be a dict with keys: resource_type, identifier, access_desc?, metadata?.
    Idempotent on (plane, resource_type, identifier) — same upsert semantics as upsert_resource.

    Returns: {"inserted_or_updated": N, "rows": [ids...]}
    """
    if plane not in _VALID_PLANES:
        raise ValueError(f"Invalid plane '{plane}'. Valid: {sorted(_VALID_PLANES)}")
    if not isinstance(items, list) or not items:
        raise ValueError("items must be a non-empty list of resource dicts")
    if len(items) > 5000:
        raise ValueError("items length exceeds 5000 — split into smaller batches")

    caller = execute_one(
        "SELECT agent_type, plane FROM agent_runs WHERE agent_id = %s AND status != 'decommissioned'",
        (agent_id,),
    )
    if caller is None:
        raise ValueError(f"Agent {agent_id} not found")
    if caller["agent_type"] != "iterator":
        raise ValueError(
            f"Only iterator agents can write resources. "
            f"{agent_id} is of type '{caller['agent_type']}'."
        )
    if caller["plane"] and caller["plane"] != plane:
        raise ValueError(
            f"Iterator {agent_id} is on plane '{caller['plane']}', cannot write "
            f"resources for plane '{plane}'."
        )

    import json
    normalised: list[tuple] = []
    for i, item in enumerate(items):
        rt = (item.get("resource_type") or "").strip()
        ident = (item.get("identifier") or "").strip()
        if not rt:
            raise ValueError(f"items[{i}].resource_type cannot be empty")
        if not ident:
            raise ValueError(f"items[{i}].identifier cannot be empty")
        normalised.append(
            (
                plane,
                rt,
                ident,
                item.get("access_desc") or "",
                json.dumps(item.get("metadata") or {}),
            )
        )

    # Single transaction. psycopg3 executemany with returning=True gives one
    # result set per input row; iterate them via fetchone() + nextset().
    from shared.db import get_pool
    inserted_ids: list[str] = []
    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO resources (plane, resource_type, identifier, access_desc, metadata)
                   VALUES (%s, %s, %s, %s, %s::jsonb)
                   ON CONFLICT (plane, resource_type, identifier) DO UPDATE
                     SET access_desc = EXCLUDED.access_desc,
                         metadata    = EXCLUDED.metadata
                   RETURNING id""",
                normalised,
                returning=True,
            )
            while True:
                row = cur.fetchone()
                if row is not None:
                    inserted_ids.append(str(row["id"]))
                if not cur.nextset():
                    break
        conn.commit()

    return {"inserted_or_updated": len(inserted_ids), "ids": inserted_ids}


def get_resource(agent_id: str, resource_id: str) -> dict:
    """Read a single resource by id. Any active agent can read."""
    execute_one(
        "SELECT 1 FROM agent_runs WHERE agent_id = %s AND status != 'decommissioned'",
        (agent_id,),
    ) or (_ for _ in ()).throw(ValueError(f"Agent {agent_id} not found"))
    row = execute_one("SELECT * FROM resources WHERE id = %s", (resource_id,))
    if row is None:
        raise ValueError(f"Resource {resource_id} not found")
    return row


def list_resources_for_plane(agent_id: str, plane: str) -> list[dict]:
    """List all resources registered for a plane.

    Useful for iterators to check what's already registered (to avoid
    duplicate work across re-invocations) and for orchestrator to monitor
    iterator progress.
    """
    execute_one(
        "SELECT 1 FROM agent_runs WHERE agent_id = %s AND status != 'decommissioned'",
        (agent_id,),
    ) or (_ for _ in ()).throw(ValueError(f"Agent {agent_id} not found"))
    if plane not in _VALID_PLANES:
        raise ValueError(f"Invalid plane '{plane}'")
    return execute(
        "SELECT * FROM resources WHERE plane = %s ORDER BY created_at DESC",
        (plane,),
    )


def list_all_resources(agent_id: str, status: str | None = None) -> list[dict]:
    """List resources across all planes, optionally filtered by status.

    status values: pending, assigned, done, rejected.
    When status is omitted, excludes 'rejected' by default (use
    status='rejected' explicitly to audit soft-deleted rows).
    """
    execute_one(
        "SELECT 1 FROM agent_runs WHERE agent_id = %s AND status != 'decommissioned'",
        (agent_id,),
    ) or (_ for _ in ()).throw(ValueError(f"Agent {agent_id} not found"))
    if status and status not in ("pending", "assigned", "done", "rejected"):
        raise ValueError(f"Invalid status '{status}'")
    if status:
        return execute(
            "SELECT * FROM resources WHERE status = %s ORDER BY plane, created_at",
            (status,),
        )
    return execute(
        "SELECT * FROM resources WHERE status != 'rejected' "
        "ORDER BY plane, created_at"
    )


def get_resource_counts(agent_id: str) -> dict:
    """Get counts of resources by plane and status. Useful for orchestrator overview."""
    execute_one(
        "SELECT 1 FROM agent_runs WHERE agent_id = %s AND status != 'decommissioned'",
        (agent_id,),
    ) or (_ for _ in ()).throw(ValueError(f"Agent {agent_id} not found"))

    rows = execute(
        """SELECT plane, status, COUNT(*) AS cnt
           FROM resources
           GROUP BY plane, status
           ORDER BY plane, status"""
    )
    return {"by_plane_status": rows}


def _resolve_rejector(agent_id: str, plane: str, force: bool) -> dict:
    """Shared auth check for reject_resource / reject_resources_bulk.

    Returns the caller's row. Raises ValueError if caller can't reject this
    plane's resources.
    """
    caller = execute_one(
        "SELECT agent_type, plane FROM agent_runs "
        "WHERE agent_id = %s AND status != 'decommissioned'",
        (agent_id,),
    )
    if caller is None:
        raise ValueError(f"Agent {agent_id} not found")
    if force:
        # Only orchestrator can force-reject
        if caller["agent_type"] != "orchestrator":
            raise ValueError(
                f"Only orchestrator can force-reject resources. "
                f"{agent_id} is of type '{caller['agent_type']}'."
            )
        return caller
    # Non-force path: iterator, own plane only
    if caller["agent_type"] != "iterator":
        raise ValueError(
            f"Only iterator agents can reject resources on their own plane "
            f"(or orchestrator via force=True). "
            f"{agent_id} is of type '{caller['agent_type']}'."
        )
    if caller["plane"] and caller["plane"] != plane:
        raise ValueError(
            f"Iterator {agent_id} is on plane '{caller['plane']}', "
            f"cannot reject resources on plane '{plane}'."
        )
    return caller


def _reject_batch(
    agent_id: str,
    plane: str,
    resource_ids: list[str] | None,
    resource_types: list[str] | None,
    reason: str,
) -> dict:
    """Perform the actual bulk soft-delete inside one transaction.

    Cascade safety: rows that already have an SME assigned via
    resource_component_agents are skipped (reported in 'skipped_cascade').
    Only rows currently in 'pending' or 'assigned' are rejected — 'done'
    and already-'rejected' rows are skipped.
    """
    from shared.db import get_pool
    rejected_ids: list[str] = []
    skipped_cascade: list[str] = []
    skipped_terminal: list[str] = []

    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            # Build candidate set via one SELECT; LOCK FOR UPDATE so a
            # concurrent SME-assignment can't sneak in between check and flip.
            where_clauses = ["plane = %s"]
            params: list = [plane]
            if resource_ids:
                where_clauses.append("id = ANY(%s::uuid[])")
                params.append(resource_ids)
            if resource_types:
                where_clauses.append("resource_type = ANY(%s)")
                params.append(resource_types)
            where_sql = " AND ".join(where_clauses)
            cur.execute(
                f"""SELECT id, status,
                           EXISTS(
                             SELECT 1 FROM resource_component_agents rca
                             WHERE rca.resource_id = r.id
                           ) AS has_sme
                    FROM resources r
                    WHERE {where_sql}
                    FOR UPDATE""",
                params,
            )
            candidates = cur.fetchall()

            to_reject: list[str] = []
            for row in candidates:
                rid = str(row["id"])
                if row["status"] in ("done", "rejected"):
                    skipped_terminal.append(rid)
                    continue
                if row["has_sme"]:
                    skipped_cascade.append(rid)
                    continue
                to_reject.append(rid)

            if to_reject:
                cur.execute(
                    """UPDATE resources
                       SET status = 'rejected',
                           rejected_at = now(),
                           rejected_by = %s,
                           rejected_reason = %s
                       WHERE id = ANY(%s::uuid[])
                       RETURNING id""",
                    (agent_id, reason, to_reject),
                )
                rejected_ids = [str(r["id"]) for r in cur.fetchall()]
        conn.commit()

    return {
        "rejected": len(rejected_ids),
        "ids": rejected_ids,
        "skipped_cascade": skipped_cascade,
        "skipped_terminal": skipped_terminal,
    }


def reject_resource(
    agent_id: str,
    resource_id: str,
    reason: str,
    force: bool = False,
) -> dict:
    """Soft-delete a single resource (status='rejected'). Iterator-scoped unless force.

    See reject_resources_bulk for the bulk variant — prefer that for cleanups.
    Cascade check: rejected only if no SME has been assigned (unless force=True
    by orchestrator — even then the SME cascade is reported).
    """
    if not reason or not reason.strip():
        raise ValueError("reason is required (used for audit trail)")
    row = execute_one(
        "SELECT plane FROM resources WHERE id = %s", (resource_id,)
    )
    if row is None:
        raise ValueError(f"Resource {resource_id} not found")
    plane = row["plane"]
    _resolve_rejector(agent_id, plane, force)
    return _reject_batch(agent_id, plane, [resource_id], None, reason)


def reject_resources_bulk(
    agent_id: str,
    plane: str,
    resource_ids: list[str] | None = None,
    resource_types: list[str] | None = None,
    reason: str = "",
    force: bool = False,
) -> dict:
    """Bulk soft-delete resources in one transaction. Plane-scoped.

    Filters (AND together): resource_ids list, resource_types list.
    At least one filter must be non-empty — we refuse a blank wipe.

    Only 'pending' and 'assigned' rows flip to 'rejected'. 'done' and
    already-'rejected' rows are skipped and returned in 'skipped_terminal'.
    Rows with an SME already assigned via resource_component_agents are
    skipped and returned in 'skipped_cascade' (decommission the SME first
    or transfer its component before retrying).

    Returns {rejected: N, ids: [...], skipped_cascade: [...], skipped_terminal: [...]}.
    """
    if plane not in _VALID_PLANES:
        raise ValueError(f"Invalid plane '{plane}'. Valid: {sorted(_VALID_PLANES)}")
    if not reason or not reason.strip():
        raise ValueError("reason is required (used for audit trail)")
    if not resource_ids and not resource_types:
        raise ValueError(
            "refuse blank-wipe — provide at least one of "
            "resource_ids or resource_types"
        )
    _resolve_rejector(agent_id, plane, force)
    return _reject_batch(
        agent_id, plane, resource_ids or None, resource_types or None, reason
    )


def bulk_spawn_smes(
    agent_id: str,
    plane: str,
    agent_manager,
    resource_ids: list[str] | None = None,
    all_pending: bool = False,
    task_description: str | None = None,
) -> dict:
    """Spawn one SME per resource for a plane in one transaction.

    Creates agent_runs rows, RCA rows (component_id=NULL reservation slots),
    flips matching resources.status to 'assigned', and optionally creates
    one task per SME (owner=caller orchestrator, worker=new SME, status=BW).

    Only the orchestrator can call. Requires at least one of resource_ids or
    all_pending=True (refuses blank-wipe). Skips resources that already have
    an SME assigned via RCA.

    Returns:
      {spawned: N, skipped_already_assigned: [...],
       items: [{agent_id, resource_id, task_id}, ...]}
    """
    if plane not in _VALID_PLANES:
        raise ValueError(f"Invalid plane '{plane}'. Valid: {sorted(_VALID_PLANES)}")
    if not resource_ids and not all_pending:
        raise ValueError(
            "refuse blank-wipe — provide at least one of "
            "resource_ids=[] or all_pending=True"
        )

    caller = execute_one(
        "SELECT agent_type FROM agent_runs WHERE agent_id = %s AND status != 'decommissioned'",
        (agent_id,),
    )
    if caller is None:
        raise ValueError(f"Agent {agent_id} not found")
    if caller["agent_type"] != "orchestrator":
        raise ValueError(
            f"Only orchestrator can bulk-spawn SMEs. "
            f"{agent_id} is of type '{caller['agent_type']}'."
        )

    # Resolve target resource set
    if resource_ids:
        candidates = execute(
            """SELECT id FROM resources
               WHERE plane = %s AND id = ANY(%s::uuid[]) AND status = 'pending'""",
            (plane, resource_ids),
        )
    else:
        candidates = execute(
            "SELECT id FROM resources WHERE plane = %s AND status = 'pending'",
            (plane,),
        )

    # Filter out already-assigned (have an RCA row)
    already_assigned: list[str] = []
    to_spawn: list[str] = []
    for c in candidates:
        rid = str(c["id"])
        existing = execute_one(
            "SELECT 1 FROM resource_component_agents WHERE resource_id = %s LIMIT 1",
            (rid,),
        )
        if existing:
            already_assigned.append(rid)
        else:
            to_spawn.append(rid)

    # Spawn each SME via AgentManager (creates workspace + .mcp.json +
    # agent_runs + RCA row + flips resource status). Tasks created after.
    spawned: list[dict] = []
    for rid in to_spawn:
        new_agent_id = agent_manager.create_agent(
            agent_type="sme",
            resource_id=rid,
        )
        task_id = None
        if task_description:
            from cartograph_mcp.tools import tasks as _tasks
            task_row = _tasks.create_task(
                owner_agent_id=agent_id,
                worker_agent_id=new_agent_id,
                description=task_description,
            )
            task_id = str(task_row["id"])
        spawned.append(
            {"agent_id": new_agent_id, "resource_id": rid, "task_id": task_id}
        )

    return {
        "spawned": len(spawned),
        "skipped_already_assigned": already_assigned,
        "items": spawned,
    }


def mark_resource_done(agent_id: str, resource_id: str) -> dict:
    """Mark a resource as fully processed. Called by SME when materialisation completes.

    Validates: agent is the SME assigned to this resource via resource_component_agents.
    """
    caller = execute_one(
        "SELECT agent_type FROM agent_runs WHERE agent_id = %s AND status != 'decommissioned'",
        (agent_id,),
    )
    if caller is None:
        raise ValueError(f"Agent {agent_id} not found")
    if caller["agent_type"] != "sme":
        raise ValueError("Only SMEs can mark resources as done")

    assigned = execute_one(
        """SELECT 1 FROM resource_component_agents
           WHERE resource_id = %s AND agent_id = %s""",
        (resource_id, agent_id),
    )
    if assigned is None:
        raise ValueError(
            f"SME {agent_id} is not assigned to resource {resource_id}"
        )

    row = execute_returning(
        """UPDATE resources SET status = 'done'
           WHERE id = %s
           RETURNING *""",
        (resource_id,),
    )
    if row is None:
        raise ValueError(f"Resource {resource_id} not found")
    return row

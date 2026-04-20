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
    """List all resources across all planes, optionally filtered by status.

    status values: pending, assigned, done.
    """
    execute_one(
        "SELECT 1 FROM agent_runs WHERE agent_id = %s AND status != 'decommissioned'",
        (agent_id,),
    ) or (_ for _ in ()).throw(ValueError(f"Agent {agent_id} not found"))
    if status and status not in ("pending", "assigned", "done"):
        raise ValueError(f"Invalid status '{status}'")
    if status:
        return execute(
            "SELECT * FROM resources WHERE status = %s ORDER BY plane, created_at",
            (status,),
        )
    return execute("SELECT * FROM resources ORDER BY plane, created_at")


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

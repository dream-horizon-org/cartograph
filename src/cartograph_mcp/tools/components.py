"""Component graph tools — SMEs write their own, everyone reads.

Components, attributions, edges, and unresolved references. SMEs are
scoped to their own components via resource_component_agents.
"""

import json

from shared.db import execute, execute_one, execute_returning, execute_mutate


# ─── Helpers ───

def _get_caller(agent_id: str) -> dict:
    row = execute_one(
        "SELECT agent_type, plane, resource_id FROM agent_runs "
        "WHERE agent_id = %s AND status != 'decommissioned'",
        (agent_id,),
    )
    if row is None:
        raise ValueError(f"Agent {agent_id} not found")
    return row


def _assert_owns_component(agent_id: str, component_id: str) -> None:
    """Raise if agent_id is not the SME assigned to this component."""
    row = execute_one(
        "SELECT 1 FROM resource_component_agents WHERE component_id = %s AND agent_id = %s",
        (component_id, agent_id),
    )
    if row is None:
        raise ValueError(
            f"Agent {agent_id} does not own component {component_id}"
        )


# ─── Components ───

def upsert_component(
    agent_id: str,
    canonical_name: str,
    display_name: str,
    component_type: str,
    confidence: float = 1.0,
    metadata: dict | None = None,
) -> dict:
    """Create or update a component. SME-only.

    On first create, also links the SME's resource to this component via
    resource_component_agents and flips the resource to 'assigned'.

    Upserts on canonical_name — if the component already exists and this
    SME owns it, metadata/display_name/confidence are updated.
    """
    caller = _get_caller(agent_id)
    if caller["agent_type"] != "sme":
        raise ValueError(f"Only SME agents can upsert components. {agent_id} is '{caller['agent_type']}'")

    meta_json = json.dumps(metadata or {})

    # Check if component already exists
    existing = execute_one(
        "SELECT id FROM components WHERE canonical_name = %s",
        (canonical_name,),
    )

    if existing:
        _assert_owns_component(agent_id, str(existing["id"]))
        row = execute_returning(
            """UPDATE components
               SET display_name = %s, component_type = %s, confidence = %s,
                   metadata = %s::jsonb, updated_at = now()
               WHERE canonical_name = %s
               RETURNING *""",
            (display_name, component_type, confidence, meta_json, canonical_name),
        )
        return row

    # Create new component
    row = execute_returning(
        """INSERT INTO components (canonical_name, display_name, component_type, confidence, metadata)
           VALUES (%s, %s, %s, %s, %s::jsonb)
           RETURNING *""",
        (canonical_name, display_name, component_type, confidence, meta_json),
    )
    component_id = str(row["id"])

    # Link resource → component → agent
    resource_id = caller.get("resource_id")
    if resource_id:
        execute_mutate(
            """INSERT INTO resource_component_agents (resource_id, component_id, agent_id)
               VALUES (%s::uuid, %s::uuid, %s)
               ON CONFLICT (resource_id, component_id) DO NOTHING""",
            (resource_id, component_id, agent_id),
        )
        execute_mutate(
            "UPDATE resources SET status = 'assigned' WHERE id = %s::uuid AND status = 'pending'",
            (resource_id,),
        )

    return row


def get_component(agent_id: str, component_id: str) -> dict:
    """Read a single component. Any active agent can read."""
    _get_caller(agent_id)
    row = execute_one("SELECT * FROM components WHERE id = %s", (component_id,))
    if row is None:
        raise ValueError(f"Component {component_id} not found")
    return row


# ─── Attributions ───

def upsert_attribution(
    agent_id: str,
    component_id: str,
    plane: str,
    resource_type: str,
    identifier: str,
    evidence: str = "",
    confidence: float = 1.0,
    metadata: dict | None = None,
) -> dict:
    """Add or update an attribution on a component. SME-only, own component.

    Upserts on (plane, resource_type, identifier).
    """
    caller = _get_caller(agent_id)
    if caller["agent_type"] != "sme":
        raise ValueError(f"Only SME agents can write attributions. {agent_id} is '{caller['agent_type']}'")
    _assert_owns_component(agent_id, component_id)

    meta_json = json.dumps(metadata or {})
    row = execute_returning(
        """INSERT INTO attributions
               (component_id, plane, resource_type, identifier, evidence,
                confidence, metadata, discovered_by)
           VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s)
           ON CONFLICT (plane, resource_type, identifier) DO UPDATE
             SET evidence = EXCLUDED.evidence,
                 confidence = EXCLUDED.confidence,
                 metadata = EXCLUDED.metadata,
                 last_seen_at = now()
           RETURNING *""",
        (component_id, plane, resource_type, identifier, evidence,
         confidence, meta_json, agent_id),
    )
    return row


def get_attributions(agent_id: str, component_id: str) -> list[dict]:
    """Read all attributions for a component. Any active agent can read."""
    _get_caller(agent_id)
    return execute(
        "SELECT * FROM attributions WHERE component_id = %s ORDER BY plane, resource_type",
        (component_id,),
    )


# ─── Edges ───

def create_edge(
    agent_id: str,
    source_id: str,
    target_id: str,
    edge_type: str,
    identifier: str,
    source_attr_id: str | None = None,
    target_attr_id: str | None = None,
    evidence: list | None = None,
    confidence: float = 1.0,
    metadata: dict | None = None,
) -> dict:
    """Create a dependency edge between two components. SME-only.

    SME must own either the source or target component.
    Upserts on (source_id, target_id, edge_type, identifier).
    """
    caller = _get_caller(agent_id)
    if caller["agent_type"] != "sme":
        raise ValueError(f"Only SME agents can create edges. {agent_id} is '{caller['agent_type']}'")

    # SME must own at least one side
    owns_source = execute_one(
        "SELECT 1 FROM resource_component_agents WHERE component_id = %s AND agent_id = %s",
        (source_id, agent_id),
    )
    owns_target = execute_one(
        "SELECT 1 FROM resource_component_agents WHERE component_id = %s AND agent_id = %s",
        (target_id, agent_id),
    )
    if not owns_source and not owns_target:
        raise ValueError(f"Agent {agent_id} must own either source or target component")

    evidence_json = json.dumps(evidence or [])
    meta_json = json.dumps(metadata or {})

    row = execute_returning(
        """INSERT INTO edges
               (source_id, target_id, edge_type, identifier,
                source_attr_id, target_attr_id, evidence,
                confidence, metadata, discovered_by)
           VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s::jsonb, %s)
           ON CONFLICT (source_id, target_id, edge_type, identifier) DO UPDATE
             SET evidence = EXCLUDED.evidence,
                 confidence = EXCLUDED.confidence,
                 metadata = EXCLUDED.metadata,
                 source_attr_id = EXCLUDED.source_attr_id,
                 target_attr_id = EXCLUDED.target_attr_id,
                 last_seen_at = now()
           RETURNING *""",
        (source_id, target_id, edge_type, identifier,
         source_attr_id, target_attr_id, evidence_json,
         confidence, meta_json, agent_id),
    )
    return row


def get_edges(agent_id: str, component_id: str) -> list[dict]:
    """Read all edges where component is source or target. Any active agent."""
    _get_caller(agent_id)
    return execute(
        """SELECT * FROM edges
           WHERE source_id = %s OR target_id = %s
           ORDER BY edge_type, identifier""",
        (component_id, component_id),
    )


# ─── Unresolved References ───

def insert_unresolved(
    agent_id: str,
    component_id: str,
    reference_type: str,
    reference_value: str,
    context: dict | None = None,
) -> dict:
    """Insert an unresolved reference found in a component. SME-only, own component."""
    caller = _get_caller(agent_id)
    if caller["agent_type"] != "sme":
        raise ValueError(f"Only SME agents can insert unresolved refs. {agent_id} is '{caller['agent_type']}'")
    _assert_owns_component(agent_id, component_id)

    ctx_json = json.dumps(context) if context else None
    row = execute_returning(
        """INSERT INTO unresolved
               (found_in_component_id, reference_type, reference_value, context, found_by_agent)
           VALUES (%s, %s, %s, %s::jsonb, %s)
           RETURNING *""",
        (component_id, reference_type, reference_value, ctx_json, agent_id),
    )
    return row


def get_unresolved(agent_id: str, component_id: str) -> list[dict]:
    """Read unresolved references for a component. Any active agent."""
    _get_caller(agent_id)
    return execute(
        "SELECT * FROM unresolved WHERE found_in_component_id = %s ORDER BY created_at",
        (component_id,),
    )


def resolve_reference(
    agent_id: str,
    unresolved_id: str,
    resolved_to_component_id: str,
) -> dict:
    """Resolve an unresolved reference to a component. SME-only."""
    caller = _get_caller(agent_id)
    if caller["agent_type"] != "sme":
        raise ValueError(f"Only SME agents can resolve references. {agent_id} is '{caller['agent_type']}'")

    row = execute_returning(
        """UPDATE unresolved
           SET resolved = TRUE,
               resolved_to_component_id = %s,
               attempts = attempts + 1
           WHERE id = %s
           RETURNING *""",
        (resolved_to_component_id, unresolved_id),
    )
    if row is None:
        raise ValueError(f"Unresolved reference {unresolved_id} not found")
    return row

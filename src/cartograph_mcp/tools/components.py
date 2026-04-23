"""Component-graph tools: components, attributions, edges, unresolved.

Writes are SME-scoped (enforced via agent_runs + resource_component_agents):
  - upsert_component: create or update the SME's single component, filling
    the RCA reservation row (component_id=NULL → component_id=<new>).
    Enforces the "1 active component per SME" invariant; split/merge go
    through the consolidation pipeline.
  - upsert_attribution: only on the SME's own component.
  - create_edge: only where the SME owns source_id.
  - insert_unresolved: only where the SME owns found_in_component_id.
  - resolve_reference: marks an unresolved row resolved + optionally creates
    an edge.

Reads are open to every active agent:
  - get_component, get_attributions, get_edges, get_unresolved.

Embeddings are written inline at row-write time via shared.embedding. If
OPENAI_API_KEY is unset or the API errors, the write still succeeds —
embedding is stored NULL and vector_search simply won't surface that row.
"""

from __future__ import annotations

import json

from shared import embedding as emb
from shared.db import execute, execute_one, execute_mutate, execute_returning


_VALID_COMPONENT_TYPES = {
    "application", "database", "cache", "queue", "lambda", "cron",
    "external-service", "library", "infrastructure",
}
_VALID_COMPONENT_STATUSES = {"active", "deprecated", "decommissioned"}
_VALID_ATTRIBUTION_PLANES = {"github", "deploy", "cloud", "telemetry", "config"}
_VALID_EDGE_TYPES = {
    "calls", "reads_from", "writes_to", "triggers",
    "publishes_to", "consumes_from", "runs_on",
}


def _caller(agent_id: str) -> dict:
    row = execute_one(
        "SELECT agent_id, agent_type, plane FROM agent_runs "
        "WHERE agent_id = %s AND status != 'decommissioned'",
        (agent_id,),
    )
    if row is None:
        raise ValueError(f"Agent {agent_id} not found")
    return row


def _assert_sme(agent_id: str) -> dict:
    row = _caller(agent_id)
    if row["agent_type"] != "sme":
        raise ValueError(
            f"Only SMEs can write component-graph rows. "
            f"{agent_id} is of type '{row['agent_type']}'."
        )
    return row


def _sme_owns_component(agent_id: str, component_id: str) -> bool:
    row = execute_one(
        """SELECT 1 FROM resource_component_agents
           WHERE agent_id = %s AND component_id = %s""",
        (agent_id, component_id),
    )
    return row is not None


# ============ upsert_component ============


def upsert_component(agent_id: str, component_data: dict) -> dict:
    """Create or update this SME's single active component.

    On first call, fills the RCA reservation row (component_id=NULL → new
    component). On subsequent calls, updates the existing component in place.

    Enforces 1-active-component-per-SME: refuses if the SME already owns a
    non-decommissioned component and this call would create a second
    (use nominate_consolidation type='split' in Phase 3 to split).
    """
    sme = _assert_sme(agent_id)
    _ = sme

    canonical_name = (component_data.get("canonical_name") or "").strip()
    display_name = (component_data.get("display_name") or "").strip()
    component_type = (component_data.get("component_type") or "").strip()
    confidence = float(component_data.get("confidence", 1.0))
    metadata = component_data.get("metadata") or {}
    component_doc_md = component_data.get("component_doc_md")
    # source_slice: structural description of which parts of which source
    # resource(s) this component covers. REPLACE-on-provide semantics
    # (caller must pass the full current view); COALESCE-preserve on omit.
    source_slice = component_data.get("source_slice")
    if source_slice is not None and not isinstance(source_slice, dict):
        raise ValueError(
            "source_slice must be a dict keyed by resource_id (see SCHEMA.md)"
        )

    if not canonical_name:
        raise ValueError("canonical_name is required")
    if not display_name:
        raise ValueError("display_name is required")
    if component_type not in _VALID_COMPONENT_TYPES:
        raise ValueError(
            f"Invalid component_type '{component_type}'. "
            f"Valid: {sorted(_VALID_COMPONENT_TYPES)}"
        )
    if not (0.0 <= confidence <= 1.0):
        raise ValueError("confidence must be in [0.0, 1.0]")

    vec = emb.vector_literal(emb.embed_text(
        emb.component_embed_text(canonical_name, display_name, component_type, metadata)
    ))

    # Does this SME already own an active component? (any RCA row linked
    # to a non-decommissioned component). If yes → UPDATE path on that one.
    # If no → CREATE path requires a reserved RCA row (component_id IS NULL).
    owned = execute_one(
        """SELECT c.id AS component_id
           FROM resource_component_agents rca
           JOIN components c ON c.id = rca.component_id
           WHERE rca.agent_id = %s AND c.status != 'decommissioned'
           LIMIT 1""",
        (agent_id,),
    )
    if owned is not None:
        # source_slice: wholesale REPLACE if provided, COALESCE-preserve
        # if omitted. Contract documented in SCHEMA.md — callers pass the
        # FULL current slice view each time, not a delta. Phase 4 merge/
        # split mutations must also explicitly call upsert_component to
        # update both sides' source_slice; transfer_attributions does NOT
        # auto-touch it (attributions are evidence, slice is structural).
        slice_json = json.dumps(source_slice) if source_slice is not None else None
        row = execute_returning(
            """UPDATE components
               SET canonical_name = %s,
                   display_name = %s,
                   component_type = %s,
                   confidence = %s,
                   metadata = %s::jsonb,
                   component_doc_md = COALESCE(%s, component_doc_md),
                   source_slice = COALESCE(%s::jsonb, source_slice),
                   embedding = %s::vector,
                   scanned_at = now(),
                   updated_at = now()
               WHERE id = %s
               RETURNING *""",
            (
                canonical_name, display_name, component_type, confidence,
                json.dumps(metadata), component_doc_md, slice_json, vec,
                owned["component_id"],
            ),
        )
        if row is None:
            raise ValueError(
                f"Component {owned['component_id']} vanished under this SME "
                "— race or manual cleanup."
            )
        return row

    # CREATE path: find a reserved RCA slot for this SME.
    reservation = execute_one(
        """SELECT resource_id FROM resource_component_agents
           WHERE agent_id = %s AND component_id IS NULL
           LIMIT 1""",
        (agent_id,),
    )
    if reservation is None:
        raise ValueError(
            f"SME {agent_id} has no assigned resource (no reserved RCA row). "
            "Spawn via bulk_spawn_smes or create_agent first."
        )

    # Check canonical_name is not already taken by another SME's component.
    conflict = execute_one(
        """SELECT c.id, rca.agent_id AS owner_agent
           FROM components c
           LEFT JOIN resource_component_agents rca ON rca.component_id = c.id
           WHERE c.canonical_name = %s""",
        (canonical_name,),
    )
    if conflict is not None and conflict["owner_agent"] != agent_id:
        raise ValueError(
            f"canonical_name '{canonical_name}' already belongs to component "
            f"{conflict['id']} (owner {conflict['owner_agent']}). Pick a "
            "different name or raise a merge nomination in Phase 3."
        )

    slice_json = json.dumps(source_slice) if source_slice is not None else None
    new_component = execute_returning(
        """INSERT INTO components
           (canonical_name, display_name, component_type, confidence, metadata,
            component_doc_md, source_slice, embedding, scanned_at)
           VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s::jsonb, %s::vector, now())
           RETURNING *""",
        (
            canonical_name, display_name, component_type, confidence,
            json.dumps(metadata), component_doc_md, slice_json, vec,
        ),
    )
    execute_mutate(
        """UPDATE resource_component_agents
           SET component_id = %s
           WHERE agent_id = %s AND resource_id = %s""",
        (new_component["id"], agent_id, reservation["resource_id"]),
    )
    return new_component


# ============ upsert_attribution ============


def upsert_attribution(agent_id: str, component_id: str, attribution_data: dict) -> dict:
    """Create or update an attribution on this SME's component.

    ON CONFLICT on (plane, resource_type, identifier): if the existing
    attribution is on THIS SME's component → UPDATE; if on another
    component → refuse (cross-component attributions are a consolidation
    signal, not a direct write).
    """
    _assert_sme(agent_id)
    if not _sme_owns_component(agent_id, component_id):
        raise ValueError(
            f"SME {agent_id} does not own component {component_id}. "
            "You can only attribute to your own component."
        )

    plane = (attribution_data.get("plane") or "").strip()
    resource_type = (attribution_data.get("resource_type") or "").strip()
    identifier = (attribution_data.get("identifier") or "").strip()
    evidence = attribution_data.get("evidence")
    confidence = float(attribution_data.get("confidence", 1.0))
    metadata = attribution_data.get("metadata") or {}

    if plane not in _VALID_ATTRIBUTION_PLANES:
        raise ValueError(
            f"Invalid plane '{plane}'. Valid: {sorted(_VALID_ATTRIBUTION_PLANES)}"
        )
    if not resource_type:
        raise ValueError("resource_type is required")
    if not identifier:
        raise ValueError("identifier is required")
    if not (0.0 <= confidence <= 1.0):
        raise ValueError("confidence must be in [0.0, 1.0]")

    # Detect cross-component conflicts.
    existing = execute_one(
        """SELECT id, component_id FROM attributions
           WHERE plane = %s AND resource_type = %s AND identifier = %s""",
        (plane, resource_type, identifier),
    )
    if existing is not None and str(existing["component_id"]) != str(component_id):
        raise ValueError(
            f"Attribution ({plane}, {resource_type}, {identifier}) already "
            f"belongs to component {existing['component_id']}. Cross-component "
            "reassignment requires consolidation (Phase 3)."
        )

    vec = emb.vector_literal(emb.embed_text(
        emb.attribution_embed_text(resource_type, identifier)
    ))
    row = execute_returning(
        """INSERT INTO attributions
           (component_id, plane, resource_type, identifier, evidence, confidence,
            metadata, embedding, discovered_by, last_seen_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::vector, %s, now())
           ON CONFLICT (plane, resource_type, identifier) DO UPDATE
             SET evidence = EXCLUDED.evidence,
                 confidence = EXCLUDED.confidence,
                 metadata = EXCLUDED.metadata,
                 embedding = EXCLUDED.embedding,
                 last_seen_at = now()
           RETURNING *""",
        (
            component_id, plane, resource_type, identifier, evidence,
            confidence, json.dumps(metadata), vec, agent_id,
        ),
    )
    return row


# ============ create_edge ============


def create_edge(agent_id: str, edge_data: dict) -> dict:
    """Record a dependency edge from one of this SME's components to another
    component. Idempotent on (source_id, target_id, edge_type, identifier).
    """
    _assert_sme(agent_id)

    source_id = (edge_data.get("source_id") or "").strip()
    target_id = (edge_data.get("target_id") or "").strip()
    edge_type = (edge_data.get("edge_type") or "").strip()
    identifier = (edge_data.get("identifier") or "").strip()
    source_attr_id = edge_data.get("source_attr_id")
    target_attr_id = edge_data.get("target_attr_id")
    evidence = edge_data.get("evidence") or []
    confidence = float(edge_data.get("confidence", 1.0))
    metadata = edge_data.get("metadata") or {}

    if not source_id:
        raise ValueError("source_id is required")
    if not target_id:
        raise ValueError("target_id is required")
    if source_id == target_id:
        raise ValueError("source_id and target_id must differ (no self-loops)")
    if edge_type not in _VALID_EDGE_TYPES:
        raise ValueError(
            f"Invalid edge_type '{edge_type}'. Valid: {sorted(_VALID_EDGE_TYPES)}"
        )
    if not identifier:
        raise ValueError("identifier is required")
    if not _sme_owns_component(agent_id, source_id):
        raise ValueError(
            f"SME {agent_id} does not own source component {source_id}. "
            "You can only create edges FROM your own component."
        )

    vec = emb.vector_literal(emb.embed_text(
        emb.edge_embed_text(edge_type, identifier)
    ))
    row = execute_returning(
        """INSERT INTO edges
           (source_id, target_id, edge_type, identifier, source_attr_id,
            target_attr_id, evidence, confidence, metadata, embedding,
            discovered_by, last_seen_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s::jsonb, %s::vector, %s, now())
           ON CONFLICT (source_id, target_id, edge_type, identifier) DO UPDATE
             SET evidence = EXCLUDED.evidence,
                 confidence = EXCLUDED.confidence,
                 metadata = EXCLUDED.metadata,
                 embedding = EXCLUDED.embedding,
                 source_attr_id = EXCLUDED.source_attr_id,
                 target_attr_id = EXCLUDED.target_attr_id,
                 last_seen_at = now()
           RETURNING *""",
        (
            source_id, target_id, edge_type, identifier,
            source_attr_id, target_attr_id,
            json.dumps(evidence), confidence, json.dumps(metadata), vec, agent_id,
        ),
    )
    return row


# ============ insert_unresolved ============


def insert_unresolved(agent_id: str, unresolved_data: dict) -> dict:
    """Record an unresolved reference found inside this SME's component."""
    _assert_sme(agent_id)
    found_in_component_id = (
        unresolved_data.get("found_in_component_id") or ""
    ).strip()
    reference_type = (unresolved_data.get("reference_type") or "").strip()
    reference_value = (unresolved_data.get("reference_value") or "").strip()
    context = unresolved_data.get("context") or {}

    if not found_in_component_id:
        raise ValueError("found_in_component_id is required")
    if not reference_type:
        raise ValueError("reference_type is required")
    if not reference_value:
        raise ValueError("reference_value is required")
    if not _sme_owns_component(agent_id, found_in_component_id):
        raise ValueError(
            f"SME {agent_id} does not own component {found_in_component_id}."
        )

    vec = emb.vector_literal(emb.embed_text(
        emb.unresolved_embed_text(reference_type, reference_value)
    ))
    row = execute_returning(
        """INSERT INTO unresolved
           (found_in_component_id, reference_type, reference_value, context,
            embedding, found_by_agent)
           VALUES (%s, %s, %s, %s::jsonb, %s::vector, %s)
           RETURNING *""",
        (
            found_in_component_id, reference_type, reference_value,
            json.dumps(context), vec, agent_id,
        ),
    )
    return row


# ============ resolve_reference ============


def resolve_reference(
    agent_id: str, unresolved_id: str, resolved_to_component_id: str
) -> dict:
    """Mark an unresolved reference as resolved (pointing at a component).

    Open to any active agent — resolution is often cross-SME. The original
    SME (found_by_agent) stays on the row; resolution doesn't claim ownership.
    """
    _caller(agent_id)  # just verify agent exists
    target_exists = execute_one(
        "SELECT 1 FROM components WHERE id = %s AND status != 'decommissioned'",
        (resolved_to_component_id,),
    )
    if target_exists is None:
        raise ValueError(
            f"resolved_to_component_id {resolved_to_component_id} "
            "does not exist or is decommissioned"
        )
    row = execute_returning(
        """UPDATE unresolved
           SET resolved = TRUE,
               resolved_to_component_id = %s,
               attempts = attempts + 1
           WHERE id = %s AND resolved = FALSE
           RETURNING *""",
        (resolved_to_component_id, unresolved_id),
    )
    if row is None:
        raise ValueError(
            f"unresolved {unresolved_id} not found or already resolved"
        )
    return row


# ============ Reads (open to all active agents) ============


def get_component(agent_id: str, component_id: str) -> dict:
    _caller(agent_id)
    row = execute_one("SELECT * FROM components WHERE id = %s", (component_id,))
    if row is None:
        raise ValueError(f"Component {component_id} not found")
    return row


def get_attributions(agent_id: str, component_id: str) -> list[dict]:
    _caller(agent_id)
    return execute(
        """SELECT * FROM attributions
           WHERE component_id = %s
           ORDER BY plane, resource_type, identifier""",
        (component_id,),
    )


def get_edges(agent_id: str, component_id: str) -> dict:
    _caller(agent_id)
    outbound = execute(
        """SELECT * FROM edges
           WHERE source_id = %s
           ORDER BY edge_type, identifier""",
        (component_id,),
    )
    inbound = execute(
        """SELECT * FROM edges
           WHERE target_id = %s
           ORDER BY edge_type, identifier""",
        (component_id,),
    )
    return {"outbound": outbound, "inbound": inbound}


def get_unresolved(agent_id: str, component_id: str) -> list[dict]:
    _caller(agent_id)
    return execute(
        """SELECT * FROM unresolved
           WHERE found_in_component_id = %s
           ORDER BY resolved ASC, created_at DESC""",
        (component_id,),
    )

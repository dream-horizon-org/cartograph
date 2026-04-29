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
from shared.actor_auth import require_active_agent
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


_caller = require_active_agent  # thin alias; see shared.actor_auth


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


# ============ Phase 7.4.12: bulk attribution write ============


def upsert_attributions_bulk(
    agent_id: str,
    component_id: str,
    attributions: list[dict],
) -> dict:
    """Atomic-with-pre-validation bulk upsert of N attributions on
    THIS SME's component. Round 2 #3 of token optimisation.

    All rows target the same component_id (caller's component). Each row
    has independent (plane, resource_type, identifier) — collisions on the
    global UNIQUE key are upserted in place if they belong to this
    component, refused (whole batch) if they belong to another.

    Pre-validate every row BEFORE opening the transaction. If any row
    fails pre-check, return per-row errors and write NOTHING (atomic).
    If all rows pass, run a single transaction with N upserts.

    Returns:
      {
        "committed": bool,
        "applied": int,
        "rows": [<full attribution row>, ...]   (only on commit)
        "errors": {<row_index>: <reason>, ...}  (only on rejection)
      }
    """
    _assert_sme(agent_id)
    if not _sme_owns_component(agent_id, component_id):
        raise ValueError(
            f"SME {agent_id} does not own component {component_id}. "
            "You can only attribute to your own component."
        )
    if not isinstance(attributions, list) or not attributions:
        raise ValueError("attributions must be a non-empty list")
    if len(attributions) > 500:
        raise ValueError("max 500 attributions per bulk call")

    # Pre-validate every row + collect cross-component conflicts.
    errors: dict[int, str] = {}
    normalized: list[dict] = []
    for i, attr in enumerate(attributions):
        if not isinstance(attr, dict):
            errors[i] = "row must be a dict"
            continue
        plane = (attr.get("plane") or "").strip()
        resource_type = (attr.get("resource_type") or "").strip()
        identifier = (attr.get("identifier") or "").strip()
        try:
            confidence = float(attr.get("confidence", 1.0))
        except (TypeError, ValueError):
            errors[i] = "confidence must be a number"
            continue
        if plane not in _VALID_ATTRIBUTION_PLANES:
            errors[i] = (
                f"Invalid plane '{plane}'. Valid: "
                f"{sorted(_VALID_ATTRIBUTION_PLANES)}"
            )
            continue
        if not resource_type:
            errors[i] = "resource_type is required"
            continue
        if not identifier:
            errors[i] = "identifier is required"
            continue
        if not (0.0 <= confidence <= 1.0):
            errors[i] = "confidence must be in [0.0, 1.0]"
            continue
        normalized.append({
            "i": i,
            "plane": plane,
            "resource_type": resource_type,
            "identifier": identifier,
            "evidence": attr.get("evidence"),
            "confidence": confidence,
            "metadata": attr.get("metadata") or {},
        })

    # Cross-component conflict detection (any row already-claimed by
    # another component → refuse the whole batch; consolidation is
    # the right path).
    if not errors:
        keys = [(n["plane"], n["resource_type"], n["identifier"])
                for n in normalized]
        # ANY-style query so this is one DB round-trip not N.
        existing = execute(
            """SELECT plane, resource_type, identifier, component_id
                 FROM attributions
                WHERE (plane, resource_type, identifier) IN (
                  SELECT * FROM UNNEST(%s::text[], %s::text[], %s::text[])
                )""",
            ([k[0] for k in keys],
             [k[1] for k in keys],
             [k[2] for k in keys]),
        )
        ex_by_key = {(r["plane"], r["resource_type"], r["identifier"]):
                     str(r["component_id"]) for r in existing}
        for n in normalized:
            ex = ex_by_key.get((n["plane"], n["resource_type"], n["identifier"]))
            if ex is not None and ex != str(component_id):
                errors[n["i"]] = (
                    f"Attribution ({n['plane']}, {n['resource_type']}, "
                    f"{n['identifier']}) already belongs to component {ex}. "
                    "Cross-component reassignment requires consolidation."
                )

    if errors:
        return {
            "committed": False,
            "applied": 0,
            "errors": errors,
        }

    # All clear → one transaction, N upserts.
    rows = []
    for n in normalized:
        vec = emb.vector_literal(emb.embed_text(
            emb.attribution_embed_text(n["resource_type"], n["identifier"])
        ))
        row = execute_returning(
            """INSERT INTO attributions
               (component_id, plane, resource_type, identifier, evidence,
                confidence, metadata, embedding, discovered_by, last_seen_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::vector, %s, now())
               ON CONFLICT (plane, resource_type, identifier) DO UPDATE
                 SET evidence = EXCLUDED.evidence,
                     confidence = EXCLUDED.confidence,
                     metadata = EXCLUDED.metadata,
                     embedding = EXCLUDED.embedding,
                     last_seen_at = now()
               RETURNING *""",
            (component_id, n["plane"], n["resource_type"], n["identifier"],
             n["evidence"], n["confidence"], json.dumps(n["metadata"]),
             vec, agent_id),
        )
        rows.append(row)
    return {
        "committed": True,
        "applied": len(rows),
        "rows": rows,
    }


# ============ create_edge ============


def create_edge(agent_id: str, edge_data: dict) -> dict:
    """Record a dependency edge from one of this SME's components to another
    component. Idempotent on (source_id, target_id, edge_type, identifier).
    """
    _assert_sme(agent_id)

    # Tolerate both str and UUID inputs from callers.
    source_id = str(edge_data.get("source_id") or "").strip()
    target_id = str(edge_data.get("target_id") or "").strip()
    edge_type = str(edge_data.get("edge_type") or "").strip()
    identifier = str(edge_data.get("identifier") or "").strip()
    source_attr_id = edge_data.get("source_attr_id")
    target_attr_id = edge_data.get("target_attr_id")
    evidence = edge_data.get("evidence") or []
    confidence = float(edge_data.get("confidence", 1.0))
    metadata = edge_data.get("metadata") or {}

    if not source_id:
        raise ValueError("source_id is required")
    if not target_id:
        raise ValueError("target_id is required")
    # Phase 7.3: self-loops permitted (cron self-trigger, recursive
    # component-level calls, service publish+consume on the same
    # topic). DB CHECK constraints + the pre-7.4.4 Python guard both
    # dropped.
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
    # Phase 3.9: columns renamed to from_component_id / to_component_id.
    # `create_edge` keeps accepting source_id/target_id in edge_data for
    # backward compat — values are written to the new columns. The newer
    # asymmetric tools (upsert_edge_catalog, upsert_edge_outbound,
    # bind_edge) target the same renamed schema. The catalog-aware
    # partial unique index (edges_bound_unique) is the ON CONFLICT
    # target since both endpoints are NOT NULL on this code path.
    row = execute_returning(
        """INSERT INTO edges
           (from_component_id, to_component_id, edge_type, identifier,
            source_attr_id, target_attr_id, evidence, confidence,
            metadata, embedding, discovered_by, last_seen_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s::jsonb, %s::vector, %s, now())
           ON CONFLICT (from_component_id, to_component_id, edge_type, identifier)
             WHERE from_component_id IS NOT NULL AND to_component_id IS NOT NULL
           DO UPDATE
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


# ============ Phase 3.9: asymmetric edge tools ============
#
# Asymmetric edge protocol (see docs/IMPLEMENTATION-PHASES.md §3.9):
#  - catalog row:  from_component_id IS NULL, callee owns
#  - bound row:    both non-null, caller owns
#  - dangling out: to_component_id IS NULL, caller owns
#
# Ownership is permanent on the from-side (or to-side when from IS NULL).
# Filling `to` on a dangling row does NOT shift ownership — caller still
# owns it.
#
# Multi-source consolidation: edges are a holistic view (unlike
# attributions which are per-plane). One SME owning a component may
# discover the same edge from multiple planes their component spans
# (code + telemetry + deploy). All sources accumulate into ONE edge
# row's metadata + confidence — never separate rows.


def upsert_edge_catalog(agent_id: str, edge_data: dict) -> dict:
    """DEPRECATED: Phase 7.4 moved catalogs from the `edges` table to a
    dedicated `catalogs` table with noun-form `kind` enum. This wrapper
    translates the old verb-form API (edge_type='calls') to the new
    one (kind='endpoint') via the canonical mapping. New code should
    call `upsert_catalog` directly.

    Required edge_data keys: to_component_id, edge_type, identifier.
    Optional: metadata, confidence.

    Returns the catalog row (NOT an edge row).
    """
    from cartograph_mcp.tools import catalogs as _cat
    to_component_id = str(edge_data.get("to_component_id") or "").strip()
    edge_type = str(edge_data.get("edge_type") or "").strip()
    identifier = str(edge_data.get("identifier") or "").strip()
    metadata = edge_data.get("metadata") or {}
    confidence = float(edge_data.get("confidence", 1.0))
    if not to_component_id:
        raise ValueError("to_component_id is required for catalog rows")
    if not identifier:
        raise ValueError("identifier is required")
    kind = _cat.edge_type_to_kind(edge_type)
    return _cat.upsert_catalog(
        agent_id, to_component_id, kind, identifier,
        metadata=metadata, confidence=confidence,
    )


def upsert_edge_outbound(agent_id: str, edge_data: dict) -> dict:
    """Caller declares an outgoing edge. to_component_id may be NULL
    (dangling) or set (bound). Writes a row owned by the caller's SME.

    Required edge_data keys: from_component_id, edge_type, identifier.
    Optional: to_component_id, metadata, confidence, source_attr_id,
    target_attr_id, evidence.

    Routing of ON CONFLICT depends on whether to_component_id is set:
      - bound:    (from, to, type, identifier) WHERE both non-null
      - dangling: (from, type, identifier)     WHERE to IS NULL

    Multi-source: subsequent calls accumulate metadata + take the max
    confidence (not the latest) — represents "we've seen this from
    more sources, our certainty grew."

    Scope: caller must own from_component_id.
    """
    _assert_sme(agent_id)
    # Tolerate both str and UUID inputs from callers.
    from_component_id = str(edge_data.get("from_component_id") or "").strip()
    to_raw = edge_data.get("to_component_id")
    to_component_id = str(to_raw).strip() if to_raw is not None else None
    if to_component_id == "":
        to_component_id = None
    edge_type = str(edge_data.get("edge_type") or "").strip()
    identifier = str(edge_data.get("identifier") or "").strip()
    source_attr_id = edge_data.get("source_attr_id")
    target_attr_id = edge_data.get("target_attr_id")
    evidence = edge_data.get("evidence") or []
    confidence = float(edge_data.get("confidence", 1.0))
    metadata = edge_data.get("metadata") or {}

    if not from_component_id:
        raise ValueError("from_component_id is required")
    if edge_type not in _VALID_EDGE_TYPES:
        raise ValueError(
            f"Invalid edge_type '{edge_type}'. Valid: {sorted(_VALID_EDGE_TYPES)}"
        )
    if not identifier:
        raise ValueError("identifier is required")
    if not (0.0 <= confidence <= 1.0):
        raise ValueError("confidence must be in [0.0, 1.0]")
    # Phase 7.3: self-loops permitted (see note in create_edge above).
    if not _sme_owns_component(agent_id, from_component_id):
        raise ValueError(
            f"SME {agent_id} does not own from_component_id {from_component_id}. "
            "You can only write outbound edges from your own component."
        )

    vec = emb.vector_literal(emb.embed_text(
        emb.edge_embed_text(edge_type, identifier)
    ))

    if to_component_id is None:
        # Dangling outgoing path. ON CONFLICT on (from, type, identifier)
        # WHERE to IS NULL.
        row = execute_returning(
            """INSERT INTO edges
               (from_component_id, to_component_id, edge_type, identifier,
                source_attr_id, target_attr_id, evidence, confidence,
                metadata, embedding, discovered_by, last_seen_at)
               VALUES (%s, NULL, %s, %s, %s, %s, %s::jsonb, %s,
                       %s::jsonb, %s::vector, %s, now())
               ON CONFLICT (from_component_id, edge_type, identifier)
                 WHERE to_component_id IS NULL
               DO UPDATE
                 SET metadata = edges.metadata || EXCLUDED.metadata,
                     confidence = GREATEST(edges.confidence, EXCLUDED.confidence),
                     source_attr_id = COALESCE(EXCLUDED.source_attr_id, edges.source_attr_id),
                     target_attr_id = COALESCE(EXCLUDED.target_attr_id, edges.target_attr_id),
                     embedding = EXCLUDED.embedding,
                     last_seen_at = now()
               RETURNING *""",
            (
                from_component_id, edge_type, identifier,
                source_attr_id, target_attr_id,
                json.dumps(evidence), confidence, json.dumps(metadata),
                vec, agent_id,
            ),
        )
    else:
        # Bound path. ON CONFLICT on (from, to, type, identifier).
        row = execute_returning(
            """INSERT INTO edges
               (from_component_id, to_component_id, edge_type, identifier,
                source_attr_id, target_attr_id, evidence, confidence,
                metadata, embedding, discovered_by, last_seen_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s,
                       %s::jsonb, %s::vector, %s, now())
               ON CONFLICT (from_component_id, to_component_id, edge_type, identifier)
                 WHERE from_component_id IS NOT NULL AND to_component_id IS NOT NULL
               DO UPDATE
                 SET metadata = edges.metadata || EXCLUDED.metadata,
                     confidence = GREATEST(edges.confidence, EXCLUDED.confidence),
                     source_attr_id = COALESCE(EXCLUDED.source_attr_id, edges.source_attr_id),
                     target_attr_id = COALESCE(EXCLUDED.target_attr_id, edges.target_attr_id),
                     embedding = EXCLUDED.embedding,
                     last_seen_at = now()
               RETURNING *""",
            (
                from_component_id, to_component_id, edge_type, identifier,
                source_attr_id, target_attr_id,
                json.dumps(evidence), confidence, json.dumps(metadata),
                vec, agent_id,
            ),
        )
    return row


def bind_edge(agent_id: str, edge_id: str, to_component_id: str) -> dict:
    """Resolve a dangling outgoing edge to a target component.
    Sets to_component_id on a row that previously had to=NULL.

    Refuses if a bound row with the resulting (from, to, type, identifier)
    already exists — caller can choose to merge metadata into the existing
    row + delete the dangling, or rename the dangling identifier. We
    don't silently merge.

    Scope: caller must own the edge's from_component_id (which is also
    the row's owner).
    """
    _assert_sme(agent_id)
    edge_id = str(edge_id or "").strip()
    to_component_id = str(to_component_id or "").strip()
    if not edge_id:
        raise ValueError("edge_id is required")
    if not to_component_id:
        raise ValueError("to_component_id is required")

    edge = execute_one(
        "SELECT * FROM edges WHERE id = %s",
        (edge_id,),
    )
    if edge is None:
        raise ValueError(f"Edge {edge_id} not found")
    # Catalog row check first — a catalog row has from=NULL AND to=set,
    # so the "already bound" message would be misleading.
    if edge["from_component_id"] is None:
        raise ValueError(
            f"Edge {edge_id} is a catalog row (from IS NULL); cannot be bound."
        )
    if edge["to_component_id"] is not None:
        raise ValueError(
            f"Edge {edge_id} is already bound to {edge['to_component_id']}; "
            "bind_edge only resolves dangling rows (to IS NULL)."
        )
    if not _sme_owns_component(agent_id, str(edge["from_component_id"])):
        raise ValueError(
            f"SME {agent_id} does not own from_component_id "
            f"{edge['from_component_id']}; cannot bind."
        )
    # Phase 7.3: self-loops permitted (see note in create_edge above).
    # Refuse if a bound row already covers this (from, to, type, identifier).
    collision = execute_one(
        """SELECT id FROM edges
           WHERE from_component_id = %s AND to_component_id = %s
             AND edge_type = %s AND identifier = %s
             AND id <> %s""",
        (edge["from_component_id"], to_component_id,
         edge["edge_type"], edge["identifier"], edge_id),
    )
    if collision is not None:
        raise ValueError(
            f"A bound edge with the same (from, to, type, identifier) already "
            f"exists at {collision['id']}. Either merge metadata into that "
            "row + delete this dangling one, or rename the identifier."
        )

    row = execute_returning(
        """UPDATE edges
           SET to_component_id = %s, last_seen_at = now()
           WHERE id = %s
           RETURNING *""",
        (to_component_id, edge_id),
    )
    return row


# ============ Phase 7.4.12: bulk outbound edges ============


def upsert_edges_outbound_bulk(
    agent_id: str,
    edges: list[dict],
) -> dict:
    """Atomic-with-pre-validation bulk upsert of N outbound edges from
    THIS SME's components. Round 2 #3 of token optimisation.

    Each row mirrors `upsert_edge_outbound`'s edge_data shape:
      {from_component_id, to_component_id?, edge_type, identifier,
       metadata?, confidence?, source_attr_id?, target_attr_id?,
       evidence?}.

    Multiple from_component_ids allowed in one batch — caller must own
    EACH of them via RCA. Pre-validate all rows; if any fails, return
    per-row errors and write nothing. If all pass, single transaction.
    Self-loops (from = to) allowed (Phase 7.3).

    Returns:
      {"committed": bool, "applied": int, "rows": [...] | "errors": {i: reason}}
    """
    _assert_sme(agent_id)
    if not isinstance(edges, list) or not edges:
        raise ValueError("edges must be a non-empty list")
    if len(edges) > 500:
        raise ValueError("max 500 edges per bulk call")

    errors: dict[int, str] = {}
    normalized: list[dict] = []
    seen_from: set[str] = set()  # components we've already verified ownership of
    for i, e in enumerate(edges):
        if not isinstance(e, dict):
            errors[i] = "row must be a dict"
            continue
        from_id = str(e.get("from_component_id") or "").strip()
        to_raw = e.get("to_component_id")
        to_id = str(to_raw).strip() if to_raw is not None else None
        if to_id == "":
            to_id = None
        edge_type = str(e.get("edge_type") or "").strip()
        identifier = str(e.get("identifier") or "").strip()
        try:
            confidence = float(e.get("confidence", 1.0))
        except (TypeError, ValueError):
            errors[i] = "confidence must be a number"
            continue
        if not from_id:
            errors[i] = "from_component_id is required"
            continue
        if edge_type not in _VALID_EDGE_TYPES:
            errors[i] = f"Invalid edge_type '{edge_type}'. Valid: {sorted(_VALID_EDGE_TYPES)}"
            continue
        if not identifier:
            errors[i] = "identifier is required"
            continue
        if not (0.0 <= confidence <= 1.0):
            errors[i] = "confidence must be in [0.0, 1.0]"
            continue
        # Verify ownership once per distinct from_id.
        if from_id not in seen_from:
            if not _sme_owns_component(agent_id, from_id):
                errors[i] = (
                    f"SME {agent_id} does not own from_component_id {from_id}"
                )
                continue
            seen_from.add(from_id)
        normalized.append({
            "i": i,
            "from_id": from_id,
            "to_id": to_id,
            "edge_type": edge_type,
            "identifier": identifier,
            "source_attr_id": e.get("source_attr_id"),
            "target_attr_id": e.get("target_attr_id"),
            "evidence": e.get("evidence") or [],
            "confidence": confidence,
            "metadata": e.get("metadata") or {},
        })

    if errors:
        return {"committed": False, "applied": 0, "errors": errors}

    rows = []
    for n in normalized:
        vec = emb.vector_literal(emb.embed_text(
            emb.edge_embed_text(n["edge_type"], n["identifier"])
        ))
        if n["to_id"] is None:
            row = execute_returning(
                """INSERT INTO edges
                   (from_component_id, to_component_id, edge_type, identifier,
                    source_attr_id, target_attr_id, evidence, confidence,
                    metadata, embedding, discovered_by, last_seen_at)
                   VALUES (%s, NULL, %s, %s, %s, %s, %s::jsonb, %s,
                           %s::jsonb, %s::vector, %s, now())
                   ON CONFLICT (from_component_id, edge_type, identifier)
                     WHERE to_component_id IS NULL
                   DO UPDATE
                     SET metadata = edges.metadata || EXCLUDED.metadata,
                         confidence = GREATEST(edges.confidence, EXCLUDED.confidence),
                         source_attr_id = COALESCE(EXCLUDED.source_attr_id, edges.source_attr_id),
                         target_attr_id = COALESCE(EXCLUDED.target_attr_id, edges.target_attr_id),
                         embedding = EXCLUDED.embedding,
                         last_seen_at = now()
                   RETURNING *""",
                (n["from_id"], n["edge_type"], n["identifier"],
                 n["source_attr_id"], n["target_attr_id"],
                 json.dumps(n["evidence"]), n["confidence"],
                 json.dumps(n["metadata"]), vec, agent_id),
            )
        else:
            row = execute_returning(
                """INSERT INTO edges
                   (from_component_id, to_component_id, edge_type, identifier,
                    source_attr_id, target_attr_id, evidence, confidence,
                    metadata, embedding, discovered_by, last_seen_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s,
                           %s::jsonb, %s::vector, %s, now())
                   ON CONFLICT (from_component_id, to_component_id, edge_type, identifier)
                     WHERE from_component_id IS NOT NULL AND to_component_id IS NOT NULL
                   DO UPDATE
                     SET metadata = edges.metadata || EXCLUDED.metadata,
                         confidence = GREATEST(edges.confidence, EXCLUDED.confidence),
                         source_attr_id = COALESCE(EXCLUDED.source_attr_id, edges.source_attr_id),
                         target_attr_id = COALESCE(EXCLUDED.target_attr_id, edges.target_attr_id),
                         embedding = EXCLUDED.embedding,
                         last_seen_at = now()
                   RETURNING *""",
                (n["from_id"], n["to_id"], n["edge_type"], n["identifier"],
                 n["source_attr_id"], n["target_attr_id"],
                 json.dumps(n["evidence"]), n["confidence"],
                 json.dumps(n["metadata"]), vec, agent_id),
            )
        rows.append(row)
    return {"committed": True, "applied": len(rows), "rows": rows}


# ============ Phase 7.4.11: edge deletion ============


def delete_edge(agent_id: str, edge_id: str) -> dict:
    """Owner-scoped, idempotent edge delete.

    Closes the gap where an SME ends up with two edges for the same
    logical dependency (e.g. one telemetry-discovered + one github-
    discovered with slightly different identifiers, post-merge) and
    needs to consolidate them into one canonical row. Without this
    tool the only options were leaving a stale duplicate or marking
    it via metadata.superseded_by_edge_id.

    Authorisation:
      - Bound + dangling rows: caller must own from_component_id
        (the row's caller-side owner).
      - Catalog rows (from_component_id IS NULL): callee-owned; not
        deletable via this tool — use upsert_catalog deletion path
        when that ships, or decommission_component for a teardown.
        Refuses with a clear message.

    Cascade behaviour:
      - flows.outgoing_edge_id has ON DELETE CASCADE — any flows
        anchored on this edge are removed atomically.
      - source_attr_id / target_attr_id on attributions are NOT
        affected (those are evidence pointers, edge id reference
        is set to NULL on delete via FK ON DELETE SET NULL).

    Idempotent: deleting a non-existent edge id returns
    {"deleted": False, "edge_id": <id>, "reason": "not_found"}.

    Returns:
      {"deleted": True,  "edge_id": <id>, "cascaded_flows": <int>}
      {"deleted": False, "edge_id": <id>, "reason": "not_found" | "catalog_not_supported"}
    """
    _assert_sme(agent_id)
    edge_id_s = str(edge_id or "").strip()
    if not edge_id_s:
        raise ValueError("edge_id is required")

    edge = execute_one(
        """SELECT id, from_component_id, to_component_id, edge_type, identifier
             FROM edges WHERE id = %s::uuid""",
        (edge_id_s,),
    )
    if edge is None:
        return {"deleted": False, "edge_id": edge_id_s, "reason": "not_found"}

    # Catalog rows live in the catalogs table now (Phase 7.4); any
    # remaining edges row with from_component_id IS NULL is a pre-7.4
    # remnant and should be cleaned up via migration, not via this tool.
    if edge["from_component_id"] is None:
        return {
            "deleted": False,
            "edge_id": edge_id_s,
            "reason": "catalog_not_supported",
        }

    if not _sme_owns_component(agent_id, str(edge["from_component_id"])):
        raise ValueError(
            f"SME {agent_id} does not own from_component_id "
            f"{edge['from_component_id']}. delete_edge is gated on the "
            "caller-side owner."
        )

    # Count cascading flows for the response shape (purely informative;
    # the FK cascade does the actual delete).
    flow_count_row = execute_one(
        "SELECT COUNT(*) AS n FROM flows WHERE outgoing_edge_id = %s::uuid",
        (edge_id_s,),
    )
    cascaded_flows = int(flow_count_row["n"]) if flow_count_row else 0

    execute_mutate(
        "DELETE FROM edges WHERE id = %s::uuid",
        (edge_id_s,),
    )
    return {
        "deleted": True,
        "edge_id": edge_id_s,
        "cascaded_flows": cascaded_flows,
    }


# ============ Phase 3.9: flows ============


def upsert_flow(
    agent_id: str,
    component_id: str,
    incoming_catalog_id: str,
    outgoing_edge_id: str,
    metadata: dict | None = None,
    confidence: float = 1.0,
) -> dict:
    """Link an incoming catalog (the surface the component exposes) to
    an outgoing edge inside the component. Set-based (many-to-many):
    one catalog can fan out to multiple outgoings; multiple catalogs
    can share an outgoing.

    Phase 7.4.2: incoming is canonically a catalog row (Phase 7.4 moved
    catalogs out of `edges`). Pre-7.4 the parameter was `incoming_edge_id`
    pointing at a catalog-shaped edge; the role is unchanged, only the
    table reference is.

    Validates:
      - SME owns component_id via RCA
      - catalog row exists at incoming_catalog_id
      - catalog.component_id == component_id
      - outgoing_edge exists, outgoing.from_component_id == component_id

    Idempotent on (component_id, incoming_catalog_id, outgoing_edge_id).
    Re-call accumulates metadata + max confidence.
    """
    _assert_sme(agent_id)
    component_id = str(component_id or "").strip()
    incoming_catalog_id = str(incoming_catalog_id or "").strip()
    outgoing_edge_id = str(outgoing_edge_id or "").strip()
    if not component_id or not incoming_catalog_id or not outgoing_edge_id:
        raise ValueError(
            "component_id, incoming_catalog_id, outgoing_edge_id required"
        )
    if not (0.0 <= float(confidence) <= 1.0):
        raise ValueError("confidence must be in [0.0, 1.0]")
    if not _sme_owns_component(agent_id, component_id):
        raise ValueError(
            f"SME {agent_id} does not own component {component_id}; "
            "cannot record flows on it."
        )

    catalog = execute_one(
        "SELECT id, component_id FROM catalogs WHERE id = %s::uuid",
        (incoming_catalog_id,),
    )
    if catalog is None:
        raise ValueError(f"Incoming catalog {incoming_catalog_id} not found")
    if str(catalog["component_id"]) != str(component_id):
        raise ValueError(
            f"Incoming catalog {incoming_catalog_id} does not belong to component "
            f"{component_id} (it belongs to {catalog['component_id']})."
        )

    outgoing = execute_one(
        "SELECT id, from_component_id FROM edges WHERE id = %s",
        (outgoing_edge_id,),
    )
    if outgoing is None:
        raise ValueError(f"Outgoing edge {outgoing_edge_id} not found")
    if str(outgoing["from_component_id"] or "") != str(component_id):
        raise ValueError(
            f"Outgoing edge {outgoing_edge_id} does not originate from component "
            f"{component_id} (its from_component_id is {outgoing['from_component_id']})."
        )

    metadata = metadata or {}
    row = execute_returning(
        """INSERT INTO flows
           (component_id, incoming_catalog_id, outgoing_edge_id,
            confidence, metadata, discovered_by)
           VALUES (%s::uuid, %s::uuid, %s::uuid, %s, %s::jsonb, %s)
           ON CONFLICT (component_id, incoming_catalog_id, outgoing_edge_id)
           DO UPDATE
             SET metadata = flows.metadata || EXCLUDED.metadata,
                 confidence = GREATEST(flows.confidence, EXCLUDED.confidence),
                 updated_at = now()
           RETURNING *""",
        (component_id, incoming_catalog_id, outgoing_edge_id,
         float(confidence), json.dumps(metadata), agent_id),
    )
    return row


def get_flow(agent_id: str, component_id: str, incoming_catalog_id: str) -> list[dict]:
    """All outgoing edges fired when the catalog at `incoming_catalog_id`
    on `component_id` is hit. Open to all active agents."""
    _caller(agent_id)
    return execute(
        """SELECT e.* FROM flows f
           JOIN edges e ON e.id = f.outgoing_edge_id
           WHERE f.component_id = %s::uuid
             AND f.incoming_catalog_id = %s::uuid
           ORDER BY e.edge_type, e.identifier""",
        (component_id, incoming_catalog_id),
    )


def get_flow_inverse(agent_id: str, component_id: str, outgoing_edge_id: str) -> list[dict]:
    """All incoming CATALOGS whose hit triggers `outgoing_edge_id` on
    `component_id` (reverse lookup of get_flow). Returns catalog rows
    (Phase 7.4.2: incoming is a catalog, not an edge). Open to all
    active agents."""
    _caller(agent_id)
    return execute(
        """SELECT c.* FROM flows f
           JOIN catalogs c ON c.id = f.incoming_catalog_id
           WHERE f.component_id = %s::uuid
             AND f.outgoing_edge_id = %s::uuid
           ORDER BY c.kind, c.identifier""",
        (component_id, outgoing_edge_id),
    )


def get_component_edges(agent_id: str, component_id: str) -> dict:
    """Categorised view of all edges touching `component_id`. Replaces
    the simpler {outbound, inbound} shape from get_edges with a
    four-way split that surfaces catalog rows + dangling outgoings.

    Returns:
      {
        incoming_bound:   edges where to=component_id, from non-null,
        incoming_catalog: edges where to=component_id, from IS NULL
                          (this component's own catalog),
        outgoing_bound:   edges where from=component_id, to non-null,
        outgoing_dangling: edges where from=component_id, to IS NULL,
      }

    Open to all active agents.
    """
    _caller(agent_id)
    incoming_bound = execute(
        """SELECT * FROM edges
           WHERE to_component_id = %s AND from_component_id IS NOT NULL
           ORDER BY edge_type, identifier""",
        (component_id,),
    )
    # Phase 7.4: catalog rows now live in the dedicated `catalogs`
    # table. We reshape them into the legacy edge-row format (with
    # from_component_id=NULL + a derived edge_type) so existing FE +
    # tests consuming get_component_edges keep working unchanged.
    _catalog_rows = execute(
        """SELECT id, component_id AS to_component_id, kind, identifier,
                  metadata, confidence, embedding, discovered_by,
                  created_at, updated_at AS last_seen_at
           FROM catalogs
           WHERE component_id = %s::uuid
           ORDER BY kind, identifier""",
        (component_id,),
    )
    _KIND_TO_DEFAULT_EDGE_TYPE = {
        "endpoint": "calls",
        "topic": "publishes_to",
        "queue": "consumes_from",
        "data_source": "reads_from",
        "trigger_target": "triggers",
    }
    incoming_catalog = []
    for r in _catalog_rows:
        incoming_catalog.append({
            **r,
            "from_component_id": None,
            "edge_type": _KIND_TO_DEFAULT_EDGE_TYPE.get(r["kind"], "calls"),
        })
    outgoing_bound = execute(
        """SELECT * FROM edges
           WHERE from_component_id = %s AND to_component_id IS NOT NULL
           ORDER BY edge_type, identifier""",
        (component_id,),
    )
    outgoing_dangling = execute(
        """SELECT * FROM edges
           WHERE from_component_id = %s AND to_component_id IS NULL
           ORDER BY edge_type, identifier""",
        (component_id,),
    )
    return {
        "incoming_bound": incoming_bound,
        "incoming_catalog": incoming_catalog,
        "outgoing_bound": outgoing_bound,
        "outgoing_dangling": outgoing_dangling,
    }


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
    """Phase 3.9 backward-compat shape: returns {outbound, inbound} where
    outbound = rows with from_component_id=this AND to non-null,
    inbound  = rows with to_component_id=this AND from non-null.
    Catalog rows (from IS NULL) and dangling outgoings (to IS NULL) are
    NOT surfaced here — use get_component_edges (Phase 3.9) for the
    richer categorised view.
    """
    _caller(agent_id)
    outbound = execute(
        """SELECT * FROM edges
           WHERE from_component_id = %s AND to_component_id IS NOT NULL
           ORDER BY edge_type, identifier""",
        (component_id,),
    )
    inbound = execute(
        """SELECT * FROM edges
           WHERE to_component_id = %s AND from_component_id IS NOT NULL
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


# ---------- Phase 4.1 hygiene: surface stale refs on this SME's edges ----------


def get_my_components(agent_id: str) -> list[dict]:
    """Phase 4.1. Return all active components this agent owns via RCA.
    Critical for split-spawned children that don't know their component_id
    on first wake (welcome task also carries it, but this is the
    belt-and-suspenders path). Also useful for SME self-inspection.

    Returns one row per component owned by the agent with: id,
    canonical_name, display_name, component_type, status, source_slice,
    split_briefing, split_from_component_id."""
    _caller(agent_id)
    return execute(
        """SELECT DISTINCT c.id, c.canonical_name, c.display_name,
                  c.component_type, c.status, c.source_slice,
                  c.split_briefing, c.split_from_component_id,
                  c.component_doc_md, c.created_at, c.updated_at
           FROM resource_component_agents rca
           JOIN components c ON c.id = rca.component_id
           WHERE rca.agent_id = %s AND c.status != 'decommissioned'
           ORDER BY c.created_at""",
        (agent_id,),
    )


def get_stale_edges(agent_id: str) -> list[dict]:
    """Return edges owned by this SME's component where the OTHER
    endpoint's component is decommissioned. "Owned" = either the edge's
    from_component_id OR to_component_id matches the caller's component.

    Each row includes `stale_component_merged_into_agent_id` when the
    dead component was absorbed (so the caller can re-bind to the
    survivor via bind_edge). Null survivor = component was
    decommissioned outright (e.g. via decommission_component).

    Hygiene-cycle tool — SMEs call this every few wakes to surface
    orphan edges created when upstream mutations skipped the
    transfer_edges step or when a related component was decommissioned
    directly.
    """
    _caller(agent_id)
    my_comp = execute_one(
        """SELECT rca.component_id
           FROM resource_component_agents rca
           JOIN components c ON c.id = rca.component_id
           WHERE rca.agent_id = %s AND c.status != 'decommissioned'
           LIMIT 1""",
        (agent_id,),
    )
    if my_comp is None:
        return []
    my_comp_id = str(my_comp["component_id"])

    # `kind` computed inline (not a physical column).
    # `my_side` = which column holds MY component (from|to).
    # Survivor lookup: after absorb, RCA on the dead component gets
    # re-pointed to the survivor, so joining components → RCA → agent_runs
    # lands on the survivor (merged_into=NULL). Instead, find the merge
    # consolidation whose component_b_id = the dead component, then look
    # up agent_b_id's merged_into_agent_id. Works for absorb-produced
    # decommissioned components; raw decommission_component leaves it NULL.
    rows = execute(
        """SELECT
               e.id AS edge_id,
               CASE
                 WHEN e.from_component_id IS NULL THEN 'catalog'
                 WHEN e.to_component_id IS NULL THEN 'dangling'
                 ELSE 'bound'
               END AS kind,
               e.edge_type, e.identifier,
               e.from_component_id, e.to_component_id,
               CASE WHEN e.from_component_id = %s THEN 'from'
                    ELSE 'to' END AS my_side,
               CASE WHEN e.from_component_id = %s THEN e.to_component_id
                    ELSE e.from_component_id END AS stale_component_id,
               c.canonical_name AS stale_component_canonical,
               c.display_name   AS stale_component_display,
               ar_b.merged_into_agent_id
                   AS stale_component_merged_into_agent_id,
               c.status AS stale_component_status
           FROM edges e
           JOIN components c ON c.id =
               CASE WHEN e.from_component_id = %s THEN e.to_component_id
                    ELSE e.from_component_id END
           LEFT JOIN consolidations cons
               ON cons.component_b_id = c.id
              AND cons.nomination_type = 'merge'
           LEFT JOIN agent_runs ar_b ON ar_b.agent_id = cons.agent_b_id
           WHERE (e.from_component_id = %s OR e.to_component_id = %s)
             AND c.status = 'decommissioned'
           ORDER BY e.edge_type, e.identifier""",
        (my_comp_id, my_comp_id, my_comp_id, my_comp_id, my_comp_id),
    )
    # Suggested action string. Resolver-worthy annotations; caller agent
    # interprets based on my_side + stale_component_merged_into_agent_id.
    for r in rows:
        if r["stale_component_merged_into_agent_id"]:
            r["suggested_action"] = "re-bind-to-survivor"
        else:
            r["suggested_action"] = "target-gone-accept-or-escalate"
    return rows


def get_stale_flows(agent_id: str) -> list[dict]:
    """Return flows on this SME's component where the OUTGOING edge's
    target component is decommissioned. Tells the caller which flow
    rows fan out into a dead component so they can rewire or delete.

    Phase 7.4.2: incoming is a catalog row owned by the same component
    as the flow, so it can never have a "dead counterparty" — catalogs
    cascade-delete with their owner. Only the outgoing-side staleness
    matters now.
    """
    _caller(agent_id)
    my_comp = execute_one(
        """SELECT rca.component_id
           FROM resource_component_agents rca
           JOIN components c ON c.id = rca.component_id
           WHERE rca.agent_id = %s AND c.status != 'decommissioned'
           LIMIT 1""",
        (agent_id,),
    )
    if my_comp is None:
        return []
    my_comp_id = str(my_comp["component_id"])

    return execute(
        """SELECT
               f.id AS flow_id, f.component_id,
               f.incoming_catalog_id, f.outgoing_edge_id,
               cin.kind AS incoming_kind,
               cin.identifier AS incoming_identifier,
               CASE
                 WHEN eout.to_component_id IS NULL THEN 'dangling'
                 ELSE 'bound'
               END AS outgoing_kind,
               eout.edge_type AS outgoing_edge_type,
               eout.identifier AS outgoing_identifier,
               cout.status AS outgoing_other_status
           FROM flows f
           JOIN catalogs cin ON cin.id = f.incoming_catalog_id
           JOIN edges eout   ON eout.id = f.outgoing_edge_id
           LEFT JOIN components cout ON cout.id = eout.to_component_id
           WHERE f.component_id = %s
             AND cout.status = 'decommissioned'
           ORDER BY f.updated_at DESC""",
        (my_comp_id,),
    )

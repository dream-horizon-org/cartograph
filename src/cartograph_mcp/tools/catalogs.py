"""Phase 7.4: catalogs as a first-class table.

Catalog rows used to live in `edges` with `from_component_id IS NULL`
borrowing the verb-form `edge_type` to describe what was being
EXPOSED. Awkward grammatically. Now they live in a dedicated
`catalogs` table with noun-form `kind` enum.

Tools:
- upsert_catalog        — owner declares an exposed thing
- get_my_catalogs       — list catalogs I own
- get_my_catalog_callers — for each catalog, list bound callers
- get_unmatched_callers  — bound edges to me with no matching catalog
- get_orphan_catalogs    — catalogs I own with no bound caller

Bridging: catalog `kind` ↔ caller `edge_type` matching is via
`_KIND_TO_EDGE_TYPES`. A bound `calls /x` to component B matches B's
catalog row `(kind='endpoint', identifier='/x')`.
"""

import json

from shared.actor_auth import require_active_agent
from shared.db import execute, execute_one, execute_returning, execute_mutate
from shared import embedding as _emb


_VALID_KINDS = {"endpoint", "topic", "queue", "data_source", "trigger_target"}

# Catalog kind → set of bound edge_types that match it. Used by
# get_my_catalog_callers + get_unmatched_callers to bridge across
# the noun/verb taxonomies.
_KIND_TO_EDGE_TYPES = {
    "endpoint":       {"calls"},
    "topic":          {"publishes_to", "consumes_from"},
    "queue":          {"publishes_to", "consumes_from"},
    "data_source":    {"reads_from", "writes_to"},
    "trigger_target": {"triggers"},
}

# Reverse map: edge_type → its canonical catalog kind (used by the
# Phase 3.9 → 7.4 backwards-compat shim and the /api/graph payload
# transformation).
_EDGE_TYPE_TO_KIND = {
    "calls":         "endpoint",
    "reads_from":    "data_source",
    "writes_to":     "data_source",
    "publishes_to":  "topic",
    "consumes_from": "queue",
    "triggers":      "trigger_target",
    "runs_on":       "data_source",
}


def _agent_owns_component(agent_id: str, component_id: str) -> bool:
    row = execute_one(
        """SELECT 1 FROM resource_component_agents
           WHERE agent_id = %s AND component_id = %s::uuid LIMIT 1""",
        (agent_id, component_id),
    )
    return row is not None


def upsert_catalog(
    agent_id: str,
    component_id: str,
    kind: str,
    identifier: str,
    metadata: dict | None = None,
    confidence: float = 1.0,
) -> dict:
    """Owner-side catalog declaration.

    Validates: caller is an active agent, kind ∈ _VALID_KINDS, caller
    owns component_id via RCA. Idempotent on (component_id, kind,
    identifier) with metadata merge + confidence max.

    Returns the upserted row.
    """
    require_active_agent(agent_id)
    if kind not in _VALID_KINDS:
        raise ValueError(
            f"Invalid kind '{kind}'. Allowed: {sorted(_VALID_KINDS)}"
        )
    if not _agent_owns_component(agent_id, component_id):
        raise ValueError(
            f"Agent {agent_id} does not own component {component_id}"
        )
    md_json = json.dumps(metadata or {})
    embed_text = f"{kind}: {identifier}"
    embed = _emb.embed_text(embed_text)
    embed_str = "[" + ",".join(map(str, embed)) + "]" if embed else None
    return execute_returning(
        """INSERT INTO catalogs
              (component_id, kind, identifier, metadata, confidence, embedding, discovered_by)
           VALUES (%s::uuid, %s, %s, %s::jsonb, %s, %s::vector, %s)
           ON CONFLICT (component_id, kind, identifier) DO UPDATE
              SET metadata = catalogs.metadata || EXCLUDED.metadata,
                  confidence = GREATEST(catalogs.confidence, EXCLUDED.confidence),
                  updated_at = now()
           RETURNING *""",
        (component_id, kind, identifier, md_json, confidence, embed_str, agent_id),
    )


def upsert_catalogs_bulk(
    agent_id: str,
    component_id: str,
    catalogs: list[dict],
) -> dict:
    """Atomic-with-pre-validation bulk upsert of N catalogs on THIS
    SME's component. Round 2 #3 of token optimisation.

    Each row: {kind, identifier, metadata?, confidence?}. Pre-validate
    all rows; if any fails, return per-row errors and write nothing.
    If all pass, run a single transaction with N upserts.

    Returns:
      {"committed": bool, "applied": int, "rows": [...] | "errors": {i: reason}}
    """
    require_active_agent(agent_id)
    if not _agent_owns_component(agent_id, component_id):
        raise ValueError(
            f"Agent {agent_id} does not own component {component_id}"
        )
    if not isinstance(catalogs, list) or not catalogs:
        raise ValueError("catalogs must be a non-empty list")
    if len(catalogs) > 500:
        raise ValueError("max 500 catalogs per bulk call")

    errors: dict[int, str] = {}
    normalized: list[dict] = []
    for i, c in enumerate(catalogs):
        if not isinstance(c, dict):
            errors[i] = "row must be a dict"
            continue
        kind = (c.get("kind") or "").strip()
        identifier = (c.get("identifier") or "").strip()
        try:
            confidence = float(c.get("confidence", 1.0))
        except (TypeError, ValueError):
            errors[i] = "confidence must be a number"
            continue
        if kind not in _VALID_KINDS:
            errors[i] = f"Invalid kind '{kind}'. Allowed: {sorted(_VALID_KINDS)}"
            continue
        if not identifier:
            errors[i] = "identifier is required"
            continue
        if not (0.0 <= confidence <= 1.0):
            errors[i] = "confidence must be in [0.0, 1.0]"
            continue
        normalized.append({
            "i": i,
            "kind": kind,
            "identifier": identifier,
            "metadata": c.get("metadata") or {},
            "confidence": confidence,
        })

    if errors:
        return {"committed": False, "applied": 0, "errors": errors}

    rows = []
    for n in normalized:
        md_json = json.dumps(n["metadata"])
        embed_text = f"{n['kind']}: {n['identifier']}"
        embed = _emb.embed_text(embed_text)
        embed_str = "[" + ",".join(map(str, embed)) + "]" if embed else None
        row = execute_returning(
            """INSERT INTO catalogs
                  (component_id, kind, identifier, metadata, confidence, embedding, discovered_by)
               VALUES (%s::uuid, %s, %s, %s::jsonb, %s, %s::vector, %s)
               ON CONFLICT (component_id, kind, identifier) DO UPDATE
                  SET metadata = catalogs.metadata || EXCLUDED.metadata,
                      confidence = GREATEST(catalogs.confidence, EXCLUDED.confidence),
                      updated_at = now()
               RETURNING *""",
            (component_id, n["kind"], n["identifier"], md_json,
             n["confidence"], embed_str, agent_id),
        )
        rows.append(row)
    return {"committed": True, "applied": len(rows), "rows": rows}


def get_my_catalogs(agent_id: str) -> list[dict]:
    """Catalog rows for components owned by this agent. Includes a
    `caller_count` field so the caller can spot orphans at a glance.

    Phase 7.4.4: switched from `JOIN resource_component_agents rca` to
    `EXISTS (...)` to avoid the post-absorb duplication bug. After
    `absorb_agent` re-points target's RCA rows to the survivor, the
    survivor can have N RCA rows pointing at the same component (one
    per inherited resource). The old JOIN returned one catalog row per
    matching RCA row; EXISTS short-circuits, so each catalog appears
    exactly once. DEMO7 sme-f9bde48a saw 12 rows for 6 distinct
    catalogs post-merge — this is that fix.
    """
    require_active_agent(agent_id)
    return execute(
        """
        SELECT c.*,
               COALESCE(
                 (SELECT COUNT(*) FROM edges e
                   WHERE e.to_component_id = c.component_id
                     AND e.identifier = c.identifier
                     AND e.from_component_id IS NOT NULL
                     AND e.edge_type = ANY(
                       CASE c.kind
                         WHEN 'endpoint'       THEN ARRAY['calls']
                         WHEN 'topic'          THEN ARRAY['publishes_to','consumes_from']
                         WHEN 'queue'          THEN ARRAY['publishes_to','consumes_from']
                         WHEN 'data_source'    THEN ARRAY['reads_from','writes_to']
                         WHEN 'trigger_target' THEN ARRAY['triggers']
                       END)),
                 0
               ) AS caller_count
          FROM catalogs c
         WHERE EXISTS (
           SELECT 1 FROM resource_component_agents rca
            WHERE rca.component_id = c.component_id
              AND rca.agent_id = %s
         )
         ORDER BY c.kind, c.identifier
        """,
        (agent_id,),
    )


def get_my_catalog_callers(agent_id: str, catalog_id: str | None = None) -> dict:
    """For each of agent's catalogs, list the bound edges that match
    via the kind ↔ edge_type bridging. Returns dict keyed by
    catalog_id → list of {caller_id (source), edge_id, edge_type,
    identifier}. Pass catalog_id to filter to one row."""
    require_active_agent(agent_id)
    # Phase 7.4.4: EXISTS instead of JOIN on RCA — see get_my_catalogs note.
    where_extra = "AND c.id = %s::uuid" if catalog_id else ""
    params = [agent_id] + ([catalog_id] if catalog_id else [])
    rows = execute(
        f"""
        SELECT c.id AS catalog_id, c.kind, c.identifier AS catalog_identifier,
               e.id AS edge_id, e.from_component_id AS caller_id,
               e.edge_type, e.identifier AS edge_identifier
          FROM catalogs c
          JOIN edges e
            ON e.to_component_id = c.component_id
           AND e.identifier = c.identifier
           AND e.from_component_id IS NOT NULL
           AND e.edge_type = ANY(
             CASE c.kind
               WHEN 'endpoint'       THEN ARRAY['calls']
               WHEN 'topic'          THEN ARRAY['publishes_to','consumes_from']
               WHEN 'queue'          THEN ARRAY['publishes_to','consumes_from']
               WHEN 'data_source'    THEN ARRAY['reads_from','writes_to']
               WHEN 'trigger_target' THEN ARRAY['triggers']
             END)
         WHERE EXISTS (
           SELECT 1 FROM resource_component_agents rca
            WHERE rca.component_id = c.component_id
              AND rca.agent_id = %s
         ) {where_extra}
        """,
        params,
    )
    grouped: dict[str, list[dict]] = {}
    for r in rows:
        cid = str(r["catalog_id"])
        grouped.setdefault(cid, []).append({
            "caller_id": r["caller_id"],
            "edge_id": r["edge_id"],
            "edge_type": r["edge_type"],
            "identifier": r["edge_identifier"],
        })
    return grouped


def get_unmatched_callers(agent_id: str) -> list[dict]:
    """Bound edges into agent's components that have no matching
    catalog row. SME triages each:
      - dynamic identifier (DB-like, ignore)
      - missing catalog row (call upsert_catalog)
      - caller error (raise clarification)
    """
    # Phase 7.4.4: EXISTS instead of JOIN on RCA — see get_my_catalogs note.
    require_active_agent(agent_id)
    return execute(
        """
        SELECT e.id AS edge_id, e.from_component_id AS caller_id,
               e.to_component_id AS target_component_id,
               e.edge_type, e.identifier
          FROM edges e
         WHERE EXISTS (
           SELECT 1 FROM resource_component_agents rca
            WHERE rca.component_id = e.to_component_id
              AND rca.agent_id = %s
         )
           AND e.from_component_id IS NOT NULL
           AND NOT EXISTS (
                 SELECT 1 FROM catalogs c
                  WHERE c.component_id = e.to_component_id
                    AND c.identifier = e.identifier
                    AND e.edge_type = ANY(
                      CASE c.kind
                        WHEN 'endpoint'       THEN ARRAY['calls']
                        WHEN 'topic'          THEN ARRAY['publishes_to','consumes_from']
                        WHEN 'queue'          THEN ARRAY['publishes_to','consumes_from']
                        WHEN 'data_source'    THEN ARRAY['reads_from','writes_to']
                        WHEN 'trigger_target' THEN ARRAY['triggers']
                      END)
               )
         ORDER BY e.created_at DESC
        """,
        (agent_id,),
    )


def get_orphan_catalogs(agent_id: str) -> list[dict]:
    """Catalogs owned by agent that no bound caller currently matches.
    Companion to get_unmatched_callers — together they show the
    catalog-coverage health of the agent's components."""
    # Phase 7.4.4: EXISTS instead of JOIN on RCA — see get_my_catalogs note.
    require_active_agent(agent_id)
    return execute(
        """
        SELECT c.*
          FROM catalogs c
         WHERE EXISTS (
           SELECT 1 FROM resource_component_agents rca
            WHERE rca.component_id = c.component_id
              AND rca.agent_id = %s
         )
           AND NOT EXISTS (
                 SELECT 1 FROM edges e
                  WHERE e.to_component_id = c.component_id
                    AND e.identifier = c.identifier
                    AND e.from_component_id IS NOT NULL
                    AND e.edge_type = ANY(
                      CASE c.kind
                        WHEN 'endpoint'       THEN ARRAY['calls']
                        WHEN 'topic'          THEN ARRAY['publishes_to','consumes_from']
                        WHEN 'queue'          THEN ARRAY['publishes_to','consumes_from']
                        WHEN 'data_source'    THEN ARRAY['reads_from','writes_to']
                        WHEN 'trigger_target' THEN ARRAY['triggers']
                      END)
               )
         ORDER BY c.created_at DESC
        """,
        (agent_id,),
    )


def edge_type_to_kind(edge_type: str) -> str:
    """Map a verb-form edge_type to its canonical noun catalog kind.
    Used by the Phase 3.9 → 7.4 backwards-compat shim and the
    /api/graph payload (which still surfaces catalog rows under a
    `kind='catalog'` discriminator with a derived edge_type)."""
    return _EDGE_TYPE_TO_KIND.get(edge_type, "endpoint")


# ============ Phase 8.2: corrective delete ============


def delete_catalog(
    agent_id: str, catalog_id: str, reason: str | None = None
) -> dict:
    """[Phase 8.2] Owner-scoped, idempotent catalog delete.

    Closes the gap where an SME declared a catalog row that's
    deprecated / wrong-shape and has no way to remove it. Without
    this, the only options were leaving the stale row in place or
    decommissioning the whole component.

    Authorisation: caller must own the row's component_id via RCA.

    Cascade behaviour:
      - flows.incoming_catalog_id has ON DELETE CASCADE — flows that
        anchored on this catalog row are removed atomically.
      - bound caller edges that bridged via (target, edge_type,
        identifier) are NOT FK-linked — they become unmatched on the
        next hygiene sweep and surface via get_unmatched_callers on
        the still-existing component owner.

    Idempotent: deleting a non-existent catalog_id returns
    {"deleted": False, "id": <id>, "reason": "not_found"}.

    Returns:
      {"deleted": True,  "id": <id>, "cascaded_flows": <int>}
      {"deleted": False, "id": <id>, "reason": "not_found"}

    Optional `reason` param surfaces in mcp_audit.args_hash.
    """
    _ = reason
    require_active_agent(agent_id)
    catalog_id_s = str(catalog_id or "").strip()
    if not catalog_id_s:
        raise ValueError("catalog_id is required")

    cat = execute_one(
        "SELECT id, component_id FROM catalogs WHERE id = %s::uuid",
        (catalog_id_s,),
    )
    if cat is None:
        return {"deleted": False, "id": catalog_id_s, "reason": "not_found"}

    if not _agent_owns_component(agent_id, str(cat["component_id"])):
        raise ValueError(
            f"Agent {agent_id} does not own component {cat['component_id']} "
            "(catalog belongs to a component you don't own)."
        )

    flow_count_row = execute_one(
        "SELECT COUNT(*) AS n FROM flows WHERE incoming_catalog_id = %s::uuid",
        (catalog_id_s,),
    )
    cascaded_flows = int(flow_count_row["n"]) if flow_count_row else 0

    execute_mutate(
        "DELETE FROM catalogs WHERE id = %s::uuid",
        (catalog_id_s,),
    )
    return {
        "deleted": True,
        "id": catalog_id_s,
        "cascaded_flows": cascaded_flows,
    }


def delete_catalogs_bulk(agent_id: str, catalog_ids: list) -> dict:
    """[Phase 8.3] Atomic-with-pre-validation bulk catalog delete.

    Per-row owner check via RCA. Missing ids skipped (idempotent).
    Cascades flows (FK CASCADE on flows.incoming_catalog_id). Max 500.

    Returns:
      {"committed": True, "applied": N,
       "rows": [{deleted, id, cascaded_flows}]}
      {"committed": False, "applied": 0, "errors": {<idx>: <reason>}}
    """
    require_active_agent(agent_id)
    if not isinstance(catalog_ids, list) or not catalog_ids:
        raise ValueError("catalog_ids must be a non-empty list")
    if len(catalog_ids) > 500:
        raise ValueError(f"max 500 ids per bulk call (got {len(catalog_ids)})")

    normalized: list[tuple[int, str]] = []
    errors: dict[int, str] = {}
    seen: set[str] = set()
    for i, raw in enumerate(catalog_ids):
        s = str(raw or "").strip()
        if not s:
            errors[i] = "id is required"
            continue
        if s in seen:
            errors[i] = f"duplicate id within batch: {s}"
            continue
        seen.add(s)
        normalized.append((i, s))
    if errors:
        return {"committed": False, "applied": 0, "errors": errors}

    id_strs = [n[1] for n in normalized]
    rows = execute(
        "SELECT id, component_id FROM catalogs WHERE id = ANY(%s::uuid[])",
        (id_strs,),
    )
    by_id = {str(r["id"]): str(r["component_id"]) for r in rows}

    owned_components = {
        str(r["component_id"]) for r in execute(
            "SELECT component_id FROM resource_component_agents "
            "WHERE agent_id = %s AND component_id IS NOT NULL",
            (agent_id,),
        )
    }
    for idx, s in normalized:
        if s not in by_id:
            continue
        if by_id[s] not in owned_components:
            errors[idx] = (
                f"catalog {s} component_id {by_id[s]} not owned by {agent_id}"
            )
    if errors:
        return {"committed": False, "applied": 0, "errors": errors}

    results: list[dict] = []
    for idx, s in normalized:
        if s not in by_id:
            results.append({"deleted": False, "id": s, "reason": "not_found"})
            continue
        cnt = execute_one(
            "SELECT COUNT(*) AS n FROM flows WHERE incoming_catalog_id = %s::uuid",
            (s,),
        )
        cascaded = int(cnt["n"]) if cnt else 0
        execute_mutate("DELETE FROM catalogs WHERE id = %s::uuid", (s,))
        results.append({
            "deleted": True, "id": s, "cascaded_flows": cascaded,
        })
    return {
        "committed": True,
        "applied": sum(1 for r in results if r["deleted"]),
        "rows": results,
    }


def get_catalogs_bulk(
    agent_id: str, component_ids: list[str]
) -> dict:
    """[Phase 8.5] Multi-component bulk catalog read. Returns
    `dict[component_id_str, list[catalog_row]]`. Resolver triangulation
    use case (catalog overlap signals during merge eval). Max 500.
    """
    require_active_agent(agent_id)
    if not isinstance(component_ids, list) or not component_ids:
        raise ValueError("component_ids must be a non-empty list")
    if len(component_ids) > 500:
        raise ValueError(
            f"max 500 component_ids per bulk call (got {len(component_ids)})"
        )
    seen: set[str] = set()
    ids: list[str] = []
    for raw in component_ids:
        s = str(raw or "").strip()
        if s and s not in seen:
            seen.add(s)
            ids.append(s)

    rows = execute(
        """SELECT * FROM catalogs
           WHERE component_id = ANY(%s::uuid[])
           ORDER BY component_id, kind, identifier""",
        (ids,),
    )
    out: dict[str, list[dict]] = {cid: [] for cid in ids}
    for r in rows:
        out[str(r["component_id"])].append(r)
    return out

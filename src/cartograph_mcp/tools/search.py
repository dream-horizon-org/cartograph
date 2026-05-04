"""Vector search + Phase 10.3 deterministic search across embedded tables.

`vector_search` embeds the query with the same model used at write
time, then does a cosine-distance KNN against the target table's
embedding column. Open to all active agents.

Phase 10.3 adds 6 deterministic SQL-LIKE search tools that fill the
"find rows without knowing the component_id first" gap:
  - search_components, search_attributions, search_edges,
    search_catalogs, search_flows, search_unresolved.

Each takes per-column filters; AND across columns, OR within a
column via list. Plain string → exact match; `%`/`_`-bearing →
ILIKE. Hard cap 100 rows. Refuses blank-filter calls.
"""

from __future__ import annotations

from shared import embedding as emb
from shared.db import execute, execute_one
from cartograph_mcp.tools._search_helper import (
    MAX_RESULTS, BlankFilterError,
    pattern_clause, eq_clause, in_clause, assemble, at_most_one,
)


# Phase 7.4.4: lean projections per table. Pre-7.4.4 we did SELECT *
# which inlined the 1024-d embedding vector + heavy JSONB blobs
# (component_doc_md, source_slice, metadata, evidence, context) on
# every result row — DEMO7 SMEs reported single calls returning 50–
# 115KB and blowing their tool-result token budgets, forcing jq
# workarounds. Search-then-fetch pattern: agents get just enough to
# triage matches here, then call get_component(id) / get_attributions
# (id) / etc. for full detail on hits they care about.
_VALID_TABLES = {
    "components": "SELECT c.id, c.canonical_name, c.display_name, "
                  "       c.component_type, c.status, "
                  "       1 - (c.embedding <=> %s::vector) AS similarity "
                  "FROM components c "
                  "WHERE c.embedding IS NOT NULL "
                  "ORDER BY c.embedding <=> %s::vector ASC "
                  "LIMIT %s",
    "attributions": "SELECT a.id, a.component_id, a.plane, a.resource_type, "
                    "       a.identifier, a.confidence, "
                    "       1 - (a.embedding <=> %s::vector) AS similarity "
                    "FROM attributions a "
                    "WHERE a.embedding IS NOT NULL "
                    "ORDER BY a.embedding <=> %s::vector ASC "
                    "LIMIT %s",
    "unresolved": "SELECT u.id, u.found_in_component_id, u.reference_type, "
                  "       u.reference_value, u.resolved, "
                  "       1 - (u.embedding <=> %s::vector) AS similarity "
                  "FROM unresolved u "
                  "WHERE u.embedding IS NOT NULL "
                  "ORDER BY u.embedding <=> %s::vector ASC "
                  "LIMIT %s",
    "edges": "SELECT e.id, e.from_component_id, e.to_component_id, "
             "       e.edge_type, e.identifier, e.confidence, "
             "       1 - (e.embedding <=> %s::vector) AS similarity "
             "FROM edges e "
             "WHERE e.embedding IS NOT NULL "
             "ORDER BY e.embedding <=> %s::vector ASC "
             "LIMIT %s",
    # Phase 7.4 follow-up: catalog rows live in their own table now,
    # embedded at write time with "{kind}: {identifier}". Lets SMEs do
    # "find an endpoint similar to /payments/charge" across the org
    # without walking get_component_edges per component.
    "catalogs": "SELECT c.id, c.component_id, c.kind, c.identifier, "
                "       c.confidence, "
                "       1 - (c.embedding <=> %s::vector) AS similarity "
                "FROM catalogs c "
                "WHERE c.embedding IS NOT NULL "
                "ORDER BY c.embedding <=> %s::vector ASC "
                "LIMIT %s",
}


def vector_search(agent_id: str, query_text: str, table: str, limit: int = 10) -> dict:
    """Return top-N rows from `table` by cosine similarity to `query_text`.

    `table` must be one of components / attributions / unresolved / edges.
    `limit` is clamped to [1, 50].

    If the query can't be embedded (missing API key, API error, empty text)
    → returns {"query_embedded": False, "results": []}. Callers distinguish
    "no hits" from "couldn't search" via that flag.
    """
    caller = execute_one(
        "SELECT agent_id FROM agent_runs WHERE agent_id = %s AND status != 'decommissioned'",
        (agent_id,),
    )
    if caller is None:
        raise ValueError(f"Agent {agent_id} not found")

    if table not in _VALID_TABLES:
        raise ValueError(
            f"Invalid table '{table}'. Valid: {sorted(_VALID_TABLES)}"
        )
    try:
        limit_i = int(limit)
    except (TypeError, ValueError) as e:
        raise ValueError("limit must be an integer") from e
    limit_i = max(1, min(50, limit_i))

    vec = emb.vector_literal(emb.embed_text(query_text))
    if vec is None:
        return {"query_embedded": False, "results": []}

    sql = _VALID_TABLES[table]
    rows = execute(sql, (vec, vec, limit_i))
    return {"query_embedded": True, "results": rows}


# ============ Phase 10.3: deterministic search ============


def _check_caller(agent_id: str) -> None:
    """Any active agent can call search_*. Mirrors vector_search auth."""
    caller = execute_one(
        "SELECT agent_id FROM agent_runs WHERE agent_id = %s AND status != 'decommissioned'",
        (agent_id,),
    )
    if caller is None:
        raise ValueError(f"Agent {agent_id} not found")


# ---- search_components ----

def search_components(
    agent_id: str,
    canonical_name_pattern: str | None = None,
    display_name_pattern: str | None = None,
    name_pattern: str | None = None,
    component_type: str | list | None = None,
    status: str | list | None = "active",
    plane: str | list | None = None,
) -> list[dict]:
    """Phase 10.3. Find components by exact/ILIKE patterns + filters.

    Args:
      canonical_name_pattern: ILIKE if contains %/_, else exact.
      display_name_pattern: same.
      name_pattern: convenience — ILIKE on canonical_name OR display_name.
        Pass at most ONE of {name_pattern, canonical_name_pattern,
        display_name_pattern}.
      component_type: scalar or list (OR within).
      status: defaults to 'active'. Pass None to include all statuses.
      plane: filter via RCA → resources.plane (scalar or list).

    Returns:
      List of dicts: {id, canonical_name, display_name, component_type,
        status, planes (list of plane names from RCA→resources)}.
      Capped at 100 rows.

    Raises:
      ValueError when more than one name field set, or when ALL filters
      are None (blank filter refused).
    """
    _check_caller(agent_id)
    at_most_one(
        ("name_pattern", name_pattern),
        ("canonical_name_pattern", canonical_name_pattern),
        ("display_name_pattern", display_name_pattern),
    )

    filters: list[tuple[str | None, object]] = []

    if name_pattern is not None:
        # Convenience: ILIKE both columns. Wrap as a parenthesised OR
        # with shared param.
        if not isinstance(name_pattern, str):
            raise ValueError("name_pattern must be a string")
        # Always ILIKE for the convenience pattern (admin UI parity).
        # Use a wildcard-bearing form whether or not caller added %.
        like = name_pattern if ("%" in name_pattern or "_" in name_pattern) \
            else f"%{name_pattern}%"
        filters.append(
            ("(c.canonical_name ILIKE %s OR c.display_name ILIKE %s)", [like, like])
        )
    else:
        filters.append(pattern_clause("c.canonical_name", canonical_name_pattern))
        filters.append(pattern_clause("c.display_name", display_name_pattern))

    filters.append(in_clause("c.component_type", component_type))
    filters.append(in_clause("c.status", status))

    plane_filter, plane_params = (None, None)
    if plane is not None:
        # Plane lives on resources; join via RCA.
        if isinstance(plane, (list, tuple)):
            placeholders = ", ".join(["%s"] * len(plane))
            plane_filter = (
                f"EXISTS (SELECT 1 FROM resource_component_agents rca "
                f"        JOIN resources r ON r.id = rca.resource_id "
                f"        WHERE rca.component_id = c.id AND r.plane IN ({placeholders}))",
                list(plane),
            )
        else:
            plane_filter = (
                "EXISTS (SELECT 1 FROM resource_component_agents rca "
                "        JOIN resources r ON r.id = rca.resource_id "
                "        WHERE rca.component_id = c.id AND r.plane = %s)",
                plane,
            )
    if plane_filter is not None:
        filters.append(plane_filter)

    where_sql, params = assemble(filters)
    sql = f"""
        SELECT c.id, c.canonical_name, c.display_name, c.component_type,
               c.status,
               COALESCE(
                 ARRAY_AGG(DISTINCT r.plane) FILTER (WHERE r.plane IS NOT NULL),
                 ARRAY[]::text[]
               ) AS planes
        FROM components c
        LEFT JOIN resource_component_agents rca ON rca.component_id = c.id
        LEFT JOIN resources r ON r.id = rca.resource_id
        WHERE {where_sql}
        GROUP BY c.id
        ORDER BY c.canonical_name ASC
        LIMIT {MAX_RESULTS}
    """
    return execute(sql, tuple(params))


# ---- search_attributions ----

def search_attributions(
    agent_id: str,
    identifier_pattern: str | None = None,
    plane: str | list | None = None,
    resource_type: str | list | None = None,
    component_id: str | None = None,
) -> list[dict]:
    """Phase 10.3. Find attributions by identifier pattern + filters.

    Returns lean rows: {id, component_id, plane, resource_type,
    identifier, confidence}.
    """
    _check_caller(agent_id)
    filters = [
        pattern_clause("a.identifier", identifier_pattern),
        in_clause("a.plane", plane),
        in_clause("a.resource_type", resource_type),
        eq_clause("a.component_id", component_id),
    ]
    where_sql, params = assemble(filters)
    sql = f"""
        SELECT a.id, a.component_id, a.plane, a.resource_type,
               a.identifier, a.confidence
        FROM attributions a
        WHERE {where_sql}
        ORDER BY a.last_seen_at DESC
        LIMIT {MAX_RESULTS}
    """
    return execute(sql, tuple(params))


# ---- search_edges ----

_VALID_EDGE_KINDS = {"bound", "catalog", "dangling"}


def search_edges(
    agent_id: str,
    identifier_pattern: str | None = None,
    edge_type: str | list | None = None,
    kind: str | list | None = None,
    from_component_id: str | None = None,
    to_component_id: str | None = None,
) -> list[dict]:
    """Phase 10.3. Find edges by identifier + edge_type + kind + endpoints.

    `kind` filter:
      - 'bound'    → from_component_id IS NOT NULL AND to_component_id IS NOT NULL
      - 'catalog'  → from_component_id IS NULL (historical pre-7.4 rows)
      - 'dangling' → to_component_id IS NULL

    Note: 'catalog' kind matches no live rows post-Phase-7.4 (catalog
    rows migrated to `catalogs` table). Use search_catalogs for live
    catalogs. The kind filter is kept for completeness.
    """
    _check_caller(agent_id)
    if kind is not None:
        kinds = kind if isinstance(kind, (list, tuple)) else [kind]
        for k in kinds:
            if k not in _VALID_EDGE_KINDS:
                raise ValueError(
                    f"kind '{k}' invalid. Valid: {sorted(_VALID_EDGE_KINDS)}"
                )

    filters: list[tuple[str | None, object]] = [
        pattern_clause("e.identifier", identifier_pattern),
        in_clause("e.edge_type", edge_type),
        eq_clause("e.from_component_id", from_component_id),
        eq_clause("e.to_component_id", to_component_id),
    ]
    if kind is not None:
        kinds = kind if isinstance(kind, (list, tuple)) else [kind]
        kind_parts = []
        for k in kinds:
            if k == "bound":
                kind_parts.append("(e.from_component_id IS NOT NULL AND e.to_component_id IS NOT NULL)")
            elif k == "catalog":
                kind_parts.append("(e.from_component_id IS NULL)")
            elif k == "dangling":
                kind_parts.append("(e.to_component_id IS NULL)")
        filters.append(("(" + " OR ".join(kind_parts) + ")", []))

    where_sql, params = assemble(filters)
    sql = f"""
        SELECT e.id, e.from_component_id, e.to_component_id,
               e.edge_type, e.identifier, e.confidence,
               CASE
                 WHEN e.from_component_id IS NOT NULL AND e.to_component_id IS NOT NULL THEN 'bound'
                 WHEN e.from_component_id IS NULL THEN 'catalog'
                 WHEN e.to_component_id IS NULL THEN 'dangling'
               END AS kind
        FROM edges e
        WHERE {where_sql}
        ORDER BY e.last_seen_at DESC
        LIMIT {MAX_RESULTS}
    """
    return execute(sql, tuple(params))


# ---- search_catalogs ----

def search_catalogs(
    agent_id: str,
    identifier_pattern: str | None = None,
    kind: str | list | None = None,
    component_id: str | None = None,
) -> list[dict]:
    """Phase 10.3. Find catalog rows by identifier + kind + owner."""
    _check_caller(agent_id)
    filters = [
        pattern_clause("c.identifier", identifier_pattern),
        in_clause("c.kind", kind),
        eq_clause("c.component_id", component_id),
    ]
    where_sql, params = assemble(filters)
    sql = f"""
        SELECT c.id, c.component_id, c.kind, c.identifier, c.confidence
        FROM catalogs c
        WHERE {where_sql}
        ORDER BY c.updated_at DESC
        LIMIT {MAX_RESULTS}
    """
    return execute(sql, tuple(params))


# ---- search_flows ----

def search_flows(
    agent_id: str,
    component_id: str | None = None,
    incoming_catalog_id: str | None = None,
    outgoing_edge_id: str | None = None,
) -> list[dict]:
    """Phase 10.3. Find flows by component / catalog / edge id.

    Flows have no human-readable identifier — only FK references. So
    this is ID-based filtering only, no string pattern field.
    """
    _check_caller(agent_id)
    filters = [
        eq_clause("f.component_id", component_id),
        eq_clause("f.incoming_catalog_id", incoming_catalog_id),
        eq_clause("f.outgoing_edge_id", outgoing_edge_id),
    ]
    where_sql, params = assemble(filters)
    sql = f"""
        SELECT f.id, f.component_id, f.incoming_catalog_id,
               f.outgoing_edge_id, f.confidence
        FROM flows f
        WHERE {where_sql}
        ORDER BY f.updated_at DESC
        LIMIT {MAX_RESULTS}
    """
    return execute(sql, tuple(params))


# ---- search_unresolved ----

def search_unresolved(
    agent_id: str,
    reference_value_pattern: str | None = None,
    reference_type: str | list | None = None,
    found_in_component_id: str | None = None,
    only_unresolved: bool = True,
) -> list[dict]:
    """Phase 10.3. Find unresolved refs by value pattern + filters.

    `only_unresolved=True` (default) excludes already-resolved rows.
    Pass False to include the full history.
    """
    _check_caller(agent_id)
    filters: list[tuple[str | None, object]] = [
        pattern_clause("u.reference_value", reference_value_pattern),
        in_clause("u.reference_type", reference_type),
        eq_clause("u.found_in_component_id", found_in_component_id),
    ]
    if only_unresolved:
        filters.append(("u.resolved = FALSE", []))

    where_sql, params = assemble(filters)
    sql = f"""
        SELECT u.id, u.found_in_component_id, u.reference_type,
               u.reference_value, u.resolved, u.attempts
        FROM unresolved u
        WHERE {where_sql}
        ORDER BY u.created_at DESC
        LIMIT {MAX_RESULTS}
    """
    return execute(sql, tuple(params))

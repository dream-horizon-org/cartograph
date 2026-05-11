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

from typing import Any

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
# Phase 7.4.4: lean projections per table. Pre-7.4.4 we did SELECT *
# which inlined the 1024-d embedding vector + heavy JSONB blobs on every
# result row. Phase 10.7: components projection adds `description` (the
# new dense embed-target field, ≤400 chars soft cap — cheap to include
# in results, saves a get_component round-trip on most hits).
#
# Map: table → (select_columns, alias). `from_clause` is built per-table
# below in vector_search() since alias placement matters for filters.
_TABLE_PROJECTIONS = {
    "components": ("c.id, c.canonical_name, c.display_name, "
                   "c.component_type, c.status, c.description", "c"),
    "attributions": ("a.id, a.component_id, a.plane, a.resource_type, "
                     "a.identifier, a.confidence", "a"),
    "unresolved": ("u.id, u.found_in_component_id, u.reference_type, "
                   "u.reference_value, u.resolved", "u"),
    "edges": ("e.id, e.from_component_id, e.to_component_id, "
              "e.edge_type, e.identifier, e.confidence", "e"),
    "catalogs": ("c.id, c.component_id, c.kind, c.identifier, "
                 "c.confidence", "c"),
}

_TABLE_FROM = {
    "components":   "components c",
    "attributions": "attributions a",
    "unresolved":   "unresolved u",
    "edges":        "edges e",
    "catalogs":     "catalogs c",
}


# Backwards compat constant — referenced by name elsewhere in this module.
_VALID_TABLES = set(_TABLE_PROJECTIONS.keys())


def vector_search(
    agent_id: str,
    query_text: str,
    table: str,
    limit: int = 10,
    filters: dict | None = None,
    exclude_self: bool = True,
) -> dict:
    """Return top-N rows from `table` by cosine similarity to `query_text`.

    `table` must be one of components / attributions / unresolved / edges /
    catalogs. `limit` is clamped to [1, 50].

    If the query can't be embedded (Ollama unreachable, empty text) →
    returns {"query_embedded": False, "results": []}. Callers distinguish
    "no hits" from "couldn't search" via that flag.

    Phase 10.7 additions:
    - `filters: dict | None = None` — optional per-table filter dict.
      AND across keys; OR within key via list. Allowed keys per table:
        components:   component_type, status
        attributions: plane, resource_type, component_id
        edges:        edge_type, from_component_id, to_component_id
        catalogs:     kind, component_id
        unresolved:   reference_type, found_in_component_id, resolved
      Plane filter on components is NOT supported here (needs RCA→
      resources JOIN); use `vector_search(table='attributions',
      filters={'plane': ...})` for plane-scoped lookups.
      Invalid key for table → ValueError listing legal keys.
    - `exclude_self: bool = True` — DEFAULT ON. Excludes rows owned by
      caller's component(s) via RCA (silent no-op for non-SME callers
      with zero owned components). Set False to include own rows
      (debug / self-loop sanity check).
    - Components projection now includes `description` column (Phase
      10.7 dense embed-target).
    """
    caller = execute_one(
        "SELECT agent_id FROM agent_runs WHERE agent_id = %s AND status != 'decommissioned'",
        (agent_id,),
    )
    if caller is None:
        raise ValueError(f"Agent {agent_id} not found")

    if table not in _TABLE_PROJECTIONS:
        raise ValueError(
            f"Invalid table '{table}'. Valid: {sorted(_TABLE_PROJECTIONS)}"
        )
    try:
        limit_i = int(limit)
    except (TypeError, ValueError) as e:
        raise ValueError("limit must be an integer") from e
    limit_i = max(1, min(50, limit_i))

    # Validate filter keys before any embed work — fail fast.
    from cartograph_mcp.tools._search_helper import (
        validate_filter_keys, build_filter_clauses, exclude_self_clause,
        assemble,
    )
    validate_filter_keys(table, filters)

    vec = emb.vector_literal(emb.embed_text(query_text))
    if vec is None:
        return {"query_embedded": False, "results": []}

    projection, alias = _TABLE_PROJECTIONS[table]
    from_table = _TABLE_FROM[table]

    # Base WHERE: embedding IS NOT NULL (rows that failed write-time embed
    # don't participate in cosine ranking).
    where_clauses: list[str] = [f"{alias}.embedding IS NOT NULL"]
    extra_params: list[Any] = []

    # Apply user-supplied filters via the helper.
    if filters:
        filter_clauses_raw, _ = build_filter_clauses(filters, table_alias=alias)
        try:
            filter_sql, filter_params = assemble(filter_clauses_raw)
            where_clauses.append(filter_sql)
            extra_params.extend(filter_params)
        except Exception:
            # All-None filter dict shouldn't happen here (validate_filter_keys
            # caught unknown keys; empty/None already short-circuited above).
            # If assemble's BlankFilterError fires anyway, treat as no-op.
            pass

    # exclude_self: skip caller's own rows.
    if exclude_self:
        excl_sql, excl_params = exclude_self_clause(table, alias, agent_id)
        if excl_sql is not None:
            where_clauses.append(excl_sql)
            if isinstance(excl_params, list):
                extra_params.extend(excl_params)
            else:
                extra_params.append(excl_params)

    where_sql = " AND ".join(where_clauses)
    sql = (
        f"SELECT {projection}, "
        f"       1 - ({alias}.embedding <=> %s::vector) AS similarity "
        f"FROM {from_table} "
        f"WHERE {where_sql} "
        f"ORDER BY {alias}.embedding <=> %s::vector ASC "
        f"LIMIT %s"
    )
    # Param order: (vec for similarity column, *extra_params, vec for ORDER BY, limit)
    params = [vec, *extra_params, vec, limit_i]
    rows = execute(sql, tuple(params))
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


def _maybe_exclude_self(
    table: str, alias: str, agent_id: str, exclude_self: bool,
) -> tuple[str | None, list[Any]]:
    """Phase 10.7: optional caller-self exclusion clause for search_*.

    Returns (sql_fragment, params_list). If exclude_self=False or the
    table has no ownership predicate, returns (None, []).

    Non-SME callers (orch / iter / resolver own zero components) →
    NOT IN (empty set) is silently TRUE in PostgreSQL → no exclusion
    happens. Predicate fires on actual SME callers with at least one
    owned component.

    IMPORTANT: this clause is applied AFTER `assemble()` of user-supplied
    filters so the BlankFilterError check still works correctly when the
    user provides zero filters (exclude_self alone shouldn't bypass the
    "no whole-table dumps" guard).
    """
    if not exclude_self:
        return (None, [])
    from cartograph_mcp.tools._search_helper import exclude_self_clause
    sql, params = exclude_self_clause(table, alias, agent_id)
    if sql is None:
        return (None, [])
    if isinstance(params, list):
        return (sql, params)
    return (sql, [params])


def _apply_exclude_self(
    where_sql: str, params: list, excl_sql: str | None, excl_params: list,
) -> tuple[str, list]:
    """Tack the exclude_self clause onto a finished WHERE expression.

    where_sql comes from assemble() (already validated non-blank). We
    string-concat the exclude_self predicate with AND. params is
    extended in-place-style (returns new list).
    """
    if excl_sql is None:
        return where_sql, params
    new_where = f"{where_sql} AND {excl_sql}"
    new_params = list(params) + list(excl_params)
    return new_where, new_params


# ---- search_components ----

def search_components(
    agent_id: str,
    canonical_name_pattern: str | None = None,
    display_name_pattern: str | None = None,
    name_pattern: str | None = None,
    component_type: str | list | None = None,
    status: str | list | None = "active",
    plane: str | list | None = None,
    exclude_self: bool = True,
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
      exclude_self: Phase 10.7. DEFAULT TRUE. Skip rows owned by the
        caller's component(s) via RCA. Non-SME callers no-op. Set
        False to include own rows.

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

    # assemble() raises BlankFilterError if all user filters are None.
    # exclude_self is applied AFTER so it doesn't bypass that guard.
    where_sql, params = assemble(filters)
    excl_sql, excl_params = _maybe_exclude_self("components", "c", agent_id, exclude_self)
    where_sql, params = _apply_exclude_self(where_sql, params, excl_sql, excl_params)

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
    exclude_self: bool = True,
) -> list[dict]:
    """Phase 10.3. Find attributions by identifier pattern + filters.

    Returns lean rows: {id, component_id, plane, resource_type,
    identifier, confidence}.

    Phase 10.7: exclude_self default TRUE — skip rows on caller's own
    component. Non-SME callers no-op.
    """
    _check_caller(agent_id)
    filters: list[tuple[str | None, object]] = [
        pattern_clause("a.identifier", identifier_pattern),
        in_clause("a.plane", plane),
        in_clause("a.resource_type", resource_type),
        eq_clause("a.component_id", component_id),
    ]
    where_sql, params = assemble(filters)
    excl_sql, excl_params = _maybe_exclude_self("attributions", "a", agent_id, exclude_self)
    where_sql, params = _apply_exclude_self(where_sql, params, excl_sql, excl_params)
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
    exclude_self: bool = True,
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
    # Phase 10.7: exclude_self for edges (either-side ownership check) —
    # applied AFTER assemble so blank-filter check stays intact.
    excl_sql, excl_params = _maybe_exclude_self("edges", "e", agent_id, exclude_self)
    where_sql, params = _apply_exclude_self(where_sql, params, excl_sql, excl_params)
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
    exclude_self: bool = True,
) -> list[dict]:
    """Phase 10.3. Find catalog rows by identifier + kind + owner.

    Phase 10.7: exclude_self default TRUE — skip catalogs owned by
    caller's component. Non-SME callers no-op.
    """
    _check_caller(agent_id)
    filters: list[tuple[str | None, object]] = [
        pattern_clause("c.identifier", identifier_pattern),
        in_clause("c.kind", kind),
        eq_clause("c.component_id", component_id),
    ]
    where_sql, params = assemble(filters)
    excl_sql, excl_params = _maybe_exclude_self("catalogs", "c", agent_id, exclude_self)
    where_sql, params = _apply_exclude_self(where_sql, params, excl_sql, excl_params)
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
    exclude_self: bool = True,
) -> list[dict]:
    """Phase 10.3. Find unresolved refs by value pattern + filters.

    `only_unresolved=True` (default) excludes already-resolved rows.
    Pass False to include the full history.

    Phase 10.7: exclude_self default TRUE — skip unresolved rows owned
    by caller's component (found_in_component_id matches). Non-SME
    callers no-op.
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
    excl_sql, excl_params = _maybe_exclude_self("unresolved", "u", agent_id, exclude_self)
    where_sql, params = _apply_exclude_self(where_sql, params, excl_sql, excl_params)
    sql = f"""
        SELECT u.id, u.found_in_component_id, u.reference_type,
               u.reference_value, u.resolved, u.attempts
        FROM unresolved u
        WHERE {where_sql}
        ORDER BY u.created_at DESC
        LIMIT {MAX_RESULTS}
    """
    return execute(sql, tuple(params))

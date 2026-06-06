"""Gate-free read queries for the standalone read-only MCP server.

These mirror the global read tools in cartograph_mcp.tools.* and
cartograph_mcp.server, with the `agent_id` active-agent gate removed
(an external non-agent caller has no agent_id) and, for search/vector
functions, with `exclude_self` removed (there is no caller "self").

Only GLOBAL reads live here — agent-scoped reads (get_my_*, inbox,
threads, secrets) are intentionally absent. See
docs/superpowers/specs/2026-05-20-readonly-mcp-server-design.md.

DRY note: the SELECTs are copied from the gated tools per the "do not
modify existing tools" constraint, so projection/schema changes must be
mirrored here.
"""

from __future__ import annotations

from typing import Any

from shared.db import execute, execute_one
from shared import embedding as emb
from cartograph_mcp.tools._search_helper import (
    MAX_RESULTS, pattern_clause, eq_clause, in_clause, assemble, at_most_one,
    validate_filter_keys, build_filter_clauses,
)


# ============ component graph (single) ============

def get_component(component_id: str) -> dict:
    row = execute_one(
        """SELECT id, canonical_name, display_name, component_type,
                  status, confidence, metadata, description,
                  component_doc_md, source_slice,
                  split_from_component_id, split_briefing,
                  scanned_at, created_at, updated_at
           FROM components WHERE id = %s""",
        (component_id,),
    )
    if row is None:
        raise ValueError(f"Component {component_id} not found")
    return row


def get_attributions(component_id: str) -> list[dict]:
    return execute(
        """SELECT * FROM attributions
           WHERE component_id = %s
           ORDER BY plane, resource_type, identifier""",
        (component_id,),
    )


def get_edges(component_id: str) -> dict:
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


def get_unresolved(component_id: str) -> list[dict]:
    return execute(
        """SELECT * FROM unresolved
           WHERE found_in_component_id = %s
           ORDER BY resolved ASC, created_at DESC""",
        (component_id,),
    )


def get_component_edges(component_id: str) -> dict:
    incoming_bound = execute(
        """SELECT * FROM edges
           WHERE to_component_id = %s AND from_component_id IS NOT NULL
           ORDER BY edge_type, identifier""",
        (component_id,),
    )
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


def get_component_owner(component_id: str) -> dict:
    row = execute_one(
        """SELECT c.id::text         AS component_id,
                  c.canonical_name,
                  c.status            AS component_status,
                  rca.agent_id        AS owner_agent_id,
                  ar.status           AS owner_status,
                  ar.merged_into_agent_id::text AS merged_into_agent_id
             FROM components c
             LEFT JOIN resource_component_agents rca ON rca.component_id = c.id
             LEFT JOIN agent_runs ar ON ar.agent_id = rca.agent_id
            WHERE c.id = %s
            LIMIT 1""",
        (component_id,),
    )
    if row is None:
        raise ValueError(f"Component {component_id} not found")
    return row


# ============ bulk reads ============

def _normalize_id_list(ids: list, label: str) -> list[str]:
    if not isinstance(ids, list) or not ids:
        raise ValueError(f"{label} must be a non-empty list")
    if len(ids) > 500:
        raise ValueError(f"max 500 {label} per bulk call (got {len(ids)})")
    seen: set[str] = set()
    out: list[str] = []
    for raw in ids:
        s = str(raw or "").strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def get_components_bulk(component_ids: list[str]) -> dict:
    ids = _normalize_id_list(component_ids, "component_ids")
    rows = execute(
        """SELECT id, canonical_name, display_name, component_type,
                  status, confidence, metadata, description,
                  component_doc_md, source_slice,
                  split_from_component_id, split_briefing,
                  scanned_at, created_at, updated_at
           FROM components WHERE id = ANY(%s::uuid[])""",
        (ids,),
    )
    by_id = {str(r["id"]): r for r in rows}
    return {cid: by_id.get(cid) for cid in ids}


def get_attributions_bulk(component_ids: list[str]) -> dict:
    ids = _normalize_id_list(component_ids, "component_ids")
    rows = execute(
        """SELECT * FROM attributions
           WHERE component_id = ANY(%s::uuid[])
           ORDER BY component_id, plane, resource_type, identifier""",
        (ids,),
    )
    out: dict[str, list[dict]] = {cid: [] for cid in ids}
    for r in rows:
        out[str(r["component_id"])].append(r)
    return out


def get_component_edges_bulk(component_ids: list[str]) -> dict:
    ids = _normalize_id_list(component_ids, "component_ids")
    out: dict[str, dict] = {}
    for cid in ids:
        out[cid] = get_component_edges(cid)
    return out


def get_catalogs_bulk(component_ids: list[str]) -> dict:
    ids = _normalize_id_list(component_ids, "component_ids")
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


def get_flows_bulk(component_ids: list[str]) -> dict:
    ids = _normalize_id_list(component_ids, "component_ids")
    rows = execute(
        """SELECT * FROM flows
           WHERE component_id = ANY(%s::uuid[])
           ORDER BY component_id, updated_at DESC""",
        (ids,),
    )
    out: dict[str, list[dict]] = {cid: [] for cid in ids}
    for r in rows:
        out[str(r["component_id"])].append(r)
    return out


# ============ flows (single) ============

def get_flow(component_id: str, incoming_catalog_id: str) -> list[dict]:
    return execute(
        """SELECT e.* FROM flows f
           JOIN edges e ON e.id = f.outgoing_edge_id
           WHERE f.component_id = %s::uuid
             AND f.incoming_catalog_id = %s::uuid
           ORDER BY e.edge_type, e.identifier""",
        (component_id, incoming_catalog_id),
    )


def get_flow_inverse(component_id: str, outgoing_edge_id: str) -> list[dict]:
    return execute(
        """SELECT c.* FROM flows f
           JOIN catalogs c ON c.id = f.incoming_catalog_id
           WHERE f.component_id = %s::uuid
             AND f.outgoing_edge_id = %s::uuid
           ORDER BY c.kind, c.identifier""",
        (component_id, outgoing_edge_id),
    )


# ============ resources ============

_VALID_PLANES = {"github", "deploy", "cloud", "telemetry", "config"}


def get_resource(resource_id: str) -> dict:
    row = execute_one("SELECT * FROM resources WHERE id = %s", (resource_id,))
    if row is None:
        raise ValueError(f"Resource {resource_id} not found")
    return row


def list_resources_for_plane(plane: str) -> list[dict]:
    if plane not in _VALID_PLANES:
        raise ValueError(f"Invalid plane '{plane}'")
    return execute(
        "SELECT * FROM resources WHERE plane = %s ORDER BY created_at DESC",
        (plane,),
    )


def list_all_resources(status: str | None = None) -> list[dict]:
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


def get_resource_counts() -> dict:
    rows = execute(
        """SELECT plane, status, COUNT(*) AS cnt
           FROM resources
           GROUP BY plane, status
           ORDER BY plane, status"""
    )
    return {"by_plane_status": rows}


# ============ agents ============

def list_agents() -> dict:
    rows = execute(
        """SELECT agent_id, agent_type, status, plane,
                  invocation_count, sleep_until, errored_at, created_at
           FROM agent_runs
           WHERE status != 'decommissioned'
           ORDER BY
             CASE agent_type
               WHEN 'orchestrator' THEN 0
               WHEN 'resolver' THEN 1
               WHEN 'sme' THEN 2
               WHEN 'iterator' THEN 3
               ELSE 99
             END,
             created_at"""
    )
    return {"agents": rows}


# ============ deterministic search (no agent_id, no exclude_self) ============

def search_components(
    canonical_name_pattern: str | None = None,
    display_name_pattern: str | None = None,
    name_pattern: str | None = None,
    component_type: str | list | None = None,
    status: str | list | None = "active",
    plane: str | list | None = None,
) -> list[dict]:
    at_most_one(
        ("name_pattern", name_pattern),
        ("canonical_name_pattern", canonical_name_pattern),
        ("display_name_pattern", display_name_pattern),
    )
    filters: list[tuple[str | None, object]] = []
    if name_pattern is not None:
        if not isinstance(name_pattern, str):
            raise ValueError("name_pattern must be a string")
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
    plane_filter = None
    if plane is not None:
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


def search_attributions(
    identifier_pattern: str | None = None,
    plane: str | list | None = None,
    resource_type: str | list | None = None,
    component_id: str | None = None,
) -> list[dict]:
    filters: list[tuple[str | None, object]] = [
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


_VALID_EDGE_KINDS = {"bound", "catalog", "dangling"}


def search_edges(
    identifier_pattern: str | None = None,
    edge_type: str | list | None = None,
    kind: str | list | None = None,
    from_component_id: str | None = None,
    to_component_id: str | None = None,
) -> list[dict]:
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


def search_catalogs(
    identifier_pattern: str | None = None,
    kind: str | list | None = None,
    component_id: str | None = None,
) -> list[dict]:
    filters: list[tuple[str | None, object]] = [
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


def search_flows(
    component_id: str | None = None,
    incoming_catalog_id: str | None = None,
    outgoing_edge_id: str | None = None,
) -> list[dict]:
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


def search_unresolved(
    reference_value_pattern: str | None = None,
    reference_type: str | list | None = None,
    found_in_component_id: str | None = None,
    only_unresolved: bool = True,
) -> list[dict]:
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


# ============ vector search (no agent_id, no exclude_self) ============

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


def vector_search(
    query_text: str,
    table: str,
    limit: int = 10,
    filters: dict | None = None,
) -> dict:
    """Cosine-KNN over an embedded table (components/attributions/unresolved/
    edges/catalogs). limit clamped to [1, 50]. Returns
    {"query_embedded": bool, "results": [...]}; query_embedded is False when
    the embedder is unreachable (no agent_id / exclude_self — read-only)."""
    if table not in _TABLE_PROJECTIONS:
        raise ValueError(
            f"Invalid table '{table}'. Valid: {sorted(_TABLE_PROJECTIONS)}"
        )
    try:
        limit_i = int(limit)
    except (TypeError, ValueError) as e:
        raise ValueError("limit must be an integer") from e
    limit_i = max(1, min(50, limit_i))

    validate_filter_keys(table, filters)

    vec = emb.vector_literal(emb.embed_text(query_text))
    if vec is None:
        return {"query_embedded": False, "results": []}

    projection, alias = _TABLE_PROJECTIONS[table]
    from_table = _TABLE_FROM[table]
    where_clauses: list[str] = [f"{alias}.embedding IS NOT NULL"]
    extra_params: list[Any] = []

    if filters:
        filter_clauses_raw, _ = build_filter_clauses(filters, table_alias=alias)
        try:
            filter_sql, filter_params = assemble(filter_clauses_raw)
            where_clauses.append(filter_sql)
            extra_params.extend(filter_params)
        except Exception:
            pass

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

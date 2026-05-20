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

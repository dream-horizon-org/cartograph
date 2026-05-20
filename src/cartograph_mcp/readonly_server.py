"""Standalone READ-ONLY cartograph MCP server.

Exposes the 25 global read tools WITHOUT agent_id, for external
non-agent clients. Separate FastMCP instance + port from the main
agent-facing server (:8100); agents are unaffected.

Binds 127.0.0.1 by default (unauthenticated read-only — not exposed on
0.0.0.0). Override host/port via CARTOGRAPH_RO_HOST / CARTOGRAPH_RO_PORT.

Connect from an MCP client:
  {"mcpServers": {"cartograph-ro":
     {"type": "http", "url": "http://localhost:8101/mcp"}}}

See docs/superpowers/specs/2026-05-20-readonly-mcp-server-design.md.
"""

import logging
import os
import sys
from typing import Any

from mcp.server.fastmcp import FastMCP

from shared.db import init_pool, close_pool
from cartograph_mcp import readonly_queries as ro

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

_RO_HOST = os.environ.get("CARTOGRAPH_RO_HOST", "127.0.0.1")
_RO_PORT = int(os.environ.get("CARTOGRAPH_RO_PORT", "8101"))

mcp = FastMCP("cartograph-db-readonly", host=_RO_HOST, port=_RO_PORT)


# ============ component graph ============

@mcp.tool()
def get_component(component_id: str) -> dict[str, Any]:
    """Read a component by id. Read-only; no agent_id required."""
    return ro.get_component(component_id)


@mcp.tool()
def get_attributions(component_id: str) -> dict[str, list[dict[str, Any]]]:
    """All attributions for a component. Read-only."""
    return {"attributions": ro.get_attributions(component_id)}


@mcp.tool()
def get_edges(component_id: str) -> dict[str, list]:
    """All bound edges for a component: {outbound, inbound}. Read-only."""
    return ro.get_edges(component_id)


@mcp.tool()
def get_unresolved(component_id: str) -> dict[str, list[dict[str, Any]]]:
    """Unresolved references for a component. Read-only."""
    return {"unresolved": ro.get_unresolved(component_id)}


@mcp.tool()
def get_component_edges(component_id: str) -> dict[str, list[dict[str, Any]]]:
    """Categorised edges (incoming_bound/incoming_catalog/outgoing_bound/
    outgoing_dangling). Read-only."""
    return ro.get_component_edges(component_id)


@mcp.tool()
def get_component_owner(component_id: str) -> dict[str, Any]:
    """The active SME owning a component via RCA. Read-only."""
    return ro.get_component_owner(component_id)


@mcp.tool()
def get_components_bulk(component_ids: list[str]) -> dict[str, Any]:
    """Bulk component read -> {id: row | None}. Max 500. Read-only."""
    return ro.get_components_bulk(component_ids)


@mcp.tool()
def get_attributions_bulk(component_ids: list[str]) -> dict[str, Any]:
    """Bulk attribution read -> {id: [rows]}. Max 500. Read-only."""
    return ro.get_attributions_bulk(component_ids)


@mcp.tool()
def get_component_edges_bulk(component_ids: list[str]) -> dict[str, Any]:
    """Bulk categorised-edge read -> {id: {...}}. Max 500. Read-only."""
    return ro.get_component_edges_bulk(component_ids)


# ============ catalogs ============

@mcp.tool()
def get_catalogs_bulk(component_ids: list[str]) -> dict[str, Any]:
    """Bulk catalog read -> {id: [rows]}. Max 500. Read-only."""
    return ro.get_catalogs_bulk(component_ids)


# ============ flows ============

@mcp.tool()
def get_flow(component_id: str, incoming_catalog_id: str) -> dict[str, list[dict[str, Any]]]:
    """Outgoing edges fired when a catalog on a component is hit. Read-only."""
    return {"outgoing": ro.get_flow(component_id, incoming_catalog_id)}


@mcp.tool()
def get_flow_inverse(component_id: str, outgoing_edge_id: str) -> dict[str, list[dict[str, Any]]]:
    """Incoming catalogs whose hit triggers an outgoing edge. Read-only."""
    return {"incoming": ro.get_flow_inverse(component_id, outgoing_edge_id)}


@mcp.tool()
def get_flows_bulk(component_ids: list[str]) -> dict[str, Any]:
    """Bulk flow read -> {id: [rows]}. Max 500. Read-only."""
    return ro.get_flows_bulk(component_ids)


# ============ resources ============

@mcp.tool()
def get_resource(resource_id: str) -> dict[str, Any]:
    """Read a single resource by id. Read-only."""
    return ro.get_resource(resource_id)


@mcp.tool()
def list_resources_for_plane(plane: str) -> dict[str, list]:
    """All resources for a plane. Read-only."""
    return {"resources": ro.list_resources_for_plane(plane)}


@mcp.tool()
def list_all_resources(status: str | None = None) -> dict[str, list]:
    """All resources across planes, optional status filter. Read-only."""
    return {"resources": ro.list_all_resources(status)}


@mcp.tool()
def get_resource_counts() -> dict[str, Any]:
    """Resource counts grouped by plane+status. Read-only."""
    return ro.get_resource_counts()


# ============ agents ============

@mcp.tool()
def list_agents() -> dict[str, list]:
    """All non-decommissioned agents. Read-only."""
    return ro.list_agents()


# ============ deterministic search ============

@mcp.tool()
def search_components(
    canonical_name_pattern: str | None = None,
    display_name_pattern: str | None = None,
    name_pattern: str | None = None,
    component_type: Any = None,
    status: Any = "active",
    plane: Any = None,
) -> list[dict]:
    """Find components by pattern + filters. Read-only. Cap 100."""
    return ro.search_components(
        canonical_name_pattern, display_name_pattern, name_pattern,
        component_type, status, plane,
    )


@mcp.tool()
def search_attributions(
    identifier_pattern: str | None = None,
    plane: Any = None,
    resource_type: Any = None,
    component_id: str | None = None,
) -> list[dict]:
    """Find attributions by identifier + filters. Read-only. Cap 100."""
    return ro.search_attributions(
        identifier_pattern, plane, resource_type, component_id,
    )


@mcp.tool()
def search_edges(
    identifier_pattern: str | None = None,
    edge_type: Any = None,
    kind: Any = None,
    from_component_id: str | None = None,
    to_component_id: str | None = None,
) -> list[dict]:
    """Find edges by identifier + edge_type + kind + endpoints. Read-only."""
    return ro.search_edges(
        identifier_pattern, edge_type, kind, from_component_id, to_component_id,
    )


@mcp.tool()
def search_catalogs(
    identifier_pattern: str | None = None,
    kind: Any = None,
    component_id: str | None = None,
) -> list[dict]:
    """Find catalog rows by identifier + kind + owner. Read-only. Cap 100."""
    return ro.search_catalogs(identifier_pattern, kind, component_id)


@mcp.tool()
def search_flows(
    component_id: str | None = None,
    incoming_catalog_id: str | None = None,
    outgoing_edge_id: str | None = None,
) -> list[dict]:
    """Find flows by component / catalog / edge id. Read-only. Cap 100."""
    return ro.search_flows(component_id, incoming_catalog_id, outgoing_edge_id)


@mcp.tool()
def search_unresolved(
    reference_value_pattern: str | None = None,
    reference_type: Any = None,
    found_in_component_id: str | None = None,
    only_unresolved: bool = True,
) -> list[dict]:
    """Find unresolved refs by value pattern + filters. Read-only. Cap 100."""
    return ro.search_unresolved(
        reference_value_pattern, reference_type,
        found_in_component_id, only_unresolved,
    )


@mcp.tool()
def vector_search(
    query_text: str,
    table: str,
    limit: int = 10,
    filters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Cosine-similarity KNN over an embedded table. Read-only.

    Returns {"query_embedded": bool, "results": [...]}. If the embedder
    is unreachable, query_embedded=False and results=[].
    """
    return ro.vector_search(query_text, table, limit, filters=filters)


def main() -> None:
    init_pool()  # NOTE: does NOT run migrations — the main server owns schema.
    tool_names = sorted(mcp._tool_manager._tools.keys())
    logger.info(
        "Cartograph READ-ONLY MCP server starting on %s:%d "
        "(streamable-http) — %d tools registered",
        _RO_HOST, _RO_PORT, len(tool_names),
    )
    logger.info("Registered tools: %s", ", ".join(tool_names))
    try:
        mcp.run(transport="streamable-http")
    finally:
        close_pool()


if __name__ == "__main__":
    main()

"""Contract test for the read-only MCP server: exactly the 25 global reads,
none requiring agent_id."""

import inspect

from cartograph_mcp import readonly_server


EXPECTED_TOOLS = {
    "get_component", "get_attributions", "get_edges", "get_unresolved",
    "get_component_edges", "get_component_owner", "get_components_bulk",
    "get_attributions_bulk", "get_component_edges_bulk",
    "get_catalogs_bulk",
    "get_flow", "get_flow_inverse", "get_flows_bulk",
    "get_resource", "list_resources_for_plane", "list_all_resources",
    "get_resource_counts",
    "list_agents",
    "search_components", "search_attributions", "search_edges",
    "search_catalogs", "search_flows", "search_unresolved", "vector_search",
}


def _registered_tools():
    return readonly_server.mcp._tool_manager._tools


def test_exactly_25_tools_registered():
    tools = _registered_tools()
    assert set(tools.keys()) == EXPECTED_TOOLS
    assert len(tools) == 25


def test_no_tool_requires_agent_id():
    for name, tool in _registered_tools().items():
        params = inspect.signature(tool.fn).parameters
        assert "agent_id" not in params, f"{name} still takes agent_id"


def test_no_search_tool_has_exclude_self():
    for name in ("search_components", "search_attributions", "search_edges",
                 "search_catalogs", "search_flows", "search_unresolved",
                 "vector_search"):
        params = inspect.signature(_registered_tools()[name].fn).parameters
        assert "exclude_self" not in params, f"{name} still takes exclude_self"

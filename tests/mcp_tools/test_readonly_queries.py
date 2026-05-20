"""Read-only query layer: same results as the gated tools, but no agent_id."""

import pytest

from shared.db import execute_mutate
from cartograph_mcp.tools import components, resources
from cartograph_mcp import readonly_queries as ro


def _seed_component(canonical="svc/a", display="Service A", ctype="application"):
    """Build one SME-owned component using the real write tools, return its id."""
    execute_mutate(
        """INSERT INTO agent_runs (agent_id, agent_type, plane, status)
           VALUES ('iter-gh', 'iterator', 'github', 'idle')"""
    )
    r = resources.upsert_resource("iter-gh", "github", "repo", canonical)
    execute_mutate(
        """INSERT INTO agent_runs (agent_id, agent_type, status)
           VALUES ('sme-a', 'sme', 'idle')"""
    )
    execute_mutate(
        """INSERT INTO resource_component_agents (resource_id, component_id, agent_id)
           VALUES (%s, NULL, 'sme-a')""",
        (r["id"],),
    )
    comp = components.upsert_component(
        "sme-a",
        {"canonical_name": canonical, "display_name": display,
         "component_type": ctype, "confidence": 0.9},
    )
    return comp["id"]


def test_get_component_no_agent_id():
    cid = _seed_component()
    row = ro.get_component(cid)
    assert row["canonical_name"] == "svc/a"
    assert "embedding" not in row  # lean projection preserved


def test_get_component_missing_raises():
    with pytest.raises(ValueError):
        ro.get_component("00000000-0000-0000-0000-000000000000")


def test_get_component_owner_no_agent_id():
    cid = _seed_component()
    owner = ro.get_component_owner(cid)
    assert owner["owner_agent_id"] == "sme-a"
    assert owner["component_status"] == "active"

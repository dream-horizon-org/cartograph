"""Phase 3: component_doc_md persists through upsert_component create + update."""

from shared.db import execute_mutate
from cartograph_mcp.tools import components, resources


def _iter(agent_factory, aid: str, plane: str):
    execute_mutate(
        """INSERT INTO agent_runs (agent_id, agent_type, plane, status)
           VALUES (%s, 'iterator', %s, 'idle')""",
        (aid, plane),
    )
    return aid


def _sme(agent_factory, sid: str, resource_id: str):
    agent_factory(sid, "sme")
    execute_mutate(
        """INSERT INTO resource_component_agents (resource_id, component_id, agent_id)
           VALUES (%s, NULL, %s)""",
        (resource_id, sid),
    )


def test_component_doc_md_set_on_create(agent_factory):
    _iter(agent_factory, "i", "github")
    r = resources.upsert_resource("i", "github", "repo", "o/r")
    _sme(agent_factory, "s", r["id"])
    c = components.upsert_component("s", {
        "canonical_name": "o/r", "display_name": "R",
        "component_type": "application",
        "component_doc_md": "# R\nDoes X.",
    })
    assert c["component_doc_md"] == "# R\nDoes X."


def test_component_doc_md_updated_when_provided(agent_factory):
    _iter(agent_factory, "i", "github")
    r = resources.upsert_resource("i", "github", "repo", "o/r")
    _sme(agent_factory, "s", r["id"])
    c1 = components.upsert_component("s", {
        "canonical_name": "o/r", "display_name": "R",
        "component_type": "application",
        "component_doc_md": "v1",
    })
    c2 = components.upsert_component("s", {
        "canonical_name": "o/r", "display_name": "R",
        "component_type": "application",
        "component_doc_md": "v2",
    })
    assert c1["id"] == c2["id"]
    assert c2["component_doc_md"] == "v2"


def test_component_doc_md_preserved_when_omitted(agent_factory):
    """COALESCE: omitting the key on a later update must not wipe the doc."""
    _iter(agent_factory, "i", "github")
    r = resources.upsert_resource("i", "github", "repo", "o/r")
    _sme(agent_factory, "s", r["id"])
    components.upsert_component("s", {
        "canonical_name": "o/r", "display_name": "R",
        "component_type": "application",
        "component_doc_md": "keep me",
    })
    updated = components.upsert_component("s", {
        "canonical_name": "o/r", "display_name": "R prime",
        "component_type": "application",
        # component_doc_md intentionally omitted
    })
    assert updated["component_doc_md"] == "keep me"


def test_component_doc_md_nullable_on_create(agent_factory):
    _iter(agent_factory, "i", "github")
    r = resources.upsert_resource("i", "github", "repo", "o/r")
    _sme(agent_factory, "s", r["id"])
    c = components.upsert_component("s", {
        "canonical_name": "o/r", "display_name": "R",
        "component_type": "application",
    })
    assert c["component_doc_md"] is None

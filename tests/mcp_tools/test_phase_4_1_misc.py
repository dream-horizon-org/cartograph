"""Phase 4.1 small-scope fixes: mark_resource_done idempotency,
nominate_consolidation metadata param, get_my_components."""

import json

import pytest

from shared.db import execute, execute_mutate, execute_one
from cartograph_mcp.tools import (
    components, consolidation, mutation, resources,
)


# ---------- fixtures ----------

def _iter(agent_factory, aid: str = "i"):
    execute_mutate(
        """INSERT INTO agent_runs (agent_id, agent_type, plane, status)
           VALUES (%s, 'iterator', 'github', 'idle')""",
        (aid,),
    )
    return aid


def _sme_with_component(agent_factory, aid: str, cname: str, identifier: str):
    r = resources.upsert_resource("i", "github", "repo", identifier)
    agent_factory(aid, "sme")
    execute_mutate(
        "INSERT INTO resource_component_agents (resource_id, component_id, agent_id) "
        "VALUES (%s, NULL, %s)",
        (r["id"], aid),
    )
    c = components.upsert_component(aid, {
        "canonical_name": cname, "display_name": cname,
        "component_type": "application",
    })
    return c["id"], str(r["id"])


# ---------- 4.1.5: mark_resource_done idempotency ----------


def test_mark_resource_done_is_idempotent(agent_factory):
    _iter(agent_factory)
    _, rid = _sme_with_component(agent_factory, "sme-a", "o/a", "o/a")
    # First call — flips to done.
    r1 = resources.mark_resource_done("sme-a", rid)
    assert r1["status"] == "done"
    # Second call — still succeeds, returns same done row. No error.
    r2 = resources.mark_resource_done("sme-a", rid)
    assert r2["status"] == "done"
    assert str(r2["id"]) == str(r1["id"])


# ---------- 4.1.6: nominate_consolidation metadata param ----------


def test_nominate_consolidation_persists_metadata(agent_factory):
    _iter(agent_factory)
    ca, _ = _sme_with_component(agent_factory, "sme-a", "o/a", "o/a")
    cb, _ = _sme_with_component(agent_factory, "sme-b", "o/b", "o/b")
    cons = consolidation.nominate_consolidation(
        "sme-a", ca, cb, "merge", 0.9, "m",
        metadata={"demo": True, "cosine": 0.87, "evidence": ["attr-1", "attr-2"]},
    )
    row = execute_one(
        "SELECT metadata FROM consolidations WHERE id=%s", (cons["id"],)
    )
    assert row["metadata"]["demo"] is True
    assert row["metadata"]["cosine"] == 0.87
    assert row["metadata"]["evidence"] == ["attr-1", "attr-2"]


def test_nominate_consolidation_metadata_defaults_to_empty(agent_factory):
    _iter(agent_factory)
    ca, _ = _sme_with_component(agent_factory, "sme-a", "o/a", "o/a")
    cb, _ = _sme_with_component(agent_factory, "sme-b", "o/b", "o/b")
    # Omit metadata param.
    cons = consolidation.nominate_consolidation(
        "sme-a", ca, cb, "merge", 0.9, "m"
    )
    row = execute_one(
        "SELECT metadata FROM consolidations WHERE id=%s", (cons["id"],)
    )
    assert row["metadata"] == {}


# ---------- 4.1.7: get_my_components ----------


def test_get_my_components_returns_single_for_sme(agent_factory):
    _iter(agent_factory)
    ca, _ = _sme_with_component(agent_factory, "sme-a", "o/a", "o/a")
    rows = components.get_my_components("sme-a")
    assert len(rows) == 1
    assert str(rows[0]["id"]) == str(ca)
    assert rows[0]["canonical_name"] == "o/a"
    assert rows[0]["status"] == "active"


def test_get_my_components_excludes_decommissioned(agent_factory):
    _iter(agent_factory)
    ca, _ = _sme_with_component(agent_factory, "sme-a", "o/a", "o/a")
    execute_mutate(
        "UPDATE components SET status='decommissioned' WHERE id=%s", (ca,)
    )
    assert components.get_my_components("sme-a") == []


def test_get_my_components_surfaces_split_briefing(agent_factory):
    """After spawn_child_agent, child should find their component with
    split_briefing populated."""
    _iter(agent_factory)
    ca, ra = _sme_with_component(agent_factory, "sme-a", "o/a", "o/a")
    agent_factory("res", "resolver")
    # Parent slice needed at top-level for 4.1.4 guard.
    execute_mutate(
        "UPDATE components SET source_slice=%s::jsonb WHERE id=%s",
        (json.dumps({ra: {"paths": ["x/", "y/"]}}), ca),
    )
    cons = consolidation.nominate_consolidation(
        "sme-a", ca, None, "split", 0.9, "split"
    )
    consolidation.review_consolidation(
        "res", cons["id"], 0.95, "approve", "M", "sme-a"
    )
    mutation.spawn_child_agent(
        "sme-a", cons["id"], "sme-child",
        {"canonical_name": "o/a-child", "display_name": "child",
         "component_type": "application"},
        {ra: {"paths": ["y/"]}},
        "carved y/ out for demo purposes",
    )
    rows = components.get_my_components("sme-child")
    assert len(rows) == 1
    assert rows[0]["canonical_name"] == "o/a-child"
    assert "carved y/" in rows[0]["split_briefing"]
    assert rows[0]["split_from_component_id"] is not None


def test_get_my_components_empty_for_non_owner(agent_factory):
    agent_factory("lone", "sme")
    assert components.get_my_components("lone") == []

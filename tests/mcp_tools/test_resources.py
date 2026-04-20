"""Tests for resources tools: upsert (iterator-only), read, mark_done (SME-only)."""

import uuid
import pytest

from cartograph_mcp.tools import resources


# ============ upsert_resource ============


def _iterator(factory, id_: str, plane: str):
    """Create iterator with a plane set."""
    from shared.db import execute_mutate
    execute_mutate(
        """INSERT INTO agent_runs (agent_id, agent_type, plane, status)
           VALUES (%s, 'iterator', %s, 'idle')""",
        (id_, plane),
    )
    return id_


def test_iterator_can_upsert_resource(agent_factory):
    _iterator(agent_factory, "iter-gh", "github")
    row = resources.upsert_resource(
        agent_id="iter-gh",
        plane="github",
        resource_type="repo",
        identifier="dream11/feeds-agg-v2",
        access_desc="clone via SSH",
        metadata={"default_branch": "master"},
    )
    assert row["plane"] == "github"
    assert row["resource_type"] == "repo"
    assert row["identifier"] == "dream11/feeds-agg-v2"
    assert row["access_desc"] == "clone via SSH"
    assert row["metadata"] == {"default_branch": "master"}
    assert row["status"] == "pending"


def test_upsert_resource_is_idempotent(agent_factory):
    _iterator(agent_factory, "iter-gh", "github")
    r1 = resources.upsert_resource(
        "iter-gh", "github", "repo", "dream11/feeds-agg-v2", "old",
    )
    r2 = resources.upsert_resource(
        "iter-gh", "github", "repo", "dream11/feeds-agg-v2", "new", {"k": "v"},
    )
    assert r1["id"] == r2["id"]
    assert r2["access_desc"] == "new"
    assert r2["metadata"] == {"k": "v"}


def test_non_iterator_cannot_upsert(agent_factory):
    agent_factory("sme-1", "sme")
    with pytest.raises(ValueError, match="Only iterator"):
        resources.upsert_resource("sme-1", "github", "repo", "x")


def test_iterator_cannot_upsert_wrong_plane(agent_factory):
    _iterator(agent_factory, "iter-gh", "github")
    with pytest.raises(ValueError, match="cannot write resources for plane"):
        resources.upsert_resource("iter-gh", "cloud", "r53_chain", "x.local")


def test_upsert_invalid_plane(agent_factory):
    _iterator(agent_factory, "iter-gh", "github")
    with pytest.raises(ValueError, match="Invalid plane"):
        resources.upsert_resource("iter-gh", "banana", "repo", "x")


def test_upsert_empty_fields_rejected(agent_factory):
    _iterator(agent_factory, "iter-gh", "github")
    with pytest.raises(ValueError, match="resource_type cannot be empty"):
        resources.upsert_resource("iter-gh", "github", "  ", "x")
    with pytest.raises(ValueError, match="identifier cannot be empty"):
        resources.upsert_resource("iter-gh", "github", "repo", "  ")


def test_unknown_agent_rejected(agent_factory):
    with pytest.raises(ValueError, match="not found"):
        resources.upsert_resource("nobody", "github", "repo", "x")


# ============ get_resource / list ============


def test_get_resource(agent_factory):
    _iterator(agent_factory, "iter-gh", "github")
    r = resources.upsert_resource("iter-gh", "github", "repo", "x")
    fetched = resources.get_resource("iter-gh", r["id"])
    assert fetched["id"] == r["id"]


def test_get_unknown_resource(agent_factory):
    _iterator(agent_factory, "iter-gh", "github")
    with pytest.raises(ValueError, match="Resource .* not found"):
        resources.get_resource("iter-gh", str(uuid.uuid4()))


def test_list_resources_for_plane(agent_factory):
    _iterator(agent_factory, "iter-gh", "github")
    _iterator(agent_factory, "iter-cloud", "cloud")
    resources.upsert_resource("iter-gh", "github", "repo", "a")
    resources.upsert_resource("iter-gh", "github", "repo", "b")
    resources.upsert_resource("iter-cloud", "cloud", "r53_chain", "x.local")

    github = resources.list_resources_for_plane("iter-gh", "github")
    assert len(github) == 2
    assert {r["identifier"] for r in github} == {"a", "b"}

    cloud = resources.list_resources_for_plane("iter-cloud", "cloud")
    assert len(cloud) == 1


def test_list_all_resources(agent_factory):
    _iterator(agent_factory, "iter-gh", "github")
    agent_factory("orch-1", "orchestrator")
    resources.upsert_resource("iter-gh", "github", "repo", "a")
    resources.upsert_resource("iter-gh", "github", "repo", "b")

    all_ = resources.list_all_resources("orch-1")
    assert len(all_) == 2


def test_list_all_resources_filter_by_status(agent_factory):
    _iterator(agent_factory, "iter-gh", "github")
    agent_factory("orch-1", "orchestrator")
    resources.upsert_resource("iter-gh", "github", "repo", "a")

    pending = resources.list_all_resources("orch-1", status="pending")
    assert len(pending) == 1
    done = resources.list_all_resources("orch-1", status="done")
    assert len(done) == 0


def test_list_all_resources_invalid_status(agent_factory):
    agent_factory("orch-1", "orchestrator")
    with pytest.raises(ValueError, match="Invalid status"):
        resources.list_all_resources("orch-1", status="banana")


def test_get_resource_counts(agent_factory):
    _iterator(agent_factory, "iter-gh", "github")
    _iterator(agent_factory, "iter-cloud", "cloud")
    agent_factory("orch-1", "orchestrator")
    resources.upsert_resource("iter-gh", "github", "repo", "a")
    resources.upsert_resource("iter-gh", "github", "repo", "b")
    resources.upsert_resource("iter-cloud", "cloud", "r53", "x")

    counts = resources.get_resource_counts("orch-1")
    rows = counts["by_plane_status"]
    bp = {(r["plane"], r["status"]): r["cnt"] for r in rows}
    assert bp[("github", "pending")] == 2
    assert bp[("cloud", "pending")] == 1


# ============ mark_resource_done ============


def test_sme_can_mark_resource_done(agent_factory):
    from shared.db import execute_mutate
    _iterator(agent_factory, "iter-gh", "github")
    agent_factory("sme-1", "sme")
    r = resources.upsert_resource("iter-gh", "github", "repo", "x")

    # Create component + RCA link so SME is "assigned"
    comp = execute_mutate(
        """INSERT INTO components (canonical_name, display_name, component_type)
           VALUES ('test/x', 'x', 'application')"""
    )
    from shared.db import execute_one
    comp_row = execute_one("SELECT id FROM components WHERE canonical_name = 'test/x'")
    execute_mutate(
        """INSERT INTO resource_component_agents (resource_id, component_id, agent_id)
           VALUES (%s, %s, %s)""",
        (r["id"], comp_row["id"], "sme-1"),
    )

    updated = resources.mark_resource_done("sme-1", r["id"])
    assert updated["status"] == "done"


def test_non_sme_cannot_mark_done(agent_factory):
    _iterator(agent_factory, "iter-gh", "github")
    r = resources.upsert_resource("iter-gh", "github", "repo", "x")
    with pytest.raises(ValueError, match="Only SMEs"):
        resources.mark_resource_done("iter-gh", r["id"])


def test_sme_not_assigned_cannot_mark_done(agent_factory):
    _iterator(agent_factory, "iter-gh", "github")
    agent_factory("sme-1", "sme")
    r = resources.upsert_resource("iter-gh", "github", "repo", "x")
    with pytest.raises(ValueError, match="not assigned to resource"):
        resources.mark_resource_done("sme-1", r["id"])

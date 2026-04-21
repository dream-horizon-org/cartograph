"""Tests for bulk_spawn_smes + decommission tools (Phase 2 kickoff)."""

import uuid
import pytest

from cartograph_mcp.tools import resources, agent_lifecycle
from shared.db import execute_mutate, execute_one, execute


class _FakeAgentManager:
    """Lightweight stand-in for AgentManager.create_agent in unit tests.

    Emulates only the DB side-effects (agent_runs row + RCA row + resource
    status flip). No workspace, no .mcp.json. Good enough to validate the
    bulk_spawn_smes logic without spawning real claude subprocesses.
    """
    def __init__(self):
        self.spawned = []

    def create_agent(self, agent_type, plane=None, resource_id=None):
        agent_id = f"sme-{uuid.uuid4().hex[:8]}"
        execute_mutate(
            """INSERT INTO agent_runs (agent_id, agent_type, status, workspace_path)
               VALUES (%s, %s, 'idle', %s)""",
            (agent_id, agent_type, f"/tmp/test/{agent_id}"),
        )
        if agent_type == "sme" and resource_id:
            execute_mutate(
                """INSERT INTO resource_component_agents
                   (resource_id, component_id, agent_id)
                   VALUES (%s, NULL, %s)""",
                (resource_id, agent_id),
            )
            execute_mutate(
                "UPDATE resources SET status='assigned' WHERE id=%s AND status='pending'",
                (resource_id,),
            )
        self.spawned.append(agent_id)
        return agent_id


def _iterator(agent_factory, agent_id: str, plane: str):
    execute_mutate(
        """INSERT INTO agent_runs (agent_id, agent_type, plane, status)
           VALUES (%s, 'iterator', %s, 'idle')""",
        (agent_id, plane),
    )
    return agent_id


# ============ bulk_spawn_smes ============


def test_bulk_spawn_smes_all_pending(agent_factory):
    _iterator(agent_factory, "iter-gh", "github")
    agent_factory("orch-1", "orchestrator")
    a = resources.upsert_resource("iter-gh", "github", "repo", "a")
    b = resources.upsert_resource("iter-gh", "github", "repo", "b")

    mgr = _FakeAgentManager()
    out = resources.bulk_spawn_smes(
        agent_id="orch-1",
        plane="github",
        agent_manager=mgr,
        all_pending=True,
    )
    assert out["spawned"] == 2
    assert out["skipped_already_assigned"] == []
    spawned_rids = {item["resource_id"] for item in out["items"]}
    assert spawned_rids == {str(a["id"]), str(b["id"])}

    # Resources now 'assigned', RCA rows exist with null component.
    for r in (a, b):
        status = execute_one("SELECT status FROM resources WHERE id=%s", (r["id"],))
        assert status["status"] == "assigned"
        rca = execute_one(
            "SELECT component_id FROM resource_component_agents WHERE resource_id=%s",
            (r["id"],),
        )
        assert rca is not None
        assert rca["component_id"] is None


def test_bulk_spawn_smes_explicit_ids(agent_factory):
    _iterator(agent_factory, "iter-gh", "github")
    agent_factory("orch-1", "orchestrator")
    a = resources.upsert_resource("iter-gh", "github", "repo", "a")
    resources.upsert_resource("iter-gh", "github", "repo", "b")

    mgr = _FakeAgentManager()
    out = resources.bulk_spawn_smes(
        agent_id="orch-1", plane="github",
        agent_manager=mgr, resource_ids=[str(a["id"])],
    )
    assert out["spawned"] == 1
    assert out["items"][0]["resource_id"] == str(a["id"])


def test_bulk_spawn_smes_skips_already_assigned(agent_factory):
    _iterator(agent_factory, "iter-gh", "github")
    agent_factory("orch-1", "orchestrator")
    a = resources.upsert_resource("iter-gh", "github", "repo", "a")

    mgr = _FakeAgentManager()
    # First spawn succeeds
    resources.bulk_spawn_smes(
        agent_id="orch-1", plane="github", agent_manager=mgr, all_pending=True,
    )
    # Second call skips — RCA row already exists
    # (but resource is now 'assigned' so it won't be in pending either; use explicit ids)
    out = resources.bulk_spawn_smes(
        agent_id="orch-1", plane="github",
        agent_manager=mgr, resource_ids=[str(a["id"])],
    )
    # Explicit ids filter to status='pending', so nothing to spawn
    assert out["spawned"] == 0


def test_bulk_spawn_smes_creates_tasks_when_description_given(agent_factory):
    _iterator(agent_factory, "iter-gh", "github")
    agent_factory("orch-1", "orchestrator")
    resources.upsert_resource("iter-gh", "github", "repo", "a")

    mgr = _FakeAgentManager()
    out = resources.bulk_spawn_smes(
        agent_id="orch-1", plane="github", agent_manager=mgr,
        all_pending=True, task_description="Analyse assigned resource",
    )
    assert out["spawned"] == 1
    assert out["items"][0]["task_id"] is not None
    task = execute_one(
        "SELECT owner_agent_id, worker_agent_id, status, description FROM tasks WHERE id=%s",
        (out["items"][0]["task_id"],),
    )
    assert task["owner_agent_id"] == "orch-1"
    assert task["worker_agent_id"] == out["items"][0]["agent_id"]
    assert task["status"] == "BW"
    assert task["description"] == "Analyse assigned resource"


def test_bulk_spawn_smes_non_orchestrator_rejected(agent_factory):
    _iterator(agent_factory, "iter-gh", "github")
    mgr = _FakeAgentManager()
    with pytest.raises(ValueError, match="Only orchestrator"):
        resources.bulk_spawn_smes(
            agent_id="iter-gh", plane="github",
            agent_manager=mgr, all_pending=True,
        )


def test_bulk_spawn_smes_blank_wipe_refused(agent_factory):
    agent_factory("orch-1", "orchestrator")
    mgr = _FakeAgentManager()
    with pytest.raises(ValueError, match="blank-wipe"):
        resources.bulk_spawn_smes(
            agent_id="orch-1", plane="github", agent_manager=mgr,
        )


# ============ decommission_agent ============


def test_decommission_agent_leave(agent_factory):
    agent_factory("orch-1", "orchestrator")
    _iterator(agent_factory, "iter-gh", "github")
    r = resources.upsert_resource("iter-gh", "github", "repo", "a")
    mgr = _FakeAgentManager()
    out = resources.bulk_spawn_smes(
        agent_id="orch-1", plane="github", agent_manager=mgr, all_pending=True,
    )
    sme_id = out["items"][0]["agent_id"]

    result = agent_lifecycle.decommission_agent(
        agent_id="orch-1",
        target_agent_id=sme_id,
        reason="manual test",
        resource_action="leave",
    )
    assert result["decommissioned"] == sme_id
    assert result["resource_action"] == "leave"
    # Status flipped
    row = execute_one("SELECT status FROM agent_runs WHERE agent_id=%s", (sme_id,))
    assert row["status"] == "decommissioned"
    # RCA row preserved
    rca = execute_one(
        "SELECT 1 FROM resource_component_agents WHERE agent_id=%s", (sme_id,)
    )
    assert rca is not None


def test_decommission_agent_reset(agent_factory):
    agent_factory("orch-1", "orchestrator")
    _iterator(agent_factory, "iter-gh", "github")
    r = resources.upsert_resource("iter-gh", "github", "repo", "a")
    mgr = _FakeAgentManager()
    out = resources.bulk_spawn_smes(
        agent_id="orch-1", plane="github", agent_manager=mgr, all_pending=True,
    )
    sme_id = out["items"][0]["agent_id"]

    result = agent_lifecycle.decommission_agent(
        agent_id="orch-1",
        target_agent_id=sme_id,
        reason="try again",
        resource_action="reset",
    )
    assert result["cascade"]["reset"] == [str(r["id"])]
    # Resource back to pending; RCA row gone
    row = execute_one("SELECT status FROM resources WHERE id=%s", (r["id"],))
    assert row["status"] == "pending"
    assert execute_one(
        "SELECT 1 FROM resource_component_agents WHERE agent_id=%s", (sme_id,)
    ) is None


def test_decommission_agent_reject(agent_factory):
    agent_factory("orch-1", "orchestrator")
    _iterator(agent_factory, "iter-gh", "github")
    r = resources.upsert_resource("iter-gh", "github", "repo", "a")
    mgr = _FakeAgentManager()
    out = resources.bulk_spawn_smes(
        agent_id="orch-1", plane="github", agent_manager=mgr, all_pending=True,
    )
    sme_id = out["items"][0]["agent_id"]

    result = agent_lifecycle.decommission_agent(
        agent_id="orch-1",
        target_agent_id=sme_id,
        reason="this resource was bogus",
        resource_action="reject",
    )
    assert result["cascade"]["rejected"] == [str(r["id"])]
    row = execute_one(
        "SELECT status, rejected_by, rejected_reason FROM resources WHERE id=%s",
        (r["id"],),
    )
    assert row["status"] == "rejected"
    assert row["rejected_by"] == "orch-1"
    assert "bogus" in row["rejected_reason"]


def test_decommission_agent_non_orchestrator_rejected(agent_factory):
    agent_factory("sme-1", "sme")
    _iterator(agent_factory, "iter-gh", "github")
    with pytest.raises(ValueError, match="Only orchestrator"):
        agent_lifecycle.decommission_agent(
            "iter-gh", "sme-1", "no"
        )


def test_decommission_agent_self_rejected(agent_factory):
    agent_factory("orch-1", "orchestrator")
    with pytest.raises(ValueError, match="cannot decommission itself"):
        agent_lifecycle.decommission_agent("orch-1", "orch-1", "nope")


def test_decommission_agent_requires_reason(agent_factory):
    agent_factory("orch-1", "orchestrator")
    agent_factory("sme-1", "sme")
    with pytest.raises(ValueError, match="reason is required"):
        agent_lifecycle.decommission_agent("orch-1", "sme-1", "")


# ============ decommission_agents_bulk ============


def test_decommission_agents_bulk_by_type(agent_factory):
    agent_factory("orch-1", "orchestrator")
    _iterator(agent_factory, "iter-gh", "github")
    for i in range(3):
        resources.upsert_resource("iter-gh", "github", "repo", f"r{i}")
    mgr = _FakeAgentManager()
    resources.bulk_spawn_smes(
        agent_id="orch-1", plane="github", agent_manager=mgr, all_pending=True,
    )

    result = agent_lifecycle.decommission_agents_bulk(
        agent_id="orch-1",
        reason="mass reset",
        agent_type="sme",
        resource_action="reset",
    )
    assert result["count"] == 3
    # All SMEs decommissioned
    running_smes = execute(
        "SELECT agent_id FROM agent_runs WHERE agent_type='sme' AND status != 'decommissioned'"
    )
    assert running_smes == []
    # All resources back to pending
    pendings = execute("SELECT COUNT(*) AS n FROM resources WHERE status='pending'")
    assert pendings[0]["n"] == 3


def test_decommission_agents_bulk_refuses_orchestrator_type(agent_factory):
    agent_factory("orch-1", "orchestrator")
    with pytest.raises(ValueError, match="refusing to decommission orchestrator"):
        agent_lifecycle.decommission_agents_bulk(
            agent_id="orch-1", reason="nope", agent_type="orchestrator",
        )


def test_decommission_agents_bulk_blank_wipe_refused(agent_factory):
    agent_factory("orch-1", "orchestrator")
    with pytest.raises(ValueError, match="blank-wipe"):
        agent_lifecycle.decommission_agents_bulk(
            agent_id="orch-1", reason="nope",
        )


def test_decommission_agents_bulk_never_targets_self(agent_factory):
    agent_factory("orch-1", "orchestrator")
    agent_factory("orch-spare", "orchestrator")
    result = agent_lifecycle.decommission_agents_bulk(
        agent_id="orch-1",
        reason="cleanup orphan orchestrators",
        agent_ids=["orch-1", "orch-spare"],
    )
    # Caller excluded; only the other one decommissioned
    assert result["decommissioned"] == ["orch-spare"]


# ============ decommission_component(s) ============


def test_decommission_component(agent_factory):
    agent_factory("orch-1", "orchestrator")
    execute_mutate(
        """INSERT INTO components (canonical_name, display_name, component_type)
           VALUES ('test/x', 'x', 'application')"""
    )
    cid = execute_one("SELECT id FROM components WHERE canonical_name='test/x'")["id"]
    out = agent_lifecycle.decommission_component("orch-1", str(cid), reason="bogus")
    assert out["decommissioned"] == str(cid)
    row = execute_one("SELECT status FROM components WHERE id=%s", (cid,))
    assert row["status"] == "decommissioned"


def test_decommission_component_already_decom_rejected(agent_factory):
    agent_factory("orch-1", "orchestrator")
    execute_mutate(
        """INSERT INTO components (canonical_name, display_name, component_type, status)
           VALUES ('test/y', 'y', 'application', 'decommissioned')"""
    )
    cid = execute_one("SELECT id FROM components WHERE canonical_name='test/y'")["id"]
    with pytest.raises(ValueError, match="already decommissioned"):
        agent_lifecycle.decommission_component("orch-1", str(cid), "retry")


def test_decommission_components_bulk(agent_factory):
    agent_factory("orch-1", "orchestrator")
    ids = []
    for name in ("a", "b", "c"):
        execute_mutate(
            """INSERT INTO components (canonical_name, display_name, component_type)
               VALUES (%s, %s, 'application')""",
            (f"test/{name}", name),
        )
        cid = execute_one(
            "SELECT id FROM components WHERE canonical_name=%s", (f"test/{name}",)
        )["id"]
        ids.append(str(cid))

    out = agent_lifecycle.decommission_components_bulk("orch-1", ids, "sweep")
    assert out["count"] == 3
    rows = execute(
        "SELECT status FROM components WHERE id = ANY(%s::uuid[])", (ids,)
    )
    assert all(r["status"] == "decommissioned" for r in rows)


def test_decommission_components_bulk_blank_wipe_refused(agent_factory):
    agent_factory("orch-1", "orchestrator")
    with pytest.raises(ValueError, match="blank-wipe"):
        agent_lifecycle.decommission_components_bulk("orch-1", [], "no")

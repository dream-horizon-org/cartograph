"""Tests for resources tools: upsert (iterator-only), read, mark_done (SME-only)."""

import uuid
import pytest

from cartograph_mcp.tools import resources
from shared.db import execute


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


# ============ upsert_resources_bulk ============


def test_bulk_upsert_inserts_many(agent_factory):
    _iterator(agent_factory, "iter-gh", "github")
    items = [
        {"resource_type": "repo", "identifier": f"dream11/svc-{i}",
         "access_desc": "clone via SSH", "metadata": {"idx": i}}
        for i in range(25)
    ]
    out = resources.upsert_resources_bulk("iter-gh", "github", items)
    assert out["inserted_or_updated"] == 25
    assert len(out["ids"]) == 25

    listed = resources.list_resources_for_plane("iter-gh", "github")
    assert len(listed) == 25
    assert all(r["plane"] == "github" for r in listed)


def test_bulk_upsert_is_idempotent(agent_factory):
    _iterator(agent_factory, "iter-gh", "github")
    items = [{"resource_type": "repo", "identifier": "dream11/a"}]
    r1 = resources.upsert_resources_bulk("iter-gh", "github", items)
    r2 = resources.upsert_resources_bulk(
        "iter-gh", "github",
        [{"resource_type": "repo", "identifier": "dream11/a", "access_desc": "updated"}],
    )
    assert r1["ids"] == r2["ids"]
    listed = resources.list_resources_for_plane("iter-gh", "github")
    assert len(listed) == 1
    assert listed[0]["access_desc"] == "updated"


def test_bulk_upsert_non_iterator_rejected(agent_factory):
    agent_factory("sme-1", "sme")
    with pytest.raises(ValueError, match="Only iterator"):
        resources.upsert_resources_bulk(
            "sme-1", "github", [{"resource_type": "repo", "identifier": "x"}]
        )


def test_bulk_upsert_wrong_plane_rejected(agent_factory):
    _iterator(agent_factory, "iter-gh", "github")
    with pytest.raises(ValueError, match="cannot write resources for plane"):
        resources.upsert_resources_bulk(
            "iter-gh", "cloud", [{"resource_type": "r53_chain", "identifier": "x"}]
        )


def test_bulk_upsert_empty_fields_rejected(agent_factory):
    _iterator(agent_factory, "iter-gh", "github")
    with pytest.raises(ValueError, match=r"items\[1\]\.identifier cannot be empty"):
        resources.upsert_resources_bulk(
            "iter-gh", "github",
            [
                {"resource_type": "repo", "identifier": "ok"},
                {"resource_type": "repo", "identifier": "  "},
            ],
        )


def test_bulk_upsert_empty_list_rejected(agent_factory):
    _iterator(agent_factory, "iter-gh", "github")
    with pytest.raises(ValueError, match="non-empty list"):
        resources.upsert_resources_bulk("iter-gh", "github", [])


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


# ============ reject_resource / reject_resources_bulk ============


def test_iterator_can_reject_own_plane_resource(agent_factory):
    _iterator(agent_factory, "iter-gh", "github")
    r = resources.upsert_resource("iter-gh", "github", "branch", "main")
    out = resources.reject_resource("iter-gh", r["id"], reason="sub-artifact")
    assert out["rejected"] == 1
    assert out["ids"] == [str(r["id"])]

    from shared.db import execute_one
    row = execute_one("SELECT status, rejected_by, rejected_reason FROM resources WHERE id=%s", (r["id"],))
    assert row["status"] == "rejected"
    assert row["rejected_by"] == "iter-gh"
    assert row["rejected_reason"] == "sub-artifact"


def test_reject_requires_reason(agent_factory):
    _iterator(agent_factory, "iter-gh", "github")
    r = resources.upsert_resource("iter-gh", "github", "branch", "main")
    with pytest.raises(ValueError, match="reason is required"):
        resources.reject_resource("iter-gh", r["id"], reason="")
    with pytest.raises(ValueError, match="reason is required"):
        resources.reject_resources_bulk(
            "iter-gh", "github", resource_types=["branch"], reason=""
        )


def test_reject_blank_wipe_refused(agent_factory):
    _iterator(agent_factory, "iter-gh", "github")
    with pytest.raises(ValueError, match="blank-wipe"):
        resources.reject_resources_bulk(
            "iter-gh", "github", reason="nope"
        )


def test_iterator_cannot_reject_other_plane(agent_factory):
    _iterator(agent_factory, "iter-gh", "github")
    _iterator(agent_factory, "iter-cloud", "cloud")
    r = resources.upsert_resource("iter-cloud", "cloud", "r53", "x.local")
    with pytest.raises(ValueError, match="cannot reject resources on plane"):
        resources.reject_resource("iter-gh", r["id"], reason="not mine")


def test_non_iterator_cannot_reject(agent_factory):
    agent_factory("sme-1", "sme")
    _iterator(agent_factory, "iter-gh", "github")
    r = resources.upsert_resource("iter-gh", "github", "branch", "main")
    with pytest.raises(ValueError, match="Only iterator"):
        resources.reject_resource("sme-1", r["id"], reason="no")


def test_orchestrator_force_reject(agent_factory):
    agent_factory("orch-1", "orchestrator")
    _iterator(agent_factory, "iter-gh", "github")
    r = resources.upsert_resource("iter-gh", "github", "branch", "main")
    out = resources.reject_resource("orch-1", r["id"], reason="override", force=True)
    assert out["rejected"] == 1


def test_bulk_reject_by_types(agent_factory):
    _iterator(agent_factory, "iter-gh", "github")
    resources.upsert_resource("iter-gh", "github", "repo", "dream11/svc")
    resources.upsert_resource("iter-gh", "github", "branch", "main")
    resources.upsert_resource("iter-gh", "github", "workflow", "ci.yml")
    resources.upsert_resource("iter-gh", "github", "webhook", "hook-1")

    out = resources.reject_resources_bulk(
        "iter-gh", "github",
        resource_types=["branch", "workflow", "webhook"],
        reason="over-granular",
    )
    assert out["rejected"] == 3
    assert out["skipped_cascade"] == []

    remaining = resources.list_resources_for_plane("iter-gh", "github")
    # list_resources_for_plane doesn't filter rejected; check status
    pending = [r for r in remaining if r["status"] == "pending"]
    rejected = [r for r in remaining if r["status"] == "rejected"]
    assert len(pending) == 1
    assert pending[0]["resource_type"] == "repo"
    assert len(rejected) == 3


def test_bulk_reject_by_ids(agent_factory):
    _iterator(agent_factory, "iter-gh", "github")
    a = resources.upsert_resource("iter-gh", "github", "branch", "main")
    b = resources.upsert_resource("iter-gh", "github", "branch", "dev")
    c = resources.upsert_resource("iter-gh", "github", "branch", "prod")
    out = resources.reject_resources_bulk(
        "iter-gh", "github",
        resource_ids=[str(a["id"]), str(c["id"])],
        reason="targeted",
    )
    assert out["rejected"] == 2
    assert set(out["ids"]) == {str(a["id"]), str(c["id"])}

    from shared.db import execute_one
    bb = execute_one("SELECT status FROM resources WHERE id=%s", (b["id"],))
    assert bb["status"] == "pending"


def test_bulk_reject_cascade_skip(agent_factory):
    """Rows already assigned to an SME via RCA are skipped."""
    from shared.db import execute_mutate, execute_one
    _iterator(agent_factory, "iter-gh", "github")
    agent_factory("sme-1", "sme")
    r = resources.upsert_resource("iter-gh", "github", "repo", "dream11/svc")

    execute_mutate(
        """INSERT INTO components (canonical_name, display_name, component_type)
           VALUES ('test/svc', 'svc', 'application')"""
    )
    comp = execute_one("SELECT id FROM components WHERE canonical_name='test/svc'")
    execute_mutate(
        """INSERT INTO resource_component_agents (resource_id, component_id, agent_id)
           VALUES (%s, %s, %s)""",
        (r["id"], comp["id"], "sme-1"),
    )

    out = resources.reject_resources_bulk(
        "iter-gh", "github", resource_ids=[str(r["id"])], reason="try"
    )
    assert out["rejected"] == 0
    assert out["skipped_cascade"] == [str(r["id"])]


def test_bulk_reject_skips_done(agent_factory):
    _iterator(agent_factory, "iter-gh", "github")
    r = resources.upsert_resource("iter-gh", "github", "branch", "main")
    from shared.db import execute_mutate
    execute_mutate("UPDATE resources SET status='done' WHERE id=%s", (r["id"],))

    out = resources.reject_resources_bulk(
        "iter-gh", "github", resource_ids=[str(r["id"])], reason="try"
    )
    assert out["rejected"] == 0
    assert out["skipped_terminal"] == [str(r["id"])]


def test_list_all_resources_excludes_rejected_by_default(agent_factory):
    _iterator(agent_factory, "iter-gh", "github")
    agent_factory("orch-1", "orchestrator")
    a = resources.upsert_resource("iter-gh", "github", "repo", "a")
    resources.upsert_resource("iter-gh", "github", "branch", "b")
    resources.reject_resources_bulk(
        "iter-gh", "github", resource_types=["branch"], reason="x"
    )

    active = resources.list_all_resources("orch-1")
    assert len(active) == 1
    assert active[0]["id"] == a["id"]

    rejected = resources.list_all_resources("orch-1", status="rejected")
    assert len(rejected) == 1
    assert rejected[0]["resource_type"] == "branch"


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


# ============ rejected-is-tombstone ============
#
# Bug: a rejected row used to block re-upsert of the same identifier.
# Fix: partial unique index `WHERE status != 'rejected'` so re-upsert
# inserts a fresh `pending` row and the tombstone stays as audit.


def test_upsert_after_reject_creates_fresh_pending_row(agent_factory):
    _iterator(agent_factory, "iter-gh", "github")
    # Admin rejects with force=True (no SME attached so no cascade).
    agent_factory("orch", "orchestrator")
    original = resources.upsert_resource(
        "iter-gh", "github", "repo", "dream11/kyc-service"
    )
    resources.reject_resource(
        "orch", original["id"], reason="test wipe", force=True
    )
    reborn = resources.upsert_resource(
        "iter-gh", "github", "repo", "dream11/kyc-service"
    )
    # Fresh row with a new id, status='pending'.
    assert reborn["id"] != original["id"]
    assert reborn["status"] == "pending"
    # Old tombstone still there with its audit trail intact.
    rows = execute(
        """SELECT id, status, rejected_reason FROM resources
           WHERE plane='github' AND resource_type='repo'
             AND identifier='dream11/kyc-service'
           ORDER BY created_at"""
    )
    assert len(rows) == 2
    assert rows[0]["status"] == "rejected"
    assert rows[0]["rejected_reason"] == "test wipe"
    assert rows[1]["status"] == "pending"


def test_live_rows_still_unique(agent_factory):
    """Partial index still enforces uniqueness among non-rejected rows."""
    _iterator(agent_factory, "iter-gh", "github")
    r1 = resources.upsert_resource(
        "iter-gh", "github", "repo", "dream11/foo",
        metadata={"v": 1},
    )
    # Second upsert on the same live identifier: UPDATE not INSERT.
    r2 = resources.upsert_resource(
        "iter-gh", "github", "repo", "dream11/foo",
        metadata={"v": 2},
    )
    assert r1["id"] == r2["id"]
    assert r2["metadata"] == {"v": 2}


def test_bulk_upsert_after_reject_creates_fresh_rows(agent_factory):
    _iterator(agent_factory, "iter-gh", "github")
    agent_factory("orch", "orchestrator")
    first = resources.upsert_resources_bulk("iter-gh", "github", [
        {"resource_type": "repo", "identifier": "dream11/kyc-service"},
        {"resource_type": "repo", "identifier": "dream11/payments"},
    ])
    # Reject all of them.
    for rid in first["ids"]:
        resources.reject_resource("orch", rid, reason="test wipe", force=True)
    # Re-upsert same identifiers → 2 fresh pending rows.
    second = resources.upsert_resources_bulk("iter-gh", "github", [
        {"resource_type": "repo", "identifier": "dream11/kyc-service"},
        {"resource_type": "repo", "identifier": "dream11/payments"},
    ])
    assert second["inserted_or_updated"] == 2
    assert set(second["ids"]).isdisjoint(set(first["ids"]))
    live = execute(
        """SELECT identifier FROM resources
           WHERE plane='github' AND status='pending'
           ORDER BY identifier"""
    )
    assert [r["identifier"] for r in live] == [
        "dream11/kyc-service", "dream11/payments",
    ]

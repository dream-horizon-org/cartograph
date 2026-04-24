"""Phase 4: tests for absorb_agent, spawn_child_agent, transfer_attributions."""

import json

import pytest

from shared.db import execute, execute_mutate, execute_one
from cartograph_mcp.tools import components, consolidation, mutation, resources


# ---------- fixtures ----------


def _iter(agent_factory, aid: str = "i", plane: str = "github"):
    execute_mutate(
        """INSERT INTO agent_runs (agent_id, agent_type, plane, status)
           VALUES (%s, 'iterator', %s, 'idle')""",
        (aid, plane),
    )
    return aid


def _sme_with_component(
    agent_factory, iterator_id: str, sme_id: str, identifier: str, cname: str,
    source_slice: dict | None = None,
) -> tuple[str, str]:
    """Seed a resource + SME + component. Returns (component_id, resource_id)."""
    r = resources.upsert_resource(iterator_id, "github", "repo", identifier)
    agent_factory(sme_id, "sme")
    execute_mutate(
        """INSERT INTO resource_component_agents (resource_id, component_id, agent_id)
           VALUES (%s, NULL, %s)""",
        (r["id"], sme_id),
    )
    cdata: dict = {
        "canonical_name": cname,
        "display_name": cname,
        "component_type": "application",
    }
    if source_slice is not None:
        cdata["source_slice"] = source_slice
    c = components.upsert_component(sme_id, cdata)
    return c["id"], str(r["id"])


def _m_state_merge(agent_factory, survivor_slice=None, target_slice=None):
    """Merge consolidation seeded to state M with sme-a as mutation_assigned_to."""
    _iter(agent_factory)
    ca, ra = _sme_with_component(
        agent_factory, "i", "sme-a", "o/a", "comp-a", survivor_slice
    )
    cb, rb = _sme_with_component(
        agent_factory, "i", "sme-b", "o/b", "comp-b", target_slice
    )
    agent_factory("res", "resolver")
    cons = consolidation.nominate_consolidation(
        "sme-a", ca, cb, "merge", 0.9, "merge"
    )
    execute_mutate("UPDATE consolidations SET status='R' WHERE id=%s", (cons["id"],))
    consolidation.review_consolidation(
        "res", cons["id"], 0.95, "approve", "M", "sme-a"
    )
    return {
        "cons_id": cons["id"], "comp_a": ca, "comp_b": cb,
        "res_a": ra, "res_b": rb,
    }


# ========================== absorb_agent ==========================


def test_absorb_agent_flips_target_and_sets_deactivation_cols(agent_factory):
    s = _m_state_merge(agent_factory)
    result = mutation.absorb_agent(
        "sme-a", s["cons_id"], "sme-b",
        deactivation_reason="merged",
        deactivation_notes="B covered same hostnames",
    )
    assert result["absorbed"] == "sme-b"
    assert result["survivor"] == "sme-a"
    row = execute_one(
        "SELECT status, merged_into_agent_id, deactivation_reason, deactivation_notes "
        "FROM agent_runs WHERE agent_id='sme-b'"
    )
    assert row["status"] == "decommissioned"
    assert row["merged_into_agent_id"] == "sme-a"
    assert row["deactivation_reason"] == "merged"
    assert row["deactivation_notes"] == "B covered same hostnames"


def test_absorb_decommissions_target_component(agent_factory):
    s = _m_state_merge(agent_factory)
    mutation.absorb_agent("sme-a", s["cons_id"], "sme-b")
    b = execute_one("SELECT status FROM components WHERE id=%s", (s["comp_b"],))
    assert b["status"] == "decommissioned"


def test_absorb_unions_source_slices_per_resource(agent_factory):
    survivor_slice = {
        "res-1": {"plane": "github", "paths": ["services/a/"], "files": ["a.py"]}
    }
    target_slice = {
        "res-1": {"plane": "github", "paths": ["services/b/"], "files": ["b.py"]},
        "res-2": {"plane": "cloud",  "paths": ["infra/"]}
    }
    s = _m_state_merge(agent_factory, survivor_slice, target_slice)
    mutation.absorb_agent("sme-a", s["cons_id"], "sme-b")
    row = execute_one("SELECT source_slice FROM components WHERE id=%s", (s["comp_a"],))
    merged = row["source_slice"]
    # res-1 got both paths + both files; res-2 only exists in target.
    assert set(merged["res-1"]["paths"]) == {"services/a/", "services/b/"}
    assert set(merged["res-1"]["files"]) == {"a.py", "b.py"}
    assert merged["res-2"]["paths"] == ["infra/"]


def test_absorb_target_component_slice_stays_as_tombstone(agent_factory):
    target_slice = {"res-1": {"plane": "github", "paths": ["x/"]}}
    s = _m_state_merge(agent_factory, None, target_slice)
    mutation.absorb_agent("sme-a", s["cons_id"], "sme-b")
    row = execute_one("SELECT source_slice FROM components WHERE id=%s", (s["comp_b"],))
    # Target slice untouched.
    assert row["source_slice"] == target_slice


def test_absorb_repoints_rca_to_survivor(agent_factory):
    s = _m_state_merge(agent_factory)
    mutation.absorb_agent("sme-a", s["cons_id"], "sme-b")
    # Target's old resource now belongs to survivor + survivor's component.
    rows = execute(
        "SELECT resource_id, component_id, agent_id FROM resource_component_agents "
        "WHERE resource_id=%s",
        (s["res_b"],),
    )
    assert len(rows) == 1
    assert rows[0]["agent_id"] == "sme-a"
    assert str(rows[0]["component_id"]) == str(s["comp_a"])


def test_absorb_gates_on_consolidation_state_M(agent_factory):
    _iter(agent_factory)
    ca, _ = _sme_with_component(agent_factory, "i", "sme-a", "o/a", "a")
    cb, _ = _sme_with_component(agent_factory, "i", "sme-b", "o/b", "b")
    cons = consolidation.nominate_consolidation("sme-a", ca, cb, "merge", 0.9, "m")
    # Still in B2 — absorb must refuse.
    with pytest.raises(ValueError, match="status='M'"):
        mutation.absorb_agent("sme-a", cons["id"], "sme-b")


def test_absorb_gates_on_mutation_assigned_to(agent_factory):
    s = _m_state_merge(agent_factory)
    # sme-b tries to absorb sme-a, but only sme-a is mutation_assigned_to.
    with pytest.raises(ValueError, match="mutation_assigned_to"):
        mutation.absorb_agent("sme-b", s["cons_id"], "sme-a")


def test_absorb_refuses_target_not_on_consolidation(agent_factory):
    s = _m_state_merge(agent_factory)
    # Create another SME not on the consolidation.
    _sme_with_component(agent_factory, "i", "sme-x", "o/x", "comp-x")
    with pytest.raises(ValueError, match="not a party"):
        mutation.absorb_agent("sme-a", s["cons_id"], "sme-x")


def test_absorb_refuses_self(agent_factory):
    s = _m_state_merge(agent_factory)
    with pytest.raises(ValueError, match="cannot absorb self"):
        mutation.absorb_agent("sme-a", s["cons_id"], "sme-a")


def test_absorb_refuses_already_decommissioned(agent_factory):
    s = _m_state_merge(agent_factory)
    mutation.absorb_agent("sme-a", s["cons_id"], "sme-b")
    # Try again — target is already decommissioned.
    with pytest.raises(ValueError, match="already decommissioned"):
        mutation.absorb_agent("sme-a", s["cons_id"], "sme-b")


def test_absorb_preserves_chain_history_for_transitive_merges(agent_factory):
    """A merged into B, then B merged into C → A.merged_into stays B,
    B.merged_into becomes C. No flattening."""
    # Round 1: B absorbs A.
    s1 = _m_state_merge(agent_factory)
    mutation.absorb_agent("sme-a", s1["cons_id"], "sme-b")
    # Wait — mutation_assigned_to was sme-a, so sme-a ABSORBS sme-b. Readjust:
    # after that, sme-b merged into sme-a.
    row_b = execute_one(
        "SELECT merged_into_agent_id FROM agent_runs WHERE agent_id='sme-b'"
    )
    assert row_b["merged_into_agent_id"] == "sme-a"

    # Round 2: spin up sme-c, merge sme-a into sme-c (via a new consolidation).
    # Seed: create a component for sme-c, nominate, review to M with sme-c
    # as mutation_assigned_to, then absorb sme-a.
    cc, _ = _sme_with_component(agent_factory, "i", "sme-c", "o/c", "comp-c")
    cons = consolidation.nominate_consolidation(
        "sme-c", cc, s1["comp_a"], "merge", 0.9, "round 2"
    )
    execute_mutate("UPDATE consolidations SET status='R' WHERE id=%s", (cons["id"],))
    consolidation.review_consolidation(
        "res", cons["id"], 0.95, "approve", "M", "sme-c"
    )
    mutation.absorb_agent("sme-c", cons["id"], "sme-a")

    # Chain: sme-b -> sme-a -> sme-c. Single-hop pointers, not flattened.
    row_a = execute_one(
        "SELECT merged_into_agent_id FROM agent_runs WHERE agent_id='sme-a'"
    )
    assert row_a["merged_into_agent_id"] == "sme-c"
    row_b = execute_one(
        "SELECT merged_into_agent_id FROM agent_runs WHERE agent_id='sme-b'"
    )
    # sme-b's pointer still points at sme-a even though sme-a is now gone.
    assert row_b["merged_into_agent_id"] == "sme-a"


# ========================== spawn_child_agent ==========================


def _m_state_split(agent_factory, parent_slice=None):
    """Split consolidation in state M with sme-a as mutation_assigned_to."""
    _iter(agent_factory)
    ca, ra = _sme_with_component(
        agent_factory, "i", "sme-a", "o/a", "comp-a", parent_slice
    )
    agent_factory("res", "resolver")
    cons = consolidation.nominate_consolidation(
        "sme-a", ca, None, "split", 0.9, "two entry points"
    )
    execute_mutate("UPDATE consolidations SET status='R' WHERE id=%s", (cons["id"],))
    consolidation.review_consolidation(
        "res", cons["id"], 0.95, "approve", "M", "sme-a"
    )
    return {"cons_id": cons["id"], "comp_a": ca, "res_a": ra}


def test_spawn_child_atomic_carve_out(agent_factory):
    _iter(agent_factory)
    ca, ra = _sme_with_component(agent_factory, "i", "sme-a", "o/a", "comp-a")
    # Set up parent slice keyed by real resource UUID.
    parent_slice = {ra: {"plane": "github", "paths": ["services/x/", "services/y/"]}}
    execute_mutate(
        "UPDATE components SET source_slice=%s::jsonb WHERE id=%s",
        (json.dumps(parent_slice), ca),
    )
    agent_factory("res", "resolver")
    cons = consolidation.nominate_consolidation(
        "sme-a", ca, None, "split", 0.9, "two entry points"
    )
    execute_mutate("UPDATE consolidations SET status='R' WHERE id=%s", (cons["id"],))
    consolidation.review_consolidation(
        "res", cons["id"], 0.95, "approve", "M", "sme-a"
    )
    child_slice = {ra: {"plane": "github", "paths": ["services/y/"]}}
    result = mutation.spawn_child_agent(
        "sme-a", cons["id"], "sme-child",
        {"canonical_name": "o/child", "display_name": "child",
         "component_type": "application"},
        child_slice,
        "split out services/y into its own component",
    )
    assert result["child_agent_id"] == "sme-child"
    # Parent's slice shrank to only services/x/.
    row = execute_one("SELECT source_slice FROM components WHERE id=%s", (ca,))
    assert row["source_slice"][ra]["paths"] == ["services/x/"]
    # Child has its slice.
    row = execute_one(
        "SELECT source_slice, split_from_component_id, split_briefing, component_type "
        "FROM components WHERE id=%s", (result["child_component_id"],)
    )
    assert row["source_slice"] == child_slice
    assert str(row["split_from_component_id"]) == str(ca)
    assert "services/y" in row["split_briefing"]
    # Child agent row exists + is idle + type sme.
    child = execute_one(
        "SELECT agent_type, status FROM agent_runs WHERE agent_id='sme-child'"
    )
    assert child["agent_type"] == "sme"
    assert child["status"] == "idle"
    # RCA row for child.
    rca = execute_one(
        "SELECT agent_id, component_id FROM resource_component_agents "
        "WHERE agent_id='sme-child'"
    )
    assert str(rca["component_id"]) == result["child_component_id"]


def test_spawn_blocks_respawn(agent_factory):
    s = _m_state_split(agent_factory)
    ra = s["res_a"]
    execute_mutate(
        "UPDATE components SET source_slice=%s::jsonb WHERE id=%s",
        (json.dumps({ra: {"paths": ["a/", "b/"]}}), s["comp_a"]),
    )
    mutation.spawn_child_agent(
        "sme-a", s["cons_id"], "sme-child",
        {"canonical_name": "o/child", "display_name": "c",
         "component_type": "application"},
        {ra: {"paths": ["b/"]}},
        "split",
    )
    with pytest.raises(ValueError, match="already spawned"):
        mutation.spawn_child_agent(
            "sme-a", s["cons_id"], "sme-child2",
            {"canonical_name": "o/child2", "display_name": "c2",
             "component_type": "application"},
            {ra: {"paths": ["b/"]}},
            "split again",
        )


def test_spawn_requires_split_nomination(agent_factory):
    # Merge consolidation: spawn must refuse.
    s = _m_state_merge(agent_factory)
    with pytest.raises(ValueError, match="nomination_type='split'"):
        mutation.spawn_child_agent(
            "sme-a", s["cons_id"], "sme-child",
            {"canonical_name": "o/child", "display_name": "c",
             "component_type": "application"},
            {"r": {"paths": ["x/"]}},
            "split",
        )


def test_spawn_requires_briefing(agent_factory):
    s = _m_state_split(agent_factory)
    ra = s["res_a"]
    with pytest.raises(ValueError, match="briefing"):
        mutation.spawn_child_agent(
            "sme-a", s["cons_id"], "sme-child",
            {"canonical_name": "o/c", "display_name": "c",
             "component_type": "application"},
            {ra: {"paths": ["x/"]}},
            "",
        )


def test_spawn_requires_non_empty_slice(agent_factory):
    s = _m_state_split(agent_factory)
    with pytest.raises(ValueError, match="child_source_slice"):
        mutation.spawn_child_agent(
            "sme-a", s["cons_id"], "sme-child",
            {"canonical_name": "o/c", "display_name": "c",
             "component_type": "application"},
            {},
            "no slice",
        )


# ========================== transfer_attributions ==========================


def test_transfer_attributions_moves_rows_and_reembeds(agent_factory):
    _iter(agent_factory)
    ca, ra = _sme_with_component(agent_factory, "i", "sme-a", "o/a", "a")
    # Create second component + RCA for sme-a on a second resource.
    rb = resources.upsert_resource("i", "github", "repo", "o/b")
    execute_mutate(
        "INSERT INTO resource_component_agents (resource_id, component_id, agent_id) "
        "VALUES (%s, %s, 'sme-a')",
        (rb["id"], ca),  # temporarily point to ca so we can upsert second comp
    )
    # Actually upsert a second component manually (component_type required).
    cb_row = execute_one(
        """INSERT INTO components (canonical_name, display_name, component_type)
           VALUES ('o/b', 'b', 'application') RETURNING id"""
    )
    cb = str(cb_row["id"])
    execute_mutate(
        "UPDATE resource_component_agents SET component_id=%s WHERE resource_id=%s AND agent_id='sme-a'",
        (cb, rb["id"]),
    )
    # Seed an attribution on ca.
    attr = execute_one(
        """INSERT INTO attributions (component_id, plane, resource_type, identifier)
           VALUES (%s, 'github', 'repo', 'o/a#readme') RETURNING id""",
        (ca,),
    )
    attr_id = str(attr["id"])

    result = mutation.transfer_attributions("sme-a", ca, cb, [attr_id])
    assert result["transferred"] == 1
    # Attribution now points at cb.
    row = execute_one("SELECT component_id FROM attributions WHERE id=%s", (attr_id,))
    assert str(row["component_id"]) == cb


def test_transfer_attributions_refuses_if_not_owner(agent_factory):
    _iter(agent_factory)
    ca, _ = _sme_with_component(agent_factory, "i", "sme-a", "o/a", "a")
    cb, _ = _sme_with_component(agent_factory, "i", "sme-b", "o/b", "b")
    attr = execute_one(
        """INSERT INTO attributions (component_id, plane, resource_type, identifier)
           VALUES (%s, 'github', 'repo', 'o/a#readme') RETURNING id""",
        (ca,),
    )
    # sme-b does NOT own ca.
    with pytest.raises(ValueError, match="does not own"):
        mutation.transfer_attributions("sme-b", ca, cb, [str(attr["id"])])


def test_transfer_attributions_does_not_touch_source_slice(agent_factory):
    """The whole point of the decoupling: attributions move, slice doesn't."""
    survivor_slice = {"res-1": {"plane": "github", "paths": ["x/"]}}
    _iter(agent_factory)
    ca, _ = _sme_with_component(agent_factory, "i", "sme-a", "o/a", "a", survivor_slice)
    cb, _ = _sme_with_component(agent_factory, "i", "sme-b", "o/b", "b")
    # RCA: sme-a also gets a row on cb so ownership check passes? No — point
    # is ownership of FROM only. sme-a owns ca. We transfer ca → cb.
    attr = execute_one(
        """INSERT INTO attributions (component_id, plane, resource_type, identifier)
           VALUES (%s, 'github', 'repo', 'a') RETURNING id""",
        (ca,),
    )
    mutation.transfer_attributions("sme-a", ca, cb, [str(attr["id"])])
    # ca's slice is unchanged.
    row = execute_one("SELECT source_slice FROM components WHERE id=%s", (ca,))
    assert row["source_slice"] == survivor_slice


def test_transfer_attributions_empty_list_rejected(agent_factory):
    _iter(agent_factory)
    ca, _ = _sme_with_component(agent_factory, "i", "sme-a", "o/a", "a")
    cb, _ = _sme_with_component(agent_factory, "i", "sme-b", "o/b", "b")
    with pytest.raises(ValueError, match="non-empty"):
        mutation.transfer_attributions("sme-a", ca, cb, [])


def test_transfer_attributions_same_component_rejected(agent_factory):
    _iter(agent_factory)
    ca, _ = _sme_with_component(agent_factory, "i", "sme-a", "o/a", "a")
    with pytest.raises(ValueError, match="must differ"):
        mutation.transfer_attributions("sme-a", ca, ca, ["00000000-0000-0000-0000-000000000000"])

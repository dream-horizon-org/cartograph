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


# ========================== absorb cascade ==========================


def test_absorb_cascade_moves_attributions_edges_flows(agent_factory):
    s = _m_state_merge(agent_factory)
    # Seed evidence on target (comp_b): 1 attribution, 1 catalog edge,
    # 1 outgoing bound edge, 1 flow linking them.
    attr = execute_one(
        """INSERT INTO attributions (component_id, plane, resource_type, identifier)
           VALUES (%s, 'github', 'repo', 'o/b#evidence') RETURNING id""",
        (s["comp_b"],),
    )
    cat = execute_one(
        """INSERT INTO edges (from_component_id, to_component_id, edge_type,
                              identifier, discovered_by)
           VALUES (NULL, %s, 'calls', 'POST /b', 'test') RETURNING id""",
        (s["comp_b"],),
    )
    ds = execute_one(
        """INSERT INTO components (canonical_name, display_name, component_type)
           VALUES ('o/ds', 'ds', 'application') RETURNING id"""
    )
    out = execute_one(
        """INSERT INTO edges (from_component_id, to_component_id, edge_type,
                              identifier, discovered_by)
           VALUES (%s, %s, 'calls', 'GET /ds', 'test') RETURNING id""",
        (s["comp_b"], ds["id"]),
    )
    flow = execute_one(
        """INSERT INTO flows (component_id, incoming_edge_id, outgoing_edge_id,
                              discovered_by)
           VALUES (%s, %s, %s, 'test') RETURNING id""",
        (s["comp_b"], cat["id"], out["id"]),
    )

    result = mutation.absorb_agent("sme-a", s["cons_id"], "sme-b")

    # All cascade counts recorded.
    assert result["cascade"]["attributions"] == 1
    assert result["cascade"]["edges"] == 2  # cat + out
    assert result["cascade"]["flows"] == 1

    # Attribution now on survivor.
    row = execute_one("SELECT component_id FROM attributions WHERE id=%s", (attr["id"],))
    assert str(row["component_id"]) == str(s["comp_a"])
    # Catalog now on survivor.
    row = execute_one("SELECT to_component_id FROM edges WHERE id=%s", (cat["id"],))
    assert str(row["to_component_id"]) == str(s["comp_a"])
    # Bound outgoing now from survivor.
    row = execute_one("SELECT from_component_id FROM edges WHERE id=%s", (out["id"],))
    assert str(row["from_component_id"]) == str(s["comp_a"])
    # Flow now on survivor.
    row = execute_one("SELECT component_id FROM flows WHERE id=%s", (flow["id"],))
    assert str(row["component_id"]) == str(s["comp_a"])


def test_absorb_cascade_attributions_off(agent_factory):
    """cascade_attributions=False → evidence stays with (decommissioned) target."""
    s = _m_state_merge(agent_factory)
    attr = execute_one(
        """INSERT INTO attributions (component_id, plane, resource_type, identifier)
           VALUES (%s, 'github', 'repo', 'o/b#x') RETURNING id""",
        (s["comp_b"],),
    )
    result = mutation.absorb_agent(
        "sme-a", s["cons_id"], "sme-b", cascade_attributions=False,
    )
    assert result["cascade"]["attributions"] == 0
    row = execute_one("SELECT component_id FROM attributions WHERE id=%s", (attr["id"],))
    assert str(row["component_id"]) == str(s["comp_b"])  # unchanged


def test_absorb_cascade_edges_off(agent_factory):
    s = _m_state_merge(agent_factory)
    execute_mutate(
        """INSERT INTO edges (from_component_id, to_component_id, edge_type,
                              identifier, discovered_by)
           VALUES (NULL, %s, 'calls', 'POST /keep', 'test')""",
        (s["comp_b"],),
    )
    result = mutation.absorb_agent(
        "sme-a", s["cons_id"], "sme-b", cascade_edges=False,
    )
    assert result["cascade"]["edges"] == 0
    # Edge still points at comp_b (now decommissioned).
    row = execute_one(
        "SELECT COUNT(*) as n FROM edges WHERE to_component_id = %s",
        (s["comp_b"],),
    )
    assert row["n"] == 1


def test_absorb_cascade_flows_off(agent_factory):
    s = _m_state_merge(agent_factory)
    cat = execute_one(
        """INSERT INTO edges (from_component_id, to_component_id, edge_type,
                              identifier, discovered_by)
           VALUES (NULL, %s, 'calls', 'POST /x', 'test') RETURNING id""",
        (s["comp_b"],),
    )
    ds = execute_one(
        """INSERT INTO components (canonical_name, display_name, component_type)
           VALUES ('o/ds2', 'ds2', 'application') RETURNING id"""
    )
    out = execute_one(
        """INSERT INTO edges (from_component_id, to_component_id, edge_type,
                              identifier, discovered_by)
           VALUES (%s, %s, 'calls', 'GET /ds2', 'test') RETURNING id""",
        (s["comp_b"], ds["id"]),
    )
    flow = execute_one(
        """INSERT INTO flows (component_id, incoming_edge_id, outgoing_edge_id,
                              discovered_by)
           VALUES (%s, %s, %s, 'test') RETURNING id""",
        (s["comp_b"], cat["id"], out["id"]),
    )
    # Cascade edges ON but flows OFF — edges move, flow stays.
    result = mutation.absorb_agent(
        "sme-a", s["cons_id"], "sme-b", cascade_flows=False,
    )
    assert result["cascade"]["edges"] >= 2
    assert result["cascade"]["flows"] == 0
    row = execute_one("SELECT component_id FROM flows WHERE id=%s", (flow["id"],))
    assert str(row["component_id"]) == str(s["comp_b"])  # flow unchanged


def test_absorb_cascade_empty_body_produces_zero_counts(agent_factory):
    """No attributions/edges/flows on target → all counts 0, absorb still succeeds."""
    s = _m_state_merge(agent_factory)
    result = mutation.absorb_agent("sme-a", s["cons_id"], "sme-b")
    assert result["cascade"] == {
        "attributions": 0, "edges": 0, "flows": 0, "collapsed_edges": 0
    }


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
    s = _m_state_merge(agent_factory)
    # Seed an attribution on the to-be-absorbed side (comp_b).
    attr = execute_one(
        """INSERT INTO attributions (component_id, plane, resource_type, identifier)
           VALUES (%s, 'github', 'repo', 'o/b#readme') RETURNING id""",
        (s["comp_b"],),
    )
    attr_id = str(attr["id"])
    # sme-a (mutation_assigned_to) moves sme-b's attribution into its own comp.
    result = mutation.transfer_attributions(
        "sme-a", s["cons_id"], [attr_id], s["comp_b"], s["comp_a"]
    )
    assert result["transferred"] == 1
    row = execute_one("SELECT component_id FROM attributions WHERE id=%s", (attr_id,))
    assert str(row["component_id"]) == str(s["comp_a"])


def test_transfer_attributions_refuses_outside_mutation(agent_factory):
    """Retightened gate: without an active M-state consolidation + scope
    match, the call refuses. Previously only checked RCA ownership — this
    was the architectural hole Phase 4.1 closes."""
    _iter(agent_factory)
    ca, _ = _sme_with_component(agent_factory, "i", "sme-a", "o/a", "a")
    cb, _ = _sme_with_component(agent_factory, "i", "sme-b", "o/b", "b")
    # Nominate but DON'T approve to M.
    cons = consolidation.nominate_consolidation(
        "sme-a", ca, cb, "merge", 0.9, "m"
    )
    attr = execute_one(
        """INSERT INTO attributions (component_id, plane, resource_type, identifier)
           VALUES (%s, 'github', 'repo', 'a') RETURNING id""",
        (ca,),
    )
    with pytest.raises(ValueError, match="status='M'"):
        mutation.transfer_attributions(
            "sme-a", cons["id"], [str(attr["id"])], ca, cb
        )


def test_transfer_attributions_refuses_wrong_actor(agent_factory):
    """mutation_assigned_to=sme-a, but sme-b tries to call → refused."""
    s = _m_state_merge(agent_factory)
    attr = execute_one(
        """INSERT INTO attributions (component_id, plane, resource_type, identifier)
           VALUES (%s, 'github', 'repo', 'o/a#readme') RETURNING id""",
        (s["comp_a"],),
    )
    with pytest.raises(ValueError, match="mutation_assigned_to"):
        mutation.transfer_attributions(
            "sme-b", s["cons_id"], [str(attr["id"])], s["comp_a"], s["comp_b"]
        )


def test_transfer_attributions_refuses_out_of_scope_components(agent_factory):
    """from/to must match the consolidation's scope."""
    s = _m_state_merge(agent_factory)
    # Make a third component NOT in the consolidation.
    _sme_with_component(agent_factory, "i", "sme-outsider", "o/c", "outsider")
    c_outsider = execute_one(
        """SELECT component_id FROM resource_component_agents
           WHERE agent_id='sme-outsider'"""
    )["component_id"]
    attr = execute_one(
        """INSERT INTO attributions (component_id, plane, resource_type, identifier)
           VALUES (%s, 'github', 'repo', 'o/a#readme') RETURNING id""",
        (s["comp_a"],),
    )
    with pytest.raises(ValueError, match="outside.*scope"):
        mutation.transfer_attributions(
            "sme-a", s["cons_id"], [str(attr["id"])],
            s["comp_a"], str(c_outsider),
        )


def test_transfer_attributions_does_not_touch_source_slice(agent_factory):
    """The decoupling invariant: attributions move, slice doesn't."""
    survivor_slice = {"res-1": {"plane": "github", "paths": ["x/"]}}
    s = _m_state_merge(agent_factory, survivor_slice, None)
    attr = execute_one(
        """INSERT INTO attributions (component_id, plane, resource_type, identifier)
           VALUES (%s, 'github', 'repo', 'o/b#readme') RETURNING id""",
        (s["comp_b"],),
    )
    mutation.transfer_attributions(
        "sme-a", s["cons_id"], [str(attr["id"])], s["comp_b"], s["comp_a"]
    )
    row = execute_one("SELECT source_slice FROM components WHERE id=%s", (s["comp_a"],))
    assert row["source_slice"] == survivor_slice


def test_transfer_attributions_empty_list_rejected(agent_factory):
    s = _m_state_merge(agent_factory)
    with pytest.raises(ValueError, match="non-empty"):
        mutation.transfer_attributions(
            "sme-a", s["cons_id"], [], s["comp_a"], s["comp_b"]
        )


def test_transfer_attributions_same_component_rejected(agent_factory):
    s = _m_state_merge(agent_factory)
    with pytest.raises(ValueError, match="must differ"):
        mutation.transfer_attributions(
            "sme-a", s["cons_id"], ["00000000-0000-0000-0000-000000000000"],
            s["comp_a"], s["comp_a"],
        )


# ========================== transfer_edges ==========================


def test_transfer_edges_merge_rewrites_from_column(agent_factory):
    """Bound edge from caller's side → re-point from_component_id."""
    s = _m_state_merge(agent_factory)
    # sme-b owns a bound edge from their component → ca (doesn't matter).
    c_target = execute_one(
        """INSERT INTO components (canonical_name, display_name, component_type)
           VALUES ('o/downstream', 'ds', 'application') RETURNING id"""
    )
    c_target_id = str(c_target["id"])
    edge = execute_one(
        """INSERT INTO edges (from_component_id, to_component_id, edge_type,
                              identifier, discovered_by)
           VALUES (%s, %s, 'calls', 'GET /x', 'test') RETURNING id""",
        (s["comp_b"], c_target_id),
    )
    result = mutation.transfer_edges(
        "sme-a", s["cons_id"], [str(edge["id"])], direction="from"
    )
    assert result["transferred"] == 1
    row = execute_one("SELECT from_component_id FROM edges WHERE id=%s", (edge["id"],))
    assert str(row["from_component_id"]) == str(s["comp_a"])


def test_transfer_edges_catalog_collision_collapses(agent_factory):
    """Catalog on comp_b matches existing catalog on comp_a → drop comp_b's."""
    s = _m_state_merge(agent_factory)
    # Catalog on comp_a.
    execute_mutate(
        """INSERT INTO edges (from_component_id, to_component_id, edge_type,
                              identifier, discovered_by)
           VALUES (NULL, %s, 'calls', 'POST /login', 'test')""",
        (s["comp_a"],),
    )
    # Duplicate catalog on comp_b.
    edge_b = execute_one(
        """INSERT INTO edges (from_component_id, to_component_id, edge_type,
                              identifier, discovered_by)
           VALUES (NULL, %s, 'calls', 'POST /login', 'test')
           RETURNING id""",
        (s["comp_b"],),
    )
    result = mutation.transfer_edges(
        "sme-a", s["cons_id"], [str(edge_b["id"])], direction="to"
    )
    assert result["transferred"] == 0
    assert result["collapsed"] == 1
    # comp_b's duplicate is gone.
    gone = execute_one("SELECT 1 FROM edges WHERE id=%s", (edge_b["id"],))
    assert gone is None


def test_transfer_edges_refuses_outside_mutation(agent_factory):
    _iter(agent_factory)
    ca, _ = _sme_with_component(agent_factory, "i", "sme-a", "o/a", "a")
    cb, _ = _sme_with_component(agent_factory, "i", "sme-b", "o/b", "b")
    cons = consolidation.nominate_consolidation("sme-a", ca, cb, "merge", 0.9, "m")
    # Not in M state.
    edge = execute_one(
        """INSERT INTO edges (from_component_id, to_component_id, edge_type,
                              identifier, discovered_by)
           VALUES (%s, %s, 'calls', 'GET /x', 'test') RETURNING id""",
        (cb, ca),
    )
    with pytest.raises(ValueError, match="status='M'"):
        mutation.transfer_edges("sme-a", cons["id"], [str(edge["id"])], "from")


def test_transfer_edges_invalid_direction_rejected(agent_factory):
    s = _m_state_merge(agent_factory)
    with pytest.raises(ValueError, match="direction"):
        mutation.transfer_edges("sme-a", s["cons_id"], ["any"], "bogus")


# ========================== transfer_flows ==========================


def test_transfer_flows_merge_rewrites_component_id(agent_factory):
    s = _m_state_merge(agent_factory)
    # Catalog on comp_b (incoming side), bound to a separate component (outgoing).
    cat = execute_one(
        """INSERT INTO edges (from_component_id, to_component_id, edge_type,
                              identifier, discovered_by)
           VALUES (NULL, %s, 'calls', 'POST /x', 'test')
           RETURNING id""",
        (s["comp_b"],),
    )
    ds = execute_one(
        """INSERT INTO components (canonical_name, display_name, component_type)
           VALUES ('o/z', 'z', 'application') RETURNING id"""
    )
    out = execute_one(
        """INSERT INTO edges (from_component_id, to_component_id, edge_type,
                              identifier, discovered_by)
           VALUES (%s, %s, 'calls', 'GET /z', 'test')
           RETURNING id""",
        (s["comp_b"], ds["id"]),
    )
    flow = execute_one(
        """INSERT INTO flows (component_id, incoming_edge_id, outgoing_edge_id,
                              discovered_by)
           VALUES (%s, %s, %s, 'test') RETURNING id""",
        (s["comp_b"], cat["id"], out["id"]),
    )
    result = mutation.transfer_flows(
        "sme-a", s["cons_id"], [str(flow["id"])]
    )
    assert result["transferred"] == 1
    row = execute_one("SELECT component_id FROM flows WHERE id=%s", (flow["id"],))
    assert str(row["component_id"]) == str(s["comp_a"])


def test_transfer_flows_refuses_outside_mutation(agent_factory):
    _iter(agent_factory)
    ca, _ = _sme_with_component(agent_factory, "i", "sme-a", "o/a", "a")
    cb, _ = _sme_with_component(agent_factory, "i", "sme-b", "o/b", "b")
    cons = consolidation.nominate_consolidation("sme-a", ca, cb, "merge", 0.9, "m")
    with pytest.raises(ValueError, match="status='M'"):
        mutation.transfer_flows(
            "sme-a", cons["id"], ["00000000-0000-0000-0000-000000000000"]
        )


def test_transfer_flows_empty_list_rejected(agent_factory):
    s = _m_state_merge(agent_factory)
    with pytest.raises(ValueError, match="non-empty"):
        mutation.transfer_flows("sme-a", s["cons_id"], [])


# ========================== stale hygiene ==========================


def test_get_stale_edges_surfaces_dead_catalog_target(agent_factory):
    from cartograph_mcp.tools import components as comp_tool
    _iter(agent_factory)
    ca, _ = _sme_with_component(agent_factory, "i", "sme-a", "o/a", "a")
    cb, _ = _sme_with_component(agent_factory, "i", "sme-b", "o/b", "b")
    # sme-a's component has a bound edge pointing at sme-b's (live) component.
    execute_mutate(
        """INSERT INTO edges (from_component_id, to_component_id, edge_type,
                              identifier, discovered_by)
           VALUES (%s, %s, 'calls', 'GET /x', 'test')""",
        (ca, cb),
    )
    # Decommission sme-b's component out from under sme-a.
    execute_mutate(
        "UPDATE components SET status='decommissioned' WHERE id=%s", (cb,)
    )
    rows = comp_tool.get_stale_edges("sme-a")
    assert len(rows) == 1
    r = rows[0]
    assert r["my_side"] == "from"
    assert str(r["stale_component_id"]) == str(cb)
    assert r["suggested_action"] == "target-gone-accept-or-escalate"


def test_get_stale_edges_includes_survivor_when_merged(agent_factory):
    """Post-absorb, the decommissioned component's agent has
    merged_into_agent_id set → suggested_action = re-bind-to-survivor."""
    from cartograph_mcp.tools import components as comp_tool
    s = _m_state_merge(agent_factory)
    # sme-a has a bound edge → sme-b's component (before absorb).
    execute_mutate(
        """INSERT INTO edges (from_component_id, to_component_id, edge_type,
                              identifier, discovered_by)
           VALUES (%s, %s, 'calls', 'GET /dead', 'test')""",
        (s["comp_a"], s["comp_b"]),
    )
    # Absorb sme-b into sme-a — flips sme-b.merged_into_agent_id to sme-a
    # AND decommissions comp_b. We turn cascade_edges OFF here so the
    # edge STAYS pointing at the dead comp_b (otherwise 4.1.3 would
    # auto-transfer it and there'd be nothing stale to surface).
    mutation.absorb_agent(
        "sme-a", s["cons_id"], "sme-b", cascade_edges=False,
    )
    rows = comp_tool.get_stale_edges("sme-a")
    # sme-a's edge now points at decommissioned comp_b.
    target_rows = [r for r in rows if str(r["stale_component_id"]) == str(s["comp_b"])]
    assert len(target_rows) == 1
    assert target_rows[0]["stale_component_merged_into_agent_id"] == "sme-a"
    assert target_rows[0]["suggested_action"] == "re-bind-to-survivor"


def test_get_stale_edges_empty_when_no_dead_neighbors(agent_factory):
    from cartograph_mcp.tools import components as comp_tool
    _iter(agent_factory)
    ca, _ = _sme_with_component(agent_factory, "i", "sme-a", "o/a", "a")
    assert comp_tool.get_stale_edges("sme-a") == []


def test_get_stale_flows_surfaces_flow_with_dead_incoming(agent_factory):
    from cartograph_mcp.tools import components as comp_tool
    _iter(agent_factory)
    ca, _ = _sme_with_component(agent_factory, "i", "sme-a", "o/a", "a")
    cb, _ = _sme_with_component(agent_factory, "i", "sme-b", "o/b", "b")
    # Bound edge from sme-b → sme-a (caller = sme-b).
    incoming = execute_one(
        """INSERT INTO edges (from_component_id, to_component_id, edge_type,
                              identifier, discovered_by)
           VALUES (%s, %s, 'calls', 'POST /x', 'test')
           RETURNING id""",
        (cb, ca),
    )
    # sme-a has an outgoing dangling edge.
    outgoing = execute_one(
        """INSERT INTO edges (from_component_id, to_component_id, edge_type,
                              identifier, discovered_by)
           VALUES (%s, NULL, 'calls', 'https://gone.example', 'test')
           RETURNING id""",
        (ca,),
    )
    # Flow on sme-a chaining incoming → outgoing.
    execute_mutate(
        """INSERT INTO flows (component_id, incoming_edge_id, outgoing_edge_id,
                              discovered_by)
           VALUES (%s, %s, %s, 'test')""",
        (ca, incoming["id"], outgoing["id"]),
    )
    # Decommission sme-b's component (makes incoming edge's from-side dead).
    execute_mutate(
        "UPDATE components SET status='decommissioned' WHERE id=%s", (cb,)
    )
    rows = comp_tool.get_stale_flows("sme-a")
    assert len(rows) == 1
    assert rows[0]["incoming_other_status"] == "decommissioned"

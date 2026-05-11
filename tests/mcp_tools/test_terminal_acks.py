"""Phase 7.1: terminal-state ack model.

Replaces Phase 5.5 auto-ack. Closure announcements land UNACKED;
trigger scanner re-wakes participants until they explicitly call
ack_terminal(entity_type, entity_id). When a participant is
decommissioned with pending acks, absorb_agent bulk-acks on their
behalf.
"""

import pytest

from shared.db import execute, execute_one, execute_mutate, execute_returning
from cartograph_mcp.tools import (
    components, consolidation, clarification, mutation, resources as _res, tasks,
)


# ---------- helpers ----------

def _setup_pair(agent_factory):
    """Two SMEs each owning a component, plus a resolver. Returns the
    consolidations id after nominate."""
    agent_factory("iter1", "iterator")
    execute_mutate(
        "UPDATE agent_runs SET plane='github', status='idle' WHERE agent_id='iter1'"
    )
    r1 = _res.upsert_resource("iter1", "github", "repo", "o/p1")
    r2 = _res.upsert_resource("iter1", "github", "repo", "o/p2")
    agent_factory("sme-a", "sme")
    agent_factory("sme-b", "sme")
    execute_mutate(
        "INSERT INTO resource_component_agents (resource_id, agent_id) "
        "VALUES (%s, 'sme-a')", (r1["id"],),
    )
    execute_mutate(
        "INSERT INTO resource_component_agents (resource_id, agent_id) "
        "VALUES (%s, 'sme-b')", (r2["id"],),
    )
    ca = components.upsert_component("sme-a", {
        "canonical_name": "ca", "display_name": "ca", "component_type": "application",
    })["id"]
    cb = components.upsert_component("sme-b", {
        "canonical_name": "cb", "display_name": "cb", "component_type": "application",
    })["id"]
    agent_factory("res", "resolver")
    return ca, cb


def _terminal_task(agent_factory):
    agent_factory("admin", "orchestrator")
    agent_factory("worker", "sme")
    t = execute_returning(
        """INSERT INTO tasks (owner_agent_id, worker_agent_id, description, status)
           VALUES ('admin', 'worker', 'do thing', 'WD') RETURNING *""",
    )
    tasks.respond_task("admin", str(t["id"]), "accepted", "TC")
    return str(t["id"])


def _terminal_consolidation_F(agent_factory):
    ca, cb = _setup_pair(agent_factory)
    cons = consolidation.nominate_consolidation(
        "sme-a", ca, cb, "merge", 0.9, "merge"
    )
    execute_mutate("UPDATE consolidations SET status='R' WHERE id=%s", (cons["id"],))
    consolidation.review_consolidation(
        "res", cons["id"], 0.1, "rejected", "F"
    )
    return str(cons["id"])


def _terminal_consolidation_D(agent_factory):
    ca, cb = _setup_pair(agent_factory)
    cons = consolidation.nominate_consolidation(
        "sme-a", ca, cb, "merge", 0.9, "merge"
    )
    execute_mutate("UPDATE consolidations SET status='MD' WHERE id=%s", (cons["id"],))
    consolidation.complete_consolidation("res", cons["id"], "verified")
    return str(cons["id"])


def _terminal_clarification_CC(agent_factory):
    agent_factory("asker", "sme")
    cl = clarification.create_clarification("asker", "admin", "what")
    execute_mutate(
        "UPDATE clarifications SET status='QC' WHERE id=%s", (cl["id"],)
    )
    clarification.respond_clarification("asker", cl["id"], "thanks", "CC")
    return str(cl["id"])


# ==========  ack_terminal tool ==========


def test_ack_terminal_inserts_row(agent_factory):
    from cartograph_mcp.tools import terminal_acks
    task_id = _terminal_task(agent_factory)
    out = terminal_acks.ack_terminal("worker", "task", task_id)
    assert out["acked"] is True
    assert out["already_acked"] is False
    row = execute_one(
        "SELECT * FROM terminal_acks WHERE entity_type='task' AND entity_id=%s::uuid AND agent_id='worker'",
        (task_id,),
    )
    assert row is not None


def test_ack_terminal_idempotent(agent_factory):
    from cartograph_mcp.tools import terminal_acks
    task_id = _terminal_task(agent_factory)
    terminal_acks.ack_terminal("worker", "task", task_id)
    out2 = terminal_acks.ack_terminal("worker", "task", task_id)
    assert out2["acked"] is False
    assert out2["already_acked"] is True


def test_ack_terminal_rejects_non_participant(agent_factory):
    from cartograph_mcp.tools import terminal_acks
    task_id = _terminal_task(agent_factory)
    agent_factory("nosy", "sme")
    with pytest.raises(ValueError, match="not a participant"):
        terminal_acks.ack_terminal("nosy", "task", task_id)


def test_ack_terminal_rejects_non_terminal_entity(agent_factory):
    from cartograph_mcp.tools import terminal_acks
    agent_factory("admin", "orchestrator")
    agent_factory("worker", "sme")
    t = execute_returning(
        """INSERT INTO tasks (owner_agent_id, worker_agent_id, description, status)
           VALUES ('admin', 'worker', 'in progress', 'BW') RETURNING *""",
    )
    with pytest.raises(ValueError, match="not in a terminal state"):
        terminal_acks.ack_terminal("worker", "task", str(t["id"]))


def test_ack_terminal_rejects_unknown_entity(agent_factory):
    from cartograph_mcp.tools import terminal_acks
    agent_factory("worker", "sme")
    with pytest.raises(ValueError, match="not found"):
        terminal_acks.ack_terminal(
            "worker", "task", "00000000-0000-0000-0000-000000000000"
        )


def test_ack_terminal_rejects_invalid_kind(agent_factory):
    from cartograph_mcp.tools import terminal_acks
    agent_factory("worker", "sme")
    with pytest.raises(ValueError, match="Invalid entity_type"):
        terminal_acks.ack_terminal(
            "worker", "bogus", "00000000-0000-0000-0000-000000000000"
        )


# ========== consolidation D acks both agents ==========


def test_consolidation_D_requires_acks_from_both_agents(agent_factory):
    """Both agent_a and agent_b are participants of a closed
    consolidation; both must be able to ack independently."""
    from cartograph_mcp.tools import terminal_acks
    cons_id = _terminal_consolidation_D(agent_factory)
    out_a = terminal_acks.ack_terminal("sme-a", "consolidation", cons_id)
    out_b = terminal_acks.ack_terminal("sme-b", "consolidation", cons_id)
    assert out_a["acked"] is True
    assert out_b["acked"] is True
    rows = execute(
        "SELECT agent_id FROM terminal_acks WHERE entity_type='consolidation' AND entity_id=%s::uuid ORDER BY agent_id",
        (cons_id,),
    )
    assert [r["agent_id"] for r in rows] == ["sme-a", "sme-b"]


def test_consolidation_F_accepts_ack_from_agent_a(agent_factory):
    from cartograph_mcp.tools import terminal_acks
    cons_id = _terminal_consolidation_F(agent_factory)
    out = terminal_acks.ack_terminal("sme-a", "consolidation", cons_id)
    assert out["acked"] is True


# ========== Phase 5.5 revert: terminal comm rows land unacked ==========


def test_task_TC_announcement_lands_unacked(agent_factory):
    """Phase 5.5 used to pre-stamp acked_at = now() on the terminal
    announcement comm. Phase 7.1 reverts that — comm must land
    unacked so the trigger scanner knows to wake the recipient."""
    task_id = _terminal_task(agent_factory)
    row = execute_one(
        "SELECT acked_at FROM communications "
        "WHERE source_id=%s::uuid ORDER BY created_at DESC LIMIT 1",
        (task_id,),
    )
    assert row["acked_at"] is None


def test_consolidation_D_announcement_lands_unacked(agent_factory):
    cons_id = _terminal_consolidation_D(agent_factory)
    rows = execute(
        "SELECT acked_at FROM communications "
        "WHERE source_id=%s::uuid AND type='consolidation' AND text='verified'",
        (cons_id,),
    )
    assert all(r["acked_at"] is None for r in rows)


def test_clarification_CC_announcement_lands_unacked(agent_factory):
    cl_id = _terminal_clarification_CC(agent_factory)
    row = execute_one(
        "SELECT acked_at FROM communications "
        "WHERE source_id=%s::uuid ORDER BY created_at DESC LIMIT 1",
        (cl_id,),
    )
    assert row["acked_at"] is None


# ========== Trigger scanner wakes on unacked terminal entities ==========


def test_scanner_wakes_on_unacked_terminal_task(agent_factory):
    from trigger_management.scanners import terminal_acks as scanner
    task_id = _terminal_task(agent_factory)
    pending = scanner.scan_terminal_pending_ack("worker")
    assert any(p["entity_type"] == "task" and p["entity_id"] == task_id
               for p in pending)


def test_scanner_skips_after_ack(agent_factory):
    from cartograph_mcp.tools import terminal_acks as ack
    from trigger_management.scanners import terminal_acks as scanner
    task_id = _terminal_task(agent_factory)
    ack.ack_terminal("worker", "task", task_id)
    pending = scanner.scan_terminal_pending_ack("worker")
    assert not any(p["entity_type"] == "task" and p["entity_id"] == task_id
                   for p in pending)


def test_scanner_returns_consolidation_for_both_agents(agent_factory):
    from trigger_management.scanners import terminal_acks as scanner
    cons_id = _terminal_consolidation_D(agent_factory)
    pending_a = scanner.scan_terminal_pending_ack("sme-a")
    pending_b = scanner.scan_terminal_pending_ack("sme-b")
    assert any(p["entity_id"] == cons_id for p in pending_a)
    assert any(p["entity_id"] == cons_id for p in pending_b)


def test_scanner_picks_clarification(agent_factory):
    from trigger_management.scanners import terminal_acks as scanner
    cl_id = _terminal_clarification_CC(agent_factory)
    pending = scanner.scan_terminal_pending_ack("asker")
    assert any(p["entity_id"] == cl_id for p in pending)


# ========== absorb_agent auto-acks decommissioned target ==========


def test_decommission_auto_acks_target_pending(agent_factory):
    """When absorb_agent decommissions the target, any unacked terminal
    entities the target was a participant of must be auto-acked on
    its behalf — otherwise the target's row will sit forever in
    terminal_acks's negative space, and stuck-state queries will be
    misleading."""
    # Set up a closed consolidation where sme-b is a participant,
    # then a SECOND consolidation where sme-a will absorb sme-b.
    ca, cb = _setup_pair(agent_factory)
    closed_cons = consolidation.nominate_consolidation(
        "sme-a", ca, cb, "merge", 0.9, "first round"
    )
    execute_mutate(
        "UPDATE consolidations SET status='R' WHERE id=%s", (closed_cons["id"],)
    )
    consolidation.review_consolidation(
        "res", closed_cons["id"], 0.1, "rejected", "F"
    )
    # closed_cons is now in F. Both sme-a and sme-b have it as
    # terminal-pending-ack.

    # New round: sme-a absorbs sme-b via a fresh consolidation
    # approved at M.
    absorb_cons = consolidation.nominate_consolidation(
        "sme-a", ca, cb, "merge", 0.9, "round 2"
    )
    execute_mutate(
        "UPDATE consolidations SET status='R' WHERE id=%s", (absorb_cons["id"],)
    )
    consolidation.review_consolidation(
        "res", absorb_cons["id"], 0.95, "approve", "M",
        mutation_assigned_to="sme-a",
    )

    mutation.absorb_agent(
        "sme-a", absorb_cons["id"], "sme-b",
        deactivation_reason="merged",
        deactivation_notes="folded in",
    )

    # sme-b should now have an auto-ack row for closed_cons.
    row = execute_one(
        """SELECT * FROM terminal_acks
           WHERE entity_type='consolidation' AND entity_id=%s::uuid AND agent_id='sme-b'""",
        (closed_cons["id"],),
    )
    assert row is not None, "sme-b should have been auto-acked on closed_cons during decommission"

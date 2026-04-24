"""Phase 4: tests for get_my_proxy_items + act_on_proxy_item router."""

import json

import pytest

from shared.db import execute, execute_mutate, execute_one
from cartograph_mcp.tools import (
    components, consolidation, mutation, proxy, resources,
)


# ---------- fixtures ----------


def _iter(agent_factory, aid: str = "i", plane: str = "github"):
    execute_mutate(
        """INSERT INTO agent_runs (agent_id, agent_type, plane, status)
           VALUES (%s, 'iterator', %s, 'idle')""",
        (aid, plane),
    )
    return aid


def _sme_with_component(
    agent_factory, iterator_id: str, sme_id: str, identifier: str, cname: str
) -> str:
    r = resources.upsert_resource(iterator_id, "github", "repo", identifier)
    agent_factory(sme_id, "sme")
    execute_mutate(
        """INSERT INTO resource_component_agents (resource_id, component_id, agent_id)
           VALUES (%s, NULL, %s)""",
        (r["id"], sme_id),
    )
    c = components.upsert_component(sme_id, {
        "canonical_name": cname, "display_name": cname,
        "component_type": "application",
    })
    return c["id"]


def _merge_and_absorb(agent_factory, survivor="sme-a", target="sme-b"):
    """Seed + consolidate + review → M → absorb. Returns consolidation_id."""
    _iter(agent_factory)
    ca = _sme_with_component(agent_factory, "i", survivor, f"o/{survivor}", survivor)
    cb = _sme_with_component(agent_factory, "i", target, f"o/{target}", target)
    agent_factory("res", "resolver")
    cons = consolidation.nominate_consolidation(
        survivor, ca, cb, "merge", 0.9, "merge"
    )
    execute_mutate("UPDATE consolidations SET status='R' WHERE id=%s", (cons["id"],))
    consolidation.review_consolidation(
        "res", cons["id"], 0.95, "approve", "M", survivor
    )
    mutation.absorb_agent(survivor, cons["id"], target,
                          deactivation_reason="merged",
                          deactivation_notes=f"{target} folded in")
    return cons["id"]


# ==================== get_my_proxy_items ====================


def test_empty_inbox_when_no_proxies(agent_factory):
    agent_factory("survivor", "sme")
    out = proxy.get_my_proxy_items("survivor")
    assert out == {"proxied": []}


def test_empty_inbox_when_proxy_has_no_pending_work(agent_factory):
    """Target had no pending tasks/chats/... — should not appear in inbox."""
    _merge_and_absorb(agent_factory)
    out = proxy.get_my_proxy_items("sme-a")
    assert out == {"proxied": []}


def test_pending_task_surfaces_under_proxy(agent_factory):
    _merge_and_absorb(agent_factory)
    # Seed an open task that was assigned to sme-b (now decommissioned).
    execute_mutate(
        """INSERT INTO tasks (owner_agent_id, worker_agent_id, description, status)
           VALUES ('admin', 'sme-b', 'finish the thing', 'BW')"""
    )
    out = proxy.get_my_proxy_items("sme-a")
    assert len(out["proxied"]) == 1
    group = out["proxied"][0]
    assert group["proxy_agent_id"] == "sme-b"
    assert group["deactivation_reason"] == "merged"
    assert group["deactivation_notes"] == "sme-b folded in"
    assert group["depth"] == 1
    assert len(group["items"]["tasks"]) == 1
    assert group["items"]["tasks"][0]["description"] == "finish the thing"


def test_unacked_chat_surfaces(agent_factory):
    _merge_and_absorb(agent_factory)
    execute_mutate(
        """INSERT INTO communications (from_agent, to_agent, type, text)
           VALUES ('admin', 'sme-b', 'chat', 'ping')"""
    )
    out = proxy.get_my_proxy_items("sme-a")
    assert out["proxied"][0]["items"]["chats"][0]["text"] == "ping"


def test_transitive_chain_depth_reflected(agent_factory):
    """B merged into A, then C absorbs A → C should see B's work at depth 2."""
    # Round 1: A absorbs B.
    _merge_and_absorb(agent_factory, survivor="sme-a", target="sme-b")
    # Seed B's pending task.
    execute_mutate(
        """INSERT INTO tasks (owner_agent_id, worker_agent_id, description, status)
           VALUES ('admin', 'sme-b', 't from b', 'BW')"""
    )
    # Round 2: C absorbs A.
    cc = _sme_with_component(agent_factory, "i", "sme-c", "o/sme-c", "c")
    # Need A's component; look it up.
    a_comp = execute_one(
        """SELECT component_id FROM resource_component_agents
           WHERE agent_id='sme-a' LIMIT 1"""
    )["component_id"]
    cons = consolidation.nominate_consolidation(
        "sme-c", cc, str(a_comp), "merge", 0.9, "round 2"
    )
    execute_mutate("UPDATE consolidations SET status='R' WHERE id=%s", (cons["id"],))
    consolidation.review_consolidation(
        "res", cons["id"], 0.95, "approve", "M", "sme-c"
    )
    mutation.absorb_agent("sme-c", cons["id"], "sme-a",
                          deactivation_reason="merged",
                          deactivation_notes="a folded in")

    # C queries — should see both A and B in the inbox.
    out = proxy.get_my_proxy_items("sme-c")
    depths = {g["proxy_agent_id"]: g["depth"] for g in out["proxied"]}
    # B had the pending task — it must be in C's inbox at depth 2.
    assert "sme-b" in depths
    assert depths["sme-b"] == 2
    # A had no pending work, so it may or may not be listed depending on
    # empty-inbox filter. If A has no items, it's skipped.


def test_active_agent_required(agent_factory):
    # No agent 'missing' → raises.
    with pytest.raises(ValueError, match="not found"):
        proxy.get_my_proxy_items("missing")


# ==================== act_on_proxy_item ====================


def test_act_on_proxy_task_respond_invokes_underlying(agent_factory):
    _merge_and_absorb(agent_factory)
    # Task owned by sme-b (decommissioned) with status BW (worker's turn).
    t = execute_one(
        """INSERT INTO tasks
           (owner_agent_id, worker_agent_id, description, status)
           VALUES ('admin', 'sme-b', 'handle', 'BW')
           RETURNING id"""
    )
    task_id = str(t["id"])
    result = proxy.act_on_proxy_item(
        survivor_id="sme-a",
        item_type="task",
        item_id=task_id,
        action="respond",
        payload={"message": "done", "new_status": "WD"},
    )
    # Task transitioned to WD (work done).
    row = execute_one("SELECT status FROM tasks WHERE id=%s", (task_id,))
    assert row["status"] == "WD"
    # Audit row written.
    audit = execute_one(
        """SELECT * FROM proxy_audit WHERE item_id=%s""", (task_id,)
    )
    assert audit is not None
    assert audit["survivor_id"] == "sme-a"
    assert audit["proxy_agent_id"] == "sme-b"
    assert audit["item_type"] == "task"
    assert audit["action"] == "respond"
    assert result["proxy_agent_id"] == "sme-b"


def test_act_on_proxy_rejects_non_proxy(agent_factory):
    # Merge A ← B, but C is unrelated.
    _merge_and_absorb(agent_factory)
    agent_factory("sme-c", "sme")
    t = execute_one(
        """INSERT INTO tasks (owner_agent_id, worker_agent_id, description, status)
           VALUES ('admin', 'sme-b', 'x', 'BW') RETURNING id"""
    )
    with pytest.raises(ValueError, match="not a legal proxy"):
        proxy.act_on_proxy_item(
            "sme-c", "task", str(t["id"]), "respond",
            {"message": "nope", "new_status": "WD"},
        )


def test_act_on_proxy_rejects_when_owner_active(agent_factory):
    """If the item's owner is still active, no proxy is needed — refuse."""
    _iter(agent_factory)
    agent_factory("sme-a", "sme")
    agent_factory("sme-b", "sme")
    t = execute_one(
        """INSERT INTO tasks (owner_agent_id, worker_agent_id, description, status)
           VALUES ('admin', 'sme-b', 'x', 'BW') RETURNING id"""
    )
    with pytest.raises(ValueError, match="active agent"):
        proxy.act_on_proxy_item(
            "sme-a", "task", str(t["id"]), "respond",
            {"message": "x", "new_status": "WD"},
        )


def test_act_on_proxy_survivor_must_be_active(agent_factory):
    _merge_and_absorb(agent_factory)
    # Decommission the survivor too.
    execute_mutate(
        "UPDATE agent_runs SET status='decommissioned' WHERE agent_id='sme-a'"
    )
    t = execute_one(
        """INSERT INTO tasks (owner_agent_id, worker_agent_id, description, status)
           VALUES ('admin', 'sme-b', 'x', 'BW') RETURNING id"""
    )
    with pytest.raises(ValueError, match="not found"):
        proxy.act_on_proxy_item(
            "sme-a", "task", str(t["id"]), "respond",
            {"message": "x", "new_status": "WD"},
        )


def test_act_on_proxy_unknown_dispatch(agent_factory):
    _merge_and_absorb(agent_factory)
    with pytest.raises(ValueError, match="Unsupported proxy action"):
        proxy.act_on_proxy_item(
            "sme-a", "task", "00000000-0000-0000-0000-000000000000",
            "bogus", {}
        )


def test_act_on_proxy_chat_ack(agent_factory):
    _merge_and_absorb(agent_factory)
    c = execute_one(
        """INSERT INTO communications (from_agent, to_agent, type, text)
           VALUES ('admin', 'sme-b', 'chat', 'ping') RETURNING id"""
    )
    proxy.act_on_proxy_item(
        "sme-a", "chat", str(c["id"]), "ack", {}
    )
    row = execute_one(
        "SELECT acked_at FROM communications WHERE id=%s", (c["id"],)
    )
    assert row["acked_at"] is not None


def test_act_on_proxy_context_is_reset_after_call(agent_factory):
    from shared.actor_auth import get_proxy_context
    _merge_and_absorb(agent_factory)
    t = execute_one(
        """INSERT INTO tasks (owner_agent_id, worker_agent_id, description, status)
           VALUES ('admin', 'sme-b', 'x', 'BW') RETURNING id"""
    )
    assert get_proxy_context() is None
    proxy.act_on_proxy_item(
        "sme-a", "task", str(t["id"]), "respond",
        {"message": "ok", "new_status": "WD"},
    )
    assert get_proxy_context() is None


def test_act_on_proxy_context_resets_even_on_exception(agent_factory):
    """If underlying tool raises, ContextVar must still reset."""
    from shared.actor_auth import get_proxy_context
    _merge_and_absorb(agent_factory)
    # Seed a task already in terminal state (TC) so respond_task rejects
    # any further transition.
    t = execute_one(
        """INSERT INTO tasks (owner_agent_id, worker_agent_id, description, status)
           VALUES ('admin', 'sme-b', 'x', 'TC') RETURNING id"""
    )
    with pytest.raises(ValueError):
        proxy.act_on_proxy_item(
            "sme-a", "task", str(t["id"]), "respond",
            {"message": "x", "new_status": "BW"},
        )
    assert get_proxy_context() is None

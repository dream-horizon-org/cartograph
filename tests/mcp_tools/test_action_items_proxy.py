"""Phase 4: action_items summary + detail surface proxy bucket."""

import pytest

from shared.db import execute_mutate, execute_one
from cartograph_mcp.tools import (
    action_items, components, consolidation, mutation, resources,
)


# ---------- fixtures ----------


def _iter(agent_factory, aid: str = "i"):
    execute_mutate(
        """INSERT INTO agent_runs (agent_id, agent_type, plane, status)
           VALUES (%s, 'iterator', 'github', 'idle')""",
        (aid,),
    )
    return aid


def _sme_with_component(agent_factory, iterator_id, sme_id, identifier, cname):
    r = resources.upsert_resource(iterator_id, "github", "repo", identifier)
    agent_factory(sme_id, "sme")
    execute_mutate(
        "INSERT INTO resource_component_agents (resource_id, component_id, agent_id) "
        "VALUES (%s, NULL, %s)",
        (r["id"], sme_id),
    )
    c = components.upsert_component(sme_id, {
        "canonical_name": cname, "display_name": cname,
        "component_type": "application",
    })
    return c["id"]


def _merge_and_absorb(agent_factory):
    _iter(agent_factory)
    ca = _sme_with_component(agent_factory, "i", "sme-a", "o/a", "a")
    cb = _sme_with_component(agent_factory, "i", "sme-b", "o/b", "b")
    agent_factory("res", "resolver")
    cons = consolidation.nominate_consolidation(
        "sme-a", ca, cb, "merge", 0.9, "merge"
    )
    execute_mutate("UPDATE consolidations SET status='R' WHERE id=%s", (cons["id"],))
    consolidation.review_consolidation(
        "res", cons["id"], 0.95, "approve", "M", "sme-a"
    )
    mutation.absorb_agent("sme-a", cons["id"], "sme-b",
                          deactivation_reason="merged",
                          deactivation_notes="B folded in")


# ---------- summary ----------


def test_summary_has_proxied_field_empty_when_no_proxies(agent_factory):
    agent_factory("lone", "sme")
    out = action_items.get_action_items_summary("lone", "sme")
    assert out["proxied"] == []
    # Legacy fields still present.
    for k in ("consolidations_pending", "tasks_pending", "clarifications_pending",
              "unacked_chats", "unacked_broadcasts"):
        assert k in out


def test_summary_proxied_reflects_pending_inherited_work(agent_factory):
    _merge_and_absorb(agent_factory)
    execute_mutate(
        """INSERT INTO tasks (owner_agent_id, worker_agent_id, description, status)
           VALUES ('admin', 'sme-b', 'inherited', 'BW')"""
    )
    out = action_items.get_action_items_summary("sme-a", "sme")
    assert len(out["proxied"]) == 1
    group = out["proxied"][0]
    assert group["proxy_agent_id"] == "sme-b"
    assert group["deactivation_reason"] == "merged"
    assert group["counts"]["tasks"] == 1
    # 'my' pending counts do NOT include the proxied task.
    assert out["tasks_pending"] == 0


# ---------- detail ----------


def test_detail_returns_my_and_proxied_buckets(agent_factory):
    _merge_and_absorb(agent_factory)
    execute_mutate(
        """INSERT INTO communications (from_agent, to_agent, type, text)
           VALUES ('admin', 'sme-b', 'chat', 'legacy question')"""
    )
    out = action_items.get_action_items_detail("sme-a", "sme")
    # Both buckets present.
    assert "my" in out
    assert "proxied" in out
    # 'my' shape preserved.
    for k in ("consolidations", "tasks", "clarifications", "chats", "broadcasts"):
        assert k in out["my"]
    # Proxied bucket carries the inherited chat.
    assert len(out["proxied"]) == 1
    assert out["proxied"][0]["items"]["chats"][0]["text"] == "legacy question"


# ---------- trigger scanner ----------


def test_trigger_scanner_counts_inherited_items(agent_factory):
    from trigger_management.scanners import proxies
    _merge_and_absorb(agent_factory)
    # No items yet → scanner returns 0.
    assert proxies.scan("sme-a") == 0
    # Seed an open task + an unacked chat.
    execute_mutate(
        """INSERT INTO tasks (owner_agent_id, worker_agent_id, description, status)
           VALUES ('admin', 'sme-b', 'x', 'BW')"""
    )
    execute_mutate(
        """INSERT INTO communications (from_agent, to_agent, type, text)
           VALUES ('admin', 'sme-b', 'chat', 'ping')"""
    )
    assert proxies.scan("sme-a") == 2


def test_trigger_scanner_returns_zero_for_non_survivor(agent_factory):
    from trigger_management.scanners import proxies
    agent_factory("lone", "sme")
    assert proxies.scan("lone") == 0

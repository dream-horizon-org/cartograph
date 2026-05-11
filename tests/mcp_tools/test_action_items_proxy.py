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


def test_summary_has_proxied_count_zero_when_no_proxies(agent_factory):
    """Phase 7.4.5: summary returns proxied_count (int), NOT a list."""
    agent_factory("lone", "sme")
    out = action_items.get_action_items_summary("lone", "sme")
    assert out["proxied_count"] == 0
    # Legacy fields still present.
    for k in ("consolidations_pending", "tasks_pending", "clarifications_pending",
              "unacked_chats", "unacked_broadcasts", "terminal_pending_ack",
              "proxied_count"):
        assert k in out


def test_summary_response_shape_is_uniform_dict_str_int(agent_factory):
    """Phase 7.4.5 regression guard: every summary value must be an int.
    Pre-7.4.5 the `proxied: list` field broke the MCP client's pydantic
    inference (it inferred dict[str, int] from the int siblings, then
    crashed on the list). DEMO7 resolver hit this on every wake."""
    _merge_and_absorb(agent_factory)
    execute_mutate(
        """INSERT INTO tasks (owner_agent_id, worker_agent_id, description, status)
           VALUES ('admin', 'sme-b', 'inherited', 'BW')"""
    )
    out = action_items.get_action_items_summary("sme-a", "sme")
    for k, v in out.items():
        assert isinstance(v, int), f"summary[{k}] = {v!r} (type {type(v).__name__}); expected int"


def test_summary_proxied_count_reflects_pending_inherited_work(agent_factory):
    _merge_and_absorb(agent_factory)
    execute_mutate(
        """INSERT INTO tasks (owner_agent_id, worker_agent_id, description, status)
           VALUES ('admin', 'sme-b', 'inherited', 'BW')"""
    )
    out = action_items.get_action_items_summary("sme-a", "sme")
    # One absorbed proxy → proxied_count == 1.
    assert out["proxied_count"] == 1
    # 'my' pending counts do NOT include the proxied task.
    assert out["tasks_pending"] == 0
    # Rich per-proxy breakdown lives on detail now.
    detail = action_items.get_action_items_detail("sme-a", "sme")
    assert len(detail["proxied"]) == 1
    assert detail["proxied"][0]["proxy_agent_id"] == "sme-b"


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


# ---------- 4.1.1 + 7.4.5 — pydantic schema regression ----------

def test_summary_response_uniform_int_shape(agent_factory):
    """Phase 7.4.5: the MCP wrapper declares `dict[str, int]` for
    get_action_items_summary. Every value in the response must be an
    int — that's the *whole point* of the 7.4.5 reshape, since mixed
    types broke the MCP client's pydantic inference. Regression guard
    against re-introducing the proxied list (or any non-int field)
    into the summary response. The rich per-proxy breakdown lives on
    get_action_items_detail."""
    _merge_and_absorb(agent_factory)
    execute_mutate(
        """INSERT INTO tasks (owner_agent_id, worker_agent_id, description, status)
           VALUES ('admin', 'sme-b', 'inherited', 'BW')"""
    )
    out = action_items.get_action_items_summary("sme-a", "sme")
    assert "proxied" not in out, (
        "summary must NOT carry a `proxied` list — moved to detail in 7.4.5"
    )
    assert "proxied_count" in out
    for k, v in out.items():
        assert isinstance(v, int), (
            f"summary[{k}] = {v!r} ({type(v).__name__}); summary must be all-int"
        )

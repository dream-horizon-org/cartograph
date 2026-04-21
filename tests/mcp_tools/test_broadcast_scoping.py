"""Tests for forward-only broadcast scoping + is_persistent override."""

import time

from shared.db import execute_mutate
from cartograph_mcp.tools import broadcast, notifications
from trigger_management.scanners import broadcasts as bcast_scanner


def _orch(agent_factory): agent_factory("orch-1", "orchestrator")


def test_new_agent_does_not_see_historical_non_persistent_broadcast(agent_factory):
    _orch(agent_factory)
    # Broadcast first, agent spawned after
    broadcast.send_broadcast("orch-1", "sme", "past broadcast")
    time.sleep(0.01)  # ensure created_at ordering clean
    agent_factory("sme-late", "sme")
    assert bcast_scanner.scan("sme-late", "sme") == 0
    assert broadcast.get_unacked_broadcasts("sme-late", "sme") == []


def test_new_agent_sees_historical_persistent_broadcast(agent_factory):
    _orch(agent_factory)
    broadcast.send_broadcast("orch-1", "sme", "standing policy", persistent=True)
    time.sleep(0.01)
    agent_factory("sme-late", "sme")
    assert bcast_scanner.scan("sme-late", "sme") == 1
    unacked = broadcast.get_unacked_broadcasts("sme-late", "sme")
    assert len(unacked) == 1
    assert unacked[0]["is_persistent"] is True


def test_existing_agent_sees_non_persistent_broadcast_sent_after_spawn(agent_factory):
    _orch(agent_factory)
    agent_factory("sme-early", "sme")
    time.sleep(0.01)
    broadcast.send_broadcast("orch-1", "sme", "new policy")
    assert bcast_scanner.scan("sme-early", "sme") == 1


def test_notifications_respect_forward_only_scoping(agent_factory):
    _orch(agent_factory)
    broadcast.send_broadcast("orch-1", "sme", "pre-spawn")
    time.sleep(0.01)
    agent_factory("sme-late", "sme")
    out = notifications.get_agent_notifications(
        "sme-late", priority_from_agent_types=["orchestrator"],
    )
    assert out["high_priority_count"] == 0


def test_notifications_see_persistent_pre_spawn_broadcast(agent_factory):
    _orch(agent_factory)
    broadcast.send_broadcast("orch-1", "sme", "standing", persistent=True)
    time.sleep(0.01)
    agent_factory("sme-late", "sme")
    out = notifications.get_agent_notifications(
        "sme-late", priority_from_agent_types=["orchestrator"],
    )
    assert out["high_priority_count"] == 1


def test_send_broadcast_default_is_non_persistent(agent_factory):
    _orch(agent_factory)
    row = broadcast.send_broadcast("orch-1", "sme", "x")
    assert row["is_persistent"] is False


def test_send_broadcast_persistent_flag_stored(agent_factory):
    _orch(agent_factory)
    row = broadcast.send_broadcast("orch-1", "sme", "x", persistent=True)
    assert row["is_persistent"] is True

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


# ===== Phase 10.8.2 — defensive gate: decom can't read its broadcast queue =====


def test_get_unacked_broadcasts_rejects_decom(agent_factory):
    """get_unacked_broadcasts must refuse a decommissioned caller. Closes
    the SQ-1 defensive gap from negative-test verification — operationally
    safe (agent_manager pickup filters decom out of wake list) but the
    tool itself didn't enforce. Survivors see decom's queue via
    get_my_proxy_items, never via this direct call."""
    import pytest
    agent_factory("sme-decom", "sme")
    execute_mutate(
        "UPDATE agent_runs SET status='decommissioned' WHERE agent_id='sme-decom'"
    )
    with pytest.raises(ValueError, match="not found"):
        broadcast.get_unacked_broadcasts("sme-decom", "sme")


def test_broadcast_scanner_skips_decom(agent_factory):
    """trigger scanner must return 0 for a decommissioned agent regardless
    of pending broadcasts. Belt-and-suspenders against any future code
    path that consults the scanner directly."""
    _orch(agent_factory)
    agent_factory("sme-decom2", "sme")
    broadcast.send_broadcast("orch-1", "sme", "policy", persistent=True)
    # Active baseline: scanner sees 1
    assert bcast_scanner.scan("sme-decom2", "sme") == 1
    # Decommission → scanner returns 0
    execute_mutate(
        "UPDATE agent_runs SET status='decommissioned' WHERE agent_id='sme-decom2'"
    )
    assert bcast_scanner.scan("sme-decom2", "sme") == 0

"""Tests for sleep_self / bulk_sleep_agents / bulk_wake_agents + the
trigger scanner's sleep filter + admin-chat auto-wake."""

from datetime import datetime, timedelta, timezone
import pytest

from shared.db import execute_mutate, execute_one
from cartograph_mcp.tools import sleep as sleep_tool, chat as chat_tool
from trigger_management import trigger_loop


def _agent(agent_factory, agent_id: str, agent_type: str = "sme") -> None:
    agent_factory(agent_id, agent_type)


# ============ sleep_self ============


def test_sleep_self_sets_future_timestamp(agent_factory):
    _agent(agent_factory, "sme-1")
    out = sleep_tool.sleep_self("sme-1", duration_seconds=300, reason="waiting")
    assert out["agent_id"] == "sme-1"
    row = execute_one("SELECT sleep_until FROM agent_runs WHERE agent_id='sme-1'")
    assert row["sleep_until"] is not None
    assert row["sleep_until"] > datetime.now(tz=timezone.utc)


def test_sleep_self_rejects_zero_or_negative_duration(agent_factory):
    _agent(agent_factory, "sme-1")
    with pytest.raises(ValueError, match="positive"):
        sleep_tool.sleep_self("sme-1", 0, "x")
    with pytest.raises(ValueError, match="positive"):
        sleep_tool.sleep_self("sme-1", -10, "x")


def test_sleep_self_caps_at_seven_days(agent_factory):
    _agent(agent_factory, "sme-1")
    with pytest.raises(ValueError, match="7 days"):
        sleep_tool.sleep_self("sme-1", 86400 * 8, "x")


def test_sleep_self_requires_reason(agent_factory):
    _agent(agent_factory, "sme-1")
    with pytest.raises(ValueError, match="reason"):
        sleep_tool.sleep_self("sme-1", 60, "")


def test_sleep_self_unknown_agent_rejected():
    with pytest.raises(ValueError, match="not found"):
        sleep_tool.sleep_self("nobody", 60, "x")


# ============ bulk_sleep_agents ============


def test_bulk_sleep_by_agent_ids(agent_factory):
    agent_factory("orch-1", "orchestrator")
    _agent(agent_factory, "sme-a"); _agent(agent_factory, "sme-b")
    until = (datetime.now(tz=timezone.utc) + timedelta(hours=1)).isoformat()
    out = sleep_tool.bulk_sleep_agents(
        "orch-1", until, "downtime", agent_ids=["sme-a", "sme-b"],
    )
    assert out["count"] == 2
    assert set(out["slept"]) == {"sme-a", "sme-b"}


def test_bulk_sleep_by_agent_type(agent_factory):
    agent_factory("orch-1", "orchestrator")
    _agent(agent_factory, "sme-a"); _agent(agent_factory, "sme-b")
    _agent(agent_factory, "iter-1", "iterator")
    until = (datetime.now(tz=timezone.utc) + timedelta(minutes=10)).isoformat()
    out = sleep_tool.bulk_sleep_agents("orch-1", until, "x", agent_type="sme")
    assert out["count"] == 2
    assert set(out["slept"]) == {"sme-a", "sme-b"}


def test_bulk_sleep_refuses_orchestrator_type(agent_factory):
    agent_factory("orch-1", "orchestrator")
    until = (datetime.now(tz=timezone.utc) + timedelta(minutes=10)).isoformat()
    with pytest.raises(ValueError, match="refusing to sleep orchestrator"):
        sleep_tool.bulk_sleep_agents(
            "orch-1", until, "x", agent_type="orchestrator"
        )


def test_bulk_sleep_skips_caller(agent_factory):
    agent_factory("orch-1", "orchestrator")
    until = (datetime.now(tz=timezone.utc) + timedelta(minutes=10)).isoformat()
    out = sleep_tool.bulk_sleep_agents(
        "orch-1", until, "x", agent_ids=["orch-1"],
    )
    assert out["count"] == 0


def test_bulk_sleep_rejects_past_timestamp(agent_factory):
    agent_factory("orch-1", "orchestrator")
    past = (datetime.now(tz=timezone.utc) - timedelta(minutes=1)).isoformat()
    with pytest.raises(ValueError, match="in the future"):
        sleep_tool.bulk_sleep_agents("orch-1", past, "x", agent_type="sme")


def test_bulk_sleep_blank_wipe_refused(agent_factory):
    agent_factory("orch-1", "orchestrator")
    until = (datetime.now(tz=timezone.utc) + timedelta(minutes=10)).isoformat()
    with pytest.raises(ValueError, match="blank-wipe"):
        sleep_tool.bulk_sleep_agents("orch-1", until, "x")


def test_bulk_sleep_non_orchestrator_rejected(agent_factory):
    _agent(agent_factory, "sme-1")
    until = (datetime.now(tz=timezone.utc) + timedelta(minutes=10)).isoformat()
    with pytest.raises(ValueError, match="Only orchestrator or admin"):
        sleep_tool.bulk_sleep_agents(
            "sme-1", until, "x", agent_type="iterator",
        )


def test_bulk_sleep_as_admin_works(agent_factory):
    _agent(agent_factory, "sme-a")
    until = (datetime.now(tz=timezone.utc) + timedelta(minutes=10)).isoformat()
    out = sleep_tool.bulk_sleep_agents(
        "admin", until, "manual pause", agent_ids=["sme-a"],
    )
    assert out["count"] == 1


# ============ bulk_wake_agents ============


def test_bulk_wake_clears_sleep_until(agent_factory):
    agent_factory("orch-1", "orchestrator")
    _agent(agent_factory, "sme-a")
    until = (datetime.now(tz=timezone.utc) + timedelta(hours=1)).isoformat()
    sleep_tool.bulk_sleep_agents("orch-1", until, "x", agent_ids=["sme-a"])
    out = sleep_tool.bulk_wake_agents("orch-1", agent_ids=["sme-a"])
    assert out["count"] == 1
    row = execute_one("SELECT sleep_until FROM agent_runs WHERE agent_id='sme-a'")
    assert row["sleep_until"] is None


def test_bulk_wake_blank_wipe_refused(agent_factory):
    agent_factory("orch-1", "orchestrator")
    with pytest.raises(ValueError, match="blank-wipe"):
        sleep_tool.bulk_wake_agents("orch-1")


# ============ Trigger scanner sleep filter ============


def test_get_idle_agents_skips_sleeping(agent_factory):
    agent_factory("orch-1", "orchestrator")
    _agent(agent_factory, "sme-awake")
    _agent(agent_factory, "sme-sleepy")
    future = datetime.now(tz=timezone.utc) + timedelta(hours=1)
    execute_mutate(
        "UPDATE agent_runs SET sleep_until=%s WHERE agent_id='sme-sleepy'",
        (future,),
    )
    idle_ids = [a["agent_id"] for a in trigger_loop.get_idle_agents()]
    assert "sme-awake" in idle_ids
    assert "sme-sleepy" not in idle_ids


def test_get_idle_agents_includes_agent_whose_sleep_expired(agent_factory):
    _agent(agent_factory, "sme-just-woke")
    past = datetime.now(tz=timezone.utc) - timedelta(minutes=1)
    execute_mutate(
        "UPDATE agent_runs SET sleep_until=%s WHERE agent_id='sme-just-woke'",
        (past,),
    )
    idle_ids = [a["agent_id"] for a in trigger_loop.get_idle_agents()]
    assert "sme-just-woke" in idle_ids


# ============ Admin-chat auto-wake ============


def test_admin_chat_clears_sleep(agent_factory):
    _agent(agent_factory, "sme-sleepy")
    future = datetime.now(tz=timezone.utc) + timedelta(hours=1)
    execute_mutate(
        "UPDATE agent_runs SET sleep_until=%s WHERE agent_id='sme-sleepy'",
        (future,),
    )
    chat_tool.send_chat("admin", "sme-sleepy", "wake up, something changed")
    row = execute_one("SELECT sleep_until FROM agent_runs WHERE agent_id='sme-sleepy'")
    assert row["sleep_until"] is None


def test_non_admin_chat_does_not_clear_sleep(agent_factory):
    _agent(agent_factory, "sme-sleepy")
    future = datetime.now(tz=timezone.utc) + timedelta(hours=1)
    execute_mutate(
        "UPDATE agent_runs SET sleep_until=%s WHERE agent_id='sme-sleepy'",
        (future,),
    )
    # sme-sleepy chats to admin (valid direction). Recipient is admin,
    # not a sleeping agent, so nothing to clear.
    chat_tool.send_chat("sme-sleepy", "admin", "fyi")
    row = execute_one("SELECT sleep_until FROM agent_runs WHERE agent_id='sme-sleepy'")
    assert row["sleep_until"] is not None


# ============ sleep as a hard floor on wake paths ============
#
# Bugs surfaced in production:
#   - try_lock_agent(): could lock a sleeping agent (race between
#     get_idle_agents read and try_lock_agent write).
#   - pickup_next_locked_agent_of_type(): would pick up a trigger_lock=TRUE
#     set BEFORE sleep_self fired, waking the agent mid-sleep.
# These three tests lock in the hard-floor contract.


def test_try_lock_agent_refuses_sleeping_agent(agent_factory):
    """Even if an agent is idle + unlocked, try_lock_agent must refuse
    while sleep_until > now()."""
    _agent(agent_factory, "orch-1", "orchestrator")
    future = datetime.now(tz=timezone.utc) + timedelta(hours=1)
    execute_mutate(
        "UPDATE agent_runs SET sleep_until=%s WHERE agent_id='orch-1'", (future,)
    )
    got = trigger_loop.try_lock_agent("orch-1")
    assert got is False
    row = execute_one("SELECT trigger_lock FROM agent_runs WHERE agent_id='orch-1'")
    assert row["trigger_lock"] is False


def test_pickup_refuses_sleeping_agent(agent_factory):
    """If trigger_lock=TRUE was set before sleep_self fired, the lane
    pickup must not consume the lock while sleep is still in effect."""
    from agent_management import db as am_db
    _agent(agent_factory, "orch-1", "orchestrator")
    future = datetime.now(tz=timezone.utc) + timedelta(hours=1)
    execute_mutate(
        """UPDATE agent_runs SET trigger_lock=TRUE, sleep_until=%s
           WHERE agent_id='orch-1'""",
        (future,),
    )
    picked = am_db.pickup_next_locked_agent_of_type("orchestrator")
    assert picked is None
    # Agent stays idle + still sleeping.
    row = execute_one(
        "SELECT status, sleep_until FROM agent_runs WHERE agent_id='orch-1'"
    )
    assert row["status"] == "idle"
    assert row["sleep_until"] > datetime.now(tz=timezone.utc)


def test_release_stale_locks_on_sleeping(agent_factory):
    """release_stale_locks_on_sleeping clears the lock so the admin UI
    and scanner don't see a misleading 'locked' row on a sleeping agent."""
    from agent_management import db as am_db
    _agent(agent_factory, "orch-1", "orchestrator")
    future = datetime.now(tz=timezone.utc) + timedelta(hours=1)
    execute_mutate(
        """UPDATE agent_runs SET trigger_lock=TRUE, sleep_until=%s
           WHERE agent_id='orch-1'""",
        (future,),
    )
    assert am_db.release_stale_locks_on_sleeping() == 1
    row = execute_one(
        "SELECT trigger_lock FROM agent_runs WHERE agent_id='orch-1'"
    )
    assert row["trigger_lock"] is False
    # Still sleeping — this helper only unlocks, doesn't wake.
    row2 = execute_one(
        "SELECT sleep_until FROM agent_runs WHERE agent_id='orch-1'"
    )
    assert row2["sleep_until"] > datetime.now(tz=timezone.utc)


def test_release_stale_locks_is_noop_on_awake_agents(agent_factory):
    """Don't touch locks on agents that aren't sleeping."""
    from agent_management import db as am_db
    _agent(agent_factory, "orch-1", "orchestrator")
    execute_mutate(
        "UPDATE agent_runs SET trigger_lock=TRUE WHERE agent_id='orch-1'"
    )
    assert am_db.release_stale_locks_on_sleeping() == 0
    row = execute_one(
        "SELECT trigger_lock FROM agent_runs WHERE agent_id='orch-1'"
    )
    assert row["trigger_lock"] is True

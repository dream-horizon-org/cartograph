"""Tests for errored-agent auto-recovery: backoff ladder, cap, force-reset."""

from shared.db import execute_mutate, execute_one
from agent_management import db as am_db
from trigger_management.scanners import recovery


def _make_errored(agent_id: str, seconds_ago: int, attempts: int = 0) -> None:
    """Insert an agent already in 'errored' state, timestamped N seconds ago."""
    execute_mutate(
        """INSERT INTO agent_runs
           (agent_id, agent_type, status, error_msg, errored_at, recovery_attempts)
           VALUES (%s, 'iterator', 'errored', 'boom',
                   now() - make_interval(secs => %s), %s)""",
        (agent_id, seconds_ago, attempts),
    )


def test_errored_agent_within_backoff_is_not_recovered():
    _make_errored("iter-recent", seconds_ago=10, attempts=0)  # 60s backoff
    assert recovery.run_recovery() == 0
    row = execute_one("SELECT status FROM agent_runs WHERE agent_id='iter-recent'")
    assert row["status"] == "errored"


def test_errored_agent_past_first_backoff_is_recovered():
    _make_errored("iter-recoverable", seconds_ago=120, attempts=0)  # past 60s
    assert recovery.run_recovery() == 1
    row = execute_one(
        "SELECT status, recovery_attempts, trigger_lock, errored_at, error_msg "
        "FROM agent_runs WHERE agent_id='iter-recoverable'"
    )
    assert row["status"] == "idle"
    assert row["recovery_attempts"] == 1
    assert row["trigger_lock"] is False
    assert row["errored_at"] is None
    # error_msg is preserved for history
    assert row["error_msg"] == "boom"


def test_backoff_ladder_second_tier():
    # attempts=1 → needs 300s
    _make_errored("iter-mid", seconds_ago=200, attempts=1)
    assert recovery.run_recovery() == 0  # not yet
    execute_mutate(
        "UPDATE agent_runs SET errored_at = now() - make_interval(secs => 400) "
        "WHERE agent_id = 'iter-mid'"
    )
    assert recovery.run_recovery() == 1


def test_errored_agent_at_max_attempts_stays_errored():
    # attempts=3 == MAX_RECOVERY_ATTEMPTS — no more auto retries
    _make_errored("iter-exhausted", seconds_ago=10_000, attempts=am_db.MAX_RECOVERY_ATTEMPTS)
    assert recovery.run_recovery() == 0
    row = execute_one(
        "SELECT status, recovery_attempts FROM agent_runs WHERE agent_id='iter-exhausted'"
    )
    assert row["status"] == "errored"
    assert row["recovery_attempts"] == am_db.MAX_RECOVERY_ATTEMPTS


def test_force_reset_bypasses_attempt_cap():
    _make_errored("iter-dead", seconds_ago=10, attempts=am_db.MAX_RECOVERY_ATTEMPTS)
    assert am_db.force_reset_agent("iter-dead") is True
    row = execute_one(
        "SELECT status, recovery_attempts, error_msg, errored_at "
        "FROM agent_runs WHERE agent_id='iter-dead'"
    )
    assert row["status"] == "idle"
    assert row["recovery_attempts"] == 0
    assert row["error_msg"] is None
    assert row["errored_at"] is None


def test_force_reset_noop_on_non_errored():
    execute_mutate(
        """INSERT INTO agent_runs (agent_id, agent_type, status)
           VALUES ('iter-idle', 'iterator', 'idle')"""
    )
    assert am_db.force_reset_agent("iter-idle") is False


def test_set_agent_errored_stamps_errored_at():
    execute_mutate(
        """INSERT INTO agent_runs (agent_id, agent_type, status)
           VALUES ('iter-about-to-fail', 'iterator', 'running')"""
    )
    am_db.set_agent_errored("iter-about-to-fail", "subprocess exit 1\nTimeoutExpired")
    row = execute_one(
        "SELECT status, error_msg, errored_at, trigger_lock "
        "FROM agent_runs WHERE agent_id='iter-about-to-fail'"
    )
    assert row["status"] == "errored"
    assert "subprocess exit 1" in row["error_msg"]
    assert row["errored_at"] is not None
    assert row["trigger_lock"] is False


def test_clear_recovery_state_resets_counter():
    _make_errored("iter-flaky", seconds_ago=10_000, attempts=2)
    # Simulate successful reset + invocation
    am_db.reset_agent_for_recovery("iter-flaky")
    am_db.clear_recovery_state("iter-flaky")
    row = execute_one(
        "SELECT recovery_attempts, error_msg FROM agent_runs WHERE agent_id='iter-flaky'"
    )
    assert row["recovery_attempts"] == 0
    assert row["error_msg"] is None

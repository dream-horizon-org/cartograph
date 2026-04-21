"""Tests for pickup_next_locked_agent_of_type + InvokeLoop lane dispatch."""

import threading
import time

from shared.db import execute_mutate, execute_one, execute
from agent_management import db as am_db


def _make_agent(agent_id: str, agent_type: str, locked: bool = False,
                invocation_count: int = 0) -> None:
    execute_mutate(
        """INSERT INTO agent_runs
           (agent_id, agent_type, status, trigger_lock, invocation_count)
           VALUES (%s, %s, 'idle', %s, %s)""",
        (agent_id, agent_type, locked, invocation_count),
    )


# ============ pickup_next_locked_agent_of_type ============


def test_pickup_none_when_no_locked_agent():
    _make_agent("orch-1", "orchestrator", locked=False)
    assert am_db.pickup_next_locked_agent_of_type("orchestrator") is None


def test_pickup_claims_highest_priority_locked_agent():
    _make_agent("sme-a", "sme", locked=True, invocation_count=5)
    _make_agent("sme-b", "sme", locked=True, invocation_count=1)  # higher pri (lower inv)
    _make_agent("sme-c", "sme", locked=True, invocation_count=3)

    claimed = am_db.pickup_next_locked_agent_of_type("sme")
    assert claimed["agent_id"] == "sme-b"
    # Status transitioned + lock cleared + counter incremented
    assert claimed["status"] == "running"
    assert claimed["trigger_lock"] is False
    assert claimed["invocation_count"] == 2  # was 1, now 2


def test_pickup_ignores_other_types():
    _make_agent("sme-1", "sme", locked=True, invocation_count=0)
    _make_agent("iter-1", "iterator", locked=True, invocation_count=0)
    claimed = am_db.pickup_next_locked_agent_of_type("iterator")
    assert claimed["agent_id"] == "iter-1"


def test_pickup_skips_unlocked():
    _make_agent("sme-idle", "sme", locked=False, invocation_count=0)
    _make_agent("sme-lock", "sme", locked=True, invocation_count=5)
    claimed = am_db.pickup_next_locked_agent_of_type("sme")
    assert claimed["agent_id"] == "sme-lock"


def test_pickup_concurrent_no_double_claim():
    """Two threads racing on the same lane should pick different agents."""
    _make_agent("sme-a", "sme", locked=True, invocation_count=0)
    _make_agent("sme-b", "sme", locked=True, invocation_count=0)

    results: list = []
    lock = threading.Lock()

    def worker():
        r = am_db.pickup_next_locked_agent_of_type("sme")
        with lock:
            results.append(r["agent_id"] if r else None)

    t1 = threading.Thread(target=worker)
    t2 = threading.Thread(target=worker)
    t1.start(); t2.start(); t1.join(); t2.join()

    assert sorted(results) == ["sme-a", "sme-b"]


# ============ InvokeLoop lane dispatch ============


class _FakeAgentManager:
    """Records invoke_agent calls; does no subprocess work."""
    def __init__(self):
        self.invoked: list[str] = []
        self.lock = threading.Lock()

    def invoke_agent(self, agent_id, prompt=None, already_picked_up=False):
        with self.lock:
            self.invoked.append(agent_id)
        # Simulate work completing: transition back to idle so we don't
        # re-claim it infinitely in the test.
        am_db.update_agent_status(agent_id, "idle")
        am_db.clear_recovery_state(agent_id)
        return ""


def test_lanes_dispatch_picks_up_and_invokes(monkeypatch):
    """Integration-ish: start lanes, enqueue locked agents, verify they get invoked."""
    from agent_management import invoke_loop as il
    # Shrink lanes for the test to keep threads low
    monkeypatch.setattr("shared.config.INVOKE_LANES_ORCH", 1)
    monkeypatch.setattr("shared.config.INVOKE_LANES_ITER", 1)
    monkeypatch.setattr("shared.config.INVOKE_LANES_RES",  1)
    monkeypatch.setattr("shared.config.INVOKE_LANES_SME",  2)

    _make_agent("orch-1", "orchestrator", locked=True, invocation_count=0)
    _make_agent("sme-1",  "sme",          locked=True, invocation_count=0)
    _make_agent("sme-2",  "sme",          locked=True, invocation_count=0)
    _make_agent("iter-1", "iterator",     locked=True, invocation_count=0)

    mgr = _FakeAgentManager()
    loop = il.InvokeLoop(agent_manager=mgr, poll_interval=0.1, heartbeat_timeout=60)
    assert loop.total_lanes == 5

    loop.start()
    # Give workers a couple of poll cycles to pick up their agents.
    deadline = time.time() + 3.0
    while time.time() < deadline:
        if len(mgr.invoked) >= 4:
            break
        time.sleep(0.1)
    loop.stop()

    assert set(mgr.invoked) == {"orch-1", "sme-1", "sme-2", "iter-1"}


def test_lanes_dispatch_respects_sme_cap(monkeypatch):
    """With SME lanes=1, 3 locked SMEs are invoked one at a time (serially)."""
    from agent_management import invoke_loop as il
    monkeypatch.setattr("shared.config.INVOKE_LANES_ORCH", 0)
    monkeypatch.setattr("shared.config.INVOKE_LANES_ITER", 0)
    monkeypatch.setattr("shared.config.INVOKE_LANES_RES",  0)
    monkeypatch.setattr("shared.config.INVOKE_LANES_SME",  1)

    _make_agent("sme-a", "sme", locked=True, invocation_count=0)
    _make_agent("sme-b", "sme", locked=True, invocation_count=0)
    _make_agent("sme-c", "sme", locked=True, invocation_count=0)

    concurrent_peak = {"n": 0, "now": 0}
    peak_lock = threading.Lock()

    class _TrackingMgr:
        def __init__(self): self.invoked = []
        def invoke_agent(self, agent_id, prompt=None, already_picked_up=False):
            with peak_lock:
                concurrent_peak["now"] += 1
                concurrent_peak["n"] = max(concurrent_peak["n"], concurrent_peak["now"])
            try:
                time.sleep(0.15)  # simulate work
                self.invoked.append(agent_id)
            finally:
                with peak_lock:
                    concurrent_peak["now"] -= 1
            am_db.update_agent_status(agent_id, "idle")

    mgr = _TrackingMgr()
    loop = il.InvokeLoop(agent_manager=mgr, poll_interval=0.05, heartbeat_timeout=60)
    loop.start()
    deadline = time.time() + 5.0
    while time.time() < deadline:
        if len(mgr.invoked) >= 3:
            break
        time.sleep(0.05)
    loop.stop()

    assert sorted(mgr.invoked) == ["sme-a", "sme-b", "sme-c"]
    assert concurrent_peak["n"] == 1  # SME cap = 1


def test_lanes_dispatch_parallelism_across_types(monkeypatch):
    """Orch + SME in different lanes → can run truly concurrently."""
    from agent_management import invoke_loop as il
    monkeypatch.setattr("shared.config.INVOKE_LANES_ORCH", 1)
    monkeypatch.setattr("shared.config.INVOKE_LANES_ITER", 0)
    monkeypatch.setattr("shared.config.INVOKE_LANES_RES",  0)
    monkeypatch.setattr("shared.config.INVOKE_LANES_SME",  1)

    _make_agent("orch-1", "orchestrator", locked=True, invocation_count=0)
    _make_agent("sme-1",  "sme",          locked=True, invocation_count=0)

    concurrent_peak = {"n": 0, "now": 0}
    peak_lock = threading.Lock()

    class _TrackingMgr:
        def __init__(self): self.invoked = []
        def invoke_agent(self, agent_id, prompt=None, already_picked_up=False):
            with peak_lock:
                concurrent_peak["now"] += 1
                concurrent_peak["n"] = max(concurrent_peak["n"], concurrent_peak["now"])
            try:
                time.sleep(0.3)
                self.invoked.append(agent_id)
            finally:
                with peak_lock:
                    concurrent_peak["now"] -= 1
            am_db.update_agent_status(agent_id, "idle")

    mgr = _TrackingMgr()
    loop = il.InvokeLoop(agent_manager=mgr, poll_interval=0.05, heartbeat_timeout=60)
    loop.start()
    deadline = time.time() + 5.0
    while time.time() < deadline:
        if len(mgr.invoked) >= 2:
            break
        time.sleep(0.05)
    loop.stop()

    assert sorted(mgr.invoked) == ["orch-1", "sme-1"]
    assert concurrent_peak["n"] == 2  # ran in parallel

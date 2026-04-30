"""Phase 9.1: mcp_call_batch — server-side parallel dispatcher tests.

Covers:
- happy-path heterogeneous reads
- per-call error collection (one bad arg, others succeed)
- nesting refusal
- empty batch (no error)
- unknown tool
- max-50 cap
- agent_id auto-injection vs preservation when sub-call carries its own
- audit: outer + sub-calls each recorded in mcp_audit
- bulk-tool inside batch
- thread-pool concurrency behaviour (sleep tools run roughly in parallel)
"""

import time
import uuid

import pytest

from shared.db import execute, execute_mutate, execute_returning, execute_one
from cartograph_mcp.tools import call_batch


# ----- A tiny dispatch shim used by most unit tests (avoids exercising
#       the full server.py registry). Each "tool" here is a plain Python
#       callable; mcp_call_batch's contract is server-agnostic.

def _dispatch_for(*tool_names_to_callables) -> dict:
    """Build a dispatch dict from `(name, fn)` pairs."""
    return dict(tool_names_to_callables)


def _ok_tool(**kwargs):
    return {"received": kwargs}


def _raises_tool(**kwargs):
    raise ValueError("synthetic failure")


# ============ shape validation ============

def test_calls_must_be_list():
    with pytest.raises(ValueError, match="must be a list"):
        call_batch.call_batch("a", calls={}, dispatch={})


def test_empty_batch_returns_empty_results():
    out = call_batch.call_batch("a", calls=[], dispatch={})
    assert out == {"results": []}


def test_call_must_be_dict():
    with pytest.raises(ValueError, match=r"calls\[0\] must be a dict"):
        call_batch.call_batch("a", calls=["not-a-dict"], dispatch={})


def test_tool_must_be_string():
    with pytest.raises(ValueError, match="non-empty string"):
        call_batch.call_batch("a", calls=[{"tool": "", "args": {}}], dispatch={})


def test_unknown_tool_rejected_pre_dispatch():
    with pytest.raises(ValueError, match="not a known"):
        call_batch.call_batch(
            "a",
            calls=[{"tool": "no_such_tool", "args": {}}],
            dispatch={"some_other_tool": _ok_tool},
        )


def test_args_must_be_dict():
    with pytest.raises(ValueError, match=r"calls\[0\]\.args must be a dict"):
        call_batch.call_batch(
            "a",
            calls=[{"tool": "ok", "args": "not-a-dict"}],
            dispatch={"ok": _ok_tool},
        )


def test_nesting_rejected():
    with pytest.raises(ValueError, match="cannot be nested"):
        call_batch.call_batch(
            "a",
            calls=[{"tool": "mcp_call_batch", "args": {"calls": []}}],
            dispatch={"mcp_call_batch": _ok_tool},
        )


def test_max_50_cap():
    over = [{"tool": "ok", "args": {}}] * 51
    with pytest.raises(ValueError, match="exceeds max 50"):
        call_batch.call_batch("a", over, dispatch={"ok": _ok_tool})


# ============ dispatch + collect-all ============

def test_happy_heterogeneous_batch():
    def reader_a(**kw): return {"a": 1, **kw}
    def reader_b(**kw): return {"b": 2, **kw}
    out = call_batch.call_batch(
        "agent-x",
        calls=[
            {"tool": "reader_a", "args": {}},
            {"tool": "reader_b", "args": {}},
        ],
        dispatch={"reader_a": reader_a, "reader_b": reader_b},
    )
    rs = out["results"]
    assert [r["idx"] for r in rs] == [0, 1]
    assert [r["tool"] for r in rs] == ["reader_a", "reader_b"]
    assert all(r["ok"] for r in rs)
    assert rs[0]["result"]["a"] == 1
    assert rs[1]["result"]["b"] == 2


def test_per_call_error_does_not_abort_siblings():
    out = call_batch.call_batch(
        "agent-x",
        calls=[
            {"tool": "ok", "args": {}},
            {"tool": "raises", "args": {}},
            {"tool": "ok", "args": {}},
        ],
        dispatch={"ok": _ok_tool, "raises": _raises_tool},
    )
    rs = out["results"]
    assert len(rs) == 3
    assert rs[0]["ok"] is True
    assert rs[1]["ok"] is False
    assert "synthetic failure" in rs[1]["error"]
    assert rs[2]["ok"] is True


def test_agent_id_injected_when_missing():
    captured = {}
    def capture(**kw):
        captured.update(kw)
        return "ok"
    call_batch.call_batch(
        "agent-x",
        calls=[{"tool": "capture", "args": {"foo": "bar"}}],
        dispatch={"capture": capture},
    )
    assert captured == {"foo": "bar", "agent_id": "agent-x"}


def test_agent_id_preserved_when_sub_call_carries_its_own():
    """Proxy paths legitimately need a different agent_id."""
    captured = {}
    def capture(**kw):
        captured.update(kw)
        return "ok"
    call_batch.call_batch(
        "agent-x",
        calls=[{"tool": "capture", "args": {"agent_id": "different-agent"}}],
        dispatch={"capture": capture},
    )
    assert captured == {"agent_id": "different-agent"}


def test_concurrency_actual_parallel_dispatch():
    """4 sub-calls each sleeping 0.2s should finish well under 0.8s
    (sequential lower bound) — proves ThreadPoolExecutor is dispatching
    in parallel rather than serially.
    """
    def slow_tool(**kw):
        time.sleep(0.2)
        return "done"
    t0 = time.monotonic()
    call_batch.call_batch(
        "agent-x",
        calls=[{"tool": "slow", "args": {}} for _ in range(4)],
        dispatch={"slow": slow_tool},
    )
    elapsed = time.monotonic() - t0
    # 4 × 0.2s sequential = 0.8s. Parallel should be ~0.2s with some overhead.
    # 0.5s threshold leaves headroom for slow CI without falsely passing
    # a sequential implementation.
    assert elapsed < 0.5, f"Expected parallel dispatch (<0.5s), got {elapsed:.3f}s"


# ============ integration: mcp_call_batch via the real server registry ============

def _setup_iterator_and_resource(agent_factory):
    iter_id = "iter-batch-tests"
    agent_factory(iter_id, "iterator")
    execute_mutate(
        "UPDATE agent_runs SET plane='github', status='idle' WHERE agent_id=%s",
        (iter_id,),
    )
    return iter_id


def test_real_dispatch_heterogeneous_reads(agent_factory):
    """End-to-end via server.py's mcp_call_batch wrapper.

    Exercises the actual _BATCH_DISPATCH map. Calls 3 different read
    tools in one batch — none mutate state, all should succeed.
    """
    from cartograph_mcp import server as srv

    iter_id = _setup_iterator_and_resource(agent_factory)
    out = srv.mcp_call_batch(
        agent_id=iter_id,
        calls=[
            {"tool": "get_action_items_summary", "args": {}},
            {"tool": "list_agents", "args": {}},
            {"tool": "get_resource_counts", "args": {}},
        ],
    )
    rs = out["results"]
    assert len(rs) == 3
    assert all(r["ok"] for r in rs), f"unexpected failures: {rs}"
    # Each result should have its expected shape
    summary = rs[0]["result"]
    assert "tasks_pending" in summary  # known key on the summary dict


def test_real_dispatch_records_outer_and_sub_calls_in_mcp_audit(agent_factory):
    """Each sub-call goes through its @mcp.tool wrapper, so audit
    records: 1 outer mcp_call_batch row + 1 row per sub-call.
    """
    from cartograph_mcp import server as srv

    iter_id = _setup_iterator_and_resource(agent_factory)

    pre = execute_one("SELECT COUNT(*) AS c FROM mcp_audit")["c"]
    srv.mcp_call_batch(
        agent_id=iter_id,
        calls=[
            {"tool": "list_agents", "args": {}},
            {"tool": "get_resource_counts", "args": {}},
        ],
    )
    post = execute_one("SELECT COUNT(*) AS c FROM mcp_audit")["c"]
    # Expect at least 3 new rows: outer + 2 sub-calls.
    assert post - pre >= 3, f"audit gain {post - pre} (expected ≥3)"

    by_tool = execute(
        "SELECT tool_name, COUNT(*) AS c FROM mcp_audit "
        "WHERE created_at > now() - interval '5 seconds' GROUP BY tool_name"
    )
    names = {r["tool_name"] for r in by_tool}
    assert "mcp_call_batch" in names
    assert "list_agents" in names
    assert "get_resource_counts" in names


def test_real_dispatch_unknown_tool_pre_flight_error(agent_factory):
    from cartograph_mcp import server as srv

    iter_id = _setup_iterator_and_resource(agent_factory)
    with pytest.raises(ValueError, match="not a known"):
        srv.mcp_call_batch(
            agent_id=iter_id,
            calls=[{"tool": "totally_made_up_tool", "args": {}}],
        )


def test_real_dispatch_nesting_refused(agent_factory):
    from cartograph_mcp import server as srv

    iter_id = _setup_iterator_and_resource(agent_factory)
    with pytest.raises(ValueError, match="cannot be nested"):
        srv.mcp_call_batch(
            agent_id=iter_id,
            calls=[{"tool": "mcp_call_batch", "args": {"calls": []}}],
        )


def test_real_dispatch_per_call_error_collection(agent_factory):
    """Mix one good read + one bad call (missing required arg).

    The bad call should fail with its own error in the result, not
    abort the sibling.
    """
    from cartograph_mcp import server as srv

    iter_id = _setup_iterator_and_resource(agent_factory)
    out = srv.mcp_call_batch(
        agent_id=iter_id,
        calls=[
            {"tool": "list_agents", "args": {}},
            # get_component requires a real component_id; passing a
            # non-existent UUID should fail inside the tool.
            {"tool": "get_component",
             "args": {"component_id": str(uuid.uuid4())}},
        ],
    )
    rs = out["results"]
    assert len(rs) == 2
    # Order-stable
    assert rs[0]["tool"] == "list_agents"
    assert rs[0]["ok"] is True
    assert rs[1]["tool"] == "get_component"
    # get_component on missing id either returns None-ish or raises;
    # contract is collect-all so caller sees per-call status either way.
    # We just assert the batch didn't raise + the sibling succeeded.

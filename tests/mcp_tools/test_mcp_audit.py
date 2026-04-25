"""Phase 5.10: per-call audit decorator + mcp_audit table."""

import pytest

from shared.db import execute, execute_one
from cartograph_mcp.audit import audited, _hash_args


# ---- decorator behaviour ----

def test_records_ok_call(agent_factory):
    agent_factory("sme-x", "sme")
    @audited
    def my_tool(agent_id, x, y):
        return x + y
    out = my_tool("sme-x", 1, 2)
    assert out == 3
    row = execute_one(
        "SELECT * FROM mcp_audit WHERE tool_name='my_tool' "
        "ORDER BY created_at DESC LIMIT 1"
    )
    assert row["agent_id"] == "sme-x"
    assert row["result_status"] == "ok"
    assert row["error_msg"] is None
    assert row["duration_ms"] >= 0


def test_records_error_call(agent_factory):
    agent_factory("sme-x", "sme")
    @audited
    def boom(agent_id):
        raise RuntimeError("nope")
    with pytest.raises(RuntimeError):
        boom("sme-x")
    row = execute_one(
        "SELECT * FROM mcp_audit WHERE tool_name='boom' "
        "ORDER BY created_at DESC LIMIT 1"
    )
    assert row["result_status"] == "error"
    assert "nope" in row["error_msg"]


def test_args_hash_stable_for_equivalent_calls():
    h1 = _hash_args(("a", 1), {"x": 2, "y": 3})
    h2 = _hash_args(("a", 1), {"y": 3, "x": 2})  # kwargs reordered
    assert h1 == h2


def test_args_hash_differs_for_different_calls():
    h1 = _hash_args(("a",), {})
    h2 = _hash_args(("b",), {})
    assert h1 != h2


def test_audit_failure_does_not_break_tool(monkeypatch, agent_factory):
    """If the mcp_audit insert fails, the tool's return value still
    flows through (audit is observability, not a hard dep)."""
    agent_factory("sme-x", "sme")
    monkeypatch.setattr(
        "cartograph_mcp.audit.execute_mutate",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down")),
    )
    @audited
    def my_tool(agent_id):
        return "ok-result"
    assert my_tool("sme-x") == "ok-result"


def test_kwargs_agent_id_recorded(agent_factory):
    agent_factory("sme-y", "sme")
    @audited
    def kwarg_tool(*, agent_id, message):
        return message
    out = kwarg_tool(agent_id="sme-y", message="hi")
    assert out == "hi"
    row = execute_one(
        "SELECT agent_id FROM mcp_audit WHERE tool_name='kwarg_tool' "
        "ORDER BY created_at DESC LIMIT 1"
    )
    assert row["agent_id"] == "sme-y"

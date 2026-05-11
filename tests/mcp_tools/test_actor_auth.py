"""Phase 4: tests for the require_active_agent helper + proxy ContextVar."""

import pytest

from shared.actor_auth import (
    require_active_agent,
    push_proxy_context,
    pop_proxy_context,
    get_proxy_context,
)
from shared.db import execute_mutate


def _make_agent(agent_id: str, status: str = "idle"):
    execute_mutate(
        "INSERT INTO agent_runs (agent_id, agent_type, status) VALUES (%s, 'sme', %s)",
        (agent_id, status),
    )


# ------------------------- strict mode (default) -------------------------


def test_require_active_agent_returns_row_for_active():
    _make_agent("a")
    row = require_active_agent("a")
    assert row["agent_id"] == "a"
    assert row["status"] == "idle"
    assert row["agent_type"] == "sme"


def test_require_active_agent_raises_for_missing():
    with pytest.raises(ValueError, match="not found"):
        require_active_agent("ghost")


def test_require_active_agent_raises_for_decommissioned_by_default():
    _make_agent("a", status="decommissioned")
    with pytest.raises(ValueError, match="not found"):
        require_active_agent("a")


# ------------------------- proxy context permits -------------------------


def test_proxy_ctx_permits_matching_proxy_agent():
    _make_agent("survivor")
    _make_agent("dead", status="decommissioned")
    token = push_proxy_context(proxy_agent_id="dead", survivor_id="survivor")
    try:
        # Decommissioned agent IS reachable inside the ctx scope.
        row = require_active_agent("dead")
        assert row["agent_id"] == "dead"
        assert row["status"] == "decommissioned"
    finally:
        pop_proxy_context(token)


def test_proxy_ctx_only_permits_the_matching_agent():
    """Survivor running the router is NOT the proxy agent — its lookup
    should still be strict. Another random decommissioned agent's
    lookup inside the same ctx should also be strict."""
    _make_agent("survivor")
    _make_agent("dead", status="decommissioned")
    _make_agent("other_dead", status="decommissioned")
    token = push_proxy_context(proxy_agent_id="dead", survivor_id="survivor")
    try:
        # The active survivor: permitted.
        assert require_active_agent("survivor")["agent_id"] == "survivor"
        # The matching proxy: permitted.
        assert require_active_agent("dead")["agent_id"] == "dead"
        # A different decommissioned agent: still blocked.
        with pytest.raises(ValueError, match="not found"):
            require_active_agent("other_dead")
    finally:
        pop_proxy_context(token)


def test_ctx_is_reset_after_pop():
    _make_agent("dead", status="decommissioned")
    token = push_proxy_context(proxy_agent_id="dead", survivor_id="x")
    assert get_proxy_context() is not None
    pop_proxy_context(token)
    assert get_proxy_context() is None
    # Strict again after reset.
    with pytest.raises(ValueError):
        require_active_agent("dead")


def test_ctx_resets_on_exception_when_used_with_try_finally():
    _make_agent("dead", status="decommissioned")
    token = push_proxy_context(proxy_agent_id="dead", survivor_id="x")
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        pass
    finally:
        pop_proxy_context(token)
    assert get_proxy_context() is None


# ------------------------- sanity: returned shape -------------------------


def test_returns_all_expected_columns():
    # components.py's _caller used to read 'plane' — make sure we surface it.
    execute_mutate(
        "INSERT INTO agent_runs (agent_id, agent_type, status, plane) "
        "VALUES ('pl', 'sme', 'idle', 'github')"
    )
    row = require_active_agent("pl")
    assert row["plane"] == "github"
    assert row["agent_type"] == "sme"
    assert row["status"] == "idle"

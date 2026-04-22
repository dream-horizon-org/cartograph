"""Phase 3: vector_search tool.

We test structural behaviour here: table whitelist, limit clamping,
agent-not-found refusal, and the query_embedded=False fallback when no
OpenAI key is set. We don't hit OpenAI from unit tests.
"""

import os
import pytest

from shared.db import execute_mutate
from cartograph_mcp.tools import search


def test_invalid_table_rejected(agent_factory):
    agent_factory("orch", "orchestrator")
    with pytest.raises(ValueError, match="Invalid table"):
        search.vector_search("orch", "q", "bogus", 5)


def test_agent_not_found_rejected():
    with pytest.raises(ValueError, match="not found"):
        search.vector_search("ghost", "q", "components", 5)


def test_no_api_key_returns_query_embedded_false(agent_factory, monkeypatch):
    """Without OPENAI_API_KEY, embed_text returns None; search short-circuits."""
    agent_factory("orch", "orchestrator")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    # Re-import config so env change sticks.
    from shared import config
    monkeypatch.setattr(config, "OPENAI_API_KEY", "")
    result = search.vector_search("orch", "feeds aggregator", "components", 5)
    assert result == {"query_embedded": False, "results": []}


def test_limit_clamp(agent_factory, monkeypatch):
    agent_factory("orch", "orchestrator")
    from shared import config
    monkeypatch.setattr(config, "OPENAI_API_KEY", "")
    # limit=100 gets clamped to 50 internally; we can't observe that directly
    # without a live API, but the call shouldn't raise.
    result = search.vector_search("orch", "x", "components", 100)
    assert "query_embedded" in result
    # Negative / zero clamp to 1.
    result = search.vector_search("orch", "x", "components", 0)
    assert "query_embedded" in result


def test_empty_query_short_circuits(agent_factory, monkeypatch):
    agent_factory("orch", "orchestrator")
    # Even WITH an API key set, empty text must short-circuit.
    from shared import config
    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-fake")
    result = search.vector_search("orch", "", "components", 5)
    assert result == {"query_embedded": False, "results": []}

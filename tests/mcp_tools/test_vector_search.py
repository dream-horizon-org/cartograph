"""Phase 3 / 3.7: vector_search tool.

Tests cover:
  - table whitelist + agent-not-found refusals
  - limit clamping
  - graceful degrade when Ollama is unreachable (returns
    query_embedded=False, not a raise)
  - empty query short-circuit
  - end-to-end (requires a live Ollama with the embedding model pulled;
    auto-skipped otherwise)

Phase 3.7 flipped the embedder from OpenAI's text-embedding-3-small (1536d,
remote HTTP) to a local Ollama mxbai-embed-large (1024d, Metal GPU). The
structural tests point the client at an unreachable URL to force the
degrade path without needing a specific environment.
"""

import pytest

from shared.db import execute_mutate
from cartograph_mcp.tools import search, components, resources


def test_invalid_table_rejected(agent_factory):
    agent_factory("orch", "orchestrator")
    with pytest.raises(ValueError, match="Invalid table"):
        search.vector_search("orch", "q", "bogus", 5)


def test_agent_not_found_rejected():
    with pytest.raises(ValueError, match="not found"):
        search.vector_search("ghost", "q", "components", 5)


def test_ollama_unreachable_returns_query_embedded_false(agent_factory, monkeypatch):
    """If Ollama is down, search must return False flag — not raise."""
    agent_factory("orch", "orchestrator")
    from shared import config
    monkeypatch.setattr(config, "OLLAMA_URL", "http://localhost:1")
    result = search.vector_search("orch", "feeds aggregator", "components", 5)
    assert result == {"query_embedded": False, "results": []}


def test_limit_clamp(agent_factory, monkeypatch):
    agent_factory("orch", "orchestrator")
    from shared import config
    monkeypatch.setattr(config, "OLLAMA_URL", "http://localhost:1")
    # limit=100 clamps to 50; limit=0 clamps to 1. Can't observe the
    # internal value without a live embed, but the call must not raise.
    result = search.vector_search("orch", "x", "components", 100)
    assert "query_embedded" in result
    result = search.vector_search("orch", "x", "components", 0)
    assert "query_embedded" in result


def test_empty_query_short_circuits(agent_factory):
    """Empty query text must short-circuit even with a reachable Ollama."""
    agent_factory("orch", "orchestrator")
    result = search.vector_search("orch", "", "components", 5)
    assert result == {"query_embedded": False, "results": []}


# ------ live Ollama end-to-end (opt-in) ------


def _ollama_reachable() -> bool:
    import urllib.request
    try:
        urllib.request.urlopen("http://localhost:11434/api/version", timeout=1).read()
        return True
    except Exception:
        return False


@pytest.mark.skipif(
    not _ollama_reachable(),
    reason="local Ollama not reachable on :11434 — skipping end-to-end",
)
def test_end_to_end_embed_and_search(agent_factory):
    """With a live Ollama, upsert a component and get it back via
    vector_search at high similarity."""
    # Set up an SME that owns a component.
    execute_mutate(
        """INSERT INTO agent_runs (agent_id, agent_type, plane, status)
           VALUES ('iter-gh', 'iterator', 'github', 'idle')"""
    )
    r = resources.upsert_resource(
        "iter-gh", "github", "repo", "dream11/feeds-aggregator"
    )
    agent_factory("sme-feeds", "sme")
    execute_mutate(
        """INSERT INTO resource_component_agents (resource_id, component_id, agent_id)
           VALUES (%s, NULL, 'sme-feeds')""",
        (r["id"],),
    )
    components.upsert_component("sme-feeds", {
        "canonical_name": "dream11/feeds-aggregator",
        "display_name":   "Feeds Aggregator",
        "component_type": "application",
    })
    out = search.vector_search("sme-feeds", "feeds aggregator service", "components", 5)
    assert out["query_embedded"] is True
    assert len(out["results"]) >= 1
    top = out["results"][0]
    assert top["canonical_name"] == "dream11/feeds-aggregator"
    assert top["similarity"] > 0.5

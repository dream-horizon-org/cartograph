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


def test_embed_unreachable_returns_query_embedded_false(agent_factory, monkeypatch):
    """If the embedding provider is down, search must return False — not raise."""
    agent_factory("orch", "orchestrator")
    from shared import embedding as emb_mod
    monkeypatch.setattr(emb_mod, "embed_text", lambda *a, **k: None)
    result = search.vector_search("orch", "feeds aggregator", "components", 5)
    assert result == {"query_embedded": False, "results": []}


def test_limit_clamp(agent_factory, monkeypatch):
    agent_factory("orch", "orchestrator")
    from shared import embedding as emb_mod
    monkeypatch.setattr(emb_mod, "embed_text", lambda *a, **k: None)
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
    # Phase 10.7: exclude_self=True is now DEFAULT. sme-feeds owns the
    # only component, so default-exclude would return 0. Pass
    # exclude_self=False to verify the basic embed→search→rank path
    # finds the component.
    out = search.vector_search(
        "sme-feeds", "feeds aggregator service", "components", 5,
        exclude_self=False,
    )
    assert out["query_embedded"] is True
    assert len(out["results"]) >= 1
    top = out["results"][0]
    assert top["canonical_name"] == "dream11/feeds-aggregator"
    assert top["similarity"] > 0.5
    # Phase 7.4.4 lean projection: result rows must NOT carry the raw
    # 1024-d embedding vector or heavy JSONB blobs (component_doc_md,
    # source_slice, metadata).
    # Phase 10.7: `description` IS now in projection (dense embed-target).
    forbidden = {"embedding", "component_doc_md", "source_slice", "metadata"}
    leaked = forbidden & set(top.keys())
    assert not leaked, f"vector_search components leaked heavy fields: {leaked}"
    assert "description" in top, "Phase 10.7 lean projection should include description"


# ============ Phase 10.7: filters + exclude_self ============


def _setup_two_smes(agent_factory):
    """Two SMEs each owning a component on github plane. Returns ids."""
    execute_mutate(
        """INSERT INTO agent_runs (agent_id, agent_type, plane, status)
           VALUES ('iter-gh', 'iterator', 'github', 'idle')""",
    )
    r_a = resources.upsert_resource("iter-gh", "github", "repo", "o/A")
    r_b = resources.upsert_resource("iter-gh", "github", "repo", "o/B")
    agent_factory("sme-A-vs", "sme")
    agent_factory("sme-B-vs", "sme")
    execute_mutate(
        """INSERT INTO resource_component_agents (resource_id, component_id, agent_id)
           VALUES (%s, NULL, 'sme-A-vs'), (%s, NULL, 'sme-B-vs')""",
        (r_a["id"], r_b["id"]),
    )
    comp_a = components.upsert_component("sme-A-vs", {
        "canonical_name": "auth-svc-A", "display_name": "Auth A",
        "component_type": "application",
        "description": "auth service A handles login",
    })
    comp_b = components.upsert_component("sme-B-vs", {
        "canonical_name": "auth-svc-B", "display_name": "Auth B",
        "component_type": "database",
        "description": "auth db B stores credentials",
    })
    return comp_a, comp_b


def test_phase10_7_exclude_self_default_true_skips_own_component(agent_factory):
    """Default exclude_self=True. Caller's own component must be missing
    from results even if it's the closest match.
    """
    comp_a, comp_b = _setup_two_smes(agent_factory)
    # sme-A queries — should NOT see auth-svc-A in results.
    out = search.vector_search("sme-A-vs", "auth", "components", 10)
    if not out["query_embedded"]:
        pytest.skip("Ollama unreachable")
    canonicals = [r["canonical_name"] for r in out["results"]]
    assert "auth-svc-A" not in canonicals, \
        "exclude_self=True (default) should skip caller's own component"


def test_phase10_7_exclude_self_false_includes_own(agent_factory):
    """Explicit exclude_self=False brings own component back."""
    comp_a, comp_b = _setup_two_smes(agent_factory)
    out = search.vector_search(
        "sme-A-vs", "auth", "components", 10, exclude_self=False,
    )
    if not out["query_embedded"]:
        pytest.skip("Ollama unreachable")
    canonicals = [r["canonical_name"] for r in out["results"]]
    assert "auth-svc-A" in canonicals


def test_phase10_7_exclude_self_for_orch_silent_noop(agent_factory):
    """Orch owns no components. exclude_self=True should be a no-op
    (NOT raise), and the SMEs' components should appear in results.
    """
    comp_a, comp_b = _setup_two_smes(agent_factory)
    agent_factory("orch-vs", "orchestrator")
    out = search.vector_search("orch-vs", "auth", "components", 10)
    if not out["query_embedded"]:
        pytest.skip("Ollama unreachable")
    canonicals = [r["canonical_name"] for r in out["results"]]
    # Both should be there; orch owns neither.
    assert "auth-svc-A" in canonicals
    assert "auth-svc-B" in canonicals


def test_phase10_7_filters_per_table_components(agent_factory):
    """component_type filter: A is application, B is database."""
    comp_a, comp_b = _setup_two_smes(agent_factory)
    agent_factory("orch-vs2", "orchestrator")
    out = search.vector_search(
        "orch-vs2", "auth", "components", 10,
        filters={"component_type": "application"},
    )
    if not out["query_embedded"]:
        pytest.skip("Ollama unreachable")
    canonicals = [r["canonical_name"] for r in out["results"]]
    assert "auth-svc-A" in canonicals
    assert "auth-svc-B" not in canonicals  # filtered out by component_type


def test_phase10_7_filters_or_within_key(agent_factory):
    """component_type=[application, database] returns both."""
    comp_a, comp_b = _setup_two_smes(agent_factory)
    agent_factory("orch-vs3", "orchestrator")
    out = search.vector_search(
        "orch-vs3", "auth", "components", 10,
        filters={"component_type": ["application", "database"]},
    )
    if not out["query_embedded"]:
        pytest.skip("Ollama unreachable")
    canonicals = [r["canonical_name"] for r in out["results"]]
    assert "auth-svc-A" in canonicals
    assert "auth-svc-B" in canonicals


def test_phase10_7_filter_invalid_key_for_table_raises(agent_factory):
    agent_factory("orch-vs4", "orchestrator")
    with pytest.raises(ValueError, match="not allowed for table 'components'"):
        search.vector_search(
            "orch-vs4", "auth", "components", 10,
            filters={"plane": "github"},  # plane not allowed on components
        )


def test_phase10_7_empty_filter_dict_is_noop(agent_factory):
    """filters={} should not error — equivalent to no filters."""
    _setup_two_smes(agent_factory)
    agent_factory("orch-vs5", "orchestrator")
    out = search.vector_search(
        "orch-vs5", "auth", "components", 10, filters={},
    )
    if not out["query_embedded"]:
        pytest.skip("Ollama unreachable")
    assert out["query_embedded"] is True


def test_phase10_7_filters_attributions_plane(agent_factory):
    """Filter attributions by plane — supported via direct column."""
    _setup_two_smes(agent_factory)
    # Seed an attribution
    from cartograph_mcp.tools import components as ct
    comp = ct.get_component("sme-A-vs",
        ct.upsert_component(
            "sme-A-vs",
            {"canonical_name": "auth-svc-A", "display_name": "Auth A",
             "component_type": "application"},
        )["id"]
    )
    ct.upsert_attribution("sme-A-vs", str(comp["id"]), {
        "plane": "github", "resource_type": "endpoint",
        "identifier": "POST /auth/login", "evidence": "test",
    })
    agent_factory("orch-vs6", "orchestrator")
    out = search.vector_search(
        "orch-vs6", "login", "attributions", 10,
        filters={"plane": "github"},
    )
    if not out["query_embedded"]:
        pytest.skip("Ollama unreachable")
    # All results should be plane='github'
    for r in out["results"]:
        assert r["plane"] == "github"

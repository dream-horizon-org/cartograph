"""Phase 3: component_doc_md persists through upsert_component create + update."""

from shared.db import execute_mutate
from cartograph_mcp.tools import components, resources


def _iter(agent_factory, aid: str, plane: str):
    execute_mutate(
        """INSERT INTO agent_runs (agent_id, agent_type, plane, status)
           VALUES (%s, 'iterator', %s, 'idle')""",
        (aid, plane),
    )
    return aid


def _sme(agent_factory, sid: str, resource_id: str):
    agent_factory(sid, "sme")
    execute_mutate(
        """INSERT INTO resource_component_agents (resource_id, component_id, agent_id)
           VALUES (%s, NULL, %s)""",
        (resource_id, sid),
    )


def test_component_doc_md_set_on_create(agent_factory):
    _iter(agent_factory, "i", "github")
    r = resources.upsert_resource("i", "github", "repo", "o/r")
    _sme(agent_factory, "s", r["id"])
    c = components.upsert_component("s", {
        "canonical_name": "o/r", "display_name": "R",
        "component_type": "application",
        "component_doc_md": "# R\nDoes X.",
    })
    assert c["component_doc_md"] == "# R\nDoes X."


def test_component_doc_md_updated_when_provided(agent_factory):
    _iter(agent_factory, "i", "github")
    r = resources.upsert_resource("i", "github", "repo", "o/r")
    _sme(agent_factory, "s", r["id"])
    c1 = components.upsert_component("s", {
        "canonical_name": "o/r", "display_name": "R",
        "component_type": "application",
        "component_doc_md": "v1",
    })
    c2 = components.upsert_component("s", {
        "canonical_name": "o/r", "display_name": "R",
        "component_type": "application",
        "component_doc_md": "v2",
    })
    assert c1["id"] == c2["id"]
    assert c2["component_doc_md"] == "v2"


def test_component_doc_md_preserved_when_omitted(agent_factory):
    """COALESCE: omitting the key on a later update must not wipe the doc."""
    _iter(agent_factory, "i", "github")
    r = resources.upsert_resource("i", "github", "repo", "o/r")
    _sme(agent_factory, "s", r["id"])
    components.upsert_component("s", {
        "canonical_name": "o/r", "display_name": "R",
        "component_type": "application",
        "component_doc_md": "keep me",
    })
    updated = components.upsert_component("s", {
        "canonical_name": "o/r", "display_name": "R prime",
        "component_type": "application",
        # component_doc_md intentionally omitted
    })
    assert updated["component_doc_md"] == "keep me"


def test_component_doc_md_nullable_on_create(agent_factory):
    _iter(agent_factory, "i", "github")
    r = resources.upsert_resource("i", "github", "repo", "o/r")
    _sme(agent_factory, "s", r["id"])
    c = components.upsert_component("s", {
        "canonical_name": "o/r", "display_name": "R",
        "component_type": "application",
    })
    assert c["component_doc_md"] is None


# ---------- Phase 10.7: `description` is the embed-target; doc_md is human-only ----------
# Note: Phase 10.2 added doc_md to the embed text. Phase 10.7 reverted that
# in favour of a separate `description` column so prose length doesn't
# dilute the identity signal. The pre-Phase-10.7 tests below were rewritten
# to reflect the new shape.


def test_phase10_7_embed_text_uses_description_not_doc_md():
    """component_embed_text new signature: takes description, not doc_md."""
    from shared import embedding as emb
    text = emb.component_embed_text(
        "fav2-api", "FAV2 API", "application", {"runtime": "java"},
        "Dense terse summary of auth service",
    )
    assert "Dense terse summary" in text
    assert "fav2-api" in text
    # doc_md content (if any) MUST NOT leak in — the param is description now.
    # No way to pass doc_md to component_embed_text anymore.


def test_phase10_7_embed_text_description_none_safe():
    """Description=None → empty string in embed (no crash)."""
    from shared import embedding as emb
    text = emb.component_embed_text(
        "fav2-api", "FAV2 API", "application", {"k": "v"}, None,
    )
    assert "fav2-api" in text
    assert "FAV2 API" in text
    # No "None" literal in the embed text.
    assert "None" not in text


def test_phase10_7_description_persists_through_create_and_update(agent_factory):
    """description set on create, replaced on update with new value,
    preserved on update with key omitted."""
    _iter(agent_factory, "i", "github")
    r = resources.upsert_resource("i", "github", "repo", "o/r")
    _sme(agent_factory, "s", r["id"])
    c1 = components.upsert_component("s", {
        "canonical_name": "o/r", "display_name": "R",
        "component_type": "application",
        "description": "v1 dense summary",
    })
    assert c1["description"] == "v1 dense summary"
    # Update with new description → REPLACE
    c2 = components.upsert_component("s", {
        "canonical_name": "o/r", "display_name": "R",
        "component_type": "application",
        "description": "v2 dense summary",
    })
    assert c2["description"] == "v2 dense summary"
    # Update without description → COALESCE-preserve
    c3 = components.upsert_component("s", {
        "canonical_name": "o/r", "display_name": "R prime",
        "component_type": "application",
    })
    assert c3["description"] == "v2 dense summary"


def test_phase10_7_description_default_empty_on_create_without_field(agent_factory):
    """Component created without description → DB stores '' (NOT NULL DEFAULT)."""
    _iter(agent_factory, "i", "github")
    r = resources.upsert_resource("i", "github", "repo", "o/r")
    _sme(agent_factory, "s", r["id"])
    c = components.upsert_component("s", {
        "canonical_name": "o/r", "display_name": "R",
        "component_type": "application",
    })
    assert c["description"] == ""


def test_phase10_7_description_explicit_empty_clears_on_update(agent_factory):
    """description='' explicitly → COALESCE('', existing) → '' (clears).
    Distinguishes from description=None (preserve) and omit (preserve)."""
    _iter(agent_factory, "i", "github")
    r = resources.upsert_resource("i", "github", "repo", "o/r")
    _sme(agent_factory, "s", r["id"])
    components.upsert_component("s", {
        "canonical_name": "o/r", "display_name": "R",
        "component_type": "application",
        "description": "to be cleared",
    })
    cleared = components.upsert_component("s", {
        "canonical_name": "o/r", "display_name": "R",
        "component_type": "application",
        "description": "",
    })
    assert cleared["description"] == ""


def test_phase10_7_description_soft_400_warn(agent_factory, caplog):
    """description >400 chars logs a warning but does NOT reject."""
    import logging
    _iter(agent_factory, "i", "github")
    r = resources.upsert_resource("i", "github", "repo", "o/r")
    _sme(agent_factory, "s", r["id"])
    long_desc = "X" * 500
    with caplog.at_level(logging.WARNING):
        c = components.upsert_component("s", {
            "canonical_name": "o/r", "display_name": "R",
            "component_type": "application",
            "description": long_desc,
        })
    # No reject — row created.
    assert c["description"] == long_desc
    # Warning logged — soft cap.
    assert any("soft cap 400" in rec.message for rec in caplog.records)


def test_phase10_7_get_component_strips_embedding(agent_factory):
    """Phase 10.7 bonus: get_component must NOT return the 1024-d
    embedding vector. Pre-fix used SELECT * which leaked ~8KB per call.
    """
    _iter(agent_factory, "i", "github")
    r = resources.upsert_resource("i", "github", "repo", "o/r")
    _sme(agent_factory, "s", r["id"])
    c = components.upsert_component("s", {
        "canonical_name": "o/r", "display_name": "R",
        "component_type": "application",
        "description": "test",
    })
    fetched = components.get_component("s", c["id"])
    assert "embedding" not in fetched, \
        "get_component must not return the embedding vector (Phase 10.7)"
    # But should return the new description column
    assert fetched["description"] == "test"


def test_phase10_7_get_components_bulk_strips_embedding(agent_factory):
    """Same strip on the bulk variant."""
    _iter(agent_factory, "i", "github")
    r = resources.upsert_resource("i", "github", "repo", "o/r")
    _sme(agent_factory, "s", r["id"])
    c = components.upsert_component("s", {
        "canonical_name": "o/r", "display_name": "R",
        "component_type": "application",
    })
    cid = str(c["id"])
    out = components.get_components_bulk("s", [cid])
    fetched = out[cid]
    assert fetched is not None
    assert "embedding" not in fetched


def test_phase10_7_doc_md_no_longer_affects_embed_recall(agent_factory):
    """Pre-10.7, doc_md content drove vector_search recall. Post-10.7,
    doc_md changes do NOT change the embedding (only description does).
    This test confirms doc_md updates don't trigger re-embed signal change.
    """
    from shared.db import execute_one
    _iter(agent_factory, "i", "github")
    r = resources.upsert_resource("i", "github", "repo", "o/r")
    _sme(agent_factory, "s", r["id"])
    components.upsert_component("s", {
        "canonical_name": "o/r", "display_name": "R",
        "component_type": "application",
        "description": "stable description",
        "component_doc_md": "v1 doc",
    })
    embed_v1 = execute_one(
        "SELECT embedding::text AS e FROM components WHERE canonical_name='o/r'",
    )["e"]
    components.upsert_component("s", {
        "canonical_name": "o/r", "display_name": "R",
        "component_type": "application",
        "description": "stable description",  # SAME description
        "component_doc_md": "wildly different doc content " * 20,  # CHANGED
    })
    embed_v2 = execute_one(
        "SELECT embedding::text AS e FROM components WHERE canonical_name='o/r'",
    )["e"]
    # Same description → embedding text input is identical → embedding
    # output should be byte-identical (deterministic embed model).
    assert embed_v1 == embed_v2, "doc_md change should NOT affect embedding"

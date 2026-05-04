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


# ---------- Phase 10.2: doc_md reaches embedding ----------

def test_phase10_2_embed_text_includes_doc_md_capped():
    """Pure unit test on the embed-text builder. doc_md included up to 500 chars."""
    from shared import embedding as emb
    short_text = emb.component_embed_text(
        "fav2-api", "FAV2 API", "application", {"runtime": "java"},
        "Auth service handling /verify and /token endpoints",
    )
    assert "Auth service handling" in short_text
    assert "fav2-api" in short_text

    # Cap test: 1500-char doc_md → only first 500 chars in embed string.
    big_doc = "X" * 1500
    big_text = emb.component_embed_text(
        "n", "n", "application", None, big_doc,
    )
    assert big_text.count("X") == 500


def test_phase10_2_doc_md_none_omits_from_embed():
    """When doc_md isn't provided, embed text falls back to old shape."""
    from shared import embedding as emb
    text = emb.component_embed_text(
        "fav2-api", "FAV2 API", "application", {"k": "v"}, None,
    )
    # Stable shape: type: name name doc meta — doc placeholder is empty string.
    assert "fav2-api" in text
    assert "FAV2 API" in text


def test_phase10_2_update_without_doc_md_preserves_existing_in_embed(agent_factory):
    """COALESCE on update preserves doc_md in DB; embedding must also be
    rebuilt from the preserved doc_md, not from None.
    """
    _iter(agent_factory, "i", "github")
    r = resources.upsert_resource("i", "github", "repo", "o/r")
    _sme(agent_factory, "s", r["id"])
    components.upsert_component("s", {
        "canonical_name": "o/r", "display_name": "R",
        "component_type": "application",
        "component_doc_md": "auth service does verify",
    })
    # Update without re-supplying doc_md.
    updated = components.upsert_component("s", {
        "canonical_name": "o/r", "display_name": "R v2",
        "component_type": "application",
    })
    # doc_md preserved.
    assert updated["component_doc_md"] == "auth service does verify"
    # Embedding column not None (got rebuilt with the preserved doc_md content).
    from shared.db import execute_one
    row = execute_one(
        "SELECT embedding IS NOT NULL AS has_embed FROM components WHERE id = %s",
        (updated["id"],),
    )
    assert row["has_embed"] is True

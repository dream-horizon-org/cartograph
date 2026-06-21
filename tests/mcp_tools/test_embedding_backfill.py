"""Phase 3.7 follow-up: embedding_backfill re-embeds NULL rows idempotently.

Covers:
- Rows with embedding=NULL get populated.
- Already-embedded rows are skipped (idempotent).
- If Ollama is unreachable, `skipped` increments rather than raising.
- End-to-end (requires live Ollama; @skipif otherwise).
"""

import pytest
import urllib.request

from shared.db import execute_mutate, execute_returning, execute_one
from shared.embedding_backfill import backfill_all


def _ollama_reachable() -> bool:
    try:
        urllib.request.urlopen("http://localhost:11434/api/version", timeout=1).read()
        return True
    except Exception:
        return False


@pytest.mark.skipif(
    not _ollama_reachable(),
    reason="local Ollama not reachable — skipping end-to-end backfill",
)
def test_backfills_null_embeddings_across_tables():
    # Seed one row in each embedding table with embedding=NULL.
    comp = execute_returning(
        """INSERT INTO components (canonical_name, display_name, component_type)
           VALUES ('o/backfill-c', 'Backfill C', 'application')
           RETURNING *"""
    )
    attr = execute_returning(
        """INSERT INTO attributions (component_id, plane, resource_type, identifier)
           VALUES (%s, 'github', 'repo', 'o/backfill-c')
           RETURNING *""",
        (comp["id"],),
    )
    unresolved = execute_returning(
        """INSERT INTO unresolved (found_in_component_id, reference_type, reference_value)
           VALUES (%s, 'hostname', 'feeds.local')
           RETURNING *""",
        (comp["id"],),
    )

    result = backfill_all()
    assert result["components"]["done"] >= 1
    assert result["attributions"]["done"] >= 1
    assert result["unresolved"]["done"] >= 1

    for table, row_id in [
        ("components", comp["id"]),
        ("attributions", attr["id"]),
        ("unresolved", unresolved["id"]),
    ]:
        r = execute_one(
            f"SELECT embedding IS NOT NULL AS has_emb FROM {table} WHERE id = %s",
            (row_id,),
        )
        assert r["has_emb"] is True, f"{table} row should have embedding after backfill"


@pytest.mark.skipif(
    not _ollama_reachable(),
    reason="local Ollama not reachable — skipping end-to-end backfill",
)
def test_backfill_is_idempotent():
    """Running a second time on already-populated tables should be a no-op."""
    execute_returning(
        """INSERT INTO components (canonical_name, display_name, component_type)
           VALUES ('o/idempotent', 'Idempotent', 'application')
           RETURNING *"""
    )
    first = backfill_all()
    second = backfill_all()
    # First pass populates; second pass finds no NULLs.
    assert first["totals"]["done"] >= 1
    assert second["totals"]["done"] == 0


def test_backfill_skips_when_embed_unreachable(monkeypatch):
    """Graceful degrade: unreachable embedder → skipped count, no raise."""
    from shared import embedding as emb_mod
    monkeypatch.setattr(emb_mod, "embed_text", lambda *a, **k: None)
    execute_returning(
        """INSERT INTO components (canonical_name, display_name, component_type)
           VALUES ('o/degrade', 'Degrade', 'application')
           RETURNING *"""
    )
    result = backfill_all()
    assert result["components"]["done"] == 0
    assert result["components"]["skipped"] >= 1

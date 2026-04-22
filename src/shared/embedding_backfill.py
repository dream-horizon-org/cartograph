"""Re-embed existing rows whose `embedding` column is NULL.

Use cases:
- Phase 3.7 migration: every row existed before the Ollama path landed,
  so all embeddings were NULL. Running this once populates them.
- Dim/model change: if EMBEDDING_MODEL or EMBEDDING_DIMS flips, the
  schema migration NULLs out every embedding. Running this re-embeds.

Run directly:
    python -m shared.embedding_backfill

Or import and call from a script / admin tool:
    from shared.embedding_backfill import backfill_all
    summary = backfill_all()

Contract:
- Reads rows with `embedding IS NULL`, builds the embed text using the
  same helpers as the write path, calls `shared.embedding.embed_text`,
  writes back.
- Skipped rows (embedding ended up None — Ollama down, empty text)
  stay NULL; they can be retried later.
- One-row-at-a-time to stay honest to the current embed API; batching
  is a future optimisation if scale warrants it.
"""

from __future__ import annotations

import json
import logging
from typing import Callable

from shared import embedding as emb
from shared.db import execute, execute_mutate


log = logging.getLogger(__name__)


def _backfill_components() -> tuple[int, int]:
    rows = execute(
        """SELECT id, canonical_name, display_name, component_type, metadata
           FROM components
           WHERE embedding IS NULL"""
    )
    done = skipped = 0
    for r in rows:
        text = emb.component_embed_text(
            r["canonical_name"], r["display_name"],
            r["component_type"], r["metadata"],
        )
        vec = emb.embed_text(text)
        lit = emb.vector_literal(vec)
        if lit is None:
            skipped += 1
            continue
        execute_mutate(
            "UPDATE components SET embedding = %s::vector WHERE id = %s",
            (lit, r["id"]),
        )
        done += 1
    return done, skipped


def _backfill_attributions() -> tuple[int, int]:
    rows = execute(
        """SELECT id, resource_type, identifier
           FROM attributions
           WHERE embedding IS NULL"""
    )
    done = skipped = 0
    for r in rows:
        text = emb.attribution_embed_text(r["resource_type"], r["identifier"])
        vec = emb.embed_text(text)
        lit = emb.vector_literal(vec)
        if lit is None:
            skipped += 1
            continue
        execute_mutate(
            "UPDATE attributions SET embedding = %s::vector WHERE id = %s",
            (lit, r["id"]),
        )
        done += 1
    return done, skipped


def _backfill_edges() -> tuple[int, int]:
    rows = execute(
        """SELECT id, edge_type, identifier FROM edges WHERE embedding IS NULL"""
    )
    done = skipped = 0
    for r in rows:
        text = emb.edge_embed_text(r["edge_type"], r["identifier"])
        vec = emb.embed_text(text)
        lit = emb.vector_literal(vec)
        if lit is None:
            skipped += 1
            continue
        execute_mutate(
            "UPDATE edges SET embedding = %s::vector WHERE id = %s",
            (lit, r["id"]),
        )
        done += 1
    return done, skipped


def _backfill_unresolved() -> tuple[int, int]:
    rows = execute(
        """SELECT id, reference_type, reference_value
           FROM unresolved
           WHERE embedding IS NULL"""
    )
    done = skipped = 0
    for r in rows:
        text = emb.unresolved_embed_text(r["reference_type"], r["reference_value"])
        vec = emb.embed_text(text)
        lit = emb.vector_literal(vec)
        if lit is None:
            skipped += 1
            continue
        execute_mutate(
            "UPDATE unresolved SET embedding = %s::vector WHERE id = %s",
            (lit, r["id"]),
        )
        done += 1
    return done, skipped


_BACKFILLERS: list[tuple[str, Callable[[], tuple[int, int]]]] = [
    ("components",   _backfill_components),
    ("attributions", _backfill_attributions),
    ("edges",        _backfill_edges),
    ("unresolved",   _backfill_unresolved),
]


def backfill_all() -> dict:
    """Re-embed all NULL-embedding rows across the four vector tables.

    Returns:
        {
          table_name: {"done": N, "skipped": M},
          ...,
          "totals": {"done": ..., "skipped": ...}
        }
    """
    out: dict = {}
    totals = {"done": 0, "skipped": 0}
    for name, fn in _BACKFILLERS:
        done, skipped = fn()
        out[name] = {"done": done, "skipped": skipped}
        totals["done"] += done
        totals["skipped"] += skipped
        log.info("Backfill %s: done=%d skipped=%d", name, done, skipped)
    out["totals"] = totals
    return out


def main() -> None:
    import sys
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        stream=sys.stdout,
    )
    from shared.db import init_pool, close_pool
    init_pool()
    try:
        # Best-effort warmup so the first embed isn't cold-start-slow.
        emb.warmup()
        result = backfill_all()
        print(json.dumps(result, indent=2))
    finally:
        close_pool()


if __name__ == "__main__":
    main()

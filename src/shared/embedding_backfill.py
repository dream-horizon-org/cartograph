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


def _backfill_components(force: bool = False) -> tuple[int, int]:
    """Re-embed components.

    Default (force=False): only rows with embedding IS NULL — the
    standard "Ollama was down at write time" backfill case.
    force=True: re-embed every row regardless. Used after embed-text
    shape changes:
      - Phase 10.2 (now superseded): added component_doc_md.
      - Phase 10.7: separated description (embed-target) from doc_md
        (human-render). MANDATORY post-10.7 to seed description from
        existing doc_md AND re-embed all rows. Without this, vectors
        are mixed-vintage (some doc-based, some description-based)
        and vector_search ranking is inconsistent.
    """
    if force:
        # Phase 10.7 seed: backfill empty description from LEFT(doc_md, 400)
        # for any row where doc_md has content but description hasn't been
        # set yet. Idempotent — second run is a no-op since description
        # is no longer empty after the first.
        seeded = execute_mutate(
            """UPDATE components
               SET description = LEFT(component_doc_md, 400)
               WHERE description = ''
                 AND component_doc_md IS NOT NULL
                 AND component_doc_md <> ''""",
        )
        log.info("Backfill components: seeded description for %d rows from doc_md",
                 seeded)

    where = "" if force else " WHERE embedding IS NULL"
    rows = execute(
        f"""SELECT id, canonical_name, display_name, component_type,
                   metadata, description
           FROM components{where}"""
    )
    done = skipped = 0
    for r in rows:
        text = emb.component_embed_text(
            r["canonical_name"], r["display_name"],
            r["component_type"], r["metadata"],
            r["description"],
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


def _backfill_catalogs() -> tuple[int, int]:
    rows = execute(
        """SELECT id, kind, identifier FROM catalogs WHERE embedding IS NULL"""
    )
    done = skipped = 0
    for r in rows:
        text = f"{r['kind']}: {r['identifier']}"
        vec = emb.embed_text(text)
        lit = emb.vector_literal(vec)
        if lit is None:
            skipped += 1
            continue
        execute_mutate(
            "UPDATE catalogs SET embedding = %s::vector WHERE id = %s",
            (lit, r["id"]),
        )
        done += 1
    return done, skipped


_BACKFILLERS: list[tuple[str, Callable[..., tuple[int, int]]]] = [
    ("components",   _backfill_components),
    ("attributions", _backfill_attributions),
    ("edges",        _backfill_edges),
    ("unresolved",   _backfill_unresolved),
    ("catalogs",     _backfill_catalogs),
]


def backfill_all(force_components: bool = False) -> dict:
    """Re-embed NULL-embedding rows across all vector tables.

    `force_components=True` re-embeds EVERY component row regardless of
    embedding state — used after Phase 10.2 changed `component_embed_text`
    to include `component_doc_md`. Other tables stay NULL-only since
    their embed-text shape is unchanged.

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
        if name == "components":
            done, skipped = fn(force=force_components)
        else:
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
    # CLI: `python -m shared.embedding_backfill` (NULL-only)
    #   or `python -m shared.embedding_backfill --force-components`
    #     (re-embed every component row; use after Phase 10.2 ships).
    force_components = "--force-components" in sys.argv
    from shared.db import init_pool, close_pool
    init_pool()
    try:
        # Best-effort warmup so the first embed isn't cold-start-slow.
        emb.warmup()
        result = backfill_all(force_components=force_components)
        print(json.dumps(result, indent=2))
    finally:
        close_pool()


if __name__ == "__main__":
    main()

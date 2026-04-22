"""Embedding client for Cartograph.

Writes vector(1536) embeddings at the same moment we write the row, via
OpenAI's `text-embedding-3-small` model. No batch pipeline.

Design notes:
- Graceful degrade when `OPENAI_API_KEY` is unset: the helper returns `None`
  and callers write the row with `embedding=NULL`. vector_search on such
  rows won't match anything, but writes don't fail.
- Uses stdlib `urllib` (no extra dependency) — one POST per embed call.
- Caller is responsible for building the embed text; this module handles
  transport + model config only.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Optional

from shared import config

log = logging.getLogger(__name__)

_OPENAI_URL = "https://api.openai.com/v1/embeddings"


def embed_text(text: str) -> Optional[list[float]]:
    """Return a 1536-d float list, or None on missing key / API failure.

    Callers must tolerate None and write `embedding=NULL`. They should NOT
    raise or block — embeddings are a lookup accelerator, not a durability
    guarantee.
    """
    if not text or not text.strip():
        return None
    if not config.OPENAI_API_KEY:
        log.debug("embed_text: OPENAI_API_KEY unset, skipping")
        return None

    body = json.dumps({"input": text, "model": config.EMBEDDING_MODEL}).encode()
    req = urllib.request.Request(
        _OPENAI_URL,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config.OPENAI_API_KEY}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            resp = json.loads(r.read().decode())
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        log.warning("embed_text failed: %s", exc)
        return None

    try:
        vec = resp["data"][0]["embedding"]
    except (KeyError, IndexError, TypeError):
        log.warning("embed_text: unexpected response shape: %s", resp)
        return None
    if len(vec) != config.EMBEDDING_DIMS:
        log.warning("embed_text: got %d dims, expected %d", len(vec), config.EMBEDDING_DIMS)
        return None
    return vec


def vector_literal(vec: Optional[list[float]]) -> Optional[str]:
    """Format a Python list as a pgvector string literal (or None).

    pgvector accepts `[1.0, 2.0, ...]` as a text literal — we build that
    string explicitly so we can bind it as a TEXT parameter and cast to
    vector in SQL, avoiding any psycopg type-adapter dependency.
    """
    if vec is None:
        return None
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"


# Embed-text builders — keep in sync with SCHEMA.md §Embedding Strategy.

def component_embed_text(canonical_name: str, display_name: str,
                         component_type: str, metadata: dict | None) -> str:
    meta = json.dumps(metadata or {}, sort_keys=True)
    return f"{component_type}: {canonical_name} {display_name} {meta}"


def attribution_embed_text(resource_type: str, identifier: str) -> str:
    return f"{resource_type}: {identifier}"


def unresolved_embed_text(reference_type: str, reference_value: str) -> str:
    return f"{reference_type}: {reference_value}"


def edge_embed_text(edge_type: str, identifier: str) -> str:
    return f"{edge_type}: {identifier}"

"""Embedding client for Cartograph.

Writes vector(EMBEDDING_DIMS) embeddings at row-write time via a LOCAL
Ollama instance (default `mxbai-embed-large`, 1024d, Metal-accelerated
on Apple Silicon). No network egress, no API key, no rate limits. Warm
embed latency ~40-60ms on M-series.

Design notes:
- Graceful degrade when Ollama is unreachable or the model isn't pulled:
  the helper returns `None` and callers write the row with
  `embedding=NULL`. vector_search on such rows won't match anything,
  but writes don't fail.
- Uses stdlib `urllib` (no extra dependency) — one POST per embed call.
- Caller builds the embed text; this module handles transport + model
  config only.
- Ollama's `/api/embeddings` endpoint auto-loads the model on first call
  (~1s cold-start). Callers that care about latency should call
  `warmup()` at service boot.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Optional

from shared import config

log = logging.getLogger(__name__)

_EMBED_PATH = "/api/embeddings"


def embed_text(text: str) -> Optional[list[float]]:
    """Return a list of floats sized EMBEDDING_DIMS, or None on failure.

    Callers must tolerate None and write `embedding=NULL`. They should NOT
    raise or block — embeddings are a lookup accelerator, not a durability
    guarantee.
    """
    if not text or not text.strip():
        return None

    url = config.OLLAMA_URL.rstrip("/") + _EMBED_PATH
    body = json.dumps({"model": config.EMBEDDING_MODEL, "prompt": text}).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            resp = json.loads(r.read().decode())
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        log.warning("embed_text failed (Ollama @ %s): %s", config.OLLAMA_URL, exc)
        return None

    vec = resp.get("embedding")
    if not isinstance(vec, list):
        log.warning("embed_text: unexpected response shape: %s", resp)
        return None
    if len(vec) != config.EMBEDDING_DIMS:
        log.warning(
            "embed_text: got %d dims, expected %d — schema and model mismatch "
            "(check CARTOGRAPH_EMBEDDING_DIMS and CARTOGRAPH_EMBEDDING_MODEL)",
            len(vec),
            config.EMBEDDING_DIMS,
        )
        return None
    return vec


def warmup() -> bool:
    """Fire a throwaway embed so the model is loaded into GPU memory.

    Ollama lazy-loads the model on first /api/embeddings call (~1s on
    M-series). Calling this at MCP server boot means the first real
    write/search doesn't eat the cold-start latency.

    Returns True if the warm call succeeded, False otherwise. Non-fatal
    either way — the MCP server starts regardless.
    """
    vec = embed_text("warmup")
    ok = vec is not None
    if ok:
        log.info(
            "Embedding warmup OK: model=%s dims=%d", config.EMBEDDING_MODEL, len(vec)
        )
    else:
        log.warning(
            "Embedding warmup FAILED (Ollama unreachable or model not pulled). "
            "Writes will proceed with embedding=NULL and vector_search will "
            "return query_embedded=False. Install/start Ollama and "
            "`ollama pull %s` to enable.",
            config.EMBEDDING_MODEL,
        )
    return ok


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

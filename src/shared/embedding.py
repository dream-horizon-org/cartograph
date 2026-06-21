"""Embedding client for Cartograph.

Writes vector(EMBEDDING_DIMS) embeddings at row-write time. Provider is
selected via CARTOGRAPH_EMBEDDING_PROVIDER:

  ollama (default)  — local Ollama /api/embeddings (mxbai-embed-large)
  bedrock           — Amazon Bedrock Runtime (Titan Embed Text v2, etc.)

Design notes:
- Graceful degrade when the provider is unreachable: returns None and
  callers write embedding=NULL. vector_search skips those rows.
- Caller builds the embed text; this module handles transport + model
  config only.
- Call warmup() at MCP server boot to fail fast in logs.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any, Optional

from shared import config

log = logging.getLogger(__name__)

_EMBED_PATH = "/api/embeddings"
_bedrock_client: Any = None


def embed_text(text: str, *, input_type: str | None = None) -> Optional[list[float]]:
    """Return a list of floats sized EMBEDDING_DIMS, or None on failure.

    `input_type` is used only for Cohere embedding models on Bedrock
    (search_query vs search_document). Titan models ignore it.
    """
    if not text or not text.strip():
        return None

    provider = config.EMBEDDING_PROVIDER
    if provider == "bedrock":
        vec = _embed_bedrock(text, input_type=input_type or "search_document")
    elif provider == "ollama":
        vec = _embed_ollama(text)
    else:
        log.warning("embed_text: unknown EMBEDDING_PROVIDER %r", provider)
        return None

    if vec is None:
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


def _embed_ollama(text: str) -> Optional[list[float]]:
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
        log.warning("embed_text (ollama): unexpected response shape: %s", resp)
        return None
    return vec


def _get_bedrock_client():
    global _bedrock_client
    if _bedrock_client is not None:
        return _bedrock_client
    try:
        import boto3
    except ImportError:
        log.warning("boto3 not installed — Bedrock embeddings unavailable")
        return None
    _bedrock_client = boto3.client(
        "bedrock-runtime",
        region_name=config.AWS_REGION,
    )
    return _bedrock_client


def _bedrock_request_body(text: str, *, input_type: str) -> str:
    model = config.EMBEDDING_MODEL
    if model.startswith("cohere."):
        return json.dumps({
            "texts": [text],
            "input_type": input_type,
            "embedding_types": ["float"],
        })
    if "titan-embed-text-v2" in model:
        return json.dumps({
            "inputText": text,
            "dimensions": config.EMBEDDING_DIMS,
            "normalize": True,
        })
    # Titan v1 and other Titan text embed models
    return json.dumps({"inputText": text})


def _parse_bedrock_embedding(payload: dict) -> Optional[list[float]]:
    model = config.EMBEDDING_MODEL
    if model.startswith("cohere."):
        embeddings = payload.get("embeddings")
        if not isinstance(embeddings, list) or not embeddings:
            return None
        first = embeddings[0]
        if isinstance(first, dict):
            vec = first.get("embedding")
            if isinstance(vec, list):
                return vec
        return None
    vec = payload.get("embedding")
    if isinstance(vec, list):
        return vec
    return None


def _embed_bedrock(text: str, *, input_type: str) -> Optional[list[float]]:
    client = _get_bedrock_client()
    if client is None:
        return None
    body = _bedrock_request_body(text, input_type=input_type)
    try:
        response = client.invoke_model(
            modelId=config.EMBEDDING_MODEL,
            body=body,
            contentType="application/json",
            accept="application/json",
        )
        raw = response["body"].read()
        payload = json.loads(raw)
    except Exception as exc:
        log.warning(
            "embed_text failed (Bedrock %s @ %s): %s",
            config.EMBEDDING_MODEL,
            config.AWS_REGION,
            exc,
        )
        return None

    vec = _parse_bedrock_embedding(payload)
    if vec is None:
        log.warning("embed_text (bedrock): unexpected response shape: %s", payload)
    return vec


def warmup() -> bool:
    """Fire a throwaway embed so the provider is reachable at boot."""
    vec = embed_text("warmup", input_type="search_query")
    ok = vec is not None
    if ok:
        log.info(
            "Embedding warmup OK: provider=%s model=%s dims=%d region=%s",
            config.EMBEDDING_PROVIDER,
            config.EMBEDDING_MODEL,
            len(vec),
            config.AWS_REGION if config.EMBEDDING_PROVIDER == "bedrock" else "n/a",
        )
    else:
        if config.EMBEDDING_PROVIDER == "bedrock":
            log.warning(
                "Embedding warmup FAILED (Bedrock unreachable or model not enabled). "
                "Writes will proceed with embedding=NULL and vector_search will "
                "return query_embedded=False. Check AWS credentials, %s model "
                "access in region %s, and CARTOGRAPH_EMBEDDING_MODEL.",
                config.EMBEDDING_MODEL,
                config.AWS_REGION,
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
    """Format a Python list as a pgvector string literal (or None)."""
    if vec is None:
        return None
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"


# Embed-text builders — keep in sync with SCHEMA.md §Embedding Strategy.

def component_embed_text(canonical_name: str, display_name: str,
                         component_type: str, metadata: dict | None,
                         description: str | None = None) -> str:
    """Build the embed text for a component row."""
    meta = json.dumps(metadata or {}, sort_keys=True)
    desc = (description or "").strip()
    return f"{component_type}: {canonical_name} {display_name} {desc} {meta}"


def attribution_embed_text(resource_type: str, identifier: str) -> str:
    return f"{resource_type}: {identifier}"


def unresolved_embed_text(reference_type: str, reference_value: str) -> str:
    return f"{reference_type}: {reference_value}"


def edge_embed_text(edge_type: str, identifier: str) -> str:
    return f"{edge_type}: {identifier}"

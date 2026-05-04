"""Contextual description synthesis and embedding for Cartograph V2."""

from __future__ import annotations

import functools

from openai import OpenAI

from agent_management.config import get_config

# Attribute types in priority order — most discriminating first.
# Fields earlier in this list carry more semantic weight in the embedding.
_FIELD_PRIORITY = [
    "entry_point",
    "deploy_config",
    "hostname",
    "runtime",
    "region",
    "endpoint",
    "config_key",
    "repo",
    "asg",
    "load_balancer",
    "k8s_workload",
    "lambda",
    "rds",
    "datadog_service",
]


@functools.lru_cache(maxsize=1)
def _get_openai_client() -> OpenAI:
    cfg = get_config()
    return OpenAI(api_key=cfg.openai_api_key)


def synthesize_description(
    component: dict,
    attributions: list[dict],
) -> str:
    """
    Build a natural-language paragraph describing what the component IS —
    not just what it is called. Richer descriptions produce better vector
    similarity across planes with different naming conventions.
    """
    parts = [
        f"{component['component_type']} named {component['canonical_name']}"
    ]

    # Group attributions by resource_type
    by_type: dict[str, list[str]] = {}
    for attr in attributions:
        try:
            by_type.setdefault(attr["resource_type"], []).append(attr["identifier"])
        except KeyError as exc:
            raise ValueError(
                f"Attribution missing required key {exc}: {attr!r}"
            ) from exc

    # Emit fields in priority order
    for field in _FIELD_PRIORITY:
        if field in by_type:
            values = ", ".join(by_type[field])
            parts.append(f"{field}: {values}")

    # Emit any remaining fields not in the priority list
    for field, values in by_type.items():
        if field not in _FIELD_PRIORITY:
            parts.append(f"{field}: {', '.join(values)}")

    return ". ".join(parts)


def embed_text(text: str) -> list[float]:
    """Embed a string using the configured OpenAI embedding model."""
    cfg = get_config()
    client = _get_openai_client()
    response = client.embeddings.create(
        input=text,
        model=cfg.embedding_model,
    )
    return response.data[0].embedding


def embed_component(component: dict, attributions: list[dict]) -> list[float]:
    """Synthesize description and embed it. Use after every attribution write."""
    description = synthesize_description(component, attributions)
    return embed_text(description)

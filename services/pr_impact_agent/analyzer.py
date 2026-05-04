"""Phase 1: given a GitHub URL, find all transitively-downstream components.

Maps repo URL → root component(s) via attributions on the github plane,
walks outbound edges (full transitive closure), and returns each reached
component with its outbound edges so the caller can see per-service infra
clusters (e.g. service-b reads_from DATABASE_URL).
"""

from __future__ import annotations

import re
from typing import Any

from .db import cursor
from .models import ComponentNode, OutboundEdge


_GH_URL = re.compile(
    r"(?:https?://github\.com/|git@github\.com:)"
    r"(?P<owner>[^/]+)/"
    r"(?P<repo>[^/.\s]+?)"
    r"(?:\.git)?"
    r"(?:/(?:pull|tree|blob|commit)/.*)?"
    r"/?$"
)


def parse_github_url(url: str) -> tuple[str, str]:
    """Extract (owner, repo) from any github URL form. Raises ValueError."""
    m = _GH_URL.search(url.strip())
    if not m:
        raise ValueError(f"not a recognised github URL: {url!r}")
    return m.group("owner"), m.group("repo")


def _candidate_identifiers(owner: str, repo: str) -> list[str]:
    slug = f"{owner}/{repo}"
    base_url = f"https://github.com/{slug}"
    return [
        slug,
        f"{slug}.git",
        base_url,
        f"{base_url}.git",
        f"git@github.com:{slug}.git",
    ]


def _find_root_components(owner: str, repo: str) -> list[str]:
    """Look up component_ids for a repo, matching across all 'repo*'
    resource_type variants the iterators have used (repo, repo_url,
    repo_name, repo_slug)."""
    candidates = [c.lower() for c in _candidate_identifiers(owner, repo)]
    with cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT component_id
            FROM attributions
            WHERE plane = 'github'
              AND resource_type LIKE 'repo%%'
              AND LOWER(identifier) = ANY(%s::text[])
            """,
            (candidates,),
        )
        return [str(r["component_id"]) for r in cur.fetchall()]


def _transitive_downstream(root_ids: list[str]) -> dict[str, int]:
    """BFS via bound outbound edges. Returns {component_id: min_depth}.
    Excludes roots themselves (depth>0)."""
    if not root_ids:
        return {}
    with cursor() as cur:
        cur.execute(
            """
            WITH RECURSIVE walk(id, depth, path) AS (
              SELECT id, 0, ARRAY[id]
              FROM components
              WHERE id = ANY(%s::uuid[])

              UNION ALL

              SELECT c.id, w.depth + 1, w.path || c.id
              FROM walk w
              JOIN edges e
                ON e.from_component_id = w.id
               AND e.to_component_id IS NOT NULL
              JOIN components c
                ON c.id = e.to_component_id
              WHERE NOT (c.id = ANY(w.path))
            )
            SELECT id::text AS id, MIN(depth) AS depth
            FROM walk
            WHERE depth > 0
            GROUP BY id
            """,
            (root_ids,),
        )
        return {r["id"]: r["depth"] for r in cur.fetchall()}


def _fetch_components(ids: list[str]) -> dict[str, dict[str, Any]]:
    if not ids:
        return {}
    with cursor() as cur:
        cur.execute(
            """
            SELECT id::text AS id, canonical_name, display_name, component_type
            FROM components
            WHERE id = ANY(%s::uuid[])
            """,
            (ids,),
        )
        return {r["id"]: r for r in cur.fetchall()}


def _fetch_github_attributions(ids: list[str]) -> dict[str, dict[str, str]]:
    """component_id -> {resource_type: identifier} for plane='github'."""
    if not ids:
        return {}
    out: dict[str, dict[str, str]] = {cid: {} for cid in ids}
    with cursor() as cur:
        cur.execute(
            """
            SELECT component_id::text AS component_id, resource_type, identifier
            FROM attributions
            WHERE plane = 'github' AND component_id = ANY(%s::uuid[])
            """,
            (ids,),
        )
        for r in cur.fetchall():
            out[r["component_id"]][r["resource_type"]] = r["identifier"]
    return out


def _derive_github_url(attrs: dict[str, str]) -> str | None:
    """Turn a component's github attributions into a canonical repo URL.

    Prefers a full URL if present; otherwise builds one from a slug variant.
    Returns None if no repo* attribution exists."""
    for key in ("repo_url", "html_url", "url"):
        v = attrs.get(key)
        if v and v.lower().startswith(("http://", "https://")):
            return v.rstrip("/").removesuffix(".git")
    for key in ("repo", "repo_name", "repo_slug"):
        v = attrs.get(key)
        if v and "/" in v and not v.startswith(("http", "git@")):
            return f"https://github.com/{v.strip().removesuffix('.git')}"
    return None


def _fetch_outbound_edges(ids: list[str]) -> dict[str, list[dict[str, Any]]]:
    if not ids:
        return {}
    out: dict[str, list[dict[str, Any]]] = {cid: [] for cid in ids}
    with cursor() as cur:
        cur.execute(
            """
            SELECT
                e.from_component_id::text AS from_id,
                e.edge_type,
                e.identifier,
                e.to_component_id::text  AS to_id,
                c.canonical_name         AS to_name,
                c.component_type         AS to_type
            FROM edges e
            LEFT JOIN components c ON c.id = e.to_component_id
            WHERE e.from_component_id = ANY(%s::uuid[])
            ORDER BY e.from_component_id, e.edge_type, e.identifier
            """,
            (ids,),
        )
        for r in cur.fetchall():
            out[r["from_id"]].append(r)
    return out


def _build_node(
    cid: str,
    comps: dict[str, dict[str, Any]],
    gh_attrs: dict[str, dict[str, str]],
    edges: dict[str, list[dict[str, Any]]],
    depth: int,
    is_root: bool,
    warnings: list[str],
) -> ComponentNode:
    comp = comps[cid]
    attrs = gh_attrs.get(cid, {})
    gh_url = _derive_github_url(attrs)
    if gh_url is None:
        warnings.append(
            f"component {comp['canonical_name']!r} ({cid}) has no github "
            f"repo* attribution — github_url will be null"
        )

    out_edges: list[OutboundEdge] = []
    for e in edges.get(cid, []):
        to_id = e.get("to_id")
        to_attrs = gh_attrs.get(to_id, {}) if to_id else {}
        out_edges.append(
            OutboundEdge(
                edge_type=e["edge_type"],
                identifier=e["identifier"],
                to_component_id=to_id,
                to_canonical_name=e.get("to_name"),
                to_component_type=e.get("to_type"),
                to_github_url=_derive_github_url(to_attrs) if to_id else None,
                resolved=to_id is not None,
            )
        )

    # Strip the keys we used to derive github_url so the metadata blob
    # is just "everything else interesting" — port, framework, runtime, etc.
    metadata = {
        k: v
        for k, v in attrs.items()
        if k not in {"repo", "repo_url", "repo_name", "repo_slug",
                     "html_url", "url"}
    }

    return ComponentNode(
        id=cid,
        canonical_name=comp["canonical_name"],
        display_name=comp["display_name"],
        component_type=comp["component_type"],
        github_url=gh_url,
        github_metadata=metadata,
        is_root=is_root,
        depth=depth,
        outbound_edges=out_edges,
    )


def analyze(input_url: str) -> dict[str, Any]:
    """Top-level entry: returns AnalyzeResponse fields as a dict."""
    owner, repo = parse_github_url(input_url)
    slug = f"{owner}/{repo}"

    root_ids = _find_root_components(owner, repo)
    if not root_ids:
        return {
            "input_url": input_url,
            "repo": slug,
            "matched": False,
            "root_components": [],
            "downstream": [],
            "warnings": [
                f"no component in cartograph maps to {slug!r}; nothing to walk"
            ],
        }

    downstream_depths = _transitive_downstream(root_ids)
    all_ids = list({*root_ids, *downstream_depths.keys()})

    comps = _fetch_components(all_ids)
    gh_attrs = _fetch_github_attributions(all_ids)
    edges = _fetch_outbound_edges(all_ids)

    warnings: list[str] = []
    roots = [
        _build_node(cid, comps, gh_attrs, edges, 0, True, warnings)
        for cid in root_ids
    ]
    ds = [
        _build_node(cid, comps, gh_attrs, edges, depth, False, warnings)
        for cid, depth in sorted(
            downstream_depths.items(), key=lambda kv: (kv[1], comps[kv[0]]["canonical_name"])
        )
    ]

    return {
        "input_url": input_url,
        "repo": slug,
        "matched": True,
        "root_components": [r.model_dump() for r in roots],
        "downstream": [d.model_dump() for d in ds],
        "warnings": warnings,
    }

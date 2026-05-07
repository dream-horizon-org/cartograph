"""Phase 1: given a GitHub URL, find all transitively-downstream components.

Maps repo URL → root component(s) via attributions on the github plane,
walks outbound edges (full transitive closure), and returns each reached
component with its outbound edges so the caller can see per-service infra
clusters (e.g. service-b reads_from DATABASE_URL).
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

from .claude_client import ClaudeClassificationError, classify_diff
from .db import cursor
from .git_client import GitCloneError, cleanup_workspace, shallow_clone
from .github_client import (
    GitHubError,
    build_diff_text,
    fetch_pr_files,
    fetch_pr_meta,
    parse_pr_url,
)
from .mcp_indexer import build_pre_analysis
from .models import ComponentNode, DirectCall, ImpactedEndpoint, OutboundEdge


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


# ---------------------------------------------------------------------------
# Phase 2: per-endpoint impact analysis
# ---------------------------------------------------------------------------


def _fetch_endpoint_catalogs(component_ids: list[str]) -> list[dict[str, Any]]:
    """Endpoint-kind catalogs for the given (root) components, with the
    description from metadata (when an iterator stored one)."""
    if not component_ids:
        return []
    with cursor() as cur:
        cur.execute(
            """
            SELECT id::text          AS id,
                   component_id::text AS component_id,
                   identifier,
                   metadata
            FROM catalogs
            WHERE component_id = ANY(%s::uuid[])
              AND kind = 'endpoint'
            ORDER BY identifier
            """,
            (component_ids,),
        )
        rows = cur.fetchall()
    out = []
    for r in rows:
        md = r.get("metadata") or {}
        out.append({
            "id": r["id"],
            "component_id": r["component_id"],
            "identifier": r["identifier"],
            "description": md.get("description") if isinstance(md, dict) else None,
        })
    return out


def _direct_targets_for_catalogs(catalog_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
    """For each impacted catalog, return rows describing each outgoing edge
    via the flows table. Returns {catalog_id: [edge_row, ...]}."""
    if not catalog_ids:
        return {}
    out: dict[str, list[dict[str, Any]]] = {cid: [] for cid in catalog_ids}
    with cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT
                f.incoming_catalog_id::text AS catalog_id,
                e.id::text                  AS edge_id,
                e.edge_type,
                e.identifier,
                e.to_component_id::text     AS to_component_id,
                c.canonical_name            AS to_canonical_name,
                c.component_type            AS to_component_type
            FROM flows f
            JOIN edges e ON e.id = f.outgoing_edge_id
            LEFT JOIN components c ON c.id = e.to_component_id
            WHERE f.incoming_catalog_id = ANY(%s::uuid[])
            """,
            (catalog_ids,),
        )
        for r in cur.fetchall():
            out[r["catalog_id"]].append(r)
    return out


def _transitive_from_seeds(seed_ids: list[str], seed_depth: int) -> dict[str, int]:
    """Outbound walk starting at `seed_ids` (themselves at `seed_depth`).
    Returns {component_id: min_depth} INCLUDING the seeds."""
    if not seed_ids:
        return {}
    with cursor() as cur:
        cur.execute(
            """
            WITH RECURSIVE walk(id, depth, path) AS (
              SELECT id, %s::int, ARRAY[id]
              FROM components WHERE id = ANY(%s::uuid[])

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
            GROUP BY id
            """,
            (seed_depth, seed_ids),
        )
        return {r["id"]: r["depth"] for r in cur.fetchall()}


def _empty_success(input_url: str, slug: str, pr_number: int | None,
                   reason: str, rationale: str | None,
                   included: list[str], skipped: list[str],
                   root_ids: list[str],
                   warnings: list[str]) -> dict[str, Any]:
    """Return a clean 'no impact' response that does NOT fall back to phase 1.

    Used when the LLM (or the empty-diff filter) legitimately concludes that
    the PR doesn't change any handler code. We trust this and surface an
    empty downstream rather than over-deploying."""
    comps = _fetch_components(root_ids)
    gh_attrs = _fetch_github_attributions(root_ids)
    edges = _fetch_outbound_edges(root_ids)
    roots = [
        _build_node(rid, comps, gh_attrs, edges, 0, True, warnings)
        for rid in root_ids
    ]
    return {
        "input_url": input_url,
        "repo": slug,
        "pr_number": pr_number,
        "matched": True,
        "fell_back_to_phase_1": False,
        "fallback_reason": reason,
        "rationale": rationale,
        "diff_files_included": included,
        "diff_files_skipped": skipped,
        "root_components": [r.model_dump() for r in roots],
        "impacted_endpoints": [],
        "new_endpoints": [],
        "new_outbound_calls": [],
        "removed_outbound_calls": [],
        "unresolved_new_dependencies": [],
        "downstream": [],
        "warnings": warnings,
    }


def _phase_1_fallback(input_url: str, slug: str, pr_number: int | None,
                      reason: str, rationale: str | None,
                      included: list[str], skipped: list[str],
                      extra_warnings: list[str] | None = None) -> dict[str, Any]:
    """Fall back to phase-1 (full transitive closure from repo)."""
    repo_url = f"https://github.com/{slug}"
    p1 = analyze(repo_url)
    warnings = list(p1.get("warnings") or [])
    if extra_warnings:
        warnings = extra_warnings + warnings
    return {
        "input_url": input_url,
        "repo": slug,
        "pr_number": pr_number,
        "matched": p1.get("matched", False),
        "fell_back_to_phase_1": True,
        "fallback_reason": reason,
        "rationale": rationale,
        "diff_files_included": included,
        "diff_files_skipped": skipped,
        "root_components": p1.get("root_components", []),
        "impacted_endpoints": [],
        "new_endpoints": [],
        "new_outbound_calls": [],
        "removed_outbound_calls": [],
        "unresolved_new_dependencies": [],
        "downstream": p1.get("downstream", []),
        "warnings": warnings,
    }


def _fetch_active_components_for_llm(limit_warning_at: int = 200) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """All active components with their hostname/repo aliases, for sending
    to the LLM as resolution candidates.

    Returns (list_for_prompt, by_canonical_lowercase_for_lookup)."""
    with cursor() as cur:
        cur.execute(
            """
            SELECT c.id::text         AS id,
                   c.canonical_name,
                   c.display_name,
                   c.component_type,
                   COALESCE(
                       array_agg(DISTINCT a.identifier) FILTER (
                           WHERE a.resource_type IN (
                               'hostname','repo','repo_url','repo_name',
                               'repo_slug','service_name'
                           )
                       ),
                       ARRAY[]::text[]
                   ) AS aliases
            FROM components c
            LEFT JOIN attributions a ON a.component_id = c.id
            WHERE c.status = 'active'
            GROUP BY c.id, c.canonical_name, c.display_name, c.component_type
            ORDER BY c.canonical_name
            """
        )
        rows = cur.fetchall()
    by_canonical = {r["canonical_name"].lower(): dict(r) for r in rows}
    return [dict(r) for r in rows], by_canonical


def _resolve_hint(host_or_service: str,
                  target_hint: str,
                  llm_match_canonical: str | None,
                  pr_owner: str,
                  candidates_by_canonical: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Run the L1–L5' pipeline for a single new/removed-call hint.

    Returns {"status": "resolved"|"ambiguous"|"unresolved",
             "candidates": [{component_id, canonical_name, component_type,
                             score, match_method, github_url}, ...]}."""
    host = (host_or_service or "").strip().lower()
    target = (target_hint or "").strip()

    extracted_host: str | None = None
    extracted_path: str | None = None
    if "://" in target:
        try:
            from urllib.parse import urlparse
            p = urlparse(target)
            if p.hostname:
                extracted_host = p.hostname.lower()
            if p.path and p.path not in ("", "/"):
                extracted_path = p.path
        except Exception:
            pass

    cands: dict[str, dict[str, Any]] = {}

    def _add(cid: str, name: str, ctype: str, score: float, method: str) -> None:
        existing = cands.get(cid)
        if existing is None or existing["score"] < score:
            cands[cid] = {
                "component_id": cid,
                "canonical_name": name,
                "component_type": ctype,
                "score": score,
                "match_method": method,
            }

    with cursor() as cur:
        # Layer 1 — canonical_name exact
        names_to_try = {host}
        if extracted_host:
            names_to_try.add(extracted_host)
        names_to_try = {n for n in names_to_try if n}
        if names_to_try:
            cur.execute(
                """
                SELECT id::text AS id, canonical_name, component_type
                FROM components
                WHERE status = 'active' AND LOWER(canonical_name) = ANY(%s::text[])
                """,
                (list(names_to_try),),
            )
            for r in cur.fetchall():
                _add(r["id"], r["canonical_name"], r["component_type"],
                     1.0, "canonical_name_exact")

        # Layer 2 — attribution exact match across identifier-y resource types
        ident_candidates: set[str] = set()
        for n in names_to_try:
            ident_candidates.update({
                n,
                f"{pr_owner}/{n}",
                f"https://github.com/{pr_owner}/{n}",
                f"https://github.com/{pr_owner}/{n}.git",
                f"http://{n}",
                f"https://{n}",
            })
        ident_candidates = {c.lower() for c in ident_candidates if c}
        if ident_candidates:
            cur.execute(
                """
                SELECT DISTINCT
                    a.component_id::text AS component_id,
                    c.canonical_name,
                    c.component_type,
                    a.resource_type
                FROM attributions a
                JOIN components c ON c.id = a.component_id
                WHERE c.status = 'active'
                  AND LOWER(a.identifier) = ANY(%s::text[])
                  AND a.resource_type IN (
                      'hostname','repo','repo_url','repo_name','repo_slug','service_name'
                  )
                """,
                (list(ident_candidates),),
            )
            for r in cur.fetchall():
                _add(r["component_id"], r["canonical_name"], r["component_type"],
                     0.9, f"attribution_exact:{r['resource_type']}")

        # Layer 3 — catalog endpoint path match (only when target has a path)
        if extracted_path:
            cur.execute(
                """
                SELECT DISTINCT
                    cat.component_id::text AS component_id,
                    c.canonical_name,
                    c.component_type
                FROM catalogs cat
                JOIN components c ON c.id = cat.component_id
                WHERE c.status = 'active'
                  AND cat.kind = 'endpoint'
                  AND LOWER(cat.identifier) LIKE %s
                """,
                (f"%{extracted_path.lower()}%",),
            )
            for r in cur.fetchall():
                _add(r["component_id"], r["canonical_name"], r["component_type"],
                     0.7, "catalog_endpoint_match")

        # Layer 4 — attribution substring (only when L1–L3 produced no candidates)
        if not cands and host and len(host) >= 4:
            cur.execute(
                """
                SELECT DISTINCT
                    a.component_id::text AS component_id,
                    c.canonical_name,
                    c.component_type,
                    a.resource_type
                FROM attributions a
                JOIN components c ON c.id = a.component_id
                WHERE c.status = 'active'
                  AND LOWER(a.identifier) LIKE %s
                  AND a.resource_type IN (
                      'hostname','repo','repo_url','repo_name','repo_slug','service_name'
                  )
                """,
                (f"%{host}%",),
            )
            for r in cur.fetchall():
                _add(r["component_id"], r["canonical_name"], r["component_type"],
                     0.5, f"attribution_substring:{r['resource_type']}")

    # Layer 5' — LLM-suggested match (only when L1–L4 produced nothing)
    if not cands and llm_match_canonical:
        comp = candidates_by_canonical.get(llm_match_canonical.strip().lower())
        if comp:
            _add(comp["id"], comp["canonical_name"], comp["component_type"],
                 0.6, "llm_resolver")

    if not cands:
        return {"status": "unresolved", "candidates": []}

    sorted_cands = sorted(cands.values(), key=lambda c: -c["score"])
    status = "resolved" if len(sorted_cands) == 1 else "ambiguous"

    # Fill github_url for each candidate (separate query, batched)
    cand_ids = [c["component_id"] for c in sorted_cands]
    gh = _fetch_github_attributions(cand_ids)
    for c in sorted_cands:
        c["github_url"] = _derive_github_url(gh.get(c["component_id"], {}))

    return {"status": status, "candidates": sorted_cands}


def _resolve_call_list(calls: list[dict[str, Any]],
                       pr_owner: str,
                       candidates_by_canonical: dict[str, dict[str, Any]]
                       ) -> list[dict[str, Any]]:
    """Add a `resolution` block to each call dict (returned from the LLM)."""
    out = []
    for c in calls:
        resolution = _resolve_hint(
            c.get("host_or_service", ""),
            c.get("target_hint", ""),
            c.get("llm_match_canonical_name"),
            pr_owner,
            candidates_by_canonical,
        )
        out.append({**c, "resolution": resolution})
    return out


def analyze_changes(input_url: str) -> dict[str, Any]:
    """Phase 3: classify the PR diff (impacted + new + removed + new endpoints),
    resolve each new/removed call to existing components via the L1–L5'
    pipeline, subtract removed flow pairs, then return the deploy-impact graph.

    Falls back to Phase 1 only on classification failure or empty/missing
    inputs — NOT on legitimate "nothing else needed" outputs."""
    owner, repo, pr_number = parse_pr_url(input_url)
    slug = f"{owner}/{repo}"
    warnings: list[str] = []

    root_ids = _find_root_components(owner, repo)
    if not root_ids:
        return {
            "input_url": input_url,
            "repo": slug,
            "pr_number": pr_number,
            "matched": False,
            "fell_back_to_phase_1": False,
            "fallback_reason": "no_root_components",
            "rationale": None,
            "diff_files_included": [],
            "diff_files_skipped": [],
            "root_components": [],
            "impacted_endpoints": [],
            "new_endpoints": [],
            "new_outbound_calls": [],
            "removed_outbound_calls": [],
            "unresolved_new_dependencies": [],
            "downstream": [],
            "warnings": [
                f"no component in cartograph maps to {slug!r}; nothing to analyze"
            ],
        }

    catalogs = _fetch_endpoint_catalogs(root_ids)

    # Fetch PR metadata up-front for head_sha + head_ref + (possibly fork's) head.repo
    try:
        pr_meta = fetch_pr_meta(owner, repo, pr_number)
        head_sha = pr_meta["head"]["sha"]
        head_ref = pr_meta["head"]["ref"]
        head_repo_full = pr_meta["head"]["repo"]["full_name"]
    except (GitHubError, KeyError, TypeError) as e:
        warnings.append(f"github PR meta fetch failed: {e}")
        return _phase_1_fallback(
            input_url, slug, pr_number,
            "github_fetch_failed", None, [], [], warnings,
        )

    try:
        files = fetch_pr_files(owner, repo, pr_number)
    except GitHubError as e:
        warnings.append(f"github files fetch failed: {e}")
        return _phase_1_fallback(
            input_url, slug, pr_number,
            "github_fetch_failed", None, [], [], warnings,
        )

    diff_text, included, skipped = build_diff_text(files)

    components_for_llm, by_canonical = _fetch_active_components_for_llm()
    if len(components_for_llm) > 200:
        warnings.append(
            f"sending {len(components_for_llm)} components to LLM — prompt may "
            f"be slow / large (consider pre-filtering when > 200)"
        )

    if not catalogs:
        warnings.append(
            f"no endpoint catalogs in DB for {slug!r} — can't classify per-endpoint"
        )
        return _phase_1_fallback(
            input_url, slug, pr_number,
            "no_endpoint_catalogs", None, included, skipped, warnings,
        )

    if not diff_text.strip():
        warnings.append(
            "PR diff is empty after filtering test/lockfile/binary files — "
            "no handler code touched, nothing to deploy"
        )
        return _empty_success(
            input_url, slug, pr_number,
            "empty_diff_after_filters", None, included, skipped,
            root_ids, warnings,
        )

    # Phase 4: clone PR HEAD so Claude can Read/Grep/Glob the actual source.
    # Phase 5: also index it with codebase-memory-mcp so we can hand Claude
    # a deterministic per-handler pre-analysis (line-range overlap + CALLS
    # walk). Workspace + cmm project are cleaned up in finally.
    workspace_path: str | None = None
    pre_analysis: dict[str, Any] | None = None
    try:
        try:
            workspace_path = shallow_clone(head_repo_full, head_sha, head_ref)
        except GitCloneError as e:
            warnings.append(f"git clone failed: {e}")
            return _phase_1_fallback(
                input_url, slug, pr_number,
                "git_clone_failed", None, included, skipped, warnings,
            )

        # build_pre_analysis is best-effort: it indexes, queries, deletes
        # the cmm project, and returns indexed=False with a `note` if
        # anything fails. We never block on this.
        try:
            pre_analysis = build_pre_analysis(workspace_path, files)
            if pre_analysis.get("indexed"):
                logger.info(
                    "code-graph pre-analysis: project=%s nodes=%s edges=%s "
                    "direct_handlers=%d transitive_handlers=%d",
                    pre_analysis.get("project_name"),
                    pre_analysis.get("nodes_count"),
                    pre_analysis.get("edges_count"),
                    len(pre_analysis.get("directly_impacted_handlers") or []),
                    len(pre_analysis.get("transitively_impacted_handlers") or []),
                )
            else:
                warnings.append(
                    "code-graph pre-analysis unavailable: "
                    + (pre_analysis.get("note") or "unknown reason")
                )
        except Exception as e:
            logger.exception("pre-analysis crashed; continuing without it")
            warnings.append(f"code-graph pre-analysis crashed: {e}")
            pre_analysis = {"indexed": False, "note": f"crash: {e}"}

        try:
            classification = classify_diff(
                diff_text, catalogs, components_for_llm,
                cwd=workspace_path,
                pre_analysis=pre_analysis,
            )
        except ClaudeClassificationError as e:
            warnings.append(f"claude classification failed: {e}")
            return _phase_1_fallback(
                input_url, slug, pr_number,
                "llm_classification_failed", None, included, skipped, warnings,
            )

        result = _build_phase3_response(
            input_url, slug, pr_number, owner,
            classification, catalogs, root_ids,
            by_canonical, included, skipped, warnings,
        )
        if pre_analysis is not None:
            result["code_graph_evidence"] = pre_analysis
        return result
    finally:
        if workspace_path:
            cleanup_workspace(workspace_path)


def _build_phase3_response(input_url: str, slug: str, pr_number: int,
                           owner: str,
                           classification: dict[str, Any],
                           catalogs: list[dict[str, Any]],
                           root_ids: list[str],
                           by_canonical: dict[str, dict[str, Any]],
                           included: list[str],
                           skipped: list[str],
                           warnings: list[str]) -> dict[str, Any]:
    """Translate the LLM classification into the final Phase-3 response,
    running resolution + flow-pair subtraction + transitive closure."""
    rationale = classification.get("rationale")
    impacted_idents = set(classification.get("impacted_endpoints") or [])
    impacted_catalogs = [c for c in catalogs if c["identifier"] in impacted_idents]

    unmatched = sorted(impacted_idents - {c["identifier"] for c in catalogs})
    if unmatched:
        warnings.append(
            f"LLM returned {len(unmatched)} identifier(s) not in catalog: {unmatched}"
        )

    # Resolve every new and removed call against cartograph (L1–L5')
    new_calls_resolved = _resolve_call_list(
        classification.get("new_outbound_calls", []), owner, by_canonical
    )
    removed_calls_resolved = _resolve_call_list(
        classification.get("removed_outbound_calls", []), owner, by_canonical
    )
    new_endpoints = classification.get("new_endpoints", [])

    # If LLM legitimately classified the PR as touching no handler code AND
    # introducing no new/removed calls or endpoints, trust it and return an
    # empty success — don't over-deploy via Phase 1 fallback.
    nothing_classified = (
        not impacted_catalogs and not new_calls_resolved
        and not removed_calls_resolved and not new_endpoints
    )
    if nothing_classified:
        return _empty_success(
            input_url, slug, pr_number,
            "no_handler_code_touched", rationale, included, skipped,
            root_ids, warnings,
        )

    # "All endpoints impacted" no longer triggers a fallback — the normal
    # closure walk below produces the same deploy graph (every flow target
    # ⇒ every transitive dep), just without the fell_back marker.

    # Build the (catalog_id, target_component_id) pairs to drop based on
    # cleanly-resolved removed_outbound_calls. Unresolved removals don't
    # subtract anything — we don't know which target to drop.
    removed_pairs: set[tuple[str, str]] = set()
    catalogs_by_identifier = {c["identifier"]: c for c in catalogs}
    for rc in removed_calls_resolved:
        res = rc["resolution"]
        if res["status"] != "resolved":
            continue
        target_id = res["candidates"][0]["component_id"]
        from_ep = rc.get("from_endpoint")
        if not from_ep:
            continue
        cat = catalogs_by_identifier.get(from_ep)
        if not cat:
            warnings.append(
                f"removed_outbound_calls.from_endpoint {from_ep!r} doesn't match "
                f"any catalog — can't subtract this removal"
            )
            continue
        removed_pairs.add((cat["id"], target_id))

    # Per-impacted-endpoint flow rows, with removed pairs filtered out
    direct_by_cat = _direct_targets_for_catalogs([c["id"] for c in impacted_catalogs])

    direct_target_ids: set[str] = set()
    impacted_out: list[ImpactedEndpoint] = []
    for cat in impacted_catalogs:
        kept_rows = []
        for r in direct_by_cat.get(cat["id"], []):
            tcid = r["to_component_id"]
            if tcid and (cat["id"], tcid) in removed_pairs:
                continue  # this call was removed by the PR
            kept_rows.append(r)
        calls = [
            DirectCall(
                edge_type=r["edge_type"],
                identifier=r["identifier"],
                to_component_id=r["to_component_id"],
                to_canonical_name=r.get("to_canonical_name"),
                to_component_type=r.get("to_component_type"),
                resolved=r["to_component_id"] is not None,
            )
            for r in kept_rows
        ]
        impacted_out.append(ImpactedEndpoint(
            endpoint=cat["identifier"],
            catalog_id=cat["id"],
            component_id=cat["component_id"],
            directly_calls=calls,
        ))
        for r in kept_rows:
            if r["to_component_id"]:
                direct_target_ids.add(r["to_component_id"])

    # Folded-in: resolved new-call targets become direct dependencies too
    for nc in new_calls_resolved:
        res = nc["resolution"]
        if res["status"] == "resolved":
            tid = res["candidates"][0]["component_id"]
            if tid not in root_ids:
                direct_target_ids.add(tid)

    closure = _transitive_from_seeds(list(direct_target_ids), seed_depth=1)
    downstream_ids = list(closure.keys())

    all_ids = list({*root_ids, *downstream_ids})
    comps = _fetch_components(all_ids)
    gh_attrs = _fetch_github_attributions(all_ids)
    edges = _fetch_outbound_edges(all_ids)

    roots = [
        _build_node(rid, comps, gh_attrs, edges, 0, True, warnings)
        for rid in root_ids
    ]
    ds = [
        _build_node(cid, comps, gh_attrs, edges, depth, False, warnings)
        for cid, depth in sorted(
            closure.items(),
            key=lambda kv: (kv[1], comps[kv[0]]["canonical_name"]),
        )
    ]

    unresolved_new = [
        c for c in new_calls_resolved
        if c["resolution"]["status"] == "unresolved"
    ]

    return {
        "input_url": input_url,
        "repo": slug,
        "pr_number": pr_number,
        "matched": True,
        "fell_back_to_phase_1": False,
        "fallback_reason": None,
        "rationale": rationale,
        "diff_files_included": included,
        "diff_files_skipped": skipped,
        "root_components": [r.model_dump() for r in roots],
        "impacted_endpoints": [e.model_dump() for e in impacted_out],
        "new_endpoints": new_endpoints,
        "new_outbound_calls": new_calls_resolved,
        "removed_outbound_calls": removed_calls_resolved,
        "unresolved_new_dependencies": unresolved_new,
        "downstream": [d.model_dump() for d in ds],
        "warnings": warnings,
    }

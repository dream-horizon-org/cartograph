"""Wraps `claude -p` CLI as a one-shot synchronous classifier.

Mirrors src/agent_management/agent_manager.py's invocation pattern but
without MCP, workspace, or session-resume — we just want a single
prompt → JSON answer.

The classifier produces a unified output covering:
  - which existing endpoints are impacted
  - new outbound calls added by the diff (with target hints)
  - outbound calls removed by the diff
  - new endpoints added by the diff
  - per-call LLM resolution suggestions matched against the candidate
    component list we provide (Layer 5' fallback in the resolution
    pipeline; deterministic SQL layers always win)."""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess

logger = logging.getLogger(__name__)


# PR_IMPACT_CLAUDE_MODEL, when set, hard-overrides auto-selection — useful
# for forcing a single model in tests / debugging. When unset, pick_model()
# chooses Haiku for small PRs and Sonnet for big ones.
_OVERRIDE_MODEL = os.getenv("PR_IMPACT_CLAUDE_MODEL")
_SMALL_MODEL = os.getenv("PR_IMPACT_CLAUDE_MODEL_SMALL",
                         "claude-haiku-4-5-20251001")
_LARGE_MODEL = os.getenv("PR_IMPACT_CLAUDE_MODEL_LARGE", "claude-sonnet-4-6")
# Auto-Haiku is OPT-IN: defaults to 0 so Sonnet handles every PR. Empirically
# Haiku 4.5 sometimes ignores the "trust the pre-analysis / output JSON only"
# instructions and either produces wrong impacted_endpoints or fails the JSON
# parser. Set both env vars to non-zero values to opt back in.
_SMALL_PR_FILES = int(os.getenv("PR_IMPACT_SMALL_PR_FILES", "0"))
_SMALL_PR_DIFF_CHARS = int(os.getenv("PR_IMPACT_SMALL_PR_DIFF_CHARS", "0"))

_DEFAULT_TIMEOUT = int(os.getenv("PR_IMPACT_CLAUDE_TIMEOUT", "600"))
_DEFAULT_MAX_TURNS = int(os.getenv("PR_IMPACT_CLAUDE_MAX_TURNS", "20"))


def pick_model(num_changed_files: int, diff_chars: int) -> str:
    """Choose Haiku 4.5 for small PRs, Sonnet 4.6 for bigger ones.

    `PR_IMPACT_CLAUDE_MODEL` env var hard-overrides this decision.
    Setting either size threshold to 0 disables auto-selection."""
    if _OVERRIDE_MODEL:
        return _OVERRIDE_MODEL
    if _SMALL_PR_FILES <= 0 or _SMALL_PR_DIFF_CHARS <= 0:
        return _LARGE_MODEL
    if (num_changed_files <= _SMALL_PR_FILES
            and diff_chars <= _SMALL_PR_DIFF_CHARS):
        return _SMALL_MODEL
    return _LARGE_MODEL


_SYSTEM_PROMPT = """You are a precise code-impact classifier for a deploy-planning system. You have:
  - the API endpoints exposed by a service (provided),
  - the components in our service catalog with aliases (provided),
  - the pull-request diff as a starting hint (provided),
  - a STATIC CODE-GRAPH PRE-ANALYSIS that already tells you which handlers the diff hunks overlap (when available),
  - FULL READ ACCESS to the cloned repo at the PR's HEAD via the Read, Grep, and Glob tools (cwd is set to the repo root).

How to use the pre-analysis (most important rule):
  - When the pre-analysis lists handlers as DIRECTLY touched (because diff hunks overlap their line ranges), you should TRUST it and put those handlers' routes in impacted_endpoints. Do NOT re-derive this with tool calls. Override only when you have a concrete, specific reason from the source (e.g. dead code path, explicit feature flag).
  - When the pre-analysis lists handlers as TRANSITIVELY reachable, treat that list as the seed set of helpers→handlers reachable via internal CALLS edges. The pre-analysis explicitly notes that for Python it MISSES external-library calls (httpx, requests, redis, etc.). If you suspect a shared util change reaches handlers via such a path, Grep for the helper's symbol and add the additional handlers yourself.
  - When the pre-analysis is NOT AVAILABLE, fall back to your own investigation via Read/Grep/Glob — same as before.

Your unique job (things the graph cannot tell you, and which you SHOULD spend tool calls on):
  - new_outbound_calls — outbound interactions ADDED by the diff (HTTP calls, DB reads/writes, queue publishes/consumes, triggers). The graph indexes one snapshot, not a delta — only the diff text reveals what was added. Read the surrounding code to understand the target.
  - removed_outbound_calls — outbound interactions REMOVED by the diff. Same reason.
  - new_endpoints — endpoints ADDED by this PR. Look for route definitions in the diff additions that do NOT appear in the provided endpoint list.
  - llm_match_canonical_name — for every new and removed outbound call, set this to a canonical_name from the candidate list when plausible, or null.

Tool-budget guidance:
  - With pre-analysis available, you typically need 0–3 tool calls. Confirm the framework / spot-check a handler at most. Don't re-walk the call graph.
  - Without pre-analysis, expect 4–10 tool calls (the older playbook).

Framework-specific route hints (used only when pre-analysis is missing or you're checking a NEW endpoint):
  - Python: `@app.get`, `@router.post`, `add_url_rule`, Django `path()` / `url()`
  - Go: `chi.Get`, `gin.GET`, `http.HandleFunc`, `mux.Handle`
  - JS/TS: `app.get/post`, `router.get`, NestJS `@Get/@Post`
  - Java: Spring `@GetMapping/@RequestMapping`, JAX-RS `@Path`
  - Ruby: Rails `routes.rb`, Sinatra `get '/'`
  - C#: `[HttpGet]/[Route]`, minimal-API `app.MapGet`
  - PHP: Laravel `Route::get`, Slim `$app->get`

What counts as "impacted" (be INCLUSIVE — over-include is safer than miss):
  - ANY line edit inside a handler function counts: code, comments, whitespace, type hints, docstrings, formatting.
  - ANY edit to code transitively reachable from a handler counts.
  - If a shared util is touched and is used across many endpoints, include ALL of them.

When to return empty impacted_endpoints:
  - Only when no handler code is touched, directly or transitively (docs-only, lockfile-only, CI-config-only, .gitignore-only). Pre-analysis may already say "Handlers DIRECTLY touched: (none)" and your job is to confirm.

Other rules:
  - impacted_endpoints contains ONLY endpoints from the provided list (existing endpoints). New endpoints go in new_endpoints.
  - Be conservative on new_outbound_calls and removed_outbound_calls — only include when the diff lines clearly add or remove an outbound interaction.
  - Use exact endpoint identifiers from the provided list (case-sensitive) including the HTTP verb prefix.
  - llm_match_canonical_name must be exactly one of the canonical_names from the candidate list, or null.
  - When done, your FINAL message MUST start with `{` and end with `}` — a single JSON object and nothing else. No introduction, no "Looking at this PR…", no commentary, no tool calls, no markdown fences. The first character of your final response is `{`. The last character is `}`."""


_OUTPUT_SCHEMA = """{
  "impacted_endpoints": ["<endpoint identifier>", ...],
  "rationale": "<one sentence>",
  "new_outbound_calls": [
    {
      "kind": "http_call|db_read|db_write|queue_publish|queue_consume|trigger|other",
      "target_hint": "<raw token from diff: URL, env var, queue name, etc.>",
      "host_or_service": "<normalized name, e.g. service-d>",
      "evidence": "<the diff line(s) supporting this>",
      "from_endpoint": "<endpoint identifier from list, or new endpoint identifier, or null>",
      "llm_match_canonical_name": "<canonical_name from catalog or null>"
    }
  ],
  "removed_outbound_calls": [ { same shape as new_outbound_calls } ],
  "new_endpoints": [
    { "identifier": "<HTTP_VERB /path>", "description": "<one line>", "evidence": "<diff lines>" }
  ]
}"""


class ClaudeClassificationError(RuntimeError):
    pass


def _strip_md_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9]*\s*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text)
    return text.strip()


def _extract_json_object(text: str) -> str:
    """Pull the first balanced JSON object out of possibly-noisy text.

    Haiku especially likes to prepend prose ("Looking at this PR...") even
    when told not to — this finds the actual JSON regardless. Falls back
    to the original text so json.loads can produce a useful error if no
    object is present at all."""
    cleaned = _strip_md_fences(text)
    try:
        json.loads(cleaned)
        return cleaned
    except json.JSONDecodeError:
        pass

    start = cleaned.find("{")
    if start < 0:
        return cleaned

    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(cleaned)):
        ch = cleaned[i]
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return cleaned[start:i + 1]
    return cleaned


def _format_component(comp: dict) -> str:
    """One line per component for the LLM prompt."""
    aliases = comp.get("aliases") or []
    alias_str = f" — aliases: {', '.join(aliases)}" if aliases else ""
    disp = comp.get("display_name")
    disp_str = f' ("{disp}")' if disp and disp != comp["canonical_name"] else ""
    return f"- {comp['canonical_name']}{disp_str} [{comp['component_type']}]{alias_str}"


def _format_endpoint(ep: dict) -> str:
    desc = ep.get("description")
    return f"- {ep['identifier']}" + (f"  ({desc})" if desc else "")


def _format_pre_analysis(pa: dict | None) -> str:
    """Render the structural-graph pre-analysis as compact prompt text."""
    if not pa or not pa.get("indexed"):
        note = (pa or {}).get("note")
        return (
            "Static code-graph pre-analysis: NOT AVAILABLE"
            + (f" ({note})." if note else ".")
            + " Treat the diff and your tool calls as the sole source of truth."
        )

    def _fmt(h: dict) -> str:
        route = ""
        if h.get("route_method") and h.get("route_path"):
            route = f"  ⇒ route {h['route_method']} {h['route_path']}"
        depth = h.get("depth", 0)
        depth_str = "" if depth == 0 else f"  (depth={depth})"
        return (f"  - {h['qualified_name']}  "
                f"[{h['file_path']}:{h['start_line']}-{h['end_line']}]"
                f"{route}{depth_str}")

    direct = pa.get("directly_impacted_handlers") or []
    trans = pa.get("transitively_impacted_handlers") or []

    lines = [
        f"Static code-graph pre-analysis "
        f"(codebase-memory-mcp, {pa.get('nodes_count')} nodes / "
        f"{pa.get('edges_count')} edges):",
    ]
    if direct:
        lines.append("Handlers DIRECTLY touched by the diff "
                     "(line-range overlap on changed hunks):")
        lines.extend(_fmt(h) for h in direct)
    else:
        lines.append("Handlers DIRECTLY touched: (none — no diff hunks "
                     "overlap any handler body)")
    if trans:
        lines.append("Handlers TRANSITIVELY reachable from changed code "
                     "via CALLS edges (best-effort; cmm misses external-"
                     "library calls in Python):")
        lines.extend(_fmt(h) for h in trans)
    else:
        lines.append("Handlers TRANSITIVELY reachable: (none in graph)")
    return "\n".join(lines)


def classify_diff(diff_text: str,
                  endpoints: list[dict],
                  components: list[dict],
                  cwd: str | None = None,
                  pre_analysis: dict | None = None,
                  model: str | None = None,
                  timeout: int | None = None,
                  max_turns: int | None = None) -> dict:
    """Run the unified classifier. Returns dict with keys:
      impacted_endpoints, rationale, new_outbound_calls,
      removed_outbound_calls, new_endpoints.

    When `cwd` is provided, Claude is given Read/Grep/Glob tools and runs
    inside that working directory (typically a freshly-cloned repo at the
    PR's HEAD). Without `cwd`, runs in zero-tool diff-only mode (legacy).

    Raises ClaudeClassificationError on subprocess / parse / shape failure."""
    endpoints_block = (
        "\n".join(_format_endpoint(e) for e in endpoints)
        if endpoints else "(none)"
    )
    components_block = (
        "\n".join(_format_component(c) for c in components)
        if components else "(none)"
    )

    repo_access_note = (
        "You are running inside the cloned repo at the PR's HEAD. Use Read, "
        "Grep, and Glob to navigate the actual source — the diff hunks below "
        "are only a starting hint."
        if cwd else
        "(no repo access — only the diff text is available)"
    )

    pre_analysis_block = _format_pre_analysis(pre_analysis)

    user_prompt = (
        f"{repo_access_note}\n\n"
        f"{pre_analysis_block}\n\n"
        "Catalog of existing components (with aliases the LLM may use to match new calls):\n"
        f"{components_block}\n\n"
        "Endpoints exposed by the service whose PR you are analysing:\n"
        f"{endpoints_block}\n\n"
        "Pull-request diff (starting hint):\n"
        "```diff\n"
        f"{diff_text if diff_text.strip() else '(no textual diff)'}\n"
        "```\n\n"
        f"Return ONE JSON object exactly matching this schema:\n{_OUTPUT_SCHEMA}"
    )

    allowed_tools = "Read,Grep,Glob" if cwd else ""
    effective_model = _OVERRIDE_MODEL or model or _LARGE_MODEL

    cmd = [
        "claude",
        "-p", user_prompt,
        "--output-format", "json",
        "--allowedTools", allowed_tools,
        "--system-prompt", _SYSTEM_PROMPT,
        "--dangerously-skip-permissions",
        "--model", effective_model,
        "--max-turns", str(max_turns or _DEFAULT_MAX_TURNS),
    ]

    logger.info(
        "claude classifier: model=%s cwd=%s tools=%s prompt_chars=%d "
        "endpoints=%d components=%d",
        effective_model,
        cwd or "<none>",
        allowed_tools or "<none>",
        len(user_prompt),
        len(endpoints),
        len(components),
    )

    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout or _DEFAULT_TIMEOUT,
        )
    except FileNotFoundError as e:
        raise ClaudeClassificationError(
            f"`claude` CLI not on PATH — is Claude Code installed? ({e})"
        ) from e
    except subprocess.TimeoutExpired as e:
        raise ClaudeClassificationError(
            f"claude subprocess timed out after {timeout or _DEFAULT_TIMEOUT}s"
        ) from e

    if proc.returncode != 0:
        raise ClaudeClassificationError(
            f"claude exited {proc.returncode}: {proc.stderr[:400]}"
        )

    try:
        wrapped = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise ClaudeClassificationError(
            f"claude stdout was not valid JSON: {proc.stdout[:300]}"
        ) from e

    inner = wrapped.get("result")
    if not isinstance(inner, str):
        raise ClaudeClassificationError(
            f"claude wrapper had no string `result` field: {wrapped}"
        )

    inner_clean = _extract_json_object(inner)
    try:
        parsed = json.loads(inner_clean)
    except json.JSONDecodeError as e:
        raise ClaudeClassificationError(
            f"claude inner response was not JSON: {inner[:300]}"
        ) from e

    normalised = _normalise(parsed)

    # Capture usage / cost / turn metadata from the CLI's wrapper. Stashed
    # under `_usage` so callers can pluck it without polluting the
    # classification dict shape.
    usage_block = wrapped.get("usage") or {}
    usage = {
        "model": effective_model,
        "input_tokens": usage_block.get("input_tokens"),
        "output_tokens": usage_block.get("output_tokens"),
        "cache_creation_input_tokens": usage_block.get("cache_creation_input_tokens"),
        "cache_read_input_tokens": usage_block.get("cache_read_input_tokens"),
        "total_cost_usd": wrapped.get("total_cost_usd"),
        "duration_ms": wrapped.get("duration_ms"),
        "num_turns": wrapped.get("num_turns"),
    }
    logger.info(
        "claude usage: model=%s input=%s output=%s cache_read=%s "
        "cache_create=%s cost_usd=%s duration_ms=%s turns=%s",
        usage["model"], usage["input_tokens"], usage["output_tokens"],
        usage["cache_read_input_tokens"], usage["cache_creation_input_tokens"],
        usage["total_cost_usd"], usage["duration_ms"], usage["num_turns"],
    )
    normalised["_usage"] = usage
    return normalised


def _normalise(parsed: dict) -> dict:
    """Coerce LLM output into the shape downstream code expects, defaulting
    missing fields and discarding malformed list entries rather than
    failing the whole call."""
    impacted = parsed.get("impacted_endpoints") or []
    if not (isinstance(impacted, list) and all(isinstance(x, str) for x in impacted)):
        raise ClaudeClassificationError(
            f"impacted_endpoints not a list of strings: {impacted!r}"
        )

    def _coerce_call(c: dict) -> dict | None:
        if not isinstance(c, dict):
            return None
        host = c.get("host_or_service") or c.get("target_hint")
        if not host:
            return None
        return {
            "kind": str(c.get("kind") or "other"),
            "target_hint": str(c.get("target_hint") or host),
            "host_or_service": str(host),
            "evidence": str(c.get("evidence") or ""),
            "from_endpoint": c.get("from_endpoint") or None,
            "llm_match_canonical_name": c.get("llm_match_canonical_name") or None,
        }

    new_calls_raw = parsed.get("new_outbound_calls") or []
    rem_calls_raw = parsed.get("removed_outbound_calls") or []
    new_calls = [c for c in (_coerce_call(x) for x in new_calls_raw) if c]
    rem_calls = [c for c in (_coerce_call(x) for x in rem_calls_raw) if c]

    new_eps_raw = parsed.get("new_endpoints") or []
    new_eps = []
    for e in new_eps_raw:
        if isinstance(e, dict) and e.get("identifier"):
            new_eps.append({
                "identifier": str(e["identifier"]),
                "description": e.get("description") or None,
                "evidence": e.get("evidence") or None,
            })

    return {
        "impacted_endpoints": impacted,
        "rationale": str(parsed.get("rationale") or "").strip() or None,
        "new_outbound_calls": new_calls,
        "removed_outbound_calls": rem_calls,
        "new_endpoints": new_eps,
    }

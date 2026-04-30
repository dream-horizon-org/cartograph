"""Phase 9.1: mcp_call_batch — server-side parallel dispatcher.

The agent emits ONE `mcp_call_batch` tool_use with N sub-calls of
mixed shapes; the server fans them out concurrently via a thread
pool and returns one bundled response. The LLM pays 1 round-trip
instead of N.

Why this exists: Claude Code's agent loop (the `claude -p` subprocess
running each agent) disables parallel tool_use internally — even
though the Anthropic API supports `[tool_use_A, tool_use_B]` per
assistant turn, Claude Code provably emits at most one tool_use per
turn (DEMO8 verified 0/851 messages with >1 tool_use across 6
agents). There is no CLI flag, no settings.json key, no env var to
override. mcp_call_batch is the workaround: parallelism happens
server-side, invisible to the agent loop's serialisation logic.

Contract (collect-all, never strict-mode):
- Each sub-call succeeds or fails on its own.
- One failure does NOT abort siblings. Matches what native parallel
  tool_use semantics would have produced.
- Per-call result carries `idx`, `tool`, and either `result` (on ok)
  or `error` (on exception).

Hard rules:
- No nesting. If any sub-call's `tool == 'mcp_call_batch'`, refuse
  the whole batch before dispatching anything. Recursive batches
  have no benefit (always flattenable) and risk pathological
  recursion.
- Cap 50 sub-calls. Above that, the SME prompt directs use of bulk
  variants which take 500 rows each (and those bulk variants are
  themselves callable inside mcp_call_batch).
- Caller's `agent_id` is injected into a sub-call's args if the
  sub-call doesn't carry its own. If it carries a different one
  (e.g. proxy paths via act_on_proxy_item), that's preserved — the
  sub-call's own ownership/auth checks fire normally.
"""

import concurrent.futures
import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)

MAX_BATCH_SIZE = 50
MAX_CONCURRENCY = 8


def call_batch(
    agent_id: str,
    calls: list[dict],
    dispatch: dict[str, Callable[..., Any]],
) -> dict:
    """Dispatch a heterogeneous batch of MCP tool calls in parallel.

    Args:
      agent_id: caller's agent id. Injected into any sub-call whose
        `args` doesn't carry an `agent_id` of its own.
      calls: list of `{"tool": str, "args": dict}`. `args` may omit
        `agent_id` (it gets injected).
      dispatch: name → callable map. Each callable is the
        `@mcp.tool()`-registered wrapper, so the existing audit
        decorator records each sub-call in `mcp_audit`.

    Returns:
      `{"results": [{"idx": int, "tool": str, "ok": bool,
                     "result": Any} OR
                    {"idx": int, "tool": str, "ok": False,
                     "error": str}]}`

    Raises:
      ValueError on shape errors (bad list, bad dict, bad tool name,
      nesting, over the cap). These are pre-flight failures — no
      sub-call has run yet.
    """
    if not isinstance(calls, list):
        raise ValueError(f"calls must be a list, got {type(calls).__name__}")
    if len(calls) == 0:
        return {"results": []}
    if len(calls) > MAX_BATCH_SIZE:
        raise ValueError(
            f"calls exceeds max {MAX_BATCH_SIZE} per batch (got {len(calls)}). "
            "Use bulk variants (e.g. upsert_attributions_bulk) for >50 same-shape rows; "
            "they cap at 500 rows each and are themselves callable inside mcp_call_batch."
        )

    # Pre-validate every entry. ALL shape errors caught before any dispatch fires.
    for i, c in enumerate(calls):
        if not isinstance(c, dict):
            raise ValueError(f"calls[{i}] must be a dict, got {type(c).__name__}")
        tool = c.get("tool")
        if not isinstance(tool, str) or not tool:
            raise ValueError(f"calls[{i}].tool must be a non-empty string")
        if tool == "mcp_call_batch":
            raise ValueError(
                f"calls[{i}]: mcp_call_batch cannot be nested. "
                "Flatten all sub-calls into one outer batch."
            )
        if tool not in dispatch:
            raise ValueError(
                f"calls[{i}].tool '{tool}' is not a known mcp_call_batch-eligible tool. "
                "See the SME prompt's BULK CALLS DECISION LADDER for the eligible set."
            )
        args = c.get("args", {})
        if not isinstance(args, dict):
            raise ValueError(
                f"calls[{i}].args must be a dict, got {type(args).__name__}"
            )

    def _run_one(item: tuple[int, dict]) -> dict:
        idx, c = item
        tool_name = c["tool"]
        args = dict(c.get("args", {}))
        # Inject caller's agent_id ONLY if the sub-call didn't carry its own.
        # Proxy paths (act_on_proxy_item) and admin paths legitimately need
        # to specify a different agent_id — we don't override.
        if "agent_id" not in args:
            args["agent_id"] = agent_id
        try:
            result = dispatch[tool_name](**args)
            return {"idx": idx, "tool": tool_name, "ok": True, "result": result}
        except Exception as exc:
            return {
                "idx": idx,
                "tool": tool_name,
                "ok": False,
                "error": repr(exc)[:500],
            }

    workers = min(MAX_CONCURRENCY, len(calls))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        results = list(ex.map(_run_one, enumerate(calls)))

    return {"results": results}

"""Phase 5.10: blanket per-call audit log of every MCP tool invocation.

Wraps every @mcp.tool registration with an `audited` decorator that
records (agent_id, tool_name, args_hash, result_status, duration_ms,
created_at) to mcp_audit. Full payloads are NOT stored — only a sha1
of canonicalised args + kwargs — so the row size stays bounded as
volume grows.

Failures inside the audit insert are swallowed with a logger.warning so
the wrapped tool still returns its result. The audit is observability,
not a hard dependency.
"""

import functools
import hashlib
import logging
import time

from shared.db import execute_mutate

logger = logging.getLogger(__name__)


def _hash_args(args, kwargs) -> str:
    """sha1(repr(args) | sorted-kwargs-repr). Doesn't store payload values."""
    try:
        material = repr(tuple(args)) + repr(sorted(kwargs.items()))
    except Exception:
        material = repr((args, kwargs))
    return hashlib.sha1(material.encode("utf-8", errors="replace")).hexdigest()


def _record(agent_id, tool_name, args_hash, status, error, duration_ms):
    try:
        execute_mutate(
            """INSERT INTO mcp_audit
                  (agent_id, tool_name, args_hash, result_status, error_msg, duration_ms)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (agent_id, tool_name, args_hash, status, error, duration_ms),
        )
    except Exception as e:
        logger.warning("mcp_audit insert failed for %s: %r", tool_name, e)


def audited(fn):
    """Decorator. Wrap a tool function so every call gets recorded.

    Convention: the first positional arg or the `agent_id` kwarg is the
    caller. We don't fail the call if the audit insert errors — the tool
    is the user-visible contract; audit is bookkeeping.
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        agent_id = kwargs.get("agent_id")
        if agent_id is None and args:
            agent_id = args[0] if isinstance(args[0], str) else None
        args_hash = _hash_args(args, kwargs)
        t0 = time.monotonic()
        try:
            result = fn(*args, **kwargs)
            duration_ms = int((time.monotonic() - t0) * 1000)
            _record(agent_id, fn.__name__, args_hash, "ok", None, duration_ms)
            return result
        except Exception as exc:
            duration_ms = int((time.monotonic() - t0) * 1000)
            _record(
                agent_id, fn.__name__, args_hash, "error",
                repr(exc)[:500], duration_ms,
            )
            raise

    return wrapper


def install(mcp_instance) -> None:
    """Monkey-patch mcp.tool() to wrap every registered tool with audited.

    Call this BEFORE any @mcp.tool() decorator runs (i.e. immediately
    after the FastMCP instance is created). Every subsequent
    @mcp.tool()-decorated function will be audited transparently.
    """
    original_tool = mcp_instance.tool

    def patched_tool(*decorator_args, **decorator_kwargs):
        # mcp.tool() returns a decorator that registers the function.
        register = original_tool(*decorator_args, **decorator_kwargs)

        def wrap(fn):
            return register(audited(fn))

        return wrap

    mcp_instance.tool = patched_tool

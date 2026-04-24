"""Phase 4: single source of truth for "actor must be active" checks.

This helper replaces four duplicated `_caller` copies and six inline
`status != 'decommissioned'` SELECTs scattered across the MCP tool
modules. Any future "actor exists + active" check MUST call through
here so the proxy-act flow works — the ContextVar gate below is the
only sanctioned way to let a decommissioned agent's ID pass.

Flow:
- Normal call: `require_active_agent(agent_id)` — raises unless the
  agent exists AND status != 'decommissioned'.
- Proxy-act call: `act_on_proxy_item` sets `_PROXY_CTX` to
  `{'proxy_agent_id': <deactivated_agent>, 'survivor_id': <acting_agent>}`
  via `push_proxy_context(...)`, invokes the underlying public tool,
  and resets the ContextVar in try/finally. Inside the tool body,
  `require_active_agent(proxy_agent_id)` will permit the row because
  the ctx matches; any OTHER agent lookup inside the same call still
  enforces the active check.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import TypedDict

from shared.db import execute_one


class ProxyContext(TypedDict):
    proxy_agent_id: str
    survivor_id: str


_PROXY_CTX: ContextVar[ProxyContext | None] = ContextVar(
    "cartograph_proxy_ctx", default=None
)


def push_proxy_context(*, proxy_agent_id: str, survivor_id: str) -> Token:
    """Enter a proxy-act scope. MUST be paired with `pop_proxy_context(token)`
    in a try/finally so the context is always reset, even on exception.
    Only the router is permitted to call this."""
    return _PROXY_CTX.set(
        {"proxy_agent_id": proxy_agent_id, "survivor_id": survivor_id}
    )


def pop_proxy_context(token: Token) -> None:
    _PROXY_CTX.reset(token)


def get_proxy_context() -> ProxyContext | None:
    """Read the current proxy context. Returns None outside proxy calls."""
    return _PROXY_CTX.get()


def require_active_agent(agent_id: str) -> dict:
    """Validate an agent exists + is not decommissioned. Returns the row.

    Bypasses the decommissioned-check ONLY when a proxy context is
    active AND its `proxy_agent_id` matches `agent_id` exactly. Any
    other agent lookup (e.g. the survivor calling the router itself)
    still enforces the active gate.
    """
    ctx = _PROXY_CTX.get()
    allow_decommissioned = bool(
        ctx is not None and ctx.get("proxy_agent_id") == agent_id
    )
    filter_sql = "" if allow_decommissioned else "AND status != 'decommissioned'"
    # Select every column the existing _caller copies read so no call
    # site needs a second SELECT. Cheap — agent_runs is small + hot.
    row = execute_one(
        f"SELECT agent_id, agent_type, plane, status FROM agent_runs "
        f"WHERE agent_id = %s {filter_sql}",
        (agent_id,),
    )
    if row is None:
        raise ValueError(f"Agent {agent_id} not found")
    return row

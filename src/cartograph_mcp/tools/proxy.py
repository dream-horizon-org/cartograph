"""Phase 4 proxy tools — get_my_proxy_items (read), act_on_proxy_item (router).

Survivors of absorb_agent inherit the decommissioned agents' pending
work indirectly: we do NOT create a proxy_items table. Instead, we walk
agent_runs.merged_into_agent_id upstream from every decommissioned agent
and keep any where the survivor is reachable. Then UNION pending items
across the five carrying tables filtered to those agents.

Separately, act_on_proxy_item is the generic router that lets a
survivor act on an inherited item by invoking the existing public tool
with actor=proxy_agent_id (the deactivated agent). shared.actor_auth's
ContextVar gates the decommissioned-active-check relaxation. All
ownership / state / content checks in the underlying tool still fire
normally — the proxy agent IS the original owner, so they pass.
Post-success, writes a proxy_audit row for the admin-UI badge join.
"""

from __future__ import annotations

import json

from shared.actor_auth import (
    require_active_agent,
    push_proxy_context,
    pop_proxy_context,
)
from shared.db import execute, execute_mutate, execute_one
from cartograph_mcp.tools import (
    broadcast as broadcast_tool,
    chat as chat_tool,
    clarification as clarification_tool,
    consolidation as consolidation_tool,
    tasks as tasks_tool,
)


_MAX_CHAIN_DEPTH = 10


# ==================== get_my_proxy_items ====================


def _proxied_agents(survivor_id: str) -> list[dict]:
    """Return the full transitive set of decommissioned agents whose
    merged_into chain terminates at `survivor_id`. Walks the pointer
    bottom-up (each decommissioned row has a merged_into pointer;
    recursively follow it). Bounded at _MAX_CHAIN_DEPTH to prevent
    pathological loops.

    Returns [{agent_id, deactivation_reason, deactivation_notes,
              merged_into_agent_id, depth}, ...] — depth=1 means
    directly absorbed by survivor; higher depths are chained merges.
    """
    rows = execute(
        """WITH RECURSIVE chain AS (
             SELECT agent_id, agent_type, status,
                    merged_into_agent_id,
                    deactivation_reason,
                    deactivation_notes,
                    1 AS depth
             FROM agent_runs
             WHERE status = 'decommissioned'
               AND merged_into_agent_id = %s
             UNION ALL
             SELECT a.agent_id, a.agent_type, a.status,
                    a.merged_into_agent_id,
                    a.deactivation_reason,
                    a.deactivation_notes,
                    c.depth + 1
             FROM agent_runs a
             JOIN chain c ON a.merged_into_agent_id = c.agent_id
             WHERE a.status = 'decommissioned'
               AND c.depth < %s
           )
           SELECT * FROM chain ORDER BY depth, agent_id""",
        (survivor_id, _MAX_CHAIN_DEPTH),
    )
    return rows


def get_my_proxy_items(
    agent_id: str,
    limit_per_type: int = 50,
    include_empty: bool = False,
) -> dict:
    """Return the survivor's full inherited work inbox.

    Response shape:
        {
          "proxied": [
            {
              "proxy_agent_id": "...",
              "deactivation_reason": "merged" | ...,
              "deactivation_notes": "...",
              "merged_into_agent_id": "...",    # next hop; may equal agent_id
              "depth": 1,                        # 1=direct, 2+=transitive
              "items": {
                "tasks":           [...],
                "chats":           [...],
                "consolidations":  [...],
                "clarifications":  [...],
                "broadcasts":      [...]
              }
            },
            ...
          ]
        }

    By default, empty-inbox proxy agents are omitted (keeps the survivor's
    wake-up inbox clean — no noise from dead agents with nothing to do).
    Pass `include_empty=True` to force the full transitive chain into the
    response regardless of inbox state — useful for audit/verification
    views (admin UI, demo scorecards) that need to see who-is-chained-to-who
    independent of pending work.
    """
    require_active_agent(agent_id)
    agents = _proxied_agents(agent_id)
    out: list[dict] = []
    for a in agents:
        pid = a["agent_id"]
        items = {
            "tasks": execute(
                """SELECT id, description, status, owner_agent_id, worker_agent_id,
                          blocker_detail, created_at, updated_at
                   FROM tasks
                   WHERE worker_agent_id = %s AND status IN ('BW','BO')
                   ORDER BY updated_at DESC LIMIT %s""",
                (pid, limit_per_type),
            ),
            "chats": execute(
                """SELECT id, from_agent, to_agent, text, created_at
                   FROM communications
                   WHERE to_agent = %s AND type = 'chat' AND acked_at IS NULL
                   ORDER BY created_at DESC LIMIT %s""",
                (pid, limit_per_type),
            ),
            # M and MD are excluded: those have an explicit mutation_assigned_to
            # actor (typically the survivor themselves acting DIRECTLY, not as
            # proxy). B1/B2/R are the inheritable ones — a thread was waiting
            # on the dead agent to respond.
            "consolidations": execute(
                """SELECT id, nomination_type, agent_a_id, agent_b_id,
                          component_a_id, component_b_id, status, updated_at
                   FROM consolidations
                   WHERE (agent_a_id = %s OR agent_b_id = %s)
                     AND status NOT IN ('D','F','M','MD')
                   ORDER BY updated_at DESC LIMIT %s""",
                (pid, pid, limit_per_type),
            ),
            "clarifications": execute(
                """SELECT id, asker_agent_id, responder_agent_id, status, updated_at
                   FROM clarifications
                   WHERE (asker_agent_id = %s OR responder_agent_id = %s)
                     AND status != 'CC'
                   ORDER BY updated_at DESC LIMIT %s""",
                (pid, pid, limit_per_type),
            ),
            "broadcasts": execute(
                """SELECT c.id, c.from_agent, c.to_agent_type, c.text, c.created_at
                   FROM communications c
                   WHERE c.type = 'broadcast'
                     AND (c.to_agent_type IS NULL
                          OR c.to_agent_type = (
                            SELECT agent_type FROM agent_runs WHERE agent_id = %s
                          ))
                     AND NOT EXISTS (
                       SELECT 1 FROM broadcast_acks ba
                       WHERE ba.communication_id = c.id AND ba.agent_id = %s
                     )
                   ORDER BY c.created_at DESC LIMIT %s""",
                (pid, pid, limit_per_type),
            ),
        }
        total = sum(len(v) for v in items.values())
        if total == 0 and not include_empty:
            continue
        out.append({
            "proxy_agent_id": pid,
            "deactivation_reason": a["deactivation_reason"],
            "deactivation_notes": a["deactivation_notes"],
            "merged_into_agent_id": a["merged_into_agent_id"],
            "depth": a["depth"],
            "items": items,
        })
    return {"proxied": out}


# ==================== act_on_proxy_item (router) ====================


def _resolve_proxy_chain(item_type: str, item_id: str, survivor_id: str) -> str:
    """Find the owner of `item_id` and verify survivor sits on its merge
    chain above. Returns the owner's agent_id (which is the proxy agent
    whose ID we invoke underlying tools as). Raises if:
      - item doesn't exist
      - item's owner is not decommissioned (no proxy needed)
      - survivor is not reachable via merged_into chain from the owner
    """
    # 1. Look up the item's owner per type.
    if item_type == "task":
        row = execute_one(
            "SELECT worker_agent_id AS owner FROM tasks WHERE id = %s", (item_id,)
        )
    elif item_type == "chat":
        row = execute_one(
            "SELECT to_agent AS owner FROM communications WHERE id = %s AND type = 'chat'",
            (item_id,),
        )
    elif item_type == "consolidation":
        # Either party's chain matters — we resolve to the party that IS
        # decommissioned (if both active, no proxy needed).
        c = execute_one(
            "SELECT agent_a_id, agent_b_id FROM consolidations WHERE id = %s",
            (item_id,),
        )
        if c is None:
            raise ValueError(f"consolidation {item_id} not found")
        for candidate in (c["agent_a_id"], c["agent_b_id"]):
            if candidate is None:
                continue
            a = execute_one(
                "SELECT status FROM agent_runs WHERE agent_id = %s", (candidate,)
            )
            if a and a["status"] == "decommissioned":
                row = {"owner": candidate}
                break
        else:
            raise ValueError(
                f"consolidation {item_id} has no decommissioned party — no proxy needed"
            )
    elif item_type == "clarification":
        c = execute_one(
            "SELECT asker_agent_id, responder_agent_id FROM clarifications WHERE id = %s",
            (item_id,),
        )
        if c is None:
            raise ValueError(f"clarification {item_id} not found")
        for candidate in (c["asker_agent_id"], c["responder_agent_id"]):
            a = execute_one(
                "SELECT status FROM agent_runs WHERE agent_id = %s", (candidate,)
            )
            if a and a["status"] == "decommissioned":
                row = {"owner": candidate}
                break
        else:
            raise ValueError(
                f"clarification {item_id} has no decommissioned party — no proxy needed"
            )
    elif item_type == "broadcast":
        # Broadcast's "owner" for ack purposes is the caller themselves —
        # but a proxy acks on behalf of a specific dead agent. Caller
        # needs to supply which proxy they're acting for via a separate
        # arg. We special-case this below.
        raise ValueError(
            "broadcast acks are scoped via the survivor+proxy pair directly "
            "— pass the proxy agent via payload['proxy_agent_id']"
        )
    else:
        raise ValueError(f"unknown item_type: {item_type}")

    if row is None or row.get("owner") is None:
        raise ValueError(f"{item_type} {item_id} not found or has no owner")
    owner = row["owner"]

    # 2. Owner must be decommissioned (else no proxy needed).
    owner_row = execute_one(
        "SELECT status, merged_into_agent_id FROM agent_runs WHERE agent_id = %s",
        (owner,),
    )
    if owner_row is None:
        raise ValueError(f"Owner agent {owner} not found")
    if owner_row["status"] != "decommissioned":
        raise ValueError(
            f"{item_type} {item_id} is owned by active agent {owner} — "
            "no proxy act needed; use the direct tool"
        )

    # 3. Walk owner.merged_into chain — survivor must be reachable.
    cursor = owner
    for _ in range(_MAX_CHAIN_DEPTH):
        cursor_row = execute_one(
            "SELECT merged_into_agent_id FROM agent_runs WHERE agent_id = %s",
            (cursor,),
        )
        if cursor_row is None or cursor_row["merged_into_agent_id"] is None:
            break
        cursor = cursor_row["merged_into_agent_id"]
        if cursor == survivor_id:
            return owner
    raise ValueError(
        f"Agent {survivor_id} is not a legal proxy for {item_type} {item_id} "
        f"(owner {owner} is not in survivor's merge chain)"
    )


# Dispatch: maps (item_type, action) to the UNDERLYING callable. The
# router sets the proxy ContextVar, calls this with actor=proxy_agent_id,
# and the ContextVar lets the underlying tool's _caller check pass.
def _dispatch_task_respond(proxy_agent_id: str, item_id: str, payload: dict):
    return tasks_tool.respond_task(
        proxy_agent_id, item_id,
        payload["message"], payload["new_status"],
        payload.get("blocker_detail"),
    )


def _dispatch_clarification_respond(proxy_agent_id: str, item_id: str, payload: dict):
    return clarification_tool.respond_clarification(
        proxy_agent_id, item_id,
        payload["message"], payload["new_status"],
    )


def _dispatch_consolidation_respond(proxy_agent_id: str, item_id: str, payload: dict):
    return consolidation_tool.respond_consolidation(
        proxy_agent_id, item_id,
        payload["confidence"], payload["message"], payload["new_status"],
    )


def _dispatch_chat_ack(proxy_agent_id: str, item_id: str, payload: dict):
    return chat_tool.ack_chats(proxy_agent_id, [item_id])


def _dispatch_chat_send(proxy_agent_id: str, _item_id: str, payload: dict):
    # `send` has no item_id — passing _item_id to keep the dispatch
    # signature uniform. Router validates this entry differently (see below).
    return chat_tool.send_chat(
        proxy_agent_id, payload["to_agent_id"], payload["message"]
    )


def _dispatch_broadcast_ack(proxy_agent_id: str, item_id: str, payload: dict):
    return broadcast_tool.ack_broadcast(proxy_agent_id, item_id)


_DISPATCH = {
    ("task",          "respond"): _dispatch_task_respond,
    ("clarification", "respond"): _dispatch_clarification_respond,
    ("consolidation", "respond"): _dispatch_consolidation_respond,
    ("chat",          "ack"):     _dispatch_chat_ack,
    ("chat",          "send"):    _dispatch_chat_send,
    ("broadcast",     "ack"):     _dispatch_broadcast_ack,
}


def act_on_proxy_item(
    survivor_id: str,
    item_type: str,
    item_id: str,
    action: str,
    payload: dict | None = None,
) -> dict:
    """Survivor invokes an existing public tool on behalf of a decommissioned
    agent they've inherited work from. The underlying tool is invoked with
    actor = proxy_agent_id — so its ownership / state / content checks all
    fire against the original owner (which passes, since they ARE the
    original owner). Only the active-check on the proxy agent is bypassed
    via shared.actor_auth's ContextVar.

    Dispatch is keyed on (item_type, action). Supported:
      - (task, respond), (clarification, respond), (consolidation, respond)
      - (chat, ack), (chat, send)
      - (broadcast, ack)

    Writes a proxy_audit row after the underlying call succeeds. The audit
    row powers the admin-UI "via <survivor>" badge.
    """
    require_active_agent(survivor_id)
    payload = payload or {}
    key = (item_type, action)
    if key not in _DISPATCH:
        raise ValueError(
            f"Unsupported proxy action: ({item_type!r}, {action!r}). "
            f"Supported: {sorted(_DISPATCH.keys())}"
        )

    # Resolve the proxy agent. For chat.send + broadcast.ack we take the
    # proxy agent directly from payload (these don't have a target item
    # owner to look up — they're "act-as" calls).
    if item_type == "chat" and action == "send":
        proxy_agent_id = payload.get("proxy_agent_id")
        if not proxy_agent_id:
            raise ValueError("chat.send via proxy requires payload['proxy_agent_id']")
        # Walk chain to verify survivor reachable from proxy.
        cursor = proxy_agent_id
        reached = False
        for _ in range(_MAX_CHAIN_DEPTH):
            r = execute_one(
                "SELECT merged_into_agent_id FROM agent_runs WHERE agent_id = %s",
                (cursor,),
            )
            if r is None or r["merged_into_agent_id"] is None:
                break
            cursor = r["merged_into_agent_id"]
            if cursor == survivor_id:
                reached = True
                break
        if not reached:
            raise ValueError(
                f"Agent {survivor_id} is not a legal proxy for {proxy_agent_id}"
            )
    elif item_type == "broadcast" and action == "ack":
        proxy_agent_id = payload.get("proxy_agent_id")
        if not proxy_agent_id:
            raise ValueError("broadcast.ack via proxy requires payload['proxy_agent_id']")
        cursor = proxy_agent_id
        reached = False
        for _ in range(_MAX_CHAIN_DEPTH):
            r = execute_one(
                "SELECT merged_into_agent_id FROM agent_runs WHERE agent_id = %s",
                (cursor,),
            )
            if r is None or r["merged_into_agent_id"] is None:
                break
            cursor = r["merged_into_agent_id"]
            if cursor == survivor_id:
                reached = True
                break
        if not reached:
            raise ValueError(
                f"Agent {survivor_id} is not a legal proxy for {proxy_agent_id}"
            )
    else:
        proxy_agent_id = _resolve_proxy_chain(item_type, item_id, survivor_id)

    token = push_proxy_context(
        proxy_agent_id=proxy_agent_id, survivor_id=survivor_id
    )
    try:
        result = _DISPATCH[key](proxy_agent_id, item_id, payload)
    finally:
        pop_proxy_context(token)

    # Audit trail — append-only.
    summary = {"payload_keys": sorted(payload.keys())}
    execute_mutate(
        """INSERT INTO proxy_audit
           (survivor_id, proxy_agent_id, item_type, item_id, action, payload_summary)
           VALUES (%s, %s, %s, %s, %s, %s::jsonb)""",
        (survivor_id, proxy_agent_id, item_type, str(item_id), action, json.dumps(summary)),
    )
    return {
        "survivor_id": survivor_id,
        "proxy_agent_id": proxy_agent_id,
        "item_type": item_type,
        "action": action,
        "result": result,
    }

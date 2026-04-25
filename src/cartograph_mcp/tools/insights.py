"""Phase 5.9: agent self-improvement loop — record_insight tool.

Agents call this when they discover a smart tactic, hit a prompt gap,
miss a tool they wish existed, or find an on-disk doc misleading.
Admin triages from the UI; "promoted" entries inform prompt + doc
updates over time.

Open to all active agents. Insertion is append-only from the agent
side; status flips happen through the admin UI triage path.
"""

import json

from shared.actor_auth import require_active_agent
from shared.db import execute, execute_returning, execute_one


_VALID_KINDS = {
    "prompt_gap",
    "tactic_win",
    "tool_gap",
    "doc_confusing",
    "workflow_friction",
}

_VALID_STATUSES = {"open", "investigating", "promoted", "wontfix"}


def record_insight(
    agent_id: str,
    kind: str,
    target: str,
    body: str,
    evidence: dict | None = None,
) -> dict:
    """Record an insight from any active agent.

    Args:
      agent_id: caller (must be active).
      kind: one of prompt_gap | tactic_win | tool_gap | doc_confusing |
            workflow_friction.
      target: what the insight is about — agent type, tool name, doc
              path, phase. Examples: 'sme.materialisation',
              'transfer_edges', 'TRIGGER-MANAGEMENT.md §1.1b'.
      body: the insight itself (what + why). Be specific.
      evidence: optional structured pointers — {task_ids, comm_ids,
                file_paths}. Helps triagers verify claims later.

    Returns the inserted row.
    """
    require_active_agent(agent_id)
    if kind not in _VALID_KINDS:
        raise ValueError(
            f"Invalid kind '{kind}'. Allowed: {sorted(_VALID_KINDS)}"
        )
    target = (target or "").strip()
    body = (body or "").strip()
    if not target:
        raise ValueError("target is required (what the insight is about)")
    if not body:
        raise ValueError("body is required")
    return execute_returning(
        """INSERT INTO agent_insights (agent_id, kind, target, body, evidence)
           VALUES (%s, %s, %s, %s, %s::jsonb)
           RETURNING *""",
        (agent_id, kind, target, body, json.dumps(evidence) if evidence else None),
    )


def list_insights(
    status: str | None = None,
    kind: str | None = None,
    target: str | None = None,
    agent_id: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """Admin-side query — used by the UI Insights tab. No agent_id auth
    here because this is admin-UI-facing; the MCP server doesn't expose
    this read tool to agents (insights are write-only from agent side
    to keep the channel clean)."""
    where = []
    params: list = []
    for col, val in [
        ("status", status), ("kind", kind),
        ("target", target), ("agent_id", agent_id),
    ]:
        if val:
            where.append(f"{col} = %s")
            params.append(val)
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    return execute(
        f"SELECT * FROM agent_insights{where_sql} "
        f"ORDER BY created_at DESC LIMIT %s",
        params + [limit],
    )


def triage_insight(
    insight_id: str, status: str, triaged_by: str, note: str | None = None
) -> dict:
    """Admin-only state flip. Called from the admin UI."""
    if status not in _VALID_STATUSES:
        raise ValueError(
            f"Invalid status '{status}'. Allowed: {sorted(_VALID_STATUSES)}"
        )
    row = execute_one(
        "SELECT id FROM agent_insights WHERE id = %s::uuid",
        (insight_id,),
    )
    if row is None:
        raise ValueError(f"Insight {insight_id} not found")
    return execute_returning(
        """UPDATE agent_insights
           SET status = %s, triaged_by = %s, triaged_at = now(),
               triage_note = %s
           WHERE id = %s::uuid
           RETURNING *""",
        (status, triaged_by, note, insight_id),
    )

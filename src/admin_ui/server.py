"""Admin Chat UI — FastAPI server.

Lightweight web UI for humans to chat with any agent. Reads/writes the
communications table directly via shared/db.py.
"""

import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from shared.db import execute, execute_one, execute_mutate, execute_returning

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


class SendChatBody(BaseModel):
    message: str = Field(..., min_length=1)


class AckBody(BaseModel):
    communication_ids: list[str]


class BroadcastBody(BaseModel):
    to_agent_type: str = Field(..., pattern="^(orchestrator|iterator|sme|resolver)$")
    message: str = Field(..., min_length=1)
    persistent: bool = False


class SleepBody(BaseModel):
    until: str = Field(..., min_length=1)           # ISO timestamp
    reason: str = Field(..., min_length=1)
    agent_ids: list[str] | None = None
    agent_type: str | None = Field(
        None, pattern="^(iterator|sme|resolver)$"   # orchestrator excluded
    )


class WakeBody(BaseModel):
    agent_ids: list[str] | None = None
    agent_type: str | None = Field(
        None, pattern="^(orchestrator|iterator|sme|resolver)$"
    )


_CLIENT_LOG_DIR = Path("/tmp/cartograph-logs")
_CLIENT_LOG_PATH = _CLIENT_LOG_DIR / "browser.log"


def _append_client_log(payload: dict[str, Any]) -> None:
    """Append one JSON line to /tmp/cartograph-logs/browser.log.

    Best-effort. Logger swallows write failures so client crash capture
    never affects the response. Each line is `{server_ts} {client_ts}
    {event} {detail_json}` for easy `grep` / `tail -f`.
    """
    try:
        _CLIENT_LOG_DIR.mkdir(parents=True, exist_ok=True)
        line = (
            f"{datetime.utcnow().isoformat()}Z "
            f"{payload.get('ts', '')} "
            f"{payload.get('event', 'unknown')} "
            f"{json.dumps(payload, default=str)}"
        )
        with _CLIENT_LOG_PATH.open("a") as f:
            f.write(line + "\n")
    except Exception:
        logger.exception("client log write failed")


def create_app() -> FastAPI:
    app = FastAPI(title="Cartograph Admin UI")

    # --- API ROUTES ---

    @app.post("/api/clientlog")
    async def client_log(req: Request):
        """Capture browser-side error / crash signals — window.error,
        unhandledrejection, webglcontextlost, beforeunload. Posted by
        app.js hooks. Appends one JSON line per event to
        /tmp/cartograph-logs/browser.log so the developer can
        `grep webglcontextlost /tmp/cartograph-logs/browser.log`.

        Why this exists: Chrome's GPU process can die mid-session
        (Exit code 5 → "GPU process crashed" → after 11 crashes Chrome
        blocklists WebGL entirely). Without this endpoint the developer
        has no on-disk evidence of WHEN the crash happened or what the
        graph state was at the time. Server can't see the browser; this
        is the only path.
        """
        try:
            body = await req.json()
        except Exception:
            body = {"raw": (await req.body()).decode("utf-8", "replace")}
        _append_client_log(body)
        return {"ok": True}

    @app.get("/api/agents")
    def list_agents(include_decommissioned: bool = Query(False)):
        """List agents ordered by type priority.

        Default excludes decommissioned. Pass `include_decommissioned=true`
        to also surface merged/absorbed agents (for the admin-UI proxy
        chain / lineage view). Deactivation columns are always returned
        for decommissioned rows so the UI can render the block inline.
        """
        where = "WHERE 1=1" if include_decommissioned else "WHERE ar.status != 'decommissioned'"
        # LEFT JOIN to the RCA reservation + component so SME rows carry
        # their managed component inline. Iterators/orch/resolver have no
        # component and come back with NULLs. Aggregating via DISTINCT ON
        # keeps it to one row per agent even if an SME somehow has
        # multiple (resource, component) rows.
        # resource_planes is the canonical "what plane(s) does this
        # agent's resource cover" — used by the FE to render G/C/T/D
        # plane symbols next to the component tag in the agent list.
        # Two-level query: inner picks the (component, resource) pair
        # via DISTINCT ON; outer aggregates resource planes per agent.
        rows = execute(
            f"""WITH base AS (
                  SELECT DISTINCT ON (ar.agent_id)
                       ar.agent_id, ar.agent_type, ar.status, ar.sleep_until,
                       ar.created_at, ar.deactivation_reason,
                       ar.deactivation_notes, ar.merged_into_agent_id,
                       c.id            AS component_id,
                       c.canonical_name AS component_canonical,
                       c.display_name   AS component_display,
                       c.status         AS component_status
                    FROM agent_runs ar
                    LEFT JOIN resource_component_agents rca ON rca.agent_id = ar.agent_id
                    LEFT JOIN components c ON c.id = rca.component_id
                    {where}
                    ORDER BY ar.agent_id,
                      CASE c.status WHEN 'active' THEN 0 WHEN 'deprecated' THEN 1 ELSE 2 END
                )
                SELECT base.*,
                       COALESCE(
                         ARRAY_AGG(DISTINCT r.plane) FILTER (WHERE r.plane IS NOT NULL),
                         ARRAY[]::text[]
                       ) AS resource_planes
                FROM base
                LEFT JOIN resource_component_agents rca2 ON rca2.agent_id = base.agent_id
                LEFT JOIN resources r ON r.id = rca2.resource_id
                GROUP BY base.agent_id, base.agent_type, base.status, base.sleep_until,
                         base.created_at, base.deactivation_reason,
                         base.deactivation_notes, base.merged_into_agent_id,
                         base.component_id, base.component_canonical,
                         base.component_display, base.component_status"""
        )
        # Re-sort by type priority after DISTINCT ON (which forced agent_id order).
        priority = {"orchestrator": 0, "resolver": 1, "sme": 2, "iterator": 3}
        rows.sort(key=lambda r: (priority.get(r["agent_type"], 99), r["created_at"]))
        return {"agents": rows}

    @app.get("/api/agent/{agent_id}/chain")
    def agent_chain(agent_id: str):
        """Walk merged_into_agent_id starting at `agent_id`. Returns the
        hop-by-hop lineage so the admin UI can render a merge-chain view
        like A → B → C (active). Bounded at depth 10."""
        chain = []
        cursor = agent_id
        for _ in range(10):
            row = execute_one(
                """SELECT agent_id, agent_type, status,
                          deactivation_reason, deactivation_notes,
                          merged_into_agent_id, created_at
                   FROM agent_runs WHERE agent_id = %s""",
                (cursor,),
            )
            if row is None:
                break
            chain.append(row)
            if row["merged_into_agent_id"] is None:
                break
            cursor = row["merged_into_agent_id"]
        return {"chain": chain}

    @app.get("/api/proxy_audit")
    def list_proxy_audit(
        item_type: Optional[str] = Query(None),
        item_id: Optional[str] = Query(None),
        survivor_id: Optional[str] = Query(None),
        limit: int = Query(100, ge=1, le=1000),
    ):
        """Proxy audit log query. Admin UI calls with (item_type, item_id)
        to join a decommissioned-author row with its "via <survivor>"
        badge. Filters AND together."""
        where = []
        params: list = []
        if item_type:
            where.append("item_type = %s")
            params.append(item_type)
        if item_id:
            where.append("item_id = %s")
            params.append(item_id)
        if survivor_id:
            where.append("survivor_id = %s")
            params.append(survivor_id)
        where_sql = (" WHERE " + " AND ".join(where)) if where else ""
        rows = execute(
            f"""SELECT * FROM proxy_audit{where_sql}
                ORDER BY created_at DESC
                LIMIT %s""",
            params + [limit],
        )
        return {"entries": rows}

    @app.get("/api/chat/{agent_id}")
    def get_chat(
        agent_id: str,
        before: Optional[str] = Query(None),
        limit: int = Query(20, ge=1, le=100),
    ):
        """Get paginated chat history. `before` is cursor for loading older messages."""
        if before:
            rows = execute(
                """SELECT * FROM communications
                   WHERE type = 'chat'
                     AND ((from_agent = %s AND to_agent = 'admin')
                       OR (from_agent = 'admin' AND to_agent = %s))
                     AND created_at < %s
                   ORDER BY created_at DESC
                   LIMIT %s""",
                (agent_id, agent_id, before, limit + 1),
            )
        else:
            rows = execute(
                """SELECT * FROM communications
                   WHERE type = 'chat'
                     AND ((from_agent = %s AND to_agent = 'admin')
                       OR (from_agent = 'admin' AND to_agent = %s))
                   ORDER BY created_at DESC
                   LIMIT %s""",
                (agent_id, agent_id, limit + 1),
            )

        has_more = len(rows) > limit
        rows = rows[:limit]
        # Reverse to ascending order for display
        rows = list(reversed(rows))
        return {"messages": rows, "has_more": has_more}

    @app.get("/api/chat/{agent_id}/new")
    def get_new_messages(agent_id: str, after: str = Query(...)):
        """Polling: get messages newer than `after` timestamp."""
        rows = execute(
            """SELECT * FROM communications
               WHERE type = 'chat'
                 AND ((from_agent = %s AND to_agent = 'admin')
                   OR (from_agent = 'admin' AND to_agent = %s))
                 AND created_at > %s
               ORDER BY created_at""",
            (agent_id, agent_id, after),
        )
        return {"messages": rows}

    @app.post("/api/chat/{agent_id}")
    def send_chat(agent_id: str, body: SendChatBody):
        """Admin sends a message to an agent.

        Phase 5.8: also clears `sleep_until` so admin chat wakes a
        sleeping agent — same semantic the MCP `send_chat` tool gives
        agents. The admin UI bypasses MCP for this write (HLD §10.3),
        so the wake side-effect has to live here too.
        """
        # Validate agent exists
        agent = execute_one(
            "SELECT agent_id FROM agent_runs WHERE agent_id = %s AND status != 'decommissioned'",
            (agent_id,),
        )
        if agent is None:
            raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found")

        if not body.message.strip():
            raise HTTPException(status_code=400, detail="Message cannot be empty")

        row = execute_returning(
            """INSERT INTO communications (from_agent, to_agent, type, text)
               VALUES ('admin', %s, 'chat', %s)
               RETURNING *""",
            (agent_id, body.message),
        )
        # Wake the agent if it was sleeping. Awake agents see no change.
        execute_mutate(
            "UPDATE agent_runs SET sleep_until = NULL "
            "WHERE agent_id = %s AND sleep_until IS NOT NULL",
            (agent_id,),
        )
        return row

    @app.post("/api/chat/{agent_id}/ack")
    def ack_messages(agent_id: str, body: AckBody):
        """Admin acks agent-to-admin messages (read receipts)."""
        if not body.communication_ids:
            return {"acked": 0}

        acked = execute_mutate(
            """UPDATE communications
               SET acked_at = now()
               WHERE id = ANY(%s::uuid[])
                 AND type = 'chat'
                 AND from_agent = %s
                 AND to_agent = 'admin'
                 AND acked_at IS NULL""",
            (body.communication_ids, agent_id),
        )
        return {"acked": acked}

    # --- COMMUNICATIONS PANEL (Phase 2.4) ---

    _VALID_COMM_TYPES = {"chat", "broadcast", "task", "consolidation", "clarification"}

    _VALID_AGENT_TYPES = {"orchestrator", "iterator", "sme", "resolver", "admin"}

    @app.get("/api/communications")
    def list_communications(
        from_agent: Optional[str] = Query(None),
        to_agent: Optional[str] = Query(None),
        from_agent_type: Optional[str] = Query(None),
        to_agent_type: Optional[str] = Query(None),
        agent: Optional[str] = Query(None),
        agent_type: Optional[str] = Query(None),
        participant_component: Optional[str] = Query(None),
        type: Optional[str] = Query(None),
        source_id: Optional[str] = Query(None),
        before: Optional[str] = Query(None),
        limit: int = Query(50, ge=1, le=200),
    ):
        """Filtered communications feed for the admin UI panel.

        All filters AND together. `before` is a created_at cursor for older
        pages. Returns newest-first with a has_more flag.

        Filter semantics:
          from_agent / to_agent  — exact agent_id match
                                   (to_agent only matches point-to-point rows;
                                   broadcasts have to_agent IS NULL)
          from_agent_type        — joins agent_runs to resolve from_agent's
                                   type. 'admin' matches from_agent='admin'.
          to_agent_type          — matches either to_agent_type column
                                   (broadcasts) OR agent_runs.agent_type of
                                   whoever to_agent points at (point-to-point).
          agent                  — participant filter: from_agent = X
                                   OR to_agent = X (either direction).
          agent_type             — participant-type filter: the type-wide
                                   union of (from_agent_type = T OR
                                   to_agent_type = T OR to_agent's type = T).
          type                   — communication type (chat/broadcast/...)
        """
        if type is not None and type not in _VALID_COMM_TYPES:
            raise HTTPException(400, f"Invalid type (allowed: {sorted(_VALID_COMM_TYPES)})")
        if from_agent_type and from_agent_type not in _VALID_AGENT_TYPES:
            raise HTTPException(400, f"Invalid from_agent_type (allowed: {sorted(_VALID_AGENT_TYPES)})")
        if to_agent_type and to_agent_type not in _VALID_AGENT_TYPES:
            raise HTTPException(400, f"Invalid to_agent_type (allowed: {sorted(_VALID_AGENT_TYPES)})")
        if agent_type and agent_type not in _VALID_AGENT_TYPES:
            raise HTTPException(400, f"Invalid agent_type (allowed: {sorted(_VALID_AGENT_TYPES)})")

        where = []
        params: list = []
        if from_agent:
            where.append("c.from_agent = %s")
            params.append(from_agent)
        if to_agent:
            where.append("c.to_agent = %s")
            params.append(to_agent)
        if from_agent_type:
            # 'admin' matches the pseudo-agent literally; others join agent_runs.
            if from_agent_type == "admin":
                where.append("c.from_agent = 'admin'")
            else:
                where.append(
                    "c.from_agent IN (SELECT agent_id FROM agent_runs "
                    "WHERE agent_type = %s)"
                )
                params.append(from_agent_type)
        if to_agent_type:
            # Match either the direct broadcast column OR the resolved type
            # of the agent_id in to_agent. 'admin' → literal match.
            if to_agent_type == "admin":
                where.append("c.to_agent = 'admin'")
            else:
                where.append(
                    "("
                    "  c.to_agent_type = %s"
                    "  OR c.to_agent IN (SELECT agent_id FROM agent_runs "
                    "                    WHERE agent_type = %s)"
                    ")"
                )
                params.extend([to_agent_type, to_agent_type])
        if agent:
            # Participant: sender OR recipient exact agent_id match.
            where.append("(c.from_agent = %s OR c.to_agent = %s)")
            params.extend([agent, agent])
        if agent_type:
            # Participant by type: from side OR to side OR broadcast column.
            if agent_type == "admin":
                where.append("(c.from_agent = 'admin' OR c.to_agent = 'admin')")
            else:
                where.append(
                    "("
                    "  c.from_agent IN (SELECT agent_id FROM agent_runs WHERE agent_type = %s)"
                    "  OR c.to_agent IN (SELECT agent_id FROM agent_runs WHERE agent_type = %s)"
                    "  OR c.to_agent_type = %s"
                    ")"
                )
                params.extend([agent_type, agent_type, agent_type])
        if type:
            where.append("c.type = %s")
            params.append(type)
        if source_id:
            where.append("c.source_id = %s::uuid")
            params.append(source_id)
        if before:
            where.append("c.created_at < %s")
            params.append(before)
        if participant_component:
            # Phase 7.4.9: filter rows where EITHER side's agent owns a
            # component whose canonical_name OR display_name matches
            # ILIKE %query%. Sub-query joins agent_runs → RCA → components.
            # Includes decommissioned agents + decommissioned components
            # so the filter works for historical traffic too. The
            # 'admin' literal is excluded naturally (no RCA row).
            like = f"%{participant_component}%"
            where.append(
                "("
                "  c.from_agent IN ("
                "    SELECT rca.agent_id FROM resource_component_agents rca "
                "    JOIN components co ON co.id = rca.component_id "
                "    WHERE co.canonical_name ILIKE %s "
                "       OR co.display_name ILIKE %s"
                "  )"
                "  OR c.to_agent IN ("
                "    SELECT rca.agent_id FROM resource_component_agents rca "
                "    JOIN components co ON co.id = rca.component_id "
                "    WHERE co.canonical_name ILIKE %s "
                "       OR co.display_name ILIKE %s"
                "  )"
                ")"
            )
            params.extend([like, like, like, like])
        where_sql = (" WHERE " + " AND ".join(where)) if where else ""

        rows = execute(
            f"""SELECT c.* FROM communications c{where_sql}
                ORDER BY c.created_at DESC
                LIMIT %s""",
            params + [limit + 1],
        )
        has_more = len(rows) > limit
        return {"messages": rows[:limit], "has_more": has_more}

    @app.get("/api/task/{task_id}")
    def get_task_detail(task_id: str):
        """Task row + paginated thread — for the right-side panel."""
        task = execute_one("SELECT * FROM tasks WHERE id = %s::uuid", (task_id,))
        if task is None:
            raise HTTPException(404, f"Task {task_id} not found")
        thread = execute(
            """SELECT * FROM communications
               WHERE type = 'task' AND source_id = %s::uuid
               ORDER BY created_at""",
            (task_id,),
        )
        return {"task": task, "thread": thread}

    @app.get("/api/consolidation/{consolidation_id}")
    def get_consolidation_detail(consolidation_id: str):
        """Placeholder until Phase 3 consolidation tools land."""
        row = execute_one(
            "SELECT * FROM consolidations WHERE id = %s::uuid",
            (consolidation_id,),
        )
        if row is None:
            raise HTTPException(404, "Consolidation not found")
        thread = execute(
            """SELECT * FROM communications
               WHERE type = 'consolidation' AND source_id = %s::uuid
               ORDER BY created_at""",
            (consolidation_id,),
        )
        return {"consolidation": row, "thread": thread}

    @app.get("/api/clarification/{clarification_id}")
    def get_clarification_detail(clarification_id: str):
        """Placeholder until Phase 3 clarification tools land."""
        row = execute_one(
            "SELECT * FROM clarifications WHERE id = %s::uuid",
            (clarification_id,),
        )
        if row is None:
            raise HTTPException(404, "Clarification not found")
        thread = execute(
            """SELECT * FROM communications
               WHERE type = 'clarification' AND source_id = %s::uuid
               ORDER BY created_at""",
            (clarification_id,),
        )
        return {"clarification": row, "thread": thread}

    # --- MCP AUDIT (Phase 5.10) ---

    @app.get("/api/mcp_audit")
    def list_mcp_audit(
        agent_id: Optional[str] = Query(None),
        tool_name: Optional[str] = Query(None),
        result_status: Optional[str] = Query(None),
        limit: int = Query(200, ge=1, le=2000),
    ):
        """Per-agent / per-tool activity timeline."""
        where = []
        params: list = []
        if agent_id:
            where.append("agent_id = %s")
            params.append(agent_id)
        if tool_name:
            where.append("tool_name = %s")
            params.append(tool_name)
        if result_status:
            where.append("result_status = %s")
            params.append(result_status)
        where_sql = (" WHERE " + " AND ".join(where)) if where else ""
        rows = execute(
            f"SELECT * FROM mcp_audit{where_sql} "
            f"ORDER BY created_at DESC LIMIT %s",
            params + [limit],
        )
        return {"entries": rows}

    # --- INSIGHTS (Phase 5.9) ---

    class TriageBody(BaseModel):
        status: str = Field(..., pattern="^(open|investigating|promoted|wontfix)$")
        triage_note: Optional[str] = None

    @app.get("/api/insights")
    def list_insights_endpoint(
        status: Optional[str] = Query(None),
        kind: Optional[str] = Query(None),
        target: Optional[str] = Query(None),
        agent_id: Optional[str] = Query(None),
        limit: int = Query(100, ge=1, le=500),
    ):
        from cartograph_mcp.tools import insights as _ins
        return {"insights": _ins.list_insights(
            status=status, kind=kind, target=target, agent_id=agent_id, limit=limit,
        )}

    @app.post("/api/insight/{insight_id}/triage")
    def triage_insight_endpoint(insight_id: str, body: TriageBody):
        from cartograph_mcp.tools import insights as _ins
        try:
            return _ins.triage_insight(
                insight_id, body.status, "admin", body.triage_note
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    # --- ENTITIES VIEW (Phase 5.2) ---
    #
    # Naming convention (Phase 5.12 cleanup): 'type' is the canonical
    # term across BOTH Communications (communications.type) and Entities
    # (the row's entity-type). The earlier 'kind' parameter was
    # internally consistent but conflicted visually with Communications'
    # 'type', so it has been renamed throughout. The query param + JSON
    # response field are both `type`. URL path stays /entity/{type}/{id}.

    _ENTITY_TYPES = {"task", "consolidation", "clarification", "broadcast"}
    # Per-type terminal-state set — drives the "open / closed" filter
    # without forcing the FE to know each state machine's terminals.
    _TERMINAL_BY_TYPE = {
        "task": {"TC"},
        "consolidation": {"D", "F"},
        "clarification": {"CC", "QR"},
        "broadcast": set(),  # broadcasts have no terminal status
    }

    @app.get("/api/entities")
    def list_entities(
        type: Optional[str] = Query(None),
        status: Optional[str] = Query(None),
        participant: Optional[str] = Query(None),
        q: Optional[str] = Query(None),
        open_only: bool = Query(False),
        before: Optional[str] = Query(None),
        limit: int = Query(50, ge=1, le=200),
    ):
        """Workflow-entity feed for the Entities tab.

        Each row is one task / consolidation / clarification / broadcast,
        normalised to a common shape so the FE can render a uniform list.
        Filters AND together. Sort: most-recent activity first.
        Cursor pagination via `before` (ISO timestamp on `last_activity`).
        Response shape: {entities: [{type, id, status, participant_a,
        participant_b, summary, last_activity, extra}], has_more}.
        """
        kind = type  # local alias to keep the body readable
        if kind is not None and kind not in _ENTITY_TYPES:
            raise HTTPException(
                400, f"Invalid type (allowed: {sorted(_ENTITY_TYPES)})"
            )

        # Per-type SELECTs, each producing the common columns:
        # kind, id, status, participant_a, participant_b, summary, last_activity, extra
        parts = []
        params: list = []
        if kind in (None, "task"):
            parts.append(
                "SELECT 'task' AS type, t.id::text AS id, t.status, "
                "       t.owner_agent_id AS participant_a, "
                "       t.worker_agent_id AS participant_b, "
                "       LEFT(t.description, 200) AS summary, "
                "       t.updated_at AS last_activity, "
                "       jsonb_build_object('blocker_detail', t.blocker_detail) AS extra "
                "FROM tasks t"
            )
        if kind in (None, "consolidation"):
            parts.append(
                "SELECT 'consolidation' AS type, c.id::text AS id, c.status, "
                "       c.agent_a_id AS participant_a, "
                "       c.agent_b_id AS participant_b, "
                "       (c.nomination_type || ' ' || c.component_a_id::text) AS summary, "
                "       c.updated_at AS last_activity, "
                "       jsonb_build_object("
                "         'nomination_type', c.nomination_type, "
                "         'component_a_id', c.component_a_id, "
                "         'component_b_id', c.component_b_id, "
                "         'a_conf', c.a_conf_score, "
                "         'b_conf', c.b_conf_score, "
                "         'r_conf', c.r_conf_score, "
                "         'mutation_assigned_to', c.mutation_assigned_to "
                "       ) AS extra "
                "FROM consolidations c"
            )
        if kind in (None, "clarification"):
            parts.append(
                "SELECT 'clarification' AS type, cl.id::text AS id, cl.status, "
                "       cl.asker_agent_id AS participant_a, "
                "       cl.responder_agent_id AS participant_b, "
                "       NULL AS summary, "
                "       cl.updated_at AS last_activity, "
                "       '{}'::jsonb AS extra "
                "FROM clarifications cl"
            )
        if kind in (None, "broadcast"):
            parts.append(
                "SELECT 'broadcast' AS type, b.id::text AS id, "
                "       CASE WHEN b.is_persistent THEN 'persistent' "
                "            ELSE 'forward-only' END AS status, "
                "       b.from_agent AS participant_a, "
                "       b.to_agent_type AS participant_b, "
                "       LEFT(b.text, 200) AS summary, "
                "       b.created_at AS last_activity, "
                "       jsonb_build_object('is_persistent', b.is_persistent) AS extra "
                "FROM communications b WHERE b.type = 'broadcast'"
            )

        union_sql = " UNION ALL ".join(parts)
        # Outer filters (status / participant / q / open_only / before).
        outer_where = []
        if status:
            outer_where.append("status = %s")
            params.append(status)
        if open_only:
            # Build NOT-terminal conditions per-type. If type is filtered,
            # use only that type's terminals; otherwise OR across all.
            if kind:
                terminals = _TERMINAL_BY_TYPE.get(kind, set())
                if terminals:
                    placeholder = ", ".join(["%s"] * len(terminals))
                    outer_where.append(f"status NOT IN ({placeholder})")
                    params.extend(terminals)
            else:
                # Compose: NOT (type=T AND status IN (terminals_T)) per type
                clauses = []
                for t, terms in _TERMINAL_BY_TYPE.items():
                    if not terms:
                        continue
                    placeholder = ", ".join(["%s"] * len(terms))
                    clauses.append(
                        f"NOT (type = %s AND status IN ({placeholder}))"
                    )
                    params.append(t)
                    params.extend(terms)
                if clauses:
                    outer_where.append("(" + " AND ".join(clauses) + ")")
        if participant:
            outer_where.append(
                "(participant_a = %s OR participant_b = %s)"
            )
            params.extend([participant, participant])
        if q:
            outer_where.append("summary ILIKE %s")
            params.append(f"%{q}%")
        if before:
            outer_where.append("last_activity < %s")
            params.append(before)
        outer_sql = (
            " WHERE " + " AND ".join(outer_where)
        ) if outer_where else ""

        rows = execute(
            f"SELECT * FROM ({union_sql}) AS e{outer_sql} "
            f"ORDER BY last_activity DESC NULLS LAST LIMIT %s",
            params + [limit + 1],
        )
        has_more = len(rows) > limit
        return {"entities": rows[:limit], "has_more": has_more}

    @app.get("/api/entity/{type}/{entity_id}")
    def get_entity_detail(type: str, entity_id: str):
        """Unified drill-down. Dispatches per-type and normalises shape:
        {type, entity, thread, extras?}. Existing per-kind endpoints
        (/api/task/:id etc.) stay as-is for backwards compatibility."""
        kind = type
        if kind not in _ENTITY_TYPES:
            raise HTTPException(400, f"Invalid type: {kind}")
        if kind == "task":
            t = execute_one("SELECT * FROM tasks WHERE id = %s::uuid", (entity_id,))
            if t is None:
                raise HTTPException(404, "Task not found")
            thread = execute(
                "SELECT * FROM communications WHERE type = 'task' "
                "AND source_id = %s::uuid ORDER BY created_at",
                (entity_id,),
            )
            return {"type": "task", "entity": t, "thread": thread}
        if kind == "consolidation":
            c = execute_one(
                "SELECT * FROM consolidations WHERE id = %s::uuid",
                (entity_id,),
            )
            if c is None:
                raise HTTPException(404, "Consolidation not found")
            thread = execute(
                "SELECT * FROM communications WHERE type = 'consolidation' "
                "AND source_id = %s::uuid ORDER BY created_at",
                (entity_id,),
            )
            return {"type": "consolidation", "entity": c, "thread": thread}
        if kind == "clarification":
            cl = execute_one(
                "SELECT * FROM clarifications WHERE id = %s::uuid",
                (entity_id,),
            )
            if cl is None:
                raise HTTPException(404, "Clarification not found")
            thread = execute(
                "SELECT * FROM communications WHERE type = 'clarification' "
                "AND source_id = %s::uuid ORDER BY created_at",
                (entity_id,),
            )
            return {"type": "clarification", "entity": cl, "thread": thread}
        if kind == "broadcast":
            b = execute_one(
                "SELECT * FROM communications WHERE id = %s::uuid "
                "AND type = 'broadcast'",
                (entity_id,),
            )
            if b is None:
                raise HTTPException(404, "Broadcast not found")
            acks = execute(
                "SELECT * FROM broadcast_acks WHERE communication_id = %s::uuid "
                "ORDER BY acked_at",
                (entity_id,),
            )
            return {"type": "broadcast", "entity": b, "thread": [], "extras": {"acks": acks}}
        raise HTTPException(400, f"Unsupported type: {kind}")  # unreachable

    # --- CATALOG VIEW (Phase 5.3) ---

    _COMPONENT_TYPES = {
        "application", "database", "cache", "queue", "lambda",
        "cron", "external-service", "library", "infrastructure",
    }
    _COMPONENT_STATUSES = {"active", "deprecated", "decommissioned"}
    _PLANES = {"github", "deploy", "cloud", "telemetry", "config"}

    @app.get("/api/components")
    def list_components(
        type: Optional[str] = Query(None),
        plane: Optional[str] = Query(None),
        status: Optional[str] = Query(None),
        q: Optional[str] = Query(None),
        before: Optional[str] = Query(None),
        limit: int = Query(100, ge=1, le=500),
    ):
        """Paginated component list for the Catalog tab.

        Each row carries plane set + per-kind edge counts so the FE can
        render the row without a follow-up. Default sort: canonical_name
        ASC. Pagination via `before` cursor on canonical_name.
        """
        if type and type not in _COMPONENT_TYPES:
            raise HTTPException(400, f"Invalid type")
        if plane and plane not in _PLANES:
            raise HTTPException(400, f"Invalid plane")
        if status and status not in _COMPONENT_STATUSES:
            raise HTTPException(400, f"Invalid status")

        where = []
        params: list = []
        if type:
            where.append("c.component_type = %s")
            params.append(type)
        if status:
            where.append("c.status = %s")
            params.append(status)
        if q:
            where.append("(c.canonical_name ILIKE %s OR c.display_name ILIKE %s)")
            params.extend([f"%{q}%", f"%{q}%"])
        if before:
            where.append("c.canonical_name > %s")
            params.append(before)
        # Plane filter sources from RCA→resources.plane (canonical:
        # what plane the component LIVES on, not where evidence was
        # discovered). attributions.plane is discovery-plane and would
        # mis-classify (e.g. github SME finding a hostname tags
        # plane=github even though the hostname "feels" deploy-y).
        if plane:
            where.append(
                "EXISTS (SELECT 1 FROM resource_component_agents rca_p "
                "JOIN resources r_p ON r_p.id = rca_p.resource_id "
                "WHERE rca_p.component_id = c.id AND r_p.plane = %s)"
            )
            params.append(plane)
        where_sql = (" WHERE " + " AND ".join(where)) if where else ""

        # planes column sources from RCA→resources.plane (see plane
        # filter above for rationale). attribution_count keeps its own
        # join since it's a count, not a plane-derived value.
        rows = execute(
            f"""SELECT c.id, c.canonical_name, c.display_name,
                       c.component_type, c.status,
                       COALESCE(
                         ARRAY_AGG(DISTINCT r.plane) FILTER (WHERE r.plane IS NOT NULL),
                         ARRAY[]::text[]
                       ) AS planes,
                       COUNT(DISTINCT a.id) AS attribution_count,
                       COUNT(DISTINCT e_bound.id) FILTER (
                         WHERE e_bound.from_component_id IS NOT NULL
                           AND e_bound.to_component_id IS NOT NULL
                       ) AS bound_count,
                       COUNT(DISTINCT cat.id) AS catalog_count,
                       COUNT(DISTINCT e_dang.id) FILTER (
                         WHERE e_dang.to_component_id IS NULL
                           AND e_dang.from_component_id = c.id
                       ) AS dangling_count,
                       (
                         SELECT rca2.agent_id FROM resource_component_agents rca2
                         WHERE rca2.component_id = c.id LIMIT 1
                       ) AS owner_sme_id
                FROM components c
                LEFT JOIN attributions a ON a.component_id = c.id
                LEFT JOIN edges e_bound
                  ON (e_bound.from_component_id = c.id OR e_bound.to_component_id = c.id)
                LEFT JOIN catalogs cat ON cat.component_id = c.id
                LEFT JOIN edges e_dang ON e_dang.from_component_id = c.id
                LEFT JOIN resource_component_agents rca ON rca.component_id = c.id
                LEFT JOIN resources r ON r.id = rca.resource_id
                {where_sql}
                GROUP BY c.id
                ORDER BY c.canonical_name ASC
                LIMIT %s""",
            params + [limit + 1],
        )
        has_more = len(rows) > limit
        rows = rows[:limit]
        # Reshape edge counts into a single nested dict so FE consumes
        # one field rather than three siblings.
        for r in rows:
            r["edge_count"] = {
                "bound": r.pop("bound_count", 0),
                "catalog": r.pop("catalog_count", 0),
                "dangling": r.pop("dangling_count", 0),
            }
        return {"components": rows, "has_more": has_more}

    @app.get("/api/component/{component_id}/drilldown")
    def get_component_drilldown(component_id: str):
        """Single round-trip for the Catalog drill-down panel: component
        row + attributions + edges (split into 4 buckets per Phase 3.9
        protocol) + flows + source resources."""
        # planes sourced from RCA→resources.plane (canonical: what
        # plane the component LIVES on). attributions.plane is
        # discovery-plane and conflates source-of-evidence with
        # category-of-component.
        component = execute_one(
            """SELECT c.*,
                      COALESCE(
                        ARRAY_AGG(DISTINCT r.plane) FILTER (WHERE r.plane IS NOT NULL),
                        ARRAY[]::text[]
                      ) AS planes
               FROM components c
               LEFT JOIN resource_component_agents rca ON rca.component_id = c.id
               LEFT JOIN resources r ON r.id = rca.resource_id
               WHERE c.id = %s::uuid
               GROUP BY c.id""",
            (component_id,),
        )
        if component is None:
            raise HTTPException(404, "Component not found")
        attributions = execute(
            """SELECT id, plane, resource_type, identifier, evidence,
                      confidence, metadata
               FROM attributions WHERE component_id = %s::uuid
               ORDER BY plane, resource_type, identifier""",
            (component_id,),
        )
        # Phase 7.4.2: edges table only carries bound + dangling rows.
        # Catalogs are sourced from the `catalogs` table separately and
        # reshaped into the legacy edge-row shape (id, target_id=
        # component_id, edge_type derived from kind, kind='catalog') so
        # FE consumers don't need a payload-shape change.
        all_edges = execute(
            """SELECT id, from_component_id, to_component_id,
                      edge_type, identifier, confidence, metadata,
                      CASE
                        WHEN from_component_id IS NOT NULL
                             AND to_component_id IS NOT NULL THEN 'bound'
                        ELSE 'dangling'
                      END AS kind
               FROM edges
               WHERE from_component_id = %s::uuid OR to_component_id = %s::uuid""",
            (component_id, component_id),
        )
        catalog_rows = execute(
            """SELECT id, NULL::uuid AS from_component_id,
                      component_id AS to_component_id,
                      CASE kind
                        WHEN 'endpoint'       THEN 'calls'
                        WHEN 'topic'          THEN 'publishes_to'
                        WHEN 'queue'          THEN 'consumes_from'
                        WHEN 'data_source'    THEN 'reads_from'
                        WHEN 'trigger_target' THEN 'triggers'
                      END AS edge_type,
                      identifier, confidence, metadata,
                      kind AS catalog_kind,
                      'catalog' AS kind
               FROM catalogs
               WHERE component_id = %s::uuid
               ORDER BY kind, identifier""",
            (component_id,),
        )
        edges = {
            "bound_in":      [e for e in all_edges
                              if e["kind"] == "bound" and str(e["to_component_id"]) == component_id],
            "bound_out":     [e for e in all_edges
                              if e["kind"] == "bound" and str(e["from_component_id"]) == component_id],
            "catalog":       catalog_rows,
            "dangling_out":  [e for e in all_edges if e["kind"] == "dangling"],
        }
        flows = execute(
            """SELECT id, incoming_catalog_id, outgoing_edge_id, confidence
               FROM flows WHERE component_id = %s::uuid""",
            (component_id,),
        )
        resources = execute(
            """SELECT r.id, r.plane, r.resource_type, r.identifier, r.status
               FROM resource_component_agents rca
               JOIN resources r ON r.id = rca.resource_id
               WHERE rca.component_id = %s::uuid""",
            (component_id,),
        )
        return {
            "component": component,
            "attributions": attributions,
            "edges": edges,
            "flows": flows,
            "resources": resources,
        }

    # --- GRAPH VIEW (Phase 3.5) ---

    @app.get("/api/graph")
    def get_graph():
        """Nodes = active components. Edges = edges table (all kinds).
        Flows = flows table, for the 3.10 line-of-sight BFS.

        Phase 3.10: edges payload discriminated by `kind`:
          bound     — from+to both set (renders solid, directional)
          catalog   — from IS NULL  (renders as dashed stub at callee)
          dangling  — to IS NULL    (renders as dashed stub at caller)

        Each node carries its plane set, component_doc_md, and
        source_slice so the FE renders hover popups without a second
        round-trip. source_id/target_id aliased to from_component_id/
        to_component_id for the 3d-force-graph link source/target
        convention; NULL endpoints anchor to a synthetic "stub" node
        on the FE side (handled client-side).
        """
        # planes sourced from RCA→resources.plane (canonical: what
        # plane the component LIVES on). See /api/components for
        # rationale.
        nodes = execute(
            """SELECT c.id, c.canonical_name, c.display_name,
                      c.component_type, c.status, c.component_doc_md,
                      c.source_slice,
                      COALESCE(
                        ARRAY_AGG(DISTINCT r.plane) FILTER (WHERE r.plane IS NOT NULL),
                        ARRAY[]::text[]
                      ) AS planes
               FROM components c
               LEFT JOIN resource_component_agents rca ON rca.component_id = c.id
               LEFT JOIN resources r ON r.id = rca.resource_id
               WHERE c.status != 'decommissioned'
               GROUP BY c.id
               ORDER BY c.canonical_name"""
        )
        # Phase 7.4: catalogs live in their own table now. UNION them
        # into the edges payload with kind='catalog' + a derived
        # edge_type so the FE Graph + Globe code (which expects the
        # legacy three-kind discriminator) keeps working without
        # a re-shape.
        edges = execute(
            """SELECT e.id::text AS id,
                      e.from_component_id::text AS source_id,
                      e.to_component_id::text   AS target_id,
                      e.edge_type, e.identifier, e.confidence, e.metadata,
                      CASE
                        WHEN e.from_component_id IS NOT NULL
                             AND e.to_component_id IS NOT NULL THEN 'bound'
                        ELSE 'dangling'
                      END AS kind
               FROM edges e
               LEFT JOIN components cs ON cs.id = e.from_component_id
               LEFT JOIN components ct ON ct.id = e.to_component_id
               WHERE (cs.id IS NULL OR cs.status != 'decommissioned')
                 AND (ct.id IS NULL OR ct.status != 'decommissioned')
               UNION ALL
               SELECT c.id::text,
                      NULL AS source_id,
                      c.component_id::text AS target_id,
                      CASE c.kind
                        WHEN 'endpoint'       THEN 'calls'
                        WHEN 'topic'          THEN 'publishes_to'
                        WHEN 'queue'          THEN 'consumes_from'
                        WHEN 'data_source'    THEN 'reads_from'
                        WHEN 'trigger_target' THEN 'triggers'
                      END AS edge_type,
                      c.identifier, c.confidence, c.metadata,
                      'catalog' AS kind
               FROM catalogs c
               JOIN components comp ON comp.id = c.component_id
                AND comp.status != 'decommissioned'"""
        )
        flows = execute(
            """SELECT f.id, f.component_id,
                      f.incoming_catalog_id, f.outgoing_edge_id,
                      f.confidence
               FROM flows f
               JOIN components c ON c.id = f.component_id
                 AND c.status != 'decommissioned'"""
        )
        return {"nodes": nodes, "edges": edges, "flows": flows}

    @app.post("/api/backfill_embeddings")
    def backfill_embeddings_endpoint():
        """Re-embed rows whose embedding column is NULL across all 4 vector
        tables. Idempotent: rows already embedded are skipped by the
        `WHERE embedding IS NULL` filter. Returns per-table counts.

        Run this after a model / dim change, or when rows were written
        while Ollama was unreachable.
        """
        from shared.embedding_backfill import backfill_all
        return backfill_all()

    class PersistenceBody(BaseModel):
        persistent: bool

    @app.post("/api/broadcast/{communication_id}/persistence")
    def toggle_broadcast_persistence(communication_id: str, body: PersistenceBody):
        """Phase 5.7: admin flips is_persistent on an existing broadcast.
        Delegates to the MCP tool so validation + audit live in one place."""
        from cartograph_mcp.tools import broadcast as _bc
        try:
            return _bc.update_broadcast_persistence(
                "admin", communication_id, body.persistent
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.post("/api/broadcast")
    def send_broadcast_from_admin(body: BroadcastBody):
        """Admin broadcasts to all agents of a type.

        Short-circuits the need to ask orchestrator first — admin is the
        most privileged actor. Inserts one communication row
        (type='broadcast', to_agent_type=<agent_type>). Per-agent acks
        happen individually via the broadcast_acks table.
        """
        row = execute_returning(
            """INSERT INTO communications (from_agent, to_agent_type, type, text, is_persistent)
               VALUES ('admin', %s, 'broadcast', %s, %s)
               RETURNING *""",
            (body.to_agent_type, body.message, body.persistent),
        )
        return row

    @app.post("/api/sleep")
    def admin_sleep(body: SleepBody):
        """Bulk-sleep agents until an ISO timestamp. Delegates to the MCP
        tool logic (same validation + 7-day cap)."""
        from cartograph_mcp.tools import sleep as _sleep
        return _sleep.bulk_sleep_agents(
            agent_id="admin",
            until=body.until,
            reason=body.reason,
            agent_ids=body.agent_ids,
            agent_type=body.agent_type,
        )

    @app.post("/api/wake")
    def admin_wake(body: WakeBody):
        """Clear sleep_until on a cohort. Delegates to the MCP tool logic."""
        from cartograph_mcp.tools import sleep as _sleep
        return _sleep.bulk_wake_agents(
            agent_id="admin",
            agent_ids=body.agent_ids,
            agent_type=body.agent_type,
        )

    # --- STATIC FILES + SPA FALLBACK (Phase 5.1) ---
    #
    # All /api/* routes registered above take precedence (FastAPI matches in
    # registration order). The catch-all below first tries to serve a real
    # file from STATIC_DIR; if no file exists at the requested path, it
    # falls back to index.html so client-side routes like /chat/sme-foo,
    # /entities/task/<uuid>, /catalog/component/<uuid> etc. all hit the
    # SPA's history-API router. We don't use StaticFiles' html=True mount
    # any more because it can't fall back to index.html for non-existent
    # paths — it 404s instead, which broke deep-links.

    @app.get("/{full_path:path}")
    def spa_or_static(full_path: str):
        # Genuine /api/ misses must 404 — never fall back to index.html.
        # FastAPI's path-param catch-all otherwise eats every unknown
        # /api/* path, masking real backend bugs.
        if full_path.startswith("api/") or full_path == "api":
            raise HTTPException(status_code=404, detail="Not Found")
        if full_path:
            candidate = (STATIC_DIR / full_path).resolve()
            try:
                # Reject path traversal: candidate must be inside STATIC_DIR.
                candidate.relative_to(STATIC_DIR.resolve())
                if candidate.is_file():
                    return FileResponse(candidate)
            except ValueError:
                pass  # fall through to index.html
        return FileResponse(STATIC_DIR / "index.html")

    return app


def main(port: int = 8200) -> None:
    import uvicorn
    from shared.db import init_pool, close_pool
    from shared.migrations import run_migrations

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        stream=sys.stdout,
    )

    init_pool()
    run_migrations()

    app = create_app()
    logger.info("Admin UI listening on port %d", port)
    try:
        uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
    finally:
        close_pool()


if __name__ == "__main__":
    main()

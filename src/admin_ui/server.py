"""Admin Chat UI — FastAPI server.

Lightweight web UI for humans to chat with any agent. Reads/writes the
communications table directly via shared/db.py.
"""

import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
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


def create_app() -> FastAPI:
    app = FastAPI(title="Cartograph Admin UI")

    # --- API ROUTES ---

    @app.get("/api/agents")
    def list_agents():
        """List all agents except decommissioned, ordered by type priority."""
        rows = execute(
            """SELECT agent_id, agent_type, status, sleep_until, created_at
               FROM agent_runs
               WHERE status != 'decommissioned'
               ORDER BY
                 CASE agent_type
                   WHEN 'orchestrator' THEN 0
                   WHEN 'resolver' THEN 1
                   WHEN 'sme' THEN 2
                   WHEN 'iterator' THEN 3
                   ELSE 99
                 END,
                 created_at"""
        )
        return {"agents": rows}

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
        """Admin sends a message to an agent."""
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

    # --- GRAPH VIEW (Phase 3.5) ---

    @app.get("/api/graph")
    def get_graph():
        """Nodes = active components. Edges = edges table.

        Each node carries its plane set (distinct planes from attributions)
        and component_doc_md so the FE can render hover popups without a
        second round-trip.
        """
        nodes = execute(
            """SELECT c.id, c.canonical_name, c.display_name,
                      c.component_type, c.status, c.component_doc_md,
                      c.source_slice,
                      COALESCE(
                        ARRAY_AGG(DISTINCT a.plane) FILTER (WHERE a.plane IS NOT NULL),
                        ARRAY[]::text[]
                      ) AS planes
               FROM components c
               LEFT JOIN attributions a ON a.component_id = c.id
               WHERE c.status != 'decommissioned'
               GROUP BY c.id
               ORDER BY c.canonical_name"""
        )
        # Phase 3.9: edges columns renamed. Phase 3.5/3.10 keep returning
        # the source_id / target_id keys to the FE for compatibility
        # (3d-force-graph needs them as link source/target). Catalog rows
        # (from IS NULL) and dangling outgoings (to IS NULL) are excluded
        # here — they get rendered via the 3.10 viz upgrades on a separate
        # path. For 3.9 the basic view stays bound-edges-only.
        edges = execute(
            """SELECT e.id,
                      e.from_component_id AS source_id,
                      e.to_component_id   AS target_id,
                      e.edge_type, e.identifier, e.confidence
               FROM edges e
               JOIN components cs ON cs.id = e.from_component_id AND cs.status != 'decommissioned'
               JOIN components ct ON ct.id = e.to_component_id   AND ct.status != 'decommissioned'
               WHERE e.from_component_id IS NOT NULL
                 AND e.to_component_id IS NOT NULL"""
        )
        return {"nodes": nodes, "edges": edges}

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

    # --- STATIC FILES ---

    if STATIC_DIR.exists():
        app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")

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

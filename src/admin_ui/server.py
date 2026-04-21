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


def create_app() -> FastAPI:
    app = FastAPI(title="Cartograph Admin UI")

    # --- API ROUTES ---

    @app.get("/api/agents")
    def list_agents():
        """List all agents except decommissioned, ordered by type priority."""
        rows = execute(
            """SELECT agent_id, agent_type, status, created_at
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

    @app.get("/api/communications")
    def list_communications(
        from_agent: Optional[str] = Query(None),
        to_agent: Optional[str] = Query(None),
        type: Optional[str] = Query(None),
        source_id: Optional[str] = Query(None),
        before: Optional[str] = Query(None),
        limit: int = Query(50, ge=1, le=200),
    ):
        """Filtered communications feed for the admin UI panel.

        All filters AND together. `before` is a created_at cursor for older
        pages. Returns newest-first with a has_more flag.
        """
        if type is not None and type not in _VALID_COMM_TYPES:
            raise HTTPException(400, f"Invalid type (allowed: {sorted(_VALID_COMM_TYPES)})")

        where = []
        params: list = []
        if from_agent:
            where.append("from_agent = %s")
            params.append(from_agent)
        if to_agent:
            where.append("to_agent = %s")
            params.append(to_agent)
        if type:
            where.append("type = %s")
            params.append(type)
        if source_id:
            where.append("source_id = %s::uuid")
            params.append(source_id)
        if before:
            where.append("created_at < %s")
            params.append(before)
        where_sql = (" WHERE " + " AND ".join(where)) if where else ""

        rows = execute(
            f"""SELECT * FROM communications{where_sql}
                ORDER BY created_at DESC
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

    @app.post("/api/broadcast")
    def send_broadcast_from_admin(body: BroadcastBody):
        """Admin broadcasts to all agents of a type.

        Short-circuits the need to ask orchestrator first — admin is the
        most privileged actor. Inserts one communication row
        (type='broadcast', to_agent_type=<agent_type>). Per-agent acks
        happen individually via the broadcast_acks table.
        """
        row = execute_returning(
            """INSERT INTO communications (from_agent, to_agent_type, type, text)
               VALUES ('admin', %s, 'broadcast', %s)
               RETURNING *""",
            (body.to_agent_type, body.message),
        )
        return row

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

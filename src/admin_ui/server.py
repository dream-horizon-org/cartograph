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


def create_app() -> FastAPI:
    app = FastAPI(title="Cartograph Admin UI")

    # --- API ROUTES ---

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
        rows = execute(
            f"""SELECT DISTINCT ON (ar.agent_id)
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
                  CASE c.status WHEN 'active' THEN 0 WHEN 'deprecated' THEN 1 ELSE 2 END"""
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
        edges = execute(
            """SELECT e.id,
                      e.from_component_id AS source_id,
                      e.to_component_id   AS target_id,
                      e.edge_type, e.identifier, e.confidence, e.metadata,
                      CASE
                        WHEN e.from_component_id IS NOT NULL
                             AND e.to_component_id IS NOT NULL THEN 'bound'
                        WHEN e.from_component_id IS NULL     THEN 'catalog'
                        ELSE 'dangling'
                      END AS kind
               FROM edges e
               LEFT JOIN components cs ON cs.id = e.from_component_id
               LEFT JOIN components ct ON ct.id = e.to_component_id
               WHERE (cs.id IS NULL OR cs.status != 'decommissioned')
                 AND (ct.id IS NULL OR ct.status != 'decommissioned')"""
        )
        flows = execute(
            """SELECT f.id, f.component_id,
                      f.incoming_edge_id, f.outgoing_edge_id,
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

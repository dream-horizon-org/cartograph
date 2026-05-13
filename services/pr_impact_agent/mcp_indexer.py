"""Wraps codebase-memory-mcp (v0.6.1) for use as a structural-graph
backend at PR-analysis time.

Strategy:
  1. Spawn `codebase-memory-mcp cli index_repository` once per PR clone.
  2. Read the resulting SQLite directly via stdlib sqlite3 — bypasses the
     MCP protocol, the buggy Cypher subset, and `detect_changes` (which
     is file-level granularity only).
  3. Run two structured queries:
       a) line-range overlap → directly-impacted handlers
       b) recursive CALLS-edge walk → transitively-impacted handlers

Falls back gracefully if the binary is missing, indexing fails, or the
SQLite schema diverges from what we expect (pre-1.0 caveat)."""

from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import subprocess
from typing import Any

logger = logging.getLogger(__name__)


_INDEXER_BIN = shutil.which("codebase-memory-mcp")
_DEFAULT_INDEX_TIMEOUT = int(os.getenv("PR_IMPACT_CMM_INDEX_TIMEOUT", "120"))
_TRANSITIVE_DEPTH = int(os.getenv("PR_IMPACT_CMM_DEPTH", "3"))
_DB_DIR = os.path.expanduser("~/.cache/codebase-memory-mcp")


class CMMError(RuntimeError):
    pass


def is_available() -> bool:
    return _INDEXER_BIN is not None


def index_repo(workspace_path: str,
               timeout: int | None = None) -> dict[str, Any]:
    """Run `codebase-memory-mcp cli index_repository`. Returns parsed JSON.

    Caller should pass the result's `project` to db_path_for() / queries
    and to delete_project() at cleanup."""
    if not _INDEXER_BIN:
        raise CMMError("codebase-memory-mcp binary not on PATH")

    args_json = json.dumps({"repo_path": workspace_path})
    cmd = [_INDEXER_BIN, "cli", "index_repository", args_json]
    logger.info("cmm index_repository: %s", workspace_path)
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout or _DEFAULT_INDEX_TIMEOUT,
        )
    except subprocess.TimeoutExpired as e:
        raise CMMError(
            f"cmm index timed out after {timeout or _DEFAULT_INDEX_TIMEOUT}s"
        ) from e

    if proc.returncode != 0:
        raise CMMError(
            f"cmm index exit {proc.returncode}: {proc.stderr[:300]}"
        )

    # Output is a single JSON line on stdout (last line). The binary also
    # writes log lines starting with `level=...` to stderr; some build
    # variants intermix progress logs into stdout — pick the last JSON-y
    # line defensively.
    payload = None
    for line in reversed(proc.stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                payload = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
    if not payload or payload.get("status") != "indexed":
        raise CMMError(f"cmm index returned no indexed payload: {proc.stdout[:300]}")
    return payload


def db_path_for(project_name: str) -> str:
    return os.path.join(_DB_DIR, f"{project_name}.db")


def _open_ro(db_path: str) -> sqlite3.Connection:
    """Open the SQLite read-only via URI mode."""
    if not os.path.exists(db_path):
        raise CMMError(f"cmm sqlite not found at {db_path}")
    uri = f"file:{db_path}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def schema_ok(db_path: str) -> bool:
    """Sanity-check the SQLite has the columns/tables we depend on.

    Pre-1.0 schema may break across cmm versions; this lets the caller
    fall back to LLM-only mode without crashing on schema drift."""
    try:
        conn = _open_ro(db_path)
        try:
            cur = conn.cursor()
            cur.execute("PRAGMA table_info(nodes)")
            node_cols = {r[1] for r in cur.fetchall()}
            cur.execute("PRAGMA table_info(edges)")
            edge_cols = {r[1] for r in cur.fetchall()}
        finally:
            conn.close()
    except sqlite3.Error as e:
        logger.warning("cmm schema check failed: %s", e)
        return False

    needed_nodes = {"id", "project", "label", "name", "qualified_name",
                    "file_path", "start_line", "end_line", "properties"}
    needed_edges = {"id", "project", "source_id", "target_id", "type",
                    "properties"}
    return needed_nodes.issubset(node_cols) and needed_edges.issubset(edge_cols)


def query_handlers_in_range(db_path: str, project: str,
                            file_path: str,
                            start_line: int, end_line: int) -> list[dict[str, Any]]:
    """Functions/methods whose body overlaps [start_line, end_line] in `file_path`."""
    conn = _open_ro(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT n.id, n.name, n.qualified_name, n.file_path,
                   n.start_line, n.end_line,
                   json_extract(n.properties, '$.route_path')   AS route_path,
                   json_extract(n.properties, '$.route_method') AS route_method
            FROM nodes n
            WHERE n.project = ?
              AND n.label IN ('Function', 'Method')
              AND n.file_path = ?
              AND n.end_line >= ?
              AND n.start_line <= ?
            ORDER BY n.start_line
            """,
            (project, file_path, start_line, end_line),
        )
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        conn.close()


def query_transitive_callers(db_path: str, project: str,
                             seed_node_ids: list[int],
                             max_depth: int | None = None
                             ) -> list[dict[str, Any]]:
    """For each seed Function id, walk REVERSE through CALLS edges to find
    its ancestors (callers, callers' callers, ...) up to max_depth.

    Returns each unique ancestor with the minimum depth at which it was
    reached. Excludes the seeds themselves (depth > 0).

    Caveat: cmm's Python call-graph misses external-library calls (httpx,
    requests, etc.). This walk covers internal/import-resolved calls only."""
    if not seed_node_ids:
        return []
    depth = max_depth if max_depth is not None else _TRANSITIVE_DEPTH

    placeholders = ",".join(["?"] * len(seed_node_ids))
    conn = _open_ro(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            f"""
            WITH RECURSIVE walk(id, depth) AS (
              SELECT id, 0 FROM nodes
              WHERE id IN ({placeholders}) AND project = ?

              UNION

              SELECT e.source_id, w.depth + 1
              FROM walk w
              JOIN edges e ON e.target_id = w.id
                          AND e.type = 'CALLS'
                          AND e.project = ?
              WHERE w.depth < ?
            )
            SELECT n.id, n.name, n.qualified_name, n.file_path,
                   n.start_line, n.end_line,
                   json_extract(n.properties, '$.route_path')   AS route_path,
                   json_extract(n.properties, '$.route_method') AS route_method,
                   MIN(w.depth) AS depth
            FROM walk w
            JOIN nodes n ON n.id = w.id
            WHERE w.depth > 0
              AND n.label IN ('Function', 'Method')
            GROUP BY n.id
            ORDER BY depth, n.qualified_name
            """,
            (*seed_node_ids, project, project, depth),
        )
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        conn.close()


def delete_project(project_name: str) -> None:
    """Best-effort cleanup of the cmm project (graph data + cache row).

    Falls back to deleting the .db file directly if the CLI fails."""
    if not _INDEXER_BIN:
        # Just delete the file.
        path = db_path_for(project_name)
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass
        return

    try:
        subprocess.run(
            [_INDEXER_BIN, "cli", "delete_project",
             json.dumps({"project": project_name})],
            capture_output=True, text=True, timeout=15,
        )
    except subprocess.TimeoutExpired:
        logger.warning("cmm delete_project timed out for %s", project_name)
    # Belt-and-suspenders: ensure the .db is gone even if the CLI no-op'd.
    path = db_path_for(project_name)
    if os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass


def build_pre_analysis(workspace_path: str,
                       changed_files: list[dict[str, Any]]
                       ) -> dict[str, Any]:
    """Top-level helper. Indexes the repo, runs the per-hunk overlap and
    transitive-caller queries, returns a structured pre-analysis blob.

    Always cleans up the cmm project, even on partial failure. Returns a
    payload with .indexed=False if anything went wrong, so the caller can
    fall back to LLM-only mode."""
    from .diff_hunks import parse_patch

    result: dict[str, Any] = {
        "indexed": False,
        "project_name": None,
        "nodes_count": None,
        "edges_count": None,
        "directly_impacted_handlers": [],
        "transitively_impacted_handlers": [],
        "note": None,
    }

    if not is_available():
        result["note"] = "codebase-memory-mcp not on PATH"
        return result

    project_name: str | None = None
    try:
        try:
            payload = index_repo(workspace_path)
        except CMMError as e:
            result["note"] = f"cmm index failed: {e}"
            return result

        project_name = payload.get("project")
        result["project_name"] = project_name
        result["nodes_count"] = payload.get("nodes")
        result["edges_count"] = payload.get("edges")
        if not project_name:
            result["note"] = "cmm payload missing project"
            return result

        db_path = db_path_for(project_name)
        if not schema_ok(db_path):
            result["note"] = "cmm sqlite schema diverged from expected v0.6.1"
            return result

        # Per-hunk overlap → direct handlers
        seen_ids: set[int] = set()
        directs: list[dict[str, Any]] = []
        for f in changed_files:
            file_path = f.get("filename")
            patch = f.get("patch")
            if not file_path or not patch:
                continue
            for (start, end) in parse_patch(patch):
                rows = query_handlers_in_range(
                    db_path, project_name, file_path, start, end
                )
                for r in rows:
                    if r["id"] in seen_ids:
                        continue
                    seen_ids.add(r["id"])
                    directs.append(r)
        result["directly_impacted_handlers"] = directs

        # Transitive callers walk
        if directs:
            transitive = query_transitive_callers(
                db_path, project_name, [d["id"] for d in directs]
            )
            # Filter out anything already in directs
            transitive = [t for t in transitive if t["id"] not in seen_ids]
            result["transitively_impacted_handlers"] = transitive

        result["indexed"] = True
        return result

    finally:
        if project_name:
            delete_project(project_name)

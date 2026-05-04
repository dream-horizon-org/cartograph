"""Agent Manager — create, invoke, and deactivate agents."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
import uuid

import yaml

from agent_management import db
from agent_management.agent_types.base import get_config

logger = logging.getLogger(__name__)

_TYPE_PREFIXES = {
    "orchestrator": "orch",
    "iterator": "iter",
    "sme": "sme",
    "resolver": "res",
}

_GENERIC_INVOCATION_PROMPT_TEMPLATE = """You have been woken up because you have pending action items.

YOUR agent_id is: {agent_id}
YOUR agent_type is: {agent_type}

Always pass this exact agent_id to every tool call. Never invent, abbreviate, or modify it.
For example: get_action_items_summary(agent_id="{agent_id}")

== OUTPUT STYLE ==
Caveman English (default level: full). Active EVERY response. Pattern:
`[thing] [action] [reason]. [next step].` Drop articles / filler /
preambles / post-hoc summaries. Keep identifiers / file paths / IDs /
hashes / error strings / code blocks VERBATIM. Resume normal English
ONLY for destructive-op confirms + admin "clarify" requests. Exempt
context: component_doc_md (graph-viz hover for humans).

== BATCHING ==
For N independent tool calls, use mcp_call_batch (Phase 9.1) — one
round-trip instead of N. Native parallel tool_use blocks are
serialised by your subprocess; mcp_call_batch is the only batched
path. See your system prompt's BULK CALLS DECISION LADDER.

== ACTION ITEMS SNAPSHOT (at {snapshot_ts}) ==
{action_items_snapshot}

The snapshot above was computed by the agent_manager just before this wake.
It is FRESHER than any cached state in your prior conversation.

Action items may have arrived AFTER this snapshot — call get_action_items_summary
or get_action_items_detail mid-wake if you suspect drift.

Your workflow:
1. Triage from the snapshot above. Address each pending item using your act tools (always passing agent_id="{agent_id}").
2. Use get_action_items_detail(agent_id="{agent_id}") only if you need full row contents the snapshot didn't include (e.g. message bodies, blocker_detail).
3. You MUST change state on every response — no empty replies.
4. Work on as many items as you can handle, then yield control.
5. You will be woken again if more items arrive.

Refer to your system prompt for phase-specific instructions and tool usage."""


def _build_action_items_snapshot(agent_id: str, agent_type: str) -> str:
    """Pre-compute a concise action-items snapshot for the invocation prompt.

    Phase 7.4.13 (token-opt Round 2 #5). Goes in the USER message, NOT the
    system_prompt — system_prompt must remain byte-identical across wakes
    for cache hits. The snapshot text varies per wake (different counts /
    pending items), so it lives in the user-visible invocation prompt.

    Returns a markdown block listing pending items per category. Skips
    full row content — agent calls get_action_items_detail mid-wake if
    it needs the message bodies.

    Includes proxy_count summary (decommissioned-agent inherited work).
    """
    from shared.db import execute_one
    # Aggregate pending counts directly via SQL — one round-trip vs N MCP
    # calls. Mirrors get_action_items_summary's logic.
    row = execute_one(
        """
        SELECT
          (SELECT COUNT(*) FROM consolidations
             WHERE ((agent_a_id = %(aid)s AND status = 'B1')
                 OR (agent_b_id = %(aid)s AND status = 'B2')
                 OR (status IN ('R','MD') AND %(atype)s = 'resolver')
                 OR (mutation_assigned_to = %(aid)s AND status = 'M'))
          ) AS consolidations_pending,
          (SELECT COUNT(*) FROM tasks
             WHERE (worker_agent_id = %(aid)s AND status = 'BW')
                OR (owner_agent_id  = %(aid)s AND status IN ('BO','WD'))
          ) AS tasks_pending,
          (SELECT COUNT(*) FROM clarifications
             WHERE (asker_agent_id = %(aid)s AND status IN ('B1','QR','QC'))
                OR (responder_agent_id = %(aid)s AND status = 'B2')
          ) AS clarifications_pending,
          (SELECT COUNT(*) FROM communications
             WHERE to_agent = %(aid)s AND type = 'chat' AND acked_at IS NULL
          ) AS unacked_chats,
          (SELECT COUNT(*) FROM communications c
             WHERE c.type = 'broadcast' AND c.to_agent_type = %(atype)s
               AND c.id NOT IN (
                 SELECT communication_id FROM broadcast_acks WHERE agent_id = %(aid)s
               )
               AND (c.is_persistent OR c.created_at > (
                 SELECT created_at FROM agent_runs WHERE agent_id = %(aid)s
               ))
          ) AS unacked_broadcasts,
          (SELECT COUNT(*) FROM (
             SELECT 1 FROM tasks t
              WHERE t.status = 'TC'
                AND (t.owner_agent_id = %(aid)s OR t.worker_agent_id = %(aid)s)
                AND NOT EXISTS (
                  SELECT 1 FROM terminal_acks
                   WHERE entity_type='task' AND entity_id=t.id AND agent_id=%(aid)s
                )
             UNION ALL
             SELECT 1 FROM consolidations c
              WHERE c.status IN ('D','F')
                AND (c.agent_a_id = %(aid)s OR c.agent_b_id = %(aid)s)
                AND NOT EXISTS (
                  SELECT 1 FROM terminal_acks
                   WHERE entity_type='consolidation' AND entity_id=c.id AND agent_id=%(aid)s
                )
             UNION ALL
             SELECT 1 FROM clarifications cl
              WHERE cl.status IN ('CC','QR')
                AND (cl.asker_agent_id = %(aid)s OR cl.responder_agent_id = %(aid)s)
                AND NOT EXISTS (
                  SELECT 1 FROM terminal_acks
                   WHERE entity_type='clarification' AND entity_id=cl.id AND agent_id=%(aid)s
                )
          ) t) AS terminal_pending_ack,
          (SELECT COUNT(*) FROM agent_runs
             WHERE merged_into_agent_id = %(aid)s AND status = 'decommissioned'
          ) AS proxied_count
        """,
        {"aid": agent_id, "atype": agent_type},
    )
    if row is None:
        return "(snapshot unavailable — call get_action_items_summary mid-wake)"

    lines = []
    if row["consolidations_pending"]: lines.append(f"- consolidations_pending: {row['consolidations_pending']}")
    if row["tasks_pending"]:           lines.append(f"- tasks_pending: {row['tasks_pending']}")
    if row["clarifications_pending"]:  lines.append(f"- clarifications_pending: {row['clarifications_pending']}")
    if row["unacked_chats"]:           lines.append(f"- unacked_chats: {row['unacked_chats']}")
    if row["unacked_broadcasts"]:      lines.append(f"- unacked_broadcasts: {row['unacked_broadcasts']}")
    if row["terminal_pending_ack"]:    lines.append(f"- terminal_pending_ack: {row['terminal_pending_ack']}  (call ack_terminal on each)")
    if row["proxied_count"]:           lines.append(f"- proxied_count: {row['proxied_count']}  (inherited work — call get_my_proxy_items, then act_on_proxy_item)")
    if not lines:
        return "(no pending items at snapshot time — admin chat or mutation override may have triggered this wake)"
    return "\n".join(lines)


class AgentManager:
    def __init__(self, workspace_root: str, mcp_config_path: str) -> None:
        self.workspace_root = workspace_root
        with open(mcp_config_path) as f:
            self.mcp_registry: dict = yaml.safe_load(f)

    def create_agent(
        self,
        agent_type: str,
        plane: str | None = None,
        resource_id: str | None = None,
    ) -> str:
        """Create a single agent. Returns agent_id.

        For SMEs, the resource_id is used to (a) size the system prompt and
        (b) write an RCA row with component_id=NULL (reserved assignment slot).
        The RCA row is the single source of truth; agent_runs no longer stores
        resource_id. Bulk SME spawning uses bulk_spawn_smes instead.
        """
        short_id = uuid.uuid4().hex[:8]
        prefix = _TYPE_PREFIXES[agent_type]
        if agent_type == "iterator" and plane:
            agent_id = f"{prefix}-{plane}-{short_id}"
        else:
            agent_id = f"{prefix}-{short_id}"

        config = get_config(
            agent_type,
            plane=plane or "",
            resource_id=resource_id or "",
            mcp_registry_keys=",".join(self.mcp_registry.keys()),
        )

        workspace_path = os.path.join(self.workspace_root, agent_id)
        os.makedirs(workspace_path, exist_ok=True)

        self._write_mcp_json(workspace_path, config.mcp_servers)
        self._write_claude_settings(workspace_path, agent_id, agent_type)

        db.create_agent_run(
            agent_id=agent_id,
            agent_type=agent_type,
            workspace_path=workspace_path,
            plane=plane,
        )

        # For SMEs, write the RCA reservation row (component_id=NULL) and
        # flip the resource to 'assigned'. This is the single source of
        # truth for SME→resource assignment.
        if agent_type == "sme" and resource_id:
            from shared.db import execute_mutate
            execute_mutate(
                """INSERT INTO resource_component_agents
                   (resource_id, component_id, agent_id)
                   VALUES (%s, NULL, %s)""",
                (resource_id, agent_id),
            )
            execute_mutate(
                "UPDATE resources SET status = 'assigned' WHERE id = %s AND status = 'pending'",
                (resource_id,),
            )

        # Transition new agent to 'idle' so trigger manager can lock it when
        # it has pending items. Initial wake-up comes from a task, chat, or
        # direct admin trigger — no more enqueued initial prompt.
        db.update_agent_status(agent_id, "idle")

        return agent_id

    def invoke_agent(
        self,
        agent_id: str,
        prompt: str | None = None,
        already_picked_up: bool = False,
    ) -> str:
        """Pick up a locked agent and invoke it.

        Atomically transitions trigger_lock=TRUE → status='running',
        trigger_lock=FALSE, invocation_count+=1, heartbeat=now().

        If already_picked_up is True, the caller (lane worker) has already
        performed the transition via pickup_next_locked_agent_of_type and
        we skip the pickup_agent call.

        If prompt is None, uses the generic invocation prompt.
        """
        agent = db.get_agent(agent_id)
        if agent is None:
            raise ValueError(f"Agent not found: {agent_id}")

        logger.info(
            "invoke_agent: %s (type=%s, session_id=%s, workspace=%s)",
            agent_id,
            agent["agent_type"],
            agent.get("session_id"),
            agent.get("workspace_path"),
        )

        # Atomic pickup: lock → running (skip if lane worker already did it)
        if not already_picked_up:
            if not db.pickup_agent(agent_id):
                logger.warning("Agent %s was not locked; skipping invocation", agent_id)
                return ""

        if prompt is None:
            # Phase 7.4.13 (token-opt Round 2 #5): pre-compute action-items
            # snapshot once via SQL and inject into the user message.
            # Saves the get_action_items_summary + get_action_items_detail
            # round-trips on every wake. Goes in user message NOT
            # system_prompt (system_prompt must remain byte-identical
            # across wakes for cache hits).
            try:
                snapshot = _build_action_items_snapshot(agent_id, agent["agent_type"])
            except Exception:
                logger.exception("action-items snapshot failed for %s; proceeding without", agent_id)
                snapshot = "(snapshot unavailable — call get_action_items_summary mid-wake)"
            from datetime import datetime
            prompt = _GENERIC_INVOCATION_PROMPT_TEMPLATE.format(
                agent_id=agent_id,
                agent_type=agent["agent_type"],
                snapshot_ts=datetime.utcnow().isoformat(timespec="seconds") + "Z",
                action_items_snapshot=snapshot,
            )

        # For SMEs, look up the assigned resource from RCA (single source of
        # truth). Iterators use agent_runs.plane; orchestrator/resolver
        # ignore both fields in their prompts.
        resource_id = ""
        if agent["agent_type"] == "sme":
            resource_id = db.get_sme_resource_id(agent_id) or ""

        config = get_config(
            agent["agent_type"],
            plane=agent.get("plane") or "",
            resource_id=resource_id,
            mcp_registry_keys=",".join(self.mcp_registry.keys()),
        )

        workspace_path = agent.get("workspace_path")
        if not workspace_path:
            msg = (
                "Agent has no workspace_path — was it created via AgentManager? "
                "Manually-inserted agents lack workspace/MCP config."
            )
            logger.error("Agent %s: %s", agent_id, msg)
            db.set_agent_errored(agent_id, msg)
            return ""

        # Use --resume if we have a session; otherwise --session-id lets us
        # provide a fresh UUID so we can persist and resume later.
        cmd = [
            "claude",
            "-p", prompt,
            "--output-format", "json",
            "--allowedTools", ",".join(config.allowed_tools),
            "--system-prompt", config.system_prompt,
            "--setting-sources", "project",  # loads .mcp.json from cwd
            "--dangerously-skip-permissions",  # non-interactive mode
            "--model", config.model,
        ]
        if config.effort:
            cmd.extend(["--effort", config.effort])
        if agent["session_id"]:
            cmd.extend(["--resume", agent["session_id"]])

        logger.info(
            "Spawning claude subprocess for %s: cwd=%s, tools=%s, session=%s",
            agent_id,
            workspace_path,
            config.allowed_tools,
            agent["session_id"] or "<new>",
        )

        # Background thread to keep the heartbeat fresh while subprocess runs.
        heartbeat_stop = threading.Event()

        def _heartbeat_keeper():
            while not heartbeat_stop.wait(10):  # every 10s
                try:
                    db.update_agent_heartbeat(agent_id)
                except Exception:
                    logger.exception("Heartbeat update failed for %s", agent_id)

        hb_thread = threading.Thread(target=_heartbeat_keeper, daemon=True)
        hb_thread.start()

        # Timeout tuned per agent_type. Iterators do heavy enumeration
        # (cloning, GitHub API sweeps, paginated listings) and frequently need
        # >5 minutes. Others yield faster so a tighter bound is fine.
        timeout_by_type = {
            "iterator": 1800,   # 30 min
            "sme":      1800,   # 30 min (deep resource analysis)
            "orchestrator": 900,
            "resolver":     900,
        }
        subprocess_timeout = timeout_by_type.get(agent["agent_type"], 900)

        try:
            # Run claude in the agent's workspace (cwd) so .mcp.json is loaded
            result = subprocess.run(
                cmd,
                cwd=workspace_path,
                capture_output=True,
                text=True,
                timeout=subprocess_timeout,
            )

            logger.info(
                "claude subprocess for %s finished: returncode=%d, stdout_len=%d, stderr_len=%d",
                agent_id,
                result.returncode,
                len(result.stdout),
                len(result.stderr),
            )

            if result.returncode != 0:
                logger.error(
                    "Agent %s failed (returncode=%d)\nSTDERR:\n%s\nSTDOUT:\n%s",
                    agent_id,
                    result.returncode,
                    result.stderr[:2000],
                    result.stdout[:2000],
                )
                err_tail = (result.stderr or "")[-2000:]
                out_tail = (result.stdout or "")[-500:]
                db.set_agent_errored(
                    agent_id,
                    f"subprocess exit {result.returncode}\n"
                    f"--- stderr tail ---\n{err_tail}\n"
                    f"--- stdout tail ---\n{out_tail}",
                )
                return result.stderr

            output = result.stdout
            logger.debug("Agent %s output: %s", agent_id, output[:500])

            if agent["session_id"] is None:
                session_id = self._extract_session_id(output)
                if session_id:
                    logger.info("Captured new session_id for %s: %s", agent_id, session_id)
                    db.update_agent_session(agent_id, session_id)
                else:
                    logger.warning("No session_id in output for %s", agent_id)

            db.update_agent_status(agent_id, "idle")
            db.update_agent_heartbeat(agent_id)
            db.clear_recovery_state(agent_id)
            # Phase 7.4.14 (token-opt Round 3 #6): clear the wake-debounce
            # window. Trigger scanner re-stamps first_pending_at when it
            # next sees a pending item — fresh 5-min window per cycle.
            db.clear_first_pending(agent_id)
            logger.info("Agent %s completed, set to idle", agent_id)
            return output

        except subprocess.TimeoutExpired as e:
            logger.error("Agent %s timed out after %ds", agent_id, subprocess_timeout)
            err_tail = ""
            if e.stderr:
                err_tail = (e.stderr.decode(errors="replace") if isinstance(e.stderr, bytes) else e.stderr)[-2000:]
            db.set_agent_errored(
                agent_id,
                f"TimeoutExpired after {subprocess_timeout}s — subprocess killed.\n"
                f"--- stderr tail ---\n{err_tail}",
            )
            return "TIMEOUT"
        except FileNotFoundError as e:
            msg = f"claude CLI not found — is Claude Code installed and on PATH? {e}"
            logger.error(msg)
            db.set_agent_errored(agent_id, msg)
            return str(e)
        except Exception as e:
            logger.exception("Unexpected error invoking agent %s", agent_id)
            db.set_agent_errored(agent_id, f"{type(e).__name__}: {e}")
            raise
        finally:
            # Stop heartbeat keeper regardless of outcome
            heartbeat_stop.set()

    def deactivate_agent(self, agent_id: str) -> None:
        db.update_agent_status(agent_id, "decommissioned")
        db.release_trigger_lock(agent_id)

    def _write_mcp_json(self, workspace_path: str, mcp_server_names: list[str]) -> None:
        """Write .mcp.json for Claude Code to load MCP servers.

        Our cartograph-db MCP runs at /mcp (FastMCP streamable-http endpoint).
        Format: {"mcpServers": {"name": {"type": "http", "url": ".../mcp"}}}
        """
        servers = {}
        for name in mcp_server_names:
            if name in self.mcp_registry:
                base_url = self.mcp_registry[name]["url"].rstrip("/")
                # FastMCP streamable-http exposes /mcp endpoint
                mcp_url = base_url if base_url.endswith("/mcp") else f"{base_url}/mcp"
                servers[name] = {
                    "type": "http",
                    "url": mcp_url,
                }
        mcp_json = {"mcpServers": servers}
        path = os.path.join(workspace_path, ".mcp.json")
        with open(path, "w") as f:
            json.dump(mcp_json, f, indent=2)
        logger.info("Wrote MCP config for %s: %s", workspace_path, list(servers.keys()))

    # Per-agent-type priority sources for the notification hook. admin is
    # always top priority. SMEs / iterators / resolvers additionally care
    # about orchestrator messages. Orchestrator itself cares only about admin.
    _HOOK_PRIORITY_BY_TYPE = {
        "orchestrator": "admin",
        "iterator":     "admin,orchestrator",
        "sme":          "admin,orchestrator",
        "resolver":     "admin,orchestrator",
    }

    def _write_claude_settings(
        self, workspace_path: str, agent_id: str, agent_type: str
    ) -> None:
        """Write .claude/settings.json with a PostToolUse hook that pings
        the notify script. The hook fires after every tool call; the script
        itself is rate-limited (10s) so high-frequency tool loops don't
        stampede the DB.

        Priority source types are baked in per agent_type (see
        _HOOK_PRIORITY_BY_TYPE). Changing them requires re-writing the
        settings file (re-running create_agent or a targeted rewrite).
        """
        priority = self._HOOK_PRIORITY_BY_TYPE.get(agent_type, "admin")
        hook_script = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "hooks", "notify.py")
        )
        settings = {
            "hooks": {
                "PostToolUse": [
                    {
                        "matcher": "*",
                        "hooks": [
                            {
                                "type": "command",
                                "command": (
                                    f"{sys.executable} {hook_script} "
                                    f"--agent-id={agent_id} --priority={priority}"
                                ),
                            }
                        ],
                    }
                ]
            }
        }
        settings_dir = os.path.join(workspace_path, ".claude")
        os.makedirs(settings_dir, exist_ok=True)
        with open(os.path.join(settings_dir, "settings.json"), "w") as f:
            json.dump(settings, f, indent=2)
        logger.info(
            "Wrote hook config for %s: priority=%s", agent_id, priority
        )

    def _extract_session_id(self, output: str) -> str | None:
        try:
            data = json.loads(output)
            return data.get("session_id")
        except (json.JSONDecodeError, TypeError):
            return None

"""Agent Manager — create, invoke, and deactivate agents."""

from __future__ import annotations

import json
import logging
import os
import subprocess
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

Action items may be arriving concurrently — always use your tools to get the latest state, don't rely on stale information.

Your workflow:
1. Call get_action_items_summary(agent_id="{agent_id}") to see current counts
2. Call get_action_items_detail(agent_id="{agent_id}") for full details on items you want to address
3. Address each item using your act tools (always passing agent_id="{agent_id}")
4. You MUST change state on every response — no empty replies
5. Work on as many items as you can handle, then yield control
6. You will be woken again if more items arrive

Refer to your system prompt for phase-specific instructions and tool usage."""


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

    def invoke_agent(self, agent_id: str, prompt: str | None = None) -> str:
        """Pick up a locked agent and invoke it.

        Atomically transitions trigger_lock=TRUE → status='running',
        trigger_lock=FALSE, invocation_count+=1, heartbeat=now().

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

        # Atomic pickup: lock → running
        if not db.pickup_agent(agent_id):
            logger.warning("Agent %s was not locked; skipping invocation", agent_id)
            return ""

        if prompt is None:
            prompt = _GENERIC_INVOCATION_PROMPT_TEMPLATE.format(
                agent_id=agent_id,
                agent_type=agent["agent_type"],
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
        ]
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

    def _extract_session_id(self, output: str) -> str | None:
        try:
            data = json.loads(output)
            return data.get("session_id")
        except (json.JSONDecodeError, TypeError):
            return None

"""Agent Manager — create, invoke, and deactivate agents."""

from __future__ import annotations

import json
import logging
import os
import subprocess
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
            resource_id=resource_id,
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

        config = get_config(
            agent["agent_type"],
            plane=agent.get("plane") or "",
            resource_id=agent.get("resource_id") or "",
            mcp_registry_keys=",".join(self.mcp_registry.keys()),
        )

        workspace_path = agent.get("workspace_path")
        if not workspace_path:
            logger.error(
                "Agent %s has no workspace_path — was it created via AgentManager? "
                "Manually-inserted agents lack workspace/MCP config.",
                agent_id,
            )
            db.update_agent_status(agent_id, "errored")
            db.release_trigger_lock(agent_id)
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

        try:
            # Run claude in the agent's workspace (cwd) so .mcp.json is loaded
            result = subprocess.run(
                cmd,
                cwd=workspace_path,
                capture_output=True,
                text=True,
                timeout=300,
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
                db.update_agent_status(agent_id, "errored")
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
            logger.info("Agent %s completed, set to idle", agent_id)
            return output

        except subprocess.TimeoutExpired:
            logger.error("Agent %s timed out after 300s", agent_id)
            db.update_agent_status(agent_id, "errored")
            return "TIMEOUT"
        except FileNotFoundError as e:
            logger.error(
                "claude CLI not found — is Claude Code installed and on PATH? %s", e
            )
            db.update_agent_status(agent_id, "errored")
            return str(e)

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

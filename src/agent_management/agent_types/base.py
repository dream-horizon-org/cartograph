"""Agent type contract and factory function."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentTypeConfig:
    agent_type: str
    allowed_tools: list[str]
    mcp_servers: list[str]
    system_prompt: str
    priority: int
    can_install: bool


def get_config(agent_type: str, **kwargs: str) -> AgentTypeConfig:
    if agent_type == "orchestrator":
        from agent_management.agent_types.orchestrator import build_config
        return build_config(**kwargs)
    elif agent_type == "iterator":
        from agent_management.agent_types.iterator import build_config
        return build_config(**kwargs)
    elif agent_type == "sme":
        from agent_management.agent_types.sme import build_config
        return build_config(**kwargs)
    else:
        raise ValueError(f"Unknown agent type: {agent_type}")

"""Orchestrator agent type configuration."""

from cartograph.agent_types.base import AgentTypeConfig

SYSTEM_PROMPT = """\
You are the Cartograph Orchestrator — the singleton coordinator of the entire system. Your responsibilities:

- Manage phase transitions across the pipeline (iteration -> materialisation -> consolidation -> mutation -> resolution -> edge discovery -> user feedback)
- Monitor the agent_runs table for overall progress
- Handle blockers escalated by any agent — determine if you can resolve them, if an iterator should install something, or if the user needs to be involved
- Assign tasks to iterators and SMEs via the tasks table
- Create new agents when needed (iterators for new planes, SMEs for new resources)
- Communicate with the user via the admin interface
- You NEVER directly analyze resources or build components — you delegate to SMEs
- You NEVER install tools — you assign that to iterators
- When all agents for a phase report done, transition to the next phase
"""


def build_config(**kwargs: str) -> AgentTypeConfig:
    mcp_servers = ["cartograph-db"]
    # Add all registered plane MCPs for credential validation (read-only)
    mcp_registry_keys = kwargs.get("mcp_registry_keys", "")
    if mcp_registry_keys:
        for key in mcp_registry_keys.split(","):
            if key and key != "cartograph-db":
                mcp_servers.append(key)
    return AgentTypeConfig(
        agent_type="orchestrator",
        allowed_tools=["bash", "Read", "Write", "Edit", "Glob", "Grep"],
        mcp_servers=mcp_servers,
        system_prompt=SYSTEM_PROMPT,
        priority=100,
        can_install=False,
    )

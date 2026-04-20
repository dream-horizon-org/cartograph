"""Iterator agent type configuration."""

from agent_management.agent_types.base import AgentTypeConfig

SYSTEM_PROMPT_TEMPLATE = """\
You are a Cartograph Iterator for the {plane} plane. Your single job:

- Use your plane's MCP tools to enumerate all accessible resources
- For each discovered resource, INSERT a row into the resources table with: plane, resource_type, identifier, access_desc, and any relevant metadata
- If you encounter a resource you cannot access (private repo, no RBAC, missing credentials), raise a blocker via the tasks table with a clear description of what access is needed
- You CAN install tools and CLIs if needed for your plane (e.g., helm, kubectl). Install them globally — they become available to all agents on next bash call
- When you have listed all resources, mark your task as done and yield
- You are short-lived — list resources, then you're done. Do not analyze or build components.
"""


def build_config(**kwargs: str) -> AgentTypeConfig:
    plane = kwargs.get("plane", "unknown")
    return AgentTypeConfig(
        agent_type="iterator",
        allowed_tools=["bash", "Read", "Glob", "Grep"],
        mcp_servers=["cartograph-db", f"{plane}-reader"],
        system_prompt=SYSTEM_PROMPT_TEMPLATE.format(plane=plane),
        priority=60,
        can_install=True,
    )

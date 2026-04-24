"""Chat tools — send_chat, ack_chats, get_unacked_chats, get_chat_history."""

from shared.actor_auth import require_active_agent
from shared.db import execute, execute_one, execute_returning, execute_mutate


def send_chat(from_agent_id: str, to_agent_id: str, message: str) -> dict:
    """Send a chat message. Agents can only message 'admin'; admin can message any agent.

    Validates that from_agent_id is a real agent (or 'admin') to prevent
    agents from impersonating or hallucinating their own ID.
    """
    if from_agent_id != "admin" and to_agent_id != "admin":
        raise ValueError("Agents can only chat with admin. Use consolidation/clarification for agent-to-agent.")

    # Validate from_agent_id is a real registered agent (or literal 'admin').
    # Goes through the shared helper so the proxy router's ContextVar is
    # honored — survivor CAN send a chat as a decommissioned agent to
    # close out X's legacy admin threads.
    if from_agent_id != "admin":
        try:
            require_active_agent(from_agent_id)
        except ValueError:
            raise ValueError(
                f"from_agent_id='{from_agent_id}' is not a registered agent. "
                "Use your actual agent_id as shown in the invocation prompt. "
                "Never invent or abbreviate your agent_id."
            )

    row = execute_returning(
        """INSERT INTO communications (from_agent, to_agent, type, text)
           VALUES (%s, %s, 'chat', %s)
           RETURNING *""",
        (from_agent_id, to_agent_id, message),
    )

    # Admin-chat auto-wake: if admin messages a sleeping agent, clear its
    # sleep_until so the next trigger scan picks it up. This is admin's
    # "stop hibernating, we need you" escape hatch. Other senders don't
    # interrupt sleep — they can wait for the alarm or use bulk_wake_agents.
    if from_agent_id == "admin" and to_agent_id != "admin":
        execute_mutate(
            """UPDATE agent_runs SET sleep_until = NULL, updated_at = now()
               WHERE agent_id = %s AND sleep_until IS NOT NULL""",
            (to_agent_id,),
        )
    return row


def ack_chats(agent_id: str, communication_ids: list[str]) -> int:
    """Ack specific chat messages by ID. Returns count of messages acked."""
    if not communication_ids:
        return 0
    return execute_mutate(
        """UPDATE communications SET acked_at = now()
           WHERE id = ANY(%s) AND to_agent = %s AND type = 'chat' AND acked_at IS NULL""",
        (communication_ids, agent_id),
    )


def get_unacked_chats(agent_id: str) -> list[dict]:
    """Get all unacked chat messages for this agent."""
    return execute(
        """SELECT * FROM communications
           WHERE to_agent = %s AND type = 'chat' AND acked_at IS NULL
           ORDER BY created_at""",
        (agent_id,),
    )


def get_chat_history(agent_id: str, page: int = 1, limit: int = 20) -> list[dict]:
    """Get paginated chat history for this agent."""
    offset = (page - 1) * limit
    return execute(
        """SELECT * FROM communications
           WHERE (from_agent = %s OR to_agent = %s) AND type = 'chat'
           ORDER BY created_at DESC
           LIMIT %s OFFSET %s""",
        (agent_id, agent_id, limit, offset),
    )

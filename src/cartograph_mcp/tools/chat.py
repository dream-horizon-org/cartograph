"""Chat tools — send_chat, ack_chats, get_unacked_chats, get_chat_history."""

from shared.db import execute, execute_returning, execute_mutate


def send_chat(from_agent_id: str, to_agent_id: str, message: str) -> dict:
    """Send a chat message. Agents can only message 'admin'; admin can message any agent."""
    if from_agent_id != "admin" and to_agent_id != "admin":
        raise ValueError("Agents can only chat with admin. Use consolidation/clarification for agent-to-agent.")

    row = execute_returning(
        """INSERT INTO communications (from_agent, to_agent, type, text)
           VALUES (%s, %s, 'chat', %s)
           RETURNING *""",
        (from_agent_id, to_agent_id, message),
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

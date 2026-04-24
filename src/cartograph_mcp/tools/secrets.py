"""Secrets tools — orchestrator writes, all agents read.

The secrets table holds credentials per plane (GitHub token, AWS keys,
Datadog API key, Consul token, etc.). Orchestrator stores them during
the User Input phase. Iterators and SMEs read them to call their plane APIs.

Storage: values are stored as TEXT in the DB. Future: encrypt at rest.
"""

from shared.actor_auth import require_active_agent
from shared.db import execute, execute_one, execute_returning


def put_secret(agent_id: str, plane: str, key: str, value: str) -> dict:
    """Write or update a secret. Only orchestrator can write secrets.

    Upserts on (plane, key). Returns the stored row (with value).
    """
    row = require_active_agent(agent_id)
    if row["agent_type"] != "orchestrator":
        raise ValueError(
            f"Only orchestrator agents can write secrets. "
            f"{agent_id} is of type '{row['agent_type']}'."
        )

    result = execute_returning(
        """INSERT INTO secrets (plane, key, value, updated_at)
           VALUES (%s, %s, %s, now())
           ON CONFLICT (plane, key) DO UPDATE
             SET value = EXCLUDED.value, updated_at = now()
           RETURNING id, plane, key, created_at, updated_at""",
        (plane, key, value),
    )
    # Don't return the value (security — only the confirmation)
    return result


def get_secret(agent_id: str, plane: str, key: str) -> dict | None:
    """Read a specific secret by plane + key. Any non-decommissioned agent can read."""
    require_active_agent(agent_id)
    return execute_one(
        "SELECT plane, key, value, created_at, updated_at FROM secrets WHERE plane = %s AND key = %s",
        (plane, key),
    )


def list_secrets_for_plane(agent_id: str, plane: str) -> list[dict]:
    """List all secret KEYS for a plane (values NOT returned, use get_secret for that).

    Useful for iterators/SMEs to discover what creds are available.
    """
    require_active_agent(agent_id)
    return execute(
        "SELECT plane, key, created_at, updated_at FROM secrets WHERE plane = %s ORDER BY key",
        (plane,),
    )


def delete_secret(agent_id: str, plane: str, key: str) -> dict:
    """Delete a secret. Only orchestrator can delete. Returns {'deleted': bool}."""
    row = require_active_agent(agent_id)
    if row["agent_type"] != "orchestrator":
        raise ValueError(
            f"Only orchestrator agents can delete secrets. "
            f"{agent_id} is of type '{row['agent_type']}'."
        )

    from shared.db import execute_mutate
    rowcount = execute_mutate(
        "DELETE FROM secrets WHERE plane = %s AND key = %s",
        (plane, key),
    )
    return {"deleted": rowcount > 0}

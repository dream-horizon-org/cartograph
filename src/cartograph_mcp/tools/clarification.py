"""Clarification tools — create, respond, read.

State machine (see TRIGGER-MANAGEMENT.md §1.3):
  B2 (Blocked-on-responder, initial — responder's turn)
    → B1 (responder asks for more detail)
    → QR (responder rejects)
    → QC (responder answers)
  B1 (Blocked-on-asker)
    → B2 (asker elaborates)
    → QC (asker closes, not needed anymore)
  QC (Query Clarified — asker's turn to ack)
    → CC (asker accepts)
    → B2 (asker not satisfied, back to responder)
  QR (Query Rejected — asker's turn to ack)
    → CC (asker acks rejection)
  CC (Clarification Completed) — terminal

Any active agent can ask any other active agent (or 'admin').
"""

from __future__ import annotations

import json

from shared.actor_auth import require_active_agent
from shared.db import execute, execute_one, execute_returning


_ASKER_TRANSITIONS = {
    "B1": {"B2", "QC"},
    "B2": set(),
    "QC": {"CC", "B2"},
    "QR": {"CC"},
    "CC": set(),
}
_RESPONDER_TRANSITIONS = {
    "B2": {"B1", "QR", "QC"},
    "B1": set(),
    "QC": set(),
    "QR": set(),
    "CC": set(),
}


_caller = require_active_agent  # thin alias; see shared.actor_auth


def create_clarification(
    asker_agent_id: str, responder_agent_id: str, question_message: str
) -> dict:
    """Create a clarification (status=B2) + initial question communication.

    Asker must be active. Responder must be active or 'admin'.
    """
    if not question_message.strip():
        raise ValueError("question_message cannot be empty")
    _caller(asker_agent_id)
    if responder_agent_id != "admin":
        _caller(responder_agent_id)
    if responder_agent_id == asker_agent_id:
        raise ValueError("asker and responder must differ")

    row = execute_returning(
        """INSERT INTO clarifications (asker_agent_id, responder_agent_id, status)
           VALUES (%s, %s, 'B2')
           RETURNING *""",
        (asker_agent_id, responder_agent_id),
    )
    metadata = {
        "state_transition": {"from": None, "to": "B2"},
        "role": "asker",
    }
    execute(
        """INSERT INTO communications (from_agent, to_agent, type, source_id, text, metadata)
           VALUES (%s, %s, 'clarification', %s, %s, %s::jsonb)""",
        (asker_agent_id, responder_agent_id, row["id"], question_message,
         json.dumps(metadata)),
    )
    return row


def respond_clarification(
    agent_id: str, clarification_id: str, message: str, new_status: str
) -> dict:
    """Asker or responder responds with a state transition + message."""
    if not message.strip():
        raise ValueError("message cannot be empty")

    clar = execute_one(
        "SELECT * FROM clarifications WHERE id = %s", (clarification_id,)
    )
    if clar is None:
        raise ValueError(f"Clarification {clarification_id} not found")

    is_asker = clar["asker_agent_id"] == agent_id
    is_responder = clar["responder_agent_id"] == agent_id
    if not (is_asker or is_responder):
        raise ValueError(
            f"Agent {agent_id} is not a participant in clarification "
            f"{clarification_id} (asker={clar['asker_agent_id']}, "
            f"responder={clar['responder_agent_id']})"
        )

    current = clar["status"]
    allowed = (_ASKER_TRANSITIONS if is_asker else _RESPONDER_TRANSITIONS).get(current, set())
    if new_status not in allowed:
        role = "asker" if is_asker else "responder"
        raise ValueError(
            f"Invalid transition {current} → {new_status} for {role}. "
            f"Allowed: {sorted(allowed) if allowed else 'none (not your turn or terminal)'}"
        )

    updated = execute_returning(
        """UPDATE clarifications
           SET status = %s, updated_at = now()
           WHERE id = %s
           RETURNING *""",
        (new_status, clarification_id),
    )

    other = clar["responder_agent_id"] if is_asker else clar["asker_agent_id"]
    metadata = {
        "state_transition": {"from": current, "to": new_status},
        "role": "asker" if is_asker else "responder",
    }
    execute(
        """INSERT INTO communications (from_agent, to_agent, type, source_id, text, metadata)
           VALUES (%s, %s, 'clarification', %s, %s, %s::jsonb)""",
        (agent_id, other, clarification_id, message, json.dumps(metadata)),
    )
    return updated


def get_my_clarifications(agent_id: str) -> list[dict]:
    """Non-terminal clarifications where agent is asker or responder."""
    _caller(agent_id)
    return execute(
        """SELECT * FROM clarifications
           WHERE (asker_agent_id = %s OR responder_agent_id = %s)
             AND status != 'CC'
           ORDER BY updated_at DESC""",
        (agent_id, agent_id),
    )


def get_clarification_thread(
    agent_id: str, clarification_id: str, page: int = 1, limit: int = 50
) -> list[dict]:
    """Paginated thread; scoped to asker + responder (admin is also valid
    as the responder_agent_id literal value)."""
    clar = execute_one(
        "SELECT * FROM clarifications WHERE id = %s", (clarification_id,)
    )
    if clar is None:
        raise ValueError(f"Clarification {clarification_id} not found")
    if clar["asker_agent_id"] != agent_id and clar["responder_agent_id"] != agent_id:
        return []
    offset = (page - 1) * limit
    return execute(
        """SELECT * FROM communications
           WHERE type = 'clarification' AND source_id = %s
           ORDER BY created_at
           LIMIT %s OFFSET %s""",
        (clarification_id, limit, offset),
    )

"""Consolidation tools — nominate, respond, review, read.

State machine (see TRIGGER-MANAGEMENT.md §1.1):
  B2 (Blocked-on-agent-b, initial — nominated agent's turn)
    → B1 (nominated responds, flips to nominator)
    → R  (escalate to resolver — only if r_conf IS NOT NULL)
  B1 (Blocked-on-agent-a, nominator's turn)
    → B2 (nominator responds, flips to nominated)
    → R  (escalate to resolver — only if r_conf IS NOT NULL)
  R  (Resolver review)
    → B1 / B2 (resolver sends back for more info)
    → F       (resolver rejects)
    → M       (resolver approves — MUST set mutation_assigned_to)
  M  (Mutation in progress) — Phase 4
  MD (Materialisation done) — Phase 4
  D  (Done) — Phase 4
  F  (Failed / rejected) — terminal

System auto-transitions (Phase 3 auto_transitions scanner):
  both conf > 0.85 AND r_conf IS NULL → status='R'
  both conf < 0.3                     → status='F'

Manual escalate to R requires r_conf IS NOT NULL (resolver has weighed in
before) — keeps first escalation system-driven, not agent-driven.
"""

from __future__ import annotations

import json

from shared.actor_auth import require_active_agent
from shared.db import execute, execute_one, execute_returning


_VALID_TYPES = {"merge", "split"}

# Transitions by role, keyed by current status → set of allowed next states.
_AGENT_A_TRANSITIONS = {
    "B1": {"B2", "R"},   # nominator's turn
    "B2": set(),          # not nominator's turn
    "R": set(),
    "M": set(), "MD": set(), "D": set(), "F": set(),
}
_AGENT_B_TRANSITIONS = {
    "B2": {"B1", "R"},   # nominated's turn
    "B1": set(),          # not nominated's turn
    "R": set(),
    "M": set(), "MD": set(), "D": set(), "F": set(),
}
_RESOLVER_TRANSITIONS = {
    "R":  {"B1", "B2", "F", "M"},
    "MD": {"D"},
    "B1": set(), "B2": set(), "M": set(), "D": set(), "F": set(),
}


_caller = require_active_agent  # thin alias; see shared.actor_auth


def _sme_component_owner(component_id: str) -> str | None:
    """Return the agent_id of the SME owning this (active) component, or None."""
    row = execute_one(
        """SELECT rca.agent_id
           FROM resource_component_agents rca
           JOIN components c ON c.id = rca.component_id
           WHERE rca.component_id = %s AND c.status != 'decommissioned'
           LIMIT 1""",
        (component_id,),
    )
    return row["agent_id"] if row else None


# ============ nominate_consolidation ============


def nominate_consolidation(
    agent_id: str,
    component_a_id: str,
    component_b_id: str | None,
    nomination_type: str,
    confidence: float,
    message: str,
) -> dict:
    """SME proposes merge (component_a + component_b) or split (component_a
    into a new child). Creates consolidation row (status=B2) + communication.

    For merge: component_b_id required, must be owned by a different SME.
    For split: component_b_id optional — the child component doesn't exist
      yet; it's spawned in Phase 4's spawn_child_agent after resolver approval.
    """
    sme = _caller(agent_id)
    if sme["agent_type"] != "sme":
        raise ValueError(
            f"Only SMEs can nominate consolidations. "
            f"{agent_id} is of type '{sme['agent_type']}'."
        )
    if nomination_type not in _VALID_TYPES:
        raise ValueError(
            f"nomination_type must be one of {sorted(_VALID_TYPES)}"
        )
    if not message.strip():
        raise ValueError("message cannot be empty")
    try:
        confidence = float(confidence)
    except (TypeError, ValueError) as e:
        raise ValueError("confidence must be a number") from e
    if not (0.0 <= confidence <= 1.0):
        raise ValueError("confidence must be in [0.0, 1.0]")

    owner_a = _sme_component_owner(component_a_id)
    if owner_a != agent_id:
        raise ValueError(
            f"SME {agent_id} does not own component_a {component_a_id}. "
            "You can only nominate from your own component."
        )

    agent_b_id: str | None = None
    if nomination_type == "merge":
        if not component_b_id:
            raise ValueError("component_b_id is required for merge nominations")
        if component_b_id == component_a_id:
            raise ValueError("component_a_id and component_b_id must differ")
        agent_b_id = _sme_component_owner(component_b_id)
        if agent_b_id is None:
            raise ValueError(
                f"component_b {component_b_id} has no active owner — cannot merge"
            )
        if agent_b_id == agent_id:
            raise ValueError(
                "Cannot merge two components owned by the same SME — "
                "use upsert_component to update in place instead."
            )

    row = execute_returning(
        """INSERT INTO consolidations
           (proposed_by, agent_a_id, agent_b_id, component_a_id, component_b_id,
            nomination_type, a_conf_score, status)
           VALUES (%s, %s, %s, %s, %s, %s, %s, 'B2')
           RETURNING *""",
        (
            agent_id, agent_id, agent_b_id, component_a_id, component_b_id,
            nomination_type, confidence,
        ),
    )

    # Announce. Target: for merge → agent_b (nominated); for split → admin
    # (self-nominated — initial responder is the system / resolver once
    # auto-transition hits).
    to_agent = agent_b_id if agent_b_id else "admin"
    metadata = {
        "state_transition": {"from": None, "to": "B2"},
        "nomination_type": nomination_type,
        "a_conf_score": confidence,
    }
    execute(
        """INSERT INTO communications (from_agent, to_agent, type, source_id, text, metadata)
           VALUES (%s, %s, 'consolidation', %s, %s, %s::jsonb)""",
        (agent_id, to_agent, row["id"], message, json.dumps(metadata)),
    )
    return row


# ============ respond_consolidation ============


def respond_consolidation(
    agent_id: str,
    consolidation_id: str,
    confidence: float,
    message: str,
    new_status: str,
) -> dict:
    """Nominator or nominated responds with updated confidence + state change.

    Writes the appropriate conf score (a_conf_score if nominator, b_conf_score
    if nominated) + appends communication with state_transition metadata.
    """
    if not message.strip():
        raise ValueError("message cannot be empty")
    try:
        confidence = float(confidence)
    except (TypeError, ValueError) as e:
        raise ValueError("confidence must be a number") from e
    if not (0.0 <= confidence <= 1.0):
        raise ValueError("confidence must be in [0.0, 1.0]")

    cons = execute_one(
        "SELECT * FROM consolidations WHERE id = %s", (consolidation_id,)
    )
    if cons is None:
        raise ValueError(f"Consolidation {consolidation_id} not found")

    is_a = cons["agent_a_id"] == agent_id
    is_b = cons["agent_b_id"] == agent_id
    if not (is_a or is_b):
        raise ValueError(
            f"Agent {agent_id} is not a participant in consolidation "
            f"{consolidation_id} (agent_a={cons['agent_a_id']}, "
            f"agent_b={cons['agent_b_id']})"
        )

    current = cons["status"]
    allowed = (_AGENT_A_TRANSITIONS if is_a else _AGENT_B_TRANSITIONS).get(current, set())
    if new_status not in allowed:
        role = "agent_a (nominator)" if is_a else "agent_b (nominated)"
        raise ValueError(
            f"Invalid transition {current} → {new_status} for {role}. "
            f"Allowed: {sorted(allowed) if allowed else 'none (not your turn or terminal)'}"
        )
    # Manual escalate to R requires r_conf_score set.
    if new_status == "R" and cons["r_conf_score"] is None:
        raise ValueError(
            "Cannot escalate to R manually unless resolver has set r_conf_score. "
            "Let the auto-transitions scanner (both > 0.85) handle first escalation."
        )

    conf_col = "a_conf_score" if is_a else "b_conf_score"
    updated = execute_returning(
        f"""UPDATE consolidations
           SET {conf_col} = %s, status = %s, updated_at = now()
           WHERE id = %s
           RETURNING *""",
        (confidence, new_status, consolidation_id),
    )

    # Communication: from=caller, to=other agent.
    other = cons["agent_b_id"] if is_a else cons["agent_a_id"]
    if not other:
        other = "admin"
    metadata = {
        "state_transition": {"from": current, "to": new_status},
        "role": "agent_a" if is_a else "agent_b",
        conf_col: confidence,
    }
    execute(
        """INSERT INTO communications (from_agent, to_agent, type, source_id, text, metadata)
           VALUES (%s, %s, 'consolidation', %s, %s, %s::jsonb)""",
        (agent_id, other, consolidation_id, message, json.dumps(metadata)),
    )
    return updated


# ============ review_consolidation ============


def review_consolidation(
    agent_id: str,
    consolidation_id: str,
    r_confidence: float,
    message: str,
    new_status: str,
    mutation_assigned_to: str | None = None,
) -> dict:
    """Resolver reviews. R → B1/B2/F/M (M requires mutation_assigned_to).

    Merge: mutation_assigned_to = the agent with more planes of attribution
           (caller picks, not enforced here — Resolver judgment).
    Split: mutation_assigned_to = agent_a (self-nominator) — enforced here.
    """
    caller = _caller(agent_id)
    if caller["agent_type"] != "resolver":
        raise ValueError(
            f"Only resolvers can review consolidations. "
            f"{agent_id} is of type '{caller['agent_type']}'."
        )
    if not message.strip():
        raise ValueError("message cannot be empty")
    try:
        r_confidence = float(r_confidence)
    except (TypeError, ValueError) as e:
        raise ValueError("r_confidence must be a number") from e
    if not (0.0 <= r_confidence <= 1.0):
        raise ValueError("r_confidence must be in [0.0, 1.0]")

    cons = execute_one(
        "SELECT * FROM consolidations WHERE id = %s", (consolidation_id,)
    )
    if cons is None:
        raise ValueError(f"Consolidation {consolidation_id} not found")

    current = cons["status"]
    allowed = _RESOLVER_TRANSITIONS.get(current, set())
    if new_status not in allowed:
        raise ValueError(
            f"Invalid resolver transition {current} → {new_status}. "
            f"Allowed: {sorted(allowed) if allowed else 'none'}"
        )

    if new_status == "M":
        if not mutation_assigned_to:
            raise ValueError(
                "mutation_assigned_to is required when transitioning to M"
            )
        # Enforce split rule: mutation_assigned_to must be agent_a.
        if cons["nomination_type"] == "split" and mutation_assigned_to != cons["agent_a_id"]:
            raise ValueError(
                f"For split nominations, mutation_assigned_to must be agent_a "
                f"({cons['agent_a_id']}), not {mutation_assigned_to}"
            )
        if cons["nomination_type"] == "merge" and mutation_assigned_to not in (
            cons["agent_a_id"], cons["agent_b_id"],
        ):
            raise ValueError(
                f"For merge nominations, mutation_assigned_to must be agent_a "
                f"({cons['agent_a_id']}) or agent_b ({cons['agent_b_id']})"
            )

    updated = execute_returning(
        """UPDATE consolidations
           SET r_conf_score = %s,
               status = %s,
               mutation_assigned_to = COALESCE(%s, mutation_assigned_to),
               resolved_by = %s,
               resolved_at = CASE WHEN %s IN ('F','D') THEN now() ELSE resolved_at END,
               updated_at = now()
           WHERE id = %s
           RETURNING *""",
        (
            r_confidence, new_status, mutation_assigned_to, agent_id,
            new_status, consolidation_id,
        ),
    )

    # Communication: from=resolver, to=the agent whose turn it becomes (or
    # mutation_assigned_to, or admin if terminal).
    if new_status in ("B1",):
        to_agent = cons["agent_a_id"]
    elif new_status == "B2":
        to_agent = cons["agent_b_id"] or "admin"
    elif new_status == "M":
        to_agent = mutation_assigned_to
    else:  # F
        to_agent = cons["agent_a_id"]
    metadata = {
        "state_transition": {"from": current, "to": new_status},
        "role": "resolver",
        "r_conf_score": r_confidence,
    }
    if mutation_assigned_to:
        metadata["mutation_assigned_to"] = mutation_assigned_to
    execute(
        """INSERT INTO communications (from_agent, to_agent, type, source_id, text, metadata)
           VALUES (%s, %s, 'consolidation', %s, %s, %s::jsonb)""",
        (agent_id, to_agent, consolidation_id, message, json.dumps(metadata)),
    )
    return updated


# ============ Mutation lifecycle (Phase 4) ============


def execute_mutation(agent_id: str, consolidation_id: str, message: str) -> dict:
    """M → MD transition. Signals the mutation work (absorb/spawn/transfer
    attributions) has been applied atomically inside this consolidation's
    scope. Gated on:
      - caller = consolidation.mutation_assigned_to
      - consolidation.status = 'M'

    The body mutations (absorb_agent / spawn_child_agent / transfer_attributions)
    are INDEPENDENT MCP tools the caller invokes before OR after this
    transition — Phase 4 does not bundle them in one transaction because
    the `source_slice` carve + agent-state flip need to stay observable
    per-step in the resolver thread. This tool is the gate that says
    "I am done with the mutation work; please verify (MD)."
    """
    caller = _caller(agent_id)
    if not message or not message.strip():
        raise ValueError("message is required")
    cons = execute_one(
        "SELECT * FROM consolidations WHERE id = %s", (consolidation_id,)
    )
    if cons is None:
        raise ValueError(f"Consolidation {consolidation_id} not found")
    if cons["status"] != "M":
        raise ValueError(
            f"execute_mutation requires status='M'; current is '{cons['status']}'"
        )
    if cons["mutation_assigned_to"] != agent_id:
        raise ValueError(
            f"Only the mutation_assigned_to agent may execute_mutation on "
            f"consolidation {consolidation_id}. "
            f"(assigned: {cons['mutation_assigned_to']}, caller: {agent_id})"
        )

    updated = execute_returning(
        """UPDATE consolidations
           SET status = 'MD', updated_at = now()
           WHERE id = %s
           RETURNING *""",
        (consolidation_id,),
    )
    metadata = {
        "state_transition": {"from": "M", "to": "MD"},
        "role": "mutation_assigned",
    }
    execute(
        """INSERT INTO communications (from_agent, to_agent, type, source_id, text, metadata)
           VALUES (%s, %s, 'consolidation', %s, %s, %s::jsonb)""",
        # MD is the resolver's turn to verify.
        (agent_id, "resolver", consolidation_id, message, json.dumps(metadata)),
    )
    return updated


def complete_consolidation(agent_id: str, consolidation_id: str, message: str) -> dict:
    """MD → D transition. Resolver verifies the mutation landed correctly
    and closes the consolidation. Gated on:
      - caller.agent_type = 'resolver'
      - consolidation.status = 'MD'
    """
    caller = _caller(agent_id)
    if caller["agent_type"] != "resolver":
        raise ValueError(
            f"Only resolver agents may complete_consolidation. "
            f"{agent_id} is type '{caller['agent_type']}'."
        )
    if not message or not message.strip():
        raise ValueError("message is required")
    cons = execute_one(
        "SELECT * FROM consolidations WHERE id = %s", (consolidation_id,)
    )
    if cons is None:
        raise ValueError(f"Consolidation {consolidation_id} not found")
    if cons["status"] != "MD":
        raise ValueError(
            f"complete_consolidation requires status='MD'; current is '{cons['status']}'"
        )

    updated = execute_returning(
        """UPDATE consolidations
           SET status = 'D',
               resolved_by = %s,
               resolved_at = now(),
               updated_at = now()
           WHERE id = %s
           RETURNING *""",
        (agent_id, consolidation_id),
    )
    metadata = {
        "state_transition": {"from": "MD", "to": "D"},
        "role": "resolver",
    }
    # Notify both parties so their triggers see the terminal state.
    notify = [cons["agent_a_id"]]
    if cons["agent_b_id"]:
        notify.append(cons["agent_b_id"])
    for to_agent in notify:
        execute(
            """INSERT INTO communications (from_agent, to_agent, type, source_id, text, metadata)
               VALUES (%s, %s, 'consolidation', %s, %s, %s::jsonb)""",
            (agent_id, to_agent, consolidation_id, message, json.dumps(metadata)),
        )
    return updated


# ============ Reads ============


def get_my_consolidations(agent_id: str) -> list[dict]:
    """All consolidations involving this agent (as nominator or nominated),
    or ALL non-terminal ones if agent is a resolver (they see everything).
    """
    caller = _caller(agent_id)
    if caller["agent_type"] == "resolver":
        return execute(
            """SELECT * FROM consolidations
               WHERE status NOT IN ('D','F')
               ORDER BY updated_at DESC"""
        )
    return execute(
        """SELECT * FROM consolidations
           WHERE (agent_a_id = %s OR agent_b_id = %s)
             AND status NOT IN ('D','F')
           ORDER BY updated_at DESC""",
        (agent_id, agent_id),
    )


def get_consolidation_thread(
    agent_id: str, consolidation_id: str, page: int = 1, limit: int = 50
) -> list[dict]:
    """Paginated thread. Scoped: only participants (agent_a, agent_b) or
    resolver can read. Non-participants get an empty list.
    """
    caller = _caller(agent_id)
    cons = execute_one(
        "SELECT * FROM consolidations WHERE id = %s", (consolidation_id,)
    )
    if cons is None:
        raise ValueError(f"Consolidation {consolidation_id} not found")
    is_participant = (
        cons["agent_a_id"] == agent_id
        or cons["agent_b_id"] == agent_id
        or caller["agent_type"] == "resolver"
    )
    if not is_participant:
        return []
    offset = (page - 1) * limit
    return execute(
        """SELECT * FROM communications
           WHERE type = 'consolidation' AND source_id = %s
           ORDER BY created_at
           LIMIT %s OFFSET %s""",
        (consolidation_id, limit, offset),
    )

"""Phase 5.5: terminal-state communications auto-ack at write site.

When a tool transitions an entity to a terminal state for the recipient
(task TC, consolidation D/F, clarification CC/QR), the announcing
communication row gets `acked_at` pre-stamped so the recipient's inbox
doesn't keep re-notifying about already-closed work.
"""

import pytest

from shared.db import execute_one, execute_mutate, execute_returning
from cartograph_mcp.tools import (
    components, consolidation, clarification, tasks,
)


def _comp(agent_id: str, name: str) -> str:
    return components.upsert_component(agent_id, {
        "canonical_name": name, "display_name": name,
        "component_type": "application",
    })["id"]


def _consolidation_at(agent_factory, status: str) -> dict:
    """Set up a consolidation in `status` with both agents wired."""
    agent_factory("iter1", "iterator")
    from shared.db import execute_mutate as em
    em("UPDATE agent_runs SET plane='github', status='idle' WHERE agent_id='iter1'")
    # SMEs need a resource + RCA reservation slot before upsert_component.
    from cartograph_mcp.tools import resources as _res
    r1 = _res.upsert_resource("iter1", "github", "repo", "o/p")
    r2 = _res.upsert_resource("iter1", "github", "repo", "o/q")
    agent_factory("sme-a", "sme")
    agent_factory("sme-b", "sme")
    em("INSERT INTO resource_component_agents (resource_id, agent_id) "
       "VALUES (%s, 'sme-a')", (r1["id"],))
    em("INSERT INTO resource_component_agents (resource_id, agent_id) "
       "VALUES (%s, 'sme-b')", (r2["id"],))
    ca = _comp("sme-a", "ca")
    cb = _comp("sme-b", "cb")
    agent_factory("res", "resolver")
    cons = consolidation.nominate_consolidation(
        "sme-a", ca, cb, "merge", 0.9, "merge"
    )
    em("UPDATE consolidations SET status=%s, "
       "    a_conf_score=0.9, b_conf_score=0.9 "
       "WHERE id=%s",
       (status, cons["id"]))
    return cons


# ---------- task ----------

def test_task_TC_autoacks_announcement(agent_factory):
    agent_factory("admin", "orchestrator")
    agent_factory("worker", "sme")
    t = execute_returning(
        """INSERT INTO tasks (owner_agent_id, worker_agent_id, description, status)
           VALUES ('admin', 'worker', 'do thing', 'WD') RETURNING *""",
    )
    tasks.respond_task("admin", str(t["id"]), "accepted", "TC")
    rows = execute_one(
        "SELECT acked_at FROM communications "
        "WHERE source_id=%s::uuid ORDER BY created_at DESC LIMIT 1",
        (t["id"],),
    )
    assert rows["acked_at"] is not None, "TC announcement must be pre-acked"


def test_task_BW_or_BO_does_not_autoack(agent_factory):
    """Non-terminal transitions stay unacked — agents need to react."""
    agent_factory("admin", "orchestrator")
    agent_factory("worker", "sme")
    t = execute_returning(
        """INSERT INTO tasks (owner_agent_id, worker_agent_id, description, status)
           VALUES ('admin', 'worker', 'do thing', 'BW') RETURNING *""",
    )
    tasks.respond_task("worker", str(t["id"]), "blocked", "BO", blocker_detail="x")
    row = execute_one(
        "SELECT acked_at FROM communications "
        "WHERE source_id=%s::uuid ORDER BY created_at DESC LIMIT 1",
        (t["id"],),
    )
    assert row["acked_at"] is None


# ---------- consolidation ----------

def test_consolidation_review_F_autoacks(agent_factory):
    cons = _consolidation_at(agent_factory, "R")
    consolidation.review_consolidation(
        "res", cons["id"], 0.1, "rejected", "F",
    )
    row = execute_one(
        "SELECT acked_at FROM communications "
        "WHERE source_id=%s::uuid ORDER BY created_at DESC LIMIT 1",
        (cons["id"],),
    )
    assert row["acked_at"] is not None


def test_consolidation_complete_D_autoacks(agent_factory):
    cons = _consolidation_at(agent_factory, "MD")
    consolidation.complete_consolidation("res", cons["id"], "verified")
    rows = execute_one(
        "SELECT BOOL_AND(acked_at IS NOT NULL) AS all_acked "
        "FROM communications WHERE source_id=%s::uuid AND type='consolidation' "
        "AND text='verified'",
        (cons["id"],),
    )
    assert rows["all_acked"], "Both D-announcement rows must be pre-acked"


def test_consolidation_review_M_does_not_autoack(agent_factory):
    cons = _consolidation_at(agent_factory, "R")
    consolidation.review_consolidation(
        "res", cons["id"], 0.95, "approved", "M",
        mutation_assigned_to="sme-a",
    )
    row = execute_one(
        "SELECT acked_at FROM communications "
        "WHERE source_id=%s::uuid ORDER BY created_at DESC LIMIT 1",
        (cons["id"],),
    )
    assert row["acked_at"] is None  # M still requires action


# ---------- clarification ----------

def test_clarification_CC_autoacks(agent_factory):
    agent_factory("asker", "sme")
    cl = clarification.create_clarification("asker", "admin", "what")
    # Move to QC first (responder answers).
    execute_mutate(
        "UPDATE clarifications SET status='QC' WHERE id=%s", (cl["id"],)
    )
    clarification.respond_clarification(
        "asker", cl["id"], "thanks", "CC"
    )
    row = execute_one(
        "SELECT acked_at FROM communications "
        "WHERE source_id=%s::uuid ORDER BY created_at DESC LIMIT 1",
        (cl["id"],),
    )
    assert row["acked_at"] is not None


def test_clarification_QR_autoacks(agent_factory):
    agent_factory("asker", "sme")
    cl = clarification.create_clarification("asker", "admin", "what")
    # Responder rejects → QR is terminal-equivalent (asker just needs to ack).
    clarification.respond_clarification(
        "admin", cl["id"], "wont answer", "QR"
    )
    row = execute_one(
        "SELECT acked_at FROM communications "
        "WHERE source_id=%s::uuid ORDER BY created_at DESC LIMIT 1",
        (cl["id"],),
    )
    assert row["acked_at"] is not None


def test_clarification_QC_does_not_autoack(agent_factory):
    """QC is not terminal — asker may still re-ask (B2). Don't ack."""
    agent_factory("asker", "sme")
    cl = clarification.create_clarification("asker", "admin", "what")
    clarification.respond_clarification(
        "admin", cl["id"], "here is the answer", "QC"
    )
    row = execute_one(
        "SELECT acked_at FROM communications "
        "WHERE source_id=%s::uuid ORDER BY created_at DESC LIMIT 1",
        (cl["id"],),
    )
    assert row["acked_at"] is None

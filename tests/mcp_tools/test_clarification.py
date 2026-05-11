"""Phase 3: clarification tools — create / respond / reads."""

import pytest

from shared.db import execute_mutate, execute_one
from cartograph_mcp.tools import clarification


def test_create_clarification_writes_row_and_message(agent_factory):
    agent_factory("sme-a", "sme")
    agent_factory("orch", "orchestrator")
    clar = clarification.create_clarification("sme-a", "orch", "What plane?")
    assert clar["status"] == "B2"
    assert clar["asker_agent_id"] == "sme-a"
    assert clar["responder_agent_id"] == "orch"
    msg = execute_one(
        "SELECT * FROM communications WHERE source_id = %s", (clar["id"],)
    )
    assert msg is not None
    assert msg["text"] == "What plane?"
    assert msg["metadata"]["role"] == "asker"


def test_create_clarification_admin_responder(agent_factory):
    """Admin is a valid responder (literal string, not in agent_runs)."""
    agent_factory("sme-a", "sme")
    clar = clarification.create_clarification("sme-a", "admin", "Help?")
    assert clar["responder_agent_id"] == "admin"


def test_responder_answers_QC(agent_factory):
    agent_factory("sme-a", "sme")
    agent_factory("orch", "orchestrator")
    clar = clarification.create_clarification("sme-a", "orch", "Q?")
    updated = clarification.respond_clarification(
        "orch", clar["id"], "A.", "QC"
    )
    assert updated["status"] == "QC"


def test_responder_rejects_QR(agent_factory):
    agent_factory("sme-a", "sme")
    agent_factory("orch", "orchestrator")
    clar = clarification.create_clarification("sme-a", "orch", "Q?")
    updated = clarification.respond_clarification(
        "orch", clar["id"], "can't answer", "QR"
    )
    assert updated["status"] == "QR"


def test_asker_closes_QC_to_CC(agent_factory):
    agent_factory("sme-a", "sme")
    agent_factory("orch", "orchestrator")
    clar = clarification.create_clarification("sme-a", "orch", "Q?")
    clarification.respond_clarification("orch", clar["id"], "A.", "QC")
    updated = clarification.respond_clarification(
        "sme-a", clar["id"], "thanks", "CC"
    )
    assert updated["status"] == "CC"


def test_asker_reopens_QC_to_B2(agent_factory):
    agent_factory("sme-a", "sme")
    agent_factory("orch", "orchestrator")
    clar = clarification.create_clarification("sme-a", "orch", "Q?")
    clarification.respond_clarification("orch", clar["id"], "A.", "QC")
    updated = clarification.respond_clarification(
        "sme-a", clar["id"], "need more", "B2"
    )
    assert updated["status"] == "B2"


def test_invalid_transition_refused(agent_factory):
    agent_factory("sme-a", "sme")
    agent_factory("orch", "orchestrator")
    clar = clarification.create_clarification("sme-a", "orch", "Q?")
    # Asker can't respond in B2 (responder's turn).
    with pytest.raises(ValueError, match="Invalid transition"):
        clarification.respond_clarification(
            "sme-a", clar["id"], "oops", "QC"
        )


def test_non_participant_cannot_respond(agent_factory):
    agent_factory("sme-a", "sme")
    agent_factory("sme-b", "sme")
    agent_factory("orch", "orchestrator")
    clar = clarification.create_clarification("sme-a", "orch", "Q?")
    with pytest.raises(ValueError, match="not a participant"):
        clarification.respond_clarification(
            "sme-b", clar["id"], "butting in", "QC"
        )


def test_get_my_clarifications_scope(agent_factory):
    agent_factory("sme-a", "sme")
    agent_factory("sme-b", "sme")
    agent_factory("orch", "orchestrator")
    clarification.create_clarification("sme-a", "orch", "Q?")
    assert len(clarification.get_my_clarifications("sme-a")) == 1
    assert len(clarification.get_my_clarifications("sme-b")) == 0


def test_get_clarification_thread_scope(agent_factory):
    agent_factory("sme-a", "sme")
    agent_factory("sme-b", "sme")
    agent_factory("orch", "orchestrator")
    clar = clarification.create_clarification("sme-a", "orch", "Q?")
    t_a = clarification.get_clarification_thread("sme-a", clar["id"])
    assert len(t_a) == 1
    t_b = clarification.get_clarification_thread("sme-b", clar["id"])
    assert t_b == []


def test_asker_equals_responder_refused(agent_factory):
    agent_factory("sme-a", "sme")
    with pytest.raises(ValueError, match="differ"):
        clarification.create_clarification("sme-a", "sme-a", "Q?")

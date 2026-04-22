"""Phase 3: consolidation tools — nominate / respond / review / reads."""

import pytest

from shared.db import execute_mutate, execute_one
from cartograph_mcp.tools import components, consolidation, resources


# ---------- fixtures ----------

def _iter(agent_factory, aid: str, plane: str):
    execute_mutate(
        """INSERT INTO agent_runs (agent_id, agent_type, plane, status)
           VALUES (%s, 'iterator', %s, 'idle')""",
        (aid, plane),
    )
    return aid


def _sme_with_component(agent_factory, iterator_id: str, sme_id: str,
                        identifier: str, cname: str) -> str:
    """Spawn SME + seed resource + create component. Returns component_id."""
    r = resources.upsert_resource(iterator_id, "github", "repo", identifier)
    agent_factory(sme_id, "sme")
    execute_mutate(
        """INSERT INTO resource_component_agents (resource_id, component_id, agent_id)
           VALUES (%s, NULL, %s)""",
        (r["id"], sme_id),
    )
    c = components.upsert_component(sme_id, {
        "canonical_name": cname, "display_name": cname,
        "component_type": "application",
    })
    return c["id"]


# ---------- nominate ----------

def test_nominate_merge_writes_row_and_message(agent_factory):
    _iter(agent_factory, "i", "github")
    ca = _sme_with_component(agent_factory, "i", "sme-a", "o/a", "a")
    cb = _sme_with_component(agent_factory, "i", "sme-b", "o/b", "b")

    cons = consolidation.nominate_consolidation(
        "sme-a", ca, cb, "merge", 0.8, "same hostname"
    )
    assert cons["nomination_type"] == "merge"
    assert cons["status"] == "B2"
    assert cons["agent_a_id"] == "sme-a"
    assert cons["agent_b_id"] == "sme-b"
    assert cons["a_conf_score"] == 0.8
    # Initial comm row written with state_transition metadata.
    msg = execute_one(
        "SELECT * FROM communications WHERE source_id = %s", (cons["id"],)
    )
    assert msg is not None
    assert msg["metadata"]["state_transition"]["to"] == "B2"


def test_nominate_split_no_component_b(agent_factory):
    _iter(agent_factory, "i", "github")
    ca = _sme_with_component(agent_factory, "i", "sme-a", "o/a", "a")
    cons = consolidation.nominate_consolidation(
        "sme-a", ca, None, "split", 0.7, "two entry points"
    )
    assert cons["nomination_type"] == "split"
    assert cons["agent_b_id"] is None
    assert cons["status"] == "B2"


def test_nominator_must_own_component_a(agent_factory):
    _iter(agent_factory, "i", "github")
    ca = _sme_with_component(agent_factory, "i", "sme-a", "o/a", "a")
    cb = _sme_with_component(agent_factory, "i", "sme-b", "o/b", "b")
    # sme-b tries to nominate sme-a's component
    with pytest.raises(ValueError, match="does not own"):
        consolidation.nominate_consolidation(
            "sme-b", ca, cb, "merge", 0.8, "bad"
        )


def test_non_sme_cannot_nominate(agent_factory):
    agent_factory("orch", "orchestrator")
    agent_factory("sme-x", "sme")
    with pytest.raises(ValueError, match="Only SMEs"):
        consolidation.nominate_consolidation(
            "orch", "00000000-0000-0000-0000-000000000000",
            None, "split", 0.5, "no"
        )


def test_merge_rejects_same_owner(agent_factory):
    """Can't merge two components owned by the same SME (structurally impossible
    anyway under the 1-SME=1-component invariant, so this is a belt test)."""
    _iter(agent_factory, "i", "github")
    ca = _sme_with_component(agent_factory, "i", "sme-a", "o/a", "a")
    # Craft a synthetic second component under the same SME via a second
    # resource + RCA row, bypassing upsert_component's invariant check.
    r2 = resources.upsert_resource("i", "github", "repo", "o/a2")
    execute_mutate(
        """INSERT INTO components (canonical_name, display_name, component_type)
           VALUES ('o/a2', 'A2', 'application')"""
    )
    cb = execute_one("SELECT id FROM components WHERE canonical_name = 'o/a2'")
    execute_mutate(
        """INSERT INTO resource_component_agents (resource_id, component_id, agent_id)
           VALUES (%s, %s, 'sme-a')""",
        (r2["id"], cb["id"]),
    )
    with pytest.raises(ValueError, match="same SME"):
        consolidation.nominate_consolidation(
            "sme-a", ca, cb["id"], "merge", 0.9, "x"
        )


def test_bad_confidence_rejected(agent_factory):
    _iter(agent_factory, "i", "github")
    ca = _sme_with_component(agent_factory, "i", "sme-a", "o/a", "a")
    cb = _sme_with_component(agent_factory, "i", "sme-b", "o/b", "b")
    with pytest.raises(ValueError, match="confidence"):
        consolidation.nominate_consolidation(
            "sme-a", ca, cb, "merge", 1.5, "x"
        )


# ---------- respond ----------

def test_agent_b_responds_B2_to_B1(agent_factory):
    _iter(agent_factory, "i", "github")
    ca = _sme_with_component(agent_factory, "i", "sme-a", "o/a", "a")
    cb = _sme_with_component(agent_factory, "i", "sme-b", "o/b", "b")
    cons = consolidation.nominate_consolidation("sme-a", ca, cb, "merge", 0.7, "m")

    updated = consolidation.respond_consolidation(
        "sme-b", cons["id"], 0.8, "agreed — same ALB", "B1"
    )
    assert updated["status"] == "B1"
    assert updated["b_conf_score"] == 0.8
    # Comm row state_transition B2→B1.
    latest = execute_one(
        "SELECT * FROM communications WHERE source_id = %s "
        "ORDER BY created_at DESC LIMIT 1", (cons["id"],)
    )
    assert latest["metadata"]["state_transition"] == {"from": "B2", "to": "B1"}


def test_agent_a_cannot_respond_during_B2(agent_factory):
    _iter(agent_factory, "i", "github")
    ca = _sme_with_component(agent_factory, "i", "sme-a", "o/a", "a")
    cb = _sme_with_component(agent_factory, "i", "sme-b", "o/b", "b")
    cons = consolidation.nominate_consolidation("sme-a", ca, cb, "merge", 0.7, "m")
    with pytest.raises(ValueError, match="Invalid transition"):
        consolidation.respond_consolidation(
            "sme-a", cons["id"], 0.9, "nope", "B2"
        )


def test_non_participant_cannot_respond(agent_factory):
    _iter(agent_factory, "i", "github")
    ca = _sme_with_component(agent_factory, "i", "sme-a", "o/a", "a")
    cb = _sme_with_component(agent_factory, "i", "sme-b", "o/b", "b")
    agent_factory("sme-c", "sme")
    cons = consolidation.nominate_consolidation("sme-a", ca, cb, "merge", 0.7, "m")
    with pytest.raises(ValueError, match="not a participant"):
        consolidation.respond_consolidation(
            "sme-c", cons["id"], 0.5, "butting in", "B1"
        )


def test_manual_escalate_to_R_refused_before_resolver_weighed_in(agent_factory):
    _iter(agent_factory, "i", "github")
    ca = _sme_with_component(agent_factory, "i", "sme-a", "o/a", "a")
    cb = _sme_with_component(agent_factory, "i", "sme-b", "o/b", "b")
    cons = consolidation.nominate_consolidation("sme-a", ca, cb, "merge", 0.9, "m")
    # B2 → R directly by agent_b when r_conf is NULL → refused.
    with pytest.raises(ValueError, match="r_conf_score"):
        consolidation.respond_consolidation(
            "sme-b", cons["id"], 0.9, "escalate", "R"
        )


# ---------- review ----------

def test_resolver_approves_merge_with_mutation_poc(agent_factory):
    _iter(agent_factory, "i", "github")
    ca = _sme_with_component(agent_factory, "i", "sme-a", "o/a", "a")
    cb = _sme_with_component(agent_factory, "i", "sme-b", "o/b", "b")
    agent_factory("res", "resolver")
    cons = consolidation.nominate_consolidation("sme-a", ca, cb, "merge", 0.9, "m")
    # Move to R by back-and-forth to simulate auto-escalation.
    execute_mutate(
        "UPDATE consolidations SET status='R' WHERE id = %s", (cons["id"],)
    )
    reviewed = consolidation.review_consolidation(
        "res", cons["id"], 0.95, "LGTM", "M", "sme-a"
    )
    assert reviewed["status"] == "M"
    assert reviewed["mutation_assigned_to"] == "sme-a"
    assert reviewed["r_conf_score"] == 0.95


def test_resolver_approve_split_forces_agent_a(agent_factory):
    _iter(agent_factory, "i", "github")
    ca = _sme_with_component(agent_factory, "i", "sme-a", "o/a", "a")
    agent_factory("res", "resolver")
    cons = consolidation.nominate_consolidation(
        "sme-a", ca, None, "split", 0.9, "split!"
    )
    execute_mutate(
        "UPDATE consolidations SET status='R' WHERE id = %s", (cons["id"],)
    )
    # Picking a non-agent-a POC is refused.
    with pytest.raises(ValueError, match="agent_a"):
        consolidation.review_consolidation(
            "res", cons["id"], 0.9, "m", "M", "someone-else"
        )
    reviewed = consolidation.review_consolidation(
        "res", cons["id"], 0.9, "m", "M", "sme-a"
    )
    assert reviewed["status"] == "M"


def test_non_resolver_cannot_review(agent_factory):
    _iter(agent_factory, "i", "github")
    ca = _sme_with_component(agent_factory, "i", "sme-a", "o/a", "a")
    cb = _sme_with_component(agent_factory, "i", "sme-b", "o/b", "b")
    cons = consolidation.nominate_consolidation("sme-a", ca, cb, "merge", 0.9, "m")
    with pytest.raises(ValueError, match="Only resolvers"):
        consolidation.review_consolidation(
            "sme-a", cons["id"], 0.9, "m", "M", "sme-a"
        )


# ---------- reads ----------

def test_get_my_consolidations_scope(agent_factory):
    _iter(agent_factory, "i", "github")
    ca = _sme_with_component(agent_factory, "i", "sme-a", "o/a", "a")
    cb = _sme_with_component(agent_factory, "i", "sme-b", "o/b", "b")
    agent_factory("sme-c", "sme")
    consolidation.nominate_consolidation("sme-a", ca, cb, "merge", 0.9, "m")
    assert len(consolidation.get_my_consolidations("sme-a")) == 1
    assert len(consolidation.get_my_consolidations("sme-b")) == 1
    assert len(consolidation.get_my_consolidations("sme-c")) == 0


def test_resolver_sees_all_consolidations(agent_factory):
    _iter(agent_factory, "i", "github")
    ca = _sme_with_component(agent_factory, "i", "sme-a", "o/a", "a")
    cb = _sme_with_component(agent_factory, "i", "sme-b", "o/b", "b")
    agent_factory("res", "resolver")
    consolidation.nominate_consolidation("sme-a", ca, cb, "merge", 0.9, "m")
    assert len(consolidation.get_my_consolidations("res")) == 1


def test_get_consolidation_thread_scope(agent_factory):
    _iter(agent_factory, "i", "github")
    ca = _sme_with_component(agent_factory, "i", "sme-a", "o/a", "a")
    cb = _sme_with_component(agent_factory, "i", "sme-b", "o/b", "b")
    agent_factory("sme-c", "sme")
    cons = consolidation.nominate_consolidation("sme-a", ca, cb, "merge", 0.9, "m")
    # Participant sees the thread.
    thread_a = consolidation.get_consolidation_thread("sme-a", cons["id"])
    assert len(thread_a) == 1
    # Non-participant gets empty.
    thread_c = consolidation.get_consolidation_thread("sme-c", cons["id"])
    assert thread_c == []

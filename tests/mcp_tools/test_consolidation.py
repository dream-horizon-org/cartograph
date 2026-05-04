"""Phase 3: consolidation tools — nominate / respond / review / reads."""

import pytest

from shared.db import execute, execute_mutate, execute_one
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
    # Post-fix: splits go straight to R (no B-negotiation without a counter-party).
    assert cons["status"] == "R"


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


# ---------- Phase 10.1: symmetric-nomination race guard ----------

def test_phase10_1_symmetric_nomination_refused(agent_factory):
    """If A nominates B for merge, B's later nomination of A on the same
    pair must refuse with 'already exists' — pre-INSERT guard catches the
    human-visible 99% of races.
    """
    _iter(agent_factory, "i", "github")
    ca = _sme_with_component(agent_factory, "i", "sme-a", "o/a", "a")
    cb = _sme_with_component(agent_factory, "i", "sme-b", "o/b", "b")

    # A → B succeeds
    consolidation.nominate_consolidation("sme-a", ca, cb, "merge", 0.7, "first")

    # B → A on same pair must refuse
    with pytest.raises(ValueError, match="already exists"):
        consolidation.nominate_consolidation("sme-b", cb, ca, "merge", 0.7, "second")


def test_phase10_1_terminal_pair_does_not_block_re_nomination(agent_factory):
    """If a prior consolidation between the pair landed at F (failed/rejected),
    a fresh nomination should succeed — terminal status doesn't block.
    """
    _iter(agent_factory, "i", "github")
    ca = _sme_with_component(agent_factory, "i", "sme-a", "o/a", "a")
    cb = _sme_with_component(agent_factory, "i", "sme-b", "o/b", "b")

    cons = consolidation.nominate_consolidation("sme-a", ca, cb, "merge", 0.7, "first")
    # Force-terminal: simulate resolver rejection landing at F.
    execute_mutate(
        "UPDATE consolidations SET status = 'F' WHERE id = %s", (cons["id"],),
    )

    # A → B again: now allowed (prior is terminal).
    second = consolidation.nominate_consolidation("sme-a", ca, cb, "merge", 0.7, "retry")
    assert second["id"] != cons["id"]


def test_phase10_1_split_does_not_block_merge(agent_factory):
    """A's prior split nomination on its own component should NOT block a
    later merge nomination between A and B (different nomination_type).
    """
    _iter(agent_factory, "i", "github")
    ca = _sme_with_component(agent_factory, "i", "sme-a", "o/a", "a")
    cb = _sme_with_component(agent_factory, "i", "sme-b", "o/b", "b")

    # Split has component_b_id=NULL, so it can't shadow a future merge pair
    # — but assert it doesn't accidentally either.
    consolidation.nominate_consolidation("sme-a", ca, None, "split", 0.7, "split-first")
    # Merge A↔B succeeds.
    consolidation.nominate_consolidation("sme-a", ca, cb, "merge", 0.7, "merge-after")


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


# ---------- Split fix: solo splits go straight to R ----------

def test_split_nomination_starts_at_R_not_B2(agent_factory):
    _iter(agent_factory, "i", "github")
    ca = _sme_with_component(agent_factory, "i", "sme-a", "o/a", "a")
    cons = consolidation.nominate_consolidation(
        "sme-a", ca, None, "split", 0.9, "two concerns"
    )
    # Pre-fix this landed at B2 and got stuck (no agent_b to respond;
    # auto-escalate requires BOTH scores set). Now it skips B-negotiation.
    assert cons["status"] == "R"
    assert cons["agent_b_id"] is None
    # Initial announcement goes to the resolver, not admin.
    msg = execute_one(
        "SELECT to_agent, metadata FROM communications WHERE source_id=%s",
        (cons["id"],),
    )
    assert msg["to_agent"] == "resolver"
    assert msg["metadata"]["state_transition"]["to"] == "R"


def test_merge_nomination_still_starts_at_B2(agent_factory):
    """Merge flow unchanged — still needs B2 → B1 negotiation."""
    _iter(agent_factory, "i", "github")
    ca = _sme_with_component(agent_factory, "i", "sme-a", "o/a", "a")
    cb = _sme_with_component(agent_factory, "i", "sme-b", "o/b", "b")
    cons = consolidation.nominate_consolidation(
        "sme-a", ca, cb, "merge", 0.9, "same host"
    )
    assert cons["status"] == "B2"
    assert cons["agent_b_id"] == "sme-b"


def test_resolver_cannot_send_solo_split_back_to_B2(agent_factory):
    _iter(agent_factory, "i", "github")
    ca = _sme_with_component(agent_factory, "i", "sme-a", "o/a", "a")
    agent_factory("res", "resolver")
    cons = consolidation.nominate_consolidation(
        "sme-a", ca, None, "split", 0.9, "split"
    )
    # Already at R from nomination. Resolver tries to bounce back to B2.
    # Post-refactor: split has its own state machine that STRUCTURALLY
    # lacks B2 — the rejection comes from the transition table, not a
    # defensive guard. Allowed targets from R (split): B1, F, M.
    with pytest.raises(ValueError, match="Invalid resolver transition"):
        consolidation.review_consolidation(
            "res", cons["id"], 0.8, "need more info", "B2", None
        )
    # B1 (back to nominator) remains valid for splits.
    result = consolidation.review_consolidation(
        "res", cons["id"], 0.8, "need more info", "B1", None
    )
    assert result["status"] == "B1"


def test_db_check_constraint_blocks_split_at_B2(agent_factory):
    """Belt-and-suspenders: even if code somehow tried a raw UPDATE, the DB
    refuses a split consolidation at B2."""
    import psycopg
    _iter(agent_factory, "i", "github")
    ca = _sme_with_component(agent_factory, "i", "sme-a", "o/a", "a")
    cons = consolidation.nominate_consolidation(
        "sme-a", ca, None, "split", 0.9, "split"
    )
    with pytest.raises((psycopg.errors.CheckViolation, Exception)) as exc_info:
        execute_mutate(
            "UPDATE consolidations SET status='B2' WHERE id=%s",
            (cons["id"],),
        )
    # Unwrap — psycopg raises a generic Exception wrapping CheckViolation.
    assert "consolidation_split_no_b2" in str(exc_info.value)


def test_split_b1_nominator_back_to_R_only(agent_factory):
    """Split at B1 (resolver asked for more info). Nominator's only legal
    target is R — NOT B2 (merge-only state)."""
    _iter(agent_factory, "i", "github")
    ca = _sme_with_component(agent_factory, "i", "sme-a", "o/a", "a")
    agent_factory("res", "resolver")
    cons = consolidation.nominate_consolidation(
        "sme-a", ca, None, "split", 0.9, "split"
    )
    consolidation.review_consolidation(
        "res", cons["id"], 0.7, "need more info", "B1", None
    )
    # Now at B1 — nominator responds.
    with pytest.raises(ValueError, match="Invalid transition"):
        consolidation.respond_consolidation(
            "sme-a", cons["id"], 0.95, "more evidence", "B2"
        )
    # R is the valid target. Manual escalate needs r_conf_score set; the
    # previous review call set it to 0.7, so this should succeed.
    result = consolidation.respond_consolidation(
        "sme-a", cons["id"], 0.95, "more evidence", "R"
    )
    assert result["status"] == "R"


# ---------- Phase 4: mutation lifecycle ----------

def _seed_m_state(agent_factory):
    """Common setup: merge consolidation in state M with sme-a as mutation_assigned_to."""
    _iter(agent_factory, "i", "github")
    ca = _sme_with_component(agent_factory, "i", "sme-a", "o/a", "a")
    cb = _sme_with_component(agent_factory, "i", "sme-b", "o/b", "b")
    agent_factory("res", "resolver")
    cons = consolidation.nominate_consolidation("sme-a", ca, cb, "merge", 0.9, "m")
    execute_mutate("UPDATE consolidations SET status='R' WHERE id=%s", (cons["id"],))
    consolidation.review_consolidation(
        "res", cons["id"], 0.95, "approve", "M", "sme-a"
    )
    return cons["id"]


def test_execute_mutation_M_to_MD(agent_factory):
    cons_id = _seed_m_state(agent_factory)
    result = consolidation.execute_mutation("sme-a", cons_id, "mutation applied")
    assert result["status"] == "MD"
    # Notification fired to resolver.
    msg = execute_one(
        "SELECT * FROM communications WHERE source_id=%s "
        "ORDER BY created_at DESC LIMIT 1",
        (cons_id,),
    )
    assert msg["to_agent"] == "resolver"
    assert msg["metadata"]["state_transition"]["to"] == "MD"


def test_execute_mutation_refuses_wrong_actor(agent_factory):
    cons_id = _seed_m_state(agent_factory)
    # sme-b is not the mutation_assigned_to — forbidden.
    with pytest.raises(ValueError, match="mutation_assigned_to"):
        consolidation.execute_mutation("sme-b", cons_id, "try")


def test_execute_mutation_refuses_wrong_state(agent_factory):
    cons_id = _seed_m_state(agent_factory)
    execute_mutate("UPDATE consolidations SET status='R' WHERE id=%s", (cons_id,))
    with pytest.raises(ValueError, match="status='M'"):
        consolidation.execute_mutation("sme-a", cons_id, "try")


def test_execute_mutation_requires_message(agent_factory):
    cons_id = _seed_m_state(agent_factory)
    with pytest.raises(ValueError, match="message"):
        consolidation.execute_mutation("sme-a", cons_id, "")


def test_complete_consolidation_MD_to_D(agent_factory):
    cons_id = _seed_m_state(agent_factory)
    consolidation.execute_mutation("sme-a", cons_id, "done")
    result = consolidation.complete_consolidation("res", cons_id, "verified")
    assert result["status"] == "D"
    assert result["resolved_by"] == "res"
    assert result["resolved_at"] is not None


def test_complete_consolidation_refuses_non_resolver(agent_factory):
    cons_id = _seed_m_state(agent_factory)
    consolidation.execute_mutation("sme-a", cons_id, "done")
    with pytest.raises(ValueError, match="Only resolver"):
        consolidation.complete_consolidation("sme-a", cons_id, "nope")


def test_complete_consolidation_refuses_wrong_state(agent_factory):
    cons_id = _seed_m_state(agent_factory)
    # Status is still 'M'; must be MD.
    with pytest.raises(ValueError, match="status='MD'"):
        consolidation.complete_consolidation("res", cons_id, "nope")


def test_complete_consolidation_notifies_both_parties(agent_factory):
    cons_id = _seed_m_state(agent_factory)
    consolidation.execute_mutation("sme-a", cons_id, "done")
    consolidation.complete_consolidation("res", cons_id, "verified")
    # Two terminal notifications, one to agent_a and one to agent_b.
    rows = execute(
        "SELECT to_agent, metadata FROM communications "
        "WHERE source_id=%s AND metadata->'state_transition'->>'to'='D'",
        (cons_id,),
    )
    to_agents = sorted(r["to_agent"] for r in rows)
    assert to_agents == ["sme-a", "sme-b"]


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

"""Phase 3: auto-transitions for consolidation.

The scanner was already coded; these tests lock the behaviour against
the documented thresholds.
"""

from shared.db import execute_mutate, execute_one
from cartograph_mcp.tools import components, consolidation, resources
from trigger_management.scanners import auto_transitions


def _iter(agent_factory, aid, plane):
    execute_mutate(
        """INSERT INTO agent_runs (agent_id, agent_type, plane, status)
           VALUES (%s, 'iterator', %s, 'idle')""",
        (aid, plane),
    )
    return aid


def _sme(agent_factory, iterator_id, sme_id, identifier, cname):
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


def test_auto_escalate_when_both_conf_above_threshold(agent_factory):
    _iter(agent_factory, "i", "github")
    ca = _sme(agent_factory, "i", "sme-a", "o/a", "a")
    cb = _sme(agent_factory, "i", "sme-b", "o/b", "b")
    cons = consolidation.nominate_consolidation("sme-a", ca, cb, "merge", 0.9, "m")
    consolidation.respond_consolidation("sme-b", cons["id"], 0.9, "agree", "B1")
    # Now both scores set high; auto-transitions should flip to R.
    n = auto_transitions.run_auto_transitions()
    assert n >= 1
    row = execute_one(
        "SELECT status FROM consolidations WHERE id = %s", (cons["id"],)
    )
    assert row["status"] == "R"


def test_auto_reject_when_both_conf_below_threshold(agent_factory):
    _iter(agent_factory, "i", "github")
    ca = _sme(agent_factory, "i", "sme-a", "o/a", "a")
    cb = _sme(agent_factory, "i", "sme-b", "o/b", "b")
    cons = consolidation.nominate_consolidation("sme-a", ca, cb, "merge", 0.2, "low")
    consolidation.respond_consolidation("sme-b", cons["id"], 0.1, "nope", "B1")
    n = auto_transitions.run_auto_transitions()
    assert n >= 1
    row = execute_one(
        "SELECT status FROM consolidations WHERE id = %s", (cons["id"],)
    )
    assert row["status"] == "F"


def test_no_auto_transition_when_one_score_mid(agent_factory):
    _iter(agent_factory, "i", "github")
    ca = _sme(agent_factory, "i", "sme-a", "o/a", "a")
    cb = _sme(agent_factory, "i", "sme-b", "o/b", "b")
    cons = consolidation.nominate_consolidation("sme-a", ca, cb, "merge", 0.95, "m")
    consolidation.respond_consolidation("sme-b", cons["id"], 0.55, "meh", "B1")
    auto_transitions.run_auto_transitions()
    row = execute_one(
        "SELECT status FROM consolidations WHERE id = %s", (cons["id"],)
    )
    assert row["status"] == "B1"  # still negotiating

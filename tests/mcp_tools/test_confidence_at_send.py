"""Phase 5.6: confidence-at-send timeline on consolidation messages.

Each consolidation write site stamps `metadata.confidence_at_send`
= {a, b, r} reflecting all three scores AS OF that message. The
consolidations row stays the source of truth for current scores;
the communications thread carries the immutable timeline.
"""

import pytest

from shared.db import execute, execute_mutate, execute_one
from cartograph_mcp.tools import (
    components, consolidation, resources as _res,
)


def _setup_pair(agent_factory):
    agent_factory("iter1", "iterator")
    execute_mutate(
        "UPDATE agent_runs SET plane='github', status='idle' WHERE agent_id='iter1'"
    )
    r1 = _res.upsert_resource("iter1", "github", "repo", "o/p1")
    r2 = _res.upsert_resource("iter1", "github", "repo", "o/p2")
    agent_factory("sme-a", "sme")
    agent_factory("sme-b", "sme")
    execute_mutate(
        "INSERT INTO resource_component_agents (resource_id, agent_id) "
        "VALUES (%s, 'sme-a')", (r1["id"],),
    )
    execute_mutate(
        "INSERT INTO resource_component_agents (resource_id, agent_id) "
        "VALUES (%s, 'sme-b')", (r2["id"],),
    )
    ca = components.upsert_component("sme-a", {
        "canonical_name": "ca", "display_name": "ca",
        "component_type": "application",
    })["id"]
    cb = components.upsert_component("sme-b", {
        "canonical_name": "cb", "display_name": "cb",
        "component_type": "application",
    })["id"]
    agent_factory("res", "resolver")
    return ca, cb


def _last_comm_metadata(consolidation_id: str) -> dict:
    row = execute_one(
        "SELECT metadata FROM communications "
        "WHERE source_id=%s::uuid AND type='consolidation' "
        "ORDER BY created_at DESC LIMIT 1",
        (consolidation_id,),
    )
    return row["metadata"]


def test_nominate_stamps_a_only(agent_factory):
    ca, cb = _setup_pair(agent_factory)
    cons = consolidation.nominate_consolidation(
        "sme-a", ca, cb, "merge", 0.82, "lgtm"
    )
    md = _last_comm_metadata(cons["id"])
    assert md["confidence_at_send"] == {"a": 0.82, "b": None, "r": None}


def test_respond_stamps_full_triple(agent_factory):
    ca, cb = _setup_pair(agent_factory)
    cons = consolidation.nominate_consolidation(
        "sme-a", ca, cb, "merge", 0.82, "lgtm"
    )
    consolidation.respond_consolidation(
        "sme-b", cons["id"], 0.71, "I am unsure", "B1"
    )
    md = _last_comm_metadata(cons["id"])
    # b just wrote 0.71, a is what the row had.
    assert md["confidence_at_send"]["a"] == 0.82
    assert md["confidence_at_send"]["b"] == 0.71
    assert md["confidence_at_send"]["r"] is None


def test_review_stamps_with_r(agent_factory):
    ca, cb = _setup_pair(agent_factory)
    cons = consolidation.nominate_consolidation(
        "sme-a", ca, cb, "merge", 0.9, "lgtm"
    )
    consolidation.respond_consolidation(
        "sme-b", cons["id"], 0.9, "agree", "B1"
    )
    # Force escalation to R (auto-trans would do this, but kick directly).
    execute_mutate(
        "UPDATE consolidations SET status='R' WHERE id=%s", (cons["id"],)
    )
    consolidation.review_consolidation(
        "res", cons["id"], 0.95, "approved", "M",
        mutation_assigned_to="sme-a",
    )
    md = _last_comm_metadata(cons["id"])
    assert md["confidence_at_send"] == {"a": 0.9, "b": 0.9, "r": 0.95}

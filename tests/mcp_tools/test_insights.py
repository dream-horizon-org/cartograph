"""Phase 5.9: record_insight tool + agent_insights lifecycle."""

import pytest

from cartograph_mcp.tools import insights


def test_record_basic(agent_factory):
    agent_factory("sme-x", "sme")
    row = insights.record_insight(
        "sme-x", "tactic_win", "sme.materialisation",
        "vector_search of attributions before upsert avoids dupes",
    )
    assert row["status"] == "open"
    assert row["agent_id"] == "sme-x"
    assert row["kind"] == "tactic_win"


def test_record_with_evidence(agent_factory):
    agent_factory("sme-x", "sme")
    row = insights.record_insight(
        "sme-x", "doc_confusing", "TRIGGER-MANAGEMENT.md §1.1b",
        "split state diagram missed the R→B1 back-edge",
        evidence={"file_paths": ["docs/TRIGGER-MANAGEMENT.md"]},
    )
    assert row["evidence"] == {"file_paths": ["docs/TRIGGER-MANAGEMENT.md"]}


def test_invalid_kind(agent_factory):
    agent_factory("sme-x", "sme")
    with pytest.raises(ValueError, match="Invalid kind"):
        insights.record_insight("sme-x", "bogus", "x", "y")


def test_requires_active_agent():
    with pytest.raises(ValueError, match="not found"):
        insights.record_insight("missing-agent", "tactic_win", "x", "y")


def test_empty_target_or_body_rejected(agent_factory):
    agent_factory("sme-x", "sme")
    with pytest.raises(ValueError, match="target"):
        insights.record_insight("sme-x", "tactic_win", "", "body")
    with pytest.raises(ValueError, match="body"):
        insights.record_insight("sme-x", "tactic_win", "target", "")


def test_list_filter_by_status(agent_factory):
    agent_factory("sme-x", "sme")
    insights.record_insight("sme-x", "tactic_win", "x", "a")
    insights.record_insight("sme-x", "prompt_gap", "y", "b")
    rows = insights.list_insights(status="open")
    assert len(rows) == 2
    rows = insights.list_insights(kind="prompt_gap")
    assert len(rows) == 1
    assert rows[0]["target"] == "y"


def test_triage_flips_status(agent_factory):
    agent_factory("sme-x", "sme")
    r = insights.record_insight("sme-x", "tactic_win", "x", "a")
    out = insights.triage_insight(str(r["id"]), "promoted", "admin", "rolled into prompt")
    assert out["status"] == "promoted"
    assert out["triaged_by"] == "admin"
    assert out["triage_note"] == "rolled into prompt"


def test_triage_invalid_status(agent_factory):
    agent_factory("sme-x", "sme")
    r = insights.record_insight("sme-x", "tactic_win", "x", "a")
    with pytest.raises(ValueError, match="Invalid status"):
        insights.triage_insight(str(r["id"]), "bogus", "admin")


def test_triage_unknown_id():
    with pytest.raises(ValueError, match="not found"):
        insights.triage_insight(
            "00000000-0000-0000-0000-000000000000", "promoted", "admin"
        )

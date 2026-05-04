"""Integration test -- V2 full lifecycle without real Claude CLI or OpenAI."""

import os
import time
from unittest.mock import patch

import pytest
import yaml

from agent_management import db
from main import boot


@pytest.fixture
def tmp_project(tmp_path):
    workspace_root = str(tmp_path / "workspaces")
    os.makedirs(workspace_root, exist_ok=True)
    mcp_config_path = str(tmp_path / "mcp_servers.yaml")
    mcp_config = {"cartograph-db": {"url": "http://localhost:3002"}}
    with open(mcp_config_path, "w") as f:
        yaml.dump(mcp_config, f)
    return {"workspace_root": workspace_root, "mcp_config_path": mcp_config_path}


def test_boot_creates_only_orchestrator(tmp_project):
    """V2 boot creates orchestrator only -- no Resolver."""
    db.init_db()
    trigger_mgr = boot(
        workspace_root=tmp_project["workspace_root"],
        mcp_config_path=tmp_project["mcp_config_path"],
        start_trigger_manager=False,
    )
    agent_types = {
        db.get_agent(t["agent_id"])["agent_type"]
        for t in db.get_pending_triggers()
    }
    assert "orchestrator" in agent_types
    assert "resolver" not in agent_types


def test_boot_is_idempotent(tmp_project):
    db.init_db()
    boot(
        workspace_root=tmp_project["workspace_root"],
        mcp_config_path=tmp_project["mcp_config_path"],
        start_trigger_manager=False,
    )
    boot(
        workspace_root=tmp_project["workspace_root"],
        mcp_config_path=tmp_project["mcp_config_path"],
        start_trigger_manager=False,
    )
    triggers = db.get_pending_triggers()
    agent_ids = {t["agent_id"] for t in triggers}
    assert len(agent_ids) == 1  # only orchestrator


def test_batch_merge_auto_executes_exact_hostname_match(tmp_project):
    """
    Two components sharing an exact hostname attribution are auto-merged
    by the batch merger without any LLM involvement.
    """
    db.init_db()

    comp_a = db.upsert_component(
        canonical_name="feeds-aggregator-v2",
        display_name="feeds-aggregator-v2",
        component_type="application",
    )
    comp_b = db.upsert_component(
        canonical_name="fav2-api-prod",
        display_name="fav2-api-prod",
        component_type="application",
    )
    db.upsert_attribution(
        comp_a, "github", "hostname", "feeds-agg.dream11.local",
        discovered_by="sme-test-a",
    )
    db.upsert_attribution(
        comp_b, "cloud", "hostname", "feeds-agg.dream11.local",
        discovered_by="sme-test-b",
    )
    db.upsert_attribution(
        comp_a, "github", "entry_point", "FeedsApplication.java",
        discovered_by="sme-test-a",
    )
    db.upsert_attribution(
        comp_b, "cloud", "entry_point", "FeedsApplication.java",
        discovered_by="sme-test-b",
    )

    from agent_management.batch_merger import run_batch_merge

    summary = run_batch_merge(re_embed_fn=None)

    assert summary["auto_executed"] >= 1
    assert summary["pending_human"] == 0

    # One component should now be decommissioned
    a = db.get_component(comp_a)
    b = db.get_component(comp_b)
    statuses = {a["status"], b["status"]}
    assert "decommissioned" in statuses
    assert "active" in statuses


def test_batch_merge_blocks_on_edge(tmp_project):
    """Components with an edge between them are not merged (caller/callee)."""
    db.init_db()

    comp_a = db.upsert_component(
        canonical_name="service-alpha",
        display_name="service-alpha",
        component_type="application",
    )
    comp_b = db.upsert_component(
        canonical_name="service-beta",
        display_name="service-beta",
        component_type="application",
    )
    db.upsert_attribution(
        comp_a, "github", "hostname", "shared.dream11.local",
        discovered_by="sme-a",
    )
    db.upsert_attribution(
        comp_b, "cloud", "hostname", "shared.dream11.local",
        discovered_by="sme-b",
    )

    # Create an edge: alpha calls beta
    conn = db._connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO edges
                   (source_id, target_id, edge_type, identifier, discovered_by)
                   VALUES (%s, %s, 'calls', 'GET /api/data', 'sme-a')""",
                (comp_a, comp_b),
            )
        conn.commit()
    finally:
        conn.close()

    from agent_management.batch_merger import run_batch_merge
    summary = run_batch_merge(re_embed_fn=None)

    assert summary["auto_executed"] == 0

    a = db.get_component(comp_a)
    b = db.get_component(comp_b)
    assert a["status"] == "active"
    assert b["status"] == "active"

"""Read-only query layer: same results as the gated tools, but no agent_id."""

import pytest

from shared.db import execute_mutate
from cartograph_mcp.tools import components, resources
from cartograph_mcp import readonly_queries as ro


def _seed_component(canonical="svc/a", display="Service A", ctype="application"):
    """Build one SME-owned component using the real write tools, return its id."""
    execute_mutate(
        """INSERT INTO agent_runs (agent_id, agent_type, plane, status)
           VALUES ('iter-gh', 'iterator', 'github', 'idle')"""
    )
    r = resources.upsert_resource("iter-gh", "github", "repo", canonical)
    execute_mutate(
        """INSERT INTO agent_runs (agent_id, agent_type, status)
           VALUES ('sme-a', 'sme', 'idle')"""
    )
    execute_mutate(
        """INSERT INTO resource_component_agents (resource_id, component_id, agent_id)
           VALUES (%s, NULL, 'sme-a')""",
        (r["id"],),
    )
    comp = components.upsert_component(
        "sme-a",
        {"canonical_name": canonical, "display_name": display,
         "component_type": ctype, "confidence": 0.9},
    )
    return comp["id"]


def test_get_component_no_agent_id():
    cid = _seed_component()
    row = ro.get_component(cid)
    assert row["canonical_name"] == "svc/a"
    assert "embedding" not in row  # lean projection preserved


def test_get_component_missing_raises():
    with pytest.raises(ValueError):
        ro.get_component("00000000-0000-0000-0000-000000000000")


def test_get_component_owner_no_agent_id():
    cid = _seed_component()
    owner = ro.get_component_owner(cid)
    assert owner["owner_agent_id"] == "sme-a"
    assert owner["component_status"] == "active"


def test_get_components_bulk_no_agent_id():
    cid = str(_seed_component())
    out = ro.get_components_bulk([cid, "00000000-0000-0000-0000-000000000000"])
    assert out[cid]["canonical_name"] == "svc/a"
    assert out["00000000-0000-0000-0000-000000000000"] is None


def test_get_components_bulk_rejects_empty():
    with pytest.raises(ValueError):
        ro.get_components_bulk([])


def test_get_attributions_bulk_no_agent_id():
    cid = str(_seed_component())
    out = ro.get_attributions_bulk([cid])
    assert isinstance(out[cid], list)


def test_get_flow_empty_when_no_flows():
    cid = _seed_component()
    # No catalog/flow seeded → empty list, but the query must run cleanly.
    rows = ro.get_flow(cid, "00000000-0000-0000-0000-000000000000")
    assert rows == []


def test_get_flow_inverse_empty_when_no_flows():
    cid = _seed_component()
    rows = ro.get_flow_inverse(cid, "00000000-0000-0000-0000-000000000000")
    assert rows == []


def test_list_resources_for_plane_no_agent_id():
    _seed_component()  # creates iter-gh + a github 'repo' resource
    rows = ro.list_resources_for_plane("github")
    assert any(r["identifier"] == "svc/a" for r in rows)


def test_list_resources_for_plane_invalid_plane():
    with pytest.raises(ValueError):
        ro.list_resources_for_plane("not-a-plane")


def test_get_resource_counts_no_agent_id():
    _seed_component()
    out = ro.get_resource_counts()
    assert "by_plane_status" in out
    assert isinstance(out["by_plane_status"], list)


def test_list_agents_no_agent_id():
    _seed_component()  # creates iter-gh + sme-a
    out = ro.list_agents()
    ids = {a["agent_id"] for a in out["agents"]}
    assert {"iter-gh", "sme-a"} <= ids


def test_get_resource_no_agent_id():
    _seed_component()
    rows = ro.list_resources_for_plane("github")
    rid = rows[0]["id"]
    got = ro.get_resource(rid)
    assert got["identifier"] == "svc/a"
    assert got["plane"] == "github"


def test_get_resource_missing_raises():
    with pytest.raises(ValueError):
        ro.get_resource("00000000-0000-0000-0000-000000000000")


def test_list_all_resources_default_includes_seeded():
    _seed_component()
    rows = ro.list_all_resources()
    assert any(r["identifier"] == "svc/a" for r in rows)


def test_list_all_resources_status_filter_and_invalid():
    _seed_component()
    all_rows = ro.list_all_resources()
    seeded = next(r for r in all_rows if r["identifier"] == "svc/a")
    # filtering by the seeded row's actual status returns it
    filtered = ro.list_all_resources(status=seeded["status"])
    assert any(r["identifier"] == "svc/a" for r in filtered)
    # invalid status raises
    with pytest.raises(ValueError):
        ro.list_all_resources(status="bogus-status")


def test_search_components_no_agent_id():
    _seed_component(canonical="svc/payments", display="Payments")
    rows = ro.search_components(name_pattern="payments")
    assert any(r["canonical_name"] == "svc/payments" for r in rows)


def test_search_components_blank_filter_refused():
    # status defaults to 'active'; pass status=None to force all-None → blank.
    # BlankFilterError subclasses ValueError.
    with pytest.raises(ValueError):
        ro.search_components(status=None)


def test_search_attributions_no_agent_id():
    cid = _seed_component()
    # search by component_id filter (exact eq)
    rows = ro.search_attributions(component_id=cid)
    assert isinstance(rows, list)

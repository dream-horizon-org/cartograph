"""Phase 3.8: components.source_slice CRUD semantics.

Locks in the consistency lens I committed to:
- accepted on create + update via upsert_component
- REPLACE-on-provide (pass a new dict → new dict is written verbatim)
- COALESCE-preserve on omit (omit the key → previous value intact)
- structural validation: must be dict if present
- flows through get_component via SELECT *
"""

import pytest

from shared.db import execute_mutate, execute_one
from cartograph_mcp.tools import components, resources


def _iter(agent_factory, aid: str, plane: str):
    execute_mutate(
        """INSERT INTO agent_runs (agent_id, agent_type, plane, status)
           VALUES (%s, 'iterator', %s, 'idle')""",
        (aid, plane),
    )
    return aid


def _sme(agent_factory, sid: str, resource_id: str):
    agent_factory(sid, "sme")
    execute_mutate(
        """INSERT INTO resource_component_agents (resource_id, component_id, agent_id)
           VALUES (%s, NULL, %s)""",
        (resource_id, sid),
    )


def _slice(resource_id: str, plane="github", paths=(), files=(), manifests=()):
    return {
        str(resource_id): {
            "plane": plane,
            "paths": list(paths),
            "files": list(files),
            "manifests": list(manifests),
        }
    }


def test_source_slice_set_on_create(agent_factory):
    _iter(agent_factory, "i", "github")
    r = resources.upsert_resource("i", "github", "repo", "o/monorepo")
    _sme(agent_factory, "s", r["id"])
    sl = _slice(r["id"], paths=["services/kyc/"], manifests=["deploy/kyc.yaml"])
    c = components.upsert_component("s", {
        "canonical_name": "o/monorepo-kyc", "display_name": "KYC slice",
        "component_type": "application",
        "source_slice": sl,
    })
    assert c["source_slice"] == sl


def test_source_slice_replace_on_update(agent_factory):
    _iter(agent_factory, "i", "github")
    r = resources.upsert_resource("i", "github", "repo", "o/monorepo")
    _sme(agent_factory, "s", r["id"])
    v1 = _slice(r["id"], paths=["services/kyc/"])
    c1 = components.upsert_component("s", {
        "canonical_name": "o/m", "display_name": "M",
        "component_type": "application", "source_slice": v1,
    })
    v2 = _slice(r["id"], paths=["services/kyc/", "services/kyc-api/"])
    c2 = components.upsert_component("s", {
        "canonical_name": "o/m", "display_name": "M",
        "component_type": "application", "source_slice": v2,
    })
    assert c1["id"] == c2["id"]
    assert c2["source_slice"] == v2
    # Verify paths list grew from one to two
    assert len(c2["source_slice"][str(r["id"])]["paths"]) == 2


def test_source_slice_preserved_when_omitted(agent_factory):
    """COALESCE: omit source_slice on a later upsert → previous value intact."""
    _iter(agent_factory, "i", "github")
    r = resources.upsert_resource("i", "github", "repo", "o/m")
    _sme(agent_factory, "s", r["id"])
    v1 = _slice(r["id"], paths=["keep-me/"])
    components.upsert_component("s", {
        "canonical_name": "o/m", "display_name": "M",
        "component_type": "application", "source_slice": v1,
    })
    updated = components.upsert_component("s", {
        "canonical_name": "o/m", "display_name": "M refined",
        "component_type": "application",
        # source_slice omitted
    })
    assert updated["source_slice"] == v1
    assert updated["display_name"] == "M refined"


def test_source_slice_nullable_on_create(agent_factory):
    _iter(agent_factory, "i", "github")
    r = resources.upsert_resource("i", "github", "repo", "o/whole-repo")
    _sme(agent_factory, "s", r["id"])
    c = components.upsert_component("s", {
        "canonical_name": "o/whole-repo", "display_name": "Whole",
        "component_type": "application",
    })
    assert c["source_slice"] is None


def test_source_slice_rejects_non_dict(agent_factory):
    _iter(agent_factory, "i", "github")
    r = resources.upsert_resource("i", "github", "repo", "o/m")
    _sme(agent_factory, "s", r["id"])
    with pytest.raises(ValueError, match="source_slice must be a dict"):
        components.upsert_component("s", {
            "canonical_name": "o/m", "display_name": "M",
            "component_type": "application",
            "source_slice": ["not", "a", "dict"],
        })


def test_source_slice_multi_resource_shape(agent_factory):
    """Components merged across resources carry multiple keys in source_slice.
    Simulates the post-merge state the absorb_agent contract produces."""
    _iter(agent_factory, "i", "github")
    r_repo = resources.upsert_resource("i", "github", "repo", "o/m")
    _iter(agent_factory, "i2", "cloud")
    r_k8s = resources.upsert_resource("i2", "cloud", "k8s_workload", "o/m-deploy")
    _sme(agent_factory, "s", r_repo["id"])
    multi = {
        str(r_repo["id"]): {
            "plane": "github",
            "paths": ["services/m/"],
        },
        str(r_k8s["id"]): {
            "plane": "cloud",
            "k8s_workloads": ["o-m-deploy@prod"],
        },
    }
    c = components.upsert_component("s", {
        "canonical_name": "o/m", "display_name": "M",
        "component_type": "application",
        "source_slice": multi,
    })
    assert set(c["source_slice"].keys()) == {str(r_repo["id"]), str(r_k8s["id"])}
    assert c["source_slice"][str(r_k8s["id"])]["plane"] == "cloud"


def test_source_slice_round_trip_via_get_component(agent_factory):
    """SELECT * returns source_slice — no schema surgery needed in reads."""
    _iter(agent_factory, "i", "github")
    r = resources.upsert_resource("i", "github", "repo", "o/m")
    _sme(agent_factory, "s", r["id"])
    sl = _slice(r["id"], paths=["a/", "b/"], files=["Dockerfile"])
    c = components.upsert_component("s", {
        "canonical_name": "o/m", "display_name": "M",
        "component_type": "application", "source_slice": sl,
    })
    read = components.get_component("s", c["id"])
    assert read["source_slice"] == sl

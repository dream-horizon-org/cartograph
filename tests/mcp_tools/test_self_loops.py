"""Phase 7.3: self-loop bound edges (X → X) accepted.

Loosens the edges_no_self_loop_v2 CHECK constraint so legitimate
self-invocation patterns (cron self-trigger, recursive component-
level calls, service publishing+consuming the same topic) can be
modelled directly without contortions.

Catalog/dangling edges with from=NULL or to=NULL were already allowed.
This phase only affects bound edges where from = to.
"""

import pytest

from shared.db import execute, execute_one, execute_mutate, execute_returning
from cartograph_mcp.tools import components, resources as _res


def _setup_sme_with_component(agent_factory):
    """Spawn an iterator + an SME, give the SME one component, return
    (sme_id, component_id)."""
    agent_factory("iter-sl", "iterator")
    execute_mutate(
        "UPDATE agent_runs SET plane='github', status='idle' WHERE agent_id='iter-sl'"
    )
    r = _res.upsert_resource("iter-sl", "github", "repo", "o/self-loop")
    agent_factory("sme-sl", "sme")
    execute_mutate(
        "INSERT INTO resource_component_agents (resource_id, agent_id) VALUES (%s, 'sme-sl')",
        (r["id"],),
    )
    cid = components.upsert_component("sme-sl", {
        "canonical_name": "selfloop",
        "display_name": "selfloop",
        "component_type": "application",
    })["id"]
    return "sme-sl", cid


def test_self_loop_bound_edge_accepted(agent_factory):
    """A bound edge with from = to must be accepted post-7.3."""
    sme, cid = _setup_sme_with_component(agent_factory)
    # Direct INSERT — components.upsert_edge_outbound also fine but
    # this isolates the constraint check.
    row = execute_returning(
        """INSERT INTO edges (from_component_id, to_component_id, edge_type, identifier, discovered_by)
           VALUES (%s, %s, 'triggers', 'daily-cron', %s)
           RETURNING id, from_component_id, to_component_id""",
        (cid, cid, sme),
    )
    assert row is not None
    assert str(row["from_component_id"]) == str(cid)
    assert str(row["to_component_id"]) == str(cid)


def test_self_loop_idempotent_via_unique_index(agent_factory):
    """Same (from, to, edge_type, identifier) tuple should be deduped
    by edges_bound_unique even when from = to."""
    sme, cid = _setup_sme_with_component(agent_factory)
    execute_mutate(
        """INSERT INTO edges (from_component_id, to_component_id, edge_type, identifier, discovered_by)
           VALUES (%s, %s, 'triggers', 'daily-cron', %s)""",
        (cid, cid, sme),
    )
    # Second insert should violate the partial unique index — confirm
    # the dedup still works.
    with pytest.raises(Exception, match="duplicate"):
        execute_mutate(
            """INSERT INTO edges (from_component_id, to_component_id, edge_type, identifier, discovered_by)
               VALUES (%s, %s, 'triggers', 'daily-cron', %s)""",
            (cid, cid, sme),
        )


def test_self_loop_bundles_with_other_callers(agent_factory):
    """When X self-calls /foo and Y also calls X at /foo, the
    convergence-group key (target=X, edge_type=calls, identifier=/foo)
    matches both — the self-loop joins Y in the bundle. Verified at
    the data-keying level (bundling itself is a FE concern in Graph)."""
    sme, x_cid = _setup_sme_with_component(agent_factory)
    # Spawn a second SME owning Y.
    r2 = _res.upsert_resource("iter-sl", "github", "repo", "o/y-caller")
    agent_factory("sme-y", "sme")
    execute_mutate(
        "INSERT INTO resource_component_agents (resource_id, agent_id) VALUES (%s, 'sme-y')",
        (r2["id"],),
    )
    y_cid = components.upsert_component("sme-y", {
        "canonical_name": "ycaller",
        "display_name": "ycaller",
        "component_type": "application",
    })["id"]
    # X→X self-loop on calls /foo
    execute_mutate(
        """INSERT INTO edges (from_component_id, to_component_id, edge_type, identifier, discovered_by)
           VALUES (%s, %s, 'calls', '/foo', %s)""",
        (x_cid, x_cid, "sme-sl"),
    )
    # Y→X bound on calls /foo
    execute_mutate(
        """INSERT INTO edges (from_component_id, to_component_id, edge_type, identifier, discovered_by)
           VALUES (%s, %s, 'calls', '/foo', %s)""",
        (y_cid, x_cid, "sme-y"),
    )
    # Both edges share (target=X, edge_type=calls, identifier=/foo)
    # — bundling preprocessing groups them by this key.
    rows = execute(
        """SELECT from_component_id, to_component_id FROM edges
           WHERE to_component_id = %s::uuid AND edge_type='calls' AND identifier='/foo'""",
        (x_cid,),
    )
    assert len(rows) == 2
    sources = {str(r["from_component_id"]) for r in rows}
    assert str(x_cid) in sources, "self-loop must be in the bundle"
    assert str(y_cid) in sources, "Y→X must be in the bundle"

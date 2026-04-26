"""Phase 5.3: /api/components + /api/component/{id}/drilldown."""

import uuid

import pytest

from shared.db import execute_returning, execute_mutate


def _new_uuid() -> str:
    return str(uuid.uuid4())


@pytest.fixture
def component_factory():
    _counter = {"n": 0}
    def _make(canonical: str | None = None, display: str | None = None,
              ctype: str = "application", status: str = "active"):
        if canonical is None:
            _counter["n"] += 1
            canonical = f"comp-{_counter['n']}"
        row = execute_returning(
            """INSERT INTO components (canonical_name, display_name, component_type, status)
               VALUES (%s, %s, %s, %s) RETURNING *""",
            (canonical, display or canonical, ctype, status),
        )
        return row
    return _make


@pytest.fixture
def attribution_factory():
    def _make(component_id: str, plane: str = "github",
              resource_type: str = "endpoint", identifier: str | None = None):
        if identifier is None:
            identifier = f"id-{_new_uuid()[:8]}"
        return execute_returning(
            """INSERT INTO attributions (component_id, plane, resource_type, identifier)
               VALUES (%s, %s, %s, %s) RETURNING *""",
            (component_id, plane, resource_type, identifier),
        )
    return _make


# ---------- list endpoint ----------

def test_list_empty(client):
    r = client.get("/api/components")
    assert r.status_code == 200
    assert r.json() == {"components": [], "has_more": False}


def test_list_returns_basic_fields(client, component_factory):
    component_factory("c-one", "C One", "application")
    r = client.get("/api/components")
    assert r.status_code == 200
    body = r.json()
    assert len(body["components"]) == 1
    c = body["components"][0]
    assert c["canonical_name"] == "c-one"
    assert c["component_type"] == "application"
    assert c["status"] == "active"
    assert c["planes"] == []
    assert c["edge_count"] == {"bound": 0, "catalog": 0, "dangling": 0}


def test_catalog_count_sourced_from_catalogs_table(client, component_factory):
    """Phase 7.4.2 regression guard: /api/components.catalog_count must
    count rows in the `catalogs` table, not `edges WHERE from IS NULL`
    (which is always empty post-Phase-7.4 migration)."""
    c = component_factory("ec")
    execute_returning(
        """INSERT INTO catalogs (component_id, kind, identifier, discovered_by)
           VALUES (%s::uuid, 'endpoint', 'GET /a', 'test') RETURNING id""",
        (c["id"],),
    )
    execute_returning(
        """INSERT INTO catalogs (component_id, kind, identifier, discovered_by)
           VALUES (%s::uuid, 'topic', 'orders.created', 'test') RETURNING id""",
        (c["id"],),
    )
    r = client.get("/api/components")
    row = next(x for x in r.json()["components"] if x["canonical_name"] == "ec")
    assert row["edge_count"]["catalog"] == 2


def test_type_filter(client, component_factory):
    component_factory("a", ctype="application")
    component_factory("b", ctype="database")
    r = client.get("/api/components?type=database")
    assert all(c["component_type"] == "database" for c in r.json()["components"])
    assert len(r.json()["components"]) == 1


def test_status_filter_active_default_excludes_decommissioned(client, component_factory):
    component_factory("alive", status="active")
    component_factory("dead", status="decommissioned")
    r = client.get("/api/components?status=active")
    names = [c["canonical_name"] for c in r.json()["components"]]
    assert "alive" in names
    assert "dead" not in names


def test_q_search(client, component_factory):
    component_factory("payments-svc", "Payments Service")
    component_factory("feeds-api", "Feeds API")
    r = client.get("/api/components?q=payments")
    body = r.json()
    assert len(body["components"]) == 1
    assert body["components"][0]["canonical_name"] == "payments-svc"


def test_plane_filter_via_attributions(client, component_factory, attribution_factory):
    c1 = component_factory("with-gh")
    c2 = component_factory("with-cloud")
    attribution_factory(c1["id"], plane="github")
    attribution_factory(c2["id"], plane="cloud")
    r = client.get("/api/components?plane=github")
    names = [c["canonical_name"] for c in r.json()["components"]]
    assert names == ["with-gh"]


def test_planes_aggregated_per_component(client, component_factory, attribution_factory):
    c = component_factory("multi")
    attribution_factory(c["id"], plane="github", identifier="i1")
    attribution_factory(c["id"], plane="cloud", identifier="i2")
    attribution_factory(c["id"], plane="github", identifier="i3")  # dup plane
    r = client.get("/api/components")
    row = next(x for x in r.json()["components"] if x["canonical_name"] == "multi")
    assert sorted(row["planes"]) == ["cloud", "github"]
    # attribution_count counts every row, planes dedupes.
    assert row["attribution_count"] == 3


def test_invalid_type_400(client):
    r = client.get("/api/components?type=bogus")
    assert r.status_code == 400


def test_invalid_plane_400(client):
    r = client.get("/api/components?plane=bogus")
    assert r.status_code == 400


# ---------- drill-down endpoint ----------

def test_drilldown_404_for_unknown(client):
    r = client.get(f"/api/component/{_new_uuid()}/drilldown")
    assert r.status_code == 404


def test_drilldown_returns_full_shape(client, component_factory, attribution_factory):
    c = component_factory("dd-comp")
    attribution_factory(c["id"], plane="github", identifier="GET /x")
    r = client.get(f"/api/component/{c['id']}/drilldown")
    assert r.status_code == 200
    body = r.json()
    assert body["component"]["canonical_name"] == "dd-comp"
    assert sorted(body["component"]["planes"]) == ["github"]
    assert len(body["attributions"]) == 1
    assert body["edges"] == {
        "bound_in": [], "bound_out": [], "catalog": [], "dangling_out": [],
    }
    assert body["flows"] == []
    assert body["resources"] == []


def test_drilldown_edge_buckets(client, component_factory):
    """A component as both source + target of bound edges, with catalog
    + dangling on its own side. All four buckets populated."""
    c = component_factory("center")
    other = component_factory("other")
    # bound out: center → other
    execute_returning(
        """INSERT INTO edges (from_component_id, to_component_id, edge_type, identifier, discovered_by)
           VALUES (%s, %s, 'calls', 'GET /a', 'test') RETURNING id""",
        (c["id"], other["id"]),
    )
    # bound in: other → center
    execute_returning(
        """INSERT INTO edges (from_component_id, to_component_id, edge_type, identifier, discovered_by)
           VALUES (%s, %s, 'calls', 'GET /b', 'test') RETURNING id""",
        (other["id"], c["id"]),
    )
    # Phase 7.4.2: catalog on center lives in `catalogs` table.
    execute_returning(
        """INSERT INTO catalogs (component_id, kind, identifier, discovered_by)
           VALUES (%s::uuid, 'endpoint', 'GET /catalog', 'test') RETURNING id""",
        (c["id"],),
    )
    # dangling out: from=center, to=NULL
    execute_returning(
        """INSERT INTO edges (from_component_id, to_component_id, edge_type, identifier, discovered_by)
           VALUES (%s, NULL, 'calls', 'GET /dangling', 'test') RETURNING id""",
        (c["id"],),
    )
    r = client.get(f"/api/component/{c['id']}/drilldown")
    e = r.json()["edges"]
    assert len(e["bound_out"]) == 1
    assert len(e["bound_in"]) == 1
    assert len(e["catalog"]) == 1
    assert len(e["dangling_out"]) == 1

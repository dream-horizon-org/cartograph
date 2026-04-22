"""Phase 3.5: /api/graph endpoint — nodes + edges for the graph viz tab."""

from shared.db import execute_mutate, execute_returning


def _component(canonical, component_type="application", doc=None):
    return execute_returning(
        """INSERT INTO components (canonical_name, display_name, component_type,
                                   component_doc_md)
           VALUES (%s, %s, %s, %s)
           RETURNING *""",
        (canonical, canonical, component_type, doc),
    )


def test_graph_empty(client):
    r = client.get("/api/graph")
    assert r.status_code == 200
    data = r.json()
    assert data == {"nodes": [], "edges": []}


def test_graph_returns_components_and_edges(client):
    c1 = _component("a", doc="# A\nThe A service.")
    c2 = _component("b")
    execute_mutate(
        """INSERT INTO edges (source_id, target_id, edge_type, identifier, discovered_by)
           VALUES (%s, %s, 'calls', 'GET /x', 'sys')""",
        (c1["id"], c2["id"]),
    )
    r = client.get("/api/graph")
    data = r.json()
    assert len(data["nodes"]) == 2
    assert len(data["edges"]) == 1
    # component_doc_md flows through
    a = next(n for n in data["nodes"] if n["canonical_name"] == "a")
    assert a["component_doc_md"] == "# A\nThe A service."
    # planes array present even when empty
    assert a["planes"] == []


def test_graph_aggregates_planes_per_component(client):
    c = _component("a")
    for plane in ("github", "deploy", "cloud"):
        execute_mutate(
            """INSERT INTO attributions (component_id, plane, resource_type, identifier)
               VALUES (%s, %s, 'repo', %s)""",
            (c["id"], plane, f"a-{plane}"),
        )
    r = client.get("/api/graph")
    data = r.json()
    assert len(data["nodes"]) == 1
    planes = set(data["nodes"][0]["planes"])
    assert planes == {"github", "deploy", "cloud"}


def test_graph_excludes_decommissioned(client):
    c_active = _component("live")
    c_dead = _component("dead")
    execute_mutate(
        "UPDATE components SET status = 'decommissioned' WHERE id = %s",
        (c_dead["id"],),
    )
    # Edge between them must also be excluded (JOIN filters on status).
    execute_mutate(
        """INSERT INTO edges (source_id, target_id, edge_type, identifier, discovered_by)
           VALUES (%s, %s, 'calls', 'x', 'sys')""",
        (c_active["id"], c_dead["id"]),
    )
    r = client.get("/api/graph")
    data = r.json()
    assert [n["canonical_name"] for n in data["nodes"]] == ["live"]
    assert data["edges"] == []

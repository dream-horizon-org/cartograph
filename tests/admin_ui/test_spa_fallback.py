"""Phase 5.1: SPA fallback for client-side routing.

The admin UI is now a single-page app with History API routing. Any
non-`/api/*` path that doesn't map to a real static file must serve
index.html so the FE router can take over.
"""


def test_root_serves_index_html(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "<title>Cartograph Admin</title>" in r.text


def test_unknown_deep_link_serves_index_html(client):
    """A URL like /chat/sme-foo is a client-side route, not a real file
    on disk. Must serve index.html so the FE router renders it."""
    r = client.get("/chat/sme-abc")
    assert r.status_code == 200
    assert "<title>Cartograph Admin</title>" in r.text


def test_entities_drilldown_url_serves_index_html(client):
    r = client.get("/entities/task/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 200
    assert "<title>Cartograph Admin</title>" in r.text


def test_catalog_drilldown_url_serves_index_html(client):
    r = client.get("/catalog/component/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 200
    assert "<title>Cartograph Admin</title>" in r.text


def test_real_static_file_served(client):
    """app.js is a real file under static/ — must be served as-is, not
    swallowed by the SPA fallback."""
    r = client.get("/app.js")
    assert r.status_code == 200
    # Verify it's actually JS, not HTML.
    assert "Router" in r.text  # Phase 5.1 router module marker.
    assert "<html" not in r.text


def test_api_routes_unaffected(client):
    """/api/* routes must still be matched by their real handlers, not
    swallowed by the catch-all."""
    r = client.get("/api/agents")
    assert r.status_code == 200
    assert "agents" in r.json()


def test_api_404_does_not_fallback_to_index(client):
    """A genuinely-unknown /api/ path should 404, not return index.html.
    (The catch-all must NOT match /api/...)"""
    r = client.get("/api/this-endpoint-does-not-exist")
    assert r.status_code == 404
    # Sanity-check we got a JSON error, not the SPA shell.
    assert "<html" not in r.text


def test_path_traversal_rejected(client):
    """A traversal attempt (../) must not escape STATIC_DIR — falls back
    to serving index.html rather than reading parent dirs."""
    r = client.get("/../README.md")
    assert r.status_code == 200
    # If traversal succeeded we'd get README contents; instead we get the
    # SPA shell.
    assert "<title>Cartograph Admin</title>" in r.text

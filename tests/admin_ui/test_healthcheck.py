"""GET /healthcheck — load-balancer / Odin probe endpoint."""


def test_healthcheck_ok(client):
    r = client.get("/healthcheck")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["service"] == "cartograph-admin"
    assert body["db"] is True


def test_healthcheck_not_spa_fallback(client):
    r = client.get("/healthcheck")
    assert "<title>Cartograph Admin</title>" not in r.text

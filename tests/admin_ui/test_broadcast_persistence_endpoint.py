"""Phase 5.7: POST /api/broadcast/:id/persistence."""

import pytest

from shared.db import execute_returning


def _broadcast_row(persistent: bool = False):
    return execute_returning(
        """INSERT INTO communications
              (from_agent, to_agent_type, type, text, is_persistent)
           VALUES ('admin', 'sme', 'broadcast', 'fix', %s) RETURNING *""",
        (persistent,),
    )


def test_endpoint_toggles_on(client):
    row = _broadcast_row(persistent=False)
    r = client.post(
        f"/api/broadcast/{row['id']}/persistence",
        json={"persistent": True},
    )
    assert r.status_code == 200
    assert r.json()["is_persistent"] is True


def test_endpoint_toggles_off(client):
    row = _broadcast_row(persistent=True)
    r = client.post(
        f"/api/broadcast/{row['id']}/persistence",
        json={"persistent": False},
    )
    assert r.status_code == 200
    assert r.json()["is_persistent"] is False


def test_endpoint_400_on_non_broadcast(client):
    chat = execute_returning(
        """INSERT INTO communications (from_agent, to_agent, type, text)
           VALUES ('admin', 'sme-x', 'chat', 'hi') RETURNING *""",
    )
    r = client.post(
        f"/api/broadcast/{chat['id']}/persistence",
        json={"persistent": True},
    )
    assert r.status_code == 400


def test_endpoint_400_on_unknown_id(client):
    r = client.post(
        "/api/broadcast/00000000-0000-0000-0000-000000000000/persistence",
        json={"persistent": True},
    )
    assert r.status_code == 400

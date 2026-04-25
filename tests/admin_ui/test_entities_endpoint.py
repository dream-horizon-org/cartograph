"""Phase 5.2: /api/entities + /api/entity/{kind}/{id} drill-down."""

import uuid

import pytest

from shared.db import execute_returning, execute_mutate


# ----- helpers ---------------------------------------------------------

def _new_uuid() -> str:
    return str(uuid.uuid4())


def _ensure_agent(agent_id: str, agent_type: str = "sme"):
    """Idempotent agent insert — tests can call multiple times safely."""
    execute_mutate(
        """INSERT INTO agent_runs (agent_id, agent_type, status)
           VALUES (%s, %s, 'idle')
           ON CONFLICT (agent_id) DO NOTHING""",
        (agent_id, agent_type),
    )


@pytest.fixture
def task_factory():
    _counter = {"n": 0}
    def _make(owner: str = "admin", worker: str | None = None, desc: str = "do thing",
              status: str = "BW"):
        if worker is None:
            _counter["n"] += 1
            worker = f"sme-tf-{_counter['n']}"
        _ensure_agent(worker, "sme")
        if owner != "admin":
            _ensure_agent(owner, "orchestrator")
        row = execute_returning(
            """INSERT INTO tasks (owner_agent_id, worker_agent_id, description, status)
               VALUES (%s, %s, %s, %s) RETURNING *""",
            (owner, worker, desc, status),
        )
        return row
    return _make


@pytest.fixture
def consolidation_factory():
    _counter = {"n": 0}
    def _make(a_id: str | None = None, b_id: str | None = None,
              status: str = "B2", nomination_type: str = "merge"):
        if a_id is None:
            _counter["n"] += 1
            a_id = f"sme-cf-a-{_counter['n']}"
        if b_id is None and nomination_type == "merge":
            b_id = f"sme-cf-b-{_counter['n']}"
        _ensure_agent(a_id, "sme")
        if b_id is not None:
            _ensure_agent(b_id, "sme")
        # need component rows + RCA — use raw inserts
        ca_id = execute_returning(
            """INSERT INTO components (canonical_name, display_name, component_type)
               VALUES (%s, %s, 'application') RETURNING id""",
            (f"{a_id}-comp", f"{a_id}-comp"),
        )["id"]
        cb_id = None
        if b_id is not None:
            cb_id = execute_returning(
                """INSERT INTO components (canonical_name, display_name, component_type)
                   VALUES (%s, %s, 'application') RETURNING id""",
                (f"{b_id}-comp", f"{b_id}-comp"),
            )["id"]
        row = execute_returning(
            """INSERT INTO consolidations (proposed_by, agent_a_id, agent_b_id,
                                            component_a_id, component_b_id,
                                            nomination_type, status, a_conf_score)
               VALUES (%s, %s, %s, %s, %s, %s, %s, 0.85)
               RETURNING *""",
            (a_id, a_id, b_id, ca_id, cb_id, nomination_type, status),
        )
        return row
    return _make


@pytest.fixture
def clarification_factory():
    _counter = {"n": 0}
    def _make(asker: str | None = None, responder: str = "admin", status: str = "B2"):
        if asker is None:
            _counter["n"] += 1
            asker = f"sme-clf-{_counter['n']}"
        _ensure_agent(asker, "sme")
        row = execute_returning(
            """INSERT INTO clarifications (asker_agent_id, responder_agent_id, status)
               VALUES (%s, %s, %s) RETURNING *""",
            (asker, responder, status),
        )
        return row
    return _make


@pytest.fixture
def broadcast_factory():
    def _make(text: str = "broadcast text", to_type: str = "sme",
              persistent: bool = False):
        row = execute_returning(
            """INSERT INTO communications (from_agent, to_agent_type, type, text, is_persistent)
               VALUES ('admin', %s, 'broadcast', %s, %s)
               RETURNING *""",
            (to_type, text, persistent),
        )
        return row
    return _make


# ----- tests -----------------------------------------------------------

def test_empty_returns_empty_list(client):
    r = client.get("/api/entities")
    assert r.status_code == 200
    assert r.json() == {"entities": [], "has_more": False}


def test_kind_filter_isolates(client, task_factory, broadcast_factory):
    task_factory()
    broadcast_factory()
    r = client.get("/api/entities?type=task")
    body = r.json()
    assert body["entities"][0]["type"] == "task"
    assert all(e["type"] == "task" for e in body["entities"])


def test_status_filter(client, task_factory):
    task_factory(status="BW")
    task_factory(status="TC")
    r = client.get("/api/entities?type=task&status=BW")
    body = r.json()
    assert all(e["status"] == "BW" for e in body["entities"])
    assert len(body["entities"]) == 1


def test_open_only_excludes_terminal(client, task_factory, consolidation_factory):
    task_factory(status="BW")     # open
    task_factory(status="TC")     # terminal
    consolidation_factory(status="B2")  # open
    consolidation_factory(status="D")   # terminal
    r = client.get("/api/entities?open_only=true")
    body = r.json()
    statuses = {(e["type"], e["status"]) for e in body["entities"]}
    assert ("task", "TC") not in statuses
    assert ("consolidation", "D") not in statuses
    assert ("task", "BW") in statuses
    assert ("consolidation", "B2") in statuses


def test_participant_filter_either_side(client, task_factory):
    task_factory(owner="orch", worker="sme-x")
    task_factory(owner="orch", worker="sme-y")
    r = client.get("/api/entities?participant=sme-x")
    body = r.json()
    assert len(body["entities"]) == 1
    assert body["entities"][0]["participant_b"] == "sme-x"


def test_pagination_cursor(client, task_factory):
    """Insert 5 tasks, paginate with limit=2."""
    for i in range(5):
        task_factory(worker=f"sme-pg-{i}", desc=f"task-{i}")
    r1 = client.get("/api/entities", params={"type": "task", "limit": 2})
    body1 = r1.json()
    assert len(body1["entities"]) == 2
    assert body1["has_more"]
    cursor = body1["entities"][-1]["last_activity"]
    # Use params= so the TestClient URL-encodes the timestamp's "+" sign
    # correctly (otherwise it gets decoded to a space server-side).
    r2 = client.get(
        "/api/entities",
        params={"type": "task", "limit": 2, "before": cursor},
    )
    body2 = r2.json()
    assert len(body2["entities"]) <= 2
    # No row from page 1 reappears.
    page1_ids = {e["id"] for e in body1["entities"]}
    page2_ids = {e["id"] for e in body2["entities"]}
    assert not (page1_ids & page2_ids)


def test_drilldown_task(client, task_factory):
    t = task_factory()
    r = client.get(f"/api/entity/task/{t['id']}")
    assert r.status_code == 200
    body = r.json()
    assert body["type"] == "task"
    assert body["entity"]["id"] == str(t["id"])
    assert "thread" in body


def test_drilldown_consolidation(client, consolidation_factory):
    c = consolidation_factory()
    r = client.get(f"/api/entity/consolidation/{c['id']}")
    assert r.status_code == 200
    assert r.json()["type"] == "consolidation"


def test_drilldown_clarification(client, clarification_factory):
    cl = clarification_factory()
    r = client.get(f"/api/entity/clarification/{cl['id']}")
    assert r.status_code == 200
    assert r.json()["type"] == "clarification"


def test_drilldown_broadcast_includes_acks(client, broadcast_factory, agent_factory):
    b = broadcast_factory()
    agent_factory("sme-acker", "sme")
    execute_mutate(
        "INSERT INTO broadcast_acks (communication_id, agent_id) VALUES (%s, %s)",
        (b["id"], "sme-acker"),
    )
    r = client.get(f"/api/entity/broadcast/{b['id']}")
    assert r.status_code == 200
    body = r.json()
    assert body["type"] == "broadcast"
    assert body["extras"]["acks"][0]["agent_id"] == "sme-acker"


def test_drilldown_404(client):
    r = client.get(f"/api/entity/task/{_new_uuid()}")
    assert r.status_code == 404


def test_invalid_kind_400(client):
    r = client.get("/api/entities?type=bogus")
    assert r.status_code == 400
    r2 = client.get(f"/api/entity/bogus/{_new_uuid()}")
    assert r2.status_code == 400


def test_q_search_summary(client, broadcast_factory):
    broadcast_factory(text="merge freeze starts Thursday")
    broadcast_factory(text="all good carry on")
    r = client.get("/api/entities?q=merge")
    body = r.json()
    assert len(body["entities"]) == 1
    assert "merge" in body["entities"][0]["summary"]

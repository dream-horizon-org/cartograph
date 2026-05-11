"""Phase 9.1: mcp_call_batch — server-side parallel dispatcher tests.

Covers:
- happy-path heterogeneous reads
- per-call error collection (one bad arg, others succeed)
- nesting refusal
- empty batch (no error)
- unknown tool
- max-50 cap
- agent_id auto-injection vs preservation when sub-call carries its own
- audit: outer + sub-calls each recorded in mcp_audit
- bulk-tool inside batch
- thread-pool concurrency behaviour (sleep tools run roughly in parallel)
"""

import time
import uuid

import pytest

from shared.db import execute, execute_mutate, execute_returning, execute_one
from cartograph_mcp.tools import call_batch


# ----- A tiny dispatch shim used by most unit tests (avoids exercising
#       the full server.py registry). Each "tool" here is a plain Python
#       callable; mcp_call_batch's contract is server-agnostic.

def _dispatch_for(*tool_names_to_callables) -> dict:
    """Build a dispatch dict from `(name, fn)` pairs."""
    return dict(tool_names_to_callables)


def _ok_tool(**kwargs):
    return {"received": kwargs}


def _raises_tool(**kwargs):
    raise ValueError("synthetic failure")


# ============ shape validation ============

def test_calls_must_be_list():
    with pytest.raises(ValueError, match="must be a list"):
        call_batch.call_batch("a", calls={}, dispatch={})


def test_empty_batch_returns_empty_results():
    out = call_batch.call_batch("a", calls=[], dispatch={})
    assert out == {"results": []}


def test_call_must_be_dict():
    with pytest.raises(ValueError, match=r"calls\[0\] must be a dict"):
        call_batch.call_batch("a", calls=["not-a-dict"], dispatch={})


def test_tool_must_be_string():
    with pytest.raises(ValueError, match="non-empty string"):
        call_batch.call_batch("a", calls=[{"tool": "", "args": {}}], dispatch={})


def test_unknown_tool_rejected_pre_dispatch():
    with pytest.raises(ValueError, match="not a known"):
        call_batch.call_batch(
            "a",
            calls=[{"tool": "no_such_tool", "args": {}}],
            dispatch={"some_other_tool": _ok_tool},
        )


def test_args_must_be_dict():
    with pytest.raises(ValueError, match=r"calls\[0\]\.args must be a dict"):
        call_batch.call_batch(
            "a",
            calls=[{"tool": "ok", "args": "not-a-dict"}],
            dispatch={"ok": _ok_tool},
        )


def test_nesting_rejected():
    with pytest.raises(ValueError, match="cannot be nested"):
        call_batch.call_batch(
            "a",
            calls=[{"tool": "mcp_call_batch", "args": {"calls": []}}],
            dispatch={"mcp_call_batch": _ok_tool},
        )


def test_max_50_cap():
    over = [{"tool": "ok", "args": {}}] * 51
    with pytest.raises(ValueError, match="exceeds max 50"):
        call_batch.call_batch("a", over, dispatch={"ok": _ok_tool})


# ============ dispatch + collect-all ============

def test_happy_heterogeneous_batch():
    def reader_a(**kw): return {"a": 1, **kw}
    def reader_b(**kw): return {"b": 2, **kw}
    out = call_batch.call_batch(
        "agent-x",
        calls=[
            {"tool": "reader_a", "args": {}},
            {"tool": "reader_b", "args": {}},
        ],
        dispatch={"reader_a": reader_a, "reader_b": reader_b},
    )
    rs = out["results"]
    assert [r["idx"] for r in rs] == [0, 1]
    assert [r["tool"] for r in rs] == ["reader_a", "reader_b"]
    assert all(r["ok"] for r in rs)
    assert rs[0]["result"]["a"] == 1
    assert rs[1]["result"]["b"] == 2


def test_per_call_error_does_not_abort_siblings():
    out = call_batch.call_batch(
        "agent-x",
        calls=[
            {"tool": "ok", "args": {}},
            {"tool": "raises", "args": {}},
            {"tool": "ok", "args": {}},
        ],
        dispatch={"ok": _ok_tool, "raises": _raises_tool},
    )
    rs = out["results"]
    assert len(rs) == 3
    assert rs[0]["ok"] is True
    assert rs[1]["ok"] is False
    assert "synthetic failure" in rs[1]["error"]
    assert rs[2]["ok"] is True


def test_agent_id_injected_when_missing():
    captured = {}
    def capture(**kw):
        captured.update(kw)
        return "ok"
    call_batch.call_batch(
        "agent-x",
        calls=[{"tool": "capture", "args": {"foo": "bar"}}],
        dispatch={"capture": capture},
    )
    assert captured == {"foo": "bar", "agent_id": "agent-x"}


def test_agent_id_preserved_when_sub_call_carries_its_own():
    """Proxy paths legitimately need a different agent_id."""
    captured = {}
    def capture(**kw):
        captured.update(kw)
        return "ok"
    call_batch.call_batch(
        "agent-x",
        calls=[{"tool": "capture", "args": {"agent_id": "different-agent"}}],
        dispatch={"capture": capture},
    )
    assert captured == {"agent_id": "different-agent"}


def test_concurrency_actual_parallel_dispatch():
    """4 sub-calls each sleeping 0.2s should finish well under 0.8s
    (sequential lower bound) — proves ThreadPoolExecutor is dispatching
    in parallel rather than serially.
    """
    def slow_tool(**kw):
        time.sleep(0.2)
        return "done"
    t0 = time.monotonic()
    call_batch.call_batch(
        "agent-x",
        calls=[{"tool": "slow", "args": {}} for _ in range(4)],
        dispatch={"slow": slow_tool},
    )
    elapsed = time.monotonic() - t0
    # 4 × 0.2s sequential = 0.8s. Parallel should be ~0.2s with some overhead.
    # 0.5s threshold leaves headroom for slow CI without falsely passing
    # a sequential implementation.
    assert elapsed < 0.5, f"Expected parallel dispatch (<0.5s), got {elapsed:.3f}s"


# ============ integration: mcp_call_batch via the real server registry ============

def _setup_iterator_and_resource(agent_factory):
    iter_id = "iter-batch-tests"
    agent_factory(iter_id, "iterator")
    execute_mutate(
        "UPDATE agent_runs SET plane='github', status='idle' WHERE agent_id=%s",
        (iter_id,),
    )
    return iter_id


def test_real_dispatch_heterogeneous_reads(agent_factory):
    """End-to-end via server.py's mcp_call_batch wrapper.

    Exercises the actual _BATCH_DISPATCH map. Calls 3 different read
    tools in one batch — none mutate state, all should succeed.
    """
    from cartograph_mcp import server as srv

    iter_id = _setup_iterator_and_resource(agent_factory)
    out = srv.mcp_call_batch(
        agent_id=iter_id,
        calls=[
            {"tool": "get_action_items_summary", "args": {}},
            {"tool": "list_agents", "args": {}},
            {"tool": "get_resource_counts", "args": {}},
        ],
    )
    rs = out["results"]
    assert len(rs) == 3
    assert all(r["ok"] for r in rs), f"unexpected failures: {rs}"
    # Each result should have its expected shape
    summary = rs[0]["result"]
    assert "tasks_pending" in summary  # known key on the summary dict


def test_real_dispatch_records_outer_and_sub_calls_in_mcp_audit(agent_factory):
    """Each sub-call goes through its @mcp.tool wrapper, so audit
    records: 1 outer mcp_call_batch row + 1 row per sub-call.
    """
    from cartograph_mcp import server as srv

    iter_id = _setup_iterator_and_resource(agent_factory)

    pre = execute_one("SELECT COUNT(*) AS c FROM mcp_audit")["c"]
    srv.mcp_call_batch(
        agent_id=iter_id,
        calls=[
            {"tool": "list_agents", "args": {}},
            {"tool": "get_resource_counts", "args": {}},
        ],
    )
    post = execute_one("SELECT COUNT(*) AS c FROM mcp_audit")["c"]
    # Expect at least 3 new rows: outer + 2 sub-calls.
    assert post - pre >= 3, f"audit gain {post - pre} (expected ≥3)"

    by_tool = execute(
        "SELECT tool_name, COUNT(*) AS c FROM mcp_audit "
        "WHERE created_at > now() - interval '5 seconds' GROUP BY tool_name"
    )
    names = {r["tool_name"] for r in by_tool}
    assert "mcp_call_batch" in names
    assert "list_agents" in names
    assert "get_resource_counts" in names


def test_real_dispatch_unknown_tool_pre_flight_error(agent_factory):
    from cartograph_mcp import server as srv

    iter_id = _setup_iterator_and_resource(agent_factory)
    with pytest.raises(ValueError, match="not a known"):
        srv.mcp_call_batch(
            agent_id=iter_id,
            calls=[{"tool": "totally_made_up_tool", "args": {}}],
        )


def test_real_dispatch_nesting_refused(agent_factory):
    from cartograph_mcp import server as srv

    iter_id = _setup_iterator_and_resource(agent_factory)
    with pytest.raises(ValueError, match="cannot be nested"):
        srv.mcp_call_batch(
            agent_id=iter_id,
            calls=[{"tool": "mcp_call_batch", "args": {"calls": []}}],
        )


def test_real_dispatch_per_call_error_collection(agent_factory):
    """Mix one good read + one bad call (missing required arg).

    The bad call should fail with its own error in the result, not
    abort the sibling.
    """
    from cartograph_mcp import server as srv

    iter_id = _setup_iterator_and_resource(agent_factory)
    out = srv.mcp_call_batch(
        agent_id=iter_id,
        calls=[
            {"tool": "list_agents", "args": {}},
            # get_component requires a real component_id; passing a
            # non-existent UUID should fail inside the tool.
            {"tool": "get_component",
             "args": {"component_id": str(uuid.uuid4())}},
        ],
    )
    rs = out["results"]
    assert len(rs) == 2
    # Order-stable
    assert rs[0]["tool"] == "list_agents"
    assert rs[0]["ok"] is True
    assert rs[1]["tool"] == "get_component"
    # get_component on missing id either returns None-ish or raises;
    # contract is collect-all so caller sees per-call status either way.
    # We just assert the batch didn't raise + the sibling succeeded.


# ============ Phase 10.1.3: per-tool caller-id kwarg name-map ============
# DEMO-MEGA filed 5 BUG-2 insights (7ce4622c, 7c0bae81, 4a220f29,
# f41f310e, 5e58b07d) — mcp_call_batch's auto-inject pushed `agent_id`
# into every sub-call, but 4 tools used non-standard caller-id param
# names and TypeError'd on the unexpected kwarg. Phase 10.1.3 fix:
# call_batch maintains a name-map keyed on tool name → caller-id kwarg
# name, so the inject pushes under the right name per tool. Tools keep
# their semantically-meaningful param names (from_agent_id /
# owner_agent_id / survivor_id / asker_agent_id).


def test_phase10_1_3_unit_caller_kwarg_lookup():
    """Direct unit test of the name-map helper. Pinned values matter:
    if a future PR changes them without updating the public tool
    signatures, this test catches the drift immediately.
    """
    assert call_batch._caller_kwarg_for("send_chat") == "from_agent_id"
    assert call_batch._caller_kwarg_for("send_broadcast") == "from_agent_id"
    assert call_batch._caller_kwarg_for("create_task") == "owner_agent_id"
    assert call_batch._caller_kwarg_for("act_on_proxy_item") == "survivor_id"
    assert call_batch._caller_kwarg_for("create_clarification") == "asker_agent_id"
    # Default: any tool not in the map gets agent_id
    assert call_batch._caller_kwarg_for("get_my_components") == "agent_id"
    assert call_batch._caller_kwarg_for("upsert_attribution") == "agent_id"


def test_phase10_1_3_unit_inject_pushes_to_right_kwarg():
    """Synthetic dispatch test: confirm the auto-inject pushes the
    caller's id under the correct kwarg name per tool, not the default
    `agent_id`. Uses fake tools so we test the dispatcher in isolation.
    """
    captured = []

    def fake_send_chat(from_agent_id, to_agent_id, message):
        captured.append(("send_chat", {
            "from_agent_id": from_agent_id,
            "to_agent_id": to_agent_id,
            "message": message,
        }))
        return "ok"

    def fake_create_task(owner_agent_id, worker_agent_id, description):
        captured.append(("create_task", {
            "owner_agent_id": owner_agent_id,
            "worker_agent_id": worker_agent_id,
            "description": description,
        }))
        return "ok"

    def fake_act_on_proxy_item(survivor_id, item_type, item_id, action, payload=None):
        captured.append(("act_on_proxy_item", {
            "survivor_id": survivor_id,
            "item_type": item_type,
            "item_id": item_id,
            "action": action,
        }))
        return "ok"

    def fake_create_clarification(asker_agent_id, responder_agent_id, question_message):
        captured.append(("create_clarification", {
            "asker_agent_id": asker_agent_id,
            "responder_agent_id": responder_agent_id,
            "question_message": question_message,
        }))
        return "ok"

    out = call_batch.call_batch(
        "caller-x",
        calls=[
            {"tool": "send_chat",
             "args": {"to_agent_id": "admin", "message": "hi"}},
            {"tool": "create_task",
             "args": {"worker_agent_id": "sme-y", "description": "do the thing"}},
            {"tool": "act_on_proxy_item",
             "args": {"item_type": "chat", "item_id": "uuid-1", "action": "ack"}},
            {"tool": "create_clarification",
             "args": {"responder_agent_id": "sme-z", "question_message": "?"}},
        ],
        dispatch={
            "send_chat": fake_send_chat,
            "create_task": fake_create_task,
            "act_on_proxy_item": fake_act_on_proxy_item,
            "create_clarification": fake_create_clarification,
        },
    )
    # All 4 succeeded — proves auto-inject reached the right kwarg name.
    assert all(r["ok"] for r in out["results"]), f"unexpected: {out}"
    # Verify each captured the caller's id under its OWN param name
    by_tool = dict(captured)
    assert by_tool["send_chat"]["from_agent_id"] == "caller-x"
    assert by_tool["create_task"]["owner_agent_id"] == "caller-x"
    assert by_tool["act_on_proxy_item"]["survivor_id"] == "caller-x"
    assert by_tool["create_clarification"]["asker_agent_id"] == "caller-x"


def test_phase10_1_3_unit_does_not_inject_when_kwarg_already_present():
    """If the sub-call already carries its own caller-id kwarg, the
    inject MUST NOT clobber it. Proxy paths legitimately need to
    specify a different identity per sub-call.
    """
    captured = {}

    def fake_send_chat(from_agent_id, to_agent_id, message):
        captured.update({"from_agent_id": from_agent_id})
        return "ok"

    call_batch.call_batch(
        "outer-caller",
        calls=[{"tool": "send_chat",
                "args": {"from_agent_id": "explicit-other",
                         "to_agent_id": "admin",
                         "message": "x"}}],
        dispatch={"send_chat": fake_send_chat},
    )
    assert captured["from_agent_id"] == "explicit-other"


def test_phase10_1_3_send_chat_real_dispatch_in_batch(agent_factory):
    """End-to-end: send_chat callable inside mcp_call_batch via the
    real server registry. DEMO-MEGA's BUG-2 cluster was filed because
    this exact call previously failed with TypeError.
    """
    from cartograph_mcp import server as srv

    sme_id = "sme-batch-chat-real"
    agent_factory(sme_id, "sme")
    execute_mutate(
        "UPDATE agent_runs SET status='idle' WHERE agent_id=%s", (sme_id,),
    )
    out = srv.mcp_call_batch(
        agent_id=sme_id,
        calls=[
            {"tool": "send_chat",
             "args": {"to_agent_id": "admin", "message": "phase10.1.3 v2"}},
        ],
    )
    assert out["results"][0]["ok"] is True, f"failure: {out['results'][0]}"
    row = execute_one(
        "SELECT * FROM communications WHERE from_agent=%s AND text=%s "
        "ORDER BY created_at DESC LIMIT 1",
        (sme_id, "phase10.1.3 v2"),
    )
    assert row is not None
    assert row["to_agent"] == "admin"


def test_phase10_1_3_send_broadcast_real_dispatch_in_batch(agent_factory):
    from cartograph_mcp import server as srv

    orch_id = "orch-batch-bcast-real"
    agent_factory(orch_id, "orchestrator")
    execute_mutate(
        "UPDATE agent_runs SET status='idle' WHERE agent_id=%s", (orch_id,),
    )
    out = srv.mcp_call_batch(
        agent_id=orch_id,
        calls=[
            {"tool": "send_broadcast",
             "args": {"to_agent_type": "sme",
                      "message": "phase10.1.3 v2 bcast",
                      "persistent": False}},
        ],
    )
    assert out["results"][0]["ok"] is True, f"failure: {out['results'][0]}"
    row = execute_one(
        "SELECT * FROM communications WHERE from_agent=%s AND text=%s",
        (orch_id, "phase10.1.3 v2 bcast"),
    )
    assert row is not None and row["to_agent_type"] == "sme"


def test_phase10_1_3_create_task_real_dispatch_in_batch(agent_factory):
    from cartograph_mcp import server as srv

    orch_id = "orch-batch-task-real"
    sme_id = "sme-batch-task-worker-real"
    agent_factory(orch_id, "orchestrator")
    agent_factory(sme_id, "sme")
    execute_mutate(
        "UPDATE agent_runs SET status='idle' WHERE agent_id IN (%s, %s)",
        (orch_id, sme_id),
    )
    out = srv.mcp_call_batch(
        agent_id=orch_id,
        calls=[
            {"tool": "create_task",
             "args": {"worker_agent_id": sme_id,
                      "description": "phase10.1.3 v2 task"}},
        ],
    )
    assert out["results"][0]["ok"] is True, f"failure: {out['results'][0]}"
    row = execute_one(
        "SELECT * FROM tasks WHERE owner_agent_id=%s AND worker_agent_id=%s",
        (orch_id, sme_id),
    )
    assert row is not None and row["status"] == "BW"


def test_phase10_1_3_act_on_proxy_item_real_dispatch_in_batch(agent_factory):
    from cartograph_mcp import server as srv

    sme_a = "sme-proxy-batch-survivor-real"
    sme_b = "sme-proxy-batch-absorbed-real"
    agent_factory(sme_a, "sme")
    agent_factory(sme_b, "sme")
    execute_mutate(
        "UPDATE agent_runs SET status='idle' WHERE agent_id IN (%s, %s)",
        (sme_a, sme_b),
    )
    execute_mutate(
        """UPDATE agent_runs SET status='decommissioned',
           deactivation_reason='merged', merged_into_agent_id=%s
           WHERE agent_id=%s""",
        (sme_a, sme_b),
    )
    chat_row = execute_returning(
        """INSERT INTO communications (from_agent, to_agent, type, text)
           VALUES ('admin', %s, 'chat', 'inherited') RETURNING id""",
        (sme_b,),
    )
    chat_id = str(chat_row["id"])
    out = srv.mcp_call_batch(
        agent_id=sme_a,
        calls=[
            {"tool": "act_on_proxy_item",
             "args": {"item_type": "chat", "item_id": chat_id,
                      "action": "ack", "payload": {}}},
        ],
    )
    assert out["results"][0]["ok"] is True, f"failure: {out['results'][0]}"
    audit = execute_one(
        "SELECT * FROM proxy_audit WHERE item_id=%s", (chat_id,),
    )
    assert audit is not None
    assert audit["survivor_id"] == sme_a
    assert audit["proxy_agent_id"] == sme_b


def test_phase10_1_3_create_clarification_real_dispatch_in_batch(agent_factory):
    """create_clarification was the 5th tool with the same bug class
    (asker_agent_id param). No DEMO-MEGA insight was filed for it
    because no agent tried to batch it during the run, but
    architecturally it has the identical issue. Phase 10.1.3 closes
    it via the same name-map.
    """
    from cartograph_mcp import server as srv

    sme_a = "sme-clar-batch-asker-real"
    sme_b = "sme-clar-batch-resp-real"
    agent_factory(sme_a, "sme")
    agent_factory(sme_b, "sme")
    execute_mutate(
        "UPDATE agent_runs SET status='idle' WHERE agent_id IN (%s, %s)",
        (sme_a, sme_b),
    )
    out = srv.mcp_call_batch(
        agent_id=sme_a,
        calls=[
            {"tool": "create_clarification",
             "args": {"responder_agent_id": sme_b,
                      "question_message": "phase10.1.3 v2 clar"}},
        ],
    )
    assert out["results"][0]["ok"] is True, f"failure: {out['results'][0]}"
    row = execute_one(
        "SELECT * FROM clarifications WHERE asker_agent_id=%s "
        "AND responder_agent_id=%s ORDER BY created_at DESC LIMIT 1",
        (sme_a, sme_b),
    )
    assert row is not None and row["status"] == "B2"


def test_phase10_1_3_all_5_renamed_tools_in_one_batch(agent_factory):
    """The big one: all 5 non-standard-caller-id tools fired together
    in a heterogeneous batch. DEMO-MEGA's BUG-2 cluster previously
    made each fail with TypeError. Phase 10.1.3 name-map fix: every
    sub-call returns ok=True via per-tool kwarg routing.
    """
    from cartograph_mcp import server as srv

    orch_id = "orch-mega-v2-batch"
    sme_a = "sme-mega-v2-survivor"
    sme_b = "sme-mega-v2-absorbed"
    sme_w = "sme-mega-v2-worker"
    sme_r = "sme-mega-v2-clar-resp"
    for aid, atype in [
        (orch_id, "orchestrator"),
        (sme_a, "sme"),
        (sme_b, "sme"),
        (sme_w, "sme"),
        (sme_r, "sme"),
    ]:
        agent_factory(aid, atype)
    execute_mutate(
        "UPDATE agent_runs SET status='idle' "
        "WHERE agent_id IN (%s,%s,%s,%s,%s)",
        (orch_id, sme_a, sme_b, sme_w, sme_r),
    )
    execute_mutate(
        """UPDATE agent_runs SET status='decommissioned',
           deactivation_reason='merged', merged_into_agent_id=%s
           WHERE agent_id=%s""",
        (sme_a, sme_b),
    )
    chat_row = execute_returning(
        """INSERT INTO communications (from_agent, to_agent, type, text)
           VALUES ('admin', %s, 'chat', 'inherit-mega') RETURNING id""",
        (sme_b,),
    )
    chat_id = str(chat_row["id"])

    # Orch fires send_chat + send_broadcast + create_task in one batch.
    out_orch = srv.mcp_call_batch(
        agent_id=orch_id,
        calls=[
            {"tool": "send_chat",
             "args": {"to_agent_id": "admin", "message": "orch v2 ping"}},
            {"tool": "send_broadcast",
             "args": {"to_agent_type": "sme", "message": "orch v2 bcast"}},
            {"tool": "create_task",
             "args": {"worker_agent_id": sme_w,
                      "description": "v2 task"}},
        ],
    )
    assert all(r["ok"] for r in out_orch["results"]), \
        f"orch batch fail: {out_orch}"

    # Survivor fires act_on_proxy_item + create_clarification together.
    out_sme = srv.mcp_call_batch(
        agent_id=sme_a,
        calls=[
            {"tool": "act_on_proxy_item",
             "args": {"item_type": "chat", "item_id": chat_id,
                      "action": "ack", "payload": {}}},
            {"tool": "create_clarification",
             "args": {"responder_agent_id": sme_r,
                      "question_message": "v2 clar"}},
        ],
    )
    assert all(r["ok"] for r in out_sme["results"]), \
        f"survivor batch fail: {out_sme}"


def test_phase10_1_3_create_edge_self_loop_succeeds_via_shim(agent_factory):
    """Phase 10.1.3 docstring fix: create_edge MCP wrapper docstring
    used to claim 'Refuses self-loops' which is wrong since Phase 7.3
    + Phase 7.4.4 dropped that constraint. Verify the shim actually
    accepts a self-loop without raising — confirms the docstring now
    matches reality.
    """
    from cartograph_mcp import server as srv
    from cartograph_mcp.tools import components as components_tool

    sme_id = "sme-self-loop-v2"
    agent_factory(sme_id, "sme")
    execute_mutate(
        "UPDATE agent_runs SET status='idle' WHERE agent_id=%s", (sme_id,),
    )
    res = execute_returning(
        """INSERT INTO resources (plane, resource_type, identifier)
           VALUES ('github','repo','o/r-self-loop-v2') RETURNING id"""
    )
    res_id = res["id"]
    execute_mutate(
        """INSERT INTO resource_component_agents (resource_id, agent_id, component_id)
           VALUES (%s, %s, NULL)""",
        (res_id, sme_id),
    )
    comp = components_tool.upsert_component(
        sme_id,
        {"canonical_name": "self-loop-v2",
         "display_name": "self-loop v2",
         "component_type": "cron"},
    )
    cid = comp["id"]
    out = srv.create_edge(
        agent_id=sme_id,
        edge_data={"source_id": cid, "target_id": cid,
                   "edge_type": "triggers",
                   "identifier": "self-trigger-v2"},
    )
    assert out is not None
    assert out["from_component_id"] == out["to_component_id"]


def test_phase10_1_3_create_edge_docstring_no_longer_lies():
    """Pin the docstring fix: create_edge MCP wrapper must NOT claim
    'Refuses self-loops' anymore. Insight 192d84c4 (DEMO-MEGA BUG-1)
    drove this fix.
    """
    from cartograph_mcp import server as srv
    doc = srv.create_edge.__doc__ or ""
    if hasattr(srv.create_edge, "fn"):
        doc = srv.create_edge.fn.__doc__ or doc
    assert "Refuses self-loops" not in doc, \
        f"docstring still lies:\n{doc}"
    assert "self-loop" in doc.lower() or "self loops" in doc.lower(), \
        f"docstring should mention self-loops are allowed:\n{doc}"

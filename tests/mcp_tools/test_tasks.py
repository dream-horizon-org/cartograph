"""Tests for task tools: create, respond (state machine), raise_blocker, reads."""

import pytest

from cartograph_mcp.tools import tasks


# ============ create_task ============


def test_create_task_by_orchestrator(agent_factory):
    agent_factory("orch-1", "orchestrator")
    agent_factory("iter-gh", "iterator")

    task = tasks.create_task("orch-1", "iter-gh", "Enumerate github repos")
    assert task["status"] == "BW"
    assert task["owner_agent_id"] == "orch-1"
    assert task["worker_agent_id"] == "iter-gh"
    assert task["description"] == "Enumerate github repos"


def test_create_task_by_admin_allowed(agent_factory):
    agent_factory("iter-gh", "iterator")
    task = tasks.create_task("admin", "iter-gh", "admin-created task")
    assert task["owner_agent_id"] == "admin"
    assert task["status"] == "BW"


def test_create_task_non_orchestrator_rejected(agent_factory):
    agent_factory("sme-1", "sme")
    agent_factory("iter-gh", "iterator")
    with pytest.raises(ValueError, match="Only orchestrator or admin"):
        tasks.create_task("sme-1", "iter-gh", "should fail")


def test_create_task_unknown_worker(agent_factory):
    agent_factory("orch-1", "orchestrator")
    with pytest.raises(ValueError, match="Worker agent .* not found"):
        tasks.create_task("orch-1", "nonexistent", "no worker")


def test_create_task_self_assignment_rejected(agent_factory):
    agent_factory("orch-1", "orchestrator")
    with pytest.raises(ValueError, match="must differ"):
        tasks.create_task("orch-1", "orch-1", "self-task")


def test_create_task_empty_description_rejected(agent_factory):
    agent_factory("orch-1", "orchestrator")
    agent_factory("iter-gh", "iterator")
    with pytest.raises(ValueError, match="description cannot be empty"):
        tasks.create_task("orch-1", "iter-gh", "   ")


def test_create_task_also_inserts_communication(agent_factory):
    from shared.db import execute
    agent_factory("orch-1", "orchestrator")
    agent_factory("iter-gh", "iterator")
    task = tasks.create_task("orch-1", "iter-gh", "Enumerate")

    comms = execute(
        "SELECT * FROM communications WHERE source_id = %s AND type = 'task'",
        (task["id"],),
    )
    assert len(comms) == 1
    assert comms[0]["from_agent"] == "orch-1"
    assert comms[0]["to_agent"] == "iter-gh"
    assert comms[0]["text"] == "Enumerate"


# ============ respond_task — worker transitions ============


def test_worker_raises_blocker_BW_to_BO(agent_factory):
    agent_factory("orch-1", "orchestrator")
    agent_factory("iter-gh", "iterator")
    task = tasks.create_task("orch-1", "iter-gh", "List repos")

    updated = tasks.respond_task(
        agent_id="iter-gh",
        task_id=task["id"],
        message="Cannot access private repo X",
        new_status="BO",
        blocker_detail="need access to dream11/private",
    )
    assert updated["status"] == "BO"
    assert updated["blocker_detail"] == "need access to dream11/private"


def test_worker_completes_BW_to_WD(agent_factory):
    agent_factory("orch-1", "orchestrator")
    agent_factory("iter-gh", "iterator")
    task = tasks.create_task("orch-1", "iter-gh", "List repos")

    updated = tasks.respond_task(
        agent_id="iter-gh",
        task_id=task["id"],
        message="Listed 50 repos into resources table",
        new_status="WD",
    )
    assert updated["status"] == "WD"


def test_worker_cannot_close_BW_to_TC(agent_factory):
    agent_factory("orch-1", "orchestrator")
    agent_factory("iter-gh", "iterator")
    task = tasks.create_task("orch-1", "iter-gh", "List repos")
    with pytest.raises(ValueError, match="Invalid transition"):
        tasks.respond_task("iter-gh", task["id"], "try to close", "TC")


def test_worker_cannot_move_from_BO(agent_factory):
    """Worker can't move out of BO — owner must unblock."""
    agent_factory("orch-1", "orchestrator")
    agent_factory("iter-gh", "iterator")
    task = tasks.create_task("orch-1", "iter-gh", "task")
    tasks.respond_task("iter-gh", task["id"], "blocker", "BO", blocker_detail="x")

    with pytest.raises(ValueError, match="Invalid transition"):
        tasks.respond_task("iter-gh", task["id"], "try unblock", "BW")


# ============ respond_task — owner transitions ============


def test_owner_resolves_blocker_BO_to_BW(agent_factory):
    agent_factory("orch-1", "orchestrator")
    agent_factory("iter-gh", "iterator")
    task = tasks.create_task("orch-1", "iter-gh", "List repos")
    tasks.respond_task("iter-gh", task["id"], "blocker", "BO", blocker_detail="x")

    updated = tasks.respond_task(
        "orch-1", task["id"], "Resolved — try again", "BW"
    )
    assert updated["status"] == "BW"
    # blocker_detail cleared when owner unblocks
    assert updated["blocker_detail"] is None


def test_owner_closes_BO_to_TC(agent_factory):
    agent_factory("orch-1", "orchestrator")
    agent_factory("iter-gh", "iterator")
    task = tasks.create_task("orch-1", "iter-gh", "Task")
    tasks.respond_task("iter-gh", task["id"], "blocker", "BO", blocker_detail="x")

    updated = tasks.respond_task("orch-1", task["id"], "Cancel it", "TC")
    assert updated["status"] == "TC"


def test_owner_rejects_WD_to_BW(agent_factory):
    agent_factory("orch-1", "orchestrator")
    agent_factory("iter-gh", "iterator")
    task = tasks.create_task("orch-1", "iter-gh", "Task")
    tasks.respond_task("iter-gh", task["id"], "done", "WD")

    updated = tasks.respond_task("orch-1", task["id"], "Redo", "BW")
    assert updated["status"] == "BW"


def test_owner_accepts_WD_to_TC(agent_factory):
    agent_factory("orch-1", "orchestrator")
    agent_factory("iter-gh", "iterator")
    task = tasks.create_task("orch-1", "iter-gh", "Task")
    tasks.respond_task("iter-gh", task["id"], "done", "WD")

    updated = tasks.respond_task("orch-1", task["id"], "Good", "TC")
    assert updated["status"] == "TC"


def test_owner_cannot_act_in_BW(agent_factory):
    """Owner has no transitions from BW — worker's turn."""
    agent_factory("orch-1", "orchestrator")
    agent_factory("iter-gh", "iterator")
    task = tasks.create_task("orch-1", "iter-gh", "Task")

    with pytest.raises(ValueError, match="Invalid transition"):
        tasks.respond_task("orch-1", task["id"], "poke", "BO")


# ============ respond_task — validation ============


def test_respond_task_non_participant_rejected(agent_factory):
    agent_factory("orch-1", "orchestrator")
    agent_factory("iter-gh", "iterator")
    agent_factory("sme-other", "sme")
    task = tasks.create_task("orch-1", "iter-gh", "Task")

    with pytest.raises(ValueError, match="neither owner .* nor worker"):
        tasks.respond_task("sme-other", task["id"], "stealing", "WD")


def test_respond_task_unknown_task(agent_factory):
    import uuid
    agent_factory("iter-gh", "iterator")
    with pytest.raises(ValueError, match="Task .* not found"):
        tasks.respond_task("iter-gh", str(uuid.uuid4()), "hi", "WD")


def test_respond_task_empty_message_rejected(agent_factory):
    agent_factory("orch-1", "orchestrator")
    agent_factory("iter-gh", "iterator")
    task = tasks.create_task("orch-1", "iter-gh", "Task")
    with pytest.raises(ValueError, match="message cannot be empty"):
        tasks.respond_task("iter-gh", task["id"], "  ", "WD")


def test_cannot_transition_from_TC(agent_factory):
    agent_factory("orch-1", "orchestrator")
    agent_factory("iter-gh", "iterator")
    task = tasks.create_task("orch-1", "iter-gh", "Task")
    tasks.respond_task("iter-gh", task["id"], "done", "WD")
    tasks.respond_task("orch-1", task["id"], "accept", "TC")

    with pytest.raises(ValueError, match="Invalid transition"):
        tasks.respond_task("orch-1", task["id"], "reopen?", "BW")


# ============ raise_blocker ============


def test_raise_blocker_shortcut(agent_factory):
    agent_factory("orch-1", "orchestrator")
    agent_factory("iter-gh", "iterator")
    task = tasks.create_task("orch-1", "iter-gh", "Task")

    updated = tasks.raise_blocker("iter-gh", task["id"], "need helm CLI")
    assert updated["status"] == "BO"
    assert updated["blocker_detail"] == "need helm CLI"


def test_raise_blocker_empty_detail_rejected(agent_factory):
    agent_factory("orch-1", "orchestrator")
    agent_factory("iter-gh", "iterator")
    task = tasks.create_task("orch-1", "iter-gh", "Task")
    with pytest.raises(ValueError, match="blocker_detail cannot be empty"):
        tasks.raise_blocker("iter-gh", task["id"], "   ")


# ============ get_my_tasks ============


def test_get_my_tasks_returns_owner_and_worker(agent_factory):
    agent_factory("orch-1", "orchestrator")
    agent_factory("iter-gh", "iterator")
    agent_factory("iter-aws", "iterator")
    t1 = tasks.create_task("orch-1", "iter-gh", "Task 1")
    t2 = tasks.create_task("orch-1", "iter-aws", "Task 2")

    # Orchestrator sees both (owner)
    own = tasks.get_my_tasks("orch-1")
    assert len(own) == 2

    # iter-gh sees only its one
    gh = tasks.get_my_tasks("iter-gh")
    assert len(gh) == 1
    assert gh[0]["id"] == t1["id"]


def test_get_my_tasks_excludes_TC(agent_factory):
    agent_factory("orch-1", "orchestrator")
    agent_factory("iter-gh", "iterator")
    t = tasks.create_task("orch-1", "iter-gh", "Task")
    tasks.respond_task("iter-gh", t["id"], "done", "WD")
    tasks.respond_task("orch-1", t["id"], "accept", "TC")

    assert tasks.get_my_tasks("orch-1") == []


# ============ get_task_thread ============


def test_get_task_thread_shows_communications(agent_factory):
    agent_factory("orch-1", "orchestrator")
    agent_factory("iter-gh", "iterator")
    t = tasks.create_task("orch-1", "iter-gh", "Task")
    tasks.respond_task("iter-gh", t["id"], "blocker", "BO", blocker_detail="x")
    tasks.respond_task("orch-1", t["id"], "resolved", "BW")

    thread = tasks.get_task_thread(t["id"], "orch-1")
    assert len(thread) == 3  # creation + BO + BW
    texts = [m["text"] for m in thread]
    assert "Task" in texts[0]
    assert "blocker" in texts[1]
    assert "resolved" in texts[2]


def test_get_task_thread_scoped_to_participants(agent_factory):
    agent_factory("orch-1", "orchestrator")
    agent_factory("iter-gh", "iterator")
    agent_factory("sme-other", "sme")
    t = tasks.create_task("orch-1", "iter-gh", "Task")

    with pytest.raises(ValueError, match="not a participant"):
        tasks.get_task_thread(t["id"], "sme-other")


def test_get_task_thread_unknown_task(agent_factory):
    import uuid
    agent_factory("orch-1", "orchestrator")
    with pytest.raises(ValueError, match="Task .* not found"):
        tasks.get_task_thread(str(uuid.uuid4()), "orch-1")

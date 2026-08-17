"""Tests for Supervisor Executor using FAKE_WORKER."""

import os
import pytest
from orchestrator.goal import Goal, Plan
from orchestrator.generator import generate_contracts
from orchestrator.supervisor import Supervisor


WORKSPACE_ROOT = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))


@pytest.fixture(autouse=True)
def set_fake_worker(monkeypatch):
    monkeypatch.setenv("FAKE_WORKER", "1")


def test_supervisor_execute_plan_success(tmp_path):
    ws_dir = str(tmp_path)
    run_dir = os.path.join(ws_dir, "scratch", "runs")
    goal = Goal(
        goal_id="sup-success",
        title="Two-step success goal",
        description="Step 1 creates a file, Step 2 validates it",
        constraints={"workspace_scope": {"allow": ["scratch/proof/"], "deny": []}},
    )
    plan = generate_contracts(goal, workspace_root=ws_dir)
    supervisor = Supervisor(goal, plan, workspace_root=ws_dir, run_dir=run_dir)

    res = supervisor.execute_plan()
    assert len(res) == 2
    for tid, status in res.items():
        assert status in ("COMPLETE", "complete")
    assert goal.status in ("COMPLETE", "complete")

    agg = supervisor.aggregate_results()
    assert agg["goal_id"] == "sup-success"
    assert agg["summary"]["completed"] == 2


def test_supervisor_dependency_order(tmp_path):
    ws_dir = str(tmp_path)
    run_dir = os.path.join(ws_dir, "scratch", "runs")
    goal = Goal(
        goal_id="sup-dep-order",
        title="Dependency order goal",
        description="Step 1 creates a file, Step 2 validates it",
    )
    plan = generate_contracts(goal, workspace_root=ws_dir)
    supervisor = Supervisor(goal, plan, workspace_root=ws_dir, run_dir=run_dir)

    # Initial ready tasks should only be step 1
    assert supervisor.graph.get_ready_tasks() == ["sup-dep-order-step-1"]
    assert supervisor.graph.get_blocked_tasks() == ["sup-dep-order-step-2"]

    res = supervisor.execute_plan()
    assert goal.status in ("COMPLETE", "complete")
    assert len(res) == 2


def test_supervisor_blocked_on_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_WORKER", "FAIL")
    ws_dir = str(tmp_path)
    run_dir = os.path.join(ws_dir, "scratch", "runs")
    goal = Goal(
        goal_id="sup-fail",
        title="Failure goal",
        description="Step 1 creates a file, Step 2 validates it",
    )
    plan = generate_contracts(goal, workspace_root=ws_dir)
    supervisor = Supervisor(goal, plan, workspace_root=ws_dir, run_dir=run_dir)

    res = supervisor.execute_plan()
    assert goal.status in ("FAILED", "failed")
    # AUT-003: an exhausted failure now escalates with an impossibility
    # artifact instead of ending as a bare VERIFICATION_FAILED.
    assert res["sup-fail-step-1"] in ("ESCALATED", "escaped", "failed")
    assert "sup-fail-step-2" not in res or supervisor.graph.nodes["sup-fail-step-2"]["status"] not in ("COMPLETE", "complete")


def test_supervisor_execute_plan_with_retry(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_WORKER", "RETRY")
    ws_dir = str(tmp_path)
    run_dir = os.path.join(ws_dir, "scratch", "runs")
    goal = Goal(
        goal_id="sup-retry",
        title="Retry goal",
        description="Step 1 creates a file",
    )
    plan = generate_contracts(goal, workspace_root=ws_dir)

    # Set max_attempts to 2 on contracts in plan
    for c in plan.contracts:
        c["contract"]["worker"]["max_attempts"] = 2

    supervisor = Supervisor(goal, plan, workspace_root=ws_dir, run_dir=run_dir)

    # Initial execution fails attempt 1
    res = supervisor.execute_plan_with_retry(changed_approach="retry approach")
    assert goal.status in ("COMPLETE", "complete")
    assert res["sup-retry-step-1"] in ("COMPLETE", "complete")


def test_orphan_working_swept_on_recovery(tmp_path):
    """Autonomy fix: a WORKING task whose lease owner is a dead pid is CRASHED
    at graph-recovery time so it can be requeued (WORKING tasks are never
    'ready')."""
    from orchestrator.state import create_initial_state, save_state
    ws_dir = str(tmp_path)
    run_dir = os.path.join(ws_dir, "scratch", "runs")
    goal = Goal(goal_id="sup-orphan", title="Orphan", description="test")
    plan = generate_contracts(goal, workspace_root=ws_dir)
    supervisor = Supervisor(goal, plan, workspace_root=ws_dir, run_dir=run_dir)

    task_id = plan.contracts[0]["task_id"]
    task_dir = os.path.join(run_dir, task_id)
    os.makedirs(task_dir, exist_ok=True)
    state = create_initial_state(task_id, journal_dir=task_dir)
    state.transition("PREFLIGHT_RUNNING", reason="test")
    state.transition("READY", reason="test")
    state.transition("WORKING", reason="test")
    state.patch_data({"worker_lease": {"pid": 99999999, "ts": 0.0}})  # dead pid
    save_state(state, os.path.join(task_dir, "state.json"))

    supervisor._recover_graph_from_state_files()
    assert supervisor.graph.nodes[task_id]["status"] == "CRASHED"


def test_stall_escalates_non_terminal_nodes(tmp_path):
    """Autonomy fix: after a no-progress iteration, non-terminal nodes are
    ESCALATED with impossibility artifacts — no silent incomplete end state."""
    from orchestrator.state import create_initial_state, save_state
    ws_dir = str(tmp_path)
    run_dir = os.path.join(ws_dir, "scratch", "runs")
    goal = Goal(goal_id="sup-stall", title="Stall", description="test")
    plan = generate_contracts(goal, workspace_root=ws_dir)
    supervisor = Supervisor(goal, plan, workspace_root=ws_dir, run_dir=run_dir)

    task_id = plan.contracts[0]["task_id"]
    task_dir = os.path.join(run_dir, task_id)
    os.makedirs(task_dir, exist_ok=True)
    state = create_initial_state(task_id, journal_dir=task_dir)
    assert state.status == "DRAFTED"
    save_state(state, os.path.join(task_dir, "state.json"))

    supervisor._escalate_stalled_nodes()
    assert supervisor.results[task_id]["status"] == "ESCALATED"
    assert supervisor.graph.nodes[task_id]["status"] == "ESCALATED"

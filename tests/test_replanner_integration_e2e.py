"""End-to-end tests for adaptive replanning integration in supervisor."""

import os

import pytest

from orchestrator.goal import Goal, Plan
from orchestrator.state import create_initial_state, save_state
from orchestrator.supervisor import Supervisor


@pytest.fixture(autouse=True)
def set_fake_worker(monkeypatch):
    monkeypatch.setenv("FAKE_WORKER", "1")


@pytest.mark.fast
def test_supervisor_adaptively_replan_splits_task(tmp_path):
    goal_id = "g_replan_test"
    ws_dir = str(tmp_path / "ws")
    run_dir = str(tmp_path / "runs" / goal_id)
    os.makedirs(ws_dir, exist_ok=True)
    os.makedirs(run_dir, exist_ok=True)

    contract = {
        "task_id": "t_big",
        "title": "Big Task",
        "objective": "Heavy task that needs decomposition",
        "worker": {"max_attempts": 1},
        "inputs": [],
        "outputs": [{"path": "scratch/t_big_out.txt"}],
        "acceptance_checks": [],
    }
    goal = Goal(goal_id=goal_id, title="Replan Goal", description="Testing adaptive replanner")
    plan = Plan(goal_id=goal_id, contracts=[contract])

    # Simulate an ESCALATED task state in run_dir
    task_dir = os.path.join(run_dir, "t_big")
    os.makedirs(task_dir, exist_ok=True)
    state = create_initial_state("t_big", journal_dir=task_dir)
    state.force_escalate(reason="complexity exceeded")
    save_state(state, os.path.join(task_dir, "state.json"))

    sup = Supervisor(goal, plan, workspace_root=ws_dir, run_dir=run_dir, adaptive_replan=True)

    # Invoke adaptive replanning
    new_plan = sup.adaptively_replan()
    assert new_plan is not None
    assert len(new_plan.contracts) == 2  # Split into part-a and part-b
    assert any("t_big-part-a" in c["task_id"] for c in new_plan.contracts)
    assert any("t_big-part-b" in c["task_id"] for c in new_plan.contracts)

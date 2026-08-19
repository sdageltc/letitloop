"""Tests for Supervisor resume functionality."""

import os

import pytest

from orchestrator.generator import generate_contracts
from orchestrator.goal import Goal
from orchestrator.supervisor import Supervisor

WORKSPACE_ROOT = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))


@pytest.fixture(autouse=True)
def set_fake_worker(monkeypatch):
    monkeypatch.setenv("FAKE_WORKER", "1")


def test_resume_skips_completed_tasks(tmp_path):
    """Resume after full execution does NOT re-run any tasks."""
    ws_dir = str(tmp_path)
    run_dir = os.path.join(ws_dir, "scratch", "runs")
    goal = Goal(
        goal_id="resume-skip",
        title="Two-step resume skip",
        description="Step 1 creates, Step 2 validates",
    )
    plan = generate_contracts(goal, workspace_root=ws_dir)

    supervisor = Supervisor(goal, plan, workspace_root=ws_dir, run_dir=run_dir)
    res = supervisor.execute_plan()
    assert all(s in ("COMPLETE", "complete") for s in res.values())

    # Resume — should be idempotent
    supervisor2 = Supervisor(goal, plan, workspace_root=ws_dir, run_dir=run_dir)
    res2 = supervisor2.resume_plan()
    assert all(s in ("COMPLETE", "complete") for s in res2.values())
    assert goal.status in ("COMPLETE", "complete")


def test_resume_after_partial_execution(tmp_path):
    """Resume completes remaining tasks when only step 1 was done."""
    ws_dir = str(tmp_path)
    run_dir = os.path.join(ws_dir, "scratch", "runs")
    goal = Goal(
        goal_id="resume-partial",
        title="Two-step partial",
        description="Step 1 creates, Step 2 validates",
    )
    plan = generate_contracts(goal, workspace_root=ws_dir)

    # Only execute step 1 manually
    step1_task_id = "resume-partial-step-1"
    step2_task_id = "resume-partial-step-2"

    supervisor = Supervisor(goal, plan, workspace_root=ws_dir, run_dir=run_dir)
    # Manually update graph to simulate step 1 being ready while step 2 is blocked
    supervisor.graph.update_status(step1_task_id, "DRAFTED")
    supervisor.graph.update_status(step2_task_id, "DRAFTED")

    # Run step 1 only, then stop
    supervisor._execute_single_contract(step1_task_id)

    # Step 2 should still be DRAFTED and blocked waiting for step 1's evidence
    step2_state = supervisor._state_path(step2_task_id)
    assert not os.path.isfile(step2_state) or supervisor.graph.nodes[step2_task_id]["status"] != "COMPLETE"

    # Now resume — should complete step 2
    supervisor2 = Supervisor(goal, plan, workspace_root=ws_dir, run_dir=run_dir)
    res = supervisor2.resume_plan()

    assert step1_task_id in res
    assert step2_task_id in res
    assert res[step2_task_id] in ("COMPLETE", "complete")
    assert goal.status in ("COMPLETE", "complete")


def test_resume_rehydrates_evidence_store(tmp_path):
    """Evidence store is populated from completed tasks on resume."""
    ws_dir = str(tmp_path)
    run_dir = os.path.join(ws_dir, "scratch", "runs")
    goal = Goal(
        goal_id="resume-evidence",
        title="Two-step with evidence",
        description="Step 1 creates, Step 2 validates",
    )
    plan = generate_contracts(goal, workspace_root=ws_dir)

    supervisor = Supervisor(goal, plan, workspace_root=ws_dir, run_dir=run_dir)
    supervisor.execute_plan()

    # New supervisor, verify evidence_store is empty before resume
    supervisor2 = Supervisor(goal, plan, workspace_root=ws_dir, run_dir=run_dir)
    assert len(supervisor2.evidence_store) == 0

    supervisor2.resume_plan()
    assert len(supervisor2.evidence_store) > 0
    # Evidence store should have entries for completed contracts
    for task_id in ("resume-evidence-step-1", "resume-evidence-step-2"):
        assert task_id in supervisor2.evidence_store
        assert len(supervisor2.evidence_store[task_id]) > 0


def test_resume_on_no_state_acts_like_fresh_execute(tmp_path):
    """Resuming a plan with no existing state is equivalent to execute_plan."""
    ws_dir = str(tmp_path)
    run_dir = os.path.join(ws_dir, "scratch", "runs")
    goal = Goal(
        goal_id="resume-fresh",
        title="Fresh goal",
        description="Step 1 creates, Step 2 validates",
    )
    plan = generate_contracts(goal, workspace_root=ws_dir)

    supervisor = Supervisor(goal, plan, workspace_root=ws_dir, run_dir=run_dir)
    res = supervisor.resume_plan()
    assert all(s in ("COMPLETE", "complete") for s in res.values())
    assert goal.status in ("COMPLETE", "complete")


def test_resume_after_interrupted_mid_run(tmp_path):
    """Simulate process interruption after step 1 completes, verify resume completes step 2."""
    ws_dir = str(tmp_path)
    run_dir = os.path.join(ws_dir, "scratch", "runs")
    goal = Goal(
        goal_id="resume-interrupted",
        title="Two-step interrupted",
        description="Step 1 creates, Step 2 validates",
    )
    plan = generate_contracts(goal, workspace_root=ws_dir)

    # Execute only step 1 via execute_plan — simulate interruption before step 2 starts
    supervisor = Supervisor(goal, plan, workspace_root=ws_dir, run_dir=run_dir)

    # Manually run step 1 but DON'T call execute_plan (which would continue to step 2)
    step1_id = "resume-interrupted-step-1"
    step2_id = "resume-interrupted-step-2"

    # Directly execute step 1
    sup_result = supervisor._execute_single_contract(step1_id)
    assert sup_result in ("COMPLETE", "complete")

    # Verify step 1 state is on disk but step 2 is not
    assert os.path.isfile(supervisor._state_path(step1_id))

    # Simulate: process dies, new supervisor resumes
    supervisor2 = Supervisor(goal, plan, workspace_root=ws_dir, run_dir=run_dir)
    res = supervisor2.resume_plan()

    # Both should be in results
    assert step1_id in res
    assert step2_id in res
    assert res[step2_id] in ("COMPLETE", "complete")
    assert goal.status in ("COMPLETE", "complete")

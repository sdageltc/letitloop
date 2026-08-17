"""Tests for failure-report CLI command."""

import os
import json
import pytest
from argparse import Namespace
from orchestrator.goal import Goal
from orchestrator.generator import generate_contracts
from orchestrator.supervisor import Supervisor


WORKSPACE_ROOT = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
DEFAULT_RUN_DIR = os.path.join(WORKSPACE_ROOT, "scratch", "orchestrator_runs")


@pytest.fixture(autouse=True)
def set_fake_worker(monkeypatch):
    monkeypatch.setenv("FAKE_WORKER", "1")


def test_failure_report_on_completed_goal(capsys):
    from orchestrator.cli import cmd_failure_report
    goal = Goal(
        goal_id="fail-report-success",
        title="Success",
        description="Success goal",
    )
    plan = generate_contracts(goal, workspace_root=WORKSPACE_ROOT)
    run_dir = os.path.join(DEFAULT_RUN_DIR, goal.goal_id)
    os.makedirs(run_dir, exist_ok=True)
    with open(os.path.join(run_dir, "goal.json"), "w", encoding="utf-8") as f:
        json.dump(goal.to_dict(), f)
    with open(os.path.join(run_dir, "plan.json"), "w", encoding="utf-8") as f:
        json.dump(plan.to_dict(), f)

    supervisor = Supervisor(goal, plan, workspace_root=WORKSPACE_ROOT, run_dir=run_dir)
    supervisor.execute_plan()

    args = Namespace(goal_id=goal.goal_id, json=False)
    cmd_failure_report(args)
    captured = capsys.readouterr()
    assert "Failure Report" in captured.out
    assert "completed successfully" in captured.out or "COMPLETE" in captured.out


def test_failure_report_on_failed_goal(capsys, monkeypatch):
    monkeypatch.setenv("FAKE_WORKER", "FAIL")
    from orchestrator.cli import cmd_failure_report
    goal = Goal(
        goal_id="fail-report-fail",
        title="Fail",
        description="Fail goal",
    )
    plan = generate_contracts(goal, workspace_root=WORKSPACE_ROOT)
    run_dir = os.path.join(DEFAULT_RUN_DIR, goal.goal_id)
    os.makedirs(run_dir, exist_ok=True)
    with open(os.path.join(run_dir, "goal.json"), "w", encoding="utf-8") as f:
        json.dump(goal.to_dict(), f)
    with open(os.path.join(run_dir, "plan.json"), "w", encoding="utf-8") as f:
        json.dump(plan.to_dict(), f)

    supervisor = Supervisor(goal, plan, workspace_root=WORKSPACE_ROOT, run_dir=run_dir)
    supervisor.execute_plan()

    args = Namespace(goal_id=goal.goal_id, json=False)
    cmd_failure_report(args)
    captured = capsys.readouterr()
    assert "Failure Report" in captured.out
    # Failed task should show a failure class and remediation
    assert "remediation" in captured.out.lower() or "Failure" in captured.out


def test_failure_report_json_output(capsys):
    from orchestrator.cli import cmd_failure_report
    goal = Goal(
        goal_id="fail-report-json",
        title="JSON",
        description="JSON output test",
    )
    plan = generate_contracts(goal, workspace_root=WORKSPACE_ROOT)
    run_dir = os.path.join(DEFAULT_RUN_DIR, goal.goal_id)
    os.makedirs(run_dir, exist_ok=True)
    with open(os.path.join(run_dir, "goal.json"), "w", encoding="utf-8") as f:
        json.dump(goal.to_dict(), f)
    with open(os.path.join(run_dir, "plan.json"), "w", encoding="utf-8") as f:
        json.dump(plan.to_dict(), f)

    supervisor = Supervisor(goal, plan, workspace_root=WORKSPACE_ROOT, run_dir=run_dir)
    supervisor.execute_plan()

    args = Namespace(goal_id=goal.goal_id, json=True)
    cmd_failure_report(args)
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert isinstance(data, list)
    assert len(data) > 0
    for item in data:
        assert "task_id" in item
        assert "failure_class" in item
        assert "remediation" in item

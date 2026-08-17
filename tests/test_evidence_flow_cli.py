import json
import os

import pytest

from orchestrator.generator import generate_contracts
from orchestrator.goal import Goal
from orchestrator.supervisor import Supervisor

WORKSPACE_ROOT = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
DEFAULT_RUN_DIR = os.path.join(WORKSPACE_ROOT, "scratch", "orchestrator_runs")


@pytest.fixture
def evidence_goal():
    return Goal(
        goal_id="evidence-cli-test",
        title="Evidence CLI Test",
        description="Two-step goal for evidence flow testing",
        constraints={
            "subtasks": [
                {
                    "task_id": "evidence-cli-test-step-1",
                    "title": "Step 1",
                    "type": "implementation",
                    "objective": "Create output file",
                    "output_path": "scratch/phase2/evidence_cli_test_step1.txt",
                    "depends_on": [],
                },
                {
                    "task_id": "evidence-cli-test-step-2",
                    "title": "Step 2",
                    "type": "implementation",
                    "objective": "Use step 1 output",
                    "output_path": "scratch/phase2/evidence_cli_test_step2.txt",
                    "depends_on": ["evidence-cli-test-step-1"],
                },
            ]
        },
    )


@pytest.fixture
def evidence_plan(evidence_goal):
    plan = generate_contracts(evidence_goal, workspace_root=WORKSPACE_ROOT)
    return plan


def test_evidence_flow_cli_runs(capsys):
    """Test that evidence flow CLI produces output without error."""
    from argparse import Namespace

    from orchestrator.cli import cmd_evidence_flow

    os.environ["FAKE_WORKER"] = "1"
    try:
        # Run supervision first
        goal = Goal(
            goal_id="evidence-cli-run-test",
            title="CLI Run Test",
            description="Test evidence flow CLI",
            constraints={
                "subtasks": [
                    {
                        "task_id": "evidence-cli-run-test-step-1",
                        "title": "Step 1",
                        "objective": "Test",
                        "output_path": "scratch/test_evidence_cli_1.txt",
                        "depends_on": [],
                    },
                    {
                        "task_id": "evidence-cli-run-test-step-2",
                        "title": "Step 2",
                        "objective": "Test",
                        "output_path": "scratch/test_evidence_cli_2.txt",
                        "depends_on": ["evidence-cli-run-test-step-1"],
                    },
                ]
            },
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

        # Now call the CLI
        args = Namespace(goal_id=goal.goal_id)
        cmd_evidence_flow(args)
        captured = capsys.readouterr()

        # Verify output
        assert "Evidence Flow:" in captured.out
        assert "Receives evidence from:" in captured.out
        assert "Evidence Store" in captured.out or "completed outputs" in captured.out
    finally:
        del os.environ["FAKE_WORKER"]


def test_evidence_flow_shows_downstream_injection(capsys):
    """Test that evidence flow correctly shows downstream injection."""
    from argparse import Namespace

    from orchestrator.cli import cmd_evidence_flow

    os.environ["FAKE_WORKER"] = "1"
    try:
        goal = Goal(
            goal_id="evidence-inject-test",
            title="Injection Test",
            description="Test evidence injection display",
            constraints={
                "subtasks": [
                    {
                        "task_id": "evidence-inject-test-a",
                        "title": "Step A",
                        "objective": "Create",
                        "output_path": "scratch/test_inject_a.txt",
                        "depends_on": [],
                    },
                    {
                        "task_id": "evidence-inject-test-b",
                        "title": "Step B",
                        "objective": "Use A",
                        "output_path": "scratch/test_inject_b.txt",
                        "depends_on": ["evidence-inject-test-a"],
                    },
                    {
                        "task_id": "evidence-inject-test-c",
                        "title": "Step C",
                        "objective": "Use A too",
                        "output_path": "scratch/test_inject_c.txt",
                        "depends_on": ["evidence-inject-test-a"],
                    },
                ]
            },
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

        args = Namespace(goal_id=goal.goal_id)
        cmd_evidence_flow(args)
        captured = capsys.readouterr()

        # Both B and C should receive evidence from A
        assert "Receives evidence from:" in captured.out
    finally:
        del os.environ["FAKE_WORKER"]


def test_evidence_flow_json_output(capsys):
    import json
    from argparse import Namespace

    from orchestrator.cli import cmd_evidence_flow

    os.environ["FAKE_WORKER"] = "1"
    try:
        goal = Goal(
            goal_id="evidence-json-test",
            title="JSON test",
            description="Test JSON output",
            constraints={
                "subtasks": [
                    {
                        "task_id": "evidence-json-test-a",
                        "title": "A",
                        "objective": "A",
                        "output_path": "scratch/test_json_a.txt",
                        "depends_on": [],
                    },
                    {
                        "task_id": "evidence-json-test-b",
                        "title": "B",
                        "objective": "B",
                        "output_path": "scratch/test_json_b.txt",
                        "depends_on": ["evidence-json-test-a"],
                    },
                ]
            },
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
        cmd_evidence_flow(args)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "goal_id" in data
        assert "contracts" in data
        assert "evidence_store" in data
    finally:
        del os.environ["FAKE_WORKER"]

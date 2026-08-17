"""Agent-loop test-drive profiles: Smoke, Repair, Quality Rejection."""

import json
import os
import pytest

from orchestrator.goal import Goal, Plan
from orchestrator.generator import generate_contracts
from orchestrator.supervisor import Supervisor
from orchestrator.contract import requires_semantic_qc
from orchestrator.state import load_state
from orchestrator.exceptions import PlannerError


@pytest.fixture(autouse=True)
def prevent_real_llm_calls(monkeypatch):
    """Prevent real LLM calls during planner decomposition."""
    def mock_planner(*args, **kwargs):
        raise PlannerError("Real LLM disabled in tests")
    monkeypatch.setattr("orchestrator.generator.decompose_goal", mock_planner)


def test_profile_a_trivial_smoke(tmp_path, monkeypatch):
    """Profile A: Trivial Smoke — file creation, supervisors returns COMPLETE."""
    monkeypatch.setenv("FAKE_WORKER", "1")
    ws_dir = str(tmp_path)
    run_dir = os.path.join(ws_dir, "scratch", "runs")

    hello_path = "scratch/test-smoke/hello.py"
    goal = Goal(
        goal_id="smoke-hello",
        title="Trivial Smoke Goal",
        description="create scratch/test-smoke/hello.py with a simple print statement",
        constraints={
            "workspace_scope": {"allow": ["scratch/test-smoke/"], "deny": []},
            "subtasks": [
                {
                    "task_id": "smoke-hello-step-1",
                    "title": "Create hello.py",
                    "type": "implementation",
                    "objective": "create scratch/test-smoke/hello.py with a simple print statement",
                    "output_path": hello_path,
                    "depends_on": [],
                    "acceptance_checks": [
                        {"id": "check-hello-exists", "kind": "file_exists", "path": hello_path},
                    ],
                }
            ],
        },
    )
    plan = generate_contracts(goal, workspace_root=ws_dir)
    supervisor = Supervisor(goal, plan, workspace_root=ws_dir, run_dir=run_dir)
    res = supervisor.execute_plan()

    created_file = tmp_path / "scratch" / "test-smoke" / "hello.py"
    assert created_file.exists()
    assert res.get("smoke-hello-step-1") in ("COMPLETE", "complete")
    assert goal.status in ("COMPLETE", "complete")


def test_profile_b_repair(tmp_path, monkeypatch):
    """Profile B: Repair — hybrid worker with deterministic repair_then_success."""
    ws_dir = str(tmp_path)
    run_dir = os.path.join(ws_dir, "scratch", "runs")
    output_path = "scratch/test-repair/output.txt"

    task_id = "repair-task-1"
    contract_dict = {
        "task_id": task_id,
        "title": "Repair Task Test",
        "status": "drafted",
        "risk_tier": "auto",
        "workspace_scope": {"allow": ["scratch/test-repair/"], "deny": []},
        "objective": "Test repair_then_success hybrid profile",
        "worker": {
            "model": "hybrid:local",
            "max_attempts": 3,
            "hybrid_profile": "repair_then_success",
            "hybrid_max_turns": 3,
            "hybrid_repair_budget": 2,
        },
        "inputs": [],
        "outputs": [{"path": output_path}],
        "acceptance_checks": [
            {"id": "check-exists", "kind": "file_exists", "path": output_path},
        ],
        "qc": {"required": False, "lens": "code_correctness"},
    }

    goal = Goal(
        goal_id="repair-goal", title="Repair Profile Goal",
        description="Goal to test repair_then_success hybrid profile",
    )
    plan = Plan(goal_id=goal.goal_id, contracts=[{"task_id": task_id, "contract": contract_dict}])

    monkeypatch.delenv("FAKE_WORKER", raising=False)

    supervisor = Supervisor(goal, plan, workspace_root=ws_dir, run_dir=run_dir)
    res = supervisor.execute_plan()

    assert res.get(task_id) in ("COMPLETE", "complete")
    assert goal.status in ("COMPLETE", "complete")

    task_run_dir = tmp_path / "scratch" / "runs" / task_id
    trace_path = task_run_dir / "hybrid_trace.json"
    assert trace_path.exists()

    with open(trace_path, "r", encoding="utf-8") as f:
        trace = json.load(f)

    turn1_critic = next((s for s in trace if s["turn"] == 1 and s["role"] == "Critic"), None)
    turn2_critic = next((s for s in trace if s["turn"] == 2 and s["role"] == "Critic"), None)

    assert turn1_critic is not None
    assert turn1_critic["status"] == "fail"
    assert turn2_critic is not None
    assert turn2_critic["status"] == "pass"


def test_profile_c_quality_rejection(tmp_path, monkeypatch):
    """Profile C: Quality Rejection — bad output with QC required."""
    monkeypatch.setenv("FAKE_WORKER", "FAIL")
    ws_dir = str(tmp_path)
    run_dir = os.path.join(ws_dir, "scratch", "runs")
    output_path = "scratch/test-quality/output.py"

    task_id = "quality-task-1"
    contract_dict = {
        "task_id": task_id,
        "title": "Quality Rejection Task",
        "status": "drafted",
        "risk_tier": "qc_required",
        "workspace_scope": {"allow": ["scratch/test-quality/"], "deny": []},
        "objective": "Test quality rejection with bad output",
        "worker": {"model": "openai:gpt-4o-mini", "max_attempts": 1},
        "inputs": [],
        "outputs": [{"path": output_path}],
        "acceptance_checks": [
            {"id": "check-min-size", "kind": "min_size", "path": output_path, "expected": 50},
        ],
        "qc": {"required": True, "lens": "code_correctness"},
    }

    assert requires_semantic_qc(
        contract_dict["risk_tier"], contract_dict["outputs"], contract_dict["acceptance_checks"]
    ) is True

    goal = Goal(
        goal_id="quality-goal", title="Quality Rejection Goal",
        description="Goal to test quality rejection",
        constraints={"risk_tier": "qc_required"},
    )
    plan = Plan(goal_id=goal.goal_id, contracts=[{"task_id": task_id, "contract": contract_dict}])

    supervisor = Supervisor(goal, plan, workspace_root=ws_dir, run_dir=run_dir)
    res = supervisor.execute_plan()

    task_status = res.get(task_id)
    assert task_status not in ("COMPLETE", "complete")
    assert goal.status not in ("COMPLETE", "complete")

    state_file = tmp_path / "scratch" / "runs" / task_id / "state.json"
    assert state_file.exists()
    state = load_state(str(state_file))
    assert len(state.worker_results) > 0
    assert state.status in ("VERIFICATION_FAILED", "ESCALATED", "BLOCKED")

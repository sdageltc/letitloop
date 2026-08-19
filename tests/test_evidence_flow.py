"""Tests for evidence propagation between dependent contracts in Supervisor."""

import os

import pytest

from orchestrator.generator import generate_contracts
from orchestrator.goal import Goal, Plan
from orchestrator.supervisor import Supervisor

WORKSPACE_ROOT = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))


@pytest.fixture(autouse=True)
def set_fake_worker(monkeypatch):
    monkeypatch.setenv("FAKE_WORKER", "1")


def test_evidence_propagates_to_downstream(tmp_path):
    ws_dir = str(tmp_path)
    run_dir = os.path.join(ws_dir, "scratch", "runs")
    goal = Goal(
        goal_id="test-evidence",
        title="Two-step test goal",
        description="Step 1 creates output file, Step 2 validates it",
        constraints={},
    )
    plan = generate_contracts(goal, workspace_root=ws_dir)
    supervisor = Supervisor(goal, plan, workspace_root=ws_dir, run_dir=run_dir)
    res = supervisor.execute_plan()

    assert len(plan.contracts) == 2
    task_a_id = plan.contracts[0]["task_id"]
    task_b_id = plan.contracts[1]["task_id"]

    assert res[task_a_id] in ("COMPLETE", "complete")
    assert res[task_b_id] in ("COMPLETE", "complete")

    b_contract = plan.contracts[1].get("contract", {})
    b_inputs = b_contract.get("inputs", [])
    input_paths = [inp["path"] if isinstance(inp, dict) else inp for inp in b_inputs]

    a_outputs = supervisor.evidence_store[task_a_id]
    a_rel_out = [os.path.relpath(p, ws_dir) if os.path.isabs(p) else p for p in a_outputs]

    assert any(rel_out in input_paths for rel_out in a_rel_out)


def test_evidence_store_populated_on_complete(tmp_path):
    ws_dir = str(tmp_path)
    run_dir = os.path.join(ws_dir, "scratch", "runs")
    goal = Goal(
        goal_id="test-single",
        title="Single Task Goal",
        description="Single task",
        constraints={},
    )
    plan = generate_contracts(goal, workspace_root=ws_dir)
    plan.contracts = [plan.contracts[0]]
    supervisor = Supervisor(goal, plan, workspace_root=ws_dir, run_dir=run_dir)
    res = supervisor.execute_plan()

    task_id = plan.contracts[0]["task_id"]
    assert res[task_id] in ("COMPLETE", "complete")
    assert task_id in supervisor.evidence_store
    assert len(supervisor.evidence_store[task_id]) > 0


def test_no_evidence_without_dependency(tmp_path):
    ws_dir = str(tmp_path)
    run_dir = os.path.join(ws_dir, "scratch", "runs")
    goal = Goal(
        goal_id="test-nodep",
        title="No Dep Goal",
        description="No dep tasks",
        constraints={
            "subtasks": [
                {
                    "task_id": "test-nodep-step-1",
                    "title": "Step 1",
                    "type": "implementation",
                    "objective": "Step 1 objective",
                    "output_path": "scratch/nodep1.txt",
                    "depends_on": [],
                },
                {
                    "task_id": "test-nodep-step-2",
                    "title": "Step 2",
                    "type": "implementation",
                    "objective": "Step 2 objective",
                    "output_path": "scratch/nodep2.txt",
                    "depends_on": [],
                },
            ]
        },
    )
    plan = generate_contracts(goal, workspace_root=ws_dir)
    supervisor = Supervisor(goal, plan, workspace_root=ws_dir, run_dir=run_dir)
    supervisor.execute_plan()

    task_a_id = plan.contracts[0]["task_id"]
    b_inputs = plan.contracts[1].get("contract", {}).get("inputs", [])
    input_paths = [inp["path"] if isinstance(inp, dict) else inp for inp in b_inputs]

    a_outputs = supervisor.evidence_store.get(task_a_id, [])
    a_rel_out = [os.path.relpath(p, ws_dir) if os.path.isabs(p) else p for p in a_outputs]

    for rel_out in a_rel_out:
        assert rel_out not in input_paths


def test_inject_upstream_evidence_returns_false_no_deps(tmp_path):
    ws_dir = str(tmp_path)
    goal = Goal(
        goal_id="test-nodep-inject",
        title="Test",
        description="Test",
        constraints={},
    )
    plan = Plan(goal_id=goal.goal_id, contracts=[])
    supervisor = Supervisor(goal, plan, workspace_root=ws_dir)

    c_info = {
        "task_id": "task-1",
        "depends_on": [],
        "contract": {
            "inputs": [{"path": "existing.txt"}],
            "outputs": [{"path": "out.txt"}],
        },
    }

    result = supervisor._inject_upstream_evidence(c_info)
    assert result is False
    assert c_info["contract"]["inputs"] == [{"path": "existing.txt"}]

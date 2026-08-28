"""End-to-end tests for supervisor budget enforcement and memory bridge integration."""

import os

import pytest
from orchestrator.generator import generate_contracts
from orchestrator.goal import Goal
from orchestrator.supervisor import Supervisor


@pytest.fixture(autouse=True)
def set_fake_worker(monkeypatch):
    monkeypatch.setenv("FAKE_WORKER", "1")


@pytest.mark.fast
def test_supervisor_budget_exhaustion_blocks_task(tmp_path):
    goal_id = "sup-budget-block"
    ws_dir = str(tmp_path)
    run_dir = os.path.join(ws_dir, "scratch", "runs")

    goal = Goal(
        goal_id=goal_id,
        title="Budget Test",
        description="Testing budget limits",
        constraints={"max_tokens": 100, "max_cost_usd": 0.000001},
    )
    plan = generate_contracts(goal, workspace_root=ws_dir)
    sup = Supervisor(goal, plan, workspace_root=ws_dir, run_dir=run_dir)

    # Exhaust the budget guard
    sup.budget_guard.ledger.record("worker", "test-model", prompt_tokens=5000, completion_tokens=1000)

    res = sup.execute_plan()
    task_id = plan.contracts[0]["task_id"]
    assert res.get(task_id) == "BLOCKED"


@pytest.mark.fast
def test_supervisor_memory_bridge_staging(tmp_path):
    goal_id = "sup-mem-stage"
    ws_dir = str(tmp_path)
    run_dir = os.path.join(ws_dir, "scratch", "runs")

    goal = Goal(
        goal_id=goal_id,
        title="Memory Bridge Test",
        description="Testing memory bridge event staging",
    )
    plan = generate_contracts(goal, workspace_root=ws_dir)
    sup = Supervisor(goal, plan, workspace_root=ws_dir, run_dir=run_dir)

    res = sup.execute_plan()
    task_id = plan.contracts[0]["task_id"]
    assert res.get(task_id) in ("COMPLETE", "complete")

    mem_file = os.path.join(run_dir, "memory_bridge.jsonl")
    assert os.path.isfile(mem_file)
    with open(mem_file, "r", encoding="utf-8") as f:
        content = f.read()
    assert "CONTRACT_COMPLETED" in content
    assert task_id in content

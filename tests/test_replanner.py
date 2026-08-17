"""Tests for Evidence-Aware Replanner."""

import json
import os

from orchestrator.goal import Goal
from orchestrator.replanner import replan, suggest_fix
from orchestrator.state import create_initial_state, save_state

WORKSPACE_ROOT = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))


def test_suggest_fix_retry(tmp_path):
    run_dir = str(tmp_path)
    task_dir = os.path.join(run_dir, "t1")
    os.makedirs(task_dir, exist_ok=True)

    state = create_initial_state("t1")
    state.status = "VERIFICATION_FAILED"
    save_state(state, os.path.join(task_dir, "state.json"))

    contract_data = {
        "task_id": "t1",
        "title": "t1",
        "status": "drafted",
        "risk_tier": "auto",
        "workspace_scope": {"allow": ["scratch/"], "deny": []},
        "objective": "obj",
        "worker": {"model": "gemini:gemini-3.6-flash", "max_attempts": 3},
        "outputs": [],
        "acceptance_checks": [],
        "qc": {"required": False, "lens": "code_correctness"},
    }
    with open(os.path.join(task_dir, "contract.json"), "w", encoding="utf-8") as f:
        json.dump(contract_data, f)

    fix = suggest_fix("t1", run_dir)
    assert fix["action"] == "retry"
    assert "attempts remaining" in fix["reason"]


def test_suggest_fix_split_on_escalation_or_timeout(tmp_path):
    run_dir = str(tmp_path)
    task_dir = os.path.join(run_dir, "t2")
    os.makedirs(task_dir, exist_ok=True)

    state = create_initial_state("t2")
    state.status = "ESCALATED"
    save_state(state, os.path.join(task_dir, "state.json"))

    fix = suggest_fix("t2", run_dir)
    assert fix["action"] == "split"
    assert "escalated" in fix["reason"]


def test_replan_no_op_on_success(tmp_path):
    run_dir = str(tmp_path)
    task_dir = os.path.join(run_dir, "t_success")
    os.makedirs(task_dir, exist_ok=True)

    state = create_initial_state("t_success")
    state.status = "COMPLETE"
    save_state(state, os.path.join(task_dir, "state.json"))

    goal = Goal(goal_id="g_success", title="Success", description="desc")
    results = {"t_success": "COMPLETE"}
    plan = replan(goal, results, run_dir)

    assert plan.goal_id == "g_success"
    assert len(plan.contracts) == 1
    assert plan.contracts[0]["status"] == "complete"


def test_replan_split_failed_task(tmp_path):
    run_dir = str(tmp_path)
    task_dir = os.path.join(run_dir, "t_fail")
    os.makedirs(task_dir, exist_ok=True)

    state = create_initial_state("t_fail")
    state.status = "ESCALATED"
    save_state(state, os.path.join(task_dir, "state.json"))

    goal = Goal(goal_id="g_split", title="Split Goal", description="desc")
    results = {"t_fail": "ESCALATED"}
    plan = replan(goal, results, run_dir)

    assert plan.goal_id == "g_split"
    assert len(plan.contracts) == 2
    assert plan.contracts[0]["task_id"] == "t_fail-part-a"
    assert plan.contracts[1]["task_id"] == "t_fail-part-b"
    assert plan.contracts[1]["depends_on"] == ["t_fail-part-a"]


def test_replan_split_rewrites_downstream_to_both_parts(tmp_path):
    """Downstream contracts depending on a split task must depend on BOTH
    part-a and part-b — not only part-b — so Part B does not block tasks
    that only need Part A's outputs."""
    run_dir = str(tmp_path)
    task_dir = os.path.join(run_dir, "t_fail")
    os.makedirs(task_dir, exist_ok=True)

    state = create_initial_state("t_fail")
    state.status = "ESCALATED"
    save_state(state, os.path.join(task_dir, "state.json"))

    goal = Goal(goal_id="g_split_dep", title="Split Goal", description="desc")
    goal.contracts = [
        {"task_id": "t_fail", "depends_on": [], "status": "DRAFTED"},
        {"task_id": "downstream", "depends_on": ["t_fail"], "status": "DRAFTED"},
    ]
    results = {"t_fail": "ESCALATED"}

    plan = replan(goal, results, run_dir)

    downstream = [c for c in plan.contracts if c["task_id"] == "downstream"]
    assert downstream, "downstream contract must survive replan"
    deps = downstream[0]["depends_on"]
    assert "t_fail-part-a" in deps
    assert "t_fail-part-b" in deps

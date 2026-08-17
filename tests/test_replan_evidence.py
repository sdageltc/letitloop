"""Tests for Phase 2 — smarter evidence-driven replanning."""

import os
import json
import pytest
from unittest.mock import patch
from orchestrator.goal import Goal, Plan
from orchestrator.replanner import replan, InspectResults
from orchestrator.state import create_initial_state, save_state


def _setup_state(tmp_path, task_id, status, attempt=1, worker_results=None,
                 failure_class="", scope_violations=None, crash_reason=""):
    state = create_initial_state(task_id)
    state.status = status
    state.attempt = attempt
    if worker_results:
        state.worker_results = worker_results
    if failure_class:
        state.data["last_failure_class"] = failure_class
    if scope_violations:
        state.data["scope_violations"] = scope_violations
    if crash_reason:
        state.data["crash_reason"] = crash_reason
    run_dir = os.path.join(str(tmp_path), "runs")
    os.makedirs(os.path.join(run_dir, task_id), exist_ok=True)
    save_state(state, os.path.join(run_dir, task_id, "state.json"))
    return run_dir


class TestReplanEvidence:

    def test_all_completed_returns_no_action(self, tmp_path):
        goal = Goal(goal_id="g_rp1", title="RP1", description="")
        run_dir = _setup_state(tmp_path, "t1", "COMPLETE")
        plan = replan(goal, {"t1": {"status": "COMPLETE"}}, run_dir)
        assert plan.contracts[0]["status"] == "complete"
        assert plan.replan_rationale["trigger"] == "all_completed"

    def test_split_on_escalated(self, tmp_path):
        goal = Goal(goal_id="g_rp2", title="RP2", description="")
        run_dir = _setup_state(tmp_path, "t1", "ESCALATED", attempt=3,
                               worker_results=[{"exit_code": 1}])
        plan = replan(goal, {"t1": {"status": "ESCALATED"}}, run_dir)
        tasks = [c["task_id"] for c in plan.contracts]
        assert "t1-part-a" in tasks
        assert "t1-part-b" in tasks

    def test_scope_violation_triggers_narrow(self, tmp_path):
        goal = Goal(goal_id="g_rp3", title="RP3", description="")
        run_dir = _setup_state(tmp_path, "t1", "BLOCKED",
                               scope_violations=[{"violation_type": "write_outside_allowed", "path": "C:\\bad.txt"}])
        plan = replan(goal, {"t1": {"status": "BLOCKED"}}, run_dir)
        tasks = [c["task_id"] for c in plan.contracts]
        assert "t1" in tasks
        # Scope-narrowed contract should have deny paths
        contract = plan.contracts[0].get("contract", {})
        if contract:
            deny = contract.get("workspace_scope", {}).get("deny", [])
            assert any("bad.txt" in d for d in deny)

    def test_crash_retry_with_remaining_attempts(self, tmp_path):
        goal = Goal(goal_id="g_rp4", title="RP4", description="")
        # Set attempt=1, but create a contract with max_attempts=3 so retry is possible
        run_dir = _setup_state(tmp_path, "t1", "BLOCKED", attempt=1,
                               failure_class="task_crashed", crash_reason="RuntimeError: OOM")
        # Write contract.json with max_attempts=3
        contract_dir = os.path.join(run_dir, "t1")
        valid_contract = {
            "task_id": "t1", "title": "test", "status": "DRAFTED", "risk_tier": "auto",
            "workspace_scope": {"allow": ["scratch/"], "deny": []},
            "objective": "test", "worker": {"model": "test", "max_attempts": 3},
            "inputs": [], "outputs": [{"path": "scratch/t1_out.txt"}],
            "acceptance_checks": [{"id": "t1-chk", "kind": "file_exists", "path": "scratch/t1_out.txt", "expected": True}],
            "qc": {"required": False, "lens": "code_correctness"},
        }
        with open(os.path.join(contract_dir, "contract.json"), "w") as f:
            json.dump(valid_contract, f)
        plan = replan(goal, {"t1": {"status": "BLOCKED"}}, run_dir)
        tasks = [c["task_id"] for c in plan.contracts]
        assert "t1" in tasks  # Should retry since attempt 1 < max 3

    def test_exhausted_crash_splits(self, tmp_path):
        goal = Goal(goal_id="g_rp5", title="RP5", description="")
        run_dir = _setup_state(tmp_path, "t1", "BLOCKED", attempt=1,
                               failure_class="task_crashed", crash_reason="RuntimeError: OOM",
                               worker_results=[{"exit_code": -1}])
        plan = replan(goal, {"t1": {"status": "BLOCKED"}}, run_dir)
        # With max_attempts=1 and attempt=1, should split
        tasks = [c["task_id"] for c in plan.contracts]
        assert "t1-part-a" in tasks

    def test_replan_rationale_attached(self, tmp_path):
        goal = Goal(goal_id="g_rp6", title="RP6", description="")
        run_dir = _setup_state(tmp_path, "t1", "VERIFICATION_FAILED",
                               failure_class="verifier_output_missing")
        plan = replan(goal, {"t1": {"status": "VERIFICATION_FAILED"}}, run_dir)
        assert hasattr(plan, "replan_rationale")
        assert "evidence" in plan.replan_rationale
        assert len(plan.replan_rationale["evidence"]) == 1

    def test_partial_failure_only_failed_tasks_replanned(self, tmp_path):
        goal = Goal(goal_id="g_rp7", title="RP7", description="")
        run_dir_a = _setup_state(tmp_path, "t1", "COMPLETE")
        run_dir_b = _setup_state(tmp_path, "t2", "ESCALATED", attempt=2, worker_results=[{"exit_code": 1}])
        # run_dir is same for both
        plan = replan(goal, {"t1": {"status": "COMPLETE"}, "t2": {"status": "ESCALATED"}}, run_dir_b)
        task_ids = [c["task_id"] for c in plan.contracts]
        assert "t1" in task_ids
        assert "t2-part-a" in task_ids or "t2" in task_ids

    def test_inspect_results_reads_scope_violations(self, tmp_path):
        run_dir = _setup_state(tmp_path, "t1", "BLOCKED",
                               scope_violations=[{"violation_type": "write_outside_allowed", "path": "C:\\x.txt"}])
        inspector = InspectResults(run_dir)
        info = inspector.inspect_task("t1")
        assert len(info["scope_violations"]) == 1
        assert info["scope_violations"][0]["violation_type"] == "write_outside_allowed"

    def test_inspect_results_reads_crash_reason(self, tmp_path):
        run_dir = _setup_state(tmp_path, "t1", "BLOCKED",
                               failure_class="task_crashed", crash_reason="RuntimeError: disk full")
        inspector = InspectResults(run_dir)
        info = inspector.inspect_task("t1")
        assert info["crash_reason"] == "RuntimeError: disk full"
        assert info["failure_class"] == "task_crashed"

    def test_inspect_results_missing_state(self, tmp_path):
        inspector = InspectResults(os.path.join(str(tmp_path), "runs"))
        info = inspector.inspect_task("nonexistent")
        assert info["status"] == "missing"

    def test_replan_rationale_all_tasks_accounted(self, tmp_path):
        goal = Goal(goal_id="g_rp11", title="RP11", description="")
        run_dir = _setup_state(tmp_path, "t1", "COMPLETE")
        plan = replan(goal, {"t1": {"status": "COMPLETE"}}, run_dir)
        evidence = plan.replan_rationale["evidence"]
        assert all(e["task_id"] == "t1" for e in evidence)

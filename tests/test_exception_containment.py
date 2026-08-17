"""Tests for Phase 1 — structured exception containment in supervisor execution paths."""

import os
import json
import pytest
from unittest.mock import patch
from tests.fault_injection import inject_fault
from orchestrator.goal import Goal, Plan
from orchestrator.supervisor import Supervisor
from orchestrator.failure import FAILURE_CLASS_TASK_CRASHED


def _make_contract(task_id, output_path=None):
    return {
        "task_id": task_id,
        "depends_on": [],
        "status": "DRAFTED",
        "contract": {
            "task_id": task_id,
            "title": f"Task {task_id}",
            "status": "DRAFTED",
            "risk_tier": "auto",
            "workspace_scope": {"allow": ["scratch/"], "deny": []},
            "objective": "test",
            "worker": {"model": "test", "max_attempts": 1},
            "inputs": [],
            "outputs": [{"path": output_path or f"scratch/{task_id}_out.txt"}],
            "acceptance_checks": [
                {"id": f"{task_id}-chk", "kind": "file_exists",
                 "path": output_path or f"scratch/{task_id}_out.txt", "expected": True},
            ],
            "qc": {"required": False, "lens": "code_correctness"},
        },
    }


class TestExceptionContainment:

    @pytest.mark.fast
    def test_worker_crash_returns_crashed_status(self, tmp_path):
        """Injected worker fault returns CRASHED, not unhandled exception."""
        with inject_fault("orchestrator.supervisor.run_worker", raises=RuntimeError("worker crash")):
            goal = Goal(goal_id="g_ec1", title="EC1", description="desc")
            contracts = [_make_contract("ec1")]
            plan = Plan(goal_id=goal.goal_id, contracts=contracts)
            run_dir = os.path.join(str(tmp_path), "runs")
            sup = Supervisor(goal, plan, workspace_root=str(tmp_path), run_dir=run_dir)
            res = sup.execute_plan()
            assert res.get("ec1") == "CRASHED"

    @pytest.mark.fast
    def test_worker_crash_creates_crash_evidence(self, tmp_path):
        """A crashed task leaves crash_traceback.log on disk."""
        with inject_fault("orchestrator.supervisor.run_worker", raises=ValueError("bad data")):
            goal = Goal(goal_id="g_ec2", title="EC2", description="desc")
            contracts = [_make_contract("ec2")]
            plan = Plan(goal_id=goal.goal_id, contracts=contracts)
            run_dir = os.path.join(str(tmp_path), "runs")
            sup = Supervisor(goal, plan, workspace_root=str(tmp_path), run_dir=run_dir)
            sup.execute_plan()
            tb = os.path.join(run_dir, "ec2", "crash_traceback.log")
            assert os.path.isfile(tb)
            with open(tb) as f:
                content = f.read()
            assert "ValueError: bad data" in content

    @pytest.mark.fast
    def test_worker_crash_records_failure_class(self, tmp_path):
        """Crashed task stores FAILURE_CLASS_TASK_CRASHED in state."""
        with inject_fault("orchestrator.supervisor.run_worker", raises=RuntimeError("boom")):
            goal = Goal(goal_id="g_ec3", title="EC3", description="desc")
            contracts = [_make_contract("ec3")]
            plan = Plan(goal_id=goal.goal_id, contracts=contracts)
            run_dir = os.path.join(str(tmp_path), "runs")
            sup = Supervisor(goal, plan, workspace_root=str(tmp_path), run_dir=run_dir)
            sup.execute_plan()
            state_file = os.path.join(run_dir, "ec3", "state.json")
            assert os.path.isfile(state_file)
            with open(state_file) as f:
                state = json.load(f)
            assert state["data"]["last_failure_class"] == FAILURE_CLASS_TASK_CRASHED
            assert "RuntimeError: boom" in state["data"]["crash_reason"]

    @pytest.mark.fast
    def test_worker_crash_marks_graph_blocked(self, tmp_path):
        """Crashed task updates graph node to BLOCKED."""
        with inject_fault("orchestrator.supervisor.run_worker", raises=RuntimeError("boom")):
            goal = Goal(goal_id="g_ec4", title="EC4", description="desc")
            contracts = [_make_contract("ec4")]
            plan = Plan(goal_id=goal.goal_id, contracts=contracts)
            run_dir = os.path.join(str(tmp_path), "runs")
            sup = Supervisor(goal, plan, workspace_root=str(tmp_path), run_dir=run_dir)
            sup.execute_plan()
            assert sup.graph.nodes.get("ec4", {}).get("status") == "BLOCKED"

    @pytest.mark.fast
    def test_preflight_crash_contained(self, tmp_path):
        """Injected preflight crash returns CRASHED, not unhandled."""
        with inject_fault("orchestrator.supervisor.run_preflight", raises=OSError("disk failure")):
            goal = Goal(goal_id="g_ec5", title="EC5", description="desc")
            contracts = [_make_contract("ec5")]
            plan = Plan(goal_id=goal.goal_id, contracts=contracts)
            run_dir = os.path.join(str(tmp_path), "runs")
            sup = Supervisor(goal, plan, workspace_root=str(tmp_path), run_dir=run_dir)
            res = sup.execute_plan()
            assert res.get("ec5") == "CRASHED"

    @pytest.mark.fast
    def test_verification_crash_contained(self, tmp_path):
        """Injected verification crash returns CRASHED, not unhandled."""
        with patch.dict(os.environ, {"FAKE_WORKER": "1"}):
            with inject_fault("orchestrator.supervisor.run_verification", raises=RuntimeError("verify crash")):
                goal = Goal(goal_id="g_ec6", title="EC6", description="desc")
                contracts = [_make_contract("ec6")]
                plan = Plan(goal_id=goal.goal_id, contracts=contracts)
                run_dir = os.path.join(str(tmp_path), "runs")
                sup = Supervisor(goal, plan, workspace_root=str(tmp_path), run_dir=run_dir)
                res = sup.execute_plan()
                assert res.get("ec6") == "CRASHED"

    @pytest.mark.fast
    def test_state_load_crash_contained(self, tmp_path):
        """Injected state load crash on resume returns CRASHED."""
        # First, run to create a state file
        from orchestrator.state import create_initial_state, save_state
        from orchestrator.goal import Goal, Plan
        goal = Goal(goal_id="g_ec8", title="EC8", description="desc")
        contracts = [_make_contract("ec8")]
        plan = Plan(goal_id=goal.goal_id, contracts=contracts)
        run_dir = os.path.join(str(tmp_path), "runs")
        os.makedirs(os.path.join(run_dir, "ec8"), exist_ok=True)
        state = create_initial_state("ec8")
        save_state(state, os.path.join(run_dir, "ec8", "state.json"))
        # Now inject fault when loading that state
        with inject_fault("orchestrator.supervisor.load_state", raises=RuntimeError("state corrupt")):
            sup = Supervisor(goal, plan, workspace_root=str(tmp_path), run_dir=run_dir)
            res = sup.execute_plan()
            assert res.get("ec8") == "CRASHED"

    @pytest.mark.fast
    def test_execute_plan_returns_results_even_on_crash(self, tmp_path):
        """execute_plan returns a dict with crashed task status, doesn't raise."""
        with inject_fault("orchestrator.supervisor.run_worker", raises=RuntimeError("any crash")):
            goal = Goal(goal_id="g_ec11", title="EC11", description="desc")
            contracts = [_make_contract("ec11")]
            plan = Plan(goal_id=goal.goal_id, contracts=contracts)
            run_dir = os.path.join(str(tmp_path), "runs")
            sup = Supervisor(goal, plan, workspace_root=str(tmp_path), run_dir=run_dir)
            res = sup.execute_plan()
            assert isinstance(res, dict)
            assert "ec11" in res

    @pytest.mark.fast
    def test_execute_plan_with_retry_handles_crashed(self, tmp_path):
        """execute_plan_with_retry handles CRASHED status gracefully."""
        with inject_fault("orchestrator.supervisor.run_worker", raises=RuntimeError("retry crash")):
            goal = Goal(goal_id="g_ec12", title="EC12", description="desc")
            contracts = [_make_contract("ec12")]
            plan = Plan(goal_id=goal.goal_id, contracts=contracts)
            run_dir = os.path.join(str(tmp_path), "runs")
            sup = Supervisor(goal, plan, workspace_root=str(tmp_path), run_dir=run_dir)
            res = sup.execute_plan_with_retry()
            assert isinstance(res, dict)
            assert "ec12" in res

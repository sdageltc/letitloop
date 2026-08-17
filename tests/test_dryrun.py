"""Tests for dry-run / simulation mode."""

import os
import pytest
from unittest.mock import patch
from orchestrator.goal import Goal, Plan
from orchestrator.supervisor import Supervisor


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
            "objective": "dry-run test",
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


class TestDryRun:

    @pytest.mark.fast
    def test_dry_run_skips_worker(self, tmp_path):
        goal = Goal(goal_id="g_dry1", title="Dry1", description="desc")
        contracts = [_make_contract("d1")]
        plan = Plan(goal_id=goal.goal_id, contracts=contracts)
        run_dir = os.path.join(str(tmp_path), "runs")
        sup = Supervisor(goal, plan, workspace_root=str(tmp_path), run_dir=run_dir, dry_run=True)
        res = sup.execute_plan()
        assert res.get("d1") in ("COMPLETE", "complete")

    @pytest.mark.fast
    def test_dry_run_creates_output(self, tmp_path):
        goal = Goal(goal_id="g_dry2", title="Dry2", description="desc")
        out_path = "scratch/dry_test_out.txt"
        contracts = [_make_contract("d2", output_path=out_path)]
        plan = Plan(goal_id=goal.goal_id, contracts=contracts)
        run_dir = os.path.join(str(tmp_path), "runs")
        sup = Supervisor(goal, plan, workspace_root=str(tmp_path), run_dir=run_dir, dry_run=True)
        sup.execute_plan()
        full = os.path.join(str(tmp_path), out_path)
        assert os.path.isfile(full)
        with open(full) as f:
            assert "SIMULATED" in f.read()

    @pytest.mark.fast
    def test_dry_run_runs_preflight(self, tmp_path):
        goal = Goal(goal_id="g_dry3", title="Dry3", description="desc")
        contracts = [_make_contract("d3")]
        plan = Plan(goal_id=goal.goal_id, contracts=contracts)
        run_dir = os.path.join(str(tmp_path), "runs")
        sup = Supervisor(goal, plan, workspace_root=str(tmp_path), run_dir=run_dir, dry_run=True)
        sup.execute_plan()
        task_dir = os.path.join(run_dir, "d3")
        pf = os.path.join(task_dir, "preflight_evidence.json")
        assert os.path.isfile(pf)

    @pytest.mark.fast
    def test_dry_run_runs_verification(self, tmp_path):
        goal = Goal(goal_id="g_dry4", title="Dry4", description="desc")
        contracts = [_make_contract("d4")]
        plan = Plan(goal_id=goal.goal_id, contracts=contracts)
        run_dir = os.path.join(str(tmp_path), "runs")
        sup = Supervisor(goal, plan, workspace_root=str(tmp_path), run_dir=run_dir, dry_run=True)
        sup.execute_plan()
        task_dir = os.path.join(run_dir, "d4")
        vf = os.path.join(task_dir, "verification_evidence.json")
        assert os.path.isfile(vf)

    @pytest.mark.fast
    def test_dry_run_no_brief_file(self, tmp_path):
        goal = Goal(goal_id="g_dry5", title="Dry5", description="desc")
        contracts = [_make_contract("d5")]
        plan = Plan(goal_id=goal.goal_id, contracts=contracts)
        run_dir = os.path.join(str(tmp_path), "runs")
        sup = Supervisor(goal, plan, workspace_root=str(tmp_path), run_dir=run_dir, dry_run=True)
        sup.execute_plan()
        task_dir = os.path.join(run_dir, "d5")
        brief = os.path.join(task_dir, "worker_brief.txt")
        assert not os.path.isfile(brief)

    @pytest.mark.fast
    def test_dry_run_state_transitions(self, tmp_path):
        goal = Goal(goal_id="g_dry6", title="Dry6", description="desc")
        contracts = [_make_contract("d6")]
        plan = Plan(goal_id=goal.goal_id, contracts=contracts)
        run_dir = os.path.join(str(tmp_path), "runs")
        sup = Supervisor(goal, plan, workspace_root=str(tmp_path), run_dir=run_dir, dry_run=True)
        sup.execute_plan()
        from orchestrator.state import load_state
        state = load_state(os.path.join(run_dir, "d6", "state.json"))
        assert state.status in ("COMPLETE", "complete")

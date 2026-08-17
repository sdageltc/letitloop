"""Tests for resource limit module."""

import os
from unittest.mock import patch

import pytest

from orchestrator.goal import Goal, Plan
from orchestrator.limits import DEFAULT_LIMITS, ResourceLimits, check_limits, format_violation
from orchestrator.supervisor import Supervisor


def _make_test_contract(task_id):
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
            "outputs": [{"path": f"scratch/{task_id}_out.txt"}],
            "acceptance_checks": [
                {"id": f"{task_id}-chk", "kind": "file_exists", "path": f"scratch/{task_id}_out.txt", "expected": True},
            ],
            "qc": {"required": False, "lens": "code_correctness"},
        },
    }


class TestLimits:
    def test_wall_clock_exceeded(self):
        limits = ResourceLimits(max_wall_clock_sec=10)
        result = check_limits(limits, elapsed_sec=15)
        assert result["exceeded"] is True
        assert result["limit_type"] == "wall_clock"

    def test_output_size_exceeded(self):
        limits = ResourceLimits(max_output_size_bytes=100)
        result = check_limits(limits, elapsed_sec=1, output_size=200)
        assert result["exceeded"] is True
        assert result["limit_type"] == "output_size"

    def test_attempts_exceeded(self):
        limits = ResourceLimits(max_attempts_global=3)
        result = check_limits(limits, elapsed_sec=1, attempts=4)
        assert result["exceeded"] is True
        assert result["limit_type"] == "attempts"

    def test_iterations_exceeded(self):
        limits = ResourceLimits(max_iterations=5)
        result = check_limits(limits, elapsed_sec=1, iterations=6)
        assert result["exceeded"] is True
        assert result["limit_type"] == "iterations"

    def test_no_violation(self):
        result = check_limits(DEFAULT_LIMITS, elapsed_sec=1, output_size=100, attempts=1, iterations=1)
        assert result["exceeded"] is False

    def test_wall_clock_exact_boundary(self):
        limits = ResourceLimits(max_wall_clock_sec=10)
        result = check_limits(limits, elapsed_sec=10)
        assert result["exceeded"] is False

    def test_format_violation_wall_clock(self):
        result = check_limits(ResourceLimits(max_wall_clock_sec=10), elapsed_sec=15)
        msg = format_violation(result)
        assert "LIMIT" in msg
        assert "wall_clock" in msg

    def test_format_no_violation(self):
        result = check_limits(DEFAULT_LIMITS, elapsed_sec=1)
        assert format_violation(result) == ""

    def test_default_values_sensible(self):
        assert DEFAULT_LIMITS.max_wall_clock_sec == 600
        assert DEFAULT_LIMITS.max_output_size_bytes == 524288
        assert DEFAULT_LIMITS.max_attempts_global == 10

    @pytest.mark.fast
    def test_integration_limits_stop_execution(self, tmp_path):
        with patch.dict(os.environ, {"FAKE_WORKER": "1"}):
            goal = Goal(goal_id="g_lim_stop", title="Limit Stop", description="desc")
            contracts = [_make_test_contract("lim_a"), _make_test_contract("lim_b")]
            plan = Plan(goal_id=goal.goal_id, contracts=contracts)
            run_dir = os.path.join(str(tmp_path), "runs")
            sup = Supervisor(goal, plan, workspace_root=str(tmp_path), run_dir=run_dir)
            with patch(
                "orchestrator.limits.check_limits",
                return_value={"exceeded": True, "reason": "test limit", "limit_type": "test"},
            ):
                sup.execute_plan()
                assert sup.goal.status == "FAILED"

    @pytest.mark.fast
    def test_execution_completes_under_limits(self, tmp_path):
        with patch.dict(os.environ, {"FAKE_WORKER": "1"}):
            goal = Goal(goal_id="g_lim_ok", title="Limit OK", description="desc")
            contracts = [_make_test_contract("lim_ok")]
            plan = Plan(goal_id=goal.goal_id, contracts=contracts)
            run_dir = os.path.join(str(tmp_path), "runs")
            sup = Supervisor(goal, plan, workspace_root=str(tmp_path), run_dir=run_dir)
            sup.execute_plan()
            assert sup.goal.status == "COMPLETE"

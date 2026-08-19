"""CLI integration tests for Phase 3 commands: reconcile, scope-check, provenance, error-inspect, feedback."""

import json
import os
import subprocess
import sys

import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CLI_MODULE = "orchestrator.cli"


def _run_cli(run_dir, *args, timeout=30, expect_fail=False):
    cmd = [sys.executable, "-m", CLI_MODULE, "--run-dir", str(run_dir)] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=PROJECT_ROOT, timeout=timeout)
    if expect_fail:
        assert result.returncode != 0, (
            f"expected failure but got exit {result.returncode}:\n{result.stdout}\n{result.stderr}"
        )
    else:
        assert result.returncode == 0, f"CLI failed:\n{result.stdout}\n{result.stderr}"
    return result


def _make_fake_goal_env(goal_id, run_dir, contracts):
    """Set up a minimal goal + plan for CLI tests, return goal path."""
    goal_data = {
        "goal_id": goal_id,
        "title": goal_id,
        "description": f"Integration test for {goal_id}",
        "status": "ACTIVE",
        "constraints": {},
    }
    goal_dir = os.path.join(run_dir, goal_id)
    os.makedirs(goal_dir, exist_ok=True)
    goal_path = os.path.join(goal_dir, "goal.json")
    with open(goal_path, "w", encoding="utf-8") as f:
        json.dump(goal_data, f)

    plan_data = {
        "goal_id": goal_id,
        "contracts": contracts,
    }
    plan_path = os.path.join(goal_dir, "plan.json")
    with open(plan_path, "w", encoding="utf-8") as f:
        json.dump(plan_data, f)

    return goal_path


class TestReconcileCLI:
    @pytest.mark.integration
    def test_reconcile_on_empty_goal(self, tmp_path):
        run_dir = str(tmp_path)
        goal_id = "g_recon_empty"
        contracts = [
            {
                "task_id": "t1",
                "depends_on": [],
                "status": "DRAFTED",
                "contract_path": "nonexistent.json",
                "contract": None,
            },
        ]
        _make_fake_goal_env(goal_id, run_dir, contracts)
        result = _run_cli(run_dir, "reconcile", goal_id)
        assert "reconciliation" in result.stdout.lower() or "reconcile" in result.stdout.lower()

    @pytest.mark.integration
    def test_reconcile_json_output(self, tmp_path):
        run_dir = str(tmp_path)
        goal_id = "g_recon_json"
        contracts = [
            {
                "task_id": "t1",
                "depends_on": [],
                "status": "DRAFTED",
                "contract_path": "nonexistent.json",
                "contract": None,
            },
        ]
        _make_fake_goal_env(goal_id, run_dir, contracts)
        result = _run_cli(run_dir, "reconcile", goal_id, "--json")
        data = json.loads(result.stdout)
        assert "goal_id" in data or "passed" in data or "total_tasks" in data


class TestScopeCheckCLI:
    @pytest.mark.integration
    def test_scope_check_empty(self, tmp_path):
        run_dir = str(tmp_path)
        goal_id = "g_scope_empty"
        contracts = [
            {
                "task_id": "t1",
                "depends_on": [],
                "status": "DRAFTED",
                "contract_path": "nonexistent.json",
                "contract": None,
            },
        ]
        _make_fake_goal_env(goal_id, run_dir, contracts)
        result = _run_cli(run_dir, "scope-check", goal_id)
        assert result.returncode == 0


class TestProvenanceCLI:
    @pytest.mark.integration
    def test_provenance_on_empty(self, tmp_path):
        run_dir = str(tmp_path)
        goal_id = "g_prov_empty"
        contracts = [
            {
                "task_id": "t1",
                "depends_on": [],
                "status": "DRAFTED",
                "contract_path": "nonexistent.json",
                "contract": None,
            },
        ]
        _make_fake_goal_env(goal_id, run_dir, contracts)
        result = _run_cli(run_dir, "provenance", goal_id)
        assert "provenance" in result.stdout.lower() or goal_id in result.stdout

    @pytest.mark.integration
    def test_provenance_json_output(self, tmp_path):
        run_dir = str(tmp_path)
        goal_id = "g_prov_json"
        contracts = [
            {
                "task_id": "t1",
                "depends_on": [],
                "status": "DRAFTED",
                "contract_path": "nonexistent.json",
                "contract": None,
            },
        ]
        _make_fake_goal_env(goal_id, run_dir, contracts)
        result = _run_cli(run_dir, "provenance", goal_id, "--json")
        data = json.loads(result.stdout)
        assert isinstance(data, dict)


class TestErrorInspectCLI:
    @pytest.mark.integration
    def test_error_inspect_no_errors(self, tmp_path):
        run_dir = str(tmp_path)
        goal_id = "g_err_clean"
        contract_path = os.path.join(run_dir, goal_id, "t1")
        os.makedirs(contract_path, exist_ok=True)
        state_data = {
            "task_id": "t1",
            "status": "COMPLETE",
            "attempt": 1,
            "events": [],
            "evidence": {},
            "worker_results": [],
            "data": {},
        }
        with open(os.path.join(contract_path, "state.json"), "w", encoding="utf-8") as f:
            json.dump(state_data, f)

        contracts = [
            {
                "task_id": "t1",
                "depends_on": [],
                "status": "COMPLETE",
                "contract_path": "nonexistent.json",
                "contract": None,
            },
        ]
        _make_fake_goal_env(goal_id, run_dir, contracts)
        result = _run_cli(run_dir, "error-inspect", goal_id)
        assert "No errors" in result.stdout

    @pytest.mark.integration
    def test_error_inspect_with_error(self, tmp_path):
        run_dir = str(tmp_path)
        goal_id = "g_err_fail"
        task_id = "t_err"
        contract_path = os.path.join(run_dir, goal_id, task_id)
        os.makedirs(contract_path, exist_ok=True)
        state_data = {
            "task_id": task_id,
            "status": "VERIFICATION_FAILED",
            "attempt": 1,
            "events": [{"from": "WORKING", "to": "VERIFYING", "reason": "test"}],
            "evidence": {},
            "worker_results": [
                {
                    "exit_code": 0,
                    "stdout": "wrong",
                    "stderr": "",
                    "elapsed_sec": 0.1,
                    "failure_class": "verifier_content_mismatch",
                }
            ],
            "data": {"last_failure_class": "verifier_content_mismatch"},
        }
        with open(os.path.join(contract_path, "state.json"), "w", encoding="utf-8") as f:
            json.dump(state_data, f)

        contracts = [
            {
                "task_id": task_id,
                "depends_on": [],
                "status": "VERIFICATION_FAILED",
                "contract_path": "nonexistent.json",
                "contract": None,
            },
        ]
        _make_fake_goal_env(goal_id, run_dir, contracts)
        result = _run_cli(run_dir, "error-inspect", goal_id, expect_fail=True)
        assert "E009" in result.stderr or "E009" in result.stdout or "error(s)" in result.stdout

    @pytest.mark.integration
    def test_error_inspect_json(self, tmp_path):
        run_dir = str(tmp_path)
        goal_id = "g_err_json"
        task_id = "t2"
        contract_path = os.path.join(run_dir, goal_id, task_id)
        os.makedirs(contract_path, exist_ok=True)
        state_data = {
            "task_id": task_id,
            "status": "VERIFICATION_FAILED",
            "attempt": 1,
            "events": [],
            "evidence": {},
            "worker_results": [
                {
                    "exit_code": 0,
                    "stdout": "",
                    "stderr": "",
                    "elapsed_sec": 0.1,
                    "failure_class": "verifier_content_mismatch",
                }
            ],
            "data": {"last_failure_class": "verifier_content_mismatch"},
        }
        with open(os.path.join(contract_path, "state.json"), "w", encoding="utf-8") as f:
            json.dump(state_data, f)

        contracts = [
            {
                "task_id": task_id,
                "depends_on": [],
                "status": "VERIFICATION_FAILED",
                "contract_path": "nonexistent.json",
                "contract": None,
            },
        ]
        _make_fake_goal_env(goal_id, run_dir, contracts)
        result = _run_cli(run_dir, "error-inspect", goal_id, "--json")
        data = json.loads(result.stdout)
        assert isinstance(data, list)


class TestFeedbackCLI:
    @pytest.mark.integration
    def test_feedback_empty(self, tmp_path):
        run_dir = str(tmp_path)
        goal_id = "g_fb_empty"
        contracts = [
            {
                "task_id": "t1",
                "depends_on": [],
                "status": "DRAFTED",
                "contract_path": "nonexistent.json",
                "contract": None,
            },
        ]
        _make_fake_goal_env(goal_id, run_dir, contracts)
        result = _run_cli(run_dir, "feedback", goal_id)
        assert "No feedback" in result.stdout

    @pytest.mark.integration
    def test_feedback_json_output(self, tmp_path):
        run_dir = str(tmp_path)
        goal_id = "g_fb_json"
        contracts = [
            {
                "task_id": "t1",
                "depends_on": [],
                "status": "DRAFTED",
                "contract_path": "nonexistent.json",
                "contract": None,
            },
        ]
        _make_fake_goal_env(goal_id, run_dir, contracts)
        result = _run_cli(run_dir, "feedback", goal_id, "--json")
        data = json.loads(result.stdout)
        assert isinstance(data, list)


class TestFailureReportCLI:
    @pytest.mark.integration
    def test_failure_report_on_completed(self, tmp_path):
        run_dir = str(tmp_path)
        goal_id = "g_fr_clean"
        task_id = "t_ok"
        contract_path = os.path.join(run_dir, goal_id, task_id)
        os.makedirs(contract_path, exist_ok=True)
        state_data = {
            "task_id": task_id,
            "status": "COMPLETE",
            "attempt": 1,
            "events": [],
            "evidence": {},
            "worker_results": [],
            "data": {},
        }
        with open(os.path.join(contract_path, "state.json"), "w", encoding="utf-8") as f:
            json.dump(state_data, f)

        contracts = [
            {
                "task_id": task_id,
                "depends_on": [],
                "status": "COMPLETE",
                "contract_path": "nonexistent.json",
                "contract": None,
            },
        ]
        _make_fake_goal_env(goal_id, run_dir, contracts)
        result = _run_cli(run_dir, "failure-report", goal_id)
        assert "completed successfully" in result.stdout or "COMPLETE" in result.stdout

    @pytest.mark.integration
    def test_failure_report_json(self, tmp_path):
        run_dir = str(tmp_path)
        goal_id = "g_fr_json"
        task_id = "t_ok"
        contract_path = os.path.join(run_dir, goal_id, task_id)
        os.makedirs(contract_path, exist_ok=True)
        state_data = {
            "task_id": task_id,
            "status": "COMPLETE",
            "attempt": 1,
            "events": [],
            "evidence": {},
            "worker_results": [],
            "data": {},
        }
        with open(os.path.join(contract_path, "state.json"), "w", encoding="utf-8") as f:
            json.dump(state_data, f)

        contracts = [
            {
                "task_id": task_id,
                "depends_on": [],
                "status": "COMPLETE",
                "contract_path": "nonexistent.json",
                "contract": None,
            },
        ]
        _make_fake_goal_env(goal_id, run_dir, contracts)
        result = _run_cli(run_dir, "failure-report", goal_id, "--json")
        data = json.loads(result.stdout)
        assert isinstance(data, list)

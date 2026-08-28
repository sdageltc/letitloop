"""Tests using the fault injection framework on orchestrator subsystems."""

import os
from unittest.mock import patch

import pytest
from orchestrator.goal import Goal, Plan
from orchestrator.supervisor import Supervisor

from tests.fault_injection import FaultInjector, corrupt_ledger, corrupt_state_file, empty_ledger, inject_fault


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
                {
                    "id": f"{task_id}-chk",
                    "kind": "file_exists",
                    "path": output_path or f"scratch/{task_id}_out.txt",
                    "expected": True,
                },
            ],
            "qc": {"required": False, "lens": "code_correctness"},
        },
    }


class TestFaultInjector:
    @pytest.mark.fast
    def test_inject_raises_on_load_state(self, tmp_path):
        """Fault injection: load_state raises RuntimeError."""
        p = os.path.join(str(tmp_path), "dummy.json")
        with open(p, "w") as f:
            f.write(
                '{"task_id": "t1", "status": "DRAFTED", "data": {}, "attempt": 0, "worker_results": [], "evidence": {}}'
            )
        with inject_fault("orchestrator.state.load_state", raises=RuntimeError("disk failure")):
            from orchestrator.state import load_state

            with pytest.raises(RuntimeError, match="disk failure"):
                load_state(p)

    @pytest.mark.fast
    def test_inject_returns_fake_state(self, tmp_path):
        """Fault injection: load_state returns canned value."""
        fake = type("FakeState", (), {"status": "COMPLETE", "attempt": 0, "evidence": {}, "worker_results": []})()
        with inject_fault("orchestrator.state.load_state", returns=fake):
            from orchestrator.state import load_state

            result = load_state("anything.json")
            assert result.status == "COMPLETE"

    @pytest.mark.fast
    def test_injector_registry(self):
        """FaultInjector with multiple targets."""
        fi = FaultInjector()
        fi.add("orchestrator.state.load_state", raises=ValueError("bad"))
        fi.add("orchestrator.preflight.run_preflight", returns=(False, {}, None))
        assert len(fi._patchers) == 2

    @pytest.mark.fast
    def test_corrupt_state_file_causes_failure(self, tmp_path):
        """A corrupt state.json leads to supervisor failure."""
        goal = Goal(goal_id="g_fi1", title="FI1", description="desc")
        contracts = [_make_contract("fi1")]
        plan = Plan(goal_id=goal.goal_id, contracts=contracts)
        run_dir = os.path.join(str(tmp_path), "runs")
        os.makedirs(os.path.join(run_dir, "fi1"), exist_ok=True)
        corrupt_state_file(os.path.join(run_dir, "fi1"))
        Supervisor(goal, plan, workspace_root=str(tmp_path), run_dir=run_dir)
        from orchestrator.exceptions import StateError
        from orchestrator.state import load_state

        with pytest.raises(StateError):
            load_state(os.path.join(run_dir, "fi1", "state.json"))

    @pytest.mark.fast
    def test_empty_ledger_doesnt_crash_reconciliation(self, tmp_path):
        """An empty evidence ledger causes no crash in reconcile."""
        from orchestrator.reconcile import run_reconciliation

        goal = Goal(goal_id="g_fi2", title="FI2", description="desc")
        contracts = [_make_contract("fi2")]
        plan = Plan(goal_id=goal.goal_id, contracts=contracts)
        run_dir = os.path.join(str(tmp_path), "runs")
        os.makedirs(run_dir, exist_ok=True)
        empty_ledger(run_dir)
        report = run_reconciliation(goal.goal_id, plan, str(tmp_path), run_dir)
        assert report.passed  # no tasks are COMPLETE, so no ledger mismatch

    @pytest.mark.fast
    def test_corrupt_ledger_doesnt_crash_reconciliation(self, tmp_path):
        """An unparseable evidence ledger causes no crash in reconcile."""
        from orchestrator import evidence as ev
        from orchestrator.reconcile import run_reconciliation

        goal = Goal(goal_id="g_fi3", title="FI3", description="desc")
        contracts = [_make_contract("fi3")]
        plan = Plan(goal_id=goal.goal_id, contracts=contracts)
        run_dir = os.path.join(str(tmp_path), "runs")
        os.makedirs(run_dir, exist_ok=True)
        corrupt_ledger(run_dir)
        # Should not crash
        ledger = ev.load_ledger(run_dir)
        assert ledger == {}
        report = run_reconciliation(goal.goal_id, plan, str(tmp_path), run_dir)
        assert report.passed

    @pytest.mark.fast
    def test_worker_fault_injection(self, tmp_path):
        """Fault barrier catches worker RuntimeError, returns CRASHED."""
        with inject_fault("orchestrator.supervisor.run_worker", raises=RuntimeError("LLM timeout")):
            goal = Goal(goal_id="g_fi4", title="FI4", description="desc")
            contracts = [_make_contract("fi4")]
            plan = Plan(goal_id=goal.goal_id, contracts=contracts)
            run_dir = os.path.join(str(tmp_path), "runs")
            sup = Supervisor(goal, plan, workspace_root=str(tmp_path), run_dir=run_dir)
            res = sup.execute_plan()
            assert res.get("fi4") == "CRASHED"

    @pytest.mark.fast
    def test_preflight_fault_injection(self, tmp_path):
        """Fault barrier catches preflight OSError, returns CRASHED."""
        with inject_fault("orchestrator.supervisor.run_preflight", raises=OSError("permission denied")):
            goal = Goal(goal_id="g_fi5", title="FI5", description="desc")
            contracts = [_make_contract("fi5")]
            plan = Plan(goal_id=goal.goal_id, contracts=contracts)
            run_dir = os.path.join(str(tmp_path), "runs")
            sup = Supervisor(goal, plan, workspace_root=str(tmp_path), run_dir=run_dir)
            res = sup.execute_plan()
            assert res.get("fi5") == "CRASHED"

    @pytest.mark.fast
    def test_state_save_fault_injection(self, tmp_path):
        """Simulate OSError on state save with retry logic."""
        calls = []

        def flaky_save(state, path):
            calls.append(1)
            if len(calls) < 3:
                raise OSError("disk full")

        with inject_fault("orchestrator.supervisor.save_state", side_effect=flaky_save):
            with patch.dict(os.environ, {"FAKE_WORKER": "1"}):
                goal = Goal(goal_id="g_fi6", title="FI6", description="desc")
                contracts = [_make_contract("fi6")]
                plan = Plan(goal_id=goal.goal_id, contracts=contracts)
                run_dir = os.path.join(str(tmp_path), "runs")
                sup = Supervisor(goal, plan, workspace_root=str(tmp_path), run_dir=run_dir)
                sup.execute_plan()
                # Retry should succeed eventually
                assert len(calls) >= 3

    def test_overlapping_faults_isolated(self):
        """Two FaultInjectors in sequence don't leak."""
        fi1 = FaultInjector().add("orchestrator.state.load_state", raises=KeyError("first"))
        fi2 = FaultInjector().add("orchestrator.state.load_state", raises=KeyError("second"))
        with fi1:
            with pytest.raises(KeyError, match="first"):
                from orchestrator.state import load_state

                load_state("x.json")
        with fi2:
            with pytest.raises(KeyError, match="second"):
                from orchestrator.state import load_state

                load_state("x.json")

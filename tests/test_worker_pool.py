"""Tests for async worker pool."""

import os
import json
import pytest
from unittest.mock import patch
from orchestrator.goal import Goal, Plan, ContractGraph
from orchestrator.supervisor import Supervisor
from orchestrator.worker_pool import WorkerPool, filter_independent_tasks, format_pool_status


WORKSPACE_ROOT = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))


def make_contract(task_id, depends_on=None, output_path=None):
    contract_dict = {
        "task_id": task_id,
        "title": f"Task {task_id}",
        "status": "DRAFTED",
        "risk_tier": "auto",
        "workspace_scope": {"allow": ["scratch/"], "deny": []},
        "objective": f"Write {output_path or f'scratch/{task_id}_out.txt'} containing 'ok'",
        "worker": {"model": "test", "max_attempts": 1},
        "inputs": [],
        "outputs": [{"path": output_path or f"scratch/{task_id}_out.txt"}],
        "acceptance_checks": [
            {"id": f"{task_id}-chk", "kind": "file_exists", "path": output_path or f"scratch/{task_id}_out.txt", "expected": True},
        ],
        "qc": {"required": False, "lens": "code_correctness"},
    }
    return {
        "task_id": task_id,
        "depends_on": depends_on or [],
        "status": "DRAFTED",
        "contract": contract_dict,
    }


class TestWorkerPool:

    def test_pool_empty_batch(self):
        pool = WorkerPool(max_workers=4)
        results = pool.execute_batch([], lambda tid: "COMPLETE")
        assert results == {}

    def test_pool_single_task(self):
        pool = WorkerPool(max_workers=4)
        tasks = [{"task_id": "t1"}]
        results = pool.execute_batch(tasks, lambda tid: "COMPLETE")
        assert results == {"t1": "COMPLETE"}

    def test_pool_multiple_tasks(self):
        pool = WorkerPool(max_workers=4)
        tasks = [{"task_id": f"t{i}"} for i in range(3)]
        results = pool.execute_batch(tasks, lambda tid: "COMPLETE")
        assert len(results) == 3
        for i in range(3):
            assert results[f"t{i}"] == "COMPLETE"

    def test_pool_task_failure(self):
        pool = WorkerPool(max_workers=4)

        def failing(tid):
            raise RuntimeError("boom")

        results = pool.execute_batch([{"task_id": "t1"}], failing)
        assert "FAILED" in results["t1"]

    def test_pool_mixed_results(self):
        pool = WorkerPool(max_workers=4)

        def mixed(tid):
            if tid == "good":
                return "COMPLETE"
            raise ValueError("bad task")

        tasks = [{"task_id": "good"}, {"task_id": "bad"}]
        results = pool.execute_batch(tasks, mixed)
        assert results["good"] == "COMPLETE"
        assert "FAILED" in results["bad"]

    def test_pool_max_workers_clamped(self):
        pool = WorkerPool(max_workers=0)
        assert pool.max_workers == 1
        pool.max_workers = 100
        assert pool.max_workers == 8

    def test_pool_on_result_callback(self):
        pool = WorkerPool(max_workers=4)
        callback_results = {}

        def cb(tid, status):
            callback_results[tid] = status

        tasks = [{"task_id": "t1"}, {"task_id": "t2"}]
        pool.execute_batch(tasks, lambda tid: "COMPLETE", on_result=cb)
        assert callback_results == {"t1": "COMPLETE", "t2": "COMPLETE"}


class TestWorkerPoolIntegration:

    @pytest.mark.fast
    def test_parallel_plan_independent_tasks(self, tmp_path):
        with patch.dict(os.environ, {"FAKE_WORKER": "1"}):
            goal = Goal(goal_id="g_parallel_independent", title="Parallel Indep", description="desc")
            contracts = [
                make_contract("indep_a", output_path="scratch/test_pool/indep_a.txt"),
                make_contract("indep_b", output_path="scratch/test_pool/indep_b.txt"),
            ]
            plan = Plan(goal_id=goal.goal_id, contracts=contracts)

            goal_path = os.path.join(str(tmp_path), "goal.json")
            run_dir = os.path.join(str(tmp_path), "runs")
            with open(goal_path, "w") as f:
                json.dump(goal.to_dict(), f)

            sup = Supervisor(goal, plan, workspace_root=str(tmp_path), run_dir=run_dir, parallel=True, max_workers=2)
            res = sup.execute_plan()
            assert res.get("indep_a") in ("COMPLETE", "complete")
            assert res.get("indep_b") in ("COMPLETE", "complete")
            assert sup.goal.status == "COMPLETE"

    @pytest.mark.fast
    def test_parallel_plan_with_dependency(self, tmp_path):
        with patch.dict(os.environ, {"FAKE_WORKER": "1"}):
            goal = Goal(goal_id="g_parallel_chain", title="Parallel Chain", description="desc")
            contracts = [
                make_contract("upstream", output_path="scratch/test_pool/upstream.txt"),
                make_contract("downstream", depends_on=["upstream"], output_path="scratch/test_pool/downstream.txt"),
            ]
            plan = Plan(goal_id=goal.goal_id, contracts=contracts)

            run_dir = os.path.join(str(tmp_path), "runs")
            sup = Supervisor(goal, plan, workspace_root=str(tmp_path), run_dir=run_dir, parallel=True, max_workers=2)
            res = sup.execute_plan()
            assert res.get("upstream") in ("COMPLETE", "complete")
            assert res.get("downstream") in ("COMPLETE", "complete")

    @pytest.mark.fast
    def test_parallel_plan_three_independent(self, tmp_path):
        with patch.dict(os.environ, {"FAKE_WORKER": "1"}):
            goal = Goal(goal_id="g_parallel_3", title="Parallel 3", description="desc")
            contracts = [
                make_contract(f"w{i}", output_path=f"scratch/test_pool/w{i}.txt")
                for i in range(3)
            ]
            plan = Plan(goal_id=goal.goal_id, contracts=contracts)

            run_dir = os.path.join(str(tmp_path), "runs")
            sup = Supervisor(goal, plan, workspace_root=str(tmp_path), run_dir=run_dir, parallel=True, max_workers=3)
            res = sup.execute_plan()
            for i in range(3):
                assert res.get(f"w{i}") in ("COMPLETE", "complete")
            assert sup.goal.status == "COMPLETE"

    @pytest.mark.fast
    def test_serial_still_works(self, tmp_path):
        with patch.dict(os.environ, {"FAKE_WORKER": "1"}):
            goal = Goal(goal_id="g_serial_test", title="Serial Test", description="desc")
            contracts = [
                make_contract("s1"),
                make_contract("s2"),
            ]
            plan = Plan(goal_id=goal.goal_id, contracts=contracts)

            run_dir = os.path.join(str(tmp_path), "runs")
            sup = Supervisor(goal, plan, workspace_root=str(tmp_path), run_dir=run_dir, parallel=False)
            res = sup.execute_plan()
            assert res.get("s1") in ("COMPLETE", "complete")
            assert res.get("s2") in ("COMPLETE", "complete")


class TestWorkerPoolUtils:

    def test_filter_independent_tasks(self):
        goal = Goal(goal_id="g", title="t", description="d")
        contracts = [
            make_contract("t1"),
            make_contract("t2", depends_on=["t1"]),
        ]
        plan = Plan(goal_id="g", contracts=contracts)
        graph = ContractGraph(plan)
        ready = graph.get_ready_tasks()
        assert ready == ["t1"]
        assert filter_independent_tasks(ready, graph) == ["t1"]

    def test_format_pool_status_empty(self):
        assert "No tasks executed" in format_pool_status({})

    def test_format_pool_status_with_results(self):
        result = format_pool_status({"t1": "COMPLETE", "t2": "VERIFICATION_FAILED"})
        assert "2 tasks" in result
        assert "1 completed" in result
        assert "1 failed" in result

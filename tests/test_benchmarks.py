"""Performance benchmarks for orchestrator hot paths.

Uses pytest-benchmark if available; falls back to simple timing assertions.
"""

import json
import os
import time

import pytest

from orchestrator.contract import Contract, validate_contract
from orchestrator.evidence import append_output, load_ledger
from orchestrator.failure import classify_failure
from orchestrator.feedback import collect_feedback
from orchestrator.goal import ContractGraph, Plan
from orchestrator.limits import ResourceLimits, check_limits
from orchestrator.scope import snapshot_scope
from orchestrator.state import create_initial_state, load_state, save_state

try:
    import pytest_benchmark
except ImportError:
    pytest_benchmark = None


@pytest.fixture
def benchmark():
    """Fallback benchmark fixture when pytest-benchmark is not installed."""

    def _bench(fn, *args, **kwargs):
        t0 = time.perf_counter()
        res = fn(*args, **kwargs)
        dur = time.perf_counter() - t0
        assert dur < 5.0
        return res

    return _bench


SAMPLE_CONTRACT = {
    "task_id": "bench",
    "title": "Benchmark Task",
    "status": "DRAFTED",
    "risk_tier": "auto",
    "workspace_scope": {"allow": ["scratch/"], "deny": []},
    "objective": "benchmark test",
    "worker": {"model": "test", "max_attempts": 1},
    "inputs": [],
    "outputs": [{"path": "scratch/bench_out.txt"}],
    "acceptance_checks": [
        {"id": "bench-chk", "kind": "file_exists", "path": "scratch/bench_out.txt", "expected": True}
    ],
    "qc": {"required": False, "lens": "code_correctness"},
}


def _minimal_contract_dict(task_id="bench"):
    return {
        "task_id": task_id,
        "title": "bench",
        "status": "DRAFTED",
        "risk_tier": "auto",
        "workspace_scope": {"allow": ["scratch/"], "deny": []},
        "objective": "bench",
        "worker": {"model": "x", "max_attempts": 1},
        "inputs": [],
        "outputs": [{"path": f"scratch/{task_id}_out.txt"}],
        "acceptance_checks": [],
        "qc": {"required": False, "lens": "code_correctness"},
    }


@pytest.mark.benchmark
def test_state_create_performance(benchmark):
    state = benchmark(create_initial_state, "bench_state")
    assert state.task_id == "bench_state"


@pytest.mark.benchmark
def test_state_save_load_roundtrip_performance(benchmark, tmp_path):
    state = create_initial_state("bench_rt")
    p = os.path.join(str(tmp_path), "state.json")

    def roundtrip():
        save_state(state, p)
        return load_state(p)

    result = benchmark(roundtrip)
    assert result.task_id == "bench_rt"


@pytest.mark.benchmark
def test_contract_graph_construction_performance(benchmark):
    contracts = [_minimal_contract_dict(f"t{i}") for i in range(100)]
    plan = Plan(goal_id="bench_graph", contracts=contracts)
    benchmark(ContractGraph, plan)


@pytest.mark.benchmark
def test_contract_graph_dependency_resolution(benchmark):
    contracts = []
    for i in range(50):
        deps = [f"t{j}" for j in range(i)]  # each depends on all previous
        c = _minimal_contract_dict(f"t{i}")
        c["depends_on"] = deps
        contracts.append(c)
    plan = Plan(goal_id="bench_dep", contracts=contracts)
    graph = ContractGraph(plan)
    benchmark(graph.get_ready_tasks)


@pytest.mark.benchmark
def test_failure_classification_performance(benchmark):
    state = create_initial_state("bench_fc")
    state._status = "VERIFICATION_FAILED"
    from orchestrator.contract import Contract

    contract = Contract(SAMPLE_CONTRACT)
    benchmark(classify_failure, state, contract)


@pytest.mark.benchmark
def test_feedback_collection_performance(benchmark):
    state = create_initial_state("bench_fb")
    state._status = "VERIFICATION_FAILED"
    benchmark(collect_feedback, "bench_fb", "goal_bench", state)


@pytest.mark.benchmark
def test_limit_check_performance(benchmark):
    limits = ResourceLimits(max_wall_clock_sec=600, max_iterations=50)
    benchmark(check_limits, limits, elapsed_sec=10, output_size=1024, attempts=2, iterations=3)


@pytest.mark.benchmark
def test_scope_snapshot_performance(benchmark, tmp_path):
    allowed = ["scratch/"]
    benchmark(snapshot_scope, str(tmp_path), allowed, str(tmp_path / "snap"))


@pytest.mark.benchmark
def test_evidence_append_performance(benchmark, tmp_path):
    out_file = os.path.join(str(tmp_path), "output.txt")
    with open(out_file, "w") as f:
        f.write("x" * 1000)
    benchmark(append_output, str(tmp_path), "t1", "output.txt", str(tmp_path))


@pytest.mark.benchmark
def test_evidence_ledger_load_performance(benchmark, tmp_path):
    ledger = {}
    for i in range(200):
        ledger[f"t{i}"] = [
            {"path": f"out_{i}.txt", "sha256": "a" * 64, "absolute_path": str(tmp_path / f"out_{i}.txt")}
        ]
    p = os.path.join(str(tmp_path), "evidence_ledger.json")
    with open(p, "w") as f:
        json.dump(ledger, f)
    benchmark(load_ledger, str(tmp_path))


@pytest.mark.benchmark
def test_validate_contract_performance(benchmark):
    benchmark(validate_contract, SAMPLE_CONTRACT, workspace_root=os.getcwd())


# --- Fallback timing tests (run even without pytest-benchmark) ---


class TestTimingSanity:
    def test_state_create_under_10ms(self):
        t0 = time.perf_counter()
        for _ in range(100):
            create_initial_state("t")
        elapsed = (time.perf_counter() - t0) / 100
        assert elapsed < 0.01, f"create_initial_state avg {elapsed * 1000:.1f}ms > 10ms"

    def test_graph_100_nodes_under_50ms(self):
        contracts = [_minimal_contract_dict(f"t{i}") for i in range(100)]
        plan = Plan(goal_id="bench", contracts=contracts)
        t0 = time.perf_counter()
        ContractGraph(plan)
        elapsed = time.perf_counter() - t0
        assert elapsed < 0.05, f"graph construction {elapsed * 1000:.1f}ms > 50ms"

    def test_limit_check_1k_calls_under_100ms(self):
        limits = ResourceLimits()
        t0 = time.perf_counter()
        for _ in range(1000):
            check_limits(limits, elapsed_sec=5, output_size=512, attempts=1, iterations=2)
        elapsed = time.perf_counter() - t0
        assert elapsed < 0.1, f"1000 limit checks {elapsed * 1000:.1f}ms > 100ms"

    def test_failure_classify_1k_under_200ms(self):
        state = create_initial_state("t")
        state._status = "VERIFICATION_FAILED"
        contract = Contract(SAMPLE_CONTRACT)
        t0 = time.perf_counter()
        for _ in range(1000):
            classify_failure(state, contract)
        elapsed = time.perf_counter() - t0
        assert elapsed < 0.2, f"1000 classifications {elapsed * 1000:.1f}ms > 200ms"

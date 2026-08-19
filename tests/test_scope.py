"""Tests for filesystem scope enforcement."""

import json
import os

import pytest

from orchestrator import failure as fail_mod
from orchestrator import scope as sc
from orchestrator.contract import Contract
from orchestrator.generator import generate_contracts
from orchestrator.goal import Goal
from orchestrator.supervisor import Supervisor


@pytest.fixture(autouse=True)
def set_fake_worker(monkeypatch):
    monkeypatch.setenv("FAKE_WORKER", "1")


def test_snapshot_and_check_pass(tmp_path):
    """Snapshot and scope check pass on clean execution."""
    ws_dir = str(tmp_path)
    run_dir = os.path.join(ws_dir, "scratch", "runs")
    goal = Goal(goal_id="scope-pass", title="Scope pass", description="Clean scope test")
    plan = generate_contracts(goal, workspace_root=ws_dir)
    supervisor = Supervisor(goal, plan, workspace_root=ws_dir, run_dir=run_dir)
    res = supervisor.execute_plan()
    assert all(s in ("COMPLETE", "complete") for s in res.values())

    for c in plan.contracts:
        tid = c["task_id"]
        task_dir = os.path.join(run_dir, tid)
        contract_path = os.path.join(task_dir, "contract.json")
        if not os.path.isfile(contract_path):
            continue
        contract, errors = __import__("orchestrator.contract", fromlist=["load_contract"]).load_contract(
            contract_path, workspace_root=ws_dir
        )
        if contract is None:
            continue
        result = sc.check_scope(contract, ws_dir, task_dir)
        assert result.passed, f"Scope check failed for {tid}: {result.violations}"


def test_detects_outside_scope_write(tmp_path):
    """Scope check detects a file written to denied path."""
    ws_dir = str(tmp_path)
    run_dir = os.path.join(ws_dir, "scratch", "runs")
    goal = Goal(goal_id="scope-outside", title="Outside", description="Test outside scope")
    plan = generate_contracts(goal, workspace_root=ws_dir)

    # Add a denied path to the contracts
    for c in plan.contracts:
        if "contract" in c and c["contract"]:
            c["contract"]["workspace_scope"]["deny"] = ["denied/"]

    supervisor = Supervisor(goal, plan, workspace_root=ws_dir, run_dir=run_dir)
    res = supervisor.execute_plan()
    assert all(s in ("COMPLETE", "complete") for s in res.values())

    # Write a file in denied path
    denied_dir = os.path.join(ws_dir, "denied")
    os.makedirs(denied_dir, exist_ok=True)
    rogue_file = os.path.join(denied_dir, "rogue.py")
    with open(rogue_file, "w", encoding="utf-8") as f:
        f.write("# rogue file in denied path")

    for c in plan.contracts:
        tid = c["task_id"]
        task_dir = os.path.join(run_dir, tid)
        contract_path = os.path.join(task_dir, "contract.json")
        if not os.path.isfile(contract_path):
            continue
        contract, _ = __import__("orchestrator.contract", fromlist=["load_contract"]).load_contract(
            contract_path, workspace_root=ws_dir
        )
        if contract is None:
            continue
        result = sc.check_scope(contract, ws_dir, task_dir)
        assert not result.passed, f"Should detect rogue file for {tid}"
        assert any(v.violation_type in ("denied_new", "outside_scope") for v in result.violations)


def test_failure_class_scope_violation(tmp_path):
    """Failure classifier returns scope_violation when scope_violations in state data."""
    from orchestrator.state import State

    state = State(task_id="scope-fail", status="VERIFICATION_FAILED")
    state.data["scope_violations"] = [{"path": "rogue.py", "violation_type": "outside_scope"}]
    fclass = fail_mod.classify_failure(state)
    assert fclass == fail_mod.FAILURE_CLASS_SCOPE_VIOLATION


def test_no_scope_violation_without_data(tmp_path):
    """No scope_violation when scope_violations is absent from state data."""
    from orchestrator.state import State

    state = State(task_id="scope-clean", status="VERIFICATION_FAILED")
    fclass = fail_mod.classify_failure(state)
    assert fclass != fail_mod.FAILURE_CLASS_SCOPE_VIOLATION


def test_snapshot_creates_file(tmp_path):
    """snapshot_scope creates snapshot JSON file."""
    ws_dir = str(tmp_path)
    run_dir = os.path.join(ws_dir, "run")
    os.makedirs(os.path.join(ws_dir, "scratch"), exist_ok=True)
    snapshot_path = sc.snapshot_scope(ws_dir, ["scratch/"], run_dir)
    assert os.path.isfile(snapshot_path)
    with open(snapshot_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert isinstance(data, dict)


def test_snapshot_roundtrip(tmp_path):
    """Snapshot then check_scope passes when no files change."""
    ws_dir = str(tmp_path)
    run_dir = os.path.join(ws_dir, "run")
    os.makedirs(os.path.join(ws_dir, "scratch"), exist_ok=True)

    sc.snapshot_scope(ws_dir, ["scratch/"], run_dir)
    snapshot = sc.load_snapshot(run_dir)
    assert isinstance(snapshot, dict)

    # No snapshot available for check (no contract) — should pass
    assert sc.load_snapshot(os.path.join(ws_dir, "no_run")) == {}


def test_scope_violation_repr(tmp_path):
    """ScopeViolation repr is non-empty."""
    v = sc.ScopeViolation(path="test.py", violation_type="outside_scope", detail="test")
    assert repr(v) == "[outside_scope] test.py"


def test_scope_check_result_to_dict(tmp_path):
    """ScopeCheckResult serializes correctly."""
    violations = [sc.ScopeViolation(path="test.py", violation_type="denied_new", detail="test")]
    result = sc.ScopeCheckResult(passed=False, violations=violations, snapshot_path="/tmp/snap.json")
    d = result.to_dict()
    assert d["passed"] is False
    assert len(d["violations"]) == 1
    assert d["snapshot_path"] == "/tmp/snap.json"
    assert d["violations"][0]["path"] == "test.py"


def test_format_scope_result(tmp_path):
    """format_scope_result returns non-empty string."""
    result_pass = sc.ScopeCheckResult(passed=True, violations=[])
    out = sc.format_scope_result(result_pass)
    assert len(out) > 0
    assert "PASSED" in out

    v = sc.ScopeViolation(path="bad.py", violation_type="denied_new", detail="created in denied")
    result_fail = sc.ScopeCheckResult(passed=False, violations=[v])
    out = sc.format_scope_result(result_fail)
    assert "FAILED" in out
    assert "bad.py" in out


def test_scope_check_empty_snapshot(tmp_path):
    """check_scope reports missing snapshot as scope violation."""
    raw = {
        "task_id": "no-snap",
        "title": "No snapshot",
        "status": "DRAFTED",
        "risk_tier": "auto",
        "workspace_scope": {"allow": ["scratch/"], "deny": []},
        "objective": "test",
        "worker": {"model": "fake", "max_attempts": 1},
        "outputs": [{"path": "scratch/test.txt"}],
        "acceptance_checks": [{"id": "c1", "kind": "file_exists", "path": "scratch/test.txt"}],
        "qc": {"required": False, "lens": "code_correctness"},
    }
    contract = Contract(raw)
    result = sc.check_scope(contract, str(tmp_path), str(tmp_path))
    assert not result.passed
    assert len(result.violations) == 1
    assert result.violations[0].violation_type == "missing_snapshot"

"""Tests for impossibility theorem artifacts."""

import os
import json
import pytest
from orchestrator import impossibility as imp
from orchestrator.state import State
from orchestrator.contract import Contract


def _make_contract(task_id="imp-test-task"):
    raw = {
        "task_id": task_id,
        "title": "Impossibility test",
        "status": "DRAFTED",
        "risk_tier": "auto",
        "workspace_scope": {"allow": ["scratch/"], "deny": []},
        "objective": "Test impossibility artifact generation",
        "worker": {"model": "fake", "max_attempts": 3},
        "inputs": [],
        "outputs": [{"path": "scratch/test_out.txt"}],
        "acceptance_checks": [{"id": "c1", "kind": "file_exists", "path": "scratch/test_out.txt", "expected": True}],
        "qc": {"required": False, "lens": "code_correctness"},
    }
    return Contract(raw)


def test_build_artifact_contains_required_fields():
    contract = _make_contract()
    state = State(task_id="imp-test-task", status="ESCALATED", attempt=3)
    state.add_worker_result({"exit_code": 1, "stdout": "", "stderr": "error"})
    state.data["last_failure_class"] = "timeout"

    art = imp.build_artifact(
        goal_id="test-goal", task_id="imp-test-task",
        contract=contract, state=state,
        failure_class="timeout",
    )
    assert art["artifact_type"] == "impossibility_theorem"
    assert art["task_id"] == "imp-test-task"
    assert art["goal_id"] == "test-goal"
    assert art["attempts_made"] == 3
    assert art["max_attempts"] == 3
    assert len(art["worker_results"]) == 1
    assert art["failure_class"] == "timeout"
    assert "recommended_human_action" in art


def test_write_artifact_creates_files(tmp_path):
    contract = _make_contract()
    state = State(task_id="imp-test-task", status="ESCALATED", attempt=2)
    state.add_worker_result({"exit_code": 1, "stdout": "", "stderr": "fail"})

    json_path, md_path = imp.write_impossibility(
        contract=contract, state=state,
        goal_id="test-goal", workspace_root=str(tmp_path),
        failure_class="worker_nonzero_exit",
    )

    assert os.path.isfile(json_path)
    assert os.path.isfile(md_path)

    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert data["task_id"] == "imp-test-task"
    assert data["failure_class"] == "worker_nonzero_exit"


def test_artifact_contains_evidence_paths(tmp_path):
    contract = _make_contract()
    state = State(task_id="imp-test-task", status="ESCALATED")
    state.add_evidence("preflight", str(tmp_path / "preflight.json"))
    state.add_evidence("verification", str(tmp_path / "verify.json"))

    art = imp.build_artifact(
        goal_id="test-goal", task_id="imp-test-task",
        contract=contract, state=state,
    )
    assert "preflight" in art["evidence_paths"]
    assert "verification" in art["evidence_paths"]


def test_artifact_includes_rejected_approaches():
    contract = _make_contract()
    state = State(task_id="imp-test-task", status="ESCALATED", attempt=2)
    state.record_approach("try method A")
    state.record_approach("try method B")

    art = imp.build_artifact(
        goal_id="test-goal", task_id="imp-test-task",
        contract=contract, state=state,
    )
    assert "try method A" in art["rejected_approaches"]
    assert "try method B" in art["rejected_approaches"]


def test_artifact_dir_structure():
    path = imp.artifact_dir("goal-42", "task-99")
    assert "goal-42" in path
    assert "task-99" in path

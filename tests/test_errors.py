"""Tests for structured error schema."""

import os
import pytest
from orchestrator.goal import Goal
from orchestrator.generator import generate_contracts
from orchestrator.supervisor import Supervisor
from orchestrator import errors as err_mod
from orchestrator.state import State


@pytest.fixture(autouse=True)
def set_fake_worker(monkeypatch):
    monkeypatch.setenv("FAKE_WORKER", "1")


def test_structured_error_creation():
    """StructuredError created with correct defaults."""
    e = err_mod.StructuredError(err_mod.E_SCOPE_VIOLATION, "File outside scope", task_id="t1")
    assert e.code == err_mod.E_SCOPE_VIOLATION
    assert e.task_id == "t1"
    assert e.severity == err_mod.SEVERITY_CRITICAL
    assert e.component == err_mod.COMPONENT_SCOPE
    assert e.timestamp


def test_structured_error_to_dict():
    """StructuredError serializes correctly."""
    e = err_mod.StructuredError(
        err_mod.E_WORKER_TIMEOUT, "Worker timed out", task_id="t1",
        context={"elapsed": 120},
    )
    d = e.to_dict()
    assert d["code"] == err_mod.E_WORKER_TIMEOUT
    assert d["severity"] == err_mod.SEVERITY_CRITICAL
    assert d["task_id"] == "t1"
    assert d["context"]["elapsed"] == 120


def test_from_failure_class():
    """from_failure_class creates correct StructuredError."""
    e = err_mod.from_failure_class("scope_violation", task_id="t1", message="bad file")
    assert e.code == err_mod.E_SCOPE_VIOLATION
    assert e.task_id == "t1"
    assert e.message == "bad file"


def test_from_failure_class_unknown():
    """Unknown failure class maps to E_UNKNOWN."""
    e = err_mod.from_failure_class("nonexistent", task_id="t1")
    assert e.code == err_mod.E_UNKNOWN
    assert e.task_id == "t1"


def test_from_state_not_errored():
    """from_state returns None for non-error states."""
    state = State(task_id="t1", status="COMPLETE")
    result = err_mod.from_state(state, "t1")
    assert result is None

    state = State(task_id="t1", status="DRAFTED")
    result = err_mod.from_state(state, "t1")
    assert result is None


def test_from_state_verification_failed():
    """from_state returns StructuredError for VERIFICATION_FAILED."""
    state = State(task_id="t1", status="VERIFICATION_FAILED")
    state.worker_results.append({"exit_code": 1, "stdout": "", "stderr": "error"})
    result = err_mod.from_state(state, "t1")
    assert result is not None
    assert result.code in (err_mod.E_WORKER_NONZERO_EXIT, err_mod.E_WORKER_EMPTY_OUTPUT, err_mod.E_UNKNOWN)
    assert result.task_id == "t1"


def test_format_error_list_empty():
    """format_error_list returns 'No errors' for empty list."""
    out = err_mod.format_error_list([])
    assert "No errors" in out


def test_format_error_list_nonempty():
    """format_error_list returns formatted output for non-empty list."""
    errors = [
        err_mod.StructuredError(err_mod.E_SCOPE_VIOLATION, "Bad file", task_id="t1"),
        err_mod.StructuredError(err_mod.E_LOCK_HELD, "Lock held", task_id="t2"),
    ]
    out = err_mod.format_error_list(errors)
    assert "2 error(s)" in out
    assert err_mod.E_SCOPE_VIOLATION in out
    assert err_mod.E_LOCK_HELD in out


def test_inspect_goal_clean(tmp_path):
    """inspect_goal returns empty list for clean execution."""
    ws_dir = str(tmp_path)
    run_dir = os.path.join(ws_dir, "scratch", "runs")
    goal = Goal(goal_id="err-clean", title="Clean", description="No errors")
    plan = generate_contracts(goal, workspace_root=ws_dir)
    supervisor = Supervisor(goal, plan, workspace_root=ws_dir, run_dir=run_dir)
    res = supervisor.execute_plan()
    assert all(s in ("COMPLETE", "complete") for s in res.values())

    errors = err_mod.inspect_goal(goal.goal_id, plan, ws_dir, run_dir)
    assert len(errors) == 0


def test_all_error_meta_defined():
    """Every error code has corresponding metadata."""
    for code, meta in err_mod.ERROR_META.items():
        assert "title" in meta
        assert "severity" in meta
        assert "component" in meta


def test_all_failure_classes_mapped():
    """Every known failure class maps to an error code."""
    from orchestrator.failure import (
        FAILURE_CLASS_TIMEOUT,
        FAILURE_CLASS_PREFLIGHT_MISSING_INPUT,
        FAILURE_CLASS_VERIFIER_OUTPUT_MISSING,
        FAILURE_CLASS_VERIFIER_CONTENT_MISMATCH,
        FAILURE_CLASS_WORKER_NONZERO_EXIT,
        FAILURE_CLASS_WORKER_EMPTY_OUTPUT,
        FAILURE_CLASS_CONTRACT_INVALID,
        FAILURE_CLASS_SCOPE_VIOLATION,
        FAILURE_CLASS_UNKNOWN,
    )
    classes = [
        FAILURE_CLASS_TIMEOUT,
        FAILURE_CLASS_PREFLIGHT_MISSING_INPUT,
        FAILURE_CLASS_VERIFIER_OUTPUT_MISSING,
        FAILURE_CLASS_VERIFIER_CONTENT_MISMATCH,
        FAILURE_CLASS_WORKER_NONZERO_EXIT,
        FAILURE_CLASS_WORKER_EMPTY_OUTPUT,
        FAILURE_CLASS_CONTRACT_INVALID,
        FAILURE_CLASS_SCOPE_VIOLATION,
        FAILURE_CLASS_UNKNOWN,
    ]
    for fc in classes:
        assert fc in err_mod.FAILURE_CLASS_TO_CODE, f"Missing mapping for {fc}"

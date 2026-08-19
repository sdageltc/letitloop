"""Tests for orchestrator safety module."""

import pytest

from orchestrator.contract import Contract
from orchestrator.goal import Plan
from orchestrator.limits import ResourceLimits
from orchestrator.safety import (
    SafetyIssue,
    SafetyReport,
    check_contract_validity,
    check_dependency_cycles,
    check_failsafe,
    check_resource_adequacy,
    check_workspace_health,
    format_safety_report,
    run_safety_checks,
)
from orchestrator.state import State

pytestmark = pytest.mark.fast


def _make_valid_contract(task_id):
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
            "objective": "safety test",
            "worker": {"model": "test", "max_attempts": 1},
            "inputs": [],
            "outputs": [{"path": f"scratch/{task_id}_out.txt"}],
            "acceptance_checks": [
                {"id": f"{task_id}-chk", "kind": "file_exists", "path": f"scratch/{task_id}_out.txt", "expected": True}
            ],
            "qc": {"required": False, "lens": "code_correctness"},
        },
    }


def test_check_contract_validity_clean():
    plan = Plan(goal_id="g1", contracts=[_make_valid_contract("t1"), _make_valid_contract("t2")])
    issues = check_contract_validity(plan)
    assert issues == []


def test_check_contract_validity_missing_field():
    invalid_c = {"task_id": "t1", "status": "DRAFTED"}
    plan = Plan(goal_id="g1", contracts=[invalid_c])
    issues = check_contract_validity(plan)
    assert len(issues) > 0
    assert any(i.issue_type == "missing_contract_field" for i in issues)


def test_check_dependency_cycles_clean():
    c1 = _make_valid_contract("t1")
    c2 = _make_valid_contract("t2")
    c2["depends_on"] = ["t1"]
    c3 = _make_valid_contract("t3")
    c3["depends_on"] = ["t2"]
    plan = Plan(goal_id="g1", contracts=[c1, c2, c3])
    issues = check_dependency_cycles(plan)
    assert issues == []


def test_check_dependency_cycles_detected():
    c1 = _make_valid_contract("A")
    c1["depends_on"] = ["B"]
    c2 = _make_valid_contract("B")
    c2["depends_on"] = ["C"]
    c3 = _make_valid_contract("C")
    c3["depends_on"] = ["A"]
    plan = Plan(goal_id="g1", contracts=[c1, c2, c3])
    issues = check_dependency_cycles(plan)
    assert len(issues) > 0
    assert any(i.issue_type == "dependency_cycle" for i in issues)


def test_check_resource_adequacy_ok():
    c1 = _make_valid_contract("t1")
    c2 = _make_valid_contract("t2")
    plan = Plan(goal_id="g1", contracts=[c1, c2])
    limits = ResourceLimits()
    issues = check_resource_adequacy(plan, limits)
    assert issues == []


def test_check_resource_adequacy_exceeded():
    c1 = _make_valid_contract("t1")
    c1["contract"]["worker"]["max_attempts"] = 4
    c2 = _make_valid_contract("t2")
    c2["contract"]["worker"]["max_attempts"] = 4
    plan = Plan(goal_id="g1", contracts=[c1, c2])
    limits = ResourceLimits(max_attempts_global=5)
    issues = check_resource_adequacy(plan, limits)
    assert len(issues) > 0
    assert any(i.issue_type == "resource_exceeded" for i in issues)


def test_check_workspace_health_ok(tmp_path):
    issues = check_workspace_health(str(tmp_path))
    assert issues == []


def test_check_workspace_health_missing(tmp_path):
    nonexistent = str(tmp_path / "does_not_exist_subfolder")
    issues = check_workspace_health(nonexistent)
    assert len(issues) > 0
    assert any(i.issue_type == "workspace_missing" for i in issues)


def test_check_failsafe_not_triggered():
    state = State(task_id="t1", status="WORKING", attempt=1)
    contract = Contract(_make_valid_contract("t1")["contract"])
    issue = check_failsafe(state, contract, goal_id="g1")
    assert issue is None


def test_check_failsafe_triggered():
    state = State(task_id="t1", status="WORKING", attempt=3)
    state.worker_results = [
        {"exit_code": 1, "failure_class": "worker_nonzero_exit"},
        {"exit_code": 1, "failure_class": "worker_nonzero_exit"},
        {"exit_code": 1, "failure_class": "worker_nonzero_exit"},
    ]
    contract = Contract(_make_valid_contract("t1")["contract"])
    contract.worker["max_attempts"] = 3
    issue = check_failsafe(state, contract, goal_id="g1")
    assert issue is not None
    assert issue.issue_type == "failsafe_triggered"


def test_run_safety_checks_clean(tmp_path):
    plan = Plan(goal_id="g1", contracts=[_make_valid_contract("t1")])
    report = run_safety_checks(plan, str(tmp_path))
    assert report.passed is True
    assert report.failed_checks == 0
    assert report.issues == []


def test_run_safety_checks_with_cycle(tmp_path):
    c1 = _make_valid_contract("A")
    c1["depends_on"] = ["B"]
    c2 = _make_valid_contract("B")
    c2["depends_on"] = ["A"]
    plan = Plan(goal_id="g1", contracts=[c1, c2])
    report = run_safety_checks(plan, str(tmp_path))
    assert report.passed is False
    assert any(i.issue_type == "dependency_cycle" for i in report.issues)


def test_format_safety_report_clean():
    report = SafetyReport(passed=True, issues=[], total_checks=3, failed_checks=0)
    formatted = format_safety_report(report)
    assert "PASSED" in formatted


def test_format_safety_report_with_issues():
    issue = SafetyIssue(issue_type="dependency_cycle", severity="error", message="Cycle detected")
    report = SafetyReport(passed=False, issues=[issue], total_checks=3, failed_checks=1)
    formatted = format_safety_report(report)
    assert "FAILED" in formatted


def test_safety_issue_to_dict():
    issue = SafetyIssue(
        issue_type="resource_exceeded",
        severity="warning",
        message="Too many attempts",
        task_id="t1",
        details={"limit": 5},
    )
    d = issue.to_dict()
    assert d["issue_type"] == "resource_exceeded"
    assert d["severity"] == "warning"
    assert d["message"] == "Too many attempts"
    assert d["task_id"] == "t1"
    assert d["details"] == {"limit": 5}

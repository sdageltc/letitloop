"""Tests for reconciliation module."""

import os
import json
import pytest
from orchestrator.goal import Goal
from orchestrator.generator import generate_contracts
from orchestrator.supervisor import Supervisor
from orchestrator import reconcile as rec


@pytest.fixture(autouse=True)
def set_fake_worker(monkeypatch):
    monkeypatch.setenv("FAKE_WORKER", "1")


def test_reconcile_passes_after_clean_execution(tmp_path):
    """Reconciliation has no issues after a successful execution."""
    ws_dir = str(tmp_path)
    run_dir = os.path.join(ws_dir, "scratch", "runs")
    goal = Goal(goal_id="recon-clean", title="Clean run", description="Test clean reconciliation")
    plan = generate_contracts(goal, workspace_root=ws_dir)
    supervisor = Supervisor(goal, plan, workspace_root=ws_dir, run_dir=run_dir)
    res = supervisor.execute_plan()
    assert all(s in ("COMPLETE", "complete") for s in res.values())

    report = rec.run_reconciliation(goal.goal_id, plan, ws_dir, run_dir)
    assert report.passed
    assert report.failed_tasks == 0
    assert report.total_tasks == len(plan.contracts)


def test_reconcile_detects_missing_output(tmp_path):
    """Reconciliation catches a deleted output file."""
    ws_dir = str(tmp_path)
    run_dir = os.path.join(ws_dir, "scratch", "runs")
    goal = Goal(goal_id="recon-missing", title="Missing output", description="Test missing output")
    plan = generate_contracts(goal, workspace_root=ws_dir)
    supervisor = Supervisor(goal, plan, workspace_root=ws_dir, run_dir=run_dir)
    res = supervisor.execute_plan()
    assert all(s in ("COMPLETE", "complete") for s in res.values())

    # Find and delete an output file
    ledger = rec.ev.load_ledger(run_dir)
    deleted = False
    for tid, entries in ledger.items():
        for e in entries:
            ap = e.get("absolute_path", "")
            if ap and os.path.isfile(ap):
                os.remove(ap)
                deleted = True
                break
        if deleted:
            break

    assert deleted, "no ledger entries to delete"

    report = rec.run_reconciliation(goal.goal_id, plan, ws_dir, run_dir)
    assert not report.passed
    assert report.failed_tasks > 0
    issue_types = [i.issue_type for i in report.issues]
    assert rec.RECONCILE_ISSUE_FILE_MISSING in issue_types or rec.RECONCILE_ISSUE_OUTPUT_MISSING in issue_types


def test_reconcile_detects_tampered_output(tmp_path):
    """Reconciliation catches a modified (hash-changed) output file."""
    ws_dir = str(tmp_path)
    run_dir = os.path.join(ws_dir, "scratch", "runs")
    goal = Goal(goal_id="recon-tamper", title="Tampered", description="Test hash mismatch")
    plan = generate_contracts(goal, workspace_root=ws_dir)
    supervisor = Supervisor(goal, plan, workspace_root=ws_dir, run_dir=run_dir)
    res = supervisor.execute_plan()
    assert all(s in ("COMPLETE", "complete") for s in res.values())

    # Find and modify an output file
    ledger = rec.ev.load_ledger(run_dir)
    modified = False
    for tid, entries in ledger.items():
        for e in entries:
            ap = e.get("absolute_path", "")
            if ap and os.path.isfile(ap) and os.path.getsize(ap) > 0:
                with open(ap, "a", encoding="utf-8") as f:
                    f.write("\n# TAMPERED")
                modified = True
                break
        if modified:
            break

    assert modified, "no ledger entries to tamper"

    report = rec.run_reconciliation(goal.goal_id, plan, ws_dir, run_dir)
    assert not report.passed
    assert any(i.issue_type == rec.RECONCILE_ISSUE_HASH_CHANGED for i in report.issues)


def test_reconcile_passes_with_no_state(tmp_path):
    """Reconciliation passes (no issues) when no tasks have been executed yet."""
    ws_dir = str(tmp_path)
    run_dir = os.path.join(ws_dir, "scratch", "runs")
    goal = Goal(goal_id="recon-fresh", title="Fresh", description="No execution yet")
    plan = generate_contracts(goal, workspace_root=ws_dir)

    report = rec.run_reconciliation(goal.goal_id, plan, ws_dir, run_dir)
    assert report.passed
    assert report.failed_tasks == 0
    assert report.checked_tasks == 0


def test_reconcile_detects_missing_ledger(tmp_path):
    """Reconciliation catches a COMPLETE task with no ledger entries."""
    ws_dir = str(tmp_path)
    run_dir = os.path.join(ws_dir, "scratch", "runs")
    goal = Goal(goal_id="recon-no-ledger", title="No ledger", description="Test missing ledger")
    plan = generate_contracts(goal, workspace_root=ws_dir)
    supervisor = Supervisor(goal, plan, workspace_root=ws_dir, run_dir=run_dir)
    res = supervisor.execute_plan()
    assert all(s in ("COMPLETE", "complete") for s in res.values())

    # Delete the ledger file
    ledger_path = os.path.join(run_dir, "evidence_ledger.json")
    if os.path.isfile(ledger_path):
        os.remove(ledger_path)

    report = rec.run_reconciliation(goal.goal_id, plan, ws_dir, run_dir)
    assert not report.passed
    assert any(i.issue_type == rec.RECONCILE_ISSUE_LEDGER_MISSING for i in report.issues)


def test_reconcile_report_to_dict(tmp_path):
    """ReconciliationReport serializes correctly."""
    ws_dir = str(tmp_path)
    run_dir = os.path.join(ws_dir, "scratch", "runs")
    goal = Goal(goal_id="recon-serialize", title="Serialize", description="Test to_dict")
    plan = generate_contracts(goal, workspace_root=ws_dir)

    report = rec.run_reconciliation(goal.goal_id, plan, ws_dir, run_dir)
    d = report.to_dict()
    assert d["goal_id"] == "recon-serialize"
    assert "passed" in d
    assert "total_tasks" in d
    assert "checked_tasks" in d
    assert "failed_tasks" in d
    assert "issues" in d


def test_format_report(tmp_path):
    """format_report returns non-empty string for both pass and fail."""
    ws_dir = str(tmp_path)
    run_dir = os.path.join(ws_dir, "scratch", "runs")
    goal = Goal(goal_id="recon-format", title="Format", description="Test format")
    plan = generate_contracts(goal, workspace_root=ws_dir)

    report = rec.run_reconciliation(goal.goal_id, plan, ws_dir, run_dir)
    output = rec.format_report(report)
    assert isinstance(output, str)
    assert len(output) > 0
    if report.passed:
        assert "PASSED" in output
    else:
        assert "FAILED" in output


def test_reconciliation_issue_to_dict():
    """ReconciliationIssue serializes correctly."""
    issue = rec.ReconciliationIssue(
        task_id="test-task",
        path="output.txt",
        issue_type=rec.RECONCILE_ISSUE_HASH_CHANGED,
        expected="abc123",
        actual="def456",
    )
    d = issue.to_dict()
    assert d["task_id"] == "test-task"
    assert d["path"] == "output.txt"
    assert d["issue_type"] == rec.RECONCILE_ISSUE_HASH_CHANGED
    assert d["expected"] == "abc123"
    assert d["actual"] == "def456"

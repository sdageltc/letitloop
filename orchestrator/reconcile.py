"""Reconciliation — compares expected vs actual state for resume integrity."""

import os
import json
from typing import Dict, List, Any, Optional
from .goal import Plan
from . import evidence as ev


RECONCILE_ISSUE_OUTPUT_MISSING = "output_missing"
RECONCILE_ISSUE_FILE_MISSING = "file_missing"
RECONCILE_ISSUE_HASH_CHANGED = "hash_changed"
RECONCILE_ISSUE_LEDGER_MISSING = "ledger_missing"


class ReconciliationIssue:
    """A single reconciliation issue — file missing, hash changed, etc."""

    def __init__(self, task_id: str, path: str, issue_type: str, expected: str = "", actual: str = ""):
        self.task_id = task_id
        self.path = path
        self.issue_type = issue_type
        self.expected = expected
        self.actual = actual

    def to_dict(self) -> Dict[str, str]:
        return {
            "task_id": self.task_id,
            "path": self.path,
            "issue_type": self.issue_type,
            "expected": self.expected,
            "actual": self.actual,
        }

    def __repr__(self) -> str:
        return f"[{self.issue_type}] {self.task_id}:{self.path}"


class ReconciliationReport:
    """Full reconciliation result for a goal plan."""

    def __init__(self, goal_id: str, passed: bool, issues: List[ReconciliationIssue],
                 total_tasks: int, checked_tasks: int):
        self.goal_id = goal_id
        self.passed = passed
        self.issues = issues
        self.total_tasks = total_tasks
        self.checked_tasks = checked_tasks
        self.failed_tasks = len(issues)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "goal_id": self.goal_id,
            "passed": self.passed,
            "total_tasks": self.total_tasks,
            "checked_tasks": self.checked_tasks,
            "failed_tasks": self.failed_tasks,
            "issues": [i.to_dict() for i in self.issues],
        }

    def __repr__(self) -> str:
        return f"<ReconciliationReport goal={self.goal_id} passed={self.passed} issues={self.failed_tasks}>"


def run_reconciliation(goal_id: str, plan: Plan, workspace_root: str, run_dir: str) -> ReconciliationReport:
    """Compare expected vs actual state for all completed tasks in a plan.

    Checks:
    1. Output integrity — every output path from completed contracts exists
    2. Ledger consistency — every ledger entry's file exists and hash matches
    3. Ledger completeness — every COMPLETE task has ledger entries
    """
    issues = []

    completed_count = 0
    for c in plan.contracts:
        task_id = c["task_id"]
        state_file = os.path.join(run_dir, task_id, "state.json")
        if not os.path.isfile(state_file):
            continue

        from .state import load_state
        state = load_state(state_file)
        if state.status.upper() not in (
            "COMPLETE", "VERIFIED", "DEGRADED_PASS", "FORCE_COMPLETE",
        ):
            continue

        contract_file = os.path.join(run_dir, task_id, "contract.json")
        if os.path.isfile(contract_file):
            with open(contract_file, "r", encoding="utf-8") as f:
                contract_dict = json.load(f)
            outputs = contract_dict.get("outputs", [])
        else:
            outputs = []

        for out in outputs:
            out_path = out.get("path", "") if isinstance(out, dict) else ""
            if not out_path:
                continue
            abs_path = os.path.join(workspace_root, out_path) if not os.path.isabs(out_path) else out_path
            if not os.path.exists(abs_path):
                issues.append(ReconciliationIssue(
                    task_id=task_id,
                    path=out_path,
                    issue_type=RECONCILE_ISSUE_OUTPUT_MISSING,
                    expected="file_exists",
                    actual="not_found",
                ))

        completed_count += 1

    ledger = ev.load_ledger(run_dir)
    for task_id, entries in ledger.items():
        for e in entries:
            abs_path = e.get("absolute_path", "")
            if not abs_path:
                continue
            if not os.path.exists(abs_path):
                issues.append(ReconciliationIssue(
                    task_id=task_id,
                    path=e.get("relative_path", "?"),
                    issue_type=RECONCILE_ISSUE_FILE_MISSING,
                    expected="file_exists",
                    actual="not_found",
                ))
                continue
            stored_hash = e.get("sha256", "")
            if stored_hash:
                current_hash = ev._sha256(abs_path)
                if stored_hash != current_hash:
                    issues.append(ReconciliationIssue(
                        task_id=task_id,
                        path=e.get("relative_path", "?"),
                        issue_type=RECONCILE_ISSUE_HASH_CHANGED,
                        expected=stored_hash,
                        actual=current_hash,
                    ))

    for c in plan.contracts:
        task_id = c["task_id"]
        state_file = os.path.join(run_dir, task_id, "state.json")
        if os.path.isfile(state_file):
            from .state import load_state
            state = load_state(state_file)
            if state.status.upper() == "COMPLETE" and task_id not in ledger:
                issues.append(ReconciliationIssue(
                    task_id=task_id,
                    path="",
                    issue_type=RECONCILE_ISSUE_LEDGER_MISSING,
                    expected="ledger_entries",
                    actual="no_entries",
                ))

    passed = len(issues) == 0
    return ReconciliationReport(
        goal_id=goal_id,
        passed=passed,
        issues=issues,
        total_tasks=len(plan.contracts),
        checked_tasks=completed_count,
    )


def format_report(report: ReconciliationReport) -> str:
    """Format a reconciliation report as human-readable string."""
    if report.passed:
        return (f"Reconciliation PASSED for {report.goal_id} "
                f"({report.checked_tasks}/{report.total_tasks} tasks checked, no issues).")
    lines = [f"Reconciliation FAILED for {report.goal_id} — {report.failed_tasks} issue(s):"]
    for iss in report.issues:
        lines.append(f"  [{iss.issue_type}] {iss.task_id}: {iss.path or '(no path)'}")
        if iss.expected or iss.actual:
            lines.append(f"    expected: {iss.expected}, actual: {iss.actual}")
    return "\n".join(lines)

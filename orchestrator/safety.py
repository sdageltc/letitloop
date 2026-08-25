"""Pre-execution safety checks and failsafe mechanisms."""

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from .failure import MAX_SAME_CLASS_STRIKES, count_consecutive_same_class
from .goal import Plan
from .limits import ResourceLimits


@dataclass
class SafetyIssue:
    issue_type: str
    severity: str
    message: str
    task_id: str = ""
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "issue_type": self.issue_type,
            "severity": self.severity,
            "message": self.message,
            "task_id": self.task_id,
            "details": self.details,
        }


@dataclass
class SafetyReport:
    passed: bool
    issues: List[SafetyIssue] = field(default_factory=list)
    total_checks: int = 0
    failed_checks: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "issues": [i.to_dict() for i in self.issues],
            "total_checks": self.total_checks,
            "failed_checks": self.failed_checks,
        }


def check_contract_validity(plan: Plan) -> List[SafetyIssue]:
    """Check that all contracts have required fields."""
    issues = []
    required_fields = {"task_id", "status", "contract"}
    contract_required = {"title", "objective", "worker", "outputs", "acceptance_checks"}

    def _as_dict(c):
        return c if isinstance(c, dict) else c._raw if hasattr(c, "_raw") else {}

    for c in plan.contracts:
        c = _as_dict(c)
        tid = c.get("task_id", "?")
        missing = required_fields - set(c.keys())
        if missing:
            issues.append(
                SafetyIssue(
                    issue_type="missing_contract_field",
                    severity="error",
                    message=f"contract {tid} missing fields: {missing}",
                    task_id=tid,
                )
            )

        contract = c.get("contract", {})
        if contract:
            missing_contract = contract_required - set(contract.keys())
            if missing_contract:
                issues.append(
                    SafetyIssue(
                        issue_type="missing_contract_detail",
                        severity="error",
                        message=f"contract {tid} missing contract detail fields: {missing_contract}",
                        task_id=tid,
                    )
                )

    return issues


def check_dependency_cycles(plan: Plan) -> List[SafetyIssue]:
    """Detect cycles in task dependency graph."""
    issues = []
    adj: Dict[str, List[str]] = {}

    def _as_dict(c):
        return c if isinstance(c, dict) else c._raw if hasattr(c, "_raw") else {}

    for c in plan.contracts:
        c = _as_dict(c)
        tid = c.get("task_id", "?")
        deps = c.get("depends_on", [])
        adj[tid] = deps

    visited: Set[str] = set()
    rec_stack: Set[str] = set()

    def _dfs(node: str) -> bool:
        visited.add(node)
        rec_stack.add(node)
        for dep in adj.get(node, []):
            if dep not in visited:
                if _dfs(dep):
                    return True
            elif dep in rec_stack:
                issues.append(
                    SafetyIssue(
                        issue_type="dependency_cycle",
                        severity="error",
                        message=f"dependency cycle detected involving {node} -> {dep}",
                        task_id=node,
                        details={"cycle_node": dep},
                    )
                )
                return True
        rec_stack.discard(node)
        return False

    for tid in adj:
        if tid not in visited:
            _dfs(tid)

    return issues


def check_resource_adequacy(plan: Plan, limits: ResourceLimits) -> List[SafetyIssue]:
    """Check that plan-level resource demands don't exceed limits."""
    issues = []

    def _as_dict(c):
        return c if isinstance(c, dict) else c._raw if hasattr(c, "_raw") else {}

    total_attempts = sum(
        cc.get("contract", {}).get("worker", {}).get("max_attempts", 1) if isinstance(cc.get("contract"), dict) else 1
        for cc in (_as_dict(c) for c in plan.contracts)
    )
    len(plan.contracts)

    if total_attempts > limits.max_attempts_global:
        issues.append(
            SafetyIssue(
                issue_type="resource_exceeded",
                severity="warning",
                message=f"total max attempts ({total_attempts}) exceed global limit ({limits.max_attempts_global})",
                details={"total_attempts": total_attempts, "limit": limits.max_attempts_global},
            )
        )

    return issues


def check_workspace_health(workspace_root: str) -> List[SafetyIssue]:
    """Verify workspace directories exist and are writable."""
    issues = []
    if not os.path.isdir(workspace_root):
        issues.append(
            SafetyIssue(
                issue_type="workspace_missing",
                severity="error",
                message=f"workspace root does not exist: {workspace_root}",
            )
        )
        return issues

    test_path = os.path.join(workspace_root, ".safety_test")
    try:
        with open(test_path, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(test_path)
    except OSError as e:
        issues.append(
            SafetyIssue(
                issue_type="workspace_not_writable",
                severity="error",
                message=f"workspace root not writable: {e}",
                details={"error": str(e)},
            )
        )

    return issues


def check_failsafe(state, contract, goal_id: str) -> Optional[SafetyIssue]:
    """Check if a task should be failsafe-triggered based on failure patterns."""
    from .failure import classify_failure

    fclass = classify_failure(state, contract)
    strikes = count_consecutive_same_class(state, fclass)

    if strikes >= MAX_SAME_CLASS_STRIKES:
        if state.attempt >= contract.worker.get("max_attempts", 3):
            return SafetyIssue(
                issue_type="failsafe_triggered",
                severity="error",
                message=f"task {state.task_id}: {strikes} consecutive '{fclass}' failures, max attempts reached",
                task_id=state.task_id,
                details={
                    "failure_class": fclass,
                    "consecutive_strikes": strikes,
                    "max_attempts": contract.worker.get("max_attempts", 3),
                    "goal_id": goal_id,
                },
            )

    return None


def run_safety_checks(
    plan: Plan,
    workspace_root: str,
    limits: Optional[ResourceLimits] = None,
) -> SafetyReport:
    """Run all pre-execution safety checks. Returns a SafetyReport."""
    issues: List[SafetyIssue] = []
    total = 0
    failed = 0

    checkers = [
        ("contract_validity", lambda: check_contract_validity(plan)),
        ("dependency_cycles", lambda: check_dependency_cycles(plan)),
        ("workspace_health", lambda: check_workspace_health(workspace_root)),
    ]

    if limits:
        checkers.append(("resource_adequacy", lambda: check_resource_adequacy(plan, limits)))

    for name, checker in checkers:
        total += 1
        try:
            result = checker()
            issues.extend(result)
            if result:
                failed += 1
        except Exception as e:
            issues.append(
                SafetyIssue(
                    issue_type="check_crashed",
                    severity="error",
                    message=f"safety check '{name}' crashed: {type(e).__name__}: {e}",
                )
            )
            failed += 1

    critical = any(i.severity == "error" for i in issues)
    return SafetyReport(
        passed=not critical,
        issues=issues,
        total_checks=total,
        failed_checks=failed,
    )


def format_safety_report(report: SafetyReport) -> str:
    """Return a human-readable safety report string."""
    if not report.issues:
        return f"Safety: PASSED ({report.total_checks} checks, 0 issues)"

    lines = [
        f"Safety: {'FAILED' if not report.passed else 'PASSED (with warnings)'}",
        f"Checks: {report.total_checks} total, {report.failed_checks} with issues",
        f"Issues: {len(report.issues)}",
    ]
    for iss in report.issues:
        icon = "ERROR" if iss.severity == "error" else "WARN"
        tid = f" [{iss.task_id}]" if iss.task_id else ""
        lines.append(f"  [{icon}]{tid} {iss.issue_type}: {iss.message}")
    return "\n".join(lines)


# --- Folded from approval.py ---
"""Approval classifier — decides whether a plan needs user approval."""

from typing import Any, Dict, Optional

from .goal import Plan

DESTRUCTIVE_KEYWORDS = [
    "delete",
    "remove",
    "rm ",
    "drop",
    "truncate",
    "reset",
    "clear",
    "purge",
    "destroy",
]

MACRO_TRIGGERS = [
    "refactor",
    "migrate",
    "rewrite",
    "convert",
    "architecture",
    "redesign",
    "restructure",
]


def _get_plan_stats(plan: Plan) -> Dict[str, Any]:
    """Aggregate statistics about a plan."""
    total = len(plan.contracts)
    touches_src = 0
    touches_tests = 0
    touches_config = 0
    touches_scratch = 0
    has_commands = False
    has_syntax_check = False
    has_destructive = False
    has_macro_keyword = False
    for c in plan.contracts:
        contract_dict = c.get("contract", {}) or {}
        outputs = contract_dict.get("outputs", c.get("outputs", []))
        for out in outputs:
            p = out.get("path", "") if isinstance(out, dict) else str(out)
            if p.startswith("src/"):
                touches_src += 1
            if p.startswith("tests/"):
                touches_tests += 1
            if any(p.startswith(x) for x in (".opencode/", ".claude/", ".agents/", "memory/", "AGENTS.md")):
                touches_config += 1
            if p.startswith("scratch/"):
                touches_scratch += 1
        checks = contract_dict.get("acceptance_checks", c.get("acceptance_checks", []))
        for ch in checks:
            if ch.get("kind") == "command":
                has_commands = True
            if ch.get("kind") == "syntax":
                has_syntax_check = True
        objective = (contract_dict.get("objective", c.get("objective", "")) or "").lower()
        if any(kw in objective for kw in DESTRUCTIVE_KEYWORDS):
            has_destructive = True
        if any(kw in objective for kw in MACRO_TRIGGERS):
            has_macro_keyword = True
    return {
        "total": total,
        "touches_src": touches_src,
        "touches_tests": touches_tests,
        "touches_config": touches_config,
        "touches_scratch": touches_scratch,
        "has_commands": has_commands,
        "has_syntax_check": has_syntax_check,
        "has_destructive": has_destructive,
        "has_macro_keyword": has_macro_keyword,
    }


def requires_approval(
    plan: Plan,
    prefs: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Decide if a plan needs user approval.

    Returns dict with:
      - requires_approval: bool
      - reasons: list[str]
    """
    stats = _get_plan_stats(plan)
    reasons = []
    planning_prefs = (prefs or {}).get("planning", {})

    # Rule: macro keyword in objective
    if stats["has_macro_keyword"]:
        reasons.append("Plan contains architecture/refactor keywords")

    # Rule: destructive action
    if stats["has_destructive"]:
        reasons.append("Plan contains destructive operations")

    # Rule: touches config paths
    if stats["touches_config"] > 0:
        reasons.append("Plan modifies config or protected files")

    # Rule: touches src/ and approval_required_for_src_changes
    if stats["touches_src"] > 0 and planning_prefs.get("approval_required_for_src_changes", True):
        reasons.append("Plan modifies source code under src/")

    # Rule: touches tests/
    if stats["touches_tests"] > 0:
        reasons.append("Plan writes test files under tests/")

    # Rule: multi-file and approval_required_for_multi_file
    total_outputs = stats["touches_src"] + stats["touches_tests"] + stats["touches_scratch"]
    if total_outputs > 1 and planning_prefs.get("approval_required_for_multi_file", True):
        reasons.append("Plan writes multiple files")

    # Rule: more contracts than threshold
    max_ok = planning_prefs.get("max_contracts_before_approval", 2)
    if stats["total"] > max_ok:
        reasons.append(f"Plan has {stats['total']} steps (limit: {max_ok})")

    # Rule: has shell commands
    if stats["has_commands"]:
        reasons.append("Plan executes shell commands")

    return {
        "requires_approval": len(reasons) > 0,
        "reasons": reasons,
    }


def format_approval_reasons(result: Dict[str, Any]) -> str:
    """Format approval reasons as a block string."""
    if not result["requires_approval"]:
        return "Approval not required."
    lines = ["Approval required because:"]
    for r in result["reasons"]:
        lines.append(f"- {r}")
    return "\n".join(lines)

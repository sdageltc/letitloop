"""Approval classifier — decides whether a plan needs user approval."""

from typing import Dict, Any, List, Optional
from .goal import Plan

DESTRUCTIVE_KEYWORDS = [
    "delete", "remove", "rm ", "drop", "truncate",
    "reset", "clear", "purge", "destroy",
]

MACRO_TRIGGERS = [
    "refactor", "migrate", "rewrite", "convert",
    "architecture", "redesign", "restructure",
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

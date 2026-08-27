"""Deterministic Policy Gatekeeper — lil gate (Sprint 6).

Validates agent actions against declarative policy files (letitloop.policy.json):
  - AST Modification Limits: max lines changed, forbidden files (CI workflows, security keys)
  - Token Budget Ceilings: strict cost cap per goal execution
  - Path Jailing: blocks directory traversal (.., symlink swaps)

Zero heavy deps: stdlib only (dataclasses, json, pathlib, re).
"""

from __future__ import annotations

import dataclasses
import json
import pathlib
import re
from typing import Any, Dict, List

from .scrubber import scrub_text


class BudgetExceededError(RuntimeError):
    """Raised when token budget ceiling is exceeded."""


class PolicyViolationError(RuntimeError):
    """Raised when a policy rule is violated (fail-closed)."""


@dataclasses.dataclass
class Policy:
    max_lines_changed: int | None = None
    forbidden_files: List[str] = dataclasses.field(default_factory=list)
    forbidden_patterns: List[str] = dataclasses.field(default_factory=list)
    token_budget: int | None = None
    allowed_paths: List[str] | None = None  # if set, only these prefixes allowed
    blocked_paths: List[str] = dataclasses.field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Policy":
        return cls(
            max_lines_changed=data.get("max_lines_changed"),
            forbidden_files=data.get("forbidden_files", []),
            forbidden_patterns=data.get("forbidden_patterns", []),
            token_budget=data.get("token_budget"),
            allowed_paths=data.get("allowed_paths"),
            blocked_paths=data.get("blocked_paths", []),
        )

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


DEFAULT_POLICY = Policy(
    max_lines_changed=500,
    forbidden_files=[
        ".github/workflows/ci.yml",
        ".github/workflows/letitloop-verify.yml",
        ".github/workflows/benchmark.yml",
    ],
    forbidden_patterns=[
        r"\.github/workflows/.*\.yml",
        r".*\.pem$",
        r".*\.key$",
        r".*id_rsa.*",
    ],
    token_budget=50000,
    blocked_paths=["..", ".git/"],
)


def load_policy(path: str | pathlib.Path = "letitloop.policy.json") -> Policy:
    p = pathlib.Path(path)
    if not p.is_file():
        return DEFAULT_POLICY
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return Policy.from_dict(data)
    except Exception:
        return DEFAULT_POLICY


def check_path_jailing(file_path: str, policy: Policy | None = None) -> List[str]:
    """Check path jailing (blocks .., symlink swaps, blocked_paths). Returns violations."""
    policy = policy or DEFAULT_POLICY
    violations: List[str] = []
    # Directory traversal
    parts = pathlib.PurePath(file_path).parts
    if ".." in parts:
        violations.append(f"path_jail: {file_path!r} contains '..' traversal")
    # Blocked prefixes
    for blocked in policy.blocked_paths:
        if file_path.startswith(blocked) or f"/{blocked}" in file_path:
            violations.append(f"path_jail: {file_path!r} matches blocked {blocked!r}")
    # Allowed paths (if set, must match one)
    if policy.allowed_paths is not None:
        allowed = any(file_path.startswith(prefix) or file_path == prefix for prefix in policy.allowed_paths)
        if not allowed:
            violations.append(f"path_jail: {file_path!r} not in allowed_paths {policy.allowed_paths}")
    # Symlink check (best-effort: if path exists and is symlink outside)
    try:
        fp = pathlib.Path(file_path)
        if fp.is_symlink():
            target = fp.resolve()
            # For now, any symlink is flagged as potential swap (conservative)
            violations.append(f"path_jail: {file_path!r} is symlink -> {target}")
    except OSError:
        pass
    return violations


def check_forbidden_file(file_path: str, policy: Policy | None = None) -> List[str]:
    """Check if file_path matches forbidden_files or patterns."""
    policy = policy or DEFAULT_POLICY
    violations: List[str] = []
    if file_path in policy.forbidden_files:
        violations.append(f"forbidden_file: {file_path!r} is in forbidden_files")
    for pat in policy.forbidden_patterns:
        try:
            if re.fullmatch(pat, file_path) or re.search(pat, file_path):
                violations.append(f"forbidden_pattern: {file_path!r} matches {pat!r}")
                break
        except re.error:
            continue
    return violations


def check_ast_limits(diff_text: str, policy: Policy | None = None) -> List[str]:
    """Check AST modification limits (max_lines_changed)."""
    policy = policy or DEFAULT_POLICY
    violations: List[str] = []
    if policy.max_lines_changed is not None:
        # Count added/removed lines in diff (lines starting with + or - but not +++ or ---)
        lines = diff_text.splitlines()
        changed = sum(
            1
            for line in lines
            if (line.startswith("+") and not line.startswith("+++"))
            or (line.startswith("-") and not line.startswith("---"))
        )
        if changed > policy.max_lines_changed:
            violations.append(f"ast_limit: {changed} lines changed exceeds max {policy.max_lines_changed}")
    return violations


def check_token_budget(tokens_used: int, policy: Policy | None = None) -> List[str]:
    policy = policy or DEFAULT_POLICY
    if policy.token_budget is not None and tokens_used > policy.token_budget:
        return [f"token_budget: {tokens_used} exceeds {policy.token_budget}"]
    return []


def evaluate_policy(
    file_path: str | None = None,
    diff_text: str | None = None,
    tokens_used: int | None = None,
    policy: Policy | None = None,
) -> Dict[str, Any]:
    """Evaluate all policy rules. Returns {allowed, violations, scrubbed_diff}."""
    policy = policy or load_policy()
    violations: List[str] = []

    if file_path:
        violations.extend(check_path_jailing(file_path, policy))
        violations.extend(check_forbidden_file(file_path, policy))

    if diff_text is not None:
        violations.extend(check_ast_limits(diff_text, policy))
        # Scrub secrets in diff before persisting (side-effect: return scrubbed)
        scrubbed = scrub_text(diff_text)
    else:
        scrubbed = None

    if tokens_used is not None:
        violations.extend(check_token_budget(tokens_used, policy))
        if violations and any("token_budget" in v for v in violations):
            raise BudgetExceededError(violations[-1])

    return {"allowed": len(violations) == 0, "violations": violations, "scrubbed_diff": scrubbed}


def gate_check(
    file_paths: List[str] | None = None,
    diff_text: str | None = None,
    tokens_used: int | None = None,
    policy_path: str | pathlib.Path = "letitloop.policy.json",
) -> Dict[str, Any]:
    """Fail-closed CI gate: evaluate current branch against security invariants.

    Returns {passed, violations, policy}. Exits 0 on PASS, 1 on policy violation (for lil gate --check).
    """
    policy = load_policy(policy_path)
    violations: List[str] = []

    if file_paths:
        for fp in file_paths:
            result = evaluate_policy(file_path=fp, policy=policy)
            violations.extend(result["violations"])

    if diff_text is not None:
        result = evaluate_policy(diff_text=diff_text, policy=policy)
        violations.extend(result["violations"])

    if tokens_used is not None:
        try:
            result = evaluate_policy(tokens_used=tokens_used, policy=policy)
            violations.extend(result["violations"])
        except BudgetExceededError as e:
            violations.append(str(e))

    passed = len(violations) == 0
    return {"passed": passed, "violations": violations, "policy": policy.to_dict()}


def format_gate_report(report: Dict[str, Any]) -> str:
    if report["passed"]:
        return "PASS Gate PASS - all policy checks passed"
    lines = ["FAIL Gate FAIL-CLOSED - policy violations:"]
    for v in report["violations"]:
        lines.append(f"  - {v}")
    return "\n".join(lines)

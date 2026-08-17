"""Plan quality validation — catches low-quality or dangerous plan patterns."""

import os
import re
from typing import Dict, List, Any, Optional
from .goal import Plan


PERMISSIVE_PATTERNS = [
    r"\.\*",        # regex match-all .*
    r"^\.\+$",      # regex non-empty match-all .+
    r"^\.\+",       # regex non-empty match-all .+ (without anchors)
    r"True",        # bare True as expected for file_exists (should be bool)
    r"^$",          # empty expected string
]


class PlanQualityWarning:
    """A single quality warning with severity."""

    def __init__(self, message: str, severity: str = "warning", task_id: str = ""):
        self.message = message
        self.severity = severity  # error or warning
        self.task_id = task_id

    def to_dict(self) -> Dict[str, str]:
        return {"message": self.message, "severity": self.severity, "task_id": self.task_id}

    def __repr__(self) -> str:
        return f"[{self.severity.upper()}] {self.task_id}: {self.message}"


def check_plan_quality(plan: Plan, workspace_root: str = "") -> List[PlanQualityWarning]:
    """Run all quality checks on a Plan. Returns list of warnings (empty = clean)."""
    warnings = []
    task_ids = set()

    for c in plan.contracts:
        tid = c.get("task_id", "")
        if not tid:
            warnings.append(PlanQualityWarning("contract missing task_id", severity="error"))
            continue

        # 1. Duplicate task_id check
        if tid in task_ids:
            warnings.append(PlanQualityWarning(f"duplicate task_id: {tid}", severity="error", task_id=tid))
        task_ids.add(tid)

        # 3. Permissive acceptance checks
        contract_dict = c.get("contract", {}) or {}
        checks = contract_dict.get("acceptance_checks", c.get("acceptance_checks", []))
        if not checks:
            warnings.append(PlanQualityWarning(
                "contract has no acceptance checks", severity="warning", task_id=tid,
            ))
        for check in checks:
            expected = check.get("expected", "")
            if isinstance(expected, str) and any(re.search(p, expected) for p in PERMISSIVE_PATTERNS):
                warnings.append(PlanQualityWarning(
                    f"permissive acceptance check: {check.get('id', '?')} uses '{expected}'",
                    severity="warning", task_id=tid,
                ))

        # 4. Missing outputs
        outputs = contract_dict.get("outputs", c.get("outputs", []))
        if not outputs:
            warnings.append(PlanQualityWarning(
                "contract has no outputs defined", severity="error", task_id=tid,
            ))

        # 5. Scope check — output paths outside allowed scope
        scope = contract_dict.get("workspace_scope", c.get("workspace_scope", {}))
        allow_paths = scope.get("allow", [])
        if allow_paths:
            for out in outputs:
                out_path = out.get("path", "") if isinstance(out, dict) else ""
                if out_path and allow_paths:
                    allowed = any(
                        out_path.startswith(a.rstrip("/\\") + "/") or
                        out_path.startswith(a.rstrip("/\\") + "\\") or
                        out_path == a
                        for a in allow_paths
                    )
                    if not allowed:
                        warnings.append(PlanQualityWarning(
                            f"output '{out_path}' not in allowed paths {allow_paths}",
                            severity="error", task_id=tid,
                        ))

        # 5b. Empty or generic objective
        objective = contract_dict.get("objective", c.get("objective", ""))
        if not objective or not objective.strip():
            warnings.append(PlanQualityWarning(
                "contract has empty objective", severity="error", task_id=tid,
            ))
        generic_objectives = ["test", "implement", "implement feature", "do task", "execute"]
        if objective.strip().lower() in generic_objectives:
            warnings.append(PlanQualityWarning(
                f"generic objective: '{objective.strip()}'", severity="warning", task_id=tid,
            ))

    # 6. Check dependency references exist
    all_ids = set(c.get("task_id", "") for c in plan.contracts)
    for c in plan.contracts:
        tid = c.get("task_id", "")
        for dep in c.get("depends_on", []):
            if dep not in all_ids:
                warnings.append(PlanQualityWarning(
                    f"dependency '{dep}' does not exist in plan", severity="error", task_id=tid,
                ))
            if dep == tid:
                warnings.append(PlanQualityWarning(
                    f"contract depends on itself: {tid}", severity="error", task_id=tid,
                ))

    # 7. Maximum contracts check
    if len(plan.contracts) > 5:
        warnings.append(PlanQualityWarning(
            f"plan has {len(plan.contracts)} contracts (max recommended: 5)",
            severity="warning",
        ))

    # 8. Reject contracts where ALL checks are weak (.+ or .* regex only)
    for c in plan.contracts:
        tid = c.get("task_id", "")
        contract_dict = c.get("contract", {}) or {}
        checks = contract_dict.get("acceptance_checks", c.get("acceptance_checks", []))
        outputs = contract_dict.get("outputs", c.get("outputs", []))
        if checks and outputs:
            weak_kinds = {"content_regex", "file_exists", "min_size"}
            all_weak = all(
                ch.get("kind") in weak_kinds and ch.get("expected", "") in (".+", ".*", True, 1)
                for ch in checks
            )
            is_scratch = all(
                out.get("path", "").startswith("scratch/") for out in outputs
            )
            if all_weak and not is_scratch:
                warnings.append(PlanQualityWarning(
                    f"all acceptance checks are weak for non-scratch outputs",
                    severity="error", task_id=tid,
                ))

    # 9. QC_required but no quality_spec
    for c in plan.contracts:
        tid = c.get("task_id", "")
        contract_dict = c.get("contract", {}) or {}
        qc = contract_dict.get("qc", c.get("qc", {}))
        qs = contract_dict.get("quality_spec", c.get("quality_spec", {}))
        if qc.get("required") and not qs:
            outputs = contract_dict.get("outputs", c.get("outputs", []))
            is_scratch = all(
                o.get("path", "").replace("\\", "/").startswith("scratch/")
                for o in outputs
            )
            severity = "warning" if is_scratch else "error"
            warnings.append(PlanQualityWarning(
                "QC required but no quality_spec defined",
                severity=severity, task_id=tid,
            ))

    # 10. Empty acceptance checks on non-trivial tasks
    for c in plan.contracts:
        tid = c.get("task_id", "")
        contract_dict = c.get("contract", {}) or {}
        checks = contract_dict.get("acceptance_checks", c.get("acceptance_checks", []))
        if not checks:
            outputs = contract_dict.get("outputs", c.get("outputs", []))
            is_scratch = all(
                o.get("path", "").replace("\\", "/").startswith("scratch/")
                for o in outputs
            )
            if not is_scratch:
                warnings.append(PlanQualityWarning(
                    "no acceptance checks for non-scratch task",
                    severity="error", task_id=tid,
                ))

    # 11. Check paths that don't match any output path
    for c in plan.contracts:
        tid = c.get("task_id", "")
        contract_dict = c.get("contract", {}) or {}
        checks = contract_dict.get("acceptance_checks", c.get("acceptance_checks", []))
        outputs = contract_dict.get("outputs", c.get("outputs", []))
        output_paths = {o.get("path", "") for o in outputs}
        for check in checks:
            path = check.get("path", "")
            if path and path not in output_paths and check.get("kind") not in ("command",):
                warnings.append(PlanQualityWarning(
                    f"check '{check.get('id', '?')}' path '{path}' does not match any output",
                    severity="warning", task_id=tid,
                ))

    # 12. Suspicious check parameters
    for c in plan.contracts:
        tid = c.get("task_id", "")
        contract_dict = c.get("contract", {}) or {}
        checks = contract_dict.get("acceptance_checks", c.get("acceptance_checks", []))
        for check in checks:
            kind = check.get("kind", "")
            if kind == "min_size":
                expected = check.get("expected", 0)
                if isinstance(expected, (int, float)) and expected <= 0:
                    warnings.append(PlanQualityWarning(
                        f"min_size expected={expected} is not positive",
                        severity="error", task_id=tid,
                    ))
            elif kind == "command":
                cmd = check.get("command", check.get("expected", ""))
                if not cmd:
                    warnings.append(PlanQualityWarning(
                        f"command check has empty command",
                        severity="error", task_id=tid,
                    ))
            elif kind == "syntax":
                path = check.get("path", "")
                if path:
                    ext = os.path.splitext(path)[1].lower()
                    if ext not in (".py", ".js", ".ts", ".tsx", ".jsx", ".rs", ".go", ".java", ".c", ".cpp", ".h", ".hpp", ".rb", ".sh", ".bat", ".ps1", ".json", ".yaml", ".yml", ".toml", ".xml", ".html", ".css", ".scss"):
                        warnings.append(PlanQualityWarning(
                            f"syntax check on path with unrecognized extension: {path}",
                            severity="warning", task_id=tid,
                        ))
            elif kind == "required_sections":
                sections = check.get("expected", [])
                if isinstance(sections, list) and not sections:
                    warnings.append(PlanQualityWarning(
                        "required_sections check with empty section list",
                        severity="warning", task_id=tid,
                    ))
            elif kind == "render":
                fmt = check.get("expected", "markdown")
                supported = {"markdown", "html"}
                if fmt not in supported:
                    warnings.append(PlanQualityWarning(
                        f"render check with unsupported format: {fmt} (supported: {sorted(supported)})",
                        severity="warning", task_id=tid,
                    ))

    # 13. quality_spec validation
    for c in plan.contracts:
        tid = c.get("task_id", "")
        contract_dict = c.get("contract", {}) or {}
        qs = contract_dict.get("quality_spec", c.get("quality_spec", {}))
        if qs:
            ms = qs.get("minimum_score")
            if ms is not None and (not isinstance(ms, (int, float)) or ms < 0 or ms > 1):
                warnings.append(PlanQualityWarning(
                    f"quality_spec.minimum_score={ms} is outside valid range [0, 1]",
                    severity="error", task_id=tid,
                ))
            dims = qs.get("quality_dimensions", {})
            if dims and isinstance(dims, dict):
                total_weight = sum(
                    v for v in dims.values() if isinstance(v, (int, float))
                )
                if total_weight <= 0:
                    warnings.append(PlanQualityWarning(
                        "quality_spec quality_dimensions weights sum to zero or negative",
                        severity="warning", task_id=tid,
                    ))

    return warnings


def plan_is_safe(warnings: List[PlanQualityWarning]) -> bool:
    """Return True if there are no error-severity issues."""
    return all(w.severity != "error" for w in warnings)


def format_warnings(warnings: List[PlanQualityWarning]) -> str:
    """Format warnings as a human-readable string."""
    if not warnings:
        return "Plan quality check passed — no issues found."
    lines = [f"Plan quality: {len(warnings)} issue(s) found"]
    for w in warnings:
        lines.append(f" [{w.severity.upper()}] {w.task_id}: {w.message}")
    return "\n".join(lines)

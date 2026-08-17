"""Structured error schema — typed error codes with severity, component, and context."""

import os
import json
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List

from .state import load_state, State
from .failure import classify_failure
from .contract import load_contract


# Error severity levels
SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"
SEVERITY_CRITICAL = "critical"

# Error components
COMPONENT_CONTRACT = "contract"
COMPONENT_STATE = "state"
COMPONENT_PREFLIGHT = "preflight"
COMPONENT_WORKER = "worker"
COMPONENT_VERIFIER = "verifier"
COMPONENT_PLANNER = "planner"
COMPONENT_SUPERVISOR = "supervisor"
COMPONENT_LOCK = "lock"
COMPONENT_SCOPE = "scope"
COMPONENT_RECONCILE = "reconcile"

# Error codes
E_CONTRACT_INVALID = "E001"
E_CONTRACT_MISSING_FIELD = "E002"
E_ILLEGAL_TRANSITION = "E003"
E_PREFLIGHT_FAILED = "E004"
E_WORKER_NONZERO_EXIT = "E005"
E_WORKER_TIMEOUT = "E006"
E_WORKER_EMPTY_OUTPUT = "E007"
E_VERIFIER_OUTPUT_MISSING = "E008"
E_VERIFIER_CONTENT_MISMATCH = "E009"
E_SCOPE_VIOLATION = "E010"
E_RECONCILE_TAMPER = "E011"
E_RECONCILE_MISSING = "E012"
E_LOCK_HELD = "E013"
E_LOCK_STALE = "E014"
E_PLANNER_FAILED = "E015"
E_UNKNOWN = "E999"


ERROR_META: Dict[str, Dict[str, str]] = {
    E_CONTRACT_INVALID: {"title": "Contract Invalid", "severity": SEVERITY_ERROR, "component": COMPONENT_CONTRACT},
    E_CONTRACT_MISSING_FIELD: {"title": "Missing Contract Field", "severity": SEVERITY_ERROR, "component": COMPONENT_CONTRACT},
    E_ILLEGAL_TRANSITION: {"title": "Illegal State Transition", "severity": SEVERITY_ERROR, "component": COMPONENT_STATE},
    E_PREFLIGHT_FAILED: {"title": "Preflight Failed", "severity": SEVERITY_ERROR, "component": COMPONENT_PREFLIGHT},
    E_WORKER_NONZERO_EXIT: {"title": "Worker Non-Zero Exit", "severity": SEVERITY_ERROR, "component": COMPONENT_WORKER},
    E_WORKER_TIMEOUT: {"title": "Worker Timeout", "severity": SEVERITY_CRITICAL, "component": COMPONENT_WORKER},
    E_WORKER_EMPTY_OUTPUT: {"title": "Worker Empty Output", "severity": SEVERITY_ERROR, "component": COMPONENT_WORKER},
    E_VERIFIER_OUTPUT_MISSING: {"title": "Verifier Output Missing", "severity": SEVERITY_ERROR, "component": COMPONENT_VERIFIER},
    E_VERIFIER_CONTENT_MISMATCH: {"title": "Verifier Content Mismatch", "severity": SEVERITY_ERROR, "component": COMPONENT_VERIFIER},
    E_SCOPE_VIOLATION: {"title": "Scope Violation", "severity": SEVERITY_CRITICAL, "component": COMPONENT_SCOPE},
    E_RECONCILE_TAMPER: {"title": "Reconcile Hash Changed", "severity": SEVERITY_CRITICAL, "component": COMPONENT_RECONCILE},
    E_RECONCILE_MISSING: {"title": "Reconcile Missing Output", "severity": SEVERITY_ERROR, "component": COMPONENT_RECONCILE},
    E_LOCK_HELD: {"title": "Lock Held", "severity": SEVERITY_ERROR, "component": COMPONENT_LOCK},
    E_LOCK_STALE: {"title": "Lock Stale", "severity": SEVERITY_WARNING, "component": COMPONENT_LOCK},
    E_PLANNER_FAILED: {"title": "Planner Failed", "severity": SEVERITY_ERROR, "component": COMPONENT_PLANNER},
    E_UNKNOWN: {"title": "Unknown Error", "severity": SEVERITY_ERROR, "component": COMPONENT_SUPERVISOR},
}


FAILURE_CLASS_TO_CODE: Dict[str, str] = {
    "timeout": E_WORKER_TIMEOUT,
    "preflight_missing_input": E_PREFLIGHT_FAILED,
    "verifier_output_missing": E_VERIFIER_OUTPUT_MISSING,
    "verifier_content_mismatch": E_VERIFIER_CONTENT_MISMATCH,
    "worker_nonzero_exit": E_WORKER_NONZERO_EXIT,
    "worker_empty_output": E_WORKER_EMPTY_OUTPUT,
    "contract_invalid": E_CONTRACT_INVALID,
    "scope_violation": E_SCOPE_VIOLATION,
    "unknown": E_UNKNOWN,
}


class StructuredError:
    """A structured error with code, severity, component, and context."""

    def __init__(
        self,
        code: str,
        message: str,
        task_id: str = "",
        context: Optional[Dict[str, Any]] = None,
    ):
        self.code = code
        self.message = message
        self.task_id = task_id
        self.context = context or {}
        self.timestamp = datetime.now(timezone.utc).isoformat()

        meta = ERROR_META.get(code, ERROR_META[E_UNKNOWN])
        self.title = meta["title"]
        self.severity = meta["severity"]
        self.component = meta["component"]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "title": self.title,
            "message": self.message,
            "severity": self.severity,
            "component": self.component,
            "task_id": self.task_id,
            "timestamp": self.timestamp,
            "context": dict(self.context),
        }

    def __repr__(self) -> str:
        return f"[{self.code}] {self.severity.upper()} {self.component}: {self.message}"


def from_failure_class(failure_class: str, task_id: str = "", message: str = "", context: Optional[Dict] = None) -> StructuredError:
    """Create a StructuredError from a failure class string."""
    code = FAILURE_CLASS_TO_CODE.get(failure_class, E_UNKNOWN)
    msg = message or ERROR_META.get(code, ERROR_META[E_UNKNOWN])["title"]
    return StructuredError(code=code, message=msg, task_id=task_id, context=context)


def from_state(state: State, task_id: str, workspace_root: str = "") -> Optional[StructuredError]:
    """Create a StructuredError from a task's state and workspace, if errored."""
    from .contract import Contract

    is_error = state.status in (
        "PREFLIGHT_FAILED", "BLOCKED", "VERIFICATION_FAILED",
        "ESCALATED", "RETRY_PENDING",
    )
    if not is_error:
        return None

    fclass = classify_failure(state)
    if not fclass or fclass == "unknown":
        if state.status == "ESCALATED":
            return StructuredError(
                code=E_UNKNOWN, message=f"Task escalated after {state.attempt} attempts",
                task_id=task_id,
                context={"attempt": state.attempt, "worker_runs": len(state.worker_results)},
            )
        return StructuredError(
            code=E_UNKNOWN, message=f"Unknown failure (status: {state.status})",
            task_id=task_id,
            context={"status": state.status},
        )

    err = from_failure_class(fclass, task_id=task_id)
    err.context["status"] = state.status
    err.context["attempt"] = state.attempt
    err.context["worker_runs"] = len(state.worker_results)
    return err


def inspect_goal(goal_id: str, plan, workspace_root: str, run_dir: str) -> List[StructuredError]:
    """Inspect all contracts in a goal for errors, returning StructuredError list."""
    errors = []
    for c in plan.contracts:
        tid = c["task_id"]
        state_file = os.path.join(run_dir, tid, "state.json")
        if not os.path.isfile(state_file):
            continue
        state = load_state(state_file)
        err = from_state(state, tid, workspace_root)
        if err is not None:
            errors.append(err)
    return errors


def format_error_list(errors: List[StructuredError]) -> str:
    """Format a list of StructuredError objects as human-readable string."""
    if not errors:
        return "No errors found."
    lines = [f"{len(errors)} error(s) found:"]
    for e in errors:
        lines.append(f"  [{e.code}] {e.severity.upper()} | {e.component} | {e.task_id}")
        lines.append(f"    {e.title}: {e.message}")
        if e.context:
            ctx_preview = ", ".join(f"{k}={v}" for k, v in list(e.context.items())[:4])
            lines.append(f"    context: {ctx_preview}")
    return "\n".join(lines)

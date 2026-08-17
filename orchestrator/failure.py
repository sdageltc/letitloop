"""Failure classification — categorizes contract execution failures for retry routing."""

import os
from typing import Dict, Any, Optional
from .state import State
from .contract import Contract


FAILURE_CLASS_TIMEOUT = "timeout"
FAILURE_CLASS_PREFLIGHT_MISSING_INPUT = "preflight_missing_input"
FAILURE_CLASS_VERIFIER_OUTPUT_MISSING = "verifier_output_missing"
FAILURE_CLASS_VERIFIER_CONTENT_MISMATCH = "verifier_content_mismatch"
FAILURE_CLASS_WORKER_NONZERO_EXIT = "worker_nonzero_exit"
FAILURE_CLASS_WORKER_EMPTY_OUTPUT = "worker_empty_output"
FAILURE_CLASS_CONTRACT_INVALID = "contract_invalid"
FAILURE_CLASS_SCOPE_VIOLATION = "scope_violation"
FAILURE_CLASS_TASK_CRASHED = "task_crashed"  # unexpected exception in supervisor execution
FAILURE_CLASS_UNKNOWN = "unknown"

# Heuristic: same failure class 3 times -> permanent escalation
MAX_SAME_CLASS_STRIKES = 3


def classify_failure(state: State, contract: Optional[Contract] = None) -> str:
    """Classify a failed task's state into a structured failure category.

    Inspects state.status, worker_results, and evidence to determine
    the most likely root cause category.
    """
    if state.data.get("scope_violations"):
        return FAILURE_CLASS_SCOPE_VIOLATION

    if state.status == "PREFLIGHT_FAILED":
        # Read preflight evidence file for missing input files
        preflight_path = state.evidence.get("preflight", "")
        if preflight_path and isinstance(preflight_path, str) and os.path.isfile(preflight_path):
            try:
                import json as _json
                with open(preflight_path, "r", encoding="utf-8") as f:
                    pdata = _json.load(f)
                for r in pdata.get("preflight_checks", []):
                    if r.get("kind") == "required_files" and not r.get("passed"):
                        return FAILURE_CLASS_PREFLIGHT_MISSING_INPUT
            except (OSError, _json.JSONDecodeError):
                pass
        return FAILURE_CLASS_UNKNOWN

    if state.status in ("VERIFICATION_FAILED",):
        # Check verification evidence for missing output vs content mismatch
        for ev_key, ev_path in state.evidence.items():
            if isinstance(ev_path, str) and os.path.isfile(ev_path):
                try:
                    import json as _json
                    with open(ev_path, "r", encoding="utf-8") as f:
                        vdata = _json.load(f)
                    for r in vdata.get("verification_results", []):
                        if r.get("kind") == "file_exists" and not r.get("passed"):
                            return FAILURE_CLASS_VERIFIER_OUTPUT_MISSING
                        if r.get("kind") in ("content_exact", "content_regex") and not r.get("passed"):
                            return FAILURE_CLASS_VERIFIER_CONTENT_MISMATCH
                except (OSError, _json.JSONDecodeError):
                    pass

    # Check worker results for timeout or nonzero exit
    if state.worker_results:
        last = state.worker_results[-1]
        stderr = last.get("stderr", "")
        if "timed out" in stderr.lower():
            return FAILURE_CLASS_TIMEOUT
        exit_code = last.get("exit_code", 0)
        if exit_code != 0:
            return FAILURE_CLASS_WORKER_NONZERO_EXIT
        stdout = last.get("stdout", "")
        if not stdout or not stdout.strip():
            return FAILURE_CLASS_WORKER_EMPTY_OUTPUT

    if state.status in ("BLOCKED",):
        return FAILURE_CLASS_PREFLIGHT_MISSING_INPUT

    return FAILURE_CLASS_UNKNOWN


def suggest_remediation(failure_class: str, attempt: int, max_attempts: int) -> Dict[str, Any]:
    """Suggest remediation action based on failure class and attempt count.

    Returns dict with keys: action (str), reason (str), requires_new_approach (bool).

    Actions: retry, split, escalate, wait, replan.
    """
    rem_attempts = max_attempts - attempt
    if failure_class == FAILURE_CLASS_TIMEOUT:
        if attempt < max_attempts:
            return {"action": "retry", "reason": f"timed out (attempt {attempt}/{max_attempts}, {rem_attempts} attempts remaining)", "requires_new_approach": True}
        return {"action": "split", "reason": f"timed out after {max_attempts} attempts — split into smaller subtasks", "requires_new_approach": False}

    if failure_class in (FAILURE_CLASS_VERIFIER_OUTPUT_MISSING, FAILURE_CLASS_WORKER_EMPTY_OUTPUT):
        if attempt < max_attempts:
            return {"action": "retry", "reason": f"output missing (attempt {attempt}/{max_attempts}, {rem_attempts} attempts remaining)", "requires_new_approach": True}
        return {"action": "split", "reason": f"output missing after {max_attempts} attempts — split into smaller subtasks", "requires_new_approach": False}

    if failure_class == FAILURE_CLASS_VERIFIER_CONTENT_MISMATCH:
        if attempt < max_attempts:
            return {"action": "retry", "reason": f"content mismatch (attempt {attempt}/{max_attempts}, {rem_attempts} attempts remaining)", "requires_new_approach": True}
        return {"action": "split", "reason": f"content mismatch after {max_attempts} attempts — split", "requires_new_approach": False}

    if failure_class == FAILURE_CLASS_WORKER_NONZERO_EXIT:
        if attempt < max_attempts:
            return {"action": "retry", "reason": f"worker nonzero exit (attempt {attempt}/{max_attempts}, {rem_attempts} attempts remaining)", "requires_new_approach": True}
        return {"action": "split", "reason": f"worker failed after {max_attempts} attempts", "requires_new_approach": False}

    if failure_class in (FAILURE_CLASS_PREFLIGHT_MISSING_INPUT, FAILURE_CLASS_CONTRACT_INVALID):
        return {"action": "replan", "reason": "contract/input issue — needs replanning", "requires_new_approach": False}

    if attempt < max_attempts:
        return {"action": "retry", "reason": f"unclassified failure (attempt {attempt}/{max_attempts}, {rem_attempts} attempts remaining)", "requires_new_approach": True}

    return {"action": "replan", "reason": f"unclassified failure after {max_attempts} attempts", "requires_new_approach": False}


def count_consecutive_same_class(state: State, failure_class: str) -> int:
    """Count how many consecutive worker results share the same failure class.

    Used for 3-strike bounding. AUT-015: an unannotated (missing/empty
    failure_class) result is treated as matching the current class so the
    strike counter does not prematurely reset.
    """
    current_class = failure_class or FAILURE_CLASS_UNKNOWN
    count = 0
    for wr in reversed(state.worker_results):
        wr_class = wr.get("failure_class") or current_class
        if wr_class == current_class:
            count += 1
        else:
            break
    return count


def annotate_worker_result(result: Dict[str, Any], failure_class: str) -> Dict[str, Any]:
    """Add failure_class annotation to a worker result dict."""
    result["failure_class"] = failure_class
    return result


FINGERPRINT_FIELDS = {"decomposition", "strategy", "format_strategy", "validation_strategy", "review_strategy"}


def compute_strategy_fingerprint(state: State) -> str:
    """Compute a hash of the approach fingerprints for divergence checking.
    
    Uses the last recorded changed_approach if available.
    """
    import hashlib
    if state.changed_approaches:
        raw = state.changed_approaches[-1]
    else:
        raw = f"default_attempt_{state.attempt}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]


def is_divergent(state: State, new_approach: str) -> bool:
    """Check if a new approach fingerprint differs from prior non-divergent ones.
    
    Returns True (divergent) if the fingerprint is new or if there is no prior
    fingerprint to compare against. Returns False (not divergent) if this
    exact approach has been tried before in the same task.
    """
    import hashlib
    new_hash = hashlib.md5(new_approach.encode()).hexdigest()[:12]
    for old_approach in state.changed_approaches[:-1]:
        old_hash = hashlib.md5(old_approach.encode()).hexdigest()[:12]
        if old_hash == new_hash and old_approach == new_approach:
            return False
    return True


def require_divergent_retry(state: State, new_approach: str) -> bool:
    """Return True if retry can proceed. If not divergent, escalate."""
    if not state.changed_approaches:
        return True
    if is_divergent(state, new_approach):
        return True
    return False

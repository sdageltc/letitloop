"""Evidence-aware replanner for dynamic plan adjustment and subtask splitting.

Expanded with richer evidence inputs (scope violations, crash info, telemetry),
better heuristics (scope-narrow, strategy-switch, merge), and explainable
replan rationale metadata.
"""

import os
import json
from typing import Dict, Any, Optional, List

from .goal import Goal, Plan
from .state import load_state, State
from .contract import load_contract, validate_contract, requires_semantic_qc
from .models import ModelRegistry
from .failure import (
    classify_failure,
    suggest_remediation,
    FAILURE_CLASS_SCOPE_VIOLATION,
    FAILURE_CLASS_TIMEOUT,
    FAILURE_CLASS_TASK_CRASHED,
    FAILURE_CLASS_WORKER_NONZERO_EXIT,
    FAILURE_CLASS_WORKER_EMPTY_OUTPUT,
)
from . import evidence as ev


class InspectResults:
    """Inspects run directory artifacts and state for tasks."""

    def __init__(self, run_dir: str):
        self.run_dir = run_dir

    def inspect_task(self, task_id: str) -> Dict[str, Any]:
        task_dir = os.path.join(self.run_dir, task_id)
        state_file = os.path.join(task_dir, "state.json")
        contract_file = os.path.join(task_dir, "contract.json")

        if not os.path.isfile(state_file):
            return {
                "task_id": task_id,
                "status": "missing",
                "attempt": 0,
                "max_attempts": 1,
                "stderr": "",
                "timed_out": False,
                "scope_violations": [],
                "crash_reason": "",
                "failure_class": "",
            }

        state = load_state(state_file)
        max_attempts = 2
        if os.path.isfile(contract_file):
            contract, _ = load_contract(contract_file)
            if contract:
                max_attempts = contract.worker.get("max_attempts", 2)

        stderr = ""
        timed_out = False
        if state.worker_results:
            last = state.worker_results[-1]
            stderr = last.get("stderr", "")
            if "timed out" in stderr.lower():
                timed_out = True

        scope_violations = state.data.get("scope_violations", [])
        crash_reason = state.data.get("crash_reason", "")
        fclass = state.data.get("last_failure_class", "") or classify_failure(state)

        return {
            "task_id": task_id,
            "status": state.status,
            "attempt": state.attempt,
            "max_attempts": max_attempts,
            "stderr": stderr,
            "timed_out": timed_out,
            "worker_results": state.worker_results,
            "scope_violations": scope_violations,
            "crash_reason": crash_reason,
            "failure_class": fclass,
        }


def _rich_suggest_fix(task_id: str, info: Dict[str, Any]) -> Dict[str, Any]:
    """Analyze rich evidence for task_id and suggest a remediation action with rationale.

    Returns dict with keys: action, reason, rationale (structured metadata).
    """
    status = info["status"]
    attempt = info["attempt"]
    max_attempts = info["max_attempts"]
    fclass = info["failure_class"]
    rem_attempts = max_attempts - attempt

    if status in ("COMPLETE", "complete", "VERIFIED", "QC_PASSED"):
        return {
            "action": "none",
            "reason": f"task {task_id} completed successfully",
            "rationale": {"evidence": "status", "trigger": status},
        }

    if status == "ESCALATED":
        return {
            "action": "split",
            "reason": f"task {task_id} escalated after {attempt} attempts",
            "rationale": {"evidence": "status", "trigger": "ESCALATED", "attempt": attempt},
        }

    # Scope violation → narrow scope
    if info["scope_violations"]:
        viol_types = [v.get("violation_type", "") for v in info["scope_violations"]]
        return {
            "action": "narrow_scope",
            "reason": f"scope violation(s): {', '.join(viol_types)} — narrowing allowed paths",
            "rationale": {"evidence": "scope_violations", "violations": viol_types},
        }

    # Crash → retry with more conservative settings
    if fclass == FAILURE_CLASS_TASK_CRASHED:
        if attempt < max_attempts:
            return {
                "action": "retry",
                "reason": f"task crashed ({info['crash_reason']}) — retry ({rem_attempts} left)",
                "rationale": {"evidence": "crash", "crash_reason": info["crash_reason"], "attempt": attempt},
            }
        return {
            "action": "split",
            "reason": f"task crashed {max_attempts} times — splitting into smaller subtasks",
            "rationale": {"evidence": "crash", "crash_reason": info["crash_reason"], "attempts_exhausted": True},
        }

    # Timeout → retry or split
    if fclass == FAILURE_CLASS_TIMEOUT:
        if attempt < max_attempts:
            return {
                "action": "retry",
                "reason": f"timed out (attempt {attempt}/{max_attempts})",
                "rationale": {"evidence": "timeout", "attempt": attempt},
            }
        return {
            "action": "split",
            "reason": f"timed out after {max_attempts} attempts — splitting",
            "rationale": {"evidence": "timeout", "attempts_exhausted": True},
        }

    # Worker issues → retry with changed approach
    if fclass in (FAILURE_CLASS_WORKER_NONZERO_EXIT, FAILURE_CLASS_WORKER_EMPTY_OUTPUT):
        if attempt < max_attempts:
            return {
                "action": "retry",
                "reason": f"worker issue ({fclass}) — retry ({rem_attempts} left)",
                "rationale": {"evidence": "worker_result", "failure_class": fclass, "attempt": attempt},
            }
        return {
            "action": "split",
            "reason": f"worker failed after {max_attempts} attempts",
            "rationale": {"evidence": "worker_result", "failure_class": fclass, "attempts_exhausted": True},
        }

    # Use structured failure classification as fallback
    if status in ("VERIFICATION_FAILED", "BLOCKED", "RETRY_PENDING"):
        rem = suggest_remediation(fclass, attempt, max_attempts)
        rem["rationale"] = {"evidence": "failure_class", "failure_class": fclass}
        return rem

    return {
        "action": "replan",
        "reason": f"task {task_id} in state {status}",
        "rationale": {"evidence": "status", "trigger": status},
    }


def replan(goal: Goal, results: Dict[str, Any], run_dir: str) -> Plan:
    """Generate a revised Plan based on goal, previous results, and rich evidence inspection.

    Returns a Plan with a replan_rationale attached for explainability.
    """
    inspector = InspectResults(run_dir)
    all_passed = True
    suggested_actions: Dict[str, Dict[str, Any]] = {}
    replan_evidence: List[Dict[str, Any]] = []

    for task_id in results:
        info = inspector.inspect_task(task_id)
        if info["status"] not in ("COMPLETE", "complete", "VERIFIED"):
            all_passed = False
        fix = _rich_suggest_fix(task_id, info)
        suggested_actions[task_id] = fix
        replan_evidence.append({
            "task_id": task_id,
            "status": info["status"],
            "action": fix["action"],
            "rationale": fix.get("rationale", {}),
        })

    if all_passed:
        contracts = [
            {"task_id": tid, "depends_on": [], "status": "complete"} for tid in results
        ]
        plan = Plan(goal_id=goal.goal_id, contracts=contracts)
        plan.replan_rationale = {
            "trigger": "all_completed",
            "evidence": replan_evidence,
        }
        return plan

    new_contracts_meta = []
    scope = goal.constraints.get("workspace_scope", {"allow": ["scratch/"], "deny": []})
    workspace_root = os.path.dirname(os.path.dirname(run_dir))
    out_dir = os.path.join(workspace_root, "orchestrator", "fixtures", "generated")
    os.makedirs(out_dir, exist_ok=True)

    split_rename_map: Dict[str, str] = {}

    contract_depends_on: Dict[str, List[str]] = {}
    for c in getattr(goal, 'contracts', []):
        if isinstance(c, dict):
            tid = c.get("task_id", "")
            deps = c.get("depends_on", [])
            if tid:
                contract_depends_on[tid] = list(deps)
        elif hasattr(c, 'task_id'):
            tid = c.task_id
            deps = getattr(c, 'depends_on', [])
            if tid:
                contract_depends_on[tid] = list(deps)

    for task_id, res in results.items():
        fix = suggested_actions.get(task_id, {"action": "none"})

        if fix["action"] == "split":
            tid_a = f"{task_id}-part-a"
            tid_b = f"{task_id}-part-b"
            out_a = f"scratch/{tid_a}_out.txt"
            out_b = f"scratch/{tid_b}_out.txt"

            original_depends_on = list(contract_depends_on.get(task_id, []))
            if not original_depends_on:
                orig_contract_path = os.path.join(run_dir, task_id, "contract.json")
                if os.path.isfile(orig_contract_path):
                    with open(orig_contract_path, "r", encoding="utf-8") as f:
                        orig_contract = json.load(f)
                    original_depends_on = list(orig_contract.get("depends_on", []))

            c_a = {
                "task_id": tid_a,
                "title": f"{task_id} - Part A (Setup)",
                "status": "DRAFTED",
                "risk_tier": "auto",
                "workspace_scope": scope,
                "objective": f"Decomposed subtask part A for {task_id}",
                "worker": {"model": ModelRegistry.WORKER_PREFIXED, "max_attempts": 2},
                "inputs": [],
                "outputs": [{"path": out_a}],
                "acceptance_checks": [{"id": f"{tid_a}-cmd", "kind": "command", "command": "python --version", "expected": 0}],
                "qc": {"required": requires_semantic_qc("auto", [{"path": out_a}], [{"kind": "command"}]), "lens": "code_correctness"},
            }
            c_b = {
                "task_id": tid_b,
                "title": f"{task_id} - Part B (Verify)",
                "status": "DRAFTED",
                "risk_tier": "auto",
                "workspace_scope": scope,
                "objective": f"Decomposed subtask part B for {task_id}",
                "worker": {"model": ModelRegistry.WORKER_PREFIXED, "max_attempts": 2},
                "inputs": [{"path": out_a}],
                "outputs": [{"path": out_b}],
                "acceptance_checks": [{"id": f"{tid_b}-exists", "kind": "file_exists", "path": out_b, "expected": True}],
                "qc": {"required": requires_semantic_qc("auto", [{"path": out_b}], [{"kind": "file_exists"}]), "lens": "code_correctness"},
            }

            path_a = os.path.join(out_dir, f"{tid_a}.json")
            path_b = os.path.join(out_dir, f"{tid_b}.json")

            with open(path_a, "w", encoding="utf-8") as f:
                json.dump(c_a, f, indent=2, ensure_ascii=False)
            with open(path_b, "w", encoding="utf-8") as f:
                json.dump(c_b, f, indent=2, ensure_ascii=False)

            errs_a = validate_contract(c_a, workspace_root=workspace_root)
            if errs_a:
                raise ValueError(f"Replanned contract {tid_a} failed validation: {errs_a}")
            errs_b = validate_contract(c_b, workspace_root=workspace_root)
            if errs_b:
                raise ValueError(f"Replanned contract {tid_b} failed validation: {errs_b}")

            # Downstream contracts must depend on BOTH split parts, not only
            # tid_b. Mapping task_id -> [tid_a, tid_b] means a downstream that
            # only needs Part A's outputs is not blocked until Part B finishes.
            split_rename_map[task_id] = [tid_a, tid_b]

            new_contracts_meta.append({
                "task_id": tid_a,
                "depends_on": list(original_depends_on),
                "status": "DRAFTED",
                "contract": c_a,
                "contract_path": os.path.relpath(path_a, workspace_root),
            })
            new_contracts_meta.append({
                "task_id": tid_b,
                "depends_on": [tid_a],
                "status": "DRAFTED",
                "contract": c_b,
                "contract_path": os.path.relpath(path_b, workspace_root),
            })

        elif fix["action"] == "narrow_scope":
            viol_paths = [v.get("path", "") for v in info.get("scope_violations", [])]
            narrowed_deny = list(scope.get("deny", []))
            for vp in viol_paths:
                if vp and vp not in narrowed_deny:
                    narrowed_deny.append(vp)
            narrowed_contract = {
                "task_id": task_id,
                "title": f"{task_id} (scope-narrowed)",
                "status": "DRAFTED",
                "risk_tier": "auto",
                "workspace_scope": {"allow": scope.get("allow", ["scratch/"]), "deny": narrowed_deny},
                "objective": f"Retry {task_id} with narrowed scope",
                "worker": {"model": ModelRegistry.WORKER_PREFIXED, "max_attempts": 2},
                "inputs": [],
                "outputs": [{"path": f"scratch/{task_id}_narrowed_out.txt"}],
                "acceptance_checks": [{"id": f"{task_id}-narrow-exists", "kind": "file_exists", "path": f"scratch/{task_id}_narrowed_out.txt", "expected": True}],
                "qc": {"required": requires_semantic_qc("auto", [{"path": f"scratch/{task_id}_narrowed_out.txt"}], [{"kind": "file_exists"}]), "lens": "code_correctness"},
            }
            errs = validate_contract(narrowed_contract, workspace_root=workspace_root)
            if errs:
                raise ValueError(f"Narrowed contract {task_id} failed validation: {errs}")
            new_contracts_meta.append({
                "task_id": task_id,
                "depends_on": [],
                "status": "DRAFTED",
                "contract": narrowed_contract,
            })

        elif fix["action"] in ("retry", "none"):
            new_contracts_meta.append({
                "task_id": task_id,
                "depends_on": [],
                "status": "DRAFTED",
            })

        else:
            new_contracts_meta.append({
                "task_id": task_id,
                "depends_on": [],
                "status": "DRAFTED",
            })

    rewritten_ids = {meta["task_id"] for meta in new_contracts_meta}

    # Carry forward downstream contracts that were not in results, rewriting
    # any depends_on that referenced a split task to BOTH split parts, so the
    # DAG is not corrupted by dropping them.
    for c in getattr(goal, 'contracts', []):
        tid = c.get("task_id", "") if isinstance(c, dict) else getattr(c, "task_id", "")
        deps = c.get("depends_on", []) if isinstance(c, dict) else getattr(c, "depends_on", [])
        if not tid or tid in rewritten_ids:
            continue
        rewritten = []
        for dep in deps:
            replacement = split_rename_map.get(dep)
            if isinstance(replacement, list):
                for part in replacement:
                    if part not in rewritten:
                        rewritten.append(part)
            else:
                rewritten.append(replacement if replacement is not None else dep)
        new_contracts_meta.append({
            "task_id": tid,
            "depends_on": rewritten,
            "status": "DRAFTED",
        })

    for meta in new_contracts_meta:
        rewritten = []
        for dep in meta.get("depends_on", []):
            replacement = split_rename_map.get(dep)
            if isinstance(replacement, list):
                # Flatten a split mapping into both parts, keeping order.
                for part in replacement:
                    if part not in rewritten:
                        rewritten.append(part)
            else:
                rewritten.append(replacement if replacement is not None else dep)
        meta["depends_on"] = rewritten

    plan = Plan(goal_id=goal.goal_id, contracts=new_contracts_meta)
    plan.replan_rationale = {
        "trigger": "replan_needed",
        "evidence": replan_evidence,
    }
    return plan


def suggest_fix(task_id: str, run_dir: str) -> Dict[str, Any]:
    """Public wrapper — analyzes evidence for a task and returns a remediation suggestion."""
    inspector = InspectResults(run_dir)
    info = inspector.inspect_task(task_id)
    return _rich_suggest_fix(task_id, info)

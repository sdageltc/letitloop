"""Contract generator: decomposes high-level goals into executable contracts."""
import json
import os
import sys
from typing import Dict, List, Any, Optional
from .goal import Goal, Plan
from .contract import validate_contract, requires_semantic_qc
from .exceptions import PlannerError
from .planner import decompose_goal, _pick_worker, _pick_checks
from .plan_quality import check_plan_quality, plan_is_safe
from .models import ModelRegistry

GENERATED_DIR_NAME = os.path.join("orchestrator", "fixtures", "generated")

HYBRID_MODEL = ModelRegistry.hybrid()
WORKER_MODEL = ModelRegistry.default_worker()


def _get_scope(goal: Goal) -> Dict[str, List[str]]:
    constraints = goal.constraints
    if "workspace_scope" in constraints and isinstance(constraints["workspace_scope"], dict):
        scope = constraints["workspace_scope"]
        return {
            "allow": list(scope.get("allow", ["scratch/"])),
            "deny": list(scope.get("deny", [])),
        }
    allow = constraints.get("allow", ["scratch/"])
    deny = constraints.get("deny", [])
    return {"allow": list(allow), "deny": list(deny)}


def _make_contract_dict(task_id, title, task_type, objective, outputs, depends_on, scope, acceptance_checks, risk_tier="auto", quality_spec=None, qc_explicit=None):
    worker = _pick_worker(task_type)
    worker["hybrid_critic_model"] = WORKER_MODEL
    worker["hybrid_max_repair_turns"] = 3
    worker["hybrid_max_tokens"] = 32000
    worker["hybrid_max_cost_usd"] = 0.30
    worker["hybrid_max_identical_verdicts"] = 2
    qc_semantic = requires_semantic_qc(risk_tier, outputs, acceptance_checks, quality_spec)
    if qc_explicit is True:
        qc_required = True
    elif qc_explicit is False:
        qc_required = qc_semantic
    else:
        qc_required = qc_semantic
    return {
        "task_id": task_id,
        "title": title,
        "status": "DRAFTED",
        "risk_tier": risk_tier,
        "workspace_scope": scope,
        "objective": objective,
        "worker": worker,
        "inputs": [],
        "outputs": outputs,
        "acceptance_checks": acceptance_checks,
        "qc": {"required": qc_required, "lens": "code_correctness"},
        "quality_spec": quality_spec or {},
    }


def generate_contracts(
    goal: Goal,
    workspace_root: str,
    failure_context: str = "",
    prefs: Optional[Dict[str, Any]] = None,
) -> Plan:
    """Generate a Plan of 1-3 contracts from a Goal and write JSONs to fixtures/generated/.

    If failure_context is provided (formatted feedback string), it is attached
    to the goal's constraints for downstream planner visibility.
    Uses hybrid: workers for implementation/code_generation tasks.
    """
    if failure_context:
        existing = goal.constraints.get("failure_context", "")
        goal.constraints["failure_context"] = (
            f"{existing}\n{failure_context}" if existing else failure_context
        )
    scope = _get_scope(goal)
    if not scope["allow"]:
        scope["allow"] = [workspace_root]
    out_dir = os.path.join(workspace_root, GENERATED_DIR_NAME)
    os.makedirs(out_dir, exist_ok=True)

    has_explicit_subtasks = (
        ("subtasks" in goal.constraints and isinstance(goal.constraints["subtasks"], list))
        or ("tasks" in goal.constraints and isinstance(goal.constraints["tasks"], list))
    )

    if not has_explicit_subtasks:
        try:
            return decompose_goal(goal, workspace_root, prefs=prefs)
        except PlannerError as e:
            print(f'[generator] Planner decomposition failed: {e}', file=sys.stderr)

    subtask_specs = []

    if "subtasks" in goal.constraints and isinstance(goal.constraints["subtasks"], list):
        subtask_specs = goal.constraints["subtasks"]
    elif "tasks" in goal.constraints and isinstance(goal.constraints["tasks"], list):
        subtask_specs = goal.constraints["tasks"]
    else:
        desc_lower = goal.description.lower()
        title_lower = goal.title.lower()

        if "step 1" in desc_lower or "two-step" in title_lower or "two step" in title_lower or "validates" in desc_lower:
            out1 = f"scratch/phase2/{goal.goal_id}_step1.txt"
            out2 = f"scratch/phase2/{goal.goal_id}_step2.txt"
            allow_paths = scope["allow"]
            if allow_paths and allow_paths[0] != "scratch/":
                base_dir = allow_paths[0].rstrip("/\\")
                out1 = f"{base_dir}/step1.txt"
                out2 = f"{base_dir}/step2.txt"

            subtask_specs = [
                {
                    "task_id": f"{goal.goal_id}-step-1",
                    "title": f"{goal.title} - Step 1 (Implementation)",
                    "type": "implementation",
                    "objective": "Execute step 1 implementation",
                    "output_path": out1,
                    "depends_on": [],
                },
                {
                    "task_id": f"{goal.goal_id}-step-2",
                    "title": f"{goal.title} - Step 2 (Verification)",
                    "type": "verification",
                    "objective": "Verify step 1 output",
                    "output_path": out2,
                    "depends_on": [f"{goal.goal_id}-step-1"],
                },
            ]
        elif "research" in desc_lower or "recon" in desc_lower:
            out = f"scratch/phase2/{goal.goal_id}_recon.txt"
            subtask_specs = [{
                "task_id": f"{goal.goal_id}-recon",
                "title": f"{goal.title} - Research",
                "type": "research",
                "objective": goal.description,
                "output_path": out,
                "depends_on": [],
            }]
        else:
            out = f"scratch/phase2/{goal.goal_id}_output.txt"
            subtask_specs = [{
                "task_id": f"{goal.goal_id}-task-1",
                "title": goal.title,
                "type": "implementation",
                "objective": goal.description,
                "output_path": out,
                "depends_on": [],
            }]

    if len(subtask_specs) > 3:
        print(f'[generator] Warning: truncating {len(subtask_specs)} subtasks to 3', file=sys.stderr)
        subtask_specs = subtask_specs[:3]
    contracts_plan_meta = []

    for idx, spec in enumerate(subtask_specs):
        task_id = spec.get("task_id", f"{goal.goal_id}-step-{idx + 1}")
        title = spec.get("title", f"{goal.title} Step {idx + 1}")
        task_type = spec.get("type", "implementation")
        objective = spec.get("objective", goal.description)
        depends_on = spec.get("depends_on", [])

        out_path = spec.get("output_path", spec.get("path"))
        if not out_path:
            allow_base = scope["allow"][0].rstrip("/\\") if scope["allow"] else "scratch"
            out_path = f"{allow_base}/{task_id}_out.txt"

        outputs = spec.get("outputs", [{"path": out_path}])
        main_out = outputs[0]["path"] if outputs else out_path

        if "acceptance_checks" in spec and spec["acceptance_checks"]:
            acceptance_checks = spec["acceptance_checks"]
        else:
            acceptance_checks = _pick_checks(task_type, main_out)

        contract_dict = _make_contract_dict(
            task_id=task_id,
            title=title,
            task_type=task_type,
            objective=objective,
            outputs=outputs,
            depends_on=depends_on,
            scope=scope,
            acceptance_checks=acceptance_checks,
            risk_tier=goal.constraints.get("risk_tier", "auto"),
        )

        errors = validate_contract(contract_dict, workspace_root=workspace_root)
        if errors:
            raise ValueError(f"Generated contract {task_id} failed validation: {errors}")

        contract_file_path = os.path.join(out_dir, f"{task_id}.json")
        with open(contract_file_path, "w", encoding="utf-8") as f:
            json.dump(contract_dict, f, indent=2, ensure_ascii=False)

        try:
            rel_contract_path = os.path.relpath(contract_file_path, workspace_root)
        except ValueError:
            rel_contract_path = os.path.abspath(contract_file_path)

        contracts_plan_meta.append({
            "task_id": task_id,
            "depends_on": depends_on,
            "status": "DRAFTED",
            "contract_path": rel_contract_path,
            "contract": contract_dict,
        })

    plan = Plan(goal_id=goal.goal_id, contracts=contracts_plan_meta)
    quality_warnings = check_plan_quality(plan, workspace_root=workspace_root)
    if not plan_is_safe(quality_warnings):
        error_msgs = [w.message for w in quality_warnings if w.severity == "error"]
        raise ValueError(f"Generated plan has quality errors: {'; '.join(error_msgs)}")
    return plan

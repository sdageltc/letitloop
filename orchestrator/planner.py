"""LLM-driven goal decomposer planner with rich preferences and hybrid routing."""

import copy
import hashlib
import json
import os
import re
from typing import Any, Dict, List, Optional

from .contract import requires_semantic_qc, validate_contract
from .exceptions import PlannerError
from .goal import Goal, Plan
from .llm import LLMError, call_llm
from .models import ModelRegistry
from .plan_quality import check_plan_quality, plan_is_safe

WORKER_MODEL = ModelRegistry.default_worker()
GENERATED_DIR = os.path.join("orchestrator", "fixtures", "generated")

HYBRID_MODEL = ModelRegistry.hybrid()

_ADVERSARIAL_SECTIONS = [
    "Executive Verdict",
    "System Map",
    "Core Strengths",
    "Critical Contradictions",
    "Uncomfortable Truths",
    "Failure Modes & Edge Cases",
    "Concrete Schemas & Artifacts",
    "Test Specifications",
    "Alternative Architectures",
    "90-Day Hardening Plan",
]

_TASK_TYPE_TO_WORKER = {
    "implementation": {"model": HYBRID_MODEL, "max_attempts": 3},
    "code_generation": {"model": HYBRID_MODEL, "max_attempts": 3},
    "research": {"model": WORKER_MODEL, "max_attempts": 2},
    "verification": {"model": WORKER_MODEL, "max_attempts": 2},
    "aggregation": {"model": WORKER_MODEL, "max_attempts": 2},
    "adversarial_audit": {
        "model": WORKER_MODEL,
        "max_attempts": 3,
        "quality_profile": "adversarial_architecture_audit",
    },
}

_TASK_TYPE_CHECKS = {
    "aggregation": lambda p: [
        {"id": f"{p}-json", "kind": "json_schema", "path": "PLACEHOLDER", "expected": {}},
    ],
    "adversarial_audit": lambda p: [
        {"id": f"{p}-content", "kind": "content_regex", "path": "PLACEHOLDER", "expected": ".+"},
        {"id": f"{p}-min_size", "kind": "min_size", "path": "PLACEHOLDER", "expected": 1000},
        {"id": f"{p}-sections", "kind": "required_sections", "path": "PLACEHOLDER", "expected": _ADVERSARIAL_SECTIONS},
    ],
}


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


def _preferences_block(goal: Goal) -> str:
    """Build a preferences section for the prompt.

    Serializes the FULL _preferences structure — not just a hardcoded
    whitelist. Known sections render as friendly bullets; arbitrary custom
    key-value pairs (including dynamic risk thresholds) are preserved and
    emitted generically so nothing is silently dropped.
    """
    constraints = goal.constraints
    prefs = constraints.get("_preferences", {})
    if not prefs or not isinstance(prefs, dict):
        return ""
    lines = ["User preferences:"]

    known_sections = {
        "style": {
            "minimal_changes": "Prefer minimal changes",
            "keep_simple": "Keep implementation simple",
        },
        "safety": {
            "never_delete_blindly": "Never delete files without asking",
            "do_not_touch": "Do not touch protected files",
        },
        "verification": {
            "run_tests": "Run tests after changes",
        },
        "planning": {
            "approval_required_for_macro": "Require approval for macro tasks",
        },
    }

    emitted = set()

    def _render_value(value, indent: str = "") -> List[str]:
        out = []
        if isinstance(value, dict):
            for k, v in value.items():
                if isinstance(v, (dict, list)):
                    out.append(f"{indent}- {k}:")
                    out.extend(_render_value(v, indent + "  "))
                else:
                    out.append(f"{indent}- {k}: {v}")
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, (dict, list)):
                    out.append(f"{indent}- {_render_scalar(item)}")
                else:
                    out.append(f"{indent}- {item}")
        else:
            out.append(f"{indent}- {value}")
        return out

    def _render_scalar(value) -> str:
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)
        return str(value)

    for section, mapping in known_sections.items():
        sec = prefs.get(section)
        if not isinstance(sec, dict):
            continue
        for key, label in mapping.items():
            if sec.get(key):
                lines.append(f"- {label}")
                emitted.add((section, key))

    # Preserve any custom keys the whitelist doesn't know — risk thresholds,
    # arbitrary key-value pairs, nested structures — so nothing is lost.
    for section, sec in prefs.items():
        if not isinstance(sec, dict):
            lines.append(f"- {section}: {_render_scalar(sec)}")
            continue
        for key, value in sec.items():
            if (section, key) in emitted:
                continue
            if isinstance(value, bool):
                lines.append(f"- {key}: {value}")
            elif isinstance(value, (dict, list)):
                lines.append(f"- {section}.{key}:")
                lines.extend(_render_value(value, "  "))
            else:
                lines.append(f"- {section}.{key}: {value}")

    lines.append("")
    return "\n".join(lines)


def _build_planner_prompt(goal: Goal) -> str:
    prompt = (
        "You are a task decomposition planner. "
        "Given a high-level goal, break it down into 1-8 subtasks as executable contracts.\n"
        f"Goal Title: {goal.title}\n"
        f"Goal Description: {goal.description}\n"
        f"Goal Constraints: {json.dumps(goal.constraints)}\n\n"
    )
    prefs_block = _preferences_block(goal)
    if prefs_block:
        prompt += prefs_block + "\n"

    prompt += (
        "Return ONLY valid JSON. No markdown, no extra text.\n\n"
        "JSON format:\n"
        "{\n"
        '  "summary": "One-line summary of the plan",\n'
        '  "contracts": [\n'
        "    {\n"
        '      "task_id": "goal-id-step-1",\n'
        '      "title": "Step 1: descriptive title",\n'
        '      "type": "implementation | code_generation | research | verification | aggregation | adversarial_audit",\n'
        '      "objective": "clear concrete objective for this step",\n'
        '      "output_path": "scratch/goal-id/step1_output.py",\n'
        '      "depends_on": [],\n'
        '      "acceptance_checks": [\n'
        '        {"id": "check-1", "kind": "content_regex", "path": "scratch/goal-id/step1_output.py", "expected": ".+"}\n'
        "      ],\n"
        '      "quality_spec": {\n'
        '        "required_sections": [],\n'
        '        "quality_dimensions": {"completeness": 0.5, "correctness": 0.5},\n'
        '        "hard_failures": [],\n'
        '        "minimum_score": 0.8\n'
        "      }\n"
        "    }\n"
        "  ]\n"
        "}\n\n"
        "Rules:\n"
        "- For implementation/code_generation tasks, produce syntax-valid files\n"
        "- Research tasks go to scratch/, implementation can go to src/ if appropriate\n"
        "- Set quality_spec with required_sections, quality_dimensions, hard_failures, and minimum_score\n"
        "- Use depends_on for ordering (e.g., inspection before implementation)\n"
        "- Keep each step focused and concrete\n"
        "- output_path must be within allowed workspace scope\n"
    )
    return prompt


def _extract_first_balanced_json_object(text: str) -> str:
    """Return the first brace-balanced JSON object, respecting JSON string literals."""
    start = text.find("{")
    if start < 0:
        return ""
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return ""


def _all_balanced_json_objects(text: str) -> List[str]:
    """Yield every string-aware brace-balanced JSON object in order.

    Unlike _extract_first_balanced_json_object, this scans past the first
    object so a leading example JSON / schema block does not shadow the
    actual plan. String literals are respected (a '{' inside a string value
    must not corrupt the balance).
    """
    results: List[str] = []
    start = 0
    while True:
        start = text.find("{", start)
        if start < 0:
            break
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    results.append(text[start : index + 1])
                    start = index + 1
                    break
        else:
            break
    return results


def _try_parse_plan_candidates(candidates: List[str]) -> Optional[Dict[str, Any]]:
    """Attempt json.loads on each candidate, preferring one shaped like a plan.

    A leading ```json example/schema block must NOT shadow the actual plan:
    among all parseable dicts with a non-empty 'contracts' list, the one with
    the most contracts wins (ties go to the latest in order). Falls back to
    the first parseable dict (e.g. a wrapped {plan: ...} schema), else None.
    """
    first_dict: Optional[Dict[str, Any]] = None
    best: Optional[Dict[str, Any]] = None
    best_count = -1
    for raw in candidates:
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(data, dict):
            continue
        if first_dict is None:
            first_dict = data
        contracts = data.get("contracts")
        if isinstance(contracts, list) and contracts:
            count = len(contracts)
            if count >= best_count:
                best = data
                best_count = count
    return best if best is not None else first_dict


def _parse_llm_json(stdout: str) -> Dict[str, Any]:
    if not stdout or not stdout.strip():
        raise PlannerError("Empty output from LLM")

    candidates: List[str] = []

    # 1. Whole response as-is (common case: no markdown, pure JSON).
    candidates.append(stdout.strip())

    # 2. Each markdown fenced block, in order — a leading ```json example
    #    block must not capture the actual plan that follows.
    fences = re.findall(r"```(?:json)?\s*(.*?)\s*```", stdout, re.DOTALL)
    candidates.extend(f.strip() for f in fences if f.strip())

    # 3. Every string-aware brace-balanced object, in order. The first object
    #    may be schema/example; the plan-bearing object wins via
    #    _try_parse_plan_candidates.
    candidates.extend(_all_balanced_json_objects(stdout))

    data = _try_parse_plan_candidates(candidates)
    if data is None:
        digest = hashlib.sha256(stdout.encode("utf-8", errors="replace")).hexdigest()[:8]
        raise PlannerError(f"Failed to parse JSON from LLM response (raw_sha256={digest})")

    if "contracts" not in data or not isinstance(data["contracts"], list):
        raise PlannerError("LLM response JSON missing 'contracts' array")
    if not data["contracts"]:
        raise PlannerError("LLM returned empty contracts array")
    return data


def _call_llm_planner(prompt: str, workspace_root: str, timeout_sec: int = 120) -> str:
    """Call the generic LLM transport with the planner prompt."""
    if os.environ.get("FAKE_WORKER", ""):
        timeout_sec = 1
    try:
        response = call_llm(prompt, ModelRegistry.default_worker(), timeout_s=timeout_sec)
        return response["text"] or ""
    except LLMError as e:
        raise PlannerError(f"Planner LLM call failed: {e}")


def _pick_worker(task_type: str) -> Dict[str, Any]:
    """Pick worker config based on task type."""
    return dict(_TASK_TYPE_TO_WORKER.get(task_type, _TASK_TYPE_TO_WORKER["implementation"]))


def _checks_for_output(task_type: str, output_path: str) -> List[Dict[str, Any]]:
    """Generate format-aware acceptance checks for an output path (AUT-007).

    Checks must match the expected artifact format: json_schema for JSON,
    required_sections/render for markdown, syntax+hygiene for Python.
    """
    extension = os.path.splitext(output_path)[1].lower()

    if task_type == "aggregation":
        return _TASK_TYPE_CHECKS["aggregation"]("chk")

    if extension == ".json":
        return [
            {"id": "chk-json", "kind": "json_schema", "path": "PLACEHOLDER", "expected": {}},
        ]

    if extension == ".md":
        if task_type == "adversarial_audit":
            checks = _TASK_TYPE_CHECKS["adversarial_audit"]("chk")
            checks.append({"id": "chk-render", "kind": "render", "path": "PLACEHOLDER", "expected": "markdown"})
            return checks
        return [
            {"id": "chk-sections", "kind": "required_sections", "path": "PLACEHOLDER", "expected": []},
            {"id": "chk-min_size", "kind": "min_size", "path": "PLACEHOLDER", "expected": 10},
            {"id": "chk-render", "kind": "render", "path": "PLACEHOLDER", "expected": "markdown"},
        ]

    if extension == ".py":
        return [
            {"id": "chk-syntax", "kind": "syntax", "path": "PLACEHOLDER"},
            {"id": "chk-hygiene", "kind": "hygiene", "path": "PLACEHOLDER"},
            {"id": "chk-min_size", "kind": "min_size", "path": "PLACEHOLDER", "expected": 10},
            {"id": "chk-content", "kind": "content_regex", "path": "PLACEHOLDER", "expected": ".+"},
        ]

    return [
        {"id": "chk-min_size", "kind": "min_size", "path": "PLACEHOLDER", "expected": 1},
        {"id": "chk-content", "kind": "content_regex", "path": "PLACEHOLDER", "expected": ".+"},
    ]


def _pick_checks(task_type: str, output_path: str) -> List[Dict[str, Any]]:
    """Generate format-aware acceptance checks, filling in the output path."""
    checks = []
    for check in _checks_for_output(task_type, output_path):
        entry = copy.deepcopy(check)
        if entry.get("path") == "PLACEHOLDER":
            entry["path"] = output_path
        checks.append(entry)
    return checks


def decompose_goal(
    goal: Goal,
    workspace_root: str,
    timeout_sec: int = 120,
    prefs: Optional[Dict[str, Any]] = None,
) -> Plan:
    """Decompose a high-level Goal into subtask contracts via LLM with @file transport.

    Generates contracts with:
    - hybrid: workers for implementation/code_generation
    - richer acceptance checks (syntax, hygiene, min_size)
    - plan metadata (summary, approval info)
    """
    goal.constraints = dict(goal.constraints)
    goal.constraints["_preferences"] = prefs or {}
    prompt = _build_planner_prompt(goal)

    stdout = _call_llm_planner(prompt, workspace_root, timeout_sec)
    llm_data = _parse_llm_json(stdout)

    scope = _get_scope(goal)
    out_dir = os.path.join(workspace_root, GENERATED_DIR)
    os.makedirs(out_dir, exist_ok=True)

    llm_data.get("summary", "")
    contracts_plan_meta = []

    for idx, raw_c in enumerate(llm_data["contracts"]):
        if not isinstance(raw_c, dict):
            raise PlannerError(f"Contract item {idx} is not a dict")

        task_id = raw_c.get("task_id", f"{goal.goal_id}-step-{idx + 1}")
        title = raw_c.get("title", f"Step {idx + 1}")
        task_type = raw_c.get("type", "implementation")
        objective = raw_c.get("objective", goal.description)
        depends_on = raw_c.get("depends_on", [])

        if task_type not in _TASK_TYPE_TO_WORKER:
            task_type = "implementation"

        if "outputs" in raw_c and isinstance(raw_c["outputs"], list) and raw_c["outputs"]:
            outputs = raw_c["outputs"]
        else:
            output_path = raw_c.get("output_path", f"scratch/phase2/{goal.goal_id}_step{idx + 1}.txt")
            outputs = [{"path": output_path}]

        main_out = outputs[0]["path"] if outputs else "scratch/scratch.txt"

        if "acceptance_checks" in raw_c and isinstance(raw_c["acceptance_checks"], list) and raw_c["acceptance_checks"]:
            acceptance_checks = raw_c["acceptance_checks"]
        else:
            acceptance_checks = _pick_checks(task_type, main_out)

        worker = _pick_worker(task_type)
        worker["hybrid_critic_model"] = WORKER_MODEL
        worker["hybrid_max_repair_turns"] = 3
        worker["hybrid_max_tokens"] = 32000
        worker["hybrid_max_cost_usd"] = 0.30
        worker["hybrid_max_identical_verdicts"] = 2

        risk_tier = goal.constraints.get("risk_tier", "auto")
        quality_spec = raw_c.get("quality_spec", {})
        if not quality_spec and requires_semantic_qc(risk_tier, outputs, acceptance_checks):
            quality_spec = {
                "required_sections": [],
                "quality_dimensions": {"completeness": 0.5, "correctness": 0.5},
                "hard_failures": [],
                "minimum_score": 0.8,
            }

        qc_explicit = raw_c.get("qc", {}).get("required") if isinstance(raw_c.get("qc"), dict) else None
        qc_semantic = requires_semantic_qc(risk_tier, outputs, acceptance_checks, quality_spec)
        if qc_explicit is True:
            qc_required = True
        elif qc_explicit is False:
            qc_required = qc_semantic
        else:
            qc_required = qc_semantic

        contract_dict = {
            "task_id": task_id,
            "title": title,
            "status": "DRAFTED",
            "risk_tier": goal.constraints.get("risk_tier", "auto"),
            "workspace_scope": scope,
            "objective": objective,
            "worker": worker,
            "inputs": raw_c.get("inputs", []),
            "outputs": outputs,
            "acceptance_checks": acceptance_checks,
            "quality_spec": quality_spec,
            "qc": {"required": qc_required, "lens": "code_correctness"},
        }

        errors = validate_contract(contract_dict, workspace_root=workspace_root)
        if errors:
            raise PlannerError(f"Generated contract {task_id} failed validation: {errors}")

        contract_file_path = os.path.join(out_dir, f"{task_id}.json")
        with open(contract_file_path, "w", encoding="utf-8") as f:
            json.dump(contract_dict, f, indent=2, ensure_ascii=False)

        try:
            rel_contract_path = os.path.relpath(contract_file_path, workspace_root)
        except ValueError:
            rel_contract_path = os.path.abspath(contract_file_path)
        contracts_plan_meta.append(
            {
                "task_id": task_id,
                "depends_on": depends_on,
                "status": "DRAFTED",
                "contract_path": rel_contract_path,
                "contract": contract_dict,
                "type": task_type,
                "objective": objective,
            }
        )

    plan = Plan(goal_id=goal.goal_id, contracts=contracts_plan_meta)
    quality_warnings = check_plan_quality(plan, workspace_root=workspace_root)
    if not plan_is_safe(quality_warnings):
        error_msgs = [w.message for w in quality_warnings if w.severity == "error"]
        raise PlannerError(f"Generated plan has quality errors: {'; '.join(error_msgs)}")
    return plan

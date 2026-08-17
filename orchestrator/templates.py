"""Contract template library — pre-built contract skeletons for common task types."""

from typing import Dict, Any, List

from .contract import validate_contract, requires_semantic_qc
from .models import ModelRegistry

_WORKER = ModelRegistry.WORKER_PREFIXED
_HYBRID = ModelRegistry.hybrid()
_HYBRID_CRITIC = ModelRegistry.WORKER_PREFIXED

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

_ADVERSARIAL_DIMENSIONS = {
    "analytical_depth": 0.25,
    "contradiction_resolution": 0.15,
    "actionability": 0.20,
    "intellectual_courage": 0.15,
    "edge_case_coverage": 0.10,
    "writing_quality": 0.10,
    "source_fidelity": 0.05,
}

TEMPLATES: Dict[str, Dict[str, Any]] = {
    "research": {
        "description": "Research / reconnaissance task — content check on output, low retry budget",
        "defaults": {
            "title": "Research: {objective}",
            "status": "DRAFTED",
            "risk_tier": "auto",
            "workspace_scope": {"allow": ["scratch/"], "deny": []},
            "worker": {"model": _WORKER, "max_attempts": 2},
            "inputs": [],
            "acceptance_checks": [
                {"id": "{task_id}-content", "kind": "content_regex", "path": "{output_path}", "expected": ".+"},
            ],
            "qc": {"required": "auto", "lens": "code_correctness"},
        },
        "notes": "task_id, objective, outputs required. Two attempts — research LLM calls are nondeterministic; a retry budget absorbs transient worker slips.",
    },
    "implementation": {
        "description": "Implementation task — hybrid LLM loop with critic + verifier, up to 5 turns",
        "defaults": {
            "title": "Implement: {objective}",
            "status": "DRAFTED",
            "risk_tier": "auto",
            "workspace_scope": {"allow": ["scratch/"], "deny": []},
            "worker": {
                "model": _HYBRID,
                "max_attempts": 3,
                "hybrid_critic_model": _HYBRID_CRITIC,
                "hybrid_max_repair_turns": 5,
                "hybrid_max_tokens": 32000,
                "hybrid_max_cost_usd": 0.30,
                "hybrid_max_identical_verdicts": 3,
            },
            "inputs": [],
            "acceptance_checks": [
                {"id": "{task_id}-syntax", "kind": "syntax", "path": "{output_path}"},
                {"id": "{task_id}-hygiene", "kind": "hygiene", "path": "{output_path}"},
                {"id": "{task_id}-min_size", "kind": "min_size", "path": "{output_path}", "expected": 10},
                {"id": "{task_id}-content", "kind": "content_regex", "path": "{output_path}", "expected": ".+"},
            ],
            "qc": {"required": "auto", "lens": "code_correctness"},
        },
        "notes": "task_id, objective, outputs required. Hybrid loop: implementer → verifier → critic → repair. Supports `hybrid:` model prefix for bounded LLM loop.",
    },
    "verification": {
        "description": "Verification task — checks upstream output exists, single shot",
        "defaults": {
            "title": "Verify: {objective}",
            "status": "DRAFTED",
            "risk_tier": "auto",
            "workspace_scope": {"allow": ["scratch/"], "deny": []},
            "worker": {"model": _WORKER, "max_attempts": 2},
            "inputs": [],
            "acceptance_checks": [
                {"id": "{task_id}-regex", "kind": "content_regex", "path": "{output_path}", "expected": ".*"},
            ],
            "qc": {"required": "auto", "lens": "code_correctness"},
        },
        "notes": "task_id, objective, outputs required. Depend on upstream task for input.",
    },
    "aggregation": {
        "description": "Aggregation task — merges upstream outputs into JSON",
        "defaults": {
            "title": "Aggregate: {objective}",
            "status": "DRAFTED",
            "risk_tier": "auto",
            "workspace_scope": {"allow": ["scratch/"], "deny": []},
            "worker": {"model": _WORKER, "max_attempts": 2},
            "inputs": [],
            "acceptance_checks": [
                {"id": "{task_id}-json", "kind": "json_schema", "path": "{output_path}", "expected": {}},
            ],
            "qc": {"required": "auto", "lens": "code_correctness"},
        },
        "notes": "task_id, objective, outputs required. Expects JSON output.",
    },
    "code_generation": {
        "description": "Code generation task — hybrid LLM loop with critic + verifier, writes source files",
        "defaults": {
            "title": "Generate: {objective}",
            "status": "DRAFTED",
            "risk_tier": "auto",
            "workspace_scope": {"allow": ["scratch/", "src/"], "deny": ["AGENTS.md", "memory/", ".opencode/"]},
            "worker": {
                "model": _HYBRID,
                "max_attempts": 3,
                "hybrid_critic_model": _HYBRID_CRITIC,
                "hybrid_max_repair_turns": 5,
                "hybrid_max_tokens": 32000,
                "hybrid_max_cost_usd": 0.30,
                "hybrid_max_identical_verdicts": 3,
            },
            "inputs": [],
            "acceptance_checks": [
                {"id": "{task_id}-syntax", "kind": "syntax", "path": "{output_path}"},
                {"id": "{task_id}-hygiene", "kind": "hygiene", "path": "{output_path}"},
                {"id": "{task_id}-min_size", "kind": "min_size", "path": "{output_path}", "expected": 10},
                {"id": "{task_id}-content", "kind": "content_regex", "path": "{output_path}", "expected": ".+"},
            ],
            "qc": {"required": "auto", "lens": "code_correctness"},
        },
        "notes": "task_id, objective, outputs required. Custom scope includes src/. Hybrid loop with verifier + critic.",
    },
    "adversarial_audit": {
        "description": "Adversarial architecture audit — deep critical review with contradiction detection, edge case enumeration, concrete schemas, and implementation-ready recommendations",
        "defaults": {
            "title": "Architecture Audit: {objective}",
            "status": "DRAFTED",
            "risk_tier": "qc_required",
            "workspace_scope": {"allow": ["scratch/"], "deny": []},
            "worker": {
                "model": _WORKER,
                "max_attempts": 3,
                "quality_profile": "adversarial_architecture_audit",
            },
            "inputs": [],
            "acceptance_checks": [
                {"id": "{task_id}-content", "kind": "content_regex", "path": "{output_path}", "expected": ".+"},
                {"id": "{task_id}-min_size", "kind": "min_size", "path": "{output_path}", "expected": 1000},
                {"id": "{task_id}-sections", "kind": "required_sections", "path": "{output_path}", "expected": list(_ADVERSARIAL_SECTIONS)},
                {"id": "{task_id}-render", "kind": "render", "path": "{output_path}", "expected": "markdown"},
            ],
            "quality_spec": {
                "required_sections": list(_ADVERSARIAL_SECTIONS),
                "quality_dimensions": _ADVERSARIAL_DIMENSIONS,
                "hard_failures": ["no_contradictions", "no_edge_cases", "no_schemas", "no_alternative_architecture", "no_uncomfortable_truths"],
                "minimum_score": 0.85,
                "minimum_counts": {
                    "contradictions": 5,
                    "edge_cases": 20,
                    "test_specs": 10,
                    "schemas": 3,
                    "radical_alternatives": 1,
                },
            },
            "qc": {"required": "auto", "lens": "architecture_audit"},
        },
        "notes": "task_id, objective, outputs required. Aggressive QC with dimensional scoring. Worker must be adversarial — reject summaries, demand original thinking.",
    },
}


def list_templates() -> List[str]:
    return list(TEMPLATES.keys())


def template_details(name: str) -> Dict[str, Any]:
    if name not in TEMPLATES:
        raise ValueError(f"unknown template: {name!r}")
    t = TEMPLATES[name]
    return {"name": name, "description": t["description"], "defaults": dict(t["defaults"]), "notes": t["notes"]}


def apply_template(name: str, overrides: Dict[str, Any]) -> Dict[str, Any]:
    if name not in TEMPLATES:
        raise ValueError(f"unknown template: {name!r}")
    t = TEMPLATES[name]
    defaults = dict(t["defaults"])

    task_id = overrides.get("task_id")
    objective = overrides.get("objective")
    outputs = overrides.get("outputs")
    if not task_id:
        raise ValueError("overrides must include 'task_id'")
    if not objective:
        raise ValueError("overrides must include 'objective'")
    if not outputs:
        raise ValueError("overrides must include 'outputs' (list of path dicts)")

    out_path = outputs[0]['path'] if isinstance(outputs[0], dict) else str(outputs[0])

    max_depth = 50

    def _subst(v, _depth=0, _visited=None):
        if _depth > max_depth:
            raise ValueError(f"template substitution exceeded max depth ({max_depth}) — possible circular reference")
        if _visited is None:
            _visited = set()
        obj_id = id(v)
        if obj_id in _visited:
            raise ValueError("circular reference detected in template substitution")
        if isinstance(v, (dict, list)):
            _visited.add(obj_id)
        if isinstance(v, str):
            return v.replace('{task_id}', task_id).replace('{objective}', objective).replace('{output_path}', out_path)
        if isinstance(v, list):
            result = [_subst(item, _depth + 1, _visited) for item in v]
            return result
        if isinstance(v, dict):
            result = {kk: _subst(vv, _depth + 1, _visited) for kk, vv in v.items()}
            return result
        return v
    def _deep_merge(base: dict, override: dict) -> dict:
        merged = dict(base)
        for k, v in override.items():
            if k in merged and isinstance(merged[k], dict) and isinstance(v, dict):
                merged[k] = _deep_merge(merged[k], v)
            else:
                merged[k] = v
        return merged

    contract = _subst(defaults)

    for k, v in overrides.items():
        if k in ("task_id", "objective", "outputs"):
            contract[k] = v
        elif k in defaults and isinstance(v, dict) and isinstance(contract.get(k), dict):
            contract[k] = _deep_merge(contract[k], v)
        else:
            contract[k] = v

    if contract.get("qc", {}).get("required") == "auto":
        risk_tier = contract.get("risk_tier", "auto")
        outputs = contract.get("outputs", [])
        checks = contract.get("acceptance_checks", [])
        contract["qc"]["required"] = requires_semantic_qc(risk_tier, outputs, checks)

    errs = validate_contract(contract)
    if errs:
        raise ValueError(f"generated contract failed validation: {errs}")

    return contract

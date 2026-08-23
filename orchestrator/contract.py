"""Strict JSON task contract validation."""

import json
import os

from .quality_plan import VALID_LENSES

REQUIRED_TOP_LEVEL = {
    "task_id": str,
    "title": str,
    "status": str,
    "risk_tier": str,
    "workspace_scope": dict,
    "objective": str,
    "worker": dict,
    "outputs": list,
    "acceptance_checks": list,
    "qc": dict,
}

VALID_RISK_TIERS = {"auto", "qc_required", "human_required"}
VALID_STATUSES = {"drafted", "DRAFTED"}
VALID_CHECK_KINDS = {
    "command",
    "file_exists",
    "json_schema",
    "content_exact",
    "content_regex",
    "syntax",
    "hygiene",
    "min_size",
    "required_sections",
    "render",
    "contradiction_count",
    "edge_case_count",
    "test_spec_count",
    "schema_count",
    "novel_artifact",
}
VALID_QC_LENSES = set(VALID_LENSES)  # single canonical registry (see quality_plan.VALID_LENSES)


def requires_semantic_qc(risk_tier: str, outputs: list, acceptance_checks: list, quality_spec: dict = None) -> bool:
    """Central policy: determine if a task requires semantic QC review.

    Returns True when:
    - risk_tier is qc_required or human_required
    - multiple outputs exist
    - outputs touch src/ or tests/
    - any acceptance check implies semantic significance
    - quality_spec has hard_failures, minimum_score, or minimum_counts
    """
    if risk_tier in ("qc_required", "human_required"):
        return True
    if len(outputs) > 1:
        return True
    for out in outputs:
        path = out.get("path", "").replace("\\", "/")
        if path.startswith("src/") or path.startswith("tests/") or "/tests/" in path:
            return True
    for check in acceptance_checks:
        kind = check.get("kind", "")
        if kind == "file_exists":
            continue
        if kind in ("syntax", "hygiene", "min_size", "command"):
            continue
        if kind == "content_regex":
            if check.get("expected", "") in (".+", ".*", ""):
                continue
        return True
    if quality_spec:
        if quality_spec.get("hard_failures") or quality_spec.get("minimum_score", 0) > 0:
            return True
        if quality_spec.get("minimum_counts"):
            return True
    if not outputs and quality_spec:
        return True
    return False


def validate_contract_against_plan(plan_contract: dict, generated_contract: dict) -> list[str]:
    """Compare safety fields between plan and generated contract.

    Generated contract may only ADD protections, never remove them.
    Returns list of error/warning strings if any safety field was DOWNGRADED.
    """
    messages = []

    plan_qc = plan_contract.get("qc", {})
    gen_qc = generated_contract.get("qc", {})
    if plan_qc.get("required") is True and gen_qc.get("required") is not True:
        messages.append("qc.required: plan requires QC (true) but generated contract does not")

    plan_allow = set(plan_contract.get("workspace_scope", {}).get("allow", []))
    gen_allow = set(generated_contract.get("workspace_scope", {}).get("allow", []))
    if not gen_allow.issuperset(plan_allow):
        missing = plan_allow - gen_allow
        messages.append(
            f"workspace_scope.allow: generated contract missing {len(missing)} path(s) from plan: {sorted(missing)}"
        )

    plan_deny = set(plan_contract.get("workspace_scope", {}).get("deny", []))
    gen_deny = set(generated_contract.get("workspace_scope", {}).get("deny", []))
    if not gen_deny.issuperset(plan_deny):
        missing = plan_deny - gen_deny
        messages.append(
            f"workspace_scope.deny: generated contract missing {len(missing)} path(s) from plan: {sorted(missing)}"
        )

    plan_checks = len(plan_contract.get("acceptance_checks", []))
    gen_checks = len(generated_contract.get("acceptance_checks", []))
    if gen_checks < plan_checks:
        messages.append(f"WARNING: acceptance_checks: plan has {plan_checks}, generated has {gen_checks}")

    plan_hf = set(plan_contract.get("quality_spec", {}).get("hard_failures", []))
    gen_hf = set(generated_contract.get("quality_spec", {}).get("hard_failures", []))
    if not gen_hf.issuperset(plan_hf):
        missing = plan_hf - gen_hf
        messages.append(
            f"quality_spec.hard_failures: generated contract missing {len(missing)} failure(s) from plan: {sorted(missing)}"
        )

    plan_ms = plan_contract.get("quality_spec", {}).get("minimum_score", 0)
    gen_ms = generated_contract.get("quality_spec", {}).get("minimum_score", 0)
    if gen_ms < plan_ms:
        messages.append(f"quality_spec.minimum_score: plan requires {plan_ms}, generated has {gen_ms}")

    return messages


class Contract:
    """Immutable validated task contract."""

    def __init__(self, raw, source_path=None):
        self._raw = raw
        self.source_path = source_path
        self.task_id = raw["task_id"]
        self.title = raw["title"]
        self.status = raw["status"]
        self.risk_tier = raw["risk_tier"]
        self.workspace_scope = raw["workspace_scope"]
        self.objective = raw["objective"]
        self.worker = raw["worker"]
        self.inputs = raw.get("inputs", [])
        self.outputs = raw["outputs"]
        self.acceptance_checks = raw["acceptance_checks"]
        self.qc = raw["qc"]
        self.quality_spec = raw.get("quality_spec", {})
        self.quality_plan = raw.get("quality_plan")
        self.components = raw.get("components", [])
        self.required_mcp_servers = raw.get("required_mcp_servers", [])
        self.next_action = raw.get("next_action", "preflight")

    def allowed_paths(self):
        """Return set of paths this contract is allowed to write to."""
        return set(self.workspace_scope.get("allow", []))

    def denied_paths(self):
        """Return set of paths this contract is denied from writing to."""
        return set(self.workspace_scope.get("deny", []))

    def to_dict(self):
        return dict(self._raw)

    def __repr__(self):
        return f"<Contract {self.task_id} status={self.status}>"


def _canonical_under(path: str, root: str) -> bool:
    try:
        candidate = os.path.realpath(path)
        base = os.path.realpath(root)
        return os.path.commonpath([candidate, base]) == base
    except (OSError, ValueError):
        return False


def check_path_allowed(path, allowed_paths, denied_paths, workspace_root):
    """Check if path is within allow-list and outside deny-list.

    Uses os.path.realpath + commonpath so symlinks, .. traversal, and
    absolute escapes are resolved before the scope check.
    """
    if not isinstance(path, str) or not path or "\x00" in path:
        return False, "invalid path"
    ws = os.path.realpath(os.path.abspath(workspace_root))
    raw = path if os.path.isabs(path) else os.path.join(ws, path)
    abs_path = os.path.realpath(os.path.abspath(raw))

    if not _canonical_under(abs_path, ws):
        return False, f"path {path} escapes workspace"

    for denied in denied_paths:
        denied_abs = os.path.realpath(os.path.abspath(os.path.join(ws, denied)))
        try:
            in_deny = os.path.commonpath([abs_path, denied_abs]) == denied_abs
        except ValueError:
            in_deny = False
        if in_deny:
            return False, f"path {path} is in deny-list"

    for allowed in allowed_paths:
        allowed_abs = os.path.realpath(os.path.abspath(os.path.join(ws, allowed)))
        try:
            in_allow = os.path.commonpath([abs_path, allowed_abs]) == allowed_abs
        except ValueError:
            in_allow = False
        if in_allow:
            return True, None

    return False, f"path {path} is not in allow-list"


def validate_contract(raw, workspace_root=None, strict_unknown=True):
    """Validate a parsed contract dict, returning list of error messages.

    Returns a list of error strings.  Empty list means valid.
    """
    errors = []

    for field, expected_type in REQUIRED_TOP_LEVEL.items():
        if field not in raw:
            errors.append(f"missing required field: {field}")
        elif not isinstance(raw[field], expected_type):
            errors.append(f"{field}: expected {expected_type.__name__}, got {type(raw[field]).__name__}")

    if errors:
        return errors

    if raw["status"] not in VALID_STATUSES:
        errors.append(f"status must be one of {sorted(VALID_STATUSES)}, got {raw['status']!r}")

    if raw["risk_tier"] not in VALID_RISK_TIERS:
        errors.append(f"risk_tier must be one of {sorted(VALID_RISK_TIERS)}, got {raw['risk_tier']!r}")

    scope = raw["workspace_scope"]
    if not isinstance(scope.get("allow"), list):
        errors.append("workspace_scope.allow must be a list")
    if not isinstance(scope.get("deny"), list):
        errors.append("workspace_scope.deny must be a list")
    if "scratch_dir" in scope and not isinstance(scope["scratch_dir"], str):
        errors.append("workspace_scope.scratch_dir must be a string")

    worker = raw["worker"]
    if not isinstance(worker.get("model"), str):
        errors.append("worker.model must be a string")
    if not isinstance(worker.get("max_attempts"), int) or worker["max_attempts"] < 1:
        errors.append("worker.max_attempts must be an int >= 1")

    if not isinstance(raw.get("inputs", []), list):
        errors.append("inputs must be a list")

    for idx, out in enumerate(raw["outputs"]):
        if not isinstance(out, dict) or "path" not in out:
            errors.append(f"outputs[{idx}]: each output must have a 'path' field")

    for idx, check in enumerate(raw["acceptance_checks"]):
        if not isinstance(check, dict):
            errors.append(f"acceptance_checks[{idx}]: must be an object")
            continue
        if "id" not in check:
            errors.append(f"acceptance_checks[{idx}]: missing 'id'")
        if check.get("kind") not in VALID_CHECK_KINDS:
            errors.append(
                f"acceptance_checks[{idx}]: kind must be one of {sorted(VALID_CHECK_KINDS)}, got {check.get('kind')!r}"
            )
        if check["kind"] in ("command", "content_exact", "content_regex") and "expected" not in check:
            errors.append(f"acceptance_checks[{idx}]: kind={check['kind']!r} requires 'expected' field")
        if check["kind"] == "min_size" and "expected" not in check:
            errors.append(f"acceptance_checks[{idx}]: kind='min_size' requires 'expected' field (minimum byte count)")

    qc = raw["qc"]
    if not isinstance(qc.get("required"), bool):
        errors.append("qc.required must be a bool")
    if qc.get("lens") not in VALID_QC_LENSES:
        errors.append(f"qc.lens must be one of {sorted(VALID_QC_LENSES)}, got {qc.get('lens')!r}")

    qs = raw.get("quality_spec", {})
    if qs:
        if not isinstance(qs, dict):
            errors.append("quality_spec must be a dict")
        else:
            if "required_sections" in qs and not isinstance(qs["required_sections"], list):
                errors.append("quality_spec.required_sections must be a list")
            if "quality_dimensions" in qs and not isinstance(qs["quality_dimensions"], dict):
                errors.append("quality_spec.quality_dimensions must be a dict")
            if "hard_failures" in qs and not isinstance(qs["hard_failures"], list):
                errors.append("quality_spec.hard_failures must be a list")
            if "minimum_counts" in qs and not isinstance(qs["minimum_counts"], dict):
                errors.append("quality_spec.minimum_counts must be a dict")
            if "minimum_score" in qs:
                ms = qs["minimum_score"]
                if not isinstance(ms, (int, float)) or ms < 0 or ms > 1:
                    errors.append("quality_spec.minimum_score must be a float between 0 and 1")
    elif raw.get("qc", {}).get("required", False):
        is_scratch = all(o.get("path", "").replace("\\", "/").startswith("scratch/") for o in raw.get("outputs", []))
        if not is_scratch:
            errors.append("qc.required is True but quality_spec is missing or empty (required for non-scratch tasks)")

    if "required_mcp_servers" in raw:
        rms = raw["required_mcp_servers"]
        if not isinstance(rms, list):
            errors.append("required_mcp_servers must be a list of strings")
        else:
            seen_servers = set()
            for idx, srv in enumerate(rms):
                if not isinstance(srv, str) or not srv.strip():
                    errors.append(f"required_mcp_servers[{idx}]: must be a non-empty string")
                elif srv in seen_servers:
                    errors.append(f"required_mcp_servers[{idx}]: duplicate server {srv!r}")
                else:
                    seen_servers.add(srv)

    if strict_unknown:
        known_keys = set(REQUIRED_TOP_LEVEL.keys()) | {
            "next_action",
            "inputs",
            "quality_spec",
            "quality_plan",
            "components",
            "required_mcp_servers",
        }
        extra = set(raw.keys()) - known_keys
        if extra:
            errors.append(f"unknown top-level keys: {sorted(extra)}")

    if "quality_plan" in raw and not isinstance(raw["quality_plan"], dict):
        errors.append("quality_plan must be a dict")

    if "components" in raw and not isinstance(raw["components"], list):
        errors.append("components must be a list")
    elif "components" in raw:
        seen_ids = set()
        for idx, comp in enumerate(raw["components"]):
            if not isinstance(comp, dict):
                errors.append(f"components[{idx}]: each component must be a dict")
                continue
            comp_id = comp.get("id")
            if not isinstance(comp_id, str) or not comp_id.strip():
                errors.append(f"components[{idx}]: missing non-empty 'id'")
            elif comp_id in seen_ids:
                errors.append(f"components[{idx}]: duplicate component id {comp_id!r}")
            else:
                seen_ids.add(comp_id)
            files = comp.get("files")
            if not isinstance(files, list) or not files:
                errors.append(f"components[{idx}]: 'files' must be a non-empty list of strings")
            elif not all(isinstance(f, str) and f.strip() for f in files):
                errors.append(f"components[{idx}]: every 'files' entry must be a non-empty string")
            if "description" in comp and not isinstance(comp["description"], str):
                errors.append(f"components[{idx}]: 'description' must be a string")

    if workspace_root:
        for path_entry in raw.get("inputs", []):
            if isinstance(path_entry, dict) and "path" in path_entry:
                ok, err = check_path_allowed(
                    path_entry["path"],
                    scope.get("allow", []),
                    scope.get("deny", []),
                    workspace_root,
                )
                if not ok:
                    errors.append(f"input path {path_entry['path']}: {err}")

        for path_entry in raw["outputs"]:
            if isinstance(path_entry, dict) and "path" in path_entry:
                ok, err = check_path_allowed(
                    path_entry["path"],
                    scope.get("allow", []),
                    scope.get("deny", []),
                    workspace_root,
                )
                if not ok:
                    errors.append(f"output path {path_entry['path']}: {err}")

    return errors


def load_contract(path, workspace_root=None):
    """Load and validate a contract JSON file.

    Returns (Contract, errors).  If errors is non-empty, Contract may be None.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        return None, [str(e)]

    errors = validate_contract(raw, workspace_root=workspace_root)
    if errors:
        return None, errors

    return Contract(raw, source_path=path), []

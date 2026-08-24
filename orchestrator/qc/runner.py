"""Strategy dispatch layer for the quality plane.

Extracted verbatim from the former flat orchestrator/quality_plane.py:
per-component reviewer dispatch, the run_quality_plane gateway (single /
panel / component-panel strategy selection with plan validation and
budget degradation), explicit-component validation, and deterministic
quality_spec gates for the legacy path.

Shared collaborators are resolved through the ``orchestrator.quality_plane``
compat namespace (``qp``) at call time so patches applied there stay live.
"""

from __future__ import annotations

import concurrent.futures
import time
from typing import Any, Dict, List, Optional

from orchestrator import quality_plane as qp

from ..component_slicer import ComponentSlice, slice_components
from ..provider_scheduler import CallSpec, ProviderScheduler
from ..qc_review import _build_qc_prompt, _select_qc_model
from ..quality_plan import (
    MODE_COMPONENT_PANEL,
    MODE_PANEL,
    MODE_SINGLE,
    QualityPlan,
    ReviewerRole,
    validate_quality_plan,
)
from ..review_artifact import (
    VERDICT_CONDITIONAL_PASS,
    VERDICT_ERROR,
    VERDICT_REJECT,
    QualityPlaneVerdict,
    ReviewArtifact,
)

_MAX_COMPONENT_WORKERS = 4


def _run_component_reviews(
    contract,
    components: List[ComponentSlice],
    verification_results: List[Dict[str, Any]],
    workspace_root: str,
    quality_plan: QualityPlan,
    scheduler: Optional[ProviderScheduler] = None,
    deadline: Optional[float] = None,
    max_workers: Optional[int] = None,
) -> List[ReviewArtifact]:
    """Dispatch reviewers per component, return artifacts.

    Phase 4: one reviewer per component (uses reviewers[0] role).
    Phase 5: up to max_reviewers_per_component reviewers, each with a
    different persona from the quality_plan's reviewer list.
    Phase 7: uses ProviderScheduler to respect per-provider concurrency.
    """
    quality_spec = getattr(contract, "quality_spec", {})

    reviewers = list(quality_plan.reviewers) if quality_plan.reviewers else [ReviewerRole("reviewer")]
    max_per_component = max(quality_plan.budget.max_reviewers_per_component, 1)
    active_reviewers = reviewers[:max_per_component]

    # Build call specs
    all_calls: List[CallSpec] = []
    call_map: dict = {}  # call_id -> (index, role, component, prompt)
    call_idx = 0
    for i, component in enumerate(components):
        base_prompt = _build_qc_prompt(
            contract,
            component.files,
            verification_results,
            quality_spec=quality_spec,
        )
        if getattr(component, "description", ""):
            base_prompt = base_prompt + (f"\n=== COMPONENT DESCRIPTION ===\n{component.description}\n")
        for role in active_reviewers:
            persona_prompt = qp._build_persona_prompt(base_prompt, role)
            call_id = f"comp_{i}_{role.role}_{call_idx}"
            all_calls.append(
                CallSpec(
                    call_id=call_id,
                    prompt=persona_prompt,
                    model=qp._resolve_reviewer_model(role),
                    reviewer_id=f"component_{i}_{role.role}",
                    role=role.role,
                    component_id=component.id,
                    component_files=component.files,
                )
            )
            call_map[call_id] = (i, role, component, persona_prompt)
            call_idx += 1

    # Schedule and execute waves (Phase 7: per-provider concurrency)
    scheduler = scheduler or ProviderScheduler()
    waves = scheduler.schedule(all_calls)

    artifacts: List[ReviewArtifact] = []
    if deadline is None:
        deadline = qp._deadline_for(quality_plan)

    max_workers = max_workers or _MAX_COMPONENT_WORKERS

    def _timeout_for_spec() -> Optional[int]:
        if deadline is None:
            return None
        # F-NEW-3: the wall-clock deadline must constrain the subprocess
        # itself — a call started at t=0.9 must not run the full 120s
        # default. Shrink the timeout to the remaining budget
        # (MIN_REVIEWER_TIMEOUT_SEC floor, 120s ceiling).
        return min(120, max(qp.MIN_REVIEWER_TIMEOUT_SEC, int(deadline - time.monotonic())))

    def _invoke_one(spec: CallSpec) -> ReviewArtifact:
        if deadline is not None and (deadline - time.monotonic()) < qp.MIN_REVIEWER_TIMEOUT_SEC:
            return qp._error_artifact(
                spec.reviewer_id,
                spec.role,
                spec.model,
                spec.component_id,
                "insufficient remaining wall clock budget",
            )
        try:
            raw = qp._invoke_reviewer(
                spec.prompt,
                spec.model,
                workspace_root,
                timeout_sec=_timeout_for_spec(),
            )
            return qp._raw_to_artifact(
                raw,
                reviewer_id=spec.reviewer_id,
                role=spec.role,
                model=spec.model,
                component_id=spec.component_id,
                component_files=spec.component_files,
            )
        except Exception:
            return qp._error_artifact(
                spec.reviewer_id,
                spec.role,
                spec.model,
                spec.component_id,
                "reviewer invocation failure",
            )

    # Phase 7: calls grouped into waves by ProviderScheduler (per-provider
    # concurrency). Within a wave, run independent reviewer invocations
    # concurrently (bounded by _MAX_COMPONENT_WORKERS), then collect results
    # in deterministic spec order so aggregation/arbitration is stable.
    for wave in waves:
        order = list(wave.calls)
        results: Dict[str, ReviewArtifact] = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {spec.call_id: pool.submit(_invoke_one, spec) for spec in wave.calls}
            for call_id, future in futures.items():
                try:
                    results[call_id] = future.result()
                except Exception:
                    spec = next(s for s in wave.calls if s.call_id == call_id)
                    results[call_id] = qp._error_artifact(
                        spec.reviewer_id,
                        spec.role,
                        spec.model,
                        spec.component_id,
                        "reviewer invocation failure",
                    )
        artifacts.extend(results[spec.call_id] for spec in order)

    return artifacts


# ── Public API ────────────────────────────────────────────────────────────


def _apply_quality_spec_gates(verdict, quality_spec):
    """Enforce deterministic quality_spec gates on a legacy-path verdict.

    NEW-4: synthesize_artifacts enforces hard_failures/minimum_score/
    minimum_counts for panel/component dispatch, but a budget-degraded plan
    routes to legacy run_qc_review which never synthesizes. Apply the same
    fail-closed gates here so degradation cannot bypass them.
    """
    if not isinstance(quality_spec, dict) or not quality_spec:
        return verdict
    issues = verdict.issues if isinstance(verdict.issues, list) else []

    hard = quality_spec.get("hard_failures") or []
    if isinstance(hard, list):
        for hf in hard:
            if (
                isinstance(hf, str)
                and hf
                and any(
                    hf.lower() in str(i.get("description") or i.get("claim") or "").lower()
                    or hf.lower() in str(i.get("path") or "").lower()
                    for i in issues
                    if isinstance(i, dict)
                )
            ):
                verdict.passed = False
                verdict.status = VERDICT_REJECT
                verdict.reason = f"quality_spec hard failure: {hf}"
                return verdict

    try:
        min_score = float(quality_spec.get("minimum_score", 0.0) or 0.0)
    except (TypeError, ValueError, OverflowError):
        min_score = 0.0
    min_counts = quality_spec.get("minimum_counts") or {}
    if not isinstance(min_counts, dict):
        min_counts = {}
    try:
        required_issues = int(min_counts.get("issues", 0) or 0)
    except (TypeError, ValueError):
        required_issues = 0

    unmet = []
    if min_score > 0 and verdict.score < min_score:
        unmet.append(f"score {verdict.score:.2f} below minimum_score {min_score}")
    if required_issues > 0 and len(issues) < required_issues:
        unmet.append(f"issues {len(issues)} below minimum_counts.issues {required_issues}")
    if unmet:
        # Downgrade PASS to CONDITIONAL_PASS — same semantics as synthesis.
        verdict.passed = False
        verdict.status = VERDICT_CONDITIONAL_PASS
        verdict.reason = "; ".join(unmet)
    return verdict


def run_quality_plane(
    contract,
    output_paths: List[str],
    verification_results: List[Dict[str, Any]],
    workspace_root: str,
    quality_plan=None,
) -> QualityPlaneVerdict:
    """Evaluate output quality. Gateway for all quality review paths.

    Phase 1: delegates to legacy run_qc_review(), wraps result in
    QualityPlaneVerdict. Phase 4: when quality_plan.mode is
    COMPONENT_PANEL, uses component slicing dispatch.

    Returns a QualityPlaneVerdict with fields matching what supervisor
    expects: .passed, .status, .score, .issues, .reason, .to_dict().
    """
    qp_ = quality_plan
    if qp_ is not None:
        errors = validate_quality_plan(qp_)
        if errors:
            return QualityPlaneVerdict(
                passed=False,
                status=VERDICT_ERROR,
                reason="invalid quality plan: " + "; ".join(errors),
                budget_used={"llm_calls": 0, "elapsed_sec": 0.0},
            )
        # Gateway-authoritative budget: degrade here so direct callers get
        # the same protection as the supervisor path (F3).
        max_calls = qp_.budget.max_llm_calls
        if max_calls is not None and qp_.estimate_calls() > max_calls:
            _pre_degrade_mode = qp_.mode
            qp_ = qp_.degraded_copy()
            # An EXPLICIT panel/component-panel plan
            # must not silently degrade at all — that is a mandatory-panel
            # downgrade. Hard block instead.
            if (
                qp_.degraded
                and _pre_degrade_mode in (MODE_PANEL, MODE_COMPONENT_PANEL)
                and getattr(contract, "quality_plan", None)
            ):
                return QualityPlaneVerdict(
                    passed=False,
                    status=VERDICT_ERROR,
                    reason=(
                        "mandatory QC panel cannot be degraded "
                        f"({_pre_degrade_mode} -> {qp_.mode}; budget {max_calls} insufficient: {qp_.degrade_reason})"
                    ),
                    budget_used={"llm_calls": 0, "elapsed_sec": 0.0},
                )

    # F-NEW-6: the effective (possibly degraded) plan state must reach the
    # emitted verdict — a silent degradation to legacy single-reviewer QC
    # must be auditable.
    degraded = bool(getattr(qp_, "degraded", False)) if qp_ is not None else False
    degrade_reason = getattr(qp_, "degrade_reason", "") if qp_ is not None else ""

    if qp_ is not None and qp_.mode == MODE_COMPONENT_PANEL:
        verdict = _run_component_quality_plane(
            contract,
            output_paths,
            verification_results,
            workspace_root,
            qp_,
        )
    elif qp_ is not None and qp_.mode == MODE_PANEL:
        verdict = _run_panel_quality_plane(
            contract,
            output_paths,
            verification_results,
            workspace_root,
            qp_,
        )
    elif qp_ is not None and qp_.mode != MODE_SINGLE:
        # Any other non-None mode is an unsupported configuration error:
        # never silently fall back to legacy QC (F1).
        return QualityPlaneVerdict(
            passed=False,
            status=VERDICT_ERROR,
            reason=f"unsupported mode: {qp_.mode}",
            budget_used={"llm_calls": 0, "elapsed_sec": 0.0},
        )
    else:
        # Legacy path (SINGLE or no plan)
        qc_result = qp.run_qc_review(contract, output_paths, verification_results, workspace_root)
        qc_model = _select_qc_model(contract)
        worker_model = contract.worker.get("model", "unknown") if hasattr(contract, "worker") else "unknown"

        verdict = QualityPlaneVerdict.from_qc_verdict(qc_result)
        verdict.add_model_metadata(qc_model=qc_model, worker_model=worker_model)
        # NEW-4: a budget-degraded plan can route to legacy single QC, which
        # never runs synthesize_artifacts — enforce the deterministic
        # quality_spec gates here so degradation cannot bypass them.
        _apply_quality_spec_gates(verdict, getattr(contract, "quality_spec", None))

    verdict.degraded = degraded
    verdict.degrade_reason = degrade_reason
    return verdict


def _run_panel_quality_plane(
    contract,
    output_paths: List[str],
    verification_results: List[Dict[str, Any]],
    workspace_root: str,
    quality_plan: QualityPlan,
) -> QualityPlaneVerdict:
    """Run the panel review path: the full output set as one component,
    reviewed by up to max_reviewers_per_component persona reviewers.

    Phase 5 semantics: distinct personas over the same files, synthesis,
    and optional arbitration on disagreement.
    """
    scheduler = ProviderScheduler()

    component = ComponentSlice(id="component_0", files=list(output_paths))

    deadline = qp._deadline_for(quality_plan)
    artifacts = _run_component_reviews(
        contract,
        [component],
        verification_results,
        workspace_root,
        quality_plan,
        scheduler=scheduler,
        deadline=deadline,
    )

    return qp._component_verdict_to_qp_verdict(
        artifacts,
        [component],
        quality_plan,
        workspace_root,
        scheduler=scheduler,
        deadline=deadline,
        quality_spec=getattr(contract, "quality_spec", {}),
    )


def _validate_explicit_components(
    declared_components,
    output_paths,
    workspace_root,
    max_components,
):
    """Validate explicit components as an exact, workspace-confined output partition.

    Returns (list[ComponentSlice], list[str] errors). On any error the slices
    list is empty (fail-closed) so callers cannot proceed with a partial or
    escaping partition. Confinement uses realpath + commonpath against the
    workspace root and is independent of contract scope allow-lists.
    """
    import os

    errors = []
    if not isinstance(declared_components, list) or not declared_components:
        return [], ["explicit components must be a non-empty list"]

    if not isinstance(max_components, int) or max_components < 1:
        return [], ["max_components must be a positive integer"]

    if len(declared_components) > max_components:
        return [], [f"explicit component count {len(declared_components)} exceeds max_components {max_components}"]

    canonical_workspace_root = os.path.normcase(os.path.realpath(workspace_root))

    def canonicalize_and_confine(path, label):
        try:
            canonical_path = os.path.normcase(os.path.realpath(path))
            if os.path.commonpath([canonical_path, canonical_workspace_root]) != canonical_workspace_root:
                errors.append(f"{label} outside workspace root: {path!r}")
                return None
            return canonical_path
        except ValueError:
            errors.append(f"{label} cannot compare with workspace root (different drives or invalid path): {path!r}")
            return None

    canonical_outputs = {}
    for output_path in output_paths:
        canonical_output = canonicalize_and_confine(
            output_path,
            "output path",
        )
        if canonical_output is None:
            continue
        if canonical_output in canonical_outputs:
            errors.append(f"duplicate declared output after canonicalization: {output_path!r}")
            continue
        canonical_outputs[canonical_output] = output_path

    if errors:
        return [], errors

    seen_component_ids = set()
    seen_files = {}
    slices = []

    for index, component in enumerate(declared_components):
        if not isinstance(component, dict):
            errors.append(f"component {index} must be a dict")
            continue

        component_id = component.get("id")
        files = component.get("files")
        description = component.get("description", "")

        if not isinstance(component_id, str) or not component_id.strip():
            errors.append(f"component {index} has invalid id")
            continue
        if component_id in seen_component_ids:
            errors.append(f"duplicate component id: {component_id!r}")
            continue
        seen_component_ids.add(component_id)

        if not isinstance(files, list) or not files:
            errors.append(f"component {component_id!r} must declare a non-empty files list")
            continue
        if not isinstance(description, str):
            errors.append(f"component {component_id!r} description must be a string")
            continue

        slice_files = []
        component_seen = set()
        for declared_file in files:
            if not isinstance(declared_file, str) or not declared_file:
                errors.append(f"component {component_id!r} contains an invalid file path")
                continue

            resolved_file = (
                declared_file if os.path.isabs(declared_file) else os.path.join(workspace_root, declared_file)
            )
            canonical_file = canonicalize_and_confine(
                resolved_file,
                f"component {component_id!r} file",
            )
            if canonical_file is None:
                continue

            if canonical_file not in canonical_outputs:
                errors.append(f"component {component_id!r} references unknown output: {declared_file!r}")
                continue

            if canonical_file in component_seen:
                errors.append(f"component {component_id!r} repeats file: {declared_file!r}")
                continue
            component_seen.add(canonical_file)

            previous_component = seen_files.get(canonical_file)
            if previous_component is not None:
                errors.append(
                    f"output {canonical_outputs[canonical_file]!r} appears in both "
                    f"{previous_component!r} and {component_id!r}"
                )
                continue

            seen_files[canonical_file] = component_id
            slice_files.append(canonical_outputs[canonical_file])

        if slice_files:
            slices.append(
                ComponentSlice(
                    id=component_id,
                    files=slice_files,
                    description=description,
                )
            )
        else:
            errors.append(f"component {component_id!r} is empty")

    missing_outputs = [
        output_path for canonical_output, output_path in canonical_outputs.items() if canonical_output not in seen_files
    ]
    if missing_outputs:
        errors.append("explicit components omit outputs: " + ", ".join(repr(path) for path in missing_outputs))

    if errors:
        return [], errors
    return slices, []


def _run_component_quality_plane(
    contract,
    output_paths: List[str],
    verification_results: List[Dict[str, Any]],
    workspace_root: str,
    quality_plan: QualityPlan,
) -> QualityPlaneVerdict:
    """Run the component-sliced quality review path."""
    scheduler = ProviderScheduler()

    if quality_plan.components == "explicit":
        declared_components = getattr(contract, "components", None)
        if not declared_components:
            return QualityPlaneVerdict(
                status=VERDICT_ERROR,
                passed=False,
                reason="invalid explicit components: contract declares no components",
                issues=["contract declares no components for components=explicit"],
            )
        components, component_errors = _validate_explicit_components(
            declared_components,
            output_paths,
            workspace_root,
            quality_plan.budget.max_components,
        )
        if component_errors:
            return QualityPlaneVerdict(
                status=VERDICT_ERROR,
                passed=False,
                reason="invalid explicit components: " + "; ".join(component_errors),
                issues=component_errors,
            )
    else:
        components = slice_components(
            output_paths,
            max_components=quality_plan.budget.max_components,
            base_dir=workspace_root,
        )

    deadline = qp._deadline_for(quality_plan)
    artifacts = _run_component_reviews(
        contract,
        components,
        verification_results,
        workspace_root,
        quality_plan,
        scheduler=scheduler,
        deadline=deadline,
    )

    return qp._component_verdict_to_qp_verdict(
        artifacts,
        components,
        quality_plan,
        workspace_root,
        scheduler=scheduler,
        deadline=deadline,
        quality_spec=getattr(contract, "quality_spec", {}),
    )

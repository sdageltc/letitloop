"""orchestrator.qc — quality-plane package.

Extracted from the former flat orchestrator/quality_plane.py (~1350 lines)
as a pure structural refactor with an identical public surface:

  personas    reviewer invocation transport, persona prompts, typed ERROR
              artifacts, model-policy resolution
  parsing     strict raw-response -> ReviewArtifact normalization, path and
              evidence normalization, plan wall-clock deadlines
  aggregator  artifact synthesis into QualityPlaneVerdict, component
              ranking, arbitration policy/prompt/execution
  runner      run_quality_plane strategy gateway (single / panel /
              component-panel), reviewer dispatch, explicit-component
              validation, quality_spec gates

QualityPlane is the only NEW symbol: a facade over the runner entrypoints.
Backward compatibility flows through orchestrator.quality_plane, which
lazily resolves names into this package while remaining the live patch
target for monkeypatched tests.
"""

from typing import Any, Dict, List

from .aggregator import (
    _build_arbitration_prompt,
    _component_verdict_to_qp_verdict,
    _run_arbitration,
    _should_arbitrate,
    _synthesis_reason,
)
from .parsing import (
    _deadline_for,
    _norm_path,
    _normalize_issue_evidence,
    _parse_reviewer_response,
    _raw_to_arbitration_verdict,
    _raw_to_artifact,
)
from .personas import (
    _PAID_MODEL_MARKERS,
    _REVIEWER_HOOK,
    MIN_REVIEWER_TIMEOUT_SEC,
    _build_persona_prompt,
    _error_artifact,
    _extract_prompt_paths,
    _fake_raw_response,
    _invoke_reviewer,
    _resolve_arbitration_model,
    _resolve_reviewer_model,
)
from .runner import (
    _MAX_COMPONENT_WORKERS,
    _apply_quality_spec_gates,
    _run_component_quality_plane,
    _run_component_reviews,
    _run_panel_quality_plane,
    _validate_explicit_components,
    run_quality_plane,
)


class QualityPlane:
    """Facade over the quality-plane runner entrypoints.

    Provided for backward compatibility and object-shaped access to the
    review strategies; it adds no behavior of its own and delegates every
    call to the module-level functions in orchestrator.qc.runner. New code
    may use either this facade or the runner entrypoints directly.
    """

    @staticmethod
    def run(
        contract,
        output_paths: List[str],
        verification_results: List[Dict[str, Any]],
        workspace_root: str,
        quality_plan=None,
    ):
        """Evaluate output quality via run_quality_plane (any plan mode)."""
        return run_quality_plane(
            contract,
            output_paths,
            verification_results,
            workspace_root,
            quality_plan=quality_plan,
        )

    @staticmethod
    def run_panel(
        contract,
        output_paths: List[str],
        verification_results: List[Dict[str, Any]],
        workspace_root: str,
        quality_plan,
    ):
        """Force the multi-persona panel review path."""
        return _run_panel_quality_plane(
            contract,
            output_paths,
            verification_results,
            workspace_root,
            quality_plan,
        )

    @staticmethod
    def run_component(
        contract,
        output_paths: List[str],
        verification_results: List[Dict[str, Any]],
        workspace_root: str,
        quality_plan,
    ):
        """Force the component-sliced panel review path."""
        return _run_component_quality_plane(
            contract,
            output_paths,
            verification_results,
            workspace_root,
            quality_plan,
        )


__all__ = [
    "MIN_REVIEWER_TIMEOUT_SEC",
    "QualityPlane",
    "run_quality_plane",
    "_MAX_COMPONENT_WORKERS",
    "_PAID_MODEL_MARKERS",
    "_REVIEWER_HOOK",
    "_apply_quality_spec_gates",
    "_build_arbitration_prompt",
    "_build_persona_prompt",
    "_component_verdict_to_qp_verdict",
    "_deadline_for",
    "_error_artifact",
    "_extract_prompt_paths",
    "_fake_raw_response",
    "_invoke_reviewer",
    "_norm_path",
    "_normalize_issue_evidence",
    "_parse_reviewer_response",
    "_raw_to_arbitration_verdict",
    "_raw_to_artifact",
    "_resolve_arbitration_model",
    "_resolve_reviewer_model",
    "_run_arbitration",
    "_run_component_quality_plane",
    "_run_component_reviews",
    "_run_panel_quality_plane",
    "_should_arbitrate",
    "_synthesis_reason",
    "_validate_explicit_components",
]

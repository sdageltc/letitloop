"""Quality plane — backward-compat facade over the ``orchestrator.qc`` package.

Phased evolution:
  Phase 1: thin wrapper over legacy run_qc_review(), no behavioral change.
  Phase 4: component slicing dispatch with per-component reviewers.
  Phase 5+: multi-agent panels, arbitration, provider scheduling.
  Refactor: this flat module was split into the orchestrator.qc package
  (personas / parsing / aggregator / runner) with an identical public
  surface; QualityPlane there is the facade over the runner entrypoints.

Every symbol that was previously importable from
``orchestrator.quality_plane`` still resolves here, and monkeypatching
names on THIS module remains effective because the qc submodules resolve
their shared collaborators through this namespace at call time. New code
should import from ``orchestrator.qc`` directly.
"""

from __future__ import annotations

import concurrent.futures  # noqa: F401  (historical attribute surface)
import json  # noqa: F401
import math  # noqa: F401
import os  # noqa: F401
import re  # noqa: F401
import time  # noqa: F401
from typing import Any, Callable, Dict, List, Optional  # noqa: F401

_HOME = {
    "MIN_REVIEWER_TIMEOUT_SEC": "orchestrator.qc.personas",
    "_PAID_MODEL_MARKERS": "orchestrator.qc.personas",
    "_REVIEWER_HOOK": "orchestrator.qc.personas",
    "_build_persona_prompt": "orchestrator.qc.personas",
    "_error_artifact": "orchestrator.qc.personas",
    "_extract_prompt_paths": "orchestrator.qc.personas",
    "_fake_raw_response": "orchestrator.qc.personas",
    "_invoke_reviewer": "orchestrator.qc.personas",
    "_resolve_arbitration_model": "orchestrator.qc.personas",
    "_resolve_reviewer_model": "orchestrator.qc.personas",
    "_deadline_for": "orchestrator.qc.parsing",
    "_norm_path": "orchestrator.qc.parsing",
    "_normalize_issue_evidence": "orchestrator.qc.parsing",
    "_parse_reviewer_response": "orchestrator.qc.parsing",
    "_raw_to_arbitration_verdict": "orchestrator.qc.parsing",
    "_raw_to_artifact": "orchestrator.qc.parsing",
    "_build_arbitration_prompt": "orchestrator.qc.aggregator",
    "_component_verdict_to_qp_verdict": "orchestrator.qc.aggregator",
    "_run_arbitration": "orchestrator.qc.aggregator",
    "_should_arbitrate": "orchestrator.qc.aggregator",
    "_synthesis_reason": "orchestrator.qc.aggregator",
    "_MAX_COMPONENT_WORKERS": "orchestrator.qc.runner",
    "_apply_quality_spec_gates": "orchestrator.qc.runner",
    "_run_component_quality_plane": "orchestrator.qc.runner",
    "_run_component_reviews": "orchestrator.qc.runner",
    "_run_panel_quality_plane": "orchestrator.qc.runner",
    "_validate_explicit_components": "orchestrator.qc.runner",
    "run_quality_plane": "orchestrator.qc.runner",
    "QualityPlane": "orchestrator.qc",
    "ComponentSlice": "orchestrator.component_slicer",
    "slice_components": "orchestrator.component_slicer",
    "LLMError": "orchestrator.llm",
    "call_llm": "orchestrator.llm",
    "ModelRegistry": "orchestrator.models",
    "CallSpec": "orchestrator.provider_scheduler",
    "ProviderScheduler": "orchestrator.provider_scheduler",
    "_build_qc_prompt": "orchestrator.qc_review",
    "_redact_secrets": "orchestrator.qc_review",
    "_select_qc_model": "orchestrator.qc_review",
    "run_qc_review": "orchestrator.qc_review",
    "MODE_COMPONENT_PANEL": "orchestrator.quality_plan",
    "MODE_PANEL": "orchestrator.quality_plan",
    "MODE_SINGLE": "orchestrator.quality_plan",
    "PERSONA_DESCRIPTIONS": "orchestrator.quality_plan",
    "QualityPlan": "orchestrator.quality_plan",
    "ReviewerRole": "orchestrator.quality_plan",
    "validate_quality_plan": "orchestrator.quality_plan",
    "ArbitrationVerdict": "orchestrator.review_artifact",
    "EvidenceRead": "orchestrator.review_artifact",
    "QualityPlaneVerdict": "orchestrator.review_artifact",
    "ReviewArtifact": "orchestrator.review_artifact",
    "ReviewIssue": "orchestrator.review_artifact",
    "VERDICT_CONDITIONAL_PASS": "orchestrator.review_artifact",
    "VERDICT_ERROR": "orchestrator.review_artifact",
    "VERDICT_INSUFFICIENT_EVIDENCE": "orchestrator.review_artifact",
    "VERDICT_PASS": "orchestrator.review_artifact",
    "VERDICT_REJECT": "orchestrator.review_artifact",
    "synthesize_artifacts": "orchestrator.review_artifact",
}


def __getattr__(name):
    try:
        home = _HOME[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    import importlib

    value = getattr(importlib.import_module(home), name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(_HOME))

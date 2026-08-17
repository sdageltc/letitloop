"""Quality Plan schema and defaults for the orchestrator quality plane.

A QualityPlan defines *how* a task's output should be reviewed — including
lens, reviewers, component slicing, synthesis, arbitration, and budget.

This module is pure schema + logic. Zero model calls. Zero side effects.
"""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional


# ── Mode constants ──────────────────────────────────────────────────────────

MODE_SINGLE = "single"
MODE_PANEL = "panel"
MODE_COMPONENT_PANEL = "component_panel"
MODE_ARBITRATION_ONLY = "arbitration_only"  # reserved; unsupported (see validate_quality_plan)
VALID_MODES = {MODE_SINGLE, MODE_PANEL, MODE_COMPONENT_PANEL}

# ── Lens constants ─────────────────────────────────────────────────────────

LENS_CODE_CORRECTNESS = "code_correctness"
LENS_PLAN_CORRECTNESS = "plan_correctness"
LENS_CONFIG_SAFETY = "config_safety"
LENS_CONTENT_QUALITY = "content_quality"
LENS_DOCUMENT_QUALITY = "document_quality"
LENS_ARCHITECTURE_AUDIT = "architecture_audit"
LENS_RESEARCH_QUALITY = "research_quality"
LENS_STRATEGIC_REVIEW = "strategic_review"
LENS_MIGRATION_SAFETY = "migration_safety"
VALID_LENSES = {
    LENS_CODE_CORRECTNESS,
    LENS_PLAN_CORRECTNESS,
    LENS_CONFIG_SAFETY,
    LENS_CONTENT_QUALITY,
    LENS_DOCUMENT_QUALITY,
    LENS_ARCHITECTURE_AUDIT,
    LENS_RESEARCH_QUALITY,
    LENS_STRATEGIC_REVIEW,
    LENS_MIGRATION_SAFETY,
}

# ── Default personas by lens ───────────────────────────────────────────────

LENS_DEFAULT_PERSONAS: Dict[str, List[str]] = {
    LENS_CODE_CORRECTNESS: ["maintainer", "test_strategist"],
    LENS_CONFIG_SAFETY: ["security_safety", "operator_sre"],
    LENS_ARCHITECTURE_AUDIT: ["systems_architect", "minimalist", "product_owner"],
    LENS_PLAN_CORRECTNESS: ["systems_architect", "operator_sre"],
    LENS_RESEARCH_QUALITY: ["research_skeptic", "product_owner"],
    LENS_MIGRATION_SAFETY: ["migration_reviewer", "operator_sre", "maintainer"],
    LENS_STRATEGIC_REVIEW: ["product_owner", "minimalist", "systems_architect"],
    LENS_CONTENT_QUALITY: ["maintainer"],
    LENS_DOCUMENT_QUALITY: ["maintainer"],
}

PERSONA_DESCRIPTIONS: Dict[str, str] = {
    "systems_architect": "contradictions, overengineering, failure modes",
    "maintainer": "complexity, testability, local implementation risk",
    "security_safety": "destructive actions, secrets, permissions, unsafe commands",
    "product_owner": "whether the work solves the actual problem",
    "operator_sre": "runtime, recovery, observability, rollback",
    "minimalist": "simpler design alternatives",
    "adversarial_user": "how can this fail in real usage",
    "research_skeptic": "citation quality, source fidelity, unsupported claims",
    "migration_reviewer": "backward compatibility, sequencing, rollback",
    "test_strategist": "coverage holes and falsifiability",
}

# ── Default mode by lens and risk tier ─────────────────────────────────────

RISK_TIER_AUTO = "auto"
RISK_TIER_QC_REQUIRED = "qc_required"
RISK_TIER_HUMAN_REQUIRED = "human_required"

DEFAULT_MODE_BY_RISK_AND_LENS: Dict[str, Dict[str, str]] = {
    RISK_TIER_AUTO: {
        "__default__": MODE_SINGLE,
        LENS_ARCHITECTURE_AUDIT: MODE_PANEL,
        LENS_STRATEGIC_REVIEW: MODE_PANEL,
        LENS_MIGRATION_SAFETY: MODE_PANEL,
    },
    RISK_TIER_QC_REQUIRED: {
        "__default__": MODE_SINGLE,
    },
    RISK_TIER_HUMAN_REQUIRED: {
        "__default__": MODE_PANEL,
    },
}

REVIEWER_MODEL_POLICY: Dict[str, str] = {
    "systems_architect": "default",
    "maintainer": "default",
    "security_safety": "default",
    "product_owner": "default",
    "operator_sre": "default",
    "minimalist": "cheap_cross_family",
    "adversarial_user": "default",
    "research_skeptic": "default",
    "migration_reviewer": "default",
    "test_strategist": "cheap_cross_family",
}


class ReviewerRole:
    def __init__(self, role: str, model_policy: str = "", focus: Optional[List[str]] = None):
        self.role = role
        self.model_policy = model_policy or REVIEWER_MODEL_POLICY.get(role, "default")
        self.focus = focus or []

    def to_dict(self) -> Dict[str, Any]:
        return {
            "role": self.role,
            "model_policy": self.model_policy,
            "focus": list(self.focus),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> ReviewerRole:
        if not isinstance(d, dict):
            d = {}
        role = d.get("role", "reviewer")
        if not isinstance(role, str) or not role:
            role = "reviewer"
        model_policy = d.get("model_policy", "")
        if not isinstance(model_policy, str):
            model_policy = ""
        focus = d.get("focus", [])
        if not isinstance(focus, list):
            focus = []
        focus = [f for f in focus if isinstance(f, str)]
        return cls(
            role=role,
            model_policy=model_policy,
            focus=focus,
        )


class SynthesisPolicy:
    def __init__(
        self,
        required: bool = True,
        preserve_dissent: bool = True,
        reject_on_p0: bool = True,
    ):
        self.required = required
        self.preserve_dissent = preserve_dissent
        self.reject_on_p0 = reject_on_p0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "required": self.required,
            "preserve_dissent": self.preserve_dissent,
            "reject_on_p0": self.reject_on_p0,
        }

    @classmethod
    def from_dict(cls, d: dict) -> SynthesisPolicy:
        if not isinstance(d, dict):
            d = {}
        return cls(
            required=d.get("required", True),
            preserve_dissent=d.get("preserve_dissent", True),
            reject_on_p0=d.get("reject_on_p0", True),
        )


class ArbitrationPolicy:
    def __init__(
        self,
        enabled: bool = False,
        trigger: Optional[List[str]] = None,
        model_policy: str = "default",
    ):
        self.enabled = enabled
        self.trigger = trigger or ["p0_disagreement", "low_confidence"]
        self.model_policy = model_policy

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "trigger": list(self.trigger),
            "model_policy": self.model_policy,
        }

    @classmethod
    def from_dict(cls, d: dict) -> ArbitrationPolicy:
        if not isinstance(d, dict):
            d = {}
        return cls(
            enabled=d.get("enabled", False),
            trigger=d.get("trigger"),
            model_policy=d.get("model_policy", "default"),
        )


class QualityBudget:
    def __init__(
        self,
        max_llm_calls: int = 8,
        max_wall_clock_sec: int = 900,
        max_reviewers_per_component: int = 3,
        max_components: int = 5,
        degrade_strategy: str = "single_reviewer_then_synthesis",
    ):
        self.max_llm_calls = max_llm_calls
        self.max_wall_clock_sec = max_wall_clock_sec
        self.max_reviewers_per_component = max_reviewers_per_component
        self.max_components = max_components
        self.degrade_strategy = degrade_strategy

    def to_dict(self) -> Dict[str, Any]:
        return {
            "max_llm_calls": self.max_llm_calls,
            "max_wall_clock_sec": self.max_wall_clock_sec,
            "max_reviewers_per_component": self.max_reviewers_per_component,
            "max_components": self.max_components,
            "degrade_strategy": self.degrade_strategy,
        }

    @classmethod
    def from_dict(cls, d: dict) -> QualityBudget:
        if not isinstance(d, dict):
            d = {}
        return cls(
            max_llm_calls=d.get("max_llm_calls", 8),
            max_wall_clock_sec=d.get("max_wall_clock_sec", 900),
            max_reviewers_per_component=d.get("max_reviewers_per_component", 3),
            max_components=d.get("max_components", 5),
            degrade_strategy=d.get("degrade_strategy", "single_reviewer_then_synthesis"),
        )


class QualityPlan:
    """Defines how output quality is evaluated for a task contract.

    A QualityPlan is optional. If absent, the orchestrator uses the legacy
    single-reviewer QC path with no behavioral change.
    """

    def __init__(
        self,
        mode: str = MODE_SINGLE,
        lens: str = LENS_CODE_CORRECTNESS,
        components: str = "auto",
        reviewers: Optional[List[ReviewerRole]] = None,
        synthesis: Optional[SynthesisPolicy] = None,
        arbitration: Optional[ArbitrationPolicy] = None,
        budget: Optional[QualityBudget] = None,
    ):
        self.mode = mode
        self.lens = lens
        self.components = components
        self.reviewers = reviewers or []
        self.synthesis = synthesis or SynthesisPolicy()
        self.arbitration = arbitration or ArbitrationPolicy()
        self.budget = budget or QualityBudget()
        self.degraded = False
        self.degrade_reason = ""
        self.original_mode = mode

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "lens": self.lens,
            "components": self.components,
            "reviewers": [r.to_dict() for r in self.reviewers],
            "synthesis": self.synthesis.to_dict(),
            "arbitration": self.arbitration.to_dict(),
            "budget": self.budget.to_dict(),
            "degraded": self.degraded,
            "degrade_reason": self.degrade_reason,
            "original_mode": self.original_mode,
        }

    @classmethod
    def from_dict(cls, d: dict) -> QualityPlan:
        if not isinstance(d, dict):
            d = {}
        reviewers_raw = d.get("reviewers", [])
        if not isinstance(reviewers_raw, list):
            reviewers_raw = []
        reviewers = [ReviewerRole.from_dict(r) for r in reviewers_raw if isinstance(r, dict)]
        qp = cls(
            mode=d.get("mode", MODE_SINGLE),
            lens=d.get("lens", LENS_CODE_CORRECTNESS),
            components=d.get("components", "auto"),
            reviewers=reviewers,
            synthesis=SynthesisPolicy.from_dict(d.get("synthesis", {})),
            arbitration=ArbitrationPolicy.from_dict(d.get("arbitration", {})),
            budget=QualityBudget.from_dict(d.get("budget", {})),
        )
        qp.degraded = bool(d.get("degraded", False))
        qp.degrade_reason = d.get("degrade_reason", "") if isinstance(d.get("degrade_reason", ""), str) else ""
        qp.original_mode = d.get("original_mode", qp.mode)
        return qp

    def estimate_calls(self) -> int:
        """Estimate worst-case LLM calls before dispatch.

        Counts only actual model invocations: reviewer calls plus one
        optional arbitration call. Local synthesis is NOT an LLM call.
        Used by budget enforcement to decide whether to degrade.
        """
        if self.mode == MODE_SINGLE:
            return 1  # legacy run_qc_review executes exactly one LLM call

        n_reviewers = max(len(self.reviewers), 1)
        n_components = 1
        if self.mode == MODE_COMPONENT_PANEL:
            n_components = min(
                self.budget.max_components,
                5,
            )

        calls = n_components * n_reviewers
        if self.arbitration.enabled:
            calls += 1
        return calls

    def degraded_copy(self) -> QualityPlan:
        """Return a degraded version that respects budget caps.

        Least-destructive ladder (perpetual-loop r2): drop arbitration first,
        then component_panel -> panel -> single, then trim reviewers. Records
        original_mode and degrade_reason for traceability.
        """
        qp = copy.deepcopy(self)
        qp.original_mode = qp.mode
        reasons = []

        while True:
            estimated = qp.estimate_calls()
            max_calls = qp.budget.max_llm_calls
            if max_calls is None or estimated <= max_calls:
                break
            if qp.arbitration.enabled:
                qp.arbitration.enabled = False
                reasons.append("dropped_arbitration")
                continue
            if qp.reviewers and len(qp.reviewers) > 1:
                qp.reviewers = qp.reviewers[:-1]
                reasons.append("trimmed_reviewer")
                continue
            if qp.mode == MODE_COMPONENT_PANEL:
                qp.mode = MODE_PANEL
                reasons.append("component_panel→panel")
                continue
            if qp.mode == MODE_PANEL:
                qp.mode = MODE_SINGLE
                qp.reviewers = qp.reviewers[:1] if qp.reviewers else []
                reasons.append("panel→single")
                continue
            break

        if reasons:
            qp.degraded = True
            qp.degrade_reason = "; ".join(reasons)
        return qp


# ── Convenience factory ────────────────────────────────────────────────────

def quality_plan_for_contract(
    risk_tier: str,
    qc_lens: str,
    quality_spec: Optional[Dict[str, Any]] = None,
) -> QualityPlan:
    """Build a default QualityPlan from contract-level fields.

    Mirrors the existing logic: code_correctness lenses get single QC,
    architecture_audit gets panel mode. Respects risk_tier escalation.
    """
    tier_map = DEFAULT_MODE_BY_RISK_AND_LENS.get(risk_tier, DEFAULT_MODE_BY_RISK_AND_LENS[RISK_TIER_AUTO])
    mode = tier_map.get(qc_lens, tier_map.get("__default__", MODE_SINGLE))

    # If quality_spec has minimum_counts (adversarial audit), assume panel
    qs = quality_spec or {}
    if qs.get("minimum_counts") and mode == MODE_SINGLE:
        mode = MODE_PANEL

    reviewers = []
    default_personas = LENS_DEFAULT_PERSONAS.get(qc_lens, [])
    for persona in default_personas:
        reviewers.append(ReviewerRole(role=persona))

    # Arbitration only for human_required or strategic lenses
    arbitration_enabled = risk_tier == RISK_TIER_HUMAN_REQUIRED or qc_lens in (
        LENS_STRATEGIC_REVIEW, LENS_ARCHITECTURE_AUDIT,
    )

    return QualityPlan(
        mode=mode,
        lens=qc_lens,
        components="auto",
        reviewers=reviewers,
        synthesis=SynthesisPolicy(
            required=mode in (MODE_PANEL, MODE_COMPONENT_PANEL),
        ),
        arbitration=ArbitrationPolicy(enabled=arbitration_enabled),
        budget=QualityBudget(),
    )


def validate_quality_plan(qp: QualityPlan) -> List[str]:
    """Validate a QualityPlan, returning error messages."""
    errors = []
    if qp.mode == MODE_ARBITRATION_ONLY:
        errors.append("mode arbitration_only is no longer supported")
    elif qp.mode not in VALID_MODES:
        errors.append(f"mode must be one of {sorted(VALID_MODES)}, got {qp.mode!r}")
    if qp.lens not in VALID_LENSES:
        errors.append(f"lens must be one of {sorted(VALID_LENSES)}, got {qp.lens!r}")
    if qp.components not in ("auto", "explicit"):
        errors.append(f"components must be 'auto' or 'explicit', got {qp.components!r}")
    if not isinstance(qp.budget.max_components, int) or isinstance(qp.budget.max_components, bool) or qp.budget.max_components < 1:
        errors.append("budget.max_components must be an integer >= 1")
    if not isinstance(qp.budget.max_llm_calls, int) or isinstance(qp.budget.max_llm_calls, bool) or qp.budget.max_llm_calls < 1:
        errors.append("budget.max_llm_calls must be an integer >= 1")
    if not isinstance(qp.budget.max_reviewers_per_component, int) or isinstance(qp.budget.max_reviewers_per_component, bool) or qp.budget.max_reviewers_per_component < 1:
        errors.append("budget.max_reviewers_per_component must be an integer >= 1")
    if not isinstance(qp.budget.max_wall_clock_sec, int) or isinstance(qp.budget.max_wall_clock_sec, bool) or qp.budget.max_wall_clock_sec < 0:
        errors.append("budget.max_wall_clock_sec must be an integer >= 0")
    if qp.mode in (MODE_PANEL, MODE_COMPONENT_PANEL) and not qp.reviewers:
        errors.append(f"reviewers list must not be empty when mode is {qp.mode}")
    for r in qp.reviewers:
        if r.role not in PERSONA_DESCRIPTIONS:
            errors.append(f"unknown persona role: {r.role!r} (valid: {sorted(PERSONA_DESCRIPTIONS)})")
    return errors

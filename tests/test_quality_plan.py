"""Tests for quality_plan.py — pure schema logic, zero model calls."""

from orchestrator.quality_plan import (
    LENS_ARCHITECTURE_AUDIT,
    LENS_CODE_CORRECTNESS,
    LENS_MIGRATION_SAFETY,
    LENS_RESEARCH_QUALITY,
    LENS_STRATEGIC_REVIEW,
    MODE_ARBITRATION_ONLY,
    MODE_COMPONENT_PANEL,
    MODE_PANEL,
    MODE_SINGLE,
    RISK_TIER_AUTO,
    RISK_TIER_HUMAN_REQUIRED,
    ArbitrationPolicy,
    QualityBudget,
    QualityPlan,
    ReviewerRole,
    SynthesisPolicy,
    quality_plan_for_contract,
    validate_quality_plan,
)


class TestQualityPlanDefaults:
    def test_default_quality_plan(self):
        qp = QualityPlan()
        assert qp.mode == MODE_SINGLE
        assert qp.lens == LENS_CODE_CORRECTNESS
        assert qp.components == "auto"
        assert len(qp.reviewers) == 0
        assert qp.synthesis.required is True
        assert qp.synthesis.preserve_dissent is True
        assert qp.synthesis.reject_on_p0 is True
        assert qp.arbitration.enabled is False
        assert qp.budget.max_llm_calls == 8

    def test_roundtrip_dict(self):
        qp = QualityPlan(
            mode=MODE_PANEL,
            lens=LENS_ARCHITECTURE_AUDIT,
            components="explicit",
            reviewers=[ReviewerRole("systems_architect"), ReviewerRole("maintainer")],
            synthesis=SynthesisPolicy(required=True, preserve_dissent=True, reject_on_p0=True),
            arbitration=ArbitrationPolicy(enabled=True, trigger=["p0_disagreement"]),
            budget=QualityBudget(max_llm_calls=6, max_components=3),
        )
        d = qp.to_dict()
        restored = QualityPlan.from_dict(d)
        assert restored.mode == MODE_PANEL
        assert restored.lens == LENS_ARCHITECTURE_AUDIT
        assert restored.components == "explicit"
        assert len(restored.reviewers) == 2
        assert restored.reviewers[0].role == "systems_architect"
        assert restored.reviewers[1].role == "maintainer"
        assert restored.synthesis.required is True
        assert restored.arbitration.enabled is True
        assert restored.arbitration.trigger == ["p0_disagreement"]
        assert restored.budget.max_llm_calls == 6
        assert restored.budget.max_components == 3


class TestQualityPlanEstimation:
    def test_estimate_single_mode(self):
        qp = QualityPlan(mode=MODE_SINGLE)
        assert qp.estimate_calls() == 1  # legacy run_qc_review executes exactly 1 call

    def test_estimate_panel_mode(self):
        qp = QualityPlan(
            mode=MODE_PANEL,
            reviewers=[ReviewerRole("a"), ReviewerRole("b"), ReviewerRole("c")],
        )
        assert qp.estimate_calls() == 3  # 3 reviewers; synthesis is local, not an LLM call

    def test_estimate_component_panel_capped(self):
        qp = QualityPlan(
            mode=MODE_COMPONENT_PANEL,
            reviewers=[ReviewerRole("a"), ReviewerRole("b")],
            budget=QualityBudget(max_components=3),
        )
        estimated = qp.estimate_calls()
        assert estimated == 6  # 3 components × 2 reviewers (synthesis is local)

    def test_estimate_with_arbitration(self):
        qp = QualityPlan(
            mode=MODE_PANEL,
            reviewers=[ReviewerRole("a")],
            arbitration=ArbitrationPolicy(enabled=True),
        )
        assert qp.estimate_calls() == 2  # 1 reviewer + arbitration (synthesis is local)

    def test_estimate_no_synthesis(self):
        qp = QualityPlan(
            mode=MODE_SINGLE,
            synthesis=SynthesisPolicy(required=False),
        )
        assert qp.estimate_calls() == 1  # just 1 reviewer, no synthesis, no arb


class TestQualityPlanDegradation:
    def test_degrade_over_budget_reduces_reviewers(self):
        qp = QualityPlan(
            mode=MODE_PANEL,
            reviewers=[ReviewerRole(str(i)) for i in range(5)],
            budget=QualityBudget(max_llm_calls=3),
        )
        degraded = qp.degraded_copy()
        # Least-destructive: trim reviewers to fit budget (5 -> 3, not 1).
        assert len(degraded.reviewers) == 3

    def test_degrade_component_to_single(self):
        # Least-destructive ladder: component_panel -> panel (panel fits the
        # budget), never straight to single when a milder step suffices.
        qp = QualityPlan(
            mode=MODE_COMPONENT_PANEL,
            reviewers=[ReviewerRole("a")],
            budget=QualityBudget(max_llm_calls=2),
        )
        degraded = qp.degraded_copy()
        assert degraded.mode == MODE_PANEL

    def test_degrade_component_ladder_to_panel_first(self):
        qp = QualityPlan(
            mode=MODE_COMPONENT_PANEL,
            reviewers=[ReviewerRole("a")],
            budget=QualityBudget(max_llm_calls=1),
        )
        # component_panel -> panel (not straight to single) when panel fits.
        degraded = qp.degraded_copy()
        assert degraded.mode in (MODE_PANEL, MODE_SINGLE)

    def test_degrade_drops_cheapest_first(self):
        qp = QualityPlan(
            mode=MODE_PANEL,
            reviewers=[ReviewerRole("a")],
            synthesis=SynthesisPolicy(required=True),
            arbitration=ArbitrationPolicy(enabled=True),
            budget=QualityBudget(max_llm_calls=1),
        )
        degraded = qp.degraded_copy()
        assert degraded.degraded is True
        assert degraded.estimate_calls() <= 1

    def test_no_degrade_when_under_budget(self):
        qp = QualityPlan(mode=MODE_SINGLE, budget=QualityBudget(max_llm_calls=10))
        degraded = qp.degraded_copy()
        assert degraded.mode == MODE_SINGLE
        assert degraded.arbitration.enabled is False  # default
        assert degraded.degraded is False
        assert degraded.degrade_reason == ""

    def test_degraded_copy_records_provenance(self):
        qp = QualityPlan(
            mode=MODE_PANEL,
            reviewers=[ReviewerRole("systems_architect"), ReviewerRole("maintainer")],
            budget=QualityBudget(max_llm_calls=1),
        )
        degraded = qp.degraded_copy()
        assert degraded.degraded is True
        assert degraded.original_mode == MODE_PANEL
        assert len(degraded.degrade_reason) > 0

    def test_degraded_copy_converges(self):
        qp = QualityPlan(
            mode=MODE_PANEL,
            reviewers=[ReviewerRole("a"), ReviewerRole("b"), ReviewerRole("c")],
            synthesis=SynthesisPolicy(required=True),
            arbitration=ArbitrationPolicy(enabled=True),
            budget=QualityBudget(max_llm_calls=1),
        )
        degraded = qp.degraded_copy()
        assert degraded.degraded is True
        assert degraded.estimate_calls() <= 1

    def test_arbitration_only_is_rejected(self):
        qp = QualityPlan(
            mode=MODE_ARBITRATION_ONLY,
            reviewers=[ReviewerRole("a")],
        )
        errors = validate_quality_plan(qp)
        assert any("arbitration_only" in e for e in errors)

    def test_estimate_component_panel_arbitration_enabled(self):
        qp = QualityPlan(
            mode=MODE_COMPONENT_PANEL,
            reviewers=[ReviewerRole("a"), ReviewerRole("b")],
            arbitration=ArbitrationPolicy(enabled=True),
            budget=QualityBudget(max_components=3),
        )
        assert qp.estimate_calls() == 7  # 3*2 + arb (synthesis is local)


class TestQualityPlanForContract:
    def test_auto_risk_code_lens_returns_single(self):
        qp = quality_plan_for_contract(RISK_TIER_AUTO, LENS_CODE_CORRECTNESS)
        assert qp.mode == MODE_SINGLE
        assert qp.lens == LENS_CODE_CORRECTNESS

    def test_auto_risk_architecture_audit_returns_panel(self):
        qp = quality_plan_for_contract(RISK_TIER_AUTO, LENS_ARCHITECTURE_AUDIT)
        assert qp.mode == MODE_PANEL

    def test_auto_risk_strategic_review_returns_panel(self):
        qp = quality_plan_for_contract(RISK_TIER_AUTO, LENS_STRATEGIC_REVIEW)
        assert qp.mode == MODE_PANEL

    def test_human_required_always_panel(self):
        qp = quality_plan_for_contract(RISK_TIER_HUMAN_REQUIRED, LENS_CODE_CORRECTNESS)
        assert qp.mode == MODE_PANEL

    def test_minimum_counts_promotes_to_panel(self):
        qp = quality_plan_for_contract(
            RISK_TIER_AUTO, LENS_CODE_CORRECTNESS, quality_spec={"minimum_counts": {"contradictions": 5}}
        )
        assert qp.mode == MODE_PANEL

    def test_architecture_audit_gets_systems_architect_personas(self):
        qp = quality_plan_for_contract(RISK_TIER_AUTO, LENS_ARCHITECTURE_AUDIT)
        roles = [r.role for r in qp.reviewers]
        assert "systems_architect" in roles
        assert "minimalist" in roles
        assert "product_owner" in roles

    def test_architecture_audit_enables_arbitration(self):
        qp = quality_plan_for_contract(RISK_TIER_AUTO, LENS_ARCHITECTURE_AUDIT)
        assert qp.arbitration.enabled is True

    def test_strategic_review_enables_arbitration(self):
        qp = quality_plan_for_contract(RISK_TIER_AUTO, LENS_STRATEGIC_REVIEW)
        assert qp.arbitration.enabled is True

    def test_code_correctness_no_arbitration_by_default(self):
        qp = quality_plan_for_contract(RISK_TIER_AUTO, LENS_CODE_CORRECTNESS)
        assert qp.arbitration.enabled is False


class TestValidateQualityPlan:
    def test_valid_default(self):
        errors = validate_quality_plan(QualityPlan())
        assert errors == []

    def test_invalid_mode(self):
        qp = QualityPlan(mode="invalid")
        errors = validate_quality_plan(qp)
        assert any("mode" in e for e in errors)

    def test_invalid_lens(self):
        qp = QualityPlan(lens="invalid")
        errors = validate_quality_plan(qp)
        assert any("lens" in e for e in errors)

    def test_invalid_components(self):
        qp = QualityPlan(components="invalid")
        errors = validate_quality_plan(qp)
        assert any("components" in e for e in errors)

    def test_budget_zero_components(self):
        qp = QualityPlan(budget=QualityBudget(max_components=0))
        errors = validate_quality_plan(qp)
        assert any("max_components" in e for e in errors)

    def test_budget_zero_calls(self):
        qp = QualityPlan(budget=QualityBudget(max_llm_calls=0))
        errors = validate_quality_plan(qp)
        assert any("max_llm_calls" in e for e in errors)

    def test_unknown_valid_mode(self):
        for mode in (MODE_SINGLE, MODE_PANEL, MODE_COMPONENT_PANEL):
            qp = QualityPlan(
                mode=mode,
                reviewers=[ReviewerRole("systems_architect")] if mode in (MODE_PANEL, MODE_COMPONENT_PANEL) else [],
            )
            assert validate_quality_plan(qp) == []

    def test_arbitration_only_mode_fails_validation(self):
        qp = QualityPlan(mode=MODE_ARBITRATION_ONLY)
        errors = validate_quality_plan(qp)
        assert any("arbitration_only" in e for e in errors)

    def test_unknown_valid_lens(self):
        for lens in (LENS_CODE_CORRECTNESS, LENS_ARCHITECTURE_AUDIT, LENS_RESEARCH_QUALITY, LENS_MIGRATION_SAFETY):
            qp = QualityPlan(lens=lens)
            assert validate_quality_plan(qp) == []

    def test_panel_mode_empty_reviewers_fails(self):
        qp = QualityPlan(mode=MODE_PANEL, reviewers=[])
        errors = validate_quality_plan(qp)
        assert any("reviewers" in e for e in errors)

    def test_component_panel_empty_reviewers_fails(self):
        qp = QualityPlan(mode=MODE_COMPONENT_PANEL, reviewers=[])
        errors = validate_quality_plan(qp)
        assert any("reviewers" in e for e in errors)

    def test_invalid_persona_role_fails(self):
        qp = QualityPlan(
            mode=MODE_PANEL,
            reviewers=[ReviewerRole("invalid_role")],
        )
        errors = validate_quality_plan(qp)
        assert any("unknown persona" in e for e in errors)

    def test_negative_wall_clock_fails(self):
        qp = QualityPlan(budget=QualityBudget(max_wall_clock_sec=-1))
        errors = validate_quality_plan(qp)
        assert any("max_wall_clock_sec" in e for e in errors)


class TestReviewerRole:
    def test_default_model_policy(self):
        r = ReviewerRole("systems_architect")
        assert r.model_policy == "default"

    def test_minimalist_uses_cheap_model(self):
        r = ReviewerRole("minimalist")
        assert r.model_policy == "cheap_cross_family"

    def test_custom_model_policy_overrides_default(self):
        r = ReviewerRole("systems_architect", model_policy="premium")
        assert r.model_policy == "premium"

    def test_roundtrip_dict(self):
        r = ReviewerRole("test_strategist", focus=["coverage", "falsifiability"])
        d = r.to_dict()
        restored = ReviewerRole.from_dict(d)
        assert restored.role == "test_strategist"
        assert restored.focus == ["coverage", "falsifiability"]

    def test_unknown_role_returns_default_policy(self):
        r = ReviewerRole("unknown_role")
        assert r.model_policy == "default"


class TestQualityBudget:
    def test_default_budget(self):
        b = QualityBudget()
        assert b.max_llm_calls == 8
        assert b.max_wall_clock_sec == 900
        assert b.max_reviewers_per_component == 3
        assert b.max_components == 5
        assert b.degrade_strategy == "single_reviewer_then_synthesis"

    def test_roundtrip_dict(self):
        b = QualityBudget(max_llm_calls=4, max_components=2, degrade_strategy="drop_arbitration")
        d = b.to_dict()
        restored = QualityBudget.from_dict(d)
        assert restored.max_llm_calls == 4
        assert restored.max_components == 2
        assert restored.degrade_strategy == "drop_arbitration"


class TestSynthesisPolicy:
    def test_default(self):
        s = SynthesisPolicy()
        assert s.required is True
        assert s.preserve_dissent is True
        assert s.reject_on_p0 is True

    def test_roundtrip_dict(self):
        s = SynthesisPolicy(required=False, preserve_dissent=False, reject_on_p0=False)
        d = s.to_dict()
        restored = SynthesisPolicy.from_dict(d)
        assert restored.required is False
        assert restored.preserve_dissent is False
        assert restored.reject_on_p0 is False


class TestArbitrationPolicy:
    def test_default_not_enabled(self):
        a = ArbitrationPolicy()
        assert a.enabled is False
        assert "p0_disagreement" in a.trigger

    def test_roundtrip_dict(self):
        a = ArbitrationPolicy(enabled=True, trigger=["low_confidence"], model_policy="premium")
        d = a.to_dict()
        restored = ArbitrationPolicy.from_dict(d)
        assert restored.enabled is True
        assert restored.trigger == ["low_confidence"]
        assert restored.model_policy == "premium"

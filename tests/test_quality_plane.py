"""Tests for quality_plane.py — component dispatch and legacy path."""

import json
import os
from unittest.mock import MagicMock, patch

import pytest
from orchestrator.quality_plan import (
    ArbitrationPolicy, QualityPlan, QualityBudget, ReviewerRole,
    MODE_COMPONENT_PANEL, MODE_PANEL, MODE_SINGLE,
    LENS_CODE_CORRECTNESS,
)
from orchestrator.quality_plane import (
    QualityPlaneVerdict,
    _REVIEWER_HOOK,
    _build_arbitration_prompt,
    _build_persona_prompt,
    _component_verdict_to_qp_verdict,
    _invoke_reviewer,
    _raw_to_arbitration_verdict,
    _raw_to_artifact,
    _resolve_arbitration_model,
    _resolve_reviewer_model,
    _run_arbitration,
    _run_component_reviews,
    _run_component_quality_plane,
    _should_arbitrate,
    _synthesis_reason,
    run_quality_plane,
)
from orchestrator.review_artifact import ArbitrationVerdict, EvidenceRead, ReviewArtifact, ReviewIssue, SynthesisVerdict
from orchestrator.component_slicer import ComponentSlice, slice_components
from orchestrator.review_artifact import EvidenceRead, ReviewArtifact, ReviewIssue


# ── Helpers ────────────────────────────────────────────────────────────────

def _make_contract(**overrides):
    """Build a minimal mock contract object."""
    contract = MagicMock()
    contract.objective = "Implement a sorting algorithm"
    contract.acceptance_checks = [
        {"kind": "file_exists", "path": "sort.py", "expected": "yes"},
    ]
    contract.qc = {"lens": "code_correctness"}
    contract.quality_spec = {}
    contract.worker = {"model": "test-worker"}
    for k, v in overrides.items():
        setattr(contract, k, v)
    return contract


def _make_reviewer_hook(*responses):
    """Return a hook that returns sequential responses for each call."""
    responses = list(responses)
    idx = 0

    def hook(prompt, model, workspace_root):
        nonlocal idx
        if idx < len(responses):
            resp = responses[idx]
            idx += 1
            return resp
        return {"status": "PASS", "reason": "fallthrough", "score": 0.95, "issues": [], "files_reviewed": ["a.py", "b.py"]}

    return hook


# ── Tests: _raw_to_artifact ────────────────────────────────────────────────

class TestRawToArtifact:
    def test_pass_conversion(self):
        raw = {"status": "PASS", "reason": "looks good", "score": 0.95, "issues": [], "files_reviewed": ["a.py", "b.py"]}
        art = _raw_to_artifact(raw, "reviewer_0", "maintainer", "test-model", "component_0", ["a.py"])
        assert art.verdict == "PASS"
        assert art.confidence >= 0.9
        assert art.role == "maintainer"
        assert art.model == "test-model"
        assert art.component_id == "component_0"

    def test_reject_with_issues(self):
        raw = {
            "status": "REJECT",
            "reason": "bugs found",
            "score": 0.3,
            "issues": [
                {"severity": "CRITICAL", "description": "null pointer risk"},
                {"severity": "MAJOR", "description": "missing error handling"},
            ],
        }
        art = _raw_to_artifact(raw, "r1", "reviewer", "m1", "c0")
        assert art.verdict == "REJECT"
        assert art.confidence == 0.3
        assert len(art.issues) == 2
        assert art.issues[0].severity == "P0"
        assert art.issues[1].severity == "P1"

    def test_unknown_severity_fails_closed_as_error(self):
        raw = {"status": "REJECT", "score": 0.5, "issues": [{"severity": "BLOCKER", "description": "bad"}]}
        art = _raw_to_artifact(raw, "r1", "r", "m1", "c0")
        assert art.verdict == "ERROR"

    def test_empty_issues_pass_high_confidence(self):
        raw = {"status": "PASS", "score": 0.5, "issues": [], "files_reviewed": ["a.py", "b.py"]}
        art = _raw_to_artifact(raw, "r1", "r", "m1", "c0", ["a.py"])
        assert art.verdict == "PASS"
        assert art.confidence >= 0.9

    def test_missing_status_fails_closed_as_error(self):
        raw = {}
        art = _raw_to_artifact(raw, "r1", "r", "m1", "c0")
        assert art.verdict == "ERROR"
        assert art.issues == []


# ── Tests: _synthesis_reason ───────────────────────────────────────────────

class TestSynthesisReason:
    def test_insufficient_evidence(self):
        assert "No reviewer examined evidence" == _synthesis_reason("INSUFFICIENT_EVIDENCE", False, [], [])

    def test_p0_blockers(self):
        blockers = [ReviewIssue("P0", claim="crash")]
        reason = _synthesis_reason("REJECT", False, blockers, [])
        assert "1 P0 blocker" in reason

    def test_p1_fixes(self):
        fixes = [ReviewIssue("P1", claim="warn")]
        reason = _synthesis_reason("CONDITIONAL_PASS", False, [], fixes)
        assert "1 P1" in reason

    def test_all_pass(self):
        reason = _synthesis_reason("PASS", True, [], [])
        assert "All components passed" in reason


# ── Tests: _resolve_arbitration_model ─────────────────────────────────────

class TestResolveArbitrationModel:
    def test_default_uses_qc_model(self):
        from orchestrator.models import ModelRegistry
        policy = ArbitrationPolicy()
        model = _resolve_arbitration_model(policy)
        assert model == ModelRegistry.default_qc()

    def test_premium_uses_opus(self):
        policy = ArbitrationPolicy(model_policy="premium")
        assert "opus" in _resolve_arbitration_model(policy).lower()

    def test_cheap_uses_default_fallback(self):
        from orchestrator.models import ModelRegistry
        policy = ArbitrationPolicy(model_policy="cheap_cross_family")
        assert _resolve_arbitration_model(policy) == ModelRegistry.FALLBACK


# ── Tests: _should_arbitrate ──────────────────────────────────────────────

class TestShouldArbitrate:
    def test_not_armed_disabled(self):
        policy = ArbitrationPolicy(enabled=False)
        assert _should_arbitrate([], SynthesisVerdict(), policy) is False

    def test_p0_disagreement_triggers(self):
        policy = ArbitrationPolicy(enabled=True, trigger=["p0_disagreement"])
        art1 = ReviewArtifact("r1", "reviewer", "m1", "c0",
                              verdict="REJECT", evidence_read=[EvidenceRead("a.py", origin="cited")],
                              issues=[ReviewIssue("P0", claim="bug")])
        synthesis = SynthesisVerdict(p0_blockers=[ReviewIssue("P0", claim="bug")])
        assert _should_arbitrate([art1], synthesis, policy) is True

    def test_low_confidence_triggers(self):
        policy = ArbitrationPolicy(enabled=True, trigger=["low_confidence"])
        art1 = ReviewArtifact("r1", "reviewer", "m1", "c0",
                              verdict="PASS", confidence=0.2,
                              evidence_read=[EvidenceRead("a.py", origin="cited")])
        synthesis = SynthesisVerdict()
        assert _should_arbitrate([art1], synthesis, policy) is True

    def test_any_reject_triggers_on_reject(self):
        policy = ArbitrationPolicy(enabled=True, trigger=["any_reject"])
        art1 = ReviewArtifact("r1", "reviewer", "m1", "c0",
                              verdict="REJECT", confidence=0.8,
                              evidence_read=[EvidenceRead("a.py", origin="cited")])
        synthesis = SynthesisVerdict()
        assert _should_arbitrate([art1], synthesis, policy) is True

    def test_no_trigger_match_returns_false(self):
        policy = ArbitrationPolicy(enabled=True, trigger=["low_confidence"])
        art1 = ReviewArtifact("r1", "reviewer", "m1", "c0",
                              verdict="PASS", confidence=0.9,
                              evidence_read=[EvidenceRead("a.py", origin="cited")])
        synthesis = SynthesisVerdict()
        assert _should_arbitrate([art1], synthesis, policy) is False


# ── Tests: _build_arbitration_prompt ─────────────────────────────────────

class TestBuildArbitrationPrompt:
    def test_includes_disagreements(self):
        art = ReviewArtifact("r1", "maintainer", "m1", "c0",
                             verdict="REJECT", confidence=0.3,
                             evidence_read=[EvidenceRead("a.py", origin="cited")],
                             issues=[ReviewIssue("P0", claim="crash")])
        synth = SynthesisVerdict(p0_blockers=[ReviewIssue("P0", claim="crash")])
        prompt = _build_arbitration_prompt([art], synth)
        assert "Reviewer 1" in prompt
        assert "maintainer" in prompt
        assert "[P0]" in prompt or "crash" in prompt
        assert "P0 BLOCKERS" in prompt

    def test_p1_section_included(self):
        art = ReviewArtifact("r1", "reviewer", "m1", "c0",
                             verdict="CONDITIONAL_PASS", confidence=0.6,
                             evidence_read=[EvidenceRead("a.py", origin="cited")],
                             issues=[ReviewIssue("P1", claim="warn")])
        synth = SynthesisVerdict(p1_required_fixes=[ReviewIssue("P1", claim="warn")])
        prompt = _build_arbitration_prompt([art], synth)
        assert "P1 REQUIRED FIXES" in prompt


# ── Tests: _raw_to_arbitration_verdict ───────────────────────────────────

class TestRawToArbitrationVerdict:
    def test_valid_status_preserved(self):
        raw = {"status": "PASS", "reason": "ok", "confidence": 0.95,
               "winning_claims": ["c1"], "discarded_claims": ["c2"]}
        av = _raw_to_arbitration_verdict(raw)
        assert av.status == "PASS"
        assert av.reason == "ok"
        assert av.confidence == 0.95
        assert av.winning_claims == ["c1"]

    def test_invalid_status_defaults_to_reject(self):
        av = _raw_to_arbitration_verdict({"status": "INVALID"})
        assert av.status == "REJECT"

    def test_missing_fields_default_gracefully(self):
        av = _raw_to_arbitration_verdict({})
        assert av.status == "REJECT"
        assert av.confidence == 0.0
        assert av.winning_claims == []


# ── Tests: _run_arbitration ──────────────────────────────────────────────

class TestRunArbitration:
    def test_returns_arbitration_verdict(self):
        policy = ArbitrationPolicy(enabled=True)
        art = ReviewArtifact("r1", "reviewer", "m1", "c0",
                             verdict="REJECT", confidence=0.3,
                             evidence_read=[EvidenceRead("a.py", origin="cited")],
                             issues=[ReviewIssue("P0", claim="bug")])
        synth = SynthesisVerdict(p0_blockers=[ReviewIssue("P0", claim="bug")])

        hook = _make_reviewer_hook({"status": "PASS", "reason": "overruled", "confidence": 0.9, "winning_claims": ["safe"], "discarded_claims": ["bug"]})

        orig_hook = _REVIEWER_HOOK
        try:
            import orchestrator.quality_plane as qp_mod
            qp_mod._REVIEWER_HOOK = hook
            result = _run_arbitration([art], synth, policy, ".")
        finally:
            qp_mod._REVIEWER_HOOK = orig_hook

        assert isinstance(result, ArbitrationVerdict)
        assert result.status == "PASS"
        assert result.reason == "overruled"


# ── Tests: arbitration integrated in component verdict ────────────────────

class TestComponentVerdictWithArbitration:
    def test_arbitration_overrides_to_pass(self):
        components = [ComponentSlice("c0", ["a.py"], "file a")]
        artifacts = [
            ReviewArtifact("r1", "m1", "m1", "c0",
                           verdict="REJECT", confidence=0.3,
                           evidence_read=[EvidenceRead("a.py", origin="cited")],
                           issues=[ReviewIssue("P0", claim="bug")]),
        ]
        plan = QualityPlan(
            mode=MODE_COMPONENT_PANEL,
            reviewers=[ReviewerRole("maintainer")],
            arbitration=ArbitrationPolicy(enabled=True, trigger=["any_reject"]),
        )

        hook = _make_reviewer_hook(
            {"status": "PASS", "reason": "overruled", "confidence": 0.9, "winning_claims": ["The bug is not blocking"]},
        )

        orig_hook = _REVIEWER_HOOK
        try:
            import orchestrator.quality_plane as qp_mod
            qp_mod._REVIEWER_HOOK = hook
            verdict = _component_verdict_to_qp_verdict(artifacts, components, plan, workspace_root=".")
        finally:
            qp_mod._REVIEWER_HOOK = orig_hook

        assert verdict.passed is True
        assert verdict.status == "PASS"
        assert verdict.arbitration_result is not None

    def test_arbitration_not_invoked_when_disabled(self):
        components = [ComponentSlice("c0", ["a.py"], "file a")]
        artifacts = [
            ReviewArtifact("r1", "m1", "m1", "c0",
                           verdict="PASS", confidence=0.95,
                           evidence_read=[EvidenceRead("a.py", origin="cited")]),
        ]
        plan = QualityPlan(
            mode=MODE_COMPONENT_PANEL,
            reviewers=[ReviewerRole("maintainer")],
            arbitration=ArbitrationPolicy(enabled=False),
        )
        verdict = _component_verdict_to_qp_verdict(artifacts, components, plan, workspace_root=".")
        assert verdict.arbitration_result is None
        assert verdict.passed is True

    def test_arbitration_budget_tracked(self):
        components = [ComponentSlice("c0", ["a.py"], "file a")]
        artifacts = [
            ReviewArtifact("r1", "m1", "m1", "c0",
                           verdict="REJECT", confidence=0.3,
                           evidence_read=[EvidenceRead("a.py", origin="cited")],
                           issues=[ReviewIssue("P0", claim="bug")]),
        ]
        plan = QualityPlan(
            mode=MODE_COMPONENT_PANEL,
            reviewers=[ReviewerRole("maintainer")],
            arbitration=ArbitrationPolicy(enabled=True, trigger=["any_reject"]),
        )

        hook = _make_reviewer_hook({"status": "PASS", "reason": "overruled", "confidence": 0.9})

        orig_hook = _REVIEWER_HOOK
        try:
            import orchestrator.quality_plane as qp_mod
            qp_mod._REVIEWER_HOOK = hook
            verdict = _component_verdict_to_qp_verdict(artifacts, components, plan, workspace_root=".")
        finally:
            qp_mod._REVIEWER_HOOK = orig_hook

        assert verdict.budget_used["llm_calls"] == 2  # 1 review + arbitration (synthesis is local)

    def test_arbitration_reject_overrides_conditional_pass(self):
        """An arbiter REJECT must override a CONDITIONAL_PASS synthesis."""
        components = [ComponentSlice("c0", ["a.py"], "file a")]
        artifacts = [
            ReviewArtifact("r1", "m1", "m1", "c0",
                           verdict="REJECT", confidence=0.5,
                           evidence_read=[EvidenceRead("a.py", origin="cited")],
                           issues=[ReviewIssue("P1", claim="test gap")]),
        ]
        plan = QualityPlan(
            mode=MODE_COMPONENT_PANEL,
            reviewers=[ReviewerRole("maintainer")],
            arbitration=ArbitrationPolicy(enabled=True, trigger=["any_reject"]),
        )

        hook = _make_reviewer_hook(
            {"status": "REJECT", "reason": "gap is blocking", "confidence": 0.9},
        )

        orig_hook = _REVIEWER_HOOK
        try:
            import orchestrator.quality_plane as qp_mod
            qp_mod._REVIEWER_HOOK = hook
            verdict = _component_verdict_to_qp_verdict(artifacts, components, plan, workspace_root=".")
        finally:
            qp_mod._REVIEWER_HOOK = orig_hook

        assert verdict.passed is False
        assert verdict.status == "REJECT"
        assert verdict.arbitration_result is not None


# ── Tests: _build_persona_prompt ───────────────────────────────────────────

class TestBuildPersonaPrompt:
    def test_appends_persona_section(self):
        result = _build_persona_prompt("base prompt", ReviewerRole("maintainer"))
        assert "=== REVIEW PERSPECTIVE: maintainer ===" in result
        assert "base prompt" in result
        assert "complexity, testability" in result

    def test_includes_focus_when_provided(self):
        result = _build_persona_prompt("base", ReviewerRole("maintainer", focus=["coverage", "edge cases"]))
        assert "Specific concerns:" in result
        assert "coverage" in result
        assert "edge cases" in result

    def test_unknown_role_omits_focus(self):
        result = _build_persona_prompt("base", ReviewerRole("unknown_role"))
        assert "=== REVIEW PERSPECTIVE: unknown_role ===" in result
        assert "Focus on:" not in result

    def test_preserves_base_prompt(self):
        base = "You are a strict code reviewer.\nCheck everything."
        result = _build_persona_prompt(base, ReviewerRole("security_safety"))
        assert result.startswith(base)


# ── Tests: _component_verdict_to_qp_verdict ────────────────────────────────

class TestComponentVerdictToQpVerdict:
    def test_all_components_pass(self):
        components = [
            ComponentSlice("component_0", ["a.py"], "file a"),
            ComponentSlice("component_1", ["b.py"], "file b"),
        ]
        artifacts = [
            ReviewArtifact("r1", "reviewer", "m1", "component_0", verdict="PASS", confidence=0.95, evidence_read=[EvidenceRead("a.py", origin="cited")]),
            ReviewArtifact("r2", "reviewer", "m1", "component_1", verdict="PASS", confidence=0.9, evidence_read=[EvidenceRead("b.py", origin="cited")]),
        ]
        plan = QualityPlan(mode=MODE_COMPONENT_PANEL, reviewers=[ReviewerRole("maintainer")])
        verdict = _component_verdict_to_qp_verdict(artifacts, components, plan)
        assert verdict.passed is True
        assert verdict.status == "PASS"
        assert verdict.score > 0.9
        assert len(verdict.component_verdicts) == 2
        assert len(verdict.review_artifacts) == 2
        assert verdict.synthesis_result is not None

    def test_reject_from_p0(self):
        components = [ComponentSlice("component_0", ["a.py"], "file a")]
        artifacts = [
            ReviewArtifact(
                "r1", "reviewer", "m1", "component_0",
                verdict="REJECT", confidence=0.3,
                evidence_read=[EvidenceRead("a.py", origin="cited")],
                issues=[ReviewIssue("P0", path="a.py", claim="crash bug")],
            ),
        ]
        plan = QualityPlan(mode=MODE_COMPONENT_PANEL, reviewers=[ReviewerRole("maintainer")])
        verdict = _component_verdict_to_qp_verdict(artifacts, components, plan)
        assert verdict.passed is False
        assert verdict.status == "REJECT"
        assert len(verdict.issues) == 1

    def test_insufficient_evidence(self):
        components = [ComponentSlice("c0", [], "empty")]
        artifacts = [ReviewArtifact("r1", "reviewer", "m1", "c0", verdict="PASS")]
        plan = QualityPlan(mode=MODE_COMPONENT_PANEL, reviewers=[ReviewerRole("maintainer")])
        verdict = _component_verdict_to_qp_verdict(artifacts, components, plan)
        assert verdict.passed is False
        assert verdict.status == "INSUFFICIENT_EVIDENCE"

    def test_budget_used_tracks_calls(self):
        components = [ComponentSlice("c0", ["a.py"])]
        artifacts = [ReviewArtifact("r1", "reviewer", "m1", "c0", verdict="PASS", evidence_read=[EvidenceRead("a.py", origin="cited")])]
        plan = QualityPlan(
            mode=MODE_COMPONENT_PANEL,
            reviewers=[ReviewerRole("maintainer")],
        )
        verdict = _component_verdict_to_qp_verdict(artifacts, components, plan)
        assert verdict.budget_used["llm_calls"] == 1  # 1 component review (synthesis is local)

    def test_multiple_reviewers_per_component_verdict(self):
        components = [ComponentSlice("c0", ["a.py"], "file a")]
        artifacts = [
            ReviewArtifact("r1", "maintainer", "m1", "c0", verdict="PASS", confidence=0.9, evidence_read=[EvidenceRead("a.py", origin="cited")]),
            ReviewArtifact("r2", "security_safety", "m1", "c0", verdict="REJECT", confidence=0.3, evidence_read=[EvidenceRead("a.py", origin="cited")], issues=[ReviewIssue("P0", path="a.py", claim="unsafe")]),
        ]
        plan = QualityPlan(mode=MODE_COMPONENT_PANEL, reviewers=[ReviewerRole("maintainer"), ReviewerRole("security_safety")])
        verdict = _component_verdict_to_qp_verdict(artifacts, components, plan)
        assert verdict.passed is False
        assert verdict.status == "REJECT"
        assert len(verdict.component_verdicts) == 1
        assert verdict.component_verdicts[0]["status"] == "REJECT"
        assert len(verdict.component_verdicts[0]["reviewers"]) == 2
        assert verdict.budget_used["llm_calls"] == 2  # 2 reviews (synthesis is local)


# ── Tests: integration with mocked reviewer hook ──────────────────────────

class TestRunComponentReviews:
    def test_dispatches_per_component(self):
        contract = _make_contract()
        components = [
            ComponentSlice("c0", ["a.py"], "file a"),
            ComponentSlice("c1", ["b.py"], "file b"),
        ]
        plan = QualityPlan(
            mode=MODE_COMPONENT_PANEL,
            reviewers=[ReviewerRole("maintainer")],
        )

        hook = _make_reviewer_hook(
            {"status": "PASS", "reason": "ok", "score": 0.9, "issues": [], "files_reviewed": ["a.py", "b.py"]},
            {"status": "PASS", "reason": "ok", "score": 0.8, "issues": [], "files_reviewed": ["a.py", "b.py"]},
        )

        orig_hook = _REVIEWER_HOOK
        try:
            import orchestrator.quality_plane as qp_mod
            qp_mod._REVIEWER_HOOK = hook
            artifacts = _run_component_reviews(contract, components, [], ".", plan)
        finally:
            qp_mod._REVIEWER_HOOK = orig_hook

        assert len(artifacts) == 2
        assert artifacts[0].component_id == "c0"
        assert artifacts[1].component_id == "c1"

    def test_dispatches_multiple_reviewers_per_component(self):
        contract = _make_contract()
        components = [ComponentSlice("c0", ["a.py"], "file a")]
        plan = QualityPlan(
            mode=MODE_COMPONENT_PANEL,
            reviewers=[ReviewerRole("maintainer"), ReviewerRole("security_safety")],
        )

        hook = _make_reviewer_hook(
            {"status": "PASS", "reason": "ok", "score": 0.9, "issues": [], "files_reviewed": ["a.py", "b.py"]},
            {"status": "PASS", "reason": "ok", "score": 0.8, "issues": [], "files_reviewed": ["a.py", "b.py"]},
        )

        orig_hook = _REVIEWER_HOOK
        try:
            import orchestrator.quality_plane as qp_mod
            qp_mod._REVIEWER_HOOK = hook
            artifacts = _run_component_reviews(contract, components, [], ".", plan)
        finally:
            qp_mod._REVIEWER_HOOK = orig_hook

        assert len(artifacts) == 2
        assert artifacts[0].role == "maintainer"
        assert artifacts[1].role == "security_safety"
        assert artifacts[0].component_id == "c0"
        assert artifacts[1].component_id == "c0"

    def test_max_reviewers_per_component_respected(self):
        contract = _make_contract()
        components = [ComponentSlice("c0", ["a.py"], "file a")]
        plan = QualityPlan(
            mode=MODE_COMPONENT_PANEL,
            reviewers=[
                ReviewerRole("maintainer"),
                ReviewerRole("security_safety"),
                ReviewerRole("product_owner"),
                ReviewerRole("systems_architect"),
            ],
            budget=QualityBudget(max_reviewers_per_component=2),
        )

        hook = _make_reviewer_hook(
            {"status": "PASS", "reason": "ok", "score": 0.9, "issues": [], "files_reviewed": ["a.py", "b.py"]},
            {"status": "PASS", "reason": "ok", "score": 0.8, "issues": [], "files_reviewed": ["a.py", "b.py"]},
        )

        orig_hook = _REVIEWER_HOOK
        try:
            import orchestrator.quality_plane as qp_mod
            qp_mod._REVIEWER_HOOK = hook
            artifacts = _run_component_reviews(contract, components, [], ".", plan)
        finally:
            qp_mod._REVIEWER_HOOK = orig_hook

        assert len(artifacts) == 2  # capped at 2, not 4
        assert artifacts[0].role == "maintainer"
        assert artifacts[1].role == "security_safety"

    def test_component_with_no_files(self):
        contract = _make_contract()
        components = [ComponentSlice("c0", [], "empty component")]
        plan = QualityPlan(
            mode=MODE_COMPONENT_PANEL,
            reviewers=[ReviewerRole("maintainer")],
        )

        hook = _make_reviewer_hook({"status": "PASS", "reason": "ok", "score": 0.9, "issues": [], "files_reviewed": ["a.py", "b.py"]})

        orig_hook = _REVIEWER_HOOK
        try:
            import orchestrator.quality_plane as qp_mod
            qp_mod._REVIEWER_HOOK = hook
            artifacts = _run_component_reviews(contract, components, [], ".", plan)
        finally:
            qp_mod._REVIEWER_HOOK = orig_hook

        assert len(artifacts) == 1
        assert artifacts[0].component_id == "c0"


class TestRunComponentQualityPlane:
    def test_slices_and_reviews(self):
        contract = _make_contract()
        plan = QualityPlan(
            mode=MODE_COMPONENT_PANEL,
            reviewers=[ReviewerRole("maintainer")],
        )

        hook = _make_reviewer_hook(
            {"status": "PASS", "reason": "a ok", "score": 0.95, "issues": [], "files_reviewed": ["a.py", "b.py"]},
        )

        orig_hook = _REVIEWER_HOOK
        try:
            import orchestrator.quality_plane as qp_mod
            qp_mod._REVIEWER_HOOK = hook
            verdict = _run_component_quality_plane(
                contract, ["a.py", "b.py"], [], ".", plan,
            )
        finally:
            qp_mod._REVIEWER_HOOK = orig_hook

        assert isinstance(verdict, QualityPlaneVerdict)
        assert verdict.passed is True
        assert verdict.status == "PASS"


class TestRunQualityPlane:
    def test_legacy_path_no_plan(self):
        contract = _make_contract()
        with patch.dict(os.environ, {"FAKE_QC": "PASS"}):
            verdict = run_quality_plane(contract, ["a.py"], [], ".")
        assert verdict.passed is True

    def test_component_path_with_plan(self):
        contract = _make_contract()
        plan = QualityPlan(
            mode=MODE_COMPONENT_PANEL,
            reviewers=[ReviewerRole("maintainer")],
        )

        hook = _make_reviewer_hook(
            {"status": "PASS", "reason": "ok", "score": 0.95, "issues": [], "files_reviewed": ["a.py", "b.py"]},
        )

        orig_hook = _REVIEWER_HOOK
        try:
            import orchestrator.quality_plane as qp_mod
            qp_mod._REVIEWER_HOOK = hook
            verdict = run_quality_plane(contract, ["a.py"], [], ".", quality_plan=plan)
        finally:
            qp_mod._REVIEWER_HOOK = orig_hook

        assert verdict.passed is True

    def test_legacy_path_passes_through_with_single_mode_plan(self):
        """A SINGLE mode plan should still use legacy path."""
        contract = _make_contract()
        plan = QualityPlan(mode=MODE_SINGLE)
        with patch.dict(os.environ, {"FAKE_QC": "PASS"}):
            verdict = run_quality_plane(contract, ["a.py"], [], ".", quality_plan=plan)
        assert verdict.passed is True

    def test_legacy_path_reject(self):
        contract = _make_contract()
        with patch.dict(os.environ, {"FAKE_QC": "REJECT"}):
            verdict = run_quality_plane(contract, ["a.py"], [], ".")
        assert verdict.passed is False
        assert verdict.status == "REJECT"

    def test_panel_path_with_plan(self):
        """MODE_PANEL dispatches persona reviewers over the full output set as one component."""
        contract = _make_contract()
        plan = QualityPlan(
            mode=MODE_PANEL,
            reviewers=[ReviewerRole("maintainer"), ReviewerRole("security_safety")],
        )

        calls = []

        def hook(prompt, model, workspace_root):
            calls.append((model, workspace_root))
            return {"status": "PASS", "reason": "ok", "score": 0.9, "issues": [], "files_reviewed": ["a.py", "b.py"]}

        orig_hook = _REVIEWER_HOOK
        try:
            import orchestrator.quality_plane as qp_mod
            qp_mod._REVIEWER_HOOK = hook
            verdict = run_quality_plane(contract, ["a.py", "b.py"], [], ".", quality_plan=plan)
        finally:
            qp_mod._REVIEWER_HOOK = orig_hook

        assert verdict.passed is True
        assert len(calls) == 2
        assert len(verdict.component_verdicts) == 1
        assert verdict.component_verdicts[0]["component_id"] == "component_0"
        assert verdict.component_verdicts[0]["files"] == ["a.py", "b.py"]

    def test_panel_path_fake_qc(self):
        """MODE_PANEL is deterministic under FAKE_QC — no real subprocess."""
        contract = _make_contract()
        plan = QualityPlan(
            mode=MODE_PANEL,
            reviewers=[ReviewerRole("maintainer")],
        )
        with patch.dict(os.environ, {"FAKE_QC": "PASS"}):
            verdict = run_quality_plane(contract, ["a.py"], [], ".", quality_plan=plan)
        assert verdict.passed is True
        assert verdict.status == "PASS"

    def test_panel_path_reject_fake_qc(self):
        """FAKE_QC=REJECT must yield a true REJECT (P0-level), matching legacy semantics."""
        contract = _make_contract()
        plan = QualityPlan(
            mode=MODE_PANEL,
            reviewers=[ReviewerRole("maintainer")],
        )
        with patch.dict(os.environ, {"FAKE_QC": "REJECT"}):
            verdict = run_quality_plane(contract, ["a.py"], [], ".", quality_plan=plan)
        assert verdict.passed is False
        assert verdict.status == "REJECT"


class TestInvokeReviewerFakeQcParity:
    """FAKE_QC env must be honored in _invoke_reviewer so panel/component
    paths are deterministic without the _REVIEWER_HOOK."""

    def _invoke_with(self, fake_mode):
        with patch.dict(os.environ, {"FAKE_QC": fake_mode}):
            return _invoke_reviewer("prompt", "model", ".")

    def test_pass(self):
        raw = self._invoke_with("PASS")
        assert raw["status"] == "PASS"
        assert raw["score"] == 0.95

    def test_reject(self):
        raw = self._invoke_with("REJECT")
        assert raw["status"] == "REJECT"
        assert raw["score"] == 0.4
        assert raw["issues"][0]["severity"] == "CRITICAL"

    def test_insufficient_evidence(self):
        raw = self._invoke_with("INSUFFICIENT_EVIDENCE")
        assert raw["status"] == "INSUFFICIENT_EVIDENCE"

    def test_error(self):
        raw = self._invoke_with("ERROR")
        assert raw["status"] == "ERROR"

    def test_unknown_mode_errors(self):
        raw = self._invoke_with("BOGUS")
        assert raw["status"] == "ERROR"
        assert "unknown FAKE_QC mode" in raw["reason"]

    def test_hook_takes_precedence_over_fake(self):
        """_REVIEWER_HOOK wins over FAKE_QC when both are present."""
        calls = []

        def hook(prompt, model, workspace_root):
            calls.append(model)
            return {"status": "PASS", "reason": "hook", "score": 0.9, "issues": [], "files_reviewed": ["a.py", "b.py"]}

        orig_hook = _REVIEWER_HOOK
        try:
            import orchestrator.quality_plane as qp_mod
            qp_mod._REVIEWER_HOOK = hook
            with patch.dict(os.environ, {"FAKE_QC": "REJECT"}):
                raw = _invoke_reviewer("p", "m", ".")
        finally:
            qp_mod._REVIEWER_HOOK = orig_hook
        assert raw["status"] == "PASS"
        assert calls == ["m"]


class TestModelResolution:
    def test_reviewer_model_policies(self):
        from orchestrator.models import ModelRegistry
        assert _resolve_reviewer_model(ReviewerRole("maintainer")) == ModelRegistry.default_qc()
        assert _resolve_reviewer_model(ReviewerRole("reviewer", model_policy="default")) == ModelRegistry.default_qc()
        assert _resolve_reviewer_model(ReviewerRole("reviewer", model_policy="cheap_cross_family")) == ModelRegistry.FALLBACK
        assert _resolve_reviewer_model(ReviewerRole("reviewer", model_policy="premium")) == ModelRegistry.OPUS
        assert _resolve_reviewer_model(ReviewerRole("reviewer", model_policy="deep")) == ModelRegistry.KIMI

    def test_dispatch_uses_per_reviewer_model(self):
        """Each reviewer's model_policy must flow into the reviewer hook call."""
        from orchestrator.models import ModelRegistry
        contract = _make_contract()
        components = [ComponentSlice("c0", ["a.py"], "file a")]
        plan = QualityPlan(
            mode=MODE_COMPONENT_PANEL,
            reviewers=[
                ReviewerRole("maintainer"),
                ReviewerRole("reviewer", model_policy="cheap_cross_family"),
            ],
        )

        models = []

        def hook(prompt, model, workspace_root):
            models.append(model)
            return {"status": "PASS", "reason": "ok", "score": 0.9, "issues": [], "files_reviewed": ["a.py", "b.py"]}

        orig_hook = _REVIEWER_HOOK
        try:
            import orchestrator.quality_plane as qp_mod
            qp_mod._REVIEWER_HOOK = hook
            artifacts = _run_component_reviews(contract, components, [], ".", plan)
        finally:
            qp_mod._REVIEWER_HOOK = orig_hook

        assert set(models) == {ModelRegistry.default_qc(), ModelRegistry.FALLBACK}
        assert {a.model for a in artifacts} == {ModelRegistry.default_qc(), ModelRegistry.FALLBACK}


class TestInvokeReviewerLLMTransport:
    """_invoke_reviewer must call the generic LLM transport, inject the
    MASTER_CONTEXT envelope for premium models, and fail closed on errors."""

    def _call(self, model, prompt="prompt", response=None, side_effect=None):
        import orchestrator.quality_plane as qp_mod
        from orchestrator.llm import LLMError
        from unittest.mock import patch

        def _default_response(*a, **k):
            return {"text": '{"status":"PASS","score":0.9,"issues":[]}', "provider": "openai"}

        eff = side_effect if side_effect is not None else _default_response if response is None else response
        with patch("orchestrator.quality_plane.call_llm", side_effect=eff) as llm_mock:
            raw = qp_mod._invoke_reviewer(prompt, model, ".")
        assert raw["status"] == "PASS"
        return llm_mock

    def test_uses_generic_llm_transport(self):
        sub = self._call("openai:gpt-4o-mini")
        assert sub.call_count == 1
        prompt_arg, model_arg = sub.call_args.args[0], sub.call_args.args[1]
        assert model_arg == "openai:gpt-4o-mini"
        assert prompt_arg == "prompt"

    def test_premium_model_injects_master_context(self):
        sub = self._call("kimi-k2")
        prompt_arg = sub.call_args.args[0]
        assert "[MASTER_CONTEXT]" in prompt_arg
        assert "[CONTEXT_COMPLETE]" in prompt_arg

    def test_cheap_model_no_master_context(self):
        sub = self._call("openai:gpt-4o-mini")
        prompt_arg = sub.call_args.args[0]
        assert "[MASTER_CONTEXT]" not in prompt_arg

    def test_llm_error_fails_closed(self):
        import orchestrator.quality_plane as qp_mod
        from orchestrator.llm import LLMError
        with patch("orchestrator.quality_plane.call_llm",
                   side_effect=LLMError("auth failed", provider="openai", status=401)):
            raw = qp_mod._invoke_reviewer("prompt", "openai:gpt-4o-mini", ".")
        assert raw["status"] == "ERROR"
        assert "QC invocation error" in raw["reason"]


class TestNormalizeIssueEvidence:
    """Structured evidence objects must normalize to text without breaking
    legacy strings, _REVIEWER_HOOK, or FAKE_QC."""

    def test_legacy_string_passthrough(self):
        import orchestrator.quality_plane as qp_mod
        assert qp_mod._normalize_issue_evidence("worker.py:12 quote") == "worker.py:12 quote"

    def test_none_empty(self):
        import orchestrator.quality_plane as qp_mod
        assert qp_mod._normalize_issue_evidence(None) == ""

    def test_full_structured_object(self):
        import orchestrator.quality_plane as qp_mod
        ev = {
            "path": "orchestrator/worker.py",
            "symbol": "run_task",
            "line_range": "120-136",
            "excerpt": "def run_task():",
            "type": "implementation",
        }
        out = qp_mod._normalize_issue_evidence(ev)
        assert "path=orchestrator/worker.py" in out
        assert "symbol=run_task" in out
        assert "lines=120-136" in out
        assert "type=implementation" in out
        assert "excerpt=def run_task():" in out

    def test_partial_object_uses_available_keys(self):
        import orchestrator.quality_plane as qp_mod
        out = qp_mod._normalize_issue_evidence({"file": "lock.py", "line": "44"})
        assert "path=lock.py" in out
        assert "lines=44" in out

    def test_parsed_issue_keeps_normalized_evidence(self):
        import orchestrator.quality_plane as qp_mod
        raw = {
            "status": "REJECT",
            "score": 0.3,
            "reason": "evidence gap",
            "issues": [{
                "severity": "P1",
                "description": "x",
                "evidence": {
                    "path": "orchestrator/lock.py",
                    "symbol": "_pid_alive",
                    "line_range": "78-85",
                    "type": "implementation",
                },
            }],
        }
        parsed = qp_mod._parse_reviewer_response(
            raw, "r1", "reviewer", "kimi-k2", "c0"
        )
        assert isinstance(parsed.issues[0].evidence, str)
        assert "path=orchestrator/lock.py" in parsed.issues[0].evidence


class TestConcurrentComponentWaves:
    """Wave calls must execute concurrently yet collect in deterministic order."""

    def test_wave_calls_run_in_parallel_and_keep_order(self):
        import orchestrator.quality_plane as qp_mod
        import threading
        import time

        contract = _make_contract()
        components = [
            ComponentSlice("c0", ["a.py"], "file a"),
            ComponentSlice("c1", ["b.py"], "file b"),
            ComponentSlice("c2", ["c.py"], "file c"),
        ]
        plan = QualityPlan(
            mode=MODE_COMPONENT_PANEL,
            reviewers=[
                ReviewerRole("maintainer", model_policy="default"),
                ReviewerRole("cross", model_policy="deep"),
                ReviewerRole("codex", model_policy="cheap_cross_family"),
            ],
        )

        # 3 components x 3 distinct-provider reviewers = 9 calls, all
        # sleeping 0.25s. Different providers land in DIFFERENT scheduler
        # waves; the executor runs each wave's calls concurrently
        # (serial = ~2.25s total, concurrent pool of 4 = ~0.75s).
        active = []
        lock = threading.Lock()
        max_active = 0

        def hook(prompt, model, workspace_root):
            nonlocal max_active
            with lock:
                active.append(object())
                max_active = max(max_active, len(active))
            time.sleep(0.25)
            with lock:
                active.pop()
            return {
                "status": "PASS", "reason": "ok", "score": 0.9,
                "issues": [], "files_reviewed": ["a.py", "b.py"],
            }

        start = time.monotonic()
        orig_hook = qp_mod._REVIEWER_HOOK
        try:
            qp_mod._REVIEWER_HOOK = hook
            artifacts = _run_component_reviews(contract, components, [], ".", plan)
        finally:
            qp_mod._REVIEWER_HOOK = orig_hook
        elapsed = time.monotonic() - start

        assert len(artifacts) == 9, "3 components x 3 reviewers = 9 artifacts"
        expected = ["c0"] * 3 + ["c1"] * 3 + ["c2"] * 3
        assert [a.component_id for a in artifacts] == expected, "deterministic order must be preserved"
        assert max_active > 1, "wave calls must run concurrently (max_active=1 means serial)"
        assert elapsed < 1.5, f"serialized wave too slow: {elapsed:.2f}s"
        assert len(active) == 0  # all hooks exited

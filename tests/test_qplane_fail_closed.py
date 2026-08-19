"""Fail-closed regression tests for the quality plane.

Covers the kimi-k2 REMAND cluster: REJECT verdicts never synthesize to
PASS, malformed reviewer JSON never raises, unknown statuses become
ERROR, unsupported modes never fall back to legacy, arbitration is
claim-scoped, component aggregation is permutation-invariant, and
budget/wall-clock limits are enforced.
"""

import types

import pytest

from orchestrator.component_slicer import ComponentSlice
from orchestrator.quality_plan import (
    MODE_ARBITRATION_ONLY,
    MODE_COMPONENT_PANEL,
    MODE_PANEL,
    QualityBudget,
    QualityPlan,
    ReviewerRole,
    validate_quality_plan,
)
from orchestrator.quality_plane import (
    _component_verdict_to_qp_verdict,
    _parse_reviewer_response,
    run_quality_plane,
)
from orchestrator.review_artifact import (
    VERDICT_CONDITIONAL_PASS,
    VERDICT_ERROR,
    VERDICT_INSUFFICIENT_EVIDENCE,
    VERDICT_PASS,
    VERDICT_REJECT,
    EvidenceRead,
    ReviewArtifact,
    ReviewIssue,
    synthesize_artifacts,
)


def artifact(status, issues=None, evidence=True, confidence=0.8, component_id="component_0"):
    return ReviewArtifact(
        reviewer_id="r",
        role="maintainer",
        component_id=component_id,
        verdict=status,
        confidence=confidence,
        evidence_read=[EvidenceRead("out.txt", origin="cited")] if evidence else [],
        issues=issues or [],
    )


def plan(mode=MODE_PANEL, **budget):
    return QualityPlan(
        mode=mode,
        reviewers=[ReviewerRole("maintainer")],
        budget=QualityBudget(**budget),
    )


def contract_stub():
    return types.SimpleNamespace(
        quality_spec={},
        objective="test",
        acceptance_checks=[],
        qc={"lens": "code_correctness"},
        worker={"model": "test-worker"},
    )


class TestSynthesisPrecedence:
    def test_reject_verdict_with_empty_issues_is_reject(self):
        result = synthesize_artifacts([artifact(VERDICT_REJECT, [])])
        assert result.status == VERDICT_REJECT

    def test_error_verdict_precedes_evidence(self):
        result = synthesize_artifacts(
            [
                artifact(VERDICT_PASS),
                artifact(VERDICT_ERROR, evidence=True),
            ]
        )
        assert result.status == VERDICT_ERROR

    def test_error_precedes_reject(self):
        result = synthesize_artifacts(
            [
                artifact(VERDICT_REJECT),
                artifact(VERDICT_ERROR),
            ]
        )
        assert result.status == VERDICT_ERROR

    def test_all_error_panel_without_evidence_is_error_not_ie(self):
        result = synthesize_artifacts(
            [
                artifact(VERDICT_ERROR, evidence=False),
                artifact(VERDICT_ERROR, evidence=False),
            ]
        )
        assert result.status == VERDICT_ERROR

    def test_p0_without_claim_is_not_a_blocker(self):
        result = synthesize_artifacts(
            [
                artifact(
                    VERDICT_PASS,
                    [ReviewIssue(severity="P0", claim="", evidence="")],
                )
            ]
        )
        assert result.status == VERDICT_PASS
        assert result.p0_blockers == []

    def test_pure_pass_panel_is_pass(self):
        result = synthesize_artifacts([artifact(VERDICT_PASS), artifact(VERDICT_PASS)])
        assert result.status == VERDICT_PASS


class TestStrictNormalization:
    @pytest.mark.parametrize("issues", [None, ["x"], [None], {"severity": "P0"}, 5])
    def test_malformed_issues_are_error_artifacts(self, issues):
        raw = {"status": VERDICT_PASS, "issues": issues}
        result = _parse_reviewer_response(raw, "r", "maintainer", "model", "component_0", ["out.txt"])
        assert result.verdict == VERDICT_ERROR

    def test_non_dict_raw_is_error(self):
        result = _parse_reviewer_response(["PASS"], "r", "maintainer", "model", "component_0", ["out.txt"])
        assert result.verdict == VERDICT_ERROR

    @pytest.mark.parametrize("score", ["nan", "inf", "-inf", -5, 2.0, "unknown"])
    def test_invalid_scores_clamp_to_zero_then_pass_boost(self, score):
        result = _parse_reviewer_response(
            {"status": VERDICT_PASS, "score": score, "issues": [], "files_reviewed": ["out.txt"]},
            "r",
            "maintainer",
            "model",
            "component_0",
            ["out.txt"],
        )
        assert result.verdict == VERDICT_PASS
        assert result.confidence == pytest.approx(0.9)

    @pytest.mark.parametrize("status", ["APPROVED", "PASS ", None, 42, ""])
    def test_unknown_status_is_error(self, status):
        result = _parse_reviewer_response(
            {"status": status, "issues": []},
            "r",
            "maintainer",
            "model",
            "component_0",
            ["out.txt"],
        )
        assert result.verdict == VERDICT_ERROR

    def test_malformed_issue_fields_do_not_raise(self):
        raw = {
            "status": "REJECT",
            "score": 0.3,
            "issues": [
                {"severity": "CRITICAL", "line": "not-a-line", "description": 42},
                {"severity": "MAJOR", "line": None, "description": None},
            ],
        }
        result = _parse_reviewer_response(raw, "r", "maintainer", "model", "component_0", ["out.txt"])
        assert result.verdict == "REJECT"
        assert result.issues[0].line == 0
        assert result.issues[1].claim == ""


class TestModeDispatch:
    def test_component_panel_routes_to_panel_not_legacy(self, monkeypatch):
        called = {"legacy": False}

        def legacy(*args, **kwargs):
            called["legacy"] = True
            raise AssertionError("legacy QC path was used")

        monkeypatch.setattr("orchestrator.quality_plane.run_qc_review", legacy)
        monkeypatch.setattr(
            "orchestrator.quality_plane._invoke_reviewer",
            lambda *args, **kwargs: {"status": "PASS", "score": 1.0, "issues": [], "files_reviewed": ["out.txt"]},
        )

        result = run_quality_plane(
            contract_stub(),
            ["out.txt"],
            [],
            ".",
            quality_plan=plan(MODE_COMPONENT_PANEL),
        )
        assert result.status == VERDICT_PASS
        assert called["legacy"] is False

    def test_panel_mode_routes_to_panel_not_legacy(self, monkeypatch):
        called = {"legacy": False}

        def legacy(*args, **kwargs):
            called["legacy"] = True
            raise AssertionError("legacy QC path was used")

        monkeypatch.setattr("orchestrator.quality_plane.run_qc_review", legacy)
        monkeypatch.setattr(
            "orchestrator.quality_plane._invoke_reviewer",
            lambda *args, **kwargs: {"status": "PASS", "score": 1.0, "issues": [], "files_reviewed": ["out.txt"]},
        )

        result = run_quality_plane(
            contract_stub(),
            ["out.txt"],
            [],
            ".",
            quality_plan=plan(MODE_PANEL),
        )
        assert result.status == VERDICT_PASS
        assert called["legacy"] is False

    def test_arbitration_only_is_rejected_and_not_legacy(self, monkeypatch):
        qp = QualityPlan(mode=MODE_ARBITRATION_ONLY)
        assert any("arbitration_only" in error for error in validate_quality_plan(qp))

        def legacy(*args, **kwargs):
            raise AssertionError("legacy QC path was used")

        monkeypatch.setattr("orchestrator.quality_plane.run_qc_review", legacy)
        result = run_quality_plane(contract_stub(), [], [], ".", quality_plan=qp)
        assert result.status == VERDICT_ERROR
        assert "arbitration_only" in result.reason


class TestClaimScopedArbitration:
    def test_arbitration_pass_without_claim_coverage_retains_blockers(self, monkeypatch):
        blocker = ReviewIssue(severity="P0", claim="dangerous operation")
        artifacts = [artifact(VERDICT_PASS, [blocker])]
        qp = plan()
        qp.arbitration.enabled = True

        monkeypatch.setattr(
            "orchestrator.quality_plane._should_arbitrate",
            lambda *args: True,
        )
        monkeypatch.setattr(
            "orchestrator.quality_plane._run_arbitration",
            lambda *args, **kwargs: types.SimpleNamespace(
                status=VERDICT_PASS,
                confidence=0.95,
                winning_claims=["unrelated claim"],
                to_dict=lambda: {"status": VERDICT_PASS},
            ),
        )

        result = _component_verdict_to_qp_verdict(
            artifacts,
            [ComponentSlice("component_0", ["out.txt"])],
            qp,
            ".",
        )
        assert result.status == VERDICT_REJECT
        assert result.synthesis_result["p0_blockers"]

    def test_arbitration_pass_with_claim_coverage_clears_blockers(self, monkeypatch):
        blocker = ReviewIssue(severity="P0", claim="dangerous operation")
        artifacts = [artifact(VERDICT_PASS, [blocker])]
        qp = plan()
        qp.arbitration.enabled = True

        monkeypatch.setattr(
            "orchestrator.quality_plane._should_arbitrate",
            lambda *args: True,
        )
        monkeypatch.setattr(
            "orchestrator.quality_plane._run_arbitration",
            lambda *args, **kwargs: types.SimpleNamespace(
                status=VERDICT_PASS,
                confidence=0.95,
                winning_claims=["The dangerous operation is acceptable"],
                to_dict=lambda: {"status": VERDICT_PASS},
            ),
        )

        result = _component_verdict_to_qp_verdict(
            artifacts,
            [ComponentSlice("component_0", ["out.txt"])],
            qp,
            ".",
        )
        assert result.status == VERDICT_PASS
        assert result.synthesis_result["p0_blockers"] == []

    def test_arbitration_skipped_when_budget_exhausted(self, monkeypatch):
        blocker = ReviewIssue(severity="P0", claim="dangerous operation")
        artifacts = [artifact(VERDICT_PASS, [blocker])]
        qp = plan(max_llm_calls=1)
        qp.arbitration.enabled = True

        monkeypatch.setattr(
            "orchestrator.quality_plane._should_arbitrate",
            lambda *args: True,
        )
        monkeypatch.setattr(
            "orchestrator.quality_plane._run_arbitration",
            lambda *args: (_ for _ in ()).throw(AssertionError("arbitration must not run when budget is exhausted")),
        )

        result = _component_verdict_to_qp_verdict(
            artifacts,
            [ComponentSlice("component_0", ["out.txt"])],
            qp,
            ".",
        )
        assert result.status == VERDICT_REJECT
        assert "budget exhausted" in result.reason


class TestOrderIndependentAggregation:
    @pytest.mark.parametrize(
        "statuses",
        [
            [VERDICT_ERROR, VERDICT_REJECT],
            [VERDICT_REJECT, VERDICT_ERROR],
            [VERDICT_REJECT, VERDICT_ERROR, VERDICT_PASS],
        ],
    )
    def test_component_aggregation_is_order_independent(self, statuses):
        artifacts = [artifact(status) for status in statuses]
        qp = plan()
        result = _component_verdict_to_qp_verdict(
            artifacts,
            [ComponentSlice("component_0", ["out.txt"])],
            qp,
            ".",
        )
        assert result.component_verdicts[0]["status"] == VERDICT_ERROR

    def test_ie_vs_pass_ranking(self):
        artifacts = [artifact(VERDICT_PASS), artifact(VERDICT_INSUFFICIENT_EVIDENCE)]
        qp = plan()
        result = _component_verdict_to_qp_verdict(
            artifacts,
            [ComponentSlice("component_0", ["out.txt"])],
            qp,
            ".",
        )
        assert result.component_verdicts[0]["status"] == VERDICT_INSUFFICIENT_EVIDENCE


class TestBudgetAndWallClock:
    def test_estimate_calls_excludes_local_synthesis(self):
        qp = plan(MODE_PANEL, max_llm_calls=8)
        assert qp.estimate_calls() == 1

    def test_zero_wall_clock_budget_marks_calls_error(self, monkeypatch):
        contract = contract_stub()
        qp = plan(MODE_PANEL, max_wall_clock_sec=0)
        monkeypatch.setattr(
            "orchestrator.quality_plane._invoke_reviewer",
            lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("reviewer should not be invoked")),
        )

        result = run_quality_plane(
            contract,
            ["out.txt"],
            [],
            ".",
            quality_plan=qp,
        )
        assert result.status == VERDICT_ERROR
        assert result.review_artifacts
        assert all(item["reason"] == "insufficient remaining wall clock budget" for item in result.review_artifacts)


class TestErrorVerdictSemantics:
    def test_error_reason_never_claims_pass(self):
        result = synthesize_artifacts([artifact(VERDICT_ERROR, evidence=False)])
        assert result.status == VERDICT_ERROR
        qp = plan(MODE_PANEL)
        verdict = _component_verdict_to_qp_verdict(
            [artifact(VERDICT_ERROR, evidence=False)],
            [ComponentSlice("component_0", ["out.txt"])],
            qp,
            ".",
        )
        assert verdict.reason == "quality review failed"
        assert "All components passed review" not in verdict.reason

    def test_arbitration_pass_cannot_clear_error(self, monkeypatch):
        artifacts = [artifact(VERDICT_ERROR, evidence=False)]
        qp = plan(MODE_PANEL)
        qp.arbitration.enabled = True

        monkeypatch.setattr(
            "orchestrator.quality_plane._should_arbitrate",
            lambda *args: True,
        )
        monkeypatch.setattr(
            "orchestrator.quality_plane._run_arbitration",
            lambda *args, **kwargs: types.SimpleNamespace(
                status=VERDICT_PASS,
                winning_claims=["everything is fine"],
                to_dict=lambda: {"status": VERDICT_PASS},
            ),
        )

        result = _component_verdict_to_qp_verdict(
            artifacts,
            [ComponentSlice("component_0", ["out.txt"])],
            qp,
            ".",
        )
        assert result.status == VERDICT_ERROR
        assert result.passed is False
        assert result.arbitration_result is not None

    def test_arbitration_reject_cannot_relabel_error(self, monkeypatch):
        artifacts = [artifact(VERDICT_ERROR, evidence=False)]
        qp = plan(MODE_PANEL)
        qp.arbitration.enabled = True

        monkeypatch.setattr(
            "orchestrator.quality_plane._should_arbitrate",
            lambda *args: True,
        )
        monkeypatch.setattr(
            "orchestrator.quality_plane._run_arbitration",
            lambda *args, **kwargs: types.SimpleNamespace(
                status=VERDICT_REJECT,
                winning_claims=[],
                to_dict=lambda: {"status": VERDICT_REJECT},
            ),
        )

        result = _component_verdict_to_qp_verdict(
            artifacts,
            [ComponentSlice("component_0", ["out.txt"])],
            qp,
            ".",
        )
        assert result.status == VERDICT_ERROR
        assert result.passed is False

    def test_error_mixed_with_pass_reviewers_stays_error(self):
        result = synthesize_artifacts(
            [
                artifact(VERDICT_PASS),
                artifact(VERDICT_ERROR, evidence=False),
            ]
        )
        assert result.status == VERDICT_ERROR


class TestArbitrationParseHardening:
    def test_non_dict_raw_is_reject_not_raise(self):
        from orchestrator.quality_plane import _raw_to_arbitration_verdict

        verdict = _raw_to_arbitration_verdict(["PASS"])
        assert verdict.status == VERDICT_REJECT
        assert verdict.confidence == 0.0

    @pytest.mark.parametrize("confidence", ["high", "nan", "inf", None, [0.9]])
    def test_non_numeric_confidence_never_raises(self, confidence):
        from orchestrator.quality_plane import _raw_to_arbitration_verdict

        verdict = _raw_to_arbitration_verdict({"status": VERDICT_PASS, "confidence": confidence})
        assert verdict.confidence == 0.0
        assert verdict.status == VERDICT_PASS

    def test_string_winning_claims_are_not_iterated_as_chars(self):
        from orchestrator.quality_plane import _raw_to_arbitration_verdict

        verdict = _raw_to_arbitration_verdict({"status": VERDICT_PASS, "winning_claims": "dangerous operation is fine"})
        assert verdict.winning_claims == []

    def test_claims_filter_to_strings(self):
        from orchestrator.quality_plane import _raw_to_arbitration_verdict

        verdict = _raw_to_arbitration_verdict({"status": VERDICT_PASS, "winning_claims": ["ok", 5, None, "also ok"]})
        assert verdict.winning_claims == ["ok", "also ok"]

    def test_arbitration_with_none_budget_does_not_raise(self, monkeypatch):
        blocker = ReviewIssue(severity="P0", claim="dangerous operation")
        artifacts = [artifact(VERDICT_PASS, [blocker])]
        qp = plan(MODE_PANEL, max_llm_calls=None)
        qp.arbitration.enabled = True

        monkeypatch.setattr(
            "orchestrator.quality_plane._should_arbitrate",
            lambda *args: True,
        )
        monkeypatch.setattr(
            "orchestrator.quality_plane._run_arbitration",
            lambda *args, **kwargs: types.SimpleNamespace(
                status=VERDICT_REJECT,
                winning_claims=[],
                to_dict=lambda: {"status": VERDICT_REJECT},
            ),
        )

        result = _component_verdict_to_qp_verdict(
            artifacts,
            [ComponentSlice("component_0", ["out.txt"])],
            qp,
            ".",
        )
        assert result.status == VERDICT_REJECT


class TestGatewayHardening:
    def test_invalid_mode_never_falls_back_to_legacy(self, monkeypatch):
        qp = QualityPlan(
            mode="bogus_mode",
            reviewers=[ReviewerRole("maintainer")],
            budget=QualityBudget(),
        )

        def legacy(*args, **kwargs):
            raise AssertionError("legacy QC path must not run for invalid mode")

        monkeypatch.setattr("orchestrator.quality_plane.run_qc_review", legacy)
        result = run_quality_plane(contract_stub(), [], [], ".", quality_plan=qp)
        assert result.status == VERDICT_ERROR
        assert "bogus_mode" in result.reason

    def test_gateway_rejects_invalid_plan_without_dispatch(self, monkeypatch):
        qp = QualityPlan(
            mode=MODE_PANEL,
            reviewers=[],
            budget=QualityBudget(max_llm_calls=0),
        )

        def legacy(*args, **kwargs):
            raise AssertionError("must not dispatch")

        monkeypatch.setattr("orchestrator.quality_plane.run_qc_review", legacy)
        result = run_quality_plane(contract_stub(), ["out.txt"], [], ".", quality_plan=qp)
        assert result.status == VERDICT_ERROR
        assert "invalid quality plan" in result.reason

    def test_gateway_degrades_over_budget_plan(self, monkeypatch):
        # 3 reviewers, budget for 1 call -> must degrade to SINGLE, not run 3
        qp = QualityPlan(
            mode=MODE_PANEL,
            reviewers=[ReviewerRole("maintainer"), ReviewerRole("minimalist"), ReviewerRole("product_owner")],
            budget=QualityBudget(max_llm_calls=1),
        )
        captured = {}

        def hook(prompt, model, workspace_root, **kwargs):
            captured["calls"] = captured.get("calls", 0) + 1
            return {"status": "PASS", "score": 0.9, "issues": [], "files_reviewed": ["out.txt"]}

        monkeypatch.setattr("orchestrator.quality_plane._invoke_reviewer", hook)
        result = run_quality_plane(contract_stub(), ["out.txt"], [], ".", quality_plan=qp)
        assert result.status == VERDICT_PASS
        assert captured["calls"] == 1

    def test_arbitration_only_reason_comes_from_validation(self, monkeypatch):
        qp = QualityPlan(
            mode=MODE_ARBITRATION_ONLY,
            reviewers=[ReviewerRole("maintainer")],
            budget=QualityBudget(),
        )

        def legacy(*args, **kwargs):
            raise AssertionError("legacy QC path was used")

        monkeypatch.setattr("orchestrator.quality_plane.run_qc_review", legacy)
        result = run_quality_plane(contract_stub(), [], [], ".", quality_plan=qp)
        assert result.status == VERDICT_ERROR
        assert "arbitration_only" in result.reason


class TestComponentResolvedStatus:
    def test_arbitrated_component_shows_resolved_status(self, monkeypatch):
        blocker = ReviewIssue(severity="P0", claim="dangerous operation")
        artifacts = [artifact(VERDICT_REJECT, [blocker])]
        qp = plan()
        qp.arbitration.enabled = True

        monkeypatch.setattr(
            "orchestrator.quality_plane._should_arbitrate",
            lambda *args: True,
        )
        monkeypatch.setattr(
            "orchestrator.quality_plane._run_arbitration",
            lambda *args, **kwargs: types.SimpleNamespace(
                status=VERDICT_PASS,
                confidence=0.95,
                winning_claims=["dangerous operation is acceptable"],
                to_dict=lambda: {"status": VERDICT_PASS},
            ),
        )

        result = _component_verdict_to_qp_verdict(
            artifacts,
            [ComponentSlice("component_0", ["out.txt"])],
            qp,
            ".",
        )
        assert result.status == VERDICT_PASS
        cv = result.component_verdicts[0]
        assert cv["status"] == VERDICT_PASS
        assert cv["raw_status"] == VERDICT_REJECT
        assert cv["arbitrated"] is True

    def test_no_arbitration_no_arbitrated_flag(self):
        result = _component_verdict_to_qp_verdict(
            [artifact(VERDICT_PASS)],
            [ComponentSlice("component_0", ["out.txt"])],
            plan(),
            ".",
        )
        cv = result.component_verdicts[0]
        assert cv["status"] == VERDICT_PASS
        assert "arbitrated" not in cv


class TestSeverityStrict:
    def test_unknown_severity_becomes_error_artifact(self):
        from orchestrator.quality_plane import _parse_reviewer_response

        raw = {"status": "REJECT", "score": 0.5, "issues": [{"severity": "BLOCKER", "description": "bad"}]}
        result = _parse_reviewer_response(raw, "r", "maintainer", "m", "component_0", ["out.txt"])
        assert result.verdict == VERDICT_ERROR
        assert "unknown severity" in result.reason


class TestEvidenceOrigin:
    def test_provided_evidence_default(self):
        from orchestrator.quality_plane import _parse_reviewer_response

        raw = {"status": "PASS", "score": 0.9, "issues": []}
        result = _parse_reviewer_response(raw, "r", "maintainer", "m", "component_0", ["out.txt"])
        assert all(er.origin == "provided" for er in result.evidence_read)

    def test_cited_evidence_detected(self):
        from orchestrator.quality_plane import _parse_reviewer_response

        raw = {
            "status": "REJECT",
            "score": 0.5,
            "issues": [
                {
                    "severity": "CRITICAL",
                    "description": "bad code",
                    "path": "out.txt",
                    "evidence": "see out.txt line 12",
                }
            ],
        }
        result = _parse_reviewer_response(raw, "r", "maintainer", "m", "component_0", ["out.txt"])
        cited = [er for er in result.evidence_read if er.origin == "cited"]
        assert len(cited) == 1
        assert cited[0].path == "out.txt"

    def test_evidence_roundtrip_preserves_origin(self):
        from orchestrator.review_artifact import EvidenceRead

        er = EvidenceRead("out.txt", "line 12", origin="cited")
        restored = EvidenceRead.from_dict(er.to_dict())
        assert restored.origin == "cited"


class TestRedaction:
    def test_redacts_sk_and_gw_keys(self):
        from orchestrator.qc_review import _redact_secrets

        text = "key=sk-abc123def456ghi789jkl0123456789 secret gw_sk_abcdef1234567890abcdef12 ok"
        out = _redact_secrets(text)
        assert "sk-abc[REDACTED]" in out
        assert "sk-abc123def456ghi789jkl0123456789" not in out
        assert "gw_sk_[REDACTED]" in out
        assert "abcdef1234567890abcdef12" not in out

    def test_redacts_aws_and_github(self):
        from orchestrator.qc_review import _redact_secrets

        text = "AKIAABCDEFGHIJKLMNOP ghp_abcdefghijklmnopqrstuvwxyz1234567890 plain"
        out = _redact_secrets(text)
        assert "AKIAABCDEFGHIJKLMNOP" not in out
        assert "ghp_abcdefghijklmnopqrstuvwxyz1234567890" not in out
        assert "plain" in out

    def test_legit_content_survives(self):
        from orchestrator.qc_review import _redact_secrets

        text = "normal code review content with no secrets"
        assert _redact_secrets(text) == text


class TestRoundAFindings:
    def test_validate_never_raises_on_non_int_budget_fields(self):
        for kw in (
            {"max_components": "x"},
            {"max_llm_calls": "x"},
            {"max_reviewers_per_component": "x"},
            {"max_wall_clock_sec": "x"},
            {"max_components": None},
            {"max_reviewers_per_component": None},
            {"max_wall_clock_sec": None},
            {"max_components": True},
        ):
            qp = plan(**kw)
            errors = validate_quality_plan(qp)
            assert errors, kw

    def test_gateway_returns_error_verdict_not_crash_on_bad_budget_types(self, monkeypatch):
        qp = QualityPlan(
            mode=MODE_PANEL,
            reviewers=[ReviewerRole("maintainer")],
            budget=QualityBudget(max_components="x"),
        )

        def legacy(*args, **kwargs):
            raise AssertionError("must not dispatch")

        monkeypatch.setattr("orchestrator.quality_plane.run_qc_review", legacy)
        result = run_quality_plane(contract_stub(), ["out.txt"], [], ".", quality_plan=qp)
        assert result.status == VERDICT_ERROR
        assert "invalid quality plan" in result.reason

    def test_artifact_from_dict_coerces_invalid_values_fail_closed(self):
        from orchestrator.review_artifact import ArbitrationVerdict, ReviewArtifact

        a = ReviewArtifact.from_dict(
            {
                "reviewer_id": "r",
                "role": "x",
                "verdict": "BOGUS",
                "confidence": 7.0,
                "issues": [{"severity": "BOGUS", "claim": "c"}],
                "evidence_read": [{"path": "a.py"}, "not-a-dict"],
            }
        )
        assert a.verdict == VERDICT_INSUFFICIENT_EVIDENCE
        assert a.confidence == 1.0
        assert a.issues[0].severity == "P3"
        assert len(a.evidence_read) == 1
        arb = ArbitrationVerdict.from_dict(
            {
                "status": "BOGUS",
                "confidence": float("inf"),
                "winning_claims": ["ok", 5],
            }
        )
        assert arb.status == VERDICT_REJECT
        assert arb.confidence == 0.0
        assert arb.winning_claims == ["ok"]

    def test_arbitration_skipped_when_deadline_passed(self, monkeypatch):
        import time as _time

        from orchestrator.quality_plane import _component_verdict_to_qp_verdict

        qp = plan()
        qp.arbitration.enabled = True
        monkeypatch.setattr("orchestrator.quality_plane._should_arbitrate", lambda *a: True)
        monkeypatch.setattr(
            "orchestrator.quality_plane._run_arbitration",
            lambda *a: (_ for _ in ()).throw(AssertionError("must not run after deadline")),
        )
        result = _component_verdict_to_qp_verdict(
            [artifact(VERDICT_PASS)],
            [ComponentSlice("component_0", ["out.txt"])],
            qp,
            ".",
            deadline=_time.monotonic() - 1,
        )
        assert result.arbitration_result is None

    def test_invoke_reviewer_forwards_timeout_and_returns_error_on_llm_failure(self, monkeypatch):
        import orchestrator.quality_plane as qp_mod
        from orchestrator.llm import LLMError

        captured = {}

        def fake_call_llm(prompt, model, timeout_s=None):
            captured["timeout_s"] = timeout_s
            raise LLMError("connection refused")

        monkeypatch.setattr(qp_mod, "call_llm", fake_call_llm)
        monkeypatch.delenv("FAKE_QC", raising=False)
        raw = qp_mod._invoke_reviewer("prompt", "model", ".", timeout_sec=42)
        assert raw["status"] == "ERROR"
        assert "QC invocation error" in raw["reason"]
        assert captured["timeout_s"] == 42

    def test_schedule_one_call_per_provider_per_wave(self):
        from orchestrator.provider_scheduler import CallSpec, ProviderScheduler

        calls = [CallSpec(call_id=str(i), prompt="p", model="any:model", reviewer_id="r") for i in range(3)]
        waves = ProviderScheduler().schedule(calls)
        assert len(waves) == 3
        for w in waves:
            assert len(w.calls) == 1
        assert sum(len(w.calls) for w in waves) == 3

    def test_synthesis_requires_cited_evidence_for_pass(self):
        provided = ReviewArtifact(
            reviewer_id="r",
            role="maintainer",
            verdict=VERDICT_PASS,
            confidence=0.9,
            evidence_read=[EvidenceRead("out.txt")],
        )
        result = synthesize_artifacts([provided])
        assert result.status == VERDICT_INSUFFICIENT_EVIDENCE
        assert not provided.has_cited_evidence()

    def test_files_reviewed_counts_as_cited_evidence(self):
        from orchestrator.quality_plane import _parse_reviewer_response

        raw = {"status": "PASS", "score": 0.9, "issues": [], "files_reviewed": ["out.txt"]}
        result = _parse_reviewer_response(raw, "r", "maintainer", "m", "component_0", ["out.txt"])
        assert result.has_cited_evidence()
        assert result.verdict == VERDICT_PASS

    def test_prompt_redaction_covers_contract_metadata(self):
        from orchestrator.qc_review import _build_qc_prompt

        contract = contract_stub()
        contract.objective = "deploy with token sk-abc123def456ghi789jkl0123456789 stored in vault"
        contract.acceptance_checks = [
            {"kind": "cmd", "command": "run", "expected": "sk-abc123def456ghi789jkl0123456789"}
        ]
        prompt = _build_qc_prompt(contract, ["out.txt"], [], quality_spec={})
        assert "sk-abc[REDACTED]" in prompt
        assert "sk-abc123def456ghi789jkl0123456789" not in prompt

    def test_no_duplicate_to_dict(self):
        import inspect

        from orchestrator.review_artifact import QualityPlaneVerdict

        sources = inspect.getsource(QualityPlaneVerdict.to_dict)
        assert sources.count("def to_dict") == 1


class TestRoundBFindings:
    """Regressions for kimi-k2 re-review #3 (round B payload) findings N1-N7."""

    def test_arbitration_invocation_failure_fails_closed(self, monkeypatch):
        """N1: an arbiter invocation exception must become a structured
        REJECT verdict — never crash the quality plane."""
        from orchestrator.quality_plane import _component_verdict_to_qp_verdict

        qp = plan()
        qp.arbitration.enabled = True
        monkeypatch.setattr("orchestrator.quality_plane._should_arbitrate", lambda *a: True)

        def boom(*a, **kw):
            raise RuntimeError("arbiter subprocess exploded")

        monkeypatch.setattr("orchestrator.quality_plane._invoke_reviewer", boom)
        result = _component_verdict_to_qp_verdict(
            [artifact(VERDICT_PASS), artifact(VERDICT_REJECT)],
            [ComponentSlice("component_0", ["out.txt"])],
            qp,
            ".",
        )
        assert result.arbitration_result is not None
        assert result.arbitration_result["status"] == VERDICT_REJECT
        assert "arbitration failure" in result.arbitration_result["reason"]

    def test_synthesis_verdict_from_dict_fail_closed(self):
        """N2: persisted SynthesisVerdict records normalize fail-closed."""
        from orchestrator.review_artifact import SynthesisVerdict

        sv = SynthesisVerdict.from_dict(
            {
                "status": "BOGUS",
                "score": float("inf"),
                "merged_issues": ["not-a-dict", {"severity": "P1", "claim": "c"}],
                "unresolved_disagreements": "x",
                "recommended_next_actions": [1, 2],
            }
        )
        assert sv.status == VERDICT_INSUFFICIENT_EVIDENCE
        assert sv.score == 0.0
        assert len(sv.merged_issues) == 1
        assert sv.unresolved_disagreements == []
        assert sv.recommended_next_actions == []

    def test_plane_verdict_from_dict_fail_closed(self):
        """N2: persisted QualityPlaneVerdict records normalize fail-closed."""
        from orchestrator.review_artifact import QualityPlaneVerdict, SynthesisVerdict

        qv = QualityPlaneVerdict.from_dict(
            {
                "passed": True,
                "status": "BOGUS",
                "score": 9.9,
                "issues": ["bad"],
                "budget_used": "nope",
                "synthesis_result": {"status": "BOGUS", "score": float("inf")},
            }
        )
        assert qv.passed is False
        assert qv.status == VERDICT_INSUFFICIENT_EVIDENCE
        assert qv.score == 0.0
        assert qv.issues == []
        assert isinstance(qv.synthesis_result, SynthesisVerdict)
        assert qv.synthesis_result.status == VERDICT_INSUFFICIENT_EVIDENCE
        assert qv.synthesis_result.score == 0.0

    def test_invoke_reviewer_timeout_zero_is_honored(self, monkeypatch):
        """N3: timeout_sec=0 must be forwarded to the LLM transport verbatim,
        not replaced by the 120s default."""
        import orchestrator.quality_plane as qp_mod
        from orchestrator.llm import LLMError
        from orchestrator.quality_plane import _invoke_reviewer

        captured = {}

        def fake_call_llm(prompt, model, timeout_s=None):
            captured["timeout_s"] = timeout_s
            raise LLMError("timed out")

        monkeypatch.setattr(qp_mod, "call_llm", fake_call_llm)
        monkeypatch.delenv("FAKE_QC", raising=False)
        raw = _invoke_reviewer("prompt", "model", ".", timeout_sec=0)
        assert captured["timeout_s"] == 0
        assert raw["status"] == "ERROR"

    def test_arbitration_pass_resolves_only_covered_component(self, monkeypatch):
        """N4: an arbiter PASS resolves only the component whose P0 claim it
        covers; unrelated components keep their own verdicts."""
        from orchestrator.quality_plane import _component_verdict_to_qp_verdict
        from orchestrator.review_artifact import ArbitrationVerdict

        qp = plan()
        qp.arbitration.enabled = True
        blocked = ReviewArtifact(
            reviewer_id="r1",
            role="maintainer",
            verdict=VERDICT_REJECT,
            confidence=0.3,
            component_id="c0",
            issues=[ReviewIssue(severity="P0", claim="deletes production data", evidence="x")],
            evidence_read=[EvidenceRead("out.txt", origin="cited")],
        )
        clean_ie = ReviewArtifact(
            reviewer_id="r2",
            role="reviewer",
            verdict=VERDICT_INSUFFICIENT_EVIDENCE,
            confidence=0.0,
            component_id="c1",
            evidence_read=[EvidenceRead("other.txt", origin="cited")],
        )

        def fake_arb(artifacts, synthesis, policy, workspace_root, deadline=None):
            return ArbitrationVerdict(status=VERDICT_PASS, winning_claims=["deletes production data"])

        monkeypatch.setattr("orchestrator.quality_plane._run_arbitration", fake_arb)
        result = _component_verdict_to_qp_verdict(
            [blocked, clean_ie],
            [ComponentSlice("c0", ["out.txt"]), ComponentSlice("c1", ["other.txt"])],
            qp,
            ".",
        )
        assert result.passed is True
        by_id = {v["component_id"]: v for v in result.component_verdicts}
        assert by_id["c0"]["status"] == VERDICT_PASS
        assert by_id["c0"]["arbitrated"] is True
        assert by_id["c1"]["status"] == VERDICT_INSUFFICIENT_EVIDENCE
        assert "arbitrated" not in by_id["c1"]

    def test_wildcard_files_reviewed_is_not_cited(self):
        """N5: '*' is not a file — self-attestation with a glob must not
        satisfy the cited-evidence gate."""
        from orchestrator.quality_plane import _parse_reviewer_response

        raw = {"status": "PASS", "score": 0.9, "issues": [], "files_reviewed": ["*"]}
        result = _parse_reviewer_response(raw, "r", "maintainer", "m", "component_0", ["out.txt"])
        assert not result.has_cited_evidence()
        assert synthesize_artifacts([result]).status == VERDICT_INSUFFICIENT_EVIDENCE

    def test_phantom_files_reviewed_is_not_cited(self):
        """N5: naming a file outside the component scope is not evidence."""
        from orchestrator.quality_plane import _parse_reviewer_response

        raw = {"status": "PASS", "score": 0.9, "issues": [], "files_reviewed": ["nope.txt"]}
        result = _parse_reviewer_response(raw, "r", "maintainer", "m", "component_0", ["out.txt"])
        assert not result.has_cited_evidence()

    def test_minimum_score_gate_downgrades_pass(self):
        """N6: quality_spec.minimum_score is a deterministic gate."""
        art = ReviewArtifact(
            reviewer_id="r",
            role="maintainer",
            verdict=VERDICT_PASS,
            confidence=0.6,
            evidence_read=[EvidenceRead("out.txt", origin="cited")],
        )
        result = synthesize_artifacts([art], quality_spec={"minimum_score": 0.9})
        assert result.status == VERDICT_CONDITIONAL_PASS
        assert any("minimum_score" in (i.claim or "") for i in result.p1_required_fixes)

    def test_hard_failures_gate_rejects(self):
        """N6: quality_spec.hard_failures identifiers are enforced in code."""
        art = ReviewArtifact(
            reviewer_id="r",
            role="maintainer",
            verdict=VERDICT_PASS,
            confidence=0.95,
            issues=[ReviewIssue(severity="P2", claim="kept placeholder TODO")],
            evidence_read=[EvidenceRead("out.txt", origin="cited")],
        )
        result = synthesize_artifacts([art], quality_spec={"hard_failures": ["placeholder"]})
        assert result.status == VERDICT_REJECT

    def test_minimum_counts_cited_evidence_gate(self):
        """N6: quality_spec.minimum_counts.cited_evidence is enforced."""
        art = ReviewArtifact(
            reviewer_id="r",
            role="maintainer",
            verdict=VERDICT_PASS,
            confidence=0.9,
            evidence_read=[EvidenceRead("a.py", origin="cited")],
        )
        result = synthesize_artifacts([art], quality_spec={"minimum_counts": {"cited_evidence": 2}})
        assert result.status == VERDICT_CONDITIONAL_PASS
        assert any("cited_evidence" in (i.claim or "") for i in result.p1_required_fixes)

    def test_arbitration_prompt_redacted(self):
        """N7: arbitration prompts must redact secret-shaped strings."""
        from orchestrator.quality_plane import _build_arbitration_prompt
        from orchestrator.review_artifact import SynthesisVerdict

        art = ReviewArtifact(
            reviewer_id="r",
            role="maintainer",
            verdict=VERDICT_REJECT,
            confidence=0.4,
            issues=[
                ReviewIssue(
                    severity="P0",
                    claim="key sk-abc123def456ghi789jkl0123456789 leaked in deployment",
                )
            ],
        )
        prompt = _build_arbitration_prompt(
            [art],
            SynthesisVerdict(status=VERDICT_REJECT, p0_blockers=[art.issues[0]]),
        )
        assert "sk-abc123def456ghi789jkl0123456789" not in prompt
        assert "sk-abc[REDACTED]" in prompt


class TestRoundCFindings:
    """Regressions for kimi-k2 re-review #4 (round C payload) findings F-NEW-1..7."""

    def test_component_review_deadline_passed_to_invoke(self, monkeypatch):
        """F-NEW-3: the wall-clock deadline must bound every reviewer
        subprocess — never the 120s default."""
        from orchestrator.quality_plane import _run_component_reviews

        captured = {}

        def fake_invoke(prompt, model, workspace_root, timeout_sec=None):
            captured["timeout_sec"] = timeout_sec
            return {"status": "PASS", "score": 0.9, "issues": [], "files_reviewed": ["out.txt"]}

        qp = plan(mode=MODE_PANEL)
        qp.budget.max_wall_clock_sec = 60
        deadline = __import__("time").monotonic() + 30
        monkeypatch.setattr("orchestrator.quality_plane._invoke_reviewer", fake_invoke)
        _run_component_reviews(
            contract_stub(),
            [ComponentSlice("c0", ["out.txt"])],
            [],
            ".",
            qp,
            deadline=deadline,
        )
        assert captured["timeout_sec"] is not None
        assert 1 <= captured["timeout_sec"] <= 30

    def test_evidence_citation_requires_exact_match(self):
        """F-NEW-1: substring citations are not evidence — 'foo' must not
        match 'foobar.py'."""
        from orchestrator.quality_plane import _parse_reviewer_response

        raw = {"status": "PASS", "score": 0.9, "issues": [], "files_reviewed": ["foo"]}
        result = _parse_reviewer_response(raw, "r", "maintainer", "m", "c0", ["foobar.py"])
        assert not result.has_cited_evidence()

    def test_evidence_citation_exact_basename_matches(self):
        """F-NEW-1: an exact basename citation still counts as evidence."""
        from orchestrator.quality_plane import _parse_reviewer_response

        raw = {"status": "PASS", "score": 0.9, "issues": [], "files_reviewed": ["foobar.py"]}
        result = _parse_reviewer_response(raw, "r", "maintainer", "m", "c0", ["foobar.py"])
        assert result.has_cited_evidence()

    def test_artifact_from_dict_normalizes_all_list_fields(self):
        """F-NEW-2: scalar persisted values must not crash serialization."""
        from orchestrator.review_artifact import ReviewArtifact

        a = ReviewArtifact.from_dict(
            {
                "reviewer_id": "r",
                "verdict": "PASS",
                "strengths": 7,
                "dissent": "x",
                "unsupported_claims": None,
                "owner_level_challenges": [1, "ok"],
                "elapsed_sec": "nope",
            }
        )
        assert a.strengths == []
        assert a.dissent == []
        assert a.unsupported_claims == []
        assert a.owner_level_challenges == ["ok"]
        assert a.elapsed_sec == 0.0
        d = a.to_dict()
        assert isinstance(d["strengths"], list)

    def test_arbitration_pass_does_not_promote_insufficient_evidence(self, monkeypatch):
        """F-NEW-4: an arbiter PASS must never turn IE into PASS."""
        from orchestrator.quality_plane import _component_verdict_to_qp_verdict
        from orchestrator.review_artifact import ArbitrationVerdict

        qp = plan()
        qp.arbitration.enabled = True
        ie = ReviewArtifact(
            reviewer_id="r",
            role="maintainer",
            verdict=VERDICT_INSUFFICIENT_EVIDENCE,
            confidence=0.0,
            component_id="c0",
            evidence_read=[EvidenceRead("out.txt", origin="cited")],
        )

        def fake_arb(artifacts, synthesis, policy, workspace_root, deadline=None):
            return ArbitrationVerdict(status=VERDICT_PASS, winning_claims=["n/a"])

        monkeypatch.setattr("orchestrator.quality_plane._run_arbitration", fake_arb)
        result = _component_verdict_to_qp_verdict(
            [ie],
            [ComponentSlice("c0", ["out.txt"])],
            qp,
            ".",
        )
        assert result.passed is False
        assert result.status == VERDICT_INSUFFICIENT_EVIDENCE

    def test_arbitration_pass_promotes_reject_when_covered(self, monkeypatch):
        """F-NEW-4: arbiter PASS still resolves a genuine P0-based REJECT."""
        from orchestrator.quality_plane import _component_verdict_to_qp_verdict
        from orchestrator.review_artifact import ArbitrationVerdict

        qp = plan()
        qp.arbitration.enabled = True
        rej = ReviewArtifact(
            reviewer_id="r",
            role="maintainer",
            verdict=VERDICT_REJECT,
            confidence=0.3,
            component_id="c0",
            issues=[ReviewIssue(severity="P0", claim="deletes data", evidence="x")],
            evidence_read=[EvidenceRead("out.txt", origin="cited")],
        )

        def fake_arb(artifacts, synthesis, policy, workspace_root, deadline=None):
            return ArbitrationVerdict(status=VERDICT_PASS, winning_claims=["deletes data"])

        monkeypatch.setattr("orchestrator.quality_plane._run_arbitration", fake_arb)
        result = _component_verdict_to_qp_verdict(
            [rej],
            [ComponentSlice("c0", ["out.txt"])],
            qp,
            ".",
        )
        assert result.passed is True
        assert result.status == VERDICT_PASS

    def test_degraded_plan_state_reaches_verdict(self):
        """F-NEW-6: degradation must be auditable on the emitted verdict."""
        import os
        import unittest.mock

        from orchestrator.quality_plane import run_quality_plane

        qp = plan(mode=MODE_PANEL)
        qp.budget.max_llm_calls = 1
        qp.reviewers = [ReviewerRole("maintainer"), ReviewerRole("minimalist")]
        # estimate_calls for a panel with 2 reviewers = 2 > 1 -> degrade
        contract = contract_stub()
        with unittest.mock.patch.dict(os.environ, {"FAKE_QC": "PASS"}):
            verdict = run_quality_plane(contract, ["out.txt"], [], ".", quality_plan=qp)
        assert verdict.degraded is True
        assert verdict.degrade_reason

    def test_legacy_prompt_file_cleanup_on_subprocess_exception(self, monkeypatch):
        """F-NEW-7: legacy run_qc_review must fail closed when the LLM
        transport errors, without leaking temp files."""
        import glob
        import os
        import tempfile

        from orchestrator.llm import LLMError
        from orchestrator.qc_review import run_qc_review

        def boom(*a, **kw):
            raise LLMError("subprocess exploded")

        monkeypatch.setattr(
            "orchestrator.qc_review.call_llm",
            boom,
        )
        monkeypatch.delenv("FAKE_QC", raising=False)

        preexisting = set(glob.glob(os.path.join(tempfile.gettempdir(), "qc_review_*.txt")))
        verdict = run_qc_review(contract_stub(), ["out.txt"], [], ".")
        leftover = set(glob.glob(os.path.join(tempfile.gettempdir(), "qc_review_*.txt")))
        assert leftover <= preexisting
        assert verdict.passed is False
        assert "QC invocation error" in verdict.reason

    def test_arbitration_verdict_from_dict_accepts_non_dict(self):
        """NEW-2 (T1 audit round 2): ArbitrationVerdict.from_dict must fail
        closed on non-dict input like every other from_dict."""
        from orchestrator.review_artifact import ArbitrationVerdict

        for bad in (None, "x", 7, ["a"]):
            av = ArbitrationVerdict.from_dict(bad)
            assert av.status == VERDICT_REJECT
            assert av.winning_claims == []

    def test_plane_verdict_roundtrip_is_json_serializable(self):
        """NEW-3 (T1 audit round 2): a from_dict-loaded QualityPlaneVerdict
        with nested synthesis/arbitration must reserialize to JSON without
        leaking typed objects."""
        import json as _json

        from orchestrator.review_artifact import QualityPlaneVerdict

        qv = QualityPlaneVerdict.from_dict(
            {
                "passed": True,
                "status": "PASS",
                "score": 0.9,
                "synthesis_result": {
                    "status": "PASS",
                    "score": 0.9,
                    "merged_issues": [{"severity": "P1", "claim": "c", "line": 3}],
                    "p0_blockers": [],
                    "p1_required_fixes": [],
                },
                "arbitration_result": {
                    "status": "PASS",
                    "confidence": 0.8,
                    "winning_claims": ["fixed"],
                    "discarded_claims": [],
                },
            }
        )
        d = qv.to_dict()
        _json.dumps(d)
        assert isinstance(d["synthesis_result"], dict)
        assert d["synthesis_result"]["status"] == "PASS"
        assert isinstance(d["arbitration_result"], dict)
        assert d["arbitration_result"]["winning_claims"] == ["fixed"]


class TestMandatoryQCNonDegradation:
    """Perpetual-loop r6: an explicit panel plan must not silently degrade
    to single-reviewer QC — hard block with an ERROR verdict."""

    def test_explicit_panel_does_not_degrade_to_single(self):
        from orchestrator.quality_plane import run_quality_plane

        qp = QualityPlan(
            mode=MODE_PANEL,
            reviewers=[ReviewerRole("maintainer"), ReviewerRole("minimalist")],
            budget=QualityBudget(max_llm_calls=1),
        )
        contract = contract_stub()
        contract.quality_plan = qp.to_dict()
        contract.quality_spec = {}
        verdict = run_quality_plane(contract, ["out.txt"], [], ".", quality_plan=qp)
        assert verdict.status == VERDICT_ERROR
        assert "cannot be degraded" in verdict.reason


class TestT1AuditRound2:
    """Regressions for the T1 audit round 2 findings NEW-4..NEW-9."""

    def test_legacy_path_enforces_minimum_score_gate(self):
        """NEW-4: quality_spec gates must hold on the legacy single-QC path
        (the budget-degraded route that skips synthesize_artifacts)."""
        from orchestrator.qc_review import QCVerdict
        from orchestrator.quality_plane import _apply_quality_spec_gates
        from orchestrator.review_artifact import QualityPlaneVerdict

        qcv = QCVerdict(
            passed=True,
            reason="ok",
            status="PASS",
            score=0.95,
            issues=[{"severity": "MINOR", "description": "cosmetic"}],
        )
        verdict = QualityPlaneVerdict.from_qc_verdict(qcv)
        _apply_quality_spec_gates(verdict, {"minimum_score": 0.99})
        assert verdict.passed is False
        assert verdict.status == VERDICT_CONDITIONAL_PASS
        assert "minimum_score" in verdict.reason

    def test_legacy_path_enforces_hard_failures_gate(self):
        """NEW-4: hard-failure identifiers are enforced on the legacy path."""
        from orchestrator.qc_review import QCVerdict
        from orchestrator.quality_plane import _apply_quality_spec_gates
        from orchestrator.review_artifact import QualityPlaneVerdict

        qcv = QCVerdict(
            passed=True,
            reason="ok",
            status="PASS",
            score=0.95,
            issues=[{"severity": "MINOR", "description": "kept placeholder TODO"}],
        )
        verdict = QualityPlaneVerdict.from_qc_verdict(qcv)
        _apply_quality_spec_gates(verdict, {"hard_failures": ["placeholder"]})
        assert verdict.passed is False
        assert verdict.status == VERDICT_REJECT

    def test_degraded_component_panel_enforces_gate_end_to_end(self, monkeypatch):
        """NEW-4: a COMPONENT_PANEL that degrades to MODE_SINGLE still gets
        quality-spec enforcement (via the legacy-path gate)."""
        import os as _os
        from unittest import mock as _mock

        from orchestrator.quality_plane import run_quality_plane

        qp = QualityPlan(
            mode=MODE_COMPONENT_PANEL,
            reviewers=[ReviewerRole("maintainer"), ReviewerRole("minimalist")],
            budget=QualityBudget(max_llm_calls=1),
        )
        contract = contract_stub()
        contract.quality_spec = {"minimum_score": 0.99}
        with _mock.patch.dict(_os.environ, {"FAKE_QC": "PASS"}):
            verdict = run_quality_plane(contract, ["out.txt"], [], ".", quality_plan=qp)
        assert verdict.degraded is True
        assert verdict.passed is False
        assert verdict.status == VERDICT_CONDITIONAL_PASS

    def test_persona_prompt_redacts_focus(self):
        """NEW-5: persona desc/focus appended after base-prompt redaction
        must be redacted too."""
        from orchestrator.quality_plane import _build_persona_prompt

        role = ReviewerRole("maintainer", focus=["deploy key sk-abc123def456ghi789jkl0123456789 safe"])
        prompt = _build_persona_prompt("base prompt", role)
        assert "sk-abc123def456ghi789jkl0123456789" not in prompt
        assert "sk-abc[REDACTED]" in prompt

    def test_provider_extraction_namespace_wins_over_substring(self):
        """NEW-6: namespaced models must be classified by their explicit
        provider prefix, never guessed from the model id content."""
        from orchestrator.provider_scheduler import _extract_provider

        assert _extract_provider("my-gateway:gemini-3.5-flash") == "my-gateway"
        assert _extract_provider("gemini:gemini-3.6-flash 3.6 Flash") == "gemini"
        assert _extract_provider("kimi-k2") == "any"

    def test_arbitration_deadline_skip_reason_is_accurate(self, monkeypatch):
        """NEW-8: a wall-clock-deadline skip must not be labeled budget
        exhaustion."""
        import time as _time

        from orchestrator.quality_plane import _component_verdict_to_qp_verdict

        qp = plan()
        qp.arbitration.enabled = True
        monkeypatch.setattr("orchestrator.quality_plane._should_arbitrate", lambda *a: True)
        result = _component_verdict_to_qp_verdict(
            [artifact(VERDICT_PASS)],
            [ComponentSlice("component_0", ["out.txt"])],
            qp,
            ".",
            deadline=_time.monotonic() - 1,
        )
        assert "wall-clock deadline exhausted" in result.reason
        assert "budget exhausted" not in result.reason

    def test_evidence_read_from_dict_coerces_non_strings(self):
        """NEW-9: persisted EvidenceRead fields must normalize to strings."""
        from orchestrator.review_artifact import EvidenceRead

        er = EvidenceRead.from_dict({"path": 7, "sections_or_lines": None, "origin": "cited"})
        assert er.path == ""
        assert er.sections_or_lines == ""
        assert er.origin == "cited"
        er2 = EvidenceRead.from_dict(None)
        assert er2.path == ""
        assert er2.origin == "provided"

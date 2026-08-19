"""Tests for review_artifact.py — pure schema logic, zero model calls."""

from orchestrator.review_artifact import (
    ISSUE_SEVERITY_P0,
    ISSUE_SEVERITY_P1,
    ISSUE_SEVERITY_P2,
    VERDICT_CONDITIONAL_PASS,
    VERDICT_ERROR,
    VERDICT_INSUFFICIENT_EVIDENCE,
    VERDICT_PASS,
    VERDICT_REJECT,
    ArbitrationVerdict,
    EvidenceRead,
    QualityPlaneVerdict,
    ReviewArtifact,
    ReviewIssue,
    synthesize_artifacts,
)


class TestEvidenceRead:
    def test_defaults(self):
        e = EvidenceRead("path/to/file.py", origin="cited")
        assert e.path == "path/to/file.py"
        assert e.sections_or_lines == ""

    def test_roundtrip_dict(self):
        e = EvidenceRead("src/main.py", "lines 1-100")
        d = e.to_dict()
        restored = EvidenceRead.from_dict(d)
        assert restored.path == "src/main.py"
        assert restored.sections_or_lines == "lines 1-100"


class TestReviewIssue:
    def test_default_severity(self):
        i = ReviewIssue(severity=ISSUE_SEVERITY_P2, claim="missing validation")
        assert i.severity == ISSUE_SEVERITY_P2
        assert i.claim == "missing validation"

    def test_full_constructor(self):
        i = ReviewIssue(
            severity=ISSUE_SEVERITY_P0,
            path="src/contract.py",
            line=26,
            claim="Validation accepts unsupported check kind",
            evidence="VALID_CHECK_KINDS lacks...",
            recommended_action="Add the new kind to VALID_CHECK_KINDS",
        )
        assert i.path == "src/contract.py"
        assert i.line == 26

    def test_roundtrip_dict(self):
        i = ReviewIssue(
            severity=ISSUE_SEVERITY_P1,
            path="tests/test_qc.py",
            line=42,
            claim="Missing coverage for edge case",
            evidence="No test for empty input",
            recommended_action="Add test case",
        )
        d = i.to_dict()
        restored = ReviewIssue.from_dict(d)
        assert restored.severity == ISSUE_SEVERITY_P1
        assert restored.path == "tests/test_qc.py"
        assert restored.line == 42
        assert restored.claim == "Missing coverage for edge case"


class TestReviewArtifact:
    def test_default_is_insufficient_evidence(self):
        a = ReviewArtifact(reviewer_id="test-1", role="maintainer")
        assert a.verdict == VERDICT_INSUFFICIENT_EVIDENCE
        assert a.confidence == 0.0
        assert a.has_evidence() is False

    def test_has_evidence_true_with_read_files(self):
        a = ReviewArtifact(
            reviewer_id="test-1",
            role="maintainer",
            evidence_read=[EvidenceRead("src/file.py", origin="cited")],
            verdict=VERDICT_PASS,
            confidence=0.9,
        )
        assert a.has_evidence() is True

    def test_has_p0_issues(self):
        a = ReviewArtifact(
            reviewer_id="test-1",
            role="maintainer",
            issues=[ReviewIssue(severity=ISSUE_SEVERITY_P0, claim="security hole")],
        )
        assert a.has_p0_issues() is True
        assert a.has_p1_issues() is False

    def test_has_p1_issues(self):
        a = ReviewArtifact(
            reviewer_id="test-1",
            role="maintainer",
            issues=[ReviewIssue(severity=ISSUE_SEVERITY_P1, claim="test gap")],
        )
        assert a.has_p1_issues() is True
        assert a.has_p0_issues() is False

    def test_max_severity_p0_over_p1(self):
        a = ReviewArtifact(
            reviewer_id="test-1",
            role="maintainer",
            issues=[
                ReviewIssue(severity=ISSUE_SEVERITY_P1, claim="minor"),
                ReviewIssue(severity=ISSUE_SEVERITY_P0, claim="critical"),
            ],
        )
        assert a.max_severity() == ISSUE_SEVERITY_P0

    def test_max_severity_no_issues(self):
        a = ReviewArtifact(reviewer_id="test", role="x")
        assert a.max_severity() == ""

    def test_roundtrip_dict_full(self):
        a = ReviewArtifact(
            reviewer_id="arch-1",
            role="systems_architect",
            model="gemini:gemini-3.6-flash",
            component_id="memory_system",
            verdict=VERDICT_CONDITIONAL_PASS,
            confidence=0.75,
            evidence_read=[EvidenceRead("src/memory.rs", "lines 1-50")],
            issues=[ReviewIssue(severity=ISSUE_SEVERITY_P1, path="src/memory.rs", line=30, claim="unsafe cast")],
            strengths=["good structure"],
            dissent=["disagree on risk level"],
            unsupported_claims=["claims X without evidence"],
            owner_level_challenges=["should this exist"],
            elapsed_sec=42.5,
        )
        d = a.to_dict()
        restored = ReviewArtifact.from_dict(d)
        assert restored.reviewer_id == "arch-1"
        assert restored.role == "systems_architect"
        assert restored.model == "gemini:gemini-3.6-flash"
        assert restored.component_id == "memory_system"
        assert restored.verdict == VERDICT_CONDITIONAL_PASS
        assert restored.confidence == 0.75
        assert len(restored.evidence_read) == 1
        assert len(restored.issues) == 1
        assert len(restored.strengths) == 1
        assert len(restored.dissent) == 1
        assert len(restored.unsupported_claims) == 1
        assert len(restored.owner_level_challenges) == 1
        assert restored.elapsed_sec == 42.5


class TestSynthesizeArtifacts:
    def test_no_evidence_returns_insufficient(self):
        a1 = ReviewArtifact(reviewer_id="r1", role="maintainer")
        a2 = ReviewArtifact(reviewer_id="r2", role="architect")
        result = synthesize_artifacts([a1, a2])
        assert result.status == VERDICT_INSUFFICIENT_EVIDENCE
        assert result.score == 0.0

    def test_unanimous_pass(self):
        a1 = ReviewArtifact(
            reviewer_id="r1",
            role="maintainer",
            evidence_read=[EvidenceRead("f.py", origin="cited")],
            verdict=VERDICT_PASS,
            confidence=0.9,
        )
        a2 = ReviewArtifact(
            reviewer_id="r2",
            role="architect",
            evidence_read=[EvidenceRead("f.py", origin="cited")],
            verdict=VERDICT_PASS,
            confidence=0.85,
        )
        result = synthesize_artifacts([a1, a2])
        assert result.status == VERDICT_PASS
        assert result.score == 0.875

    def test_p0_blocker_causes_reject(self):
        a1 = ReviewArtifact(
            reviewer_id="r1",
            role="maintainer",
            evidence_read=[EvidenceRead("f.py", origin="cited")],
            verdict=VERDICT_PASS,
            confidence=0.9,
            issues=[ReviewIssue(severity=ISSUE_SEVERITY_P0, claim="critical bug", evidence="exists at line 42")],
        )
        a2 = ReviewArtifact(
            reviewer_id="r2",
            role="architect",
            evidence_read=[EvidenceRead("f.py", origin="cited")],
            verdict=VERDICT_PASS,
            confidence=0.85,
        )
        result = synthesize_artifacts([a1, a2])
        assert result.status == VERDICT_REJECT
        assert len(result.p0_blockers) == 1
        assert result.p0_blockers[0].claim == "critical bug"

    def test_p1_issues_cause_conditional_pass(self):
        a1 = ReviewArtifact(
            reviewer_id="r1",
            role="maintainer",
            evidence_read=[EvidenceRead("f.py", origin="cited")],
            verdict=VERDICT_PASS,
            confidence=0.8,
            issues=[ReviewIssue(severity=ISSUE_SEVERITY_P1, claim="test gap", evidence="line 30: missing edge case")],
        )
        result = synthesize_artifacts([a1])
        assert result.status == VERDICT_CONDITIONAL_PASS
        assert len(result.p1_required_fixes) == 1

    def test_p0_with_claim_but_no_evidence_still_blocks(self):
        a1 = ReviewArtifact(
            reviewer_id="r1",
            role="maintainer",
            evidence_read=[EvidenceRead("f.py", origin="cited")],
            verdict=VERDICT_REJECT,
            confidence=0.0,
            issues=[
                ReviewIssue(severity=ISSUE_SEVERITY_P0, claim="unsupported claim", evidence=""),
            ],
        )
        result = synthesize_artifacts([a1])
        assert len(result.p0_blockers) == 1  # claim exists, severity is P0
        assert result.status == VERDICT_REJECT

    def test_empty_claim_skipped_from_blockers(self):
        a1 = ReviewArtifact(
            reviewer_id="r1",
            role="maintainer",
            evidence_read=[EvidenceRead("f.py", origin="cited")],
            verdict=VERDICT_REJECT,
            confidence=0.0,
            issues=[
                ReviewIssue(severity=ISSUE_SEVERITY_P0, claim=""),
            ],
        )
        result = synthesize_artifacts([a1])
        assert len(result.p0_blockers) == 0  # empty claim → unparseable, skip
        assert len(result.merged_issues) == 1  # still tracked as raw issue

    def test_dissent_preserved(self):
        a1 = ReviewArtifact(
            reviewer_id="r1",
            role="maintainer",
            evidence_read=[EvidenceRead("f.py", origin="cited")],
            verdict=VERDICT_PASS,
            confidence=0.9,
            dissent=["architecture is overengineered"],
        )
        result = synthesize_artifacts([a1])
        assert result.dissent_preserved is True

    def test_score_averaging(self):
        a1 = ReviewArtifact(
            reviewer_id="r1",
            role="x",
            evidence_read=[EvidenceRead("f.py", origin="cited")],
            verdict=VERDICT_PASS,
            confidence=1.0,
        )
        a2 = ReviewArtifact(
            reviewer_id="r2",
            role="y",
            evidence_read=[EvidenceRead("f.py", origin="cited")],
            verdict=VERDICT_PASS,
            confidence=0.5,
        )
        result = synthesize_artifacts([a1, a2])
        assert result.score == 0.75

    def test_insufficient_evidence_verdict_not_evidence(self):
        """A reviewer explicitly returning INSUFFICIENT_EVIDENCE must not
        count as having reviewed the files — even with files assigned."""
        a1 = ReviewArtifact(
            reviewer_id="r1",
            role="maintainer",
            evidence_read=[EvidenceRead("f.py", origin="cited")],
            verdict=VERDICT_INSUFFICIENT_EVIDENCE,
            confidence=0.0,
        )
        result = synthesize_artifacts([a1])
        assert result.status == VERDICT_INSUFFICIENT_EVIDENCE

    def test_error_verdict_never_passes(self):
        """A failed reviewer call must synthesize to ERROR, never PASS."""
        a1 = ReviewArtifact(
            reviewer_id="r1",
            role="maintainer",
            evidence_read=[EvidenceRead("f.py", origin="cited")],
            verdict="ERROR",
            confidence=0.0,
        )
        result = synthesize_artifacts([a1])
        assert result.status == "ERROR"

    def test_error_verdict_dominates_pass(self):
        a1 = ReviewArtifact(
            reviewer_id="r1",
            role="maintainer",
            evidence_read=[EvidenceRead("f.py", origin="cited")],
            verdict="PASS",
            confidence=0.9,
        )
        a2 = ReviewArtifact(
            reviewer_id="r2",
            role="architect",
            evidence_read=[EvidenceRead("f.py", origin="cited")],
            verdict="ERROR",
            confidence=0.0,
        )
        result = synthesize_artifacts([a1, a2])
        assert result.status == "ERROR"


class TestArbitrationVerdict:
    def test_default_is_reject(self):
        a = ArbitrationVerdict()
        assert a.status == VERDICT_REJECT

    def test_roundtrip_dict(self):
        a = ArbitrationVerdict(
            status=VERDICT_PASS,
            winning_claims=["claim A is valid"],
            discarded_claims=["claim B unsupported"],
            reason="evidence supports A",
            confidence=0.95,
        )
        d = a.to_dict()
        restored = ArbitrationVerdict.from_dict(d)
        assert restored.status == VERDICT_PASS
        assert len(restored.winning_claims) == 1
        assert restored.confidence == 0.95


class TestQualityPlaneVerdict:
    def test_default_insufficient(self):
        v = QualityPlaneVerdict()
        assert v.passed is False
        assert v.status == VERDICT_INSUFFICIENT_EVIDENCE

    def test_roundtrip_dict(self):
        v = QualityPlaneVerdict(
            passed=True,
            status=VERDICT_PASS,
            score=0.91,
            reason="all checks passed",
            component_verdicts=[{"id": "comp1", "status": "PASS"}],
            review_artifacts=[{"id": "art1"}],
            synthesis_result={"status": "PASS"},
            budget_used={"llm_calls": 3, "elapsed_sec": 120.0},
            degraded=True,
            degrade_reason="budget reduced reviewers from 3 to 1",
        )
        d = v.to_dict()
        restored = QualityPlaneVerdict.from_dict(d)
        assert restored.passed is True
        assert restored.status == VERDICT_PASS
        assert restored.score == 0.91
        assert restored.degraded is True
        assert restored.budget_used["llm_calls"] == 3

    def test_map_to_supervisor_states(self):
        assert QualityPlaneVerdict(passed=True, status=VERDICT_PASS).passed is True
        assert QualityPlaneVerdict(passed=False, status=VERDICT_REJECT).passed is False
        assert QualityPlaneVerdict(passed=False, status=VERDICT_INSUFFICIENT_EVIDENCE).passed is False
        assert QualityPlaneVerdict(passed=False, status=VERDICT_ERROR).passed is False

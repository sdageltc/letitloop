"""Structured review artifact schema for the quality plane.

Every reviewer (human or LLM) returns a ReviewArtifact. This schema enforces
evidence-first review: unsupported claims are tracked separately, and
INSUFFICIENT_EVIDENCE is the default for zero evidence_read paths.

Zero model calls. Zero side effects.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

VERDICT_PASS = "PASS"
VERDICT_REJECT = "REJECT"
VERDICT_CONDITIONAL_PASS = "CONDITIONAL_PASS"
VERDICT_INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
VERDICT_ERROR = "ERROR"
VALID_VERDICTS = {
    VERDICT_PASS,
    VERDICT_REJECT,
    VERDICT_CONDITIONAL_PASS,
    VERDICT_INSUFFICIENT_EVIDENCE,
    VERDICT_ERROR,
}

ISSUE_SEVERITY_P0 = "P0"
ISSUE_SEVERITY_P1 = "P1"
ISSUE_SEVERITY_P2 = "P2"
ISSUE_SEVERITY_P3 = "P3"
VALID_SEVERITIES = {ISSUE_SEVERITY_P0, ISSUE_SEVERITY_P1, ISSUE_SEVERITY_P2, ISSUE_SEVERITY_P3}


class EvidenceRead:
    """Tracks what evidence a reviewer actually examined.

    origin distinguishes files *provided* to the reviewer (auto-populated
    from component_files) from evidence the reviewer *cited* in its issues.
    The orchestrator supplies file names, so origin="provided" does not
    claim the model actually read the file (F7 provenance fix).
    """

    def __init__(self, path: str, sections_or_lines: str = "", origin: str = "provided"):
        self.path = path
        self.sections_or_lines = sections_or_lines
        self.origin = origin if origin in ("provided", "cited") else "provided"

    def to_dict(self) -> Dict[str, str]:
        return {
            "path": self.path,
            "sections_or_lines": self.sections_or_lines,
            "origin": self.origin,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, str]) -> EvidenceRead:
        if not isinstance(d, dict):
            d = {}
        path = d.get("path", "")
        sections = d.get("sections_or_lines", "")
        # NEW-9: persisted evidence fields must be strings — a scalar/none
        # value must never survive into path normalization/serialization.
        return cls(
            path=path if isinstance(path, str) else "",
            sections_or_lines=sections if isinstance(sections, str) else "",
            origin=d.get("origin", "provided"),
        )


class ReviewIssue:
    """A single issue found during review."""

    def __init__(
        self,
        severity: str,
        path: str = "",
        line: int = 0,
        claim: str = "",
        evidence: str = "",
        recommended_action: str = "",
    ):
        self.severity = severity
        self.path = path
        self.line = line
        self.claim = claim
        self.evidence = evidence
        self.recommended_action = recommended_action

    def to_dict(self) -> Dict[str, Any]:
        return {
            "severity": self.severity,
            "path": self.path,
            "line": self.line,
            "claim": self.claim,
            "evidence": self.evidence,
            "recommended_action": self.recommended_action,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> ReviewIssue:
        severity = d.get("severity", ISSUE_SEVERITY_P3)
        if severity not in VALID_SEVERITIES:
            severity = ISSUE_SEVERITY_P3
        try:
            line = int(d.get("line", 0))
        except (TypeError, ValueError, OverflowError):
            line = 0
        return cls(
            severity=severity,
            path=d.get("path", "") if isinstance(d.get("path", ""), str) else "",
            line=line,
            claim=d.get("claim", "") if isinstance(d.get("claim", ""), str) else str(d.get("claim", "")),
            evidence=d.get("evidence", "") if isinstance(d.get("evidence", ""), str) else "",
            recommended_action=(
                d.get("recommended_action", "") if isinstance(d.get("recommended_action", ""), str) else ""
            ),
        )


class ReviewArtifact:
    """Structured output from a single reviewer in the quality plane.

    Every reviewer MUST populate evidence_read. If empty, the artifact
    should be treated as INSUFFICIENT_EVIDENCE at the synthesis layer.
    """

    def __init__(
        self,
        reviewer_id: str,
        role: str,
        model: str = "",
        component_id: str = "",
        verdict: str = VERDICT_INSUFFICIENT_EVIDENCE,
        confidence: float = 0.0,
        evidence_read: Optional[List[EvidenceRead]] = None,
        issues: Optional[List[ReviewIssue]] = None,
        strengths: Optional[List[str]] = None,
        dissent: Optional[List[str]] = None,
        unsupported_claims: Optional[List[str]] = None,
        owner_level_challenges: Optional[List[str]] = None,
        timed_out: bool = False,
        elapsed_sec: float = 0.0,
        reason: str = "",
    ):
        self.reviewer_id = reviewer_id
        self.role = role
        self.model = model
        self.component_id = component_id
        self.verdict = verdict
        self.confidence = confidence
        self.evidence_read = evidence_read or []
        self.issues = issues or []
        self.strengths = strengths or []
        self.dissent = dissent or []
        self.unsupported_claims = unsupported_claims or []
        self.owner_level_challenges = owner_level_challenges or []
        self.timed_out = timed_out
        self.elapsed_sec = elapsed_sec
        self.reason = reason

    def to_dict(self) -> Dict[str, Any]:
        return {
            "reviewer_id": self.reviewer_id,
            "role": self.role,
            "model": self.model,
            "component_id": self.component_id,
            "verdict": self.verdict,
            "confidence": self.confidence,
            "evidence_read": [e.to_dict() for e in self.evidence_read],
            "issues": [i.to_dict() for i in self.issues],
            "strengths": list(self.strengths),
            "dissent": list(self.dissent),
            "unsupported_claims": list(self.unsupported_claims),
            "owner_level_challenges": list(self.owner_level_challenges),
            "timed_out": self.timed_out,
            "elapsed_sec": self.elapsed_sec,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> ReviewArtifact:
        verdict = d.get("verdict", VERDICT_INSUFFICIENT_EVIDENCE)
        if verdict not in VALID_VERDICTS:
            verdict = VERDICT_INSUFFICIENT_EVIDENCE
        try:
            confidence = float(d.get("confidence", 0.0))
        except (TypeError, ValueError, OverflowError):
            confidence = 0.0
        if not math.isfinite(confidence):
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))
        evidence_list = d.get("evidence_read", [])
        if not isinstance(evidence_list, list):
            evidence_list = []
        issues_list = d.get("issues", [])
        if not isinstance(issues_list, list):
            issues_list = []

        def _str_list(key):
            values = d.get(key, [])
            if not isinstance(values, list):
                return []
            return [v for v in values if isinstance(v, str)]

        try:
            elapsed = float(d.get("elapsed_sec", 0.0))
        except (TypeError, ValueError, OverflowError):
            elapsed = 0.0
        if not math.isfinite(elapsed) or elapsed < 0.0:
            elapsed = 0.0

        return cls(
            reviewer_id=d.get("reviewer_id", "") if isinstance(d.get("reviewer_id", ""), str) else "",
            role=d.get("role", "") if isinstance(d.get("role", ""), str) else "",
            model=d.get("model", "") if isinstance(d.get("model", ""), str) else "",
            component_id=d.get("component_id", "") if isinstance(d.get("component_id", ""), str) else "",
            verdict=verdict,
            confidence=confidence,
            evidence_read=[EvidenceRead.from_dict(e) for e in evidence_list if isinstance(e, dict)],
            issues=[ReviewIssue.from_dict(i) for i in issues_list if isinstance(i, dict)],
            # F-NEW-2: every list field must normalize to a list of strings —
            # a scalar persisted value must never crash serialization.
            strengths=_str_list("strengths"),
            dissent=_str_list("dissent"),
            unsupported_claims=_str_list("unsupported_claims"),
            owner_level_challenges=_str_list("owner_level_challenges"),
            timed_out=bool(d.get("timed_out", False)),
            elapsed_sec=elapsed,
            reason=d.get("reason", "") if isinstance(d.get("reason", ""), str) else "",
        )

    def has_evidence(self) -> bool:
        """Return True if the reviewer read at least one source file."""
        return len(self.evidence_read) > 0

    def has_cited_evidence(self) -> bool:
        """Return True if the reviewer explicitly cited evidence (F5).

        Provided evidence (auto-populated from component_files) records what
        the orchestrator supplied; only origin="cited" demonstrates the
        reviewer actually engaged with a file.
        """
        return any(e.origin == "cited" for e in self.evidence_read)

    def has_p0_issues(self) -> bool:
        """Return True if any issue is severity P0."""
        return any(i.severity == ISSUE_SEVERITY_P0 for i in self.issues)

    def has_p1_issues(self) -> bool:
        """Return True if any issue is severity P1."""
        return any(i.severity == ISSUE_SEVERITY_P1 for i in self.issues)

    def max_severity(self) -> str:
        """Return the highest severity found, or None if no issues."""
        if self.has_p0_issues():
            return ISSUE_SEVERITY_P0
        if self.has_p1_issues():
            return ISSUE_SEVERITY_P1
        for i in self.issues:
            if i.severity in VALID_SEVERITIES:
                return i.severity
        return ""


class SynthesisVerdict:
    """Aggregated output after merging multiple ReviewArtifacts.

    NOT a summary. Preserves dissent, detects unsupported claims,
    enforces reject-on-P0.
    """

    def __init__(
        self,
        status: str = VERDICT_PASS,
        score: float = 0.0,
        merged_issues: Optional[List[ReviewIssue]] = None,
        unresolved_disagreements: Optional[List[str]] = None,
        p0_blockers: Optional[List[ReviewIssue]] = None,
        p1_required_fixes: Optional[List[ReviewIssue]] = None,
        recommended_next_actions: Optional[List[str]] = None,
        dissent_preserved: bool = True,
    ):
        self.status = status
        self.score = score
        self.merged_issues = merged_issues or []
        self.unresolved_disagreements = unresolved_disagreements or []
        self.p0_blockers = p0_blockers or []
        self.p1_required_fixes = p1_required_fixes or []
        self.recommended_next_actions = recommended_next_actions or []
        self.dissent_preserved = dissent_preserved

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "score": self.score,
            "merged_issues": [i.to_dict() for i in self.merged_issues],
            "unresolved_disagreements": list(self.unresolved_disagreements),
            "p0_blockers": [i.to_dict() for i in self.p0_blockers],
            "p1_required_fixes": [i.to_dict() for i in self.p1_required_fixes],
            "recommended_next_actions": list(self.recommended_next_actions),
            "dissent_preserved": self.dissent_preserved,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> SynthesisVerdict:
        # N2: persisted records must pass through the same fail-closed
        # normalization as live responses.
        if not isinstance(d, dict):
            d = {}
        status = d.get("status", VERDICT_PASS)
        valid = {VERDICT_PASS, VERDICT_REJECT, VERDICT_CONDITIONAL_PASS, VERDICT_INSUFFICIENT_EVIDENCE}
        if status not in valid:
            status = VERDICT_INSUFFICIENT_EVIDENCE
        try:
            score = float(d.get("score", 0.0))
        except (TypeError, ValueError, OverflowError):
            score = 0.0
        if not math.isfinite(score) or score < 0.0 or score > 1.0:
            score = 0.0

        def _issue_list(key):
            values = d.get(key, [])
            if not isinstance(values, list):
                return []
            return [ReviewIssue.from_dict(i) for i in values if isinstance(i, dict)]

        def _str_list(key):
            values = d.get(key, [])
            if not isinstance(values, list):
                return []
            return [v for v in values if isinstance(v, str)]

        return cls(
            status=status,
            score=score,
            merged_issues=_issue_list("merged_issues"),
            unresolved_disagreements=_str_list("unresolved_disagreements"),
            p0_blockers=_issue_list("p0_blockers"),
            p1_required_fixes=_issue_list("p1_required_fixes"),
            recommended_next_actions=_str_list("recommended_next_actions"),
            dissent_preserved=bool(d.get("dissent_preserved", True)),
        )


class ArbitrationVerdict:
    """Terminal resolver for reviewer disagreements.

    Max depth = 1. No arbitration of arbitration.
    """

    def __init__(
        self,
        status: str = VERDICT_REJECT,
        winning_claims: Optional[List[str]] = None,
        discarded_claims: Optional[List[str]] = None,
        reason: str = "",
        confidence: float = 0.0,
    ):
        self.status = status
        self.winning_claims = winning_claims or []
        self.discarded_claims = discarded_claims or []
        self.reason = reason
        self.confidence = confidence

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "winning_claims": list(self.winning_claims),
            "discarded_claims": list(self.discarded_claims),
            "reason": self.reason,
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> ArbitrationVerdict:
        if not isinstance(d, dict):
            d = {}
        status = d.get("status", VERDICT_REJECT)
        if status not in (
            VERDICT_PASS,
            VERDICT_REJECT,
            VERDICT_CONDITIONAL_PASS,
            VERDICT_INSUFFICIENT_EVIDENCE,
        ):
            status = VERDICT_REJECT
        try:
            confidence = float(d.get("confidence", 0.0))
        except (TypeError, ValueError, OverflowError):
            confidence = 0.0
        if not math.isfinite(confidence):
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))
        return cls(
            status=status,
            winning_claims=[c for c in d.get("winning_claims", []) if isinstance(c, str)],
            discarded_claims=[c for c in d.get("discarded_claims", []) if isinstance(c, str)],
            reason=d.get("reason", "") if isinstance(d.get("reason", ""), str) else "",
            confidence=confidence,
        )


class QualityPlaneVerdict:
    """Final aggregate verdict from the quality plane.

    Returned by run_quality_plane() and consumed by supervisor.
    Maps to supervisor states: PASS→COMPLETE, REJECT→QC_REJECTED,
    INSUFFICIENT_EVIDENCE→QC_INSUFFICIENT_EVIDENCE, ERROR→QC_REJECTED,
    CONDITIONAL_PASS→QC_CONDITIONAL_PASS, HUMAN_REQUIRED→ESCALATED.
    """

    def __init__(
        self,
        passed: bool = False,
        status: str = VERDICT_INSUFFICIENT_EVIDENCE,
        score: float = 0.0,
        reason: str = "",
        issues: Optional[List[Dict[str, Any]]] = None,
        component_verdicts: Optional[List[Dict[str, Any]]] = None,
        review_artifacts: Optional[List[Dict[str, Any]]] = None,
        synthesis_result: Optional[Dict[str, Any]] = None,
        arbitration_result: Optional[Dict[str, Any]] = None,
        budget_used: Optional[Dict[str, Any]] = None,
        degraded: bool = False,
        degrade_reason: str = "",
    ):
        self.passed = passed
        self.status = status
        self.score = score
        self.reason = reason
        self.issues = issues or []
        self.component_verdicts = component_verdicts or []
        self.review_artifacts = review_artifacts or []
        self.synthesis_result = synthesis_result
        self.arbitration_result = arbitration_result
        self.budget_used = budget_used or {"llm_calls": 0, "elapsed_sec": 0.0}
        self.degraded = degraded
        self.degrade_reason = degrade_reason

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "passed": self.passed,
            "status": self.status,
            "score": self.score,
            "reason": self.reason,
            "issues": list(self.issues),
            "component_verdicts": list(self.component_verdicts),
            "review_artifacts": list(self.review_artifacts),
            "synthesis_result": self.synthesis_result.to_dict()
            if isinstance(self.synthesis_result, SynthesisVerdict)
            else self.synthesis_result,
            "arbitration_result": self.arbitration_result.to_dict()
            if isinstance(self.arbitration_result, ArbitrationVerdict)
            else self.arbitration_result,
            "budget_used": dict(self.budget_used),
            "degraded": self.degraded,
            "degrade_reason": self.degrade_reason,
        }
        if hasattr(self, "_qc_model") and self._qc_model:
            d["qc_model"] = self._qc_model
        if hasattr(self, "_worker_model") and self._worker_model:
            d["worker_model"] = self._worker_model
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> QualityPlaneVerdict:
        # N2: persisted verdict records must be fail-closed on
        # deserialization — contradictory or unknown fields are normalized
        # instead of re-entering the system as trusted audit data.
        if not isinstance(d, dict):
            d = {}
        status = d.get("status", VERDICT_INSUFFICIENT_EVIDENCE)
        valid = {VERDICT_PASS, VERDICT_REJECT, VERDICT_CONDITIONAL_PASS, VERDICT_INSUFFICIENT_EVIDENCE, VERDICT_ERROR}
        if status not in valid:
            status = VERDICT_INSUFFICIENT_EVIDENCE
        try:
            score = float(d.get("score", 0.0))
        except (TypeError, ValueError, OverflowError):
            score = 0.0
        if not math.isfinite(score) or score < 0.0 or score > 1.0:
            score = 0.0
        passed = bool(d.get("passed", False))
        if passed and status != VERDICT_PASS:
            # Contradictory terminal semantics: fail closed.
            passed = False
        budget = d.get("budget_used", {})
        if not isinstance(budget, dict):
            budget = {}

        def _verdict_list(key):
            values = d.get(key, [])
            if not isinstance(values, list):
                return []
            return [v for v in values if isinstance(v, dict)]

        synthesis_raw = d.get("synthesis_result")
        synthesis_result = SynthesisVerdict.from_dict(synthesis_raw) if isinstance(synthesis_raw, dict) else None
        arb_raw = d.get("arbitration_result")
        arbitration_result = ArbitrationVerdict.from_dict(arb_raw) if isinstance(arb_raw, dict) else None

        return cls(
            passed=passed,
            status=status,
            score=score,
            reason=d.get("reason", "") if isinstance(d.get("reason", ""), str) else "",
            issues=_verdict_list("issues"),
            component_verdicts=_verdict_list("component_verdicts"),
            review_artifacts=_verdict_list("review_artifacts"),
            synthesis_result=synthesis_result,
            arbitration_result=arbitration_result,
            budget_used=budget,
            degraded=bool(d.get("degraded", False)),
            degrade_reason=(d.get("degrade_reason", "") if isinstance(d.get("degrade_reason", ""), str) else ""),
        )

    @classmethod
    def from_qc_verdict(cls, qc_verdict, qc_model: str = "", worker_model: str = "") -> QualityPlaneVerdict:
        """Convert a legacy QCVerdict to a QualityPlaneVerdict for backward compat."""
        return cls(
            passed=qc_verdict.passed,
            status=qc_verdict.status,
            score=qc_verdict.score,
            reason=qc_verdict.reason,
            issues=qc_verdict.issues,
            budget_used={"llm_calls": 1, "elapsed_sec": 0.0},
        )

    def add_model_metadata(self, qc_model: str, worker_model: str) -> None:
        """Attach model metadata after construction (for qc_verdict.json compat)."""
        self._qc_model = qc_model
        self._worker_model = worker_model


def synthesize_artifacts(artifacts: List[ReviewArtifact], quality_spec: Optional[dict] = None) -> SynthesisVerdict:
    """Merge reviewer artifacts using deterministic fail-closed precedence.

    Precedence (checked in order):
    1. any artifact verdict ERROR → ERROR (before the evidence gate: a failed
       reviewer call must never synthesize to PASS or IE)
    2. any artifact verdict REJECT → REJECT (fail-closed: a REJECT verdict is
       honored even when its issue list is empty or unparseable)
    3. no evidence → INSUFFICIENT_EVIDENCE
    4. P0 with a claim → REJECT (severity is the gate, not evidence field)
    5. P1 → CONDITIONAL_PASS
    6. else PASS
    Issues without a claim are recorded but never block. All dissent preserved.
    """
    all_issues: List[ReviewIssue] = []
    p0_blockers: List[ReviewIssue] = []
    p1_required: List[ReviewIssue] = []
    disagreements: List[str] = []
    all_dissent: List[str] = []
    scores: List[float] = []

    for a in artifacts:
        scores.append(a.confidence)
        all_dissent.extend(a.dissent)
        for i in a.issues:
            all_issues.append(i)
            if i.severity == ISSUE_SEVERITY_P0 and i.claim:
                p0_blockers.append(i)
            elif i.severity == ISSUE_SEVERITY_P1:
                p1_required.append(i)

    if any(a.verdict == VERDICT_ERROR for a in artifacts):
        return SynthesisVerdict(
            status=VERDICT_ERROR,
            score=min(scores) if scores else 0.0,
            merged_issues=all_issues,
            unresolved_disagreements=disagreements,
            p0_blockers=p0_blockers,
            p1_required_fixes=p1_required,
            dissent_preserved=True,
        )

    if any(a.verdict == VERDICT_REJECT for a in artifacts):
        return SynthesisVerdict(
            status=VERDICT_REJECT,
            score=min(scores) if scores else 0.0,
            merged_issues=all_issues,
            unresolved_disagreements=disagreements,
            p0_blockers=p0_blockers,
            p1_required_fixes=p1_required,
            dissent_preserved=True,
        )

    has_evidence = any(a.has_cited_evidence() and a.verdict != VERDICT_INSUFFICIENT_EVIDENCE for a in artifacts)
    if not has_evidence:
        return SynthesisVerdict(
            status=VERDICT_INSUFFICIENT_EVIDENCE,
            score=0.0,
            merged_issues=all_issues,
            unresolved_disagreements=disagreements,
            p0_blockers=p0_blockers,
            p1_required_fixes=p1_required,
            dissent_preserved=True,
        )

    if p0_blockers:
        status = VERDICT_REJECT
        score = min(scores) if scores else 0.0
    elif p1_required:
        status = VERDICT_CONDITIONAL_PASS
        score = sum(scores) / len(scores) if scores else 0.5
    else:
        status = VERDICT_PASS
        score = sum(scores) / len(scores) if scores else 1.0

    if quality_spec and status == VERDICT_PASS:
        # N6: quality_spec thresholds are deterministic gates, not just
        # prompt instructions.
        hard = quality_spec.get("hard_failures") or []
        if isinstance(hard, list):
            for hf in hard:
                if (
                    isinstance(hf, str)
                    and hf
                    and any(
                        hf.lower() in (i.claim or "").lower() or hf.lower() in (i.path or "").lower()
                        for i in all_issues
                    )
                ):
                    return SynthesisVerdict(
                        status=VERDICT_REJECT,
                        score=min(scores) if scores else 0.0,
                        merged_issues=all_issues,
                        unresolved_disagreements=disagreements,
                        p0_blockers=p0_blockers,
                        p1_required_fixes=p1_required,
                        dissent_preserved=True,
                    )
        try:
            min_score = float(quality_spec.get("minimum_score", 0.0) or 0.0)
        except (TypeError, ValueError, OverflowError):
            min_score = 0.0
        min_counts = quality_spec.get("minimum_counts") or {}
        if not isinstance(min_counts, dict):
            min_counts = {}
        try:
            required_issues = int(min_counts.get("issues", 0) or 0)
            required_evidence = int(min_counts.get("cited_evidence", 0) or 0)
        except (TypeError, ValueError):
            required_issues = required_evidence = 0
        cited_count = sum(1 for a in artifacts if a.has_cited_evidence())
        unmet = []
        if min_score > 0 and score < min_score:
            unmet.append(f"score {score:.2f} below minimum_score {min_score}")
        if required_issues > 0 and len(all_issues) < required_issues:
            unmet.append(f"issues {len(all_issues)} below minimum_counts.issues {required_issues}")
        if required_evidence > 0 and cited_count < required_evidence:
            unmet.append(f"cited evidence {cited_count} below minimum_counts.cited_evidence {required_evidence}")
        if unmet:
            # PASS is only granted when every threshold is met; otherwise
            # the outcome degrades to CONDITIONAL_PASS with the unmet
            # thresholds recorded as required fixes.
            status = VERDICT_CONDITIONAL_PASS
            p1_required = list(p1_required) + [
                ReviewIssue(
                    severity=ISSUE_SEVERITY_P1,
                    claim="quality_spec unmet: " + "; ".join(unmet),
                )
            ]

    return SynthesisVerdict(
        status=status,
        score=score,
        merged_issues=all_issues,
        unresolved_disagreements=disagreements,
        p0_blockers=p0_blockers,
        p1_required_fixes=p1_required,
        dissent_preserved=True,
    )

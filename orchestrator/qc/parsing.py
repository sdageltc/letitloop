"""Strict response parsing layer for the quality plane.

Extracted verbatim from the former flat orchestrator/quality_plane.py:
path normalization, issue-evidence normalization, fail-closed raw-response
to ReviewArtifact conversion, plan wall-clock deadlines, and arbitration
verdict normalization.

Shared collaborators are resolved through the ``orchestrator.quality_plane``
compat namespace (``qp``) at call time so patches applied there stay live.
"""

from __future__ import annotations

import math
import re
import time
from typing import Any, List, Optional

from orchestrator import quality_plane as qp

from ..quality_plan import QualityPlan
from ..review_artifact import (
    VERDICT_CONDITIONAL_PASS,
    VERDICT_ERROR,
    VERDICT_INSUFFICIENT_EVIDENCE,
    VERDICT_PASS,
    VERDICT_REJECT,
    ArbitrationVerdict,
    EvidenceRead,
    ReviewArtifact,
    ReviewIssue,
)


def _norm_path(path: str) -> str:
    """Normalize separators + drive letter without altering UNC shares (r2)."""
    if not isinstance(path, str):
        return ""
    normalized = path.replace("\\", "/")
    if normalized.startswith("//"):
        return normalized
    if re.match(r"^[A-Za-z]:", normalized):
        return normalized[0].lower() + normalized[1:]
    return normalized


def _normalize_issue_evidence(value: Any) -> str:
    """Normalize structured and legacy reviewer evidence to ReviewIssue text."""
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        return str(value) if value is not None else ""

    path = value.get("path") or value.get("file") or ""
    symbol = value.get("symbol") or value.get("function") or value.get("class") or value.get("function_or_class") or ""
    line_range = value.get("line_range") or value.get("lines") or value.get("line") or ""
    excerpt = value.get("excerpt") or value.get("quote") or value.get("code") or ""
    evidence_type = value.get("type") or value.get("evidence_type") or ""

    fields = []
    if path:
        fields.append(f"path={path}")
    if symbol:
        fields.append(f"symbol={symbol}")
    if line_range:
        fields.append(f"lines={line_range}")
    if evidence_type:
        fields.append(f"type={evidence_type}")
    if excerpt:
        fields.append(f"excerpt={excerpt}")
    return "; ".join(str(field) for field in fields)


def _parse_reviewer_response(
    raw,
    reviewer_id: str,
    role: str,
    model: str,
    component_id: str,
    component_files: Optional[List[str]] = None,
) -> ReviewArtifact:
    """Strictly normalize a raw reviewer response into a ReviewArtifact.

    Fail-closed: malformed input NEVER raises and NEVER produces a PASS.
    Every schema violation yields a typed ERROR artifact.
    """
    try:
        if not isinstance(raw, dict):
            return qp._error_artifact(
                reviewer_id,
                role,
                model,
                component_id,
                "malformed reviewer response",
            )

        status = raw.get("status")
        valid_statuses = {
            VERDICT_PASS,
            VERDICT_REJECT,
            VERDICT_CONDITIONAL_PASS,
            VERDICT_INSUFFICIENT_EVIDENCE,
            VERDICT_ERROR,
        }
        if not isinstance(status, str) or status not in valid_statuses:
            return qp._error_artifact(
                reviewer_id,
                role,
                model,
                component_id,
                "unknown status",
            )

        issues_raw = raw.get("issues", [])
        if not isinstance(issues_raw, list) or any(not isinstance(issue, dict) for issue in issues_raw):
            return qp._error_artifact(
                reviewer_id,
                role,
                model,
                component_id,
                "malformed issues",
            )

        severity_map = {
            "CRITICAL": "P0",
            "MAJOR": "P1",
            "MINOR": "P2",
            "P0": "P0",
            "P1": "P1",
            "P2": "P2",
        }
        issues = []
        for item in issues_raw:
            raw_severity = item.get("severity")
            if raw_severity not in severity_map:
                # R2-strict: an unknown severity token is a malformed
                # response — fail closed rather than silently downgrading
                # a potential blocker to non-blocking P3.
                return qp._error_artifact(
                    reviewer_id,
                    role,
                    model,
                    component_id,
                    f"unknown severity: {raw_severity!r}",
                )
            severity = severity_map[raw_severity]
            line_value = item.get("line", 0)
            try:
                line = int(line_value)
            except (TypeError, ValueError, OverflowError):
                line = 0

            claim = item.get("description") or item.get("claim") or ""
            issues.append(
                ReviewIssue(
                    severity=severity,
                    path=item.get("path", "") if isinstance(item.get("path", ""), str) else "",
                    line=line,
                    claim=claim if isinstance(claim, str) else str(claim),
                    evidence=_normalize_issue_evidence(item.get("evidence", "")),
                    recommended_action=(
                        item.get("recommended_action", "")
                        if isinstance(item.get("recommended_action", ""), str)
                        else ""
                    ),
                )
            )

        try:
            score = float(raw.get("score", 0.0))
        except (TypeError, ValueError, OverflowError):
            score = 0.0
        if not math.isfinite(score) or score < 0.0 or score > 1.0:
            score = 0.0

        evidence_read = [EvidenceRead(path=f) for f in (component_files or [])]
        # F7: an issue that names a component file counts as a cited
        # evidence read; plain file inclusion stays origin="provided".
        cited_paths = {
            _norm_path(p)
            for issue in issues
            for p in ([issue.path] if issue.path else []) + ([issue.evidence] if issue.evidence else [])
            if p
        }
        # F5: an explicit structured "files_reviewed" assertion from the
        # reviewer is also cited evidence (demonstrates engagement).
        files_reviewed = raw.get("files_reviewed", [])
        if isinstance(files_reviewed, list):
            cited_paths.update(_norm_path(p) for p in files_reviewed if isinstance(p, str) and p.strip())
        for er in evidence_read:
            base = _norm_path(er.path)
            # N5/F-NEW-1: "*" is not a file and substring matches are not
            # evidence. A citation counts only when it is an exact path or
            # an exact basename of a real component file.
            if any(cite == base or base.endswith("/" + cite) for cite in cited_paths):
                er.origin = "cited"
        # 0.9 QC evidence gate: PASS requires at least one cited evidence read
        if status == VERDICT_PASS:
            has_cited = any(er.origin == "cited" for er in evidence_read)
            if not has_cited:
                status = VERDICT_INSUFFICIENT_EVIDENCE
                score = 0.0
        if status == VERDICT_PASS and not issues:
            score = max(score, 0.9)

        return ReviewArtifact(
            reviewer_id=reviewer_id,
            role=role,
            model=model,
            component_id=component_id,
            verdict=status,
            confidence=score,
            evidence_read=evidence_read,
            issues=issues,
        )
    except Exception:
        return qp._error_artifact(
            reviewer_id,
            role,
            model,
            component_id,
            "normalization failure",
        )


def _raw_to_artifact(
    raw: dict,
    reviewer_id: str,
    role: str,
    model: str,
    component_id: str,
    component_files: Optional[List[str]] = None,
) -> ReviewArtifact:
    """Convert a raw model response dict into a ReviewArtifact (strict)."""
    return _parse_reviewer_response(
        raw,
        reviewer_id,
        role,
        model,
        component_id,
        component_files,
    )


def _deadline_for(quality_plan: QualityPlan) -> Optional[float]:
    """Compute the wall-clock deadline for a plan's quality review (F3).

    max_wall_clock_sec == 0 means "exhausted immediately" (fail-closed).
    Returns None when the budget is negative (unbounded per validation is
    >= 0, so this only occurs for direct callers bypassing validation).
    """
    budget = quality_plan.budget.max_wall_clock_sec
    if not isinstance(budget, (int, float)):
        return None
    if budget == 0:
        return time.monotonic()
    if budget > 0:
        return time.monotonic() + budget
    return None


def _raw_to_arbitration_verdict(raw: dict) -> ArbitrationVerdict:
    """Convert a raw model response dict into an ArbitrationVerdict (strict)."""
    if not isinstance(raw, dict):
        raw = {}
    status = raw.get("status", VERDICT_REJECT)
    if status not in ("PASS", "REJECT", "CONDITIONAL_PASS", "INSUFFICIENT_EVIDENCE"):
        status = VERDICT_REJECT

    def _claim_list(value):
        if not isinstance(value, list):
            return []
        return [c for c in value if isinstance(c, str)]

    try:
        confidence = float(raw.get("confidence", 0.0))
    except (TypeError, ValueError, OverflowError):
        confidence = 0.0
    if not math.isfinite(confidence) or confidence < 0.0 or confidence > 1.0:
        confidence = 0.0
    return ArbitrationVerdict(
        status=status,
        winning_claims=_claim_list(raw.get("winning_claims")),
        discarded_claims=_claim_list(raw.get("discarded_claims")),
        reason=raw.get("reason", "") if isinstance(raw.get("reason", ""), str) else "",
        confidence=confidence,
    )

"""Synthesis and arbitration layer for the quality plane.

Extracted verbatim from the former flat orchestrator/quality_plane.py:
per-component artifact synthesis into a QualityPlaneVerdict, worst-case
component ranking, arbitration trigger policy, arbiter prompt building,
and arbitration execution.

Shared collaborators are resolved through the ``orchestrator.quality_plane``
compat namespace (``qp``) at call time so patches applied there stay live.
"""

from __future__ import annotations

import time
from typing import List, Optional

from orchestrator import quality_plane as qp

from ..component_slicer import ComponentSlice
from ..provider_scheduler import ProviderScheduler
from ..qc_review import _redact_secrets
from ..quality_plan import QualityPlan
from ..review_artifact import (
    VERDICT_CONDITIONAL_PASS,
    VERDICT_ERROR,
    VERDICT_INSUFFICIENT_EVIDENCE,
    VERDICT_PASS,
    VERDICT_REJECT,
    ArbitrationVerdict,
    QualityPlaneVerdict,
    ReviewArtifact,
    synthesize_artifacts,
)


def _component_verdict_to_qp_verdict(
    artifacts: List[ReviewArtifact],
    components: List[ComponentSlice],
    quality_plan: QualityPlan,
    workspace_root: str = "",
    scheduler: Optional[ProviderScheduler] = None,
    deadline: Optional[float] = None,
    quality_spec: Optional[dict] = None,
) -> QualityPlaneVerdict:
    """Synthesize per-component artifacts into a single QualityPlaneVerdict.

    Phase 6: optionally runs arbitration when enabled in quality_plan.
    Phase 7: attaches scheduler usage to budget_used.
    """
    synthesis = synthesize_artifacts(artifacts, quality_spec=quality_spec)

    arbitration_result = None
    arbitration_skipped = False
    arbitration_skip_reason = "budget exhausted"
    budget_calls = len(artifacts)
    resolved_component_ids = set()

    if qp._should_arbitrate(artifacts, synthesis, quality_plan.arbitration):
        max_calls = quality_plan.budget.max_llm_calls
        if max_calls is None or budget_calls + 1 <= max_calls:
            if deadline is not None and time.monotonic() + 1 >= deadline:
                # F3/N3: arbitration must not run past the plan's wall-clock
                # budget — skip when fewer than 1s remains (an invocation
                # needs at least a full second to be safe).
                arbitration_skipped = True
                arbitration_skip_reason = "wall-clock deadline exhausted"
            else:
                arbitration_result = qp._run_arbitration(
                    artifacts,
                    synthesis,
                    quality_plan.arbitration,
                    workspace_root,
                    deadline=deadline,
                )
            # A failed reviewer call (ERROR artifact) is never curable by
            # arbitration: the synthesis ERROR verdict must stand. Both the
            # PASS and REJECT overrides are inert in that case.
            has_error = any(a.verdict == VERDICT_ERROR for a in artifacts)
            if arbitration_result is None:
                pass  # arbitration skipped (deadline or budget exhausted)
            elif arbitration_result.status == VERDICT_PASS:
                # Claim-scoped override: an arbiter PASS may only clear P0
                # blockers whose claims it explicitly addresses in
                # winning_claims. Uncovered blockers stay active (fail-closed).
                # F-NEW-4: an arbiter PASS must never promote a non-REJECT
                # synthesis state (INSUFFICIENT_EVIDENCE, CONDITIONAL_PASS) to
                # PASS — it only resolves an actual P0-based rejection.
                if not has_error and synthesis.status == VERDICT_REJECT and synthesis.p0_blockers:
                    blocker_claims = [b.claim for b in synthesis.p0_blockers if b.claim]
                    winning_claims = arbitration_result.winning_claims or []
                    # Claim-scoped containment (N4): an arbiter PASS may only
                    # clear P0 blockers whose claims it explicitly addresses in
                    # winning_claims. No confidence-based blanket override.
                    covered = all(
                        any(
                            winning.lower() in claim.lower() or claim.lower() in winning.lower()
                            for winning in winning_claims
                            if isinstance(winning, str)
                        )
                        for claim in blocker_claims
                    )
                    if covered:
                        synthesis.status = VERDICT_PASS
                        synthesis.p0_blockers = []
                        # N4: only components whose artifacts contributed a
                        # covered claim are resolved by the arbiter PASS;
                        # unrelated components keep their own verdicts.
                        winning_lower = [w.lower() for w in winning_claims if isinstance(w, str)]
                        for a in artifacts:
                            if not a.component_id or a.verdict == VERDICT_PASS:
                                continue
                            if any(
                                isinstance(i.claim, str)
                                and i.claim
                                and any(i.claim.lower() in w for w in winning_lower)
                                for i in a.issues
                            ):
                                resolved_component_ids.add(a.component_id)
                    elif synthesis.p0_blockers:
                        synthesis.status = VERDICT_REJECT
            elif arbitration_result.status == VERDICT_REJECT and synthesis.status not in (
                VERDICT_REJECT,
                VERDICT_ERROR,
            ):
                synthesis.status = VERDICT_REJECT
                # N4: the arbiter REJECT resolves the disputed components
                # (any component with a non-PASS artifact); components that
                # passed cleanly keep their own verdicts.
                resolved_component_ids = {
                    a.component_id for a in artifacts if a.component_id and a.verdict != VERDICT_PASS
                }
        else:
            arbitration_skipped = True

    # Group artifacts by component_id for component-level summaries
    comp_artifacts: dict = {}
    for a in artifacts:
        cid = a.component_id
        comp_artifacts.setdefault(cid, []).append(a)

    # Order-independent worst-case verdict ranking (permutation-invariant)
    rank = {
        VERDICT_ERROR: 5,
        VERDICT_REJECT: 4,
        VERDICT_CONDITIONAL_PASS: 3,
        VERDICT_INSUFFICIENT_EVIDENCE: 2,
        VERDICT_PASS: 1,
    }

    component_verdicts = []
    for c in components:
        c_artifacts = comp_artifacts.get(c.id, [])
        if c_artifacts:
            statuses = [a.verdict for a in c_artifacts]
            raw_worst = max(statuses, key=lambda s: rank.get(s, 0))
            scores = [a.confidence for a in c_artifacts if a.confidence > 0]
            avg_score = sum(scores) / len(scores) if scores else 0.0
            all_issues = []
            for a in c_artifacts:
                all_issues.extend(i.to_dict() for i in a.issues)
            entry = {
                "component_id": c.id,
                # N4: arbitration resolution is component-scoped. Only
                # components whose claims the arbiter resolved receive the
                # global synthesis status; unrelated components keep their
                # own worst-case verdict (component-level truth preserved).
                "status": synthesis.status if (arbitration_result and c.id in resolved_component_ids) else raw_worst,
                "raw_status": raw_worst,
                "score": round(avg_score, 4),
                "issues": all_issues,
                "files": c.files,
                "reviewers": [a.role for a in c_artifacts],
            }
            if arbitration_result and c.id in resolved_component_ids:
                # Resolved status reflects arbitration when it ran (F2):
                # a top-level PASS with component-level REJECT is a
                # contradictory audit record. raw_status preserves the
                # pre-arbitration worst-case for audit.
                entry["arbitrated"] = True
            component_verdicts.append(entry)

    passed = synthesis.status == VERDICT_PASS
    if arbitration_result:
        budget_calls += 1
    provider_usage = scheduler.usage_summary() if scheduler else {}
    budget_used = {"llm_calls": budget_calls, "elapsed_sec": 0.0}
    if provider_usage:
        budget_used["provider_usage"] = provider_usage
    reason = _synthesis_reason(synthesis.status, passed, synthesis.p0_blockers, synthesis.p1_required_fixes)
    if arbitration_skipped:
        # NEW-8: report the ACTUAL skip cause — deadline exhaustion is not
        # the same audit signal as budget exhaustion.
        reason += f"; arbitration skipped: {arbitration_skip_reason}"
    return QualityPlaneVerdict(
        passed=passed,
        status=synthesis.status,
        score=synthesis.score,
        reason=reason,
        issues=[i.to_dict() for i in synthesis.merged_issues],
        component_verdicts=component_verdicts,
        review_artifacts=[a.to_dict() for a in artifacts],
        synthesis_result=synthesis.to_dict(),
        arbitration_result=arbitration_result.to_dict() if arbitration_result else None,
        budget_used=budget_used,
    )


def _synthesis_reason(status: str, passed: bool, p0_blockers: list, p1_fixes: list) -> str:
    """Build a one-line reason string from synthesis results."""
    if status == VERDICT_ERROR:
        return "quality review failed"
    if status == VERDICT_INSUFFICIENT_EVIDENCE:
        return "No reviewer examined evidence"
    if p0_blockers:
        return f"{len(p0_blockers)} P0 blocker(s) found"
    if p1_fixes:
        return f"{len(p1_fixes)} P1 required fix(es) found"
    return "All components passed review"


# ── Arbitration ─────────────────────────────────────────────────────────────


def _should_arbitrate(
    artifacts: List[ReviewArtifact],
    synthesis,
    arbitration_policy,
) -> bool:
    """Check if arbitration should be triggered based on synthesis results."""
    if not arbitration_policy.enabled:
        return False
    triggers = set(arbitration_policy.trigger)

    if "p0_disagreement" in triggers and synthesis.p0_blockers:
        # P0 found — check if reviewers disagree on which issues are P0
        return True

    if "low_confidence" in triggers:
        avg_conf = sum(a.confidence for a in artifacts) / max(len(artifacts), 1)
        if avg_conf < 0.5:
            return True
        # Also trigger if any reviewer confidence is below threshold
        if any(a.confidence < 0.3 for a in artifacts if a.has_evidence()):
            return True

    if "any_reject" in triggers:
        if any(a.verdict in (VERDICT_REJECT, "ERROR") for a in artifacts):
            return True

    return False


def _build_arbitration_prompt(
    artifacts: List[ReviewArtifact],
    synthesis,
) -> str:
    """Build a prompt asking the arbiter to resolve reviewer disagreements."""
    lines = [
        "You are an impartial arbitration reviewer. Multiple reviewers have evaluated the same work",
        "and disagree. Review the disagreements below and determine the correct outcome.",
        "",
        "Respond with ONLY a JSON object:",
        '  {"status": "PASS" or "REJECT" or "CONDITIONAL_PASS",',
        '   "reason": "short explanation of your decision",',
        '   "confidence": 0.0-1.0,',
        '   "winning_claims": ["claim1", ...],',
        '   "discarded_claims": ["claim2", ...]}',
        "",
        "=== REVIEWER DISAGREEMENTS ===",
    ]

    for i, a in enumerate(artifacts):
        lines.append(f"\n--- Reviewer {i + 1}: {a.role} (confidence={a.confidence}) ---")
        lines.append(f"  Verdict: {a.verdict}")
        for issue in a.issues:
            lines.append(f"  [{issue.severity}] {issue.claim} (evidence: {issue.evidence or 'none'})")
        if a.dissent:
            lines.append(f"  Dissent: {'; '.join(a.dissent)}")

    if synthesis.p0_blockers:
        lines.append("\n=== P0 BLOCKERS FROM SYNTHESIS ===")
        for b in synthesis.p0_blockers:
            lines.append(f"  [{b.severity}] {b.claim}")

    if synthesis.p1_required_fixes:
        lines.append("\n=== P1 REQUIRED FIXES ===")
        for f in synthesis.p1_required_fixes:
            lines.append(f"  [{f.severity}] {f.claim}")

    lines.append("")
    lines.append("Based on the above, what is the correct final verdict?")
    # N7: reviewer claims/evidence/dissent can surface secrets — redact the
    # complete arbitration prompt before it is transmitted externally.
    return _redact_secrets("\n".join(lines))


def _run_arbitration(
    artifacts: List[ReviewArtifact],
    synthesis,
    arbitration_policy,
    workspace_root: str,
    deadline: Optional[float] = None,
) -> ArbitrationVerdict:
    """Run arbitration over conflicting artifacts, return an ArbitrationVerdict."""
    model = qp._resolve_arbitration_model(arbitration_policy)
    prompt = _build_arbitration_prompt(artifacts, synthesis)

    timeout_sec = None
    if deadline is not None:
        remaining = deadline - time.monotonic()
        if remaining < qp.MIN_REVIEWER_TIMEOUT_SEC:
            return ArbitrationVerdict(
                status=VERDICT_REJECT,
                reason="insufficient remaining wall clock budget for arbitration",
                confidence=0.0,
            )
        timeout_sec = min(120, int(remaining))

    try:
        raw = qp._invoke_reviewer(prompt, model, workspace_root, timeout_sec=timeout_sec)
        return qp._raw_to_arbitration_verdict(raw)
    except Exception as exc:
        # N1: arbitration must never crash the quality plane — any
        # invocation/parse failure becomes a fail-closed REJECT verdict so
        # the supervisor sees a structured QC outcome, not a task exception.
        return ArbitrationVerdict(
            status=VERDICT_REJECT,
            reason=f"arbitration failure: {exc}",
            confidence=0.0,
        )

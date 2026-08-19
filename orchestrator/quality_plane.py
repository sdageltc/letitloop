"""Quality plane — orchestrator-level quality evaluation gateway.

Phased evolution:
  Phase 1: thin wrapper over legacy run_qc_review(), no behavioral change.
  Phase 4: component slicing dispatch with per-component reviewers.
  Phase 5+: multi-agent panels, arbitration, provider scheduling.

Supervisor calls run_quality_plane(). The implementation decides which
review strategy to use based on the contract's quality_plan (if any).
"""

from __future__ import annotations

import concurrent.futures
import json
import math
import os
import re
import time
from typing import Any, Callable, Dict, List, Optional

from .component_slicer import ComponentSlice, slice_components
from .llm import LLMError, call_llm
from .models import ModelRegistry
from .provider_scheduler import CallSpec, ProviderScheduler
from .qc_review import _build_qc_prompt, _redact_secrets, _select_qc_model, run_qc_review
from .quality_plan import (
    MODE_COMPONENT_PANEL,
    MODE_PANEL,
    MODE_SINGLE,
    PERSONA_DESCRIPTIONS,
    QualityPlan,
    ReviewerRole,
    validate_quality_plan,
)
from .review_artifact import (
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

MIN_REVIEWER_TIMEOUT_SEC = 15


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


_REVIEWER_HOOK: Optional[Callable] = None

_PAID_MODEL_MARKERS = (
    "kimi-k2",
    "kimi-k3",
    "claude-opus-5",
    "claude-sonnet-5",
    "claude-fable-5",
    "claude-3-opus",
    "claude-3-7-sonnet",
    "claude-3-5-sonnet",
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
    "gpt-5.6-cyber",
    "o1",
    "o3",
    "o3-mini",
    "o4-mini",
    "deepseek-v4-pro",
    "deepseek-reasoner",
    "gemini-3.1-pro",
    "gemini-2.5-pro",
    "gemini-1.5-pro",
)


def _invoke_reviewer(
    prompt: str,
    model: str,
    workspace_root: str,
    timeout_sec: Optional[int] = None,
) -> dict:
    """Call the generic LLM transport with the given prompt, return parsed JSON dict.

    Returns {"status": "ERROR", "reason": ...} on any failure so callers
    never need to handle exceptions from this function.
    """
    if _REVIEWER_HOOK is not None:
        hook = _REVIEWER_HOOK
        try:
            import inspect

            signature = inspect.signature(hook)
            accepts_timeout = "timeout_sec" in signature.parameters or any(
                parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in signature.parameters.values()
            )
        except (TypeError, ValueError):
            accepts_timeout = False
        if accepts_timeout:
            return hook(prompt, model, workspace_root, timeout_sec=timeout_sec)
        return hook(prompt, model, workspace_root)

    fake = os.environ.get("FAKE_QC", "")
    if fake:
        return _fake_raw_response(fake, prompt)

    timeout = timeout_sec if timeout_sec is not None else 120

    # Paid routes still receive the prepared master-context payload: the
    # complete review context as one payload, no external file reads.
    normalized_model = str(model or "").strip().lower()
    if any(marker in normalized_model for marker in _PAID_MODEL_MARKERS) and "gpt-4o-mini" not in normalized_model:
        prompt = (
            "[MASTER_CONTEXT]\n"
            "[CONTEXT_COMPLETE]\n"
            "The following payload is the complete review context. Do not read "
            "additional files to reconstruct missing context.\n\n" + prompt
        )

    try:
        response = call_llm(prompt, model, timeout_s=timeout)
    except LLMError as e:
        return {"status": "ERROR", "reason": f"QC invocation error: {e}", "score": 0.0, "issues": []}

    stdout = (response.get("text") or "").strip()
    if stdout.startswith("```"):
        stdout = stdout[3:]
        if stdout.startswith("json"):
            stdout = stdout[4:]
        stdout = stdout.rsplit("```", 1)[0].strip()
    try:
        parsed = json.loads(stdout)
        if isinstance(parsed, dict):
            return parsed
    except (json.JSONDecodeError, ValueError):
        pass

    return {"status": "ERROR", "reason": "unparseable output", "score": 0.0, "issues": []}


def _fake_raw_response(fake_mode: str, prompt: str = "") -> dict:
    """Deterministic fake reviewer response — parity with FAKE_QC modes in qc_review.py.

    Enables deterministic testing of the panel/component paths without
    spawning a real model subprocess.
    """
    if fake_mode == "PASS":
        # N5: the fake reviewer claims it examined the actual files named in
        # the prompt (``--- path ---`` markers). A "*" glob is never accepted
        # as cited evidence, so the fake must name real files like a model.
        paths = _extract_prompt_paths(prompt)
        return {"status": "PASS", "score": 0.95, "issues": [], "files_reviewed": paths}
    if fake_mode == "REJECT":
        return {
            "status": "REJECT",
            "score": 0.4,
            # CRITICAL maps to P0 so synthesis yields a true REJECT,
            # matching the legacy FAKE_QC=REJECT semantics.
            "issues": [{"severity": "CRITICAL", "description": "intentional test rejection"}],
        }
    if fake_mode == "INSUFFICIENT_EVIDENCE":
        return {"status": "INSUFFICIENT_EVIDENCE", "score": 0.0, "issues": []}
    if fake_mode == "ERROR":
        return {"status": "ERROR", "reason": "simulated provider error", "score": 0.0, "issues": []}
    return {"status": "ERROR", "reason": f"unknown FAKE_QC mode: {fake_mode}", "score": 0.0, "issues": []}


def _extract_prompt_paths(prompt: str) -> List[str]:
    """Extract file paths embedded as ``--- <path> (N bytes) ---`` markers."""
    paths = []
    for m in re.finditer(
        r"^--- (.+?)(?: ---)? \((?:read error: .*|file not found|\d+ bytes)\)(?: ---)?$", prompt, re.MULTILINE
    ):
        p = m.group(1).strip()
        if p and p not in paths:
            paths.append(p)
    return paths


# ── Persona prompt building ───────────────────────────────────────────────


def _build_persona_prompt(base_prompt: str, role: ReviewerRole) -> str:
    """Append persona-specific instructions to a base review prompt."""
    desc = PERSONA_DESCRIPTIONS.get(role.role, "")
    parts = [base_prompt]
    parts.append("")
    parts.append(f"=== REVIEW PERSPECTIVE: {role.role} ===")
    if desc:
        parts.append(f"Focus on: {desc}")
    if role.focus:
        parts.append(f"Specific concerns: {', '.join(role.focus)}")
    parts.append("")
    parts.extend(
        [
            "=== EVIDENCE REQUIREMENTS ===",
            "Every implementation or verification claim MUST include all of:",
            "1. repository-relative file path;",
            "2. function or class name;",
            "3. exact line range;",
            "4. a quoted code excerpt from that line range;",
            "5. evidence type: implementation, verification, or observation.",
            "Filename-only, keyword-only, and source-file-present claims are invalid "
            "and cannot support an implementation or verification conclusion.",
            "Use this evidence shape when reporting an issue:",
            '{"path":"orchestrator/example.py","symbol":"run_task",'
            '"line_range":"120-136","excerpt":"...exact source text...",'
            '"type":"implementation"}',
            "",
        ]
    )
    # NEW-5: the base prompt was redacted by _build_qc_prompt, but the
    # persona section (desc/focus) is appended AFTER — a secret in a
    # configured persona would leak. Redact the complete joined prompt.
    return _redact_secrets("\n".join(parts))


# ── Component review dispatch ─────────────────────────────────────────────


def _error_artifact(
    reviewer_id: str,
    role: str,
    model: str,
    component_id: str,
    reason: str,
) -> ReviewArtifact:
    """Build a typed ERROR artifact. Never raises."""
    return ReviewArtifact(
        reviewer_id=reviewer_id,
        role=role,
        model=model,
        component_id=component_id,
        verdict=VERDICT_ERROR,
        confidence=0.0,
        evidence_read=[],
        issues=[],
        reason=reason,
    )


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
            return _error_artifact(
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
            return _error_artifact(
                reviewer_id,
                role,
                model,
                component_id,
                "unknown status",
            )

        issues_raw = raw.get("issues", [])
        if not isinstance(issues_raw, list) or any(not isinstance(issue, dict) for issue in issues_raw):
            return _error_artifact(
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
                return _error_artifact(
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
        return _error_artifact(
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


_MAX_COMPONENT_WORKERS = 4


def _run_component_reviews(
    contract,
    components: List[ComponentSlice],
    verification_results: List[Dict[str, Any]],
    workspace_root: str,
    quality_plan: QualityPlan,
    scheduler: Optional[ProviderScheduler] = None,
    deadline: Optional[float] = None,
    max_workers: Optional[int] = None,
) -> List[ReviewArtifact]:
    """Dispatch reviewers per component, return artifacts.

    Phase 4: one reviewer per component (uses reviewers[0] role).
    Phase 5: up to max_reviewers_per_component reviewers, each with a
    different persona from the quality_plan's reviewer list.
    Phase 7: uses ProviderScheduler to respect per-provider concurrency.
    """
    quality_spec = getattr(contract, "quality_spec", {})

    reviewers = list(quality_plan.reviewers) if quality_plan.reviewers else [ReviewerRole("reviewer")]
    max_per_component = max(quality_plan.budget.max_reviewers_per_component, 1)
    active_reviewers = reviewers[:max_per_component]

    # Build call specs
    all_calls: List[CallSpec] = []
    call_map: dict = {}  # call_id -> (index, role, component, prompt)
    call_idx = 0
    for i, component in enumerate(components):
        base_prompt = _build_qc_prompt(
            contract,
            component.files,
            verification_results,
            quality_spec=quality_spec,
        )
        if getattr(component, "description", ""):
            base_prompt = base_prompt + (f"\n=== COMPONENT DESCRIPTION ===\n{component.description}\n")
        for role in active_reviewers:
            persona_prompt = _build_persona_prompt(base_prompt, role)
            call_id = f"comp_{i}_{role.role}_{call_idx}"
            all_calls.append(
                CallSpec(
                    call_id=call_id,
                    prompt=persona_prompt,
                    model=_resolve_reviewer_model(role),
                    reviewer_id=f"component_{i}_{role.role}",
                    role=role.role,
                    component_id=component.id,
                    component_files=component.files,
                )
            )
            call_map[call_id] = (i, role, component, persona_prompt)
            call_idx += 1

    # Schedule and execute waves (Phase 7: per-provider concurrency)
    scheduler = scheduler or ProviderScheduler()
    waves = scheduler.schedule(all_calls)

    artifacts: List[ReviewArtifact] = []
    if deadline is None:
        deadline = _deadline_for(quality_plan)

    max_workers = max_workers or _MAX_COMPONENT_WORKERS

    def _timeout_for_spec() -> Optional[int]:
        if deadline is None:
            return None
        # F-NEW-3: the wall-clock deadline must constrain the subprocess
        # itself — a call started at t=0.9 must not run the full 120s
        # default. Shrink the timeout to the remaining budget
        # (MIN_REVIEWER_TIMEOUT_SEC floor, 120s ceiling).
        return min(120, max(MIN_REVIEWER_TIMEOUT_SEC, int(deadline - time.monotonic())))

    def _invoke_one(spec: CallSpec) -> ReviewArtifact:
        if deadline is not None and (deadline - time.monotonic()) < MIN_REVIEWER_TIMEOUT_SEC:
            return _error_artifact(
                spec.reviewer_id,
                spec.role,
                spec.model,
                spec.component_id,
                "insufficient remaining wall clock budget",
            )
        try:
            raw = _invoke_reviewer(
                spec.prompt,
                spec.model,
                workspace_root,
                timeout_sec=_timeout_for_spec(),
            )
            return _raw_to_artifact(
                raw,
                reviewer_id=spec.reviewer_id,
                role=spec.role,
                model=spec.model,
                component_id=spec.component_id,
                component_files=spec.component_files,
            )
        except Exception:
            return _error_artifact(
                spec.reviewer_id,
                spec.role,
                spec.model,
                spec.component_id,
                "reviewer invocation failure",
            )

    # Phase 7: calls grouped into waves by ProviderScheduler (per-provider
    # concurrency). Within a wave, run independent reviewer invocations
    # concurrently (bounded by _MAX_COMPONENT_WORKERS), then collect results
    # in deterministic spec order so aggregation/arbitration is stable.
    for wave in waves:
        order = list(wave.calls)
        results: Dict[str, ReviewArtifact] = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {spec.call_id: pool.submit(_invoke_one, spec) for spec in wave.calls}
            for call_id, future in futures.items():
                try:
                    results[call_id] = future.result()
                except Exception:
                    spec = next(s for s in wave.calls if s.call_id == call_id)
                    results[call_id] = _error_artifact(
                        spec.reviewer_id,
                        spec.role,
                        spec.model,
                        spec.component_id,
                        "reviewer invocation failure",
                    )
        artifacts.extend(results[spec.call_id] for spec in order)

    return artifacts


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

    if _should_arbitrate(artifacts, synthesis, quality_plan.arbitration):
        max_calls = quality_plan.budget.max_llm_calls
        if max_calls is None or budget_calls + 1 <= max_calls:
            if deadline is not None and time.monotonic() + 1 >= deadline:
                # F3/N3: arbitration must not run past the plan's wall-clock
                # budget — skip when fewer than 1s remains (an invocation
                # needs at least a full second to be safe).
                arbitration_skipped = True
                arbitration_skip_reason = "wall-clock deadline exhausted"
            else:
                arbitration_result = _run_arbitration(
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


# ── Model policy resolution ──────────────────────────────────────────────


def _resolve_reviewer_model(role) -> str:
    """Map a ReviewerRole's model_policy to a concrete model ID."""
    policy = role.model_policy if hasattr(role, "model_policy") else "default"
    mapping = {
        "default": ModelRegistry.default_qc(),
        "premium": ModelRegistry.OPUS,
        "cheap_cross_family": ModelRegistry.FALLBACK,
        "deep": ModelRegistry.KIMI,
    }
    return mapping.get(policy, ModelRegistry.default_qc())


def _resolve_arbitration_model(arb_policy) -> str:
    """Map ArbitrationPolicy.model_policy to a concrete model ID."""
    policy = arb_policy.model_policy if hasattr(arb_policy, "model_policy") else "default"
    mapping = {
        "default": ModelRegistry.default_qc(),
        "premium": ModelRegistry.OPUS,
        "cheap_cross_family": ModelRegistry.FALLBACK,
        "deep": ModelRegistry.KIMI,
    }
    return mapping.get(policy, ModelRegistry.default_qc())


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
    model = _resolve_arbitration_model(arbitration_policy)
    prompt = _build_arbitration_prompt(artifacts, synthesis)

    timeout_sec = None
    if deadline is not None:
        remaining = deadline - time.monotonic()
        if remaining < MIN_REVIEWER_TIMEOUT_SEC:
            return ArbitrationVerdict(
                status=VERDICT_REJECT,
                reason="insufficient remaining wall clock budget for arbitration",
                confidence=0.0,
            )
        timeout_sec = min(120, int(remaining))

    try:
        raw = _invoke_reviewer(prompt, model, workspace_root, timeout_sec=timeout_sec)
        return _raw_to_arbitration_verdict(raw)
    except Exception as exc:
        # N1: arbitration must never crash the quality plane — any
        # invocation/parse failure becomes a fail-closed REJECT verdict so
        # the supervisor sees a structured QC outcome, not a task exception.
        return ArbitrationVerdict(
            status=VERDICT_REJECT,
            reason=f"arbitration failure: {exc}",
            confidence=0.0,
        )


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


# ── Public API ────────────────────────────────────────────────────────────


def _apply_quality_spec_gates(verdict, quality_spec):
    """Enforce deterministic quality_spec gates on a legacy-path verdict.

    NEW-4: synthesize_artifacts enforces hard_failures/minimum_score/
    minimum_counts for panel/component dispatch, but a budget-degraded plan
    routes to legacy run_qc_review which never synthesizes. Apply the same
    fail-closed gates here so degradation cannot bypass them.
    """
    if not isinstance(quality_spec, dict) or not quality_spec:
        return verdict
    issues = verdict.issues if isinstance(verdict.issues, list) else []

    hard = quality_spec.get("hard_failures") or []
    if isinstance(hard, list):
        for hf in hard:
            if (
                isinstance(hf, str)
                and hf
                and any(
                    hf.lower() in str(i.get("description") or i.get("claim") or "").lower()
                    or hf.lower() in str(i.get("path") or "").lower()
                    for i in issues
                    if isinstance(i, dict)
                )
            ):
                verdict.passed = False
                verdict.status = VERDICT_REJECT
                verdict.reason = f"quality_spec hard failure: {hf}"
                return verdict

    try:
        min_score = float(quality_spec.get("minimum_score", 0.0) or 0.0)
    except (TypeError, ValueError, OverflowError):
        min_score = 0.0
    min_counts = quality_spec.get("minimum_counts") or {}
    if not isinstance(min_counts, dict):
        min_counts = {}
    try:
        required_issues = int(min_counts.get("issues", 0) or 0)
    except (TypeError, ValueError):
        required_issues = 0

    unmet = []
    if min_score > 0 and verdict.score < min_score:
        unmet.append(f"score {verdict.score:.2f} below minimum_score {min_score}")
    if required_issues > 0 and len(issues) < required_issues:
        unmet.append(f"issues {len(issues)} below minimum_counts.issues {required_issues}")
    if unmet:
        # Downgrade PASS to CONDITIONAL_PASS — same semantics as synthesis.
        verdict.passed = False
        verdict.status = VERDICT_CONDITIONAL_PASS
        verdict.reason = "; ".join(unmet)
    return verdict


def run_quality_plane(
    contract,
    output_paths: List[str],
    verification_results: List[Dict[str, Any]],
    workspace_root: str,
    quality_plan=None,
) -> QualityPlaneVerdict:
    """Evaluate output quality. Gateway for all quality review paths.

    Phase 1: delegates to legacy run_qc_review(), wraps result in
    QualityPlaneVerdict. Phase 4: when quality_plan.mode is
    COMPONENT_PANEL, uses component slicing dispatch.

    Returns a QualityPlaneVerdict with fields matching what supervisor
    expects: .passed, .status, .score, .issues, .reason, .to_dict().
    """
    qp = quality_plan
    if qp is not None:
        errors = validate_quality_plan(qp)
        if errors:
            return QualityPlaneVerdict(
                passed=False,
                status=VERDICT_ERROR,
                reason="invalid quality plan: " + "; ".join(errors),
                budget_used={"llm_calls": 0, "elapsed_sec": 0.0},
            )
        # Gateway-authoritative budget: degrade here so direct callers get
        # the same protection as the supervisor path (F3).
        max_calls = qp.budget.max_llm_calls
        if max_calls is not None and qp.estimate_calls() > max_calls:
            _pre_degrade_mode = qp.mode
            qp = qp.degraded_copy()
            # An EXPLICIT panel/component-panel plan
            # must not silently degrade at all — that is a mandatory-panel
            # downgrade. Hard block instead.
            if (
                qp.degraded
                and _pre_degrade_mode in (MODE_PANEL, MODE_COMPONENT_PANEL)
                and getattr(contract, "quality_plan", None)
            ):
                return QualityPlaneVerdict(
                    passed=False,
                    status=VERDICT_ERROR,
                    reason=(
                        "mandatory QC panel cannot be degraded "
                        f"({_pre_degrade_mode} -> {qp.mode}; budget {max_calls} insufficient: {qp.degrade_reason})"
                    ),
                    budget_used={"llm_calls": 0, "elapsed_sec": 0.0},
                )

    # F-NEW-6: the effective (possibly degraded) plan state must reach the
    # emitted verdict — a silent degradation to legacy single-reviewer QC
    # must be auditable.
    degraded = bool(getattr(qp, "degraded", False)) if qp is not None else False
    degrade_reason = getattr(qp, "degrade_reason", "") if qp is not None else ""

    if qp is not None and qp.mode == MODE_COMPONENT_PANEL:
        verdict = _run_component_quality_plane(
            contract,
            output_paths,
            verification_results,
            workspace_root,
            qp,
        )
    elif qp is not None and qp.mode == MODE_PANEL:
        verdict = _run_panel_quality_plane(
            contract,
            output_paths,
            verification_results,
            workspace_root,
            qp,
        )
    elif qp is not None and qp.mode != MODE_SINGLE:
        # Any other non-None mode is an unsupported configuration error:
        # never silently fall back to legacy QC (F1).
        return QualityPlaneVerdict(
            passed=False,
            status=VERDICT_ERROR,
            reason=f"unsupported mode: {qp.mode}",
            budget_used={"llm_calls": 0, "elapsed_sec": 0.0},
        )
    else:
        # Legacy path (SINGLE or no plan)
        qc_result = run_qc_review(contract, output_paths, verification_results, workspace_root)
        qc_model = _select_qc_model(contract)
        worker_model = contract.worker.get("model", "unknown") if hasattr(contract, "worker") else "unknown"

        verdict = QualityPlaneVerdict.from_qc_verdict(qc_result)
        verdict.add_model_metadata(qc_model=qc_model, worker_model=worker_model)
        # NEW-4: a budget-degraded plan can route to legacy single QC, which
        # never runs synthesize_artifacts — enforce the deterministic
        # quality_spec gates here so degradation cannot bypass them.
        _apply_quality_spec_gates(verdict, getattr(contract, "quality_spec", None))

    verdict.degraded = degraded
    verdict.degrade_reason = degrade_reason
    return verdict


def _run_panel_quality_plane(
    contract,
    output_paths: List[str],
    verification_results: List[Dict[str, Any]],
    workspace_root: str,
    quality_plan: QualityPlan,
) -> QualityPlaneVerdict:
    """Run the panel review path: the full output set as one component,
    reviewed by up to max_reviewers_per_component persona reviewers.

    Phase 5 semantics: distinct personas over the same files, synthesis,
    and optional arbitration on disagreement.
    """
    scheduler = ProviderScheduler()

    component = ComponentSlice(id="component_0", files=list(output_paths))

    deadline = _deadline_for(quality_plan)
    artifacts = _run_component_reviews(
        contract,
        [component],
        verification_results,
        workspace_root,
        quality_plan,
        scheduler=scheduler,
        deadline=deadline,
    )

    return _component_verdict_to_qp_verdict(
        artifacts,
        [component],
        quality_plan,
        workspace_root,
        scheduler=scheduler,
        deadline=deadline,
        quality_spec=getattr(contract, "quality_spec", {}),
    )


def _validate_explicit_components(
    declared_components,
    output_paths,
    workspace_root,
    max_components,
):
    """Validate explicit components as an exact, workspace-confined output partition.

    Returns (list[ComponentSlice], list[str] errors). On any error the slices
    list is empty (fail-closed) so callers cannot proceed with a partial or
    escaping partition. Confinement uses realpath + commonpath against the
    workspace root and is independent of contract scope allow-lists.
    """
    import os

    errors = []
    if not isinstance(declared_components, list) or not declared_components:
        return [], ["explicit components must be a non-empty list"]

    if not isinstance(max_components, int) or max_components < 1:
        return [], ["max_components must be a positive integer"]

    if len(declared_components) > max_components:
        return [], [f"explicit component count {len(declared_components)} exceeds max_components {max_components}"]

    canonical_workspace_root = os.path.normcase(os.path.realpath(workspace_root))

    def canonicalize_and_confine(path, label):
        try:
            canonical_path = os.path.normcase(os.path.realpath(path))
            if os.path.commonpath([canonical_path, canonical_workspace_root]) != canonical_workspace_root:
                errors.append(f"{label} outside workspace root: {path!r}")
                return None
            return canonical_path
        except ValueError:
            errors.append(f"{label} cannot compare with workspace root (different drives or invalid path): {path!r}")
            return None

    canonical_outputs = {}
    for output_path in output_paths:
        canonical_output = canonicalize_and_confine(
            output_path,
            "output path",
        )
        if canonical_output is None:
            continue
        if canonical_output in canonical_outputs:
            errors.append(f"duplicate declared output after canonicalization: {output_path!r}")
            continue
        canonical_outputs[canonical_output] = output_path

    if errors:
        return [], errors

    seen_component_ids = set()
    seen_files = {}
    slices = []

    for index, component in enumerate(declared_components):
        if not isinstance(component, dict):
            errors.append(f"component {index} must be a dict")
            continue

        component_id = component.get("id")
        files = component.get("files")
        description = component.get("description", "")

        if not isinstance(component_id, str) or not component_id.strip():
            errors.append(f"component {index} has invalid id")
            continue
        if component_id in seen_component_ids:
            errors.append(f"duplicate component id: {component_id!r}")
            continue
        seen_component_ids.add(component_id)

        if not isinstance(files, list) or not files:
            errors.append(f"component {component_id!r} must declare a non-empty files list")
            continue
        if not isinstance(description, str):
            errors.append(f"component {component_id!r} description must be a string")
            continue

        slice_files = []
        component_seen = set()
        for declared_file in files:
            if not isinstance(declared_file, str) or not declared_file:
                errors.append(f"component {component_id!r} contains an invalid file path")
                continue

            resolved_file = (
                declared_file if os.path.isabs(declared_file) else os.path.join(workspace_root, declared_file)
            )
            canonical_file = canonicalize_and_confine(
                resolved_file,
                f"component {component_id!r} file",
            )
            if canonical_file is None:
                continue

            if canonical_file not in canonical_outputs:
                errors.append(f"component {component_id!r} references unknown output: {declared_file!r}")
                continue

            if canonical_file in component_seen:
                errors.append(f"component {component_id!r} repeats file: {declared_file!r}")
                continue
            component_seen.add(canonical_file)

            previous_component = seen_files.get(canonical_file)
            if previous_component is not None:
                errors.append(
                    f"output {canonical_outputs[canonical_file]!r} appears in both "
                    f"{previous_component!r} and {component_id!r}"
                )
                continue

            seen_files[canonical_file] = component_id
            slice_files.append(canonical_outputs[canonical_file])

        if slice_files:
            slices.append(
                ComponentSlice(
                    id=component_id,
                    files=slice_files,
                    description=description,
                )
            )
        else:
            errors.append(f"component {component_id!r} is empty")

    missing_outputs = [
        output_path for canonical_output, output_path in canonical_outputs.items() if canonical_output not in seen_files
    ]
    if missing_outputs:
        errors.append("explicit components omit outputs: " + ", ".join(repr(path) for path in missing_outputs))

    if errors:
        return [], errors
    return slices, []


def _run_component_quality_plane(
    contract,
    output_paths: List[str],
    verification_results: List[Dict[str, Any]],
    workspace_root: str,
    quality_plan: QualityPlan,
) -> QualityPlaneVerdict:
    """Run the component-sliced quality review path."""
    scheduler = ProviderScheduler()

    if quality_plan.components == "explicit":
        declared_components = getattr(contract, "components", None)
        if not declared_components:
            return QualityPlaneVerdict(
                status=VERDICT_ERROR,
                passed=False,
                reason="invalid explicit components: contract declares no components",
                issues=["contract declares no components for components=explicit"],
            )
        components, component_errors = _validate_explicit_components(
            declared_components,
            output_paths,
            workspace_root,
            quality_plan.budget.max_components,
        )
        if component_errors:
            return QualityPlaneVerdict(
                status=VERDICT_ERROR,
                passed=False,
                reason="invalid explicit components: " + "; ".join(component_errors),
                issues=component_errors,
            )
    else:
        components = slice_components(
            output_paths,
            max_components=quality_plan.budget.max_components,
            base_dir=workspace_root,
        )

    deadline = _deadline_for(quality_plan)
    artifacts = _run_component_reviews(
        contract,
        components,
        verification_results,
        workspace_root,
        quality_plan,
        scheduler=scheduler,
        deadline=deadline,
    )

    return _component_verdict_to_qp_verdict(
        artifacts,
        components,
        quality_plan,
        workspace_root,
        scheduler=scheduler,
        deadline=deadline,
        quality_spec=getattr(contract, "quality_spec", {}),
    )

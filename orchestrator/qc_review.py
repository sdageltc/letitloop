"""Automated QC reviewer: calls a model to evaluate work output quality.

Returns structured verdict (PASS / REJECT) with actionable reasons.
Used by supervisor after verification passes and qc.required is true.
"""

import json
import math
import os

from .llm import LLMError, call_llm
from .models import ModelRegistry
from .quality_lenses import get_lens

QC_STATUS_PASS = "PASS"
QC_STATUS_REJECT = "REJECT"
QC_STATUS_INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
QC_STATUS_ERROR = "ERROR"
VALID_QC_STATUSES = {QC_STATUS_PASS, QC_STATUS_REJECT, QC_STATUS_INSUFFICIENT_EVIDENCE, QC_STATUS_ERROR}


class QCVerdict:
    def __init__(
        self,
        passed: bool,
        reason: str,
        details: str = "",
        status: str = "",
        score: float = 0.0,
        issues: list = None,
        dimension_scores: dict = None,
        dimension_reasoning: dict = None,
    ):
        self.passed = passed
        self.reason = reason
        self.details = details
        self.status = status or (QC_STATUS_PASS if passed else QC_STATUS_REJECT)
        self.score = score
        self.issues = issues or []
        self.dimension_scores = dimension_scores or {}
        self.dimension_reasoning = dimension_reasoning or {}

    def to_dict(self):
        return {
            "passed": self.passed,
            "reason": self.reason,
            "details": self.details,
            "status": self.status,
            "score": self.score,
            "issues": self.issues,
            "dimension_scores": dict(self.dimension_scores),
            "dimension_reasoning": dict(self.dimension_reasoning),
        }


def _redact_secrets(text: str) -> str:
    """Mask high-entropy credential patterns before external transmission (F8).

    Delegates to orchestrator.redaction (single source of truth) so the QC
    path and the evidence journal share the same firewall.
    """
    from .redaction import redact

    return redact(text)


def _build_qc_prompt(contract, output_paths, verification_results, quality_spec=None) -> str:
    qc_lens = (
        contract.qc.get("lens", "code_correctness")
        if hasattr(contract, "qc") and isinstance(contract.qc, dict)
        else "code_correctness"
    )
    lens = get_lens(qc_lens)
    dim_scores_json = lens.dim_scores_json()
    dim_reasoning_json = lens.dim_reasoning_json()

    lines = [
        "You are a strict code/output reviewer. Your job: determine if the delivered work meets the contract requirements.",
        "",
        "You MUST respond with ONLY a JSON object with exactly these fields:",
        '  {"status": "PASS" or "REJECT" or "INSUFFICIENT_EVIDENCE",',
        '   "reason": "short reason",',
        '   "details": "specific issues or affirmation",',
        '   "score": 0.0-1.0,',
        '   "issues": [{"severity": "CRITICAL"|"MAJOR"|"MINOR", "description": "..."}],',
        f'   "dimension_scores": {dim_scores_json},',
        f'   "dimension_reasoning": {dim_reasoning_json}}}',
        "",
        "=== CONTRACT OBJECTIVE ===",
        contract.objective,
        "",
        "=== ACCEPTANCE CRITERIA ===",
    ]
    for chk in contract.acceptance_checks:
        lines.append(
            f"  - {chk.get('kind')}: {chk.get('path', chk.get('command', ''))} expected={chk.get('expected', '?')}"
        )
    lines.append("")
    if quality_spec:
        lines.append("=== QUALITY SPECIFICATION ===")
        if quality_spec.get("required_sections"):
            lines.append(f"  Required sections: {quality_spec['required_sections']}")
        if quality_spec.get("quality_dimensions"):
            lines.append(f"  Quality dimensions: {json.dumps(quality_spec['quality_dimensions'])}")
        if quality_spec.get("hard_failures"):
            lines.append(f"  Hard failures: {quality_spec['hard_failures']}")
        if quality_spec.get("minimum_score"):
            lines.append(f"  Minimum score: {quality_spec['minimum_score']}")
        lines.append("")
    lines.append("=== VERIFICATION RESULTS ===")
    for vr in verification_results:
        status = "PASS" if vr.get("passed") else "FAIL"
        lines.append(f"  [{status}] {vr.get('check_id')}: {vr.get('message', '')}")
    lines.append("")
    lines.append("=== OUTPUT FILES ===")
    for p in output_paths:
        full = os.path.abspath(p) if not os.path.isabs(p) else p
        if os.path.isfile(full):
            try:
                with open(full, "r", encoding="utf-8", errors="replace") as f:
                    content = _redact_secrets(f.read())
                lines.append(f"--- {p} ({len(content)} bytes) ---")
                lines.append(content[:3000])
                if len(content) > 3000:
                    lines.append(f"... [truncated, total {len(content)} bytes]")
            except OSError as e:
                lines.append(f"--- {p} --- (read error: {e})")
        else:
            lines.append(f"--- {p} --- (file not found)")
    lines.append(lens.render_evaluation_section(quality_spec))
    # F6: redact the COMPLETE prompt (contract metadata, acceptance checks,
    # verification messages and quality-spec values can all carry secrets),
    # not just embedded file contents.
    return _redact_secrets("\n".join(lines))


def _fake_qc_review(contract, output_paths, verification_results, workspace_root) -> QCVerdict:
    """Deterministic fake QC for testing — controlled by FAKE_QC env var."""
    fake_mode = os.environ.get("FAKE_QC", "")

    if fake_mode == "PASS":
        return QCVerdict(
            passed=True,
            reason="fake QC PASS",
            details="Deterministic fake QC approval",
            status=QC_STATUS_PASS,
            score=0.95,
            issues=[{"severity": "MINOR", "description": "cosmetic"}],
        )
    elif fake_mode == "REJECT":
        return QCVerdict(
            passed=False,
            reason="fake QC REJECT",
            details="Deterministic fake QC rejection for testing",
            status=QC_STATUS_REJECT,
            score=0.4,
            issues=[{"severity": "MAJOR", "description": "intentional test rejection"}],
        )
    elif fake_mode == "INSUFFICIENT_EVIDENCE":
        return QCVerdict(
            passed=False,
            reason="fake QC INSUFFICIENT_EVIDENCE",
            details="Deterministic fake insufficient evidence for testing",
            status=QC_STATUS_INSUFFICIENT_EVIDENCE,
            score=0.0,
            issues=[],
        )
    elif fake_mode == "ERROR":
        return QCVerdict(
            passed=False,
            reason="fake QC ERROR",
            details="Simulated provider error for testing",
            status=QC_STATUS_ERROR,
            score=0.0,
            issues=[{"severity": "CRITICAL", "description": "provider error"}],
        )
    elif fake_mode == "MALFORMED":
        return QCVerdict(
            passed=False,
            reason="QC reviewer returned unparseable output",
            details="not a valid json response {{{broken",
            status=QC_STATUS_ERROR,
            score=0.0,
            issues=[],
        )

    return QCVerdict(False, "unknown FAKE_QC mode", status=QC_STATUS_ERROR)


def _select_qc_model(contract) -> str:
    """Select QC model — defaults to the QC model (env QC_MODEL override)."""
    return ModelRegistry.default_qc()


def run_qc_review(contract, output_paths, verification_results, workspace_root) -> QCVerdict:
    """Run automated QC review via the generic LLM transport."""
    if os.environ.get("FAKE_QC", ""):
        return _fake_qc_review(contract, output_paths, verification_results, workspace_root)

    quality_spec = getattr(contract, "quality_spec", {})
    prompt = _build_qc_prompt(contract, output_paths, verification_results, quality_spec=quality_spec)

    qc_model = _select_qc_model(contract)

    try:
        response = call_llm(prompt, qc_model, timeout_s=120)
        stdout = response["text"] or ""
    except LLMError as e:
        return QCVerdict(False, f"QC invocation error: {e}", "")

    stdout = stdout.strip()
    if stdout.startswith("```"):
        stdout = stdout[3:]
        if stdout.startswith("json"):
            stdout = stdout[4:]
        stdout = stdout.rsplit("```", 1)[0].strip()
    try:
        parsed = json.loads(stdout)
        if isinstance(parsed, dict):
            status = parsed.get("status", QC_STATUS_REJECT)
            if status not in VALID_QC_STATUSES:
                status = QC_STATUS_ERROR
            reason = parsed.get("reason", "no reason given")
            if not isinstance(reason, str):
                reason = str(reason)
            details = parsed.get("details", "")
            if not isinstance(details, str):
                details = str(details)
            try:
                score = float(parsed.get("score", 0.0))
            except (TypeError, ValueError, OverflowError):
                score = 0.0
            if not math.isfinite(score):
                score = 0.0
            score = max(0.0, min(1.0, score))
            issues_raw = parsed.get("issues", [])
            issues = [i for i in issues_raw if isinstance(i, dict)] if isinstance(issues_raw, list) else []
            dimension_scores = parsed.get("dimension_scores", {})
            if not isinstance(dimension_scores, dict):
                dimension_scores = {}
            dimension_reasoning = parsed.get("dimension_reasoning", {})
            if not isinstance(dimension_reasoning, dict):
                dimension_reasoning = {}
            passed = status == QC_STATUS_PASS
            if status == QC_STATUS_INSUFFICIENT_EVIDENCE:
                passed = False
                if not details:
                    details = "QC reviewer could not determine quality from available evidence"
            return QCVerdict(
                passed=passed,
                reason=reason,
                details=details,
                status=status,
                score=score,
                issues=issues,
                dimension_scores=dimension_scores,
                dimension_reasoning=dimension_reasoning,
            )
    except (json.JSONDecodeError, ValueError):
        pass

    return QCVerdict(False, "QC reviewer returned unparseable output", details=stdout[:500], status=QC_STATUS_ERROR)

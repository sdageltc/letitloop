"""Reviewer persona layer for the quality plane.

Extracted verbatim from the former flat orchestrator/quality_plane.py:
reviewer invocation transport (hook / FAKE_QC / real LLM), persona prompt
building, typed ERROR artifacts, and model-policy resolution.

Shared collaborators are resolved through the ``orchestrator.quality_plane``
compat namespace (``qp``) at call time so patches applied there stay live.
"""

from __future__ import annotations

import json
import os
import re
from typing import Callable, List, Optional

from orchestrator import quality_plane as qp

from ..llm import LLMError
from ..models import ModelRegistry
from ..qc_review import _redact_secrets
from ..quality_plan import PERSONA_DESCRIPTIONS, ReviewerRole
from ..review_artifact import VERDICT_ERROR, ReviewArtifact

MIN_REVIEWER_TIMEOUT_SEC = 15


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
    hook = qp._REVIEWER_HOOK
    if hook is not None:
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
        response = qp.call_llm(prompt, model, timeout_s=timeout)
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

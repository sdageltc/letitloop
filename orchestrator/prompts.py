"""Prompt templates for hybrid worker Implementer and Critic roles."""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def build_implementer_prompt(
    title: str,
    objective: str,
    output_paths: List[str],
    acceptance_summary: str,
    prior_failures: Optional[List[Dict[str, Any]]] = None,
    critic_feedback: Optional[str] = None,
    verifier_feedback: Optional[str] = None,
    max_turns: int = 3,
    current_turn: int = 1,
    quality_spec: Optional[Dict[str, Any]] = None,
    supervisor_attempt: int = 1,
    strategy_fingerprint: str = "",
    prior_fingerprint: str = "",
) -> str:
    """Build a strict bounded prompt for the Implementer role.

    The Implementer must return JSON-formatted file artifacts.
    No prose, no explanation, no extra files.
    """
    parts = [
        "SYSTEM: You are the Implementer. Your job is to produce file content only.",
        "",
        "RULES:",
        "- Return ONLY valid JSON matching the schema below.",
        "- No markdown fences around the JSON.",
        "- No explanatory text before or after.",
        "- Write complete file content. Do not use placeholders.",
        "- Do not add extra files beyond the required output paths.",
        "",
    ]

    parts.extend(
        [
            f"TASK: {title}",
            f"OBJECTIVE: {objective}",
            "",
            "REQUIRED OUTPUT PATHS:",
        ]
    )
    for p in output_paths:
        parts.append(f"  - {p}")
    parts.append("")

    parts.extend(
        [
            "ACCEPTANCE CRITERIA:",
            acceptance_summary,
            "",
        ]
    )

    if quality_spec:
        parts.append("QUALITY SPECIFICATION:")
        if quality_spec.get("required_sections"):
            parts.append(f"  Required sections: {quality_spec['required_sections']}")
        if quality_spec.get("quality_dimensions"):
            parts.append(f"  Quality dimensions: {quality_spec['quality_dimensions']}")
        if quality_spec.get("hard_failures"):
            parts.append(f"  Hard failures: {quality_spec['hard_failures']}")
        if quality_spec.get("minimum_score"):
            parts.append(f"  Target minimum score: {quality_spec['minimum_score']}")
        parts.append("")

    # prior_failures describe a PREVIOUS SUPERVISOR attempt (only exist on
    # attempt > 1) — gate them. critic/verifier feedback is repair-TURN
    # feedback within the current attempt and must always be injected.
    if supervisor_attempt > 1 and prior_failures:
        parts.append("PREVIOUS SUPERVISOR ATTEMPT FAILED:")
        for f in prior_failures:
            msg = f.get("message", str(f))
            parts.append(f"  - {msg}")
        parts.append("")

    # Perpetual-loop r2: if the failure signature is identical to the prior
    # attempt, the retry is structurally the same — demand a real change.
    if (
        supervisor_attempt > 1
        and prior_fingerprint
        and strategy_fingerprint
        and prior_fingerprint == strategy_fingerprint
    ):
        parts.append("RETRY WAS IDENTICAL: adjust the approach — same failure signature as the prior attempt")
        parts.append("")

    # Deterministic attempt-indexed directives — cheaper and safer than AST
    # fingerprinting.
    # cheaper and safer than AST fingerprinting.
    if supervisor_attempt == 2:
        parts.append(
            "RETRY GUIDANCE (attempt 2): do not repeat the prior approach. First state the changed strategy, then decompose the failing logic explicitly."
        )
        parts.append("")
    elif supervisor_attempt >= 3:
        parts.append(
            "RETRY GUIDANCE (attempt 3+): apply a minimal defensive patch. Remove speculative rewrites and preserve already-passing behavior."
        )
        parts.append("")

    def _truncate_feedback(fb: str, max_chars: int = 600) -> str:
        if not fb or len(fb) <= max_chars:
            return fb
        head = fb[: max_chars // 2]
        tail = fb[-max_chars // 2 :]
        return f"{head}\n... [truncated] ...\n{tail}"

    if critic_feedback:
        parts.append("CRITIC FEEDBACK FROM LAST TURN:")
        parts.append(f"  {_truncate_feedback(critic_feedback)}")
        parts.append("")

    if verifier_feedback:
        parts.append("VERIFIER FEEDBACK FROM LAST TURN:")
        parts.append(f"  {_truncate_feedback(verifier_feedback)}")
        parts.append("")

    parts.extend(
        [
            f"SUPERVISOR ATTEMPT {supervisor_attempt}; TURN {current_turn} of {max_turns}.",
            "",
            "OUTPUT SCHEMA (Search/Replace Delta Blocks):",
            "To edit existing code, return ONLY Search/Replace delta blocks:",
            "<<<<<<< SEARCH",
            "[exact lines from original file to replace]",
            "=======",
            "[new replacement lines]",
            ">>>>>>> REPLACE",
            "",
            "Alternatively, for brand-new files only, return JSON:",
            "[",
            '  {"path": "relative/path.ext", "content": "complete file content"}',
            "]",
            "",
            "Return ONLY the requested output format. No markdown commentary outside fences.",
            "",
            "[CONTEXT_COMPLETE]",
        ]
    )

    return "\n".join(parts)


def build_critic_prompt(
    title: str,
    objective: str,
    output_paths: List[str],
    acceptance_summary: str,
    artifact_summaries: List[Dict[str, Any]],
    verifier_results: Optional[List[Dict[str, Any]]] = None,
    quality_spec: Optional[Dict[str, Any]] = None,
) -> str:
    """Build an adversarial critic prompt.

    The Critic must return PASS/FAIL JSON.
    Evidence-first reasoning. No praise. No implementation.
    """
    parts = [
        "SYSTEM: You are an adversarial code reviewer (the Critic).",
        "",
        "RULES:",
        "- Evaluate whether the proposed artifacts satisfy the task objective, quality spec, and acceptance criteria.",
        "- Assume all submitted artifacts contain subtle defects until proven otherwise.",
        "- Cite exact file paths and specific content in your findings.",
        "- Return ONLY valid JSON matching the schema below.",
        "- No markdown fences around the JSON.",
        "- No explanatory text.",
        "",
    ]

    parts.extend(
        [
            f"TASK: {title}",
            f"OBJECTIVE: {objective}",
            "",
            "REQUIRED OUTPUT PATHS:",
        ]
    )
    for p in output_paths:
        parts.append(f"  - {p}")
    parts.append("")

    parts.extend(
        [
            "ACCEPTANCE CRITERIA:",
            acceptance_summary,
            "",
        ]
    )

    if quality_spec:
        parts.append("QUALITY SPECIFICATION:")
        if quality_spec.get("required_sections"):
            parts.append(f"  Required sections: {quality_spec['required_sections']}")
        if quality_spec.get("quality_dimensions"):
            parts.append(f"  Quality dimensions: {quality_spec['quality_dimensions']}")
        if quality_spec.get("hard_failures"):
            parts.append(f"  Hard failures: {quality_spec['hard_failures']}")
        if quality_spec.get("minimum_score"):
            parts.append(f"  Minimum score: {quality_spec['minimum_score']}")
        parts.append("")

    parts.append("PROPOSED ARTIFACTS:")
    for a in artifact_summaries:
        path = a.get("path", "?")
        content = a.get("content", "")
        preview = content[:2000] if content else "(empty)"
        parts.append(f"--- {path} ---")
        parts.append(preview)
        parts.append("")
        if len(content) > 2000:
            parts.append(f"[truncated, full length: {len(content)} chars]")
            parts.append("")

    if verifier_results:
        parts.append("DETERMINISTIC VERIFIER RESULTS:")
        for r in verifier_results:
            status = "PASS" if r.get("passed") else "FAIL"
            parts.append(f"  [{status}] {r.get('check_id', '?')}: {r.get('message', '')}")
        parts.append("")

    parts.extend(
        [
            "OUTPUT SCHEMA:",
            "{",
            '  "status": "PASS" or "FAIL" or "INSUFFICIENT_EVIDENCE",',
            '  "summary": "One-line audit summary",',
            '  "score": 0.0-1.0,',
            '  "issues": [',
            "    {",
            '      "severity": "CRITICAL" | "MAJOR" | "MINOR",',
            '      "location": "path:line",',
            '      "description": "...",',
            '      "suggested_remediation": "..."',
            "    }",
            "  ],",
            '  "implementer_guidance": "Short actionable instruction for repair"',
            "}",
            "",
            "Return ONLY valid JSON in the above format. No markdown. No commentary.",
            "Cap issues to top 3 severity items. If PASS, issues should be empty.",
            "If you cannot determine quality from evidence, return INSUFFICIENT_EVIDENCE.",
            "",
            "[CONTEXT_COMPLETE]",
        ]
    )

    return "\n".join(parts)


def summarize_acceptance(checks: List[Dict[str, Any]]) -> str:
    """Compact one-line-per-check summary of acceptance criteria."""
    lines = []
    for c in checks:
        kind = c.get("kind", "?")
        path = c.get("path", c.get("command", ""))
        exp = c.get("expected", "")
        exp_str = f" expected={exp}" if exp else ""
        lines.append(f"  [{kind}] {path}{exp_str}")
    return "\n".join(lines)


def summarize_verifier_results(results: List[Dict[str, Any]]) -> str:
    """Compact summary of deterministic verifier outcomes."""
    lines = []
    for r in results:
        status = "PASS" if r.get("passed") else "FAIL"
        lines.append(f"  [{status}] {r.get('check_id', '?')}: {r.get('message', '')}")
    return "\n".join(lines)

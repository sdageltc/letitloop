"""
orchestrator/feasibility_gate.py
Cognitive Feasibility & Architectural Deliberation Gate.
"""

from __future__ import annotations
import json
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional

from orchestrator.llm import call_llm


@dataclass
class FeasibilityVerdict:
    verdict: str  # "FEASIBLE", "IRREDUCIBLE", "DEFER"
    is_approved: bool
    rationale: str
    suggested_strategy: str
    risk_score: float  # 0.0 (low risk) to 1.0 (critical risk)
    requires_research: bool


class CognitiveFeasibilityGate:
    """Pre-implementation deliberation gate that reasons on architectural necessity, risk, and feasibility before code mutation."""

    @classmethod
    def deliberate(
        cls,
        target_symbol: str,
        source_code: str,
        complexity_score: float,
        model_name: str = "cli:agy",
        thinking_budget: int = 2048,
    ) -> FeasibilityVerdict:
        prompt = f"""You are the Principal Systems Architect for LetItLoop.
Evaluate whether the following Python symbol should undergo self-evolution refactoring.

Target Symbol: `{target_symbol}`
McCabe Complexity Score: {complexity_score:.1f}

Source Code Context:
```python
{source_code}
```

Evaluate:
1. Is this logic genuinely reducible / refactorable, or is it already optimal?
2. What are the regression risks to callers? (Auth, cryptographic, or core lockfiles should be DEFERRED).
3. Does this require external research/patterns (e.g. arXiv algorithms, GitHub reference implementations)?

Return ONLY valid JSON matching this schema:
```json
{{
    "verdict": "FEASIBLE" | "IRREDUCIBLE" | "DEFER",
    "rationale": "...",
    "suggested_strategy": "...",
    "risk_score": 0.0-1.0,
    "requires_research": true | false
}}
```
"""
        try:
            raw_resp = call_llm(
                model=model_name,
                prompt=prompt,
                thinking_budget=thinking_budget,
            )
            raw_text = raw_resp["text"] if isinstance(raw_resp, dict) else str(raw_resp)
            return cls._parse_verdict(raw_text)
        except Exception as e:
            return FeasibilityVerdict(
                verdict="DEFER",
                is_approved=False,
                rationale=f"Feasibility deliberation error: {e}",
                suggested_strategy="Defer due to deliberation failure",
                risk_score=1.0,
                requires_research=False,
            )

    @classmethod
    def _parse_verdict(cls, text: str) -> FeasibilityVerdict:
        # Strip markdown fences if present
        cleaned = text.strip()
        json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.DOTALL)
        if json_match:
            cleaned = json_match.group(1)
        else:
            # Try to match raw JSON object
            raw_match = re.search(r"(\{.*\})", cleaned, re.DOTALL)
            if raw_match:
                cleaned = raw_match.group(1)

        try:
            data = json.loads(cleaned)
            verdict = str(data.get("verdict", "DEFER")).upper()
            is_approved = verdict == "FEASIBLE"
            return FeasibilityVerdict(
                verdict=verdict,
                is_approved=is_approved,
                rationale=str(data.get("rationale", "No rationale provided.")),
                suggested_strategy=str(data.get("suggested_strategy", "None")),
                risk_score=float(data.get("risk_score", 0.5)),
                requires_research=bool(data.get("requires_research", False)),
            )
        except Exception:
            return FeasibilityVerdict(
                verdict="DEFER",
                is_approved=False,
                rationale="Malformed LLM feasibility output; defaulted to fail-closed DEFER.",
                suggested_strategy="Defer",
                risk_score=1.0,
                requires_research=False,
            )

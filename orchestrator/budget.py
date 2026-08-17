"""Budget enforcement and loop detection for hybrid worker inner loop.

Provides:
- UsageLedger for tracking token/cost per LLM call
- BudgetGuard for pre-flight checks against hard caps
- LoopDetector for stuck/oscillation detection
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


DEFAULT_ESTIMATED_INPUT_USD_PER_M = 0.15
DEFAULT_ESTIMATED_OUTPUT_USD_PER_M = 0.60


@dataclass
class UsageRecord:
    role: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    estimated_cost_usd: float

    def to_dict(self) -> Dict:
        return {
            "role": self.role,
            "model": self.model,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "estimated_cost_usd": self.estimated_cost_usd,
        }


@dataclass
class UsageLedger:
    records: List[UsageRecord] = field(default_factory=list)

    @property
    def total_prompt_tokens(self) -> int:
        return sum(r.prompt_tokens for r in self.records)

    @property
    def total_completion_tokens(self) -> int:
        return sum(r.completion_tokens for r in self.records)

    @property
    def total_tokens(self) -> int:
        return self.total_prompt_tokens + self.total_completion_tokens

    @property
    def total_cost_usd(self) -> float:
        return sum(r.estimated_cost_usd for r in self.records)

    @property
    def call_count(self) -> int:
        return len(self.records)

    def record(self, role: str, model: str, prompt_tokens: int, completion_tokens: int) -> None:
        estimated_cost = (
            (prompt_tokens / 1_000_000) * DEFAULT_ESTIMATED_INPUT_USD_PER_M
            + (completion_tokens / 1_000_000) * DEFAULT_ESTIMATED_OUTPUT_USD_PER_M
        )
        self.records.append(UsageRecord(
            role=role, model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            estimated_cost_usd=estimated_cost,
        ))

    def estimate_cost(
        self,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> float:
        return (
            (prompt_tokens / 1_000_000) * DEFAULT_ESTIMATED_INPUT_USD_PER_M
            + (completion_tokens / 1_000_000) * DEFAULT_ESTIMATED_OUTPUT_USD_PER_M
        )

    def to_dict(self) -> Dict:
        return {
            "records": [r.to_dict() for r in self.records],
            "total_prompt_tokens": self.total_prompt_tokens,
            "total_completion_tokens": self.total_completion_tokens,
            "total_tokens": self.total_tokens,
            "total_cost_usd": self.total_cost_usd,
            "call_count": self.call_count,
        }


class BudgetExhaustedError(Exception):
    pass


@dataclass
class BudgetGuard:
    max_tokens: int = 100_000
    max_cost_usd: float = 0.50
    ledger: UsageLedger = field(default_factory=UsageLedger)

    def check_before_call(
        self,
        estimated_prompt_tokens: int = 10_000,
        estimated_completion_tokens: int = 2_000,
    ) -> None:
        if self.ledger.total_cost_usd >= self.max_cost_usd:
            raise BudgetExhaustedError(
                f"cost ceiling ${self.ledger.total_cost_usd:.4f} >= ${self.max_cost_usd:.2f}"
            )
        if self.ledger.total_tokens + estimated_prompt_tokens + estimated_completion_tokens > self.max_tokens:
            raise BudgetExhaustedError(
                f"token budget would exceed: {self.ledger.total_tokens} + "
                f"{estimated_prompt_tokens + estimated_completion_tokens} > {self.max_tokens}"
            )
        new_cost = self.ledger.total_cost_usd + self.ledger.estimate_cost(
            estimated_prompt_tokens, estimated_completion_tokens,
        )
        if new_cost > self.max_cost_usd:
            raise BudgetExhaustedError(
                f"projected cost ${new_cost:.4f} would exceed ceiling ${self.max_cost_usd:.2f}"
            )

    def remaining_pct(self) -> float:
        cost_ratio = self.ledger.total_cost_usd / self.max_cost_usd if self.max_cost_usd > 0 else 1.0
        token_ratio = self.ledger.total_tokens / self.max_tokens if self.max_tokens > 0 else 1.0
        consumed = max(cost_ratio, token_ratio)
        return max(0.0, 1.0 - consumed)

    def to_dict(self) -> Dict:
        return {
            "max_tokens": self.max_tokens,
            "max_cost_usd": self.max_cost_usd,
            "ledger": self.ledger.to_dict(),
            "remaining_pct": self.remaining_pct(),
        }


class LoopDetector:
    """Detect stuck/oscillation patterns in the inner loop.

    Detects:
    1. Repeated identical output hashes (same artifact content)
    2. Repeated identical failure reasons
    3. Repeated identical critic verdicts
    """

    def __init__(
        self,
        max_identical_outputs: int = 2,
        max_identical_failures: int = 2,
        max_identical_verdicts: int = 2,
    ):
        self.max_identical_outputs = max_identical_outputs
        self.max_identical_failures = max_identical_failures
        self.max_identical_verdicts = max_identical_verdicts
        self.output_hashes: List[str] = []
        self.failure_reasons: List[str] = []
        self.critic_verdicts: List[str] = []

    def _hash_content(self, content: str) -> str:
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    def record_outputs(self, contents: List[str]) -> Optional[str]:
        combined = "|".join(sorted(contents))
        h = self._hash_content(combined)
        self.output_hashes.append(h)
        if len(self.output_hashes) >= self.max_identical_outputs:
            recent = self.output_hashes[-self.max_identical_outputs:]
            if len(set(recent)) == 1:
                return "identical output repeated"
        return None

    def record_failure(self, reason: str) -> Optional[str]:
        self.failure_reasons.append(reason)
        if len(self.failure_reasons) >= self.max_identical_failures:
            recent = self.failure_reasons[-self.max_identical_failures:]
            if len(set(recent)) == 1:
                return "identical failure repeated"
        return None

    def record_critic_verdict(self, verdict: str) -> Optional[str]:
        self.critic_verdicts.append(verdict)
        if len(self.critic_verdicts) >= self.max_identical_verdicts:
            recent = self.critic_verdicts[-self.max_identical_verdicts:]
            if len(set(recent)) == 1:
                return "identical critic verdict repeated"
        return None

    def to_dict(self) -> Dict:
        return {
            "output_hashes": list(self.output_hashes),
            "failure_reasons": list(self.failure_reasons),
            "critic_verdicts": list(self.critic_verdicts),
        }

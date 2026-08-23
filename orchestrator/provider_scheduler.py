"""Provider-aware scheduling for the quality plane.

Prevents concurrent calls to the same model provider (rate-limit
avoidance). Groups calls by provider into waves; the current runtime
executes them serially (H2) — this module only structures calls.

Zero LLM calls. Deterministic ordering.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from .models import _bare_model_id, classify_model, escalation_ladder


@dataclass
class CallSpec:
    """A single model invocation request."""

    call_id: str
    prompt: str
    model: str
    reviewer_id: str = ""
    role: str = ""
    component_id: str = ""
    component_files: Optional[List[str]] = None

    def to_dict(self) -> dict:
        return {
            "call_id": self.call_id,
            "model": self.model,
            "reviewer_id": self.reviewer_id,
            "role": self.role,
            "component_id": self.component_id,
        }


@dataclass
class ScheduleWave:
    """A set of calls grouped for concurrency-safe execution (see H2 note)."""

    calls: List[CallSpec] = field(default_factory=list)


@dataclass
class UsageRecord:
    """Per-provider usage snapshot."""

    provider: str
    model: str
    calls: int = 0


def _extract_provider(model: str) -> str:
    """Extract provider name from a model ID (matches ``orchestrator.llm`` prefixes)."""
    lower = model.lower()
    if lower.startswith("hybrid:"):
        return _extract_provider(model[7:])
    if lower.startswith("any:"):
        return "any"
    for prefix in ("openai:", "anthropic:", "gemini:", "deepseek:"):
        if lower.startswith(prefix):
            return prefix[:-1]
    if ":" in lower:
        return lower.split(":", 1)[0]
    return "any"


class ProviderScheduler:
    """Schedules model calls respecting per-provider concurrency limits.

    Groups calls by provider, then builds waves where calls to different
    providers can run in parallel.
    """

    def __init__(self, max_concurrent: Optional[Dict[str, int]] = None):
        self.max_concurrent = max_concurrent or {
            "openai": 1,
            "anthropic": 1,
            "gemini": 1,
            "deepseek": 1,
            "any": 3,
        }
        self.usage: Dict[str, UsageRecord] = {}

    def schedule(self, calls: List[CallSpec]) -> List[ScheduleWave]:
        """Build execution waves from a list of call specs.

        Within a wave, every call targets a different provider and MAY run
        concurrently under the current executor; waves execute sequentially.
        A provider's max_concurrent limit is enforced by the executor layer
        (one call per provider per wave — the executor never receives two
        calls to the same provider in one wave), which makes the contract
        valid whether or not the executor parallelizes.

        NOTE (H2): the current runtime (_run_component_reviews) executes
        calls strictly serially. This scheduler only *structures* calls
        into concurrency-safe groups — it does not execute them. Callers
        must not assume parallel execution until an executor is added.

        Returns a list of ScheduleWave objects.
        """
        calls = list(calls)
        if not calls:
            return []

        # Group by provider
        provider_groups: dict = {}
        for c in calls:
            prov = _extract_provider(c.model)
            provider_groups.setdefault(prov, []).append(c)
            if prov not in self.usage:
                self.usage[prov] = UsageRecord(provider=prov, model=c.model, calls=0)

        # Build waves: round-robin across providers, one call per provider
        # per wave (F4 — wave semantics now match the documented contract).
        waves: List[ScheduleWave] = []
        remaining = {p: list(cs) for p, cs in provider_groups.items()}
        active_providers = {p for p, cs in remaining.items() if cs}

        while active_providers:
            wave = ScheduleWave()
            for prov in list(active_providers):
                if not remaining[prov]:
                    active_providers.discard(prov)
                    continue
                c = remaining[prov].pop(0)
                wave.calls.append(c)
                self.usage[prov].calls += 1
            if wave.calls:
                waves.append(wave)

        return waves

    def usage_summary(self) -> Dict[str, Any]:
        """Return per-provider and total usage as a dict."""
        total = sum(r.calls for r in self.usage.values())
        return {
            "per_provider": {k: v.calls for k, v in self.usage.items()},
            "total": total,
        }


# ---------------------------------------------------------------------------
# Risk-aware dynamic routing (issue #19): cost/risk-tiered model selection.

# Projected (prompt_tokens, completion_tokens) per tier for budget pre-flight.
_PROJECTED_TOKENS_BY_TIER: Dict[int, tuple] = {
    1: (4_000, 1_000),
    2: (8_000, 2_000),
    3: (16_000, 4_000),
}

_BUDGET_LOW_RATIO = 0.25


@dataclass
class RoutingDecision:
    """Outcome of a risk-aware routing decision.

    escalate_context is True when capability failure evidence should be
    attached to the retry brief (tier escalations), not for pure transport
    rotation (rate limits / timeouts).
    """

    model: str
    tier: int
    reason: str
    escalate_context: bool = False
    skip_providers: Set[str] = field(default_factory=set)


def _error_status(error: Any) -> Optional[int]:
    status = getattr(error, "status", None)
    return status if isinstance(status, int) else None


def _is_rate_limit(error: Any) -> bool:
    if isinstance(error, TimeoutError):
        return True
    return _error_status(error) == 429


def _failure_class(error: Any) -> str:
    if error is None:
        return "unknown"
    if isinstance(error, TimeoutError):
        return "timeout"
    if _error_status(error) == 429:
        return "rate-limit"
    return type(error).__name__.lower()


def _budget_pressure(budget_state: Any) -> str:
    """Classify a duck-typed budget snapshot: '' | 'low' | 'exhausted'."""
    if budget_state is None:
        return ""
    exhausted = getattr(budget_state, "exhausted", None)
    if exhausted is None and hasattr(budget_state, "get"):
        exhausted = budget_state.get("exhausted")
    if exhausted:
        return "exhausted"
    ratio: Optional[float] = None
    if hasattr(budget_state, "remaining_pct"):
        try:
            ratio = float(budget_state.remaining_pct())
        except (TypeError, ValueError):
            ratio = None
    elif hasattr(budget_state, "get"):
        raw = budget_state.get("remaining_pct")
        if isinstance(raw, (int, float)):
            ratio = float(raw)
    if ratio is None:
        return ""
    if ratio <= 0.0:
        return "exhausted"
    if ratio < _BUDGET_LOW_RATIO:
        return "low"
    return ""


def _first_affordable(ladder: List[str], tier_of: Dict[str, int], guard: Any) -> Optional[str]:
    """First ladder candidate whose projected call passes the guard, else None."""
    seen_tiers: Set[int] = set()
    max_tier = max(_PROJECTED_TOKENS_BY_TIER)
    for candidate in ladder:
        tier = tier_of[candidate]
        if tier in seen_tiers:
            continue
        seen_tiers.add(tier)
        prompt_tokens, completion_tokens = _PROJECTED_TOKENS_BY_TIER.get(tier, _PROJECTED_TOKENS_BY_TIER[max_tier])
        try:
            guard.check_before_call(prompt_tokens, completion_tokens)
        except Exception:
            continue
        return candidate
    return None


def _decide(
    start_model: Optional[str] = None,
    attempt: int = 1,
    last_error: Any = None,
    budget_state: Any = None,
    ladder: Optional[List[str]] = None,
    budget_guard: Any = None,
) -> RoutingDecision:
    """Pure routing core (no side effects): map inputs to a RoutingDecision."""
    ladder = list(ladder) if ladder else escalation_ladder(start_model)
    if not ladder:
        ladder = escalation_ladder()
    # Merge the full default ladder so budget floors can reach lower tiers
    # even when a custom/start-scoped ladder omits them.
    seen_names = {_bare_model_id(c) for c in ladder}
    for candidate in escalation_ladder():
        if _bare_model_id(candidate) not in seen_names:
            seen_names.add(_bare_model_id(candidate))
            ladder.append(candidate)
    tier_of = {candidate: classify_model(candidate) for candidate in ladder}
    current = (start_model or "").strip() or ladder[0]
    current_tier = tier_of.get(current, classify_model(current))

    def decision(model: str, tier: int, reason: str, escalate: bool = False, skip: Set[str] = frozenset()) -> RoutingDecision:
        return RoutingDecision(
            model=model,
            tier=int(tier),
            reason=reason,
            escalate_context=bool(escalate),
            skip_providers=set(skip),
        )

    pressure = _budget_pressure(budget_state)

    if pressure:
        floor_tier = min(tier_of.values()) if tier_of else 1
        floor_candidates = [c for c in ladder if tier_of.get(c) == floor_tier]
        chosen = floor_candidates[0] if floor_candidates else ladder[0]
        return decision(
            chosen,
            floor_tier,
            f"budget: budget_state {pressure}; preferring lowest tier",
            escalate=attempt > 1,
        )

    if attempt <= 1:
        return decision(current, current_tier, "first-attempt")

    skip_providers: Set[str] = set()
    if _is_rate_limit(last_error):
        same_tier = [c for c in ladder if tier_of[c] == current_tier]
        idx = same_tier.index(current) if current in same_tier else -1
        nxt = same_tier[idx + 1] if 0 <= idx < len(same_tier) - 1 else None
        skip_providers = {_extract_provider(current)} - {"any"}
        if nxt is not None:
            return decision(
                nxt,
                current_tier,
                f"rate-limit: rotating within tier {current_tier} "
                f"(failure class: {_failure_class(last_error)})",
                skip=skip_providers,
            )
        # Same tier exhausted -> escalate to the next tier below.

    steps = max(1, int(attempt) - 1)
    max_tier = max(tier_of.values())
    target_tier = min(current_tier + steps, max_tier)
    target = next((c for c in ladder if tier_of[c] == target_tier), current)
    fail_class = _failure_class(last_error)
    if _is_rate_limit(last_error):
        reason = (
            f"rate-limit: no same-tier candidate left; escalating past tier "
            f"{current_tier} (failure class: {fail_class})"
        )
    else:
        reason = f"escalation: attempt {attempt} after {fail_class} failure; tier {current_tier} -> {target_tier}"

    if budget_guard is not None:
        affordable = _first_affordable(ladder, tier_of, budget_guard)
        if affordable is None:
            floor_tier = min(tier_of.values())
            fallback = [c for c in ladder if tier_of[c] == floor_tier][0]
            return decision(
                fallback,
                floor_tier,
                f"budget: guard rejects projected cost for tier {target_tier}; downgraded to lowest tier",
                escalate=True,
            )
        if tier_of[affordable] != target_tier:
            return decision(
                affordable,
                tier_of[affordable],
                f"budget: guard caps tier {target_tier}; downgraded to tier {tier_of[affordable]}",
                escalate=True,
            )

    return decision(target, target_tier, reason, escalate=True)


class RiskAwareRouter:
    """Maps (start model, attempt, last error, budget) to a RoutingDecision.

    Pure policy over the escalation ladder; performs no LLM calls and mutates
    nothing. Default runtime behavior is unchanged — callers must opt in via
    ``next_retry_model`` / LETITLOOP_TIERED_ROUTING.
    """

    def __init__(self, ladder: Optional[List[str]] = None, budget_guard: Any = None):
        self._ladder = list(ladder) if ladder else None
        self.budget_guard = budget_guard

    def ladder_for(self, start_model: Optional[str]) -> List[str]:
        if self._ladder is not None:
            return list(self._ladder)
        return escalation_ladder(start_model)

    def route(
        self,
        start_model: Optional[str],
        attempt: int,
        last_error: Any = None,
        budget_state: Any = None,
    ) -> RoutingDecision:
        return _decide(
            start_model=start_model,
            attempt=attempt,
            last_error=last_error,
            budget_state=budget_state,
            ladder=self.ladder_for(start_model),
            budget_guard=self.budget_guard,
        )


def next_retry_model(
    contract_worker: dict,
    attempt: int,
    last_error: Any = None,
    budget_guard: Any = None,
) -> Optional[RoutingDecision]:
    """Integration hook for the supervisor wave (issue #19).

    Returns a RoutingDecision only when tiered routing is opted in via
    ``contract_worker["escalate_on_retry"]`` or LETITLOOP_TIERED_ROUTING=1;
    otherwise returns None so default runtime behavior stays unchanged.
    """
    worker = contract_worker or {}
    enabled = bool(worker.get("escalate_on_retry")) or os.environ.get("LETITLOOP_TIERED_ROUTING") == "1"
    if not enabled:
        return None
    router = RiskAwareRouter(budget_guard=budget_guard)
    return router.route(worker.get("model"), attempt, last_error=last_error)

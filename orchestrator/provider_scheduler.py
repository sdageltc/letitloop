"""Provider-aware scheduling for the quality plane.

Prevents concurrent calls to the same model provider (rate-limit
avoidance). Groups calls by provider into waves; the current runtime
executes them serially (H2) — this module only structures calls.

Zero LLM calls. Deterministic ordering.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


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

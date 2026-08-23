"""Tests for provider_scheduler.py — deterministic scheduling logic."""

import json

import pytest

from orchestrator.budget import BudgetGuard
from orchestrator.models import (
    MODEL_TIERS,
    classify_model,
    escalation_ladder,
    model_tiers,
    tier_for_risk,
)
from orchestrator.provider_scheduler import (
    CallSpec,
    ProviderScheduler,
    RiskAwareRouter,
    RoutingDecision,
    _decide,
    _extract_provider,
    next_retry_model,
)

pytestmark = pytest.mark.fast


class _RateLimitError(Exception):
    def __init__(self, message="rate limited"):
        super().__init__(message)
        self.status = 429


class _BudgetState:
    def __init__(self, exhausted=False, remaining_pct=None):
        self.exhausted = exhausted
        self._remaining = remaining_pct

    def remaining_pct(self):
        return self._remaining


class TestExtractProvider:
    def test_known_prefixes(self):
        assert _extract_provider("gemini:gemini-3.6-flash") == "gemini"
        assert _extract_provider("openai:gpt-4o-mini") == "openai"
        assert _extract_provider("anthropic:claude-3-opus-latest") == "anthropic"
        assert _extract_provider("deepseek:deepseek-chat") == "deepseek"

    def test_any_prefix(self):
        assert _extract_provider("any:anything") == "any"

    def test_hybrid_strips_prefix(self):
        assert _extract_provider("hybrid:gemini:gemini-3.6-flash") == "gemini"

    def test_generic_provider_prefix(self):
        assert _extract_provider("my-gateway:my-model") == "my-gateway"

    def test_unprefixed_defaults_to_any(self):
        assert _extract_provider("some-local-model") == "any"

    def test_empty_string(self):
        assert _extract_provider("") == "any"


class TestProviderScheduler:
    def test_empty_input_returns_empty_list(self):
        sched = ProviderScheduler()
        assert sched.schedule([]) == []

    def test_single_call_returns_one_wave(self):
        sched = ProviderScheduler()
        calls = [CallSpec(call_id="c1", prompt="test", model="gemini:gemini-3.6-flash")]
        waves = sched.schedule(calls)
        assert len(waves) == 1
        assert len(waves[0].calls) == 1

    def test_same_provider_serialized(self):
        sched = ProviderScheduler()
        calls = [
            CallSpec(call_id="c1", prompt="a", model="gemini:gemini-3.6-flash"),
            CallSpec(call_id="c2", prompt="b", model="gemini:gemini-3.6-flash"),
        ]
        waves = sched.schedule(calls)
        assert len(waves) == 2
        assert len(waves[0].calls) == 1
        assert len(waves[1].calls) == 1
        assert waves[0].calls[0].call_id == "c1"
        assert waves[1].calls[0].call_id == "c2"

    def test_different_providers_in_same_wave(self):
        sched = ProviderScheduler()
        calls = [
            CallSpec(call_id="c1", prompt="a", model="gemini:gemini-3.6-flash"),
            CallSpec(call_id="c2", prompt="b", model="openai:gpt-4o-mini"),
        ]
        waves = sched.schedule(calls)
        assert len(waves) == 1
        assert len(waves[0].calls) == 2

    def test_mixed_providers_serialize_same_provider(self):
        sched = ProviderScheduler()
        calls = [
            CallSpec(call_id="a1", prompt="a1", model="gemini:gemini-3.6-flash"),
            CallSpec(call_id="b1", prompt="b1", model="openai:gpt-4o-mini"),
            CallSpec(call_id="a2", prompt="a2", model="gemini:gemini-3.6-flash"),
            CallSpec(call_id="b2", prompt="b2", model="openai:gpt-4o-mini"),
        ]
        waves = sched.schedule(calls)
        # Wave 1: a1 + b1 (different providers)
        # Wave 2: a2 + b2 (different providers)
        assert len(waves) == 2
        assert len(waves[0].calls) == 2
        assert len(waves[1].calls) == 2
        wave1_ids = [c.call_id for c in waves[0].calls]
        wave2_ids = [c.call_id for c in waves[1].calls]
        assert "a1" in wave1_ids
        assert "b1" in wave1_ids
        assert "a2" in wave2_ids
        assert "b2" in wave2_ids

    def test_usage_tracking(self):
        sched = ProviderScheduler()
        calls = [
            CallSpec(call_id="c1", prompt="a", model="gemini:gemini-3.6-flash"),
            CallSpec(call_id="c2", prompt="b", model="gemini:gemini-3.6-flash"),
            CallSpec(call_id="c3", prompt="c", model="openai:gpt-4o-mini"),
        ]
        sched.schedule(calls)
        summary = sched.usage_summary()
        assert summary["total"] == 3
        assert summary["per_provider"].get("gemini") == 2
        assert summary["per_provider"].get("openai") == 1

    def test_custom_max_concurrent(self):
        sched = ProviderScheduler(max_concurrent={"any": 2})
        calls = [
            CallSpec(call_id="c1", prompt="a", model="any:model-1"),
            CallSpec(call_id="c2", prompt="b", model="any:model-2"),
            CallSpec(call_id="c3", prompt="c", model="any:model-3"),
        ]
        waves = sched.schedule(calls)
        # F4: one call per provider per wave — wave width never exceeds 1
        # per provider; the executor enforces max_concurrent.
        assert len(waves) == 3
        assert all(len(w.calls) == 1 for w in waves)

    def test_unprefixed_models_serialize(self):
        sched = ProviderScheduler()
        calls = [
            CallSpec(call_id="c1", prompt="a", model="local-model-1"),
            CallSpec(call_id="c2", prompt="b", model="local-model-2"),
            CallSpec(call_id="c3", prompt="c", model="local-model-3"),
            CallSpec(call_id="c4", prompt="d", model="local-model-4"),
        ]
        waves = sched.schedule(calls)
        # All unprefixed models map to "any" — one call per wave.
        assert len(waves) == 4
        assert all(len(w.calls) == 1 for w in waves)

    def test_call_spec_to_dict(self):
        cs = CallSpec(call_id="c1", prompt="hello", model="gemini:gemini-3.6-flash", role="reviewer")
        d = cs.to_dict()
        assert d["call_id"] == "c1"
        assert d["model"] == "gemini:gemini-3.6-flash"
        assert d["role"] == "reviewer"
        assert "prompt" not in d  # prompt is not in to_dict (too large)

    def test_ordering_stable(self):
        sched = ProviderScheduler()
        calls_a = [
            CallSpec(call_id="z", prompt="z", model="gemini:gemini-3.6-flash"),
            CallSpec(call_id="a", prompt="a", model="openai:gpt-4o-mini"),
        ]
        calls_b = [
            CallSpec(call_id="a", prompt="a", model="openai:gpt-4o-mini"),
            CallSpec(call_id="z", prompt="z", model="gemini:gemini-3.6-flash"),
        ]
        waves_a = sched.schedule(calls_a)
        waves_b = sched.schedule(calls_b)
        # Different input order but same providers — should produce same wave structure
        id_set_a = {c.call_id for wave in waves_a for c in wave.calls}
        id_set_b = {c.call_id for wave in waves_b for c in wave.calls}
        assert id_set_a == id_set_b


class TestGeminiModelExtraction:
    def test_prefixed_gemini_model_maps_to_gemini(self):
        assert _extract_provider("gemini:gemini-3.6-flash") == "gemini"

    def test_gemini_calls_serialize_at_one_concurrent(self):
        """Calls to the same provider must serialize at one-per-wave."""
        sched = ProviderScheduler()
        calls = [CallSpec(call_id=f"c{i}", prompt=f"p{i}", model="gemini:gemini-3.6-flash") for i in range(4)]
        waves = sched.schedule(calls)
        assert all(len(w.calls) == 1 for w in waves)
        assert len(waves) == 4


class TestModelTiers:
    def test_default_ladder_shape(self):
        assert set(MODEL_TIERS.keys()) == {1, 2, 3}
        assert all(MODEL_TIERS[t] for t in MODEL_TIERS)

    def test_classification_table(self):
        expected = {
            "gemini-2.5-flash-lite": 1,
            "qwen2.5-coder": 1,
            "gemini-2.5-flash": 2,
            "claude-3-5-haiku": 2,
            "gpt-4o-mini": 2,
            "claude-3-5-sonnet": 3,
            "gpt-4o": 3,
            "deepseek-r1": 3,
        }
        for model, tier in expected.items():
            assert classify_model(model) == tier, model

    def test_prefixed_models_classify_same_as_bare(self):
        assert classify_model("openai:gpt-4o-mini") == 2
        assert classify_model("anthropic:claude-3-5-sonnet") == 3
        assert classify_model("hybrid:gemini:gemini-2.5-flash-lite") == 1

    def test_unknown_model_defaults_to_standard_tier2(self):
        assert classify_model("totally-mystery-model-x") == 2
        assert classify_model("kimi-k3") == 2
        assert classify_model("") == 2

    def test_tier_for_risk_mapping(self):
        assert tier_for_risk("trivial") == 1
        assert tier_for_risk("boilerplate") == 1
        assert tier_for_risk("format") == 1
        assert tier_for_risk("") == 2
        assert tier_for_risk("standard") == 2
        assert tier_for_risk("default") == 2
        assert tier_for_risk("anything-unrecognized") == 2
        assert tier_for_risk("crucible") == 3
        assert tier_for_risk("architecture") == 3
        assert tier_for_risk("high_risk") == 3

    def test_env_json_override_and_invalid_fallback(self, monkeypatch):
        monkeypatch.setenv(
            "LETITLOOP_MODEL_TIERS",
            json.dumps({"1": ["tiny-model"], "2": ["mid-model"], "3": ["big-model"]}),
        )
        tiers = model_tiers()
        assert tiers[1] == ["tiny-model"]
        assert classify_model("tiny-model") == 1
        assert classify_model("mid-model") == 2
        monkeypatch.setenv("LETITLOOP_MODEL_TIERS", "not-valid-json")
        assert model_tiers() == MODEL_TIERS


class TestEscalationLadder:
    def test_full_ladder_order_from_tier1(self):
        ladder = escalation_ladder("gemini-2.5-flash-lite")
        assert ladder[0] == "gemini-2.5-flash-lite"
        tiers = [classify_model(m) for m in ladder]
        assert tiers == sorted(tiers)  # non-decreasing
        assert len(set(ladder)) == len(ladder)  # deduped
        assert set(MODEL_TIERS[1] + MODEL_TIERS[2] + MODEL_TIERS[3]) <= set(ladder)

    def test_dedupe_when_start_mid_tier(self):
        ladder = escalation_ladder("claude-3-5-haiku")
        assert ladder[0] == "claude-3-5-haiku"
        assert ladder.count("claude-3-5-haiku") == 1
        assert "gemini-2.5-flash" in ladder
        assert "claude-3-5-sonnet" in ladder
        # Tier1 models are below the start tier -> excluded.
        assert "gemini-2.5-flash-lite" not in ladder

    def test_start_tier_override(self):
        ladder = escalation_ladder(None, start_tier=2)
        assert ladder[0] == MODEL_TIERS[2][0]
        assert all(classify_model(m) >= 2 for m in ladder)

    def test_no_args_starts_at_floor(self):
        ladder = escalation_ladder()
        assert ladder[: len(MODEL_TIERS[1])] == MODEL_TIERS[1]


class TestRiskAwareRouterRoute:
    def test_first_attempt_passthrough(self):
        decision = RiskAwareRouter().route("openai:gpt-4o-mini", 1)
        assert isinstance(decision, RoutingDecision)
        assert decision.model == "openai:gpt-4o-mini"
        assert decision.tier == 2
        assert decision.escalate_context is False
        assert decision.skip_providers == set()

    def test_rate_limit_rotates_within_same_tier(self):
        router = RiskAwareRouter()
        decision = router.route("gemini:gemini-2.5-flash-lite", 2, last_error=_RateLimitError())
        assert decision.model == "qwen2.5-coder"  # next tier-1 candidate
        assert decision.tier == 1
        assert "rate-limit" in decision.reason
        assert decision.skip_providers == {"gemini"}
        assert decision.escalate_context is False

    def test_timeout_rotates_within_same_tier(self):
        decision = RiskAwareRouter().route("gemini-2.5-flash", 2, last_error=TimeoutError())
        assert decision.model == "claude-3-5-haiku"  # next tier-2 candidate
        assert decision.tier == 2
        assert "rate-limit" in decision.reason

    def test_generic_failures_escalate_tier_by_tier(self):
        err = ValueError("bad parse")
        router = RiskAwareRouter()
        d2 = router.route("gemini-2.5-flash-lite", 2, last_error=err)
        assert d2.model == "gemini-2.5-flash"
        assert d2.tier == 2
        assert d2.escalate_context is True
        assert "valueerror" in d2.reason
        d3 = router.route("gemini-2.5-flash-lite", 3, last_error=err)
        assert d3.model == "claude-3-5-sonnet"
        assert d3.tier == 3
        assert d3.escalate_context is True
        d4 = router.route("gemini-2.5-flash-lite", 4, last_error=err)
        assert d4.model == "claude-3-5-sonnet"  # capped at top of ladder

    def test_escalation_caps_at_top_tier(self):
        err = RuntimeError("still failing")
        top = RiskAwareRouter().route("claude-3-5-sonnet", 5, last_error=err)
        assert top.tier == 3
        assert top.escalate_context is True
        assert "runtimeerror" in top.reason

    def test_exhausted_budget_state_downgrades_to_lowest_tier(self):
        state = _BudgetState(exhausted=True)
        decision = RiskAwareRouter().route("claude-3-5-sonnet", 1, budget_state=state)
        assert decision.model == "gemini-2.5-flash-lite"
        assert decision.tier == 1
        assert "budget" in decision.reason

    def test_low_budget_state_prefers_floor(self):
        state = _BudgetState(remaining_pct=0.10)
        decision = RiskAwareRouter().route("gpt-4o", 1, budget_state=state)
        assert decision.tier == 1
        assert "budget" in decision.reason

    def test_budget_guard_ceiling_blocks_higher_tier(self):
        guard = BudgetGuard(max_cost_usd=0.004)
        guard.ledger.record("worker", "x", 10_000, 1_000)  # spent $0.0021
        decision = RiskAwareRouter(budget_guard=guard).route(
            "gemini-2.5-flash", 2, last_error=ValueError("boom")
        )
        assert decision.model == "gemini-2.5-flash-lite"
        assert decision.tier == 1
        assert "budget" in decision.reason
        assert decision.escalate_context is True

    def test_decide_is_pure(self):
        ladder = escalation_ladder("gemini-2.5-flash")
        snapshot = list(ladder)
        kwargs = dict(start_model="gemini-2.5-flash", attempt=3, last_error=ValueError("x"), ladder=ladder)
        a = _decide(**kwargs)
        b = _decide(**kwargs)
        assert a == b
        assert ladder == snapshot


class TestNextRetryModelHook:
    def test_disabled_by_default_returns_none(self, monkeypatch):
        monkeypatch.delenv("LETITLOOP_TIERED_ROUTING", raising=False)
        result = next_retry_model({"model": "gemini-2.5-flash"}, 2, last_error=ValueError("x"))
        assert result is None

    def test_enabled_via_contract_flag(self, monkeypatch):
        monkeypatch.delenv("LETITLOOP_TIERED_ROUTING", raising=False)
        worker = {"model": "gemini-2.5-flash", "escalate_on_retry": True}
        decision = next_retry_model(worker, 2, last_error=ValueError("x"))
        assert isinstance(decision, RoutingDecision)
        assert decision.model == "claude-3-5-sonnet"

    def test_enabled_via_env(self, monkeypatch):
        monkeypatch.setenv("LETITLOOP_TIERED_ROUTING", "1")
        decision = next_retry_model({"model": "gemini-2.5-flash"}, 3, last_error=ValueError("x"))
        assert isinstance(decision, RoutingDecision)
        assert decision.model == "claude-3-5-sonnet"
        assert decision.escalate_context is True

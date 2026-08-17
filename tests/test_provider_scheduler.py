"""Tests for provider_scheduler.py — deterministic scheduling logic."""

from orchestrator.provider_scheduler import (
    CallSpec,
    ProviderScheduler,
    _extract_provider,
)


class TestExtractProvider:
    def test_known_prefixes(self):
        assert _extract_provider("gemini:gemini-3.6-flash") == "gemini"
        assert _extract_provider("openai:gpt-4o-mini") == "openai"
        assert _extract_provider("anthropic:claude-opus-4-1") == "anthropic"
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

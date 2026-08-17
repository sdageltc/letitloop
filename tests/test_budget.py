"""Tests for orchestrator/budget.py — budget guard and loop detector."""

import pytest

from orchestrator.budget import (
    BudgetExhaustedError,
    BudgetGuard,
    LoopDetector,
    UsageLedger,
)


class TestUsageLedger:
    def test_starts_empty(self):
        ledger = UsageLedger()
        assert ledger.total_tokens == 0
        assert ledger.total_cost_usd == 0.0
        assert ledger.call_count == 0

    def test_records_usage(self):
        ledger = UsageLedger()
        ledger.record("Implementer", "model-x", 1000, 200)
        assert ledger.total_prompt_tokens == 1000
        assert ledger.total_completion_tokens == 200
        assert ledger.total_tokens == 1200
        assert ledger.call_count == 1
        assert ledger.total_cost_usd > 0

    def test_records_multiple_calls(self):
        ledger = UsageLedger()
        ledger.record("Implementer", "m1", 1000, 200)
        ledger.record("Critic", "m2", 500, 100)
        assert ledger.call_count == 2
        assert ledger.total_tokens == 1800

    def test_to_dict_includes_totals(self):
        ledger = UsageLedger()
        ledger.record("x", "m", 1000, 500)
        d = ledger.to_dict()
        assert d["total_tokens"] == 1500
        assert d["call_count"] == 1
        assert len(d["records"]) == 1


class TestBudgetGuard:
    def test_no_error_when_under_budget(self):
        guard = BudgetGuard(max_tokens=50000, max_cost_usd=1.0)
        guard.check_before_call(1000, 500)

    def test_raises_on_cost_ceiling(self):
        guard = BudgetGuard(max_tokens=50000, max_cost_usd=0.01)
        guard.ledger.record("x", "m", 100000, 50000)
        with pytest.raises(BudgetExhaustedError, match="cost ceiling"):
            guard.check_before_call(1000, 500)

    def test_raises_on_token_budget(self):
        guard = BudgetGuard(max_tokens=5000, max_cost_usd=10.0)
        guard.ledger.record("x", "m", 4000, 500)
        with pytest.raises(BudgetExhaustedError, match="token budget"):
            guard.check_before_call(1000, 500)

    def test_raises_on_projected_cost(self):
        guard = BudgetGuard(max_tokens=500000, max_cost_usd=0.001)
        with pytest.raises(BudgetExhaustedError, match="projected cost"):
            guard.check_before_call(10000, 2000)

    def test_remaining_pct_full(self):
        guard = BudgetGuard(max_tokens=50000, max_cost_usd=1.0)
        assert guard.remaining_pct() == 1.0

    def test_remaining_pct_partial(self):
        guard = BudgetGuard(max_tokens=50000, max_cost_usd=1.0)
        guard.ledger.record("x", "m", 25000, 5000)
        pct = guard.remaining_pct()
        assert 0.0 < pct < 1.0

    def test_remaining_pct_zero(self):
        guard = BudgetGuard(max_tokens=100, max_cost_usd=0.001)
        guard.ledger.record("x", "m", 100000, 50000)
        assert guard.remaining_pct() == 0.0

    def test_to_dict(self):
        guard = BudgetGuard(max_tokens=50000, max_cost_usd=1.0)
        guard.ledger.record("x", "m", 1000, 500)
        d = guard.to_dict()
        assert d["max_tokens"] == 50000
        assert "ledger" in d


class TestLoopDetector:
    def test_no_stuck_initially(self):
        d = LoopDetector()
        assert d.record_outputs(["hello"]) is None

    def test_detects_identical_outputs(self):
        d = LoopDetector(max_identical_outputs=2)
        assert d.record_outputs(["hello"]) is None
        signal = d.record_outputs(["hello"])
        assert signal is not None
        assert "identical" in signal

    def test_different_outputs_ok(self):
        d = LoopDetector(max_identical_outputs=2)
        assert d.record_outputs(["hello"]) is None
        assert d.record_outputs(["world"]) is None

    def test_detects_identical_failures(self):
        d = LoopDetector(max_identical_failures=2)
        assert d.record_failure("err1") is None
        signal = d.record_failure("err1")
        assert signal is not None

    def test_different_failures_ok(self):
        d = LoopDetector(max_identical_failures=2)
        assert d.record_failure("err1") is None
        assert d.record_failure("err2") is None

    def test_detects_identical_critic_verdicts(self):
        d = LoopDetector(max_identical_verdicts=2)
        assert d.record_critic_verdict("FAIL") is None
        signal = d.record_critic_verdict("FAIL")
        assert signal is not None

    def test_to_dict_includes_state(self):
        d = LoopDetector()
        d.record_outputs(["a"])
        d.record_failure("err")
        d.record_critic_verdict("FAIL")
        dump = d.to_dict()
        assert len(dump["output_hashes"]) == 1
        assert len(dump["failure_reasons"]) == 1
        assert len(dump["critic_verdicts"]) == 1

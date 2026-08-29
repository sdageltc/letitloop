"""Unit and regression tests for framework cookbooks to guarantee zero bit-rot in CI."""

from __future__ import annotations

import asyncio
import json
import pathlib
from datetime import date, timedelta

import pytest


@pytest.mark.fast
def test_langgraph_financial_analyst_run_and_resume(tmp_path: pathlib.Path) -> None:
    """Verify LangGraph financial analyst runs first time and fast-forwards on resume."""
    from examples.cookbooks.langgraph_financial_analyst import (
        DEFAULT_MODEL,
        _workflow_wal_dir,
        run_financial_analyst,
    )

    wal_dir = str(tmp_path / "wal_langgraph_analyst")

    # 1. Initial full run
    r1 = run_financial_analyst(ticker="AAPL", wal_dir=wal_dir, kill_at=None)
    assert "report" in r1
    assert r1["report"]["status"] == "COMPLETED"
    assert "signals" in r1
    assert r1["signals"]["trend"] in ["BULLISH", "BEARISH"]
    assert "market_data" in r1
    assert r1["market_data"]["ticker"] == "AAPL"

    # 2. Resumed run (must be identical)
    r2 = run_financial_analyst(ticker="AAPL", wal_dir=wal_dir, kill_at=None)
    assert r2 == r1

    # 3. WAL persistence check
    run_dir = _workflow_wal_dir(wal_dir, "AAPL", DEFAULT_MODEL, True)
    wal_file = pathlib.Path(run_dir) / "state.wal.jsonl"
    assert wal_file.exists()
    assert "LILWAL02:" in wal_file.read_text(encoding="utf-8")


@pytest.mark.fast
def test_langgraph_financial_analyst_async_dependency_injection(tmp_path: pathlib.Path) -> None:
    """The async workflow accepts fake external adapters and caches their JSON-safe outputs."""
    from examples.cookbooks.langgraph_financial_analyst import (
        _summarise_call_log,
        _workflow_wal_dir,
        run_financial_analyst_async,
    )
    from orchestrator.state import load_state

    calls = {"market": 0, "llm": 0}

    async def fake_market_data_fetcher(ticker: str):
        calls["market"] += 1
        return {
            "ticker": ticker,
            "source": "fake",
            "close_prices": [100.0 + index for index in range(60)],
            "current_price": 159.0,
            "market_cap_b": 100.0,
            "volume": 1_000,
            "timestamp": 0.0,
        }

    async def fake_llm_caller(market_data, indicators, model):
        calls["llm"] += 1
        return {
            "ticker": market_data["ticker"],
            "text": "Test investment memo",
            "recommendation": "HOLD",
            "target_price": indicators["current_price"],
            "risk_level": "MODERATE",
            "rationale": "Deterministic fake memo for dependency-injection testing.",
            "model": model,
            "provider": "fake",
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }

    kwargs = {
        "ticker": "AAPL",
        "wal_dir": str(tmp_path / "wal_injected"),
        "model": "deepseek:test-model",
        "offline": False,
        "market_data_fetcher": fake_market_data_fetcher,
        "llm_caller": fake_llm_caller,
        "call_log_path": str(tmp_path / "external_calls.jsonl"),
    }
    first = asyncio.run(run_financial_analyst_async(**kwargs))
    calls_after_first = _summarise_call_log(kwargs["call_log_path"])
    second = asyncio.run(run_financial_analyst_async(**kwargs))
    calls_after_second = _summarise_call_log(kwargs["call_log_path"])

    assert first == second
    assert calls == {"market": 1, "llm": 1}
    assert (
        calls_after_first
        == calls_after_second
        == {
            "market_fetch_calls": 1,
            "llm_calls": 1,
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
        }
    )
    assert first["market_data"]["source"] == "fake"
    assert first["investment_memo"]["provider"] == "fake"
    assert first["final_report"]["status"] == "COMPLETED"
    assert first["signals"] == first["indicators"]
    assert first["thesis"] == first["investment_memo"]
    assert first["report"] == first["final_report"]
    json.dumps(first)

    run_dir = _workflow_wal_dir(kwargs["wal_dir"], "AAPL", kwargs["model"], False)
    state = load_state(str(pathlib.Path(run_dir) / "state.json"), journal_dir=run_dir)
    memo_step = state.data["step_outputs"]["generate_investment_memo"]
    assert memo_step["investment_memo"]["text"] == "Test investment memo"
    assert memo_step["investment_memo"]["usage"]["total_tokens"] == 15


@pytest.mark.fast
def test_deepseek_llm_prompt_and_usage_without_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """The live node sends every required signal once and preserves provider usage."""
    from examples.cookbooks import langgraph_financial_analyst as cookbook

    captured = {"calls": 0}

    def fake_call_llm(prompt, model, **kwargs):
        captured.update({"calls": captured["calls"] + 1, "prompt": prompt, "model": model, "kwargs": kwargs})
        return {
            "text": "Test investment memo",
            "model": model,
            "provider": "deepseek",
            "usage": {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150},
        }

    monkeypatch.setattr(cookbook, "call_llm", fake_call_llm)
    market_data = {
        "ticker": "AAPL",
        "currency": "USD",
        "current_price": 200.0,
        "market_cap": 3_000_000_000_000,
        "trailing_pe": 25.0,
        "forward_pe": 22.0,
        "revenue_growth": 0.08,
        "profit_margin": 0.21,
    }
    indicators = {
        "sma20": 198.0,
        "sma50": 190.0,
        "rsi14": 61.5,
        "macd": 2.4,
        "macd_signal": 2.0,
        "macd_histogram": 0.4,
    }

    result = asyncio.run(cookbook._live_llm_caller(market_data, indicators, "deepseek:deepseek-v4-flash"))

    assert captured["calls"] == 1
    assert captured["model"] == "deepseek:deepseek-v4-flash"
    for required_text in ("AAPL", "sma20", "sma50", "rsi14", "macd", "macd_signal", "macd_histogram"):
        assert required_text in captured["prompt"]
    assert captured["kwargs"]["temperature"] == pytest.approx(0.2)
    assert result == {
        "ticker": "AAPL",
        "text": "Test investment memo",
        "model": "deepseek:deepseek-v4-flash",
        "provider": "deepseek",
        "usage": {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150},
    }
    json.dumps(result)


@pytest.mark.fast
def test_deepseek_failure_names_ticker_model_and_provider_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provider failures should be actionable without exposing prompt or credentials."""
    from examples.cookbooks import langgraph_financial_analyst as cookbook

    def failed_call_llm(prompt, model, **kwargs):
        del prompt, model, kwargs
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(cookbook, "call_llm", failed_call_llm)
    market_data = {"ticker": "AAPL", "current_price": 200.0}
    indicators = {
        "sma20": 198.0,
        "sma50": 190.0,
        "rsi14": 61.5,
        "macd": 2.4,
        "macd_signal": 2.0,
        "macd_histogram": 0.4,
    }

    with pytest.raises(
        RuntimeError,
        match=r"LLM request failed for AAPL using deepseek:deepseek-v4-flash: provider unavailable",
    ):
        asyncio.run(cookbook._live_llm_caller(market_data, indicators, "deepseek:deepseek-v4-flash"))


@pytest.mark.fast
def test_financial_indicators_known_uptrend_values() -> None:
    """SMA, Wilder RSI, and MACD should match deterministic reference values."""
    from examples.cookbooks.langgraph_financial_analyst import _calculate_technical_signals

    result = _calculate_technical_signals(
        {
            "ticker": "TEST",
            "close_prices": [float(value) for value in range(1, 61)],
            "current_price": 60.0,
        }
    )

    assert result["sma20"] == pytest.approx(50.5)
    assert result["sma50"] == pytest.approx(35.5)
    assert result["rsi14"] == pytest.approx(100.0)
    assert result["macd"] == pytest.approx(6.866964)
    assert result["macd_signal"] == pytest.approx(6.804976)
    assert result["macd_histogram"] == pytest.approx(0.061989)
    assert result["trend"] == "BULLISH"


@pytest.mark.fast
def test_financial_indicators_flat_prices_and_invalid_input() -> None:
    """Flat prices are neutral; empty or undersized histories fail clearly."""
    from examples.cookbooks.langgraph_financial_analyst import _calculate_technical_signals

    flat = _calculate_technical_signals({"ticker": "FLAT", "close_prices": [100.0] * 60, "current_price": 100.0})
    assert flat["sma20"] == pytest.approx(100.0)
    assert flat["sma50"] == pytest.approx(100.0)
    assert flat["rsi14"] == pytest.approx(50.0)
    assert flat["macd"] == pytest.approx(0.0)
    assert flat["macd_signal"] == pytest.approx(0.0)
    assert flat["macd_histogram"] == pytest.approx(0.0)

    falling = _calculate_technical_signals(
        {"ticker": "DOWN", "close_prices": [float(value) for value in range(60, 0, -1)], "current_price": 1.0}
    )
    assert falling["rsi14"] == pytest.approx(0.0)

    with pytest.raises(ValueError, match="At least 50"):
        _calculate_technical_signals({"ticker": "EMPTY", "close_prices": []})
    with pytest.raises(ValueError, match="At least 50"):
        _calculate_technical_signals({"ticker": "SHORT", "close_prices": [100.0] * 49})


@pytest.mark.fast
def test_yfinance_fetch_normalises_json_and_skips_non_finite_values() -> None:
    """The live adapter accepts pandas-like objects without leaking their scalar types."""
    from examples.cookbooks.langgraph_financial_analyst import _fetch_market_data

    class NumpyLikeFloat:
        def __init__(self, value: float):
            self.value = float(value)

        def __float__(self):
            return self.value

    class FakeCloseSeries:
        def items(self):
            values = [NumpyLikeFloat(100.0 + index) for index in range(60)]
            values.extend([float("nan"), float("inf")])
            first_day = date(2025, 1, 1)
            return [(first_day + timedelta(days=index), value) for index, value in enumerate(values)]

    class FakeHistory:
        empty = False

        def __getitem__(self, column):
            assert column == "Close"
            return FakeCloseSeries()

    class FakeTicker:
        def __init__(self, ticker):
            assert ticker == "AAPL"
            self.history_kwargs = None
            self.info = {
                "currency": "USD",
                "trailingPE": float("nan"),
                "forwardPE": NumpyLikeFloat(22.5),
                "revenueGrowth": None,
                "profitMargins": NumpyLikeFloat(0.21),
            }
            self.fast_info = {
                "last_price": NumpyLikeFloat(159.5),
                "market_cap": NumpyLikeFloat(3_000_000_000),
                "last_volume": NumpyLikeFloat(42_000_000),
            }

        def history(self, **kwargs):
            self.history_kwargs = kwargs
            return FakeHistory()

    class FakeYFinance:
        last_ticker = None

        @classmethod
        def Ticker(cls, ticker):
            cls.last_ticker = FakeTicker(ticker)
            return cls.last_ticker

    result = _fetch_market_data(" aapl ", yf_module=FakeYFinance)

    assert FakeYFinance.last_ticker.history_kwargs == {
        "period": "1y",
        "interval": "1d",
        "auto_adjust": True,
        "actions": False,
        "timeout": 15,
    }
    assert result["ticker"] == "AAPL"
    assert result["source"] == "yfinance"
    assert result["currency"] == "USD"
    assert result["current_price"] == pytest.approx(159.5)
    assert result["market_cap"] == 3_000_000_000
    assert result["trailing_pe"] is None
    assert result["forward_pe"] == pytest.approx(22.5)
    assert result["revenue_growth"] is None
    assert result["profit_margin"] == pytest.approx(0.21)
    assert len(result["dates"]) == len(result["close_prices"]) == 60
    assert all(isinstance(price, float) for price in result["close_prices"])
    json.dumps(result)


@pytest.mark.fast
@pytest.mark.parametrize("failure", ["empty", "network"])
def test_yfinance_fetch_reports_empty_history_and_network_errors(failure: str) -> None:
    from examples.cookbooks.langgraph_financial_analyst import MarketDataError, _fetch_market_data

    class FakeTicker:
        def history(self, **kwargs):
            del kwargs
            if failure == "network":
                raise OSError("network unavailable")
            return type("EmptyHistory", (), {"empty": True})()

    class FakeYFinance:
        @staticmethod
        def Ticker(ticker):
            del ticker
            return FakeTicker()

    expected = "Unable to fetch" if failure == "network" else "no historical prices"
    with pytest.raises(MarketDataError, match=expected):
        _fetch_market_data("AAPL", yf_module=FakeYFinance)


@pytest.mark.integration
def test_langgraph_financial_analyst_sigkill_recovery(tmp_path: pathlib.Path) -> None:
    """A post-memo SIGKILL must not repeat market, LLM, or token consumption."""
    from examples.cookbooks.langgraph_financial_analyst import demo_sigkill_recovery

    wal_dir = str(tmp_path / "wal_langgraph_demo")
    result = demo_sigkill_recovery(wal_dir=wal_dir, ticker="NVDA")
    assert "report" in result
    assert result["report"]["status"] == "COMPLETED"
    assert result["ticker"] == "NVDA"
    proof = result["recovery_proof"]
    expected_calls = proof["calls_before_resume"]
    assert proof["calls_after_resume"] == expected_calls
    assert proof["calls_after_warm_run"] == expected_calls
    assert expected_calls["market_fetch_calls"] == 1
    assert expected_calls["llm_calls"] == 1
    assert expected_calls["total_tokens"] > 0
    assert proof["committed_before_resume"] == [
        "compute_indicators",
        "fetch_market_data",
        "generate_investment_memo",
    ]
    assert set(proof["in_memory_fast_forward_ms"]) == {
        "fetch_market_data",
        "compute_indicators",
        "generate_investment_memo",
    }
    # Functional correctness is strict (the trap callback must never execute);
    # CI timing is intentionally generous to avoid failures from noisy runners.
    assert all(duration_ms < 25.0 for duration_ms in proof["in_memory_fast_forward_ms"].values())


@pytest.mark.fast
def test_financial_agent_tickers_use_distinct_wal_caches(tmp_path: pathlib.Path) -> None:
    """AAPL checkpoints must never satisfy an NVDA workflow."""
    from examples.cookbooks.langgraph_financial_analyst import (
        _workflow_wal_dir,
        run_financial_analyst_async,
    )

    calls = {"market": 0, "llm": 0}

    async def fake_market_data_fetcher(ticker: str):
        calls["market"] += 1
        return {
            "ticker": ticker,
            "source": "fake",
            "close_prices": [100.0 + index for index in range(60)],
            "current_price": 159.0,
        }

    async def fake_llm_caller(market_data, indicators, model):
        del indicators
        calls["llm"] += 1
        return {
            "ticker": market_data["ticker"],
            "text": f"Memo for {market_data['ticker']}",
            "model": model,
            "provider": "fake",
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        }

    wal_dir = str(tmp_path / "ticker_wal")
    model = "deepseek:test-model"
    common = {
        "wal_dir": wal_dir,
        "model": model,
        "offline": False,
        "market_data_fetcher": fake_market_data_fetcher,
        "llm_caller": fake_llm_caller,
    }
    aapl = asyncio.run(run_financial_analyst_async(ticker="AAPL", **common))
    nvda = asyncio.run(run_financial_analyst_async(ticker="NVDA", **common))

    assert aapl["ticker"] == aapl["market_data"]["ticker"] == "AAPL"
    assert nvda["ticker"] == nvda["market_data"]["ticker"] == "NVDA"
    assert calls == {"market": 2, "llm": 2}
    assert _workflow_wal_dir(wal_dir, "AAPL", model, False) != _workflow_wal_dir(wal_dir, "NVDA", model, False)


@pytest.mark.integration
def test_dspy_durable_optimize_mock_run(tmp_path: pathlib.Path) -> None:
    """Preserve the upstream subprocess smoke test for the completed DSPy cookbook."""
    import os
    import subprocess
    import sys

    cookbook_path = (
        pathlib.Path(__file__).resolve().parent.parent / "examples" / "cookbooks" / "dspy_durable_optimize.py"
    )
    assert cookbook_path.exists(), f"Cookbook not found: {cookbook_path}"

    wal_dir = tmp_path / "dspy_wal_subprocess"
    wal_dir.mkdir()
    env = os.environ.copy()
    env["PYTHONPATH"] = str(pathlib.Path(__file__).resolve().parent.parent)
    env["LETITLOOP_WAL_DIR"] = str(wal_dir)
    env["DSPY_DEMO_MODE"] = "1"

    result = subprocess.run(
        [sys.executable, str(cookbook_path), "--demo", "--wal-dir", str(wal_dir)],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"Cookbook failed with returncode {result.returncode}:\n{result.stderr}"
    assert "SUCCESS: DSPy prompt optimizer recovered" in result.stdout or "Optimization Result:" in result.stdout


@pytest.mark.fast
def test_cookbook_module_builders() -> None:
    """Verify the financial cookbook constructs the required graph topology."""
    from examples.cookbooks.langgraph_financial_analyst import _build_langgraph_pipeline

    lg_pipeline = _build_langgraph_pipeline()
    graph = lg_pipeline.get_graph()
    assert set(graph.nodes) == {
        "__start__",
        "fetch_market_data",
        "compute_indicators",
        "generate_investment_memo",
        "generate_report",
        "__end__",
    }
    assert {(edge.source, edge.target) for edge in graph.edges} == {
        ("__start__", "fetch_market_data"),
        ("fetch_market_data", "compute_indicators"),
        ("compute_indicators", "generate_investment_memo"),
        ("generate_investment_memo", "generate_report"),
        ("generate_report", "__end__"),
    }

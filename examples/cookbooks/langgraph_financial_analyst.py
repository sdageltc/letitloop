"""LangGraph Financial Analyst Cookbook — crash-proof analysis with @durable_async.

Combines injectable market-data/LLM adapters with LangGraph StateGraph orchestration
and LetItLoop ``@durable_async`` WAL checkpoints.  The adapters make the cookbook
safe to exercise offline in tests while later phases can supply yfinance and a real
LLM without changing the graph or recovery code.

Architecture:
  Fetch Market Data -> Calculate Indicators -> Generate Investment Memo -> Generate Report
  (WAL step 1)          (WAL step 2)          (WAL step 3)               (WAL step 4)

On POSIX, the demo sends a real SIGKILL immediately after the investment memo's
durable step has been committed (Windows uses exit 137).  An independent fsynced
call log proves that recovery does not repeat yfinance or LLM calls.  Its <1ms
timings cover only in-memory ``async_step``
cache lookups; process startup, imports, WAL loading, and unfinished work are excluded.

Modes:
  - offline (default): deterministic market data and memo; no external API calls
  - live: yfinance market data and DeepSeek through ``orchestrator.llm.call_llm``

Usage:
  python -m pip install -e ".[financial-agent]"
  python examples/cookbooks/langgraph_financial_analyst.py --demo
  python examples/cookbooks/langgraph_financial_analyst.py --ticker NVDA --offline
  DEEPSEEK_API_KEY=... python examples/cookbooks/langgraph_financial_analyst.py --ticker AAPL --live
  python examples/cookbooks/langgraph_financial_analyst.py --ticker AAPL --offline --kill-at 2
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import hashlib
import json
import math
import os
import pathlib
import subprocess
import sys
import time
from datetime import date, timedelta
from typing import Any, Awaitable, Callable, Dict, Optional, TypedDict

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from orchestrator.decorators import async_step, durable_async  # noqa: E402
from orchestrator.llm import call_llm  # noqa: E402

WAL_DIR_DEFAULT = str(ROOT / ".bench_wal" / "cookbooks" / "langgraph_financial_analyst")
GOAL_ID = "langgraph-financial-analyst"
DEFAULT_MODEL = "deepseek:deepseek-v4-flash"

JsonDict = Dict[str, Any]
MarketDataFetcher = Callable[[str], Awaitable[JsonDict]]
LLMCaller = Callable[[JsonDict, JsonDict, str], Awaitable[JsonDict]]


class MarketDataError(RuntimeError):
    """Raised when live market data cannot be fetched or normalised safely."""


class FinancialAgentState(TypedDict, total=False):
    """JSON/WAL-safe state shared by every LangGraph node."""

    ticker: str
    model: str
    offline: bool
    market_data: JsonDict
    indicators: JsonDict
    investment_memo: JsonDict
    final_report: JsonDict


@dataclasses.dataclass(frozen=True)
class FinancialAgentDependencies:
    """External side effects injected into the graph for live use or tests."""

    market_data_fetcher: MarketDataFetcher
    llm_caller: LLMCaller


# --- Step 1: Market Data Ingestion (live adapter comes in phase 2) ---


def _simulated_market_data(ticker: str) -> JsonDict:
    """Return deterministic JSON-safe data for tests and the offline demo."""
    ticker = _normalise_ticker(ticker)
    seed = int(hashlib.sha256(ticker.encode()).hexdigest()[:6], 16)
    base = 100.0 + (seed % 150)
    first_day = date(2025, 1, 1)
    close_prices = [round(base + (index * 0.15) + (((index % 7) - 3) * 0.4), 2) for index in range(260)]
    dates = [(first_day + timedelta(days=index)).isoformat() for index in range(len(close_prices))]
    market_cap = int(base * 12_500_000_000)
    return {
        "ticker": ticker,
        "source": "simulated",
        "period": "1y",
        "interval": "1d",
        "currency": "USD",
        "current_price": close_prices[-1],
        "market_cap": market_cap,
        "trailing_pe": 24.0,
        "forward_pe": 21.5,
        "revenue_growth": 0.08,
        "profit_margin": 0.21,
        "volume": 54_200_000,
        "dates": dates,
        "close_prices": close_prices,
        # Compatibility aliases retained until the legacy report is replaced.
        "prices": close_prices,
        "market_cap_b": round(market_cap / 1_000_000_000, 2),
    }


def _finite_float(value: Any) -> Optional[float]:
    """Convert numpy/pandas scalars to a finite built-in float."""
    if value is None or isinstance(value, bool):
        return None
    try:
        converted = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return converted if math.isfinite(converted) else None


def _finite_int(value: Any) -> Optional[int]:
    converted = _finite_float(value)
    return int(converted) if converted is not None else None


def _lookup(container: Any, *names: str) -> Any:
    """Read a value from yfinance dict-like or attribute-style objects."""
    if container is None:
        return None
    for name in names:
        try:
            if isinstance(container, dict) and name in container:
                return container[name]
            return container[name]
        except (KeyError, TypeError, AttributeError):
            try:
                value = getattr(container, name)
            except (AttributeError, KeyError, TypeError):
                continue
            else:
                return value
    return None


def _date_to_iso(value: Any) -> str:
    candidate = value.date() if hasattr(value, "date") and callable(value.date) else value
    if hasattr(candidate, "isoformat") and callable(candidate.isoformat):
        return str(candidate.isoformat())
    return str(candidate)


def _normalise_history(history: Any) -> tuple[list[str], list[float]]:
    """Extract aligned dates/prices from a pandas-like yfinance history frame."""
    if history is None or bool(getattr(history, "empty", False)):
        raise MarketDataError("yfinance returned no historical prices")
    try:
        close_series = history["Close"]
        rows = close_series.items()
    except (KeyError, TypeError, AttributeError) as exc:
        raise MarketDataError("yfinance history is missing the Close column") from exc

    dates: list[str] = []
    close_prices: list[float] = []
    for index, raw_price in rows:
        price = _finite_float(raw_price)
        if price is None:
            continue
        dates.append(_date_to_iso(index))
        close_prices.append(price)

    if len(close_prices) < 50:
        raise MarketDataError(f"At least 50 finite closing prices are required; yfinance returned {len(close_prices)}")
    return dates, close_prices


def _fetch_market_data(ticker: str, *, yf_module: Any = None) -> JsonDict:
    """Fetch one year of prices/fundamentals and return only JSON-safe values."""
    ticker = _normalise_ticker(ticker)
    if yf_module is None:
        try:
            import yfinance as yf_module
        except ImportError as exc:
            raise MarketDataError("yfinance is required for live mode; install it with `pip install yfinance`") from exc

    try:
        security = yf_module.Ticker(ticker)
        history = security.history(
            period="1y",
            interval="1d",
            auto_adjust=True,
            actions=False,
            timeout=15,
        )
    except Exception as exc:
        raise MarketDataError(f"Unable to fetch one year of history for {ticker}: {exc}") from exc

    dates, close_prices = _normalise_history(history)

    try:
        info = getattr(security, "info", None) or {}
    except Exception:
        info = {}
    try:
        fast_info = getattr(security, "fast_info", None) or {}
    except Exception:
        fast_info = {}

    current_price = _finite_float(
        _lookup(fast_info, "last_price", "lastPrice")
        or _lookup(info, "currentPrice", "regularMarketPrice")
        or close_prices[-1]
    )
    market_cap = _finite_int(_lookup(fast_info, "market_cap", "marketCap") or _lookup(info, "marketCap"))
    volume = _finite_int(
        _lookup(fast_info, "last_volume", "lastVolume") or _lookup(info, "regularMarketVolume", "volume")
    )
    currency = _lookup(info, "currency") or _lookup(fast_info, "currency")
    currency = str(currency) if currency is not None else None

    return {
        "ticker": ticker,
        "source": "yfinance",
        "period": "1y",
        "interval": "1d",
        "currency": currency,
        "current_price": current_price or close_prices[-1],
        "market_cap": market_cap,
        "trailing_pe": _finite_float(_lookup(info, "trailingPE")),
        "forward_pe": _finite_float(_lookup(info, "forwardPE")),
        "revenue_growth": _finite_float(_lookup(info, "revenueGrowth")),
        "profit_margin": _finite_float(_lookup(info, "profitMargins")),
        "volume": volume,
        "dates": dates,
        "close_prices": close_prices,
        # Compatibility aliases retained until the legacy report is replaced.
        "prices": close_prices,
        "market_cap_b": round(market_cap / 1_000_000_000, 2) if market_cap is not None else None,
    }


async def _offline_market_data_fetcher(ticker: str) -> JsonDict:
    """Async dependency used by tests and ``--offline`` runs."""
    return _simulated_market_data(ticker)


async def _default_market_data_fetcher(ticker: str) -> JsonDict:
    """Run the synchronous market-data adapter without blocking the event loop."""
    return await asyncio.to_thread(_fetch_market_data, ticker)


# --- Step 2: Technical Signal Computation ---


def _ema(values: list[float], period: int) -> list[float]:
    """Return an EMA series seeded with the first value."""
    if not values:
        raise ValueError("EMA requires at least one price")
    if period <= 0:
        raise ValueError("EMA period must be positive")
    multiplier = 2.0 / (period + 1)
    output = [values[0]]
    for value in values[1:]:
        output.append(((value - output[-1]) * multiplier) + output[-1])
    return output


def _rsi(values: list[float], period: int = 14) -> float:
    """Calculate Wilder's RSI, including stable all-up/down/flat handling."""
    if len(values) < period + 1:
        raise ValueError(f"RSI{period} requires at least {period + 1} prices")
    changes = [values[index] - values[index - 1] for index in range(1, len(values))]
    gains = [max(change, 0.0) for change in changes]
    losses = [max(-change, 0.0) for change in changes]
    average_gain = sum(gains[:period]) / period
    average_loss = sum(losses[:period]) / period
    for gain, loss in zip(gains[period:], losses[period:]):
        average_gain = ((average_gain * (period - 1)) + gain) / period
        average_loss = ((average_loss * (period - 1)) + loss) / period

    if average_gain == 0.0 and average_loss == 0.0:
        return 50.0
    if average_loss == 0.0:
        return 100.0
    if average_gain == 0.0:
        return 0.0
    relative_strength = average_gain / average_loss
    return 100.0 - (100.0 / (1.0 + relative_strength))


def _calculate_technical_signals(market_data: JsonDict) -> JsonDict:
    """Compute SMA20/SMA50, Wilder RSI14, and MACD(12, 26, 9)."""
    raw_prices = market_data.get("close_prices") or market_data.get("prices") or []
    prices = [price for raw in raw_prices if (price := _finite_float(raw)) is not None]
    if len(prices) < 50:
        raise ValueError(f"At least 50 finite closing prices are required; received {len(prices)}")

    sma20 = sum(prices[-20:]) / 20
    sma50 = sum(prices[-50:]) / 50
    rsi14 = _rsi(prices, period=14)
    ema12 = _ema(prices, period=12)
    ema26 = _ema(prices, period=26)
    macd_series = [short - long for short, long in zip(ema12, ema26)]
    signal_series = _ema(macd_series, period=9)
    macd = macd_series[-1]
    macd_signal = signal_series[-1]
    macd_histogram = macd - macd_signal
    returns = [
        (prices[index] - prices[index - 1]) / prices[index - 1]
        for index in range(1, len(prices))
        if prices[index - 1] != 0
    ]
    volatility = sum(abs(value) for value in returns) / len(returns) if returns else 0.0
    current_price = _finite_float(market_data.get("current_price")) or prices[-1]
    trend = "BULLISH" if sma20 > sma50 and macd > macd_signal else "BEARISH"

    return {
        "ticker": market_data["ticker"],
        "current_price": round(current_price, 6),
        "sma20": round(sma20, 6),
        "sma50": round(sma50, 6),
        "rsi14": round(rsi14, 6),
        "macd": round(macd, 6),
        "macd_signal": round(macd_signal, 6),
        "macd_histogram": round(macd_histogram, 6),
        "trend": trend,
        "volatility": round(volatility, 6),
        # Compatibility aliases retained for the phase-1 report contract.
        "sma_short": round(sma20, 6),
        "sma_long": round(sma50, 6),
        "rsi_approx": round(rsi14, 6),
    }


# --- Step 3: LLM / Reasoning Synthesis ---


def _synthesize_analyst_thesis(signals: JsonDict) -> JsonDict:
    """Synthesize investment thesis, valuation targets, and key risk factors."""
    ticker = signals["ticker"]
    trend = signals["trend"]
    current_price = signals["current_price"]

    if trend == "BULLISH":
        recommendation = "OVERWEIGHT"
        target_price = round(current_price * 1.18, 2)
        risk_level = "MODERATE"
        rationale = f"{ticker} exhibits upward momentum with price trading above SMA long-term average."
    else:
        recommendation = "NEUTRAL"
        target_price = round(current_price * 1.02, 2)
        risk_level = "HIGH"
        rationale = f"{ticker} displays consolidation pressure below moving average threshold."

    return {
        "ticker": ticker,
        "recommendation": recommendation,
        "target_price": target_price,
        "risk_level": risk_level,
        "rationale": rationale,
    }


async def _offline_llm_caller(market_data: JsonDict, indicators: JsonDict, model: str) -> JsonDict:
    """Return a deterministic memo and simulated token usage without network calls."""
    memo = _synthesize_analyst_thesis(indicators)
    text = f"""## Investment view
{memo["recommendation"]} on {market_data["ticker"]} with a target price of ${memo["target_price"]}.

## Main reasons
{memo["rationale"]}

## Risks
Risk level: {memo["risk_level"]}. This offline example does not include all company-specific risks.

## Disclaimer
This educational example is not financial advice.
""".strip()
    prompt = _build_investment_memo_prompt(market_data, indicators)
    prompt_tokens = max(1, len(prompt) // 4)
    completion_tokens = max(1, len(text) // 4)
    memo.update(
        {
            "text": text,
            "model": model,
            "provider": "offline",
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
        }
    )
    return memo


def _build_investment_memo_prompt(market_data: JsonDict, indicators: JsonDict) -> str:
    """Build a compact, inspectable prompt without sending the full price history."""
    fundamentals = {
        "ticker": market_data["ticker"],
        "currency": market_data.get("currency"),
        "current_price": market_data.get("current_price"),
        "market_cap": market_data.get("market_cap"),
        "trailing_pe": market_data.get("trailing_pe"),
        "forward_pe": market_data.get("forward_pe"),
        "revenue_growth": market_data.get("revenue_growth"),
        "profit_margin": market_data.get("profit_margin"),
    }
    technicals = {
        "sma20": indicators["sma20"],
        "sma50": indicators["sma50"],
        "rsi14": indicators["rsi14"],
        "macd": indicators["macd"],
        "macd_signal": indicators["macd_signal"],
        "macd_histogram": indicators["macd_histogram"],
    }
    return f"""Write a concise investment memorandum for {market_data["ticker"]}.

Fundamentals and current price:
{json.dumps(fundamentals, indent=2, sort_keys=True)}

Technical indicators:
{json.dumps(technicals, indent=2, sort_keys=True)}

Use Markdown and include these clearly labelled sections:
1. Investment view (bullish, neutral, or bearish)
2. Main reasons grounded in the supplied fundamentals and indicators
3. Key risks and uncertainties
4. Disclaimer that this is educational analysis, not financial advice

Do not invent missing fundamentals. State when a supplied value is unavailable.
""".strip()


def _normalise_llm_usage(usage: Any) -> JsonDict:
    """Return the provider's token accounting as finite JSON-safe integers."""
    raw_usage = usage if isinstance(usage, dict) else {}
    prompt_tokens = max(0, _finite_int(raw_usage.get("prompt_tokens")) or 0)
    completion_tokens = max(0, _finite_int(raw_usage.get("completion_tokens")) or 0)
    total_tokens = _finite_int(raw_usage.get("total_tokens"))
    if total_tokens is None:
        total_tokens = prompt_tokens + completion_tokens
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": max(0, total_tokens),
    }


async def _live_llm_caller(market_data: JsonDict, indicators: JsonDict, model: str) -> JsonDict:
    """Call the repository's synchronous provider adapter without blocking the graph."""
    prompt = _build_investment_memo_prompt(market_data, indicators)
    try:
        response = await asyncio.to_thread(
            call_llm,
            prompt,
            model,
            system=(
                "You are a careful financial research assistant. Use only the supplied data, "
                "separate observations from inference, and never present the memo as personalized advice."
            ),
            max_tokens=1_000,
            temperature=0.2,
        )
    except Exception as exc:
        raise RuntimeError(f"LLM request failed for {market_data['ticker']} using {model}: {exc}") from exc
    if not isinstance(response, dict):
        raise RuntimeError("The LLM adapter returned a non-object response")
    text = response.get("text")
    if not isinstance(text, str) or not text.strip():
        raise RuntimeError("The LLM adapter returned an empty investment memo")
    return {
        "ticker": market_data["ticker"],
        "text": text.strip(),
        "model": str(response.get("model") or model),
        "provider": str(response.get("provider") or model.partition(":")[0]),
        "usage": _normalise_llm_usage(response.get("usage")),
    }


# --- Step 4: Executive Briefing Generation ---


def _generate_final_report(thesis: JsonDict, signals: JsonDict) -> JsonDict:
    """Produce formatted executive analyst artifact."""
    ticker = thesis["ticker"]
    memo_text = str(thesis.get("text") or thesis.get("rationale") or "No investment memo was produced.")
    if thesis.get("recommendation") and thesis.get("target_price") is not None:
        rating_line = (
            f"**Rating:** {thesis['recommendation']} | **Target:** ${thesis['target_price']} "
            f"| **Risk:** {thesis.get('risk_level', 'N/A')}"
        )
        summary = f"{ticker} rated {thesis['recommendation']} (Target: ${thesis['target_price']})"
    else:
        rating_line = (
            f"**Memo model:** {thesis.get('model', 'unknown')} | **Provider:** {thesis.get('provider', 'unknown')}"
        )
        summary = f"{ticker} investment memorandum generated"
    report_md = f"""# Equity Research Note: {ticker}
{rating_line}

## Key Technicals
- Spot Price: ${signals["current_price"]}
- Trend Signal: {signals["trend"]} (RSI14: {signals["rsi14"]})
- SMA20 / SMA50: ${signals["sma20"]} / ${signals["sma50"]}
- MACD / Signal / Histogram: {signals["macd"]} / {signals["macd_signal"]} / {signals["macd_histogram"]}

## Investment Thesis
{memo_text}
""".strip()

    return {
        "ticker": ticker,
        "status": "COMPLETED",
        "summary": summary,
        "markdown": report_md,
    }


# --- Injectable node contracts / LangGraph builder ---


async def _fetch_market_data_node(
    state: FinancialAgentState, dependencies: FinancialAgentDependencies
) -> FinancialAgentState:
    return {"market_data": await dependencies.market_data_fetcher(state["ticker"])}


async def _compute_indicators_node(
    state: FinancialAgentState, dependencies: FinancialAgentDependencies
) -> FinancialAgentState:
    del dependencies
    return {"indicators": _calculate_technical_signals(state["market_data"])}


async def _generate_investment_memo_node(
    state: FinancialAgentState, dependencies: FinancialAgentDependencies
) -> FinancialAgentState:
    memo = await dependencies.llm_caller(state["market_data"], state["indicators"], state["model"])
    return {"investment_memo": memo}


async def _generate_report_node(
    state: FinancialAgentState, dependencies: FinancialAgentDependencies
) -> FinancialAgentState:
    del dependencies
    report = _generate_final_report(state["investment_memo"], state["indicators"])
    return {"final_report": report}


def _normalise_ticker(ticker: str) -> str:
    normalised = ticker.upper().strip()
    if not normalised or not all(char.isalnum() or char in {"-", "."} for char in normalised):
        raise ValueError(f"Invalid ticker symbol: {ticker!r}")
    return normalised


def _workflow_identity(ticker: str, model: str, offline: bool) -> str:
    """Return a stable ticker-aware identity so unrelated runs never share cache."""
    ticker_slug = _normalise_ticker(ticker).lower().replace(".", "-")
    mode = "offline" if offline else "live"
    model_digest = hashlib.sha256(model.encode("utf-8")).hexdigest()[:8]
    return f"{ticker_slug}-{mode}-{model_digest}"


def _workflow_wal_dir(wal_dir: str, ticker: str, model: str, offline: bool) -> str:
    return str(pathlib.Path(wal_dir) / _workflow_identity(ticker, model, offline))


def _append_call_event(call_log_path: str, event: JsonDict) -> None:
    """Append and fsync one external-call receipt independent of the workflow WAL."""
    path = pathlib.Path(call_log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"timestamp": time.time(), **event}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _with_call_logging(
    dependencies: FinancialAgentDependencies, call_log_path: Optional[str]
) -> FinancialAgentDependencies:
    """Wrap successful external calls with receipts that survive SIGKILL."""
    if call_log_path is None:
        return dependencies

    async def logged_market_fetcher(ticker: str) -> JsonDict:
        result = await dependencies.market_data_fetcher(ticker)
        _append_call_event(call_log_path, {"event": "market_fetch", "ticker": ticker})
        return result

    async def logged_llm_caller(market_data: JsonDict, indicators: JsonDict, model: str) -> JsonDict:
        result = await dependencies.llm_caller(market_data, indicators, model)
        _append_call_event(
            call_log_path,
            {
                "event": "llm_call",
                "ticker": market_data["ticker"],
                "model": model,
                "usage": _normalise_llm_usage(result.get("usage")),
            },
        )
        return result

    return FinancialAgentDependencies(
        market_data_fetcher=logged_market_fetcher,
        llm_caller=logged_llm_caller,
    )


def _summarise_call_log(call_log_path: str) -> JsonDict:
    """Summarise the independent call receipts used by the recovery proof."""
    summary = {
        "market_fetch_calls": 0,
        "llm_calls": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
    }
    path = pathlib.Path(call_log_path)
    if not path.exists():
        return summary

    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Invalid call log entry at line {line_number}: {exc}") from exc
        if event.get("event") == "market_fetch":
            summary["market_fetch_calls"] += 1
        elif event.get("event") == "llm_call":
            summary["llm_calls"] += 1
            usage = _normalise_llm_usage(event.get("usage"))
            summary["prompt_tokens"] += usage["prompt_tokens"]
            summary["completion_tokens"] += usage["completion_tokens"]
            summary["total_tokens"] += usage["total_tokens"]
    return summary


def _resolve_dependencies(
    *,
    offline: bool,
    market_data_fetcher: Optional[MarketDataFetcher],
    llm_caller: Optional[LLMCaller],
    call_log_path: Optional[str] = None,
) -> FinancialAgentDependencies:
    dependencies = FinancialAgentDependencies(
        market_data_fetcher=(
            market_data_fetcher or (_offline_market_data_fetcher if offline else _default_market_data_fetcher)
        ),
        llm_caller=(llm_caller or (_offline_llm_caller if offline else _live_llm_caller)),
    )
    return _with_call_logging(dependencies, call_log_path)


def _kill_after_step(kill_at: Optional[int], step_index: int) -> None:
    if kill_at == step_index:
        if os.name == "nt":
            os._exit(137)
        import signal

        os.kill(os.getpid(), signal.SIGKILL)


def _build_langgraph_pipeline(
    dependencies: Optional[FinancialAgentDependencies] = None,
    *,
    kill_at: Optional[int] = None,
):
    """Build the real four-node async StateGraph used by every workflow run."""
    try:
        from langgraph.graph import END, START, StateGraph
    except ImportError as exc:
        raise RuntimeError(
            "LangGraph is required for this cookbook; install it with `python -m pip install -e '.[financial-agent]'`."
        ) from exc

    deps = dependencies or _resolve_dependencies(offline=True, market_data_fetcher=None, llm_caller=None)
    workflow = StateGraph(FinancialAgentState)

    async def fetch_market_data(state: FinancialAgentState) -> FinancialAgentState:
        update = await async_step("fetch_market_data", _fetch_market_data_node, state, deps)
        _kill_after_step(kill_at, 0)
        return update

    async def compute_indicators(state: FinancialAgentState) -> FinancialAgentState:
        update = await async_step("compute_indicators", _compute_indicators_node, state, deps)
        _kill_after_step(kill_at, 1)
        return update

    async def generate_investment_memo(state: FinancialAgentState) -> FinancialAgentState:
        update = await async_step("generate_investment_memo", _generate_investment_memo_node, state, deps)
        _kill_after_step(kill_at, 2)
        return update

    async def generate_report(state: FinancialAgentState) -> FinancialAgentState:
        update = await async_step("generate_report", _generate_report_node, state, deps)
        _kill_after_step(kill_at, 3)
        return update

    workflow.add_node("fetch_market_data", fetch_market_data)
    workflow.add_node("compute_indicators", compute_indicators)
    workflow.add_node("generate_investment_memo", generate_investment_memo)
    workflow.add_node("generate_report", generate_report)
    workflow.add_edge(START, "fetch_market_data")
    workflow.add_edge("fetch_market_data", "compute_indicators")
    workflow.add_edge("compute_indicators", "generate_investment_memo")
    workflow.add_edge("generate_investment_memo", "generate_report")
    workflow.add_edge("generate_report", END)
    return workflow.compile()


def _with_legacy_aliases(state: FinancialAgentState) -> JsonDict:
    """Expose the new names while preserving the existing cookbook result keys."""
    result: JsonDict = dict(state)
    result["signals"] = state["indicators"]
    result["thesis"] = state["investment_memo"]
    result["report"] = state["final_report"]
    return result


async def run_financial_analyst_async(
    ticker: str = "AAPL",
    wal_dir: str = WAL_DIR_DEFAULT,
    kill_at: Optional[int] = None,
    *,
    model: str = DEFAULT_MODEL,
    offline: bool = True,
    market_data_fetcher: Optional[MarketDataFetcher] = None,
    llm_caller: Optional[LLMCaller] = None,
    call_log_path: Optional[str] = None,
) -> JsonDict:
    """Execute the injectable four-node workflow inside ``@durable_async``."""
    ticker = _normalise_ticker(ticker)
    dependencies = _resolve_dependencies(
        offline=offline,
        market_data_fetcher=market_data_fetcher,
        llm_caller=llm_caller,
        call_log_path=call_log_path,
    )
    identity = _workflow_identity(ticker, model, offline)
    run_dir = _workflow_wal_dir(wal_dir, ticker, model, offline)

    @durable_async(goal_id=f"{GOAL_ID}:{identity}", wal_dir=run_dir)
    async def _execute() -> JsonDict:
        initial_state: FinancialAgentState = {
            "ticker": ticker,
            "model": model,
            "offline": offline,
        }
        graph = _build_langgraph_pipeline(dependencies, kill_at=kill_at)
        final_state = await graph.ainvoke(initial_state)
        return _with_legacy_aliases(final_state)

    return await _execute()


def run_financial_analyst(
    ticker: str = "AAPL",
    wal_dir: str = WAL_DIR_DEFAULT,
    kill_at: Optional[int] = None,
    *,
    model: str = DEFAULT_MODEL,
    offline: bool = True,
    market_data_fetcher: Optional[MarketDataFetcher] = None,
    llm_caller: Optional[LLMCaller] = None,
    call_log_path: Optional[str] = None,
) -> JsonDict:
    """Synchronous CLI/backward-compatible wrapper around the async workflow."""
    return asyncio.run(
        run_financial_analyst_async(
            ticker=ticker,
            wal_dir=wal_dir,
            kill_at=kill_at,
            model=model,
            offline=offline,
            market_data_fetcher=market_data_fetcher,
            llm_caller=llm_caller,
            call_log_path=call_log_path,
        )
    )


async def _measure_in_memory_fast_forwards(
    *,
    wal_dir: str,
    ticker: str,
    model: str,
    offline: bool,
    step_ids: tuple[str, ...],
) -> dict[str, float]:
    """Measure only cached ``async_step`` lookups after WAL initialization."""
    ticker = _normalise_ticker(ticker)
    identity = _workflow_identity(ticker, model, offline)
    run_dir = _workflow_wal_dir(wal_dir, ticker, model, offline)

    @durable_async(goal_id=f"{GOAL_ID}:{identity}", wal_dir=run_dir)
    async def _measure() -> dict[str, float]:
        async def must_not_execute() -> None:
            raise AssertionError("A measured fast-forward unexpectedly executed its underlying function")

        durations_ms: dict[str, float] = {}
        for step_id in step_ids:
            started_ns = time.perf_counter_ns()
            await async_step(step_id, must_not_execute)
            durations_ms[step_id] = (time.perf_counter_ns() - started_ns) / 1_000_000
        return durations_ms

    return await _measure()


def demo_sigkill_recovery(
    wal_dir: str = WAL_DIR_DEFAULT,
    ticker: str = "NVDA",
    *,
    model: str = DEFAULT_MODEL,
    offline: bool = True,
) -> JsonDict:
    """Prove committed calls survive POSIX SIGKILL (or Windows exit 137) without repetition."""
    import shutil

    ticker = _normalise_ticker(ticker)
    run_dir = _workflow_wal_dir(wal_dir, ticker, model, offline)
    call_log_path = str(pathlib.Path(run_dir) / "external_calls.jsonl")
    committed_steps = ("fetch_market_data", "compute_indicators", "generate_investment_memo")
    if os.path.exists(run_dir):
        shutil.rmtree(run_dir, ignore_errors=True)

    print(f"[demo] WAL directory: {run_dir}")
    print(f"[demo] Independent call log: {call_log_path}")
    print(f"[demo] 1) Launching {ticker}; SIGKILL follows the committed investment-memo step...")

    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            f"""
import pathlib, sys
sys.path.insert(0, {str(ROOT)!r})
from examples.cookbooks.langgraph_financial_analyst import run_financial_analyst
run_financial_analyst(
    ticker={ticker!r}, wal_dir={wal_dir!r}, kill_at=2,
    model={model!r}, offline={offline!r}, call_log_path={call_log_path!r}
)
""",
        ],
        env={**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONPATH": str(ROOT)},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        child_stdout, child_stderr = proc.communicate(timeout=120 if not offline else 30)
    except subprocess.TimeoutExpired as exc:
        proc.kill()
        proc.communicate()
        raise RuntimeError("Demo subprocess did not reach the post-memo SIGKILL checkpoint") from exc

    expected_exit_codes = {137} if os.name == "nt" else {-9, 137}
    if proc.returncode not in expected_exit_codes:
        details = (child_stderr or child_stdout).strip()
        raise RuntimeError(f"Demo subprocess exited with {proc.returncode}, not SIGKILL; output: {details[-1000:]}")
    print(f"[demo]    Subprocess received SIGKILL (pid={proc.pid}, exit={proc.returncode})")

    from orchestrator.state import load_state

    checkpoint = load_state(str(pathlib.Path(run_dir) / "state.json"), journal_dir=run_dir)
    committed_before_resume = set(checkpoint.data.get("step_outputs", {}))
    assert committed_before_resume == set(committed_steps), committed_before_resume
    print(f"[demo]    WAL committed steps: {', '.join(sorted(committed_before_resume))}; report pending")

    calls_before_resume = _summarise_call_log(call_log_path)
    assert calls_before_resume["market_fetch_calls"] == 1, calls_before_resume
    assert calls_before_resume["llm_calls"] == 1, calls_before_resume
    assert calls_before_resume["total_tokens"] > 0, calls_before_resume
    print(
        "[demo]    Before recovery: "
        f"market fetch calls={calls_before_resume['market_fetch_calls']}, "
        f"LLM calls={calls_before_resume['llm_calls']}, "
        f"tokens={calls_before_resume['total_tokens']}"
    )

    fast_forward_ms = asyncio.run(
        _measure_in_memory_fast_forwards(
            wal_dir=wal_dir,
            ticker=ticker,
            model=model,
            offline=offline,
            step_ids=committed_steps,
        )
    )
    for step_id in committed_steps:
        duration_ms = fast_forward_ms[step_id]
        target = "PASS" if duration_ms < 1.0 else "above target on this run"
        print(f"[demo]    {step_id} in-memory fast-forward: {duration_ms:.3f}ms ({target})")

    print("[demo] 2) Resuming only the unfinished report node from WAL...")
    t0 = time.perf_counter()
    result = run_financial_analyst(
        ticker=ticker,
        wal_dir=wal_dir,
        kill_at=None,
        model=model,
        offline=offline,
        call_log_path=call_log_path,
    )
    overall_resume_ms = (time.perf_counter() - t0) * 1000

    print(f"[demo]    Overall resume (WAL load + graph + report): {overall_resume_ms:.2f}ms")
    print(f"[demo]    Report: {result['report']['summary']}")

    assert "report" in result, "Pipeline failed to produce final report upon resume"
    assert result["report"]["status"] == "COMPLETED"
    calls_after_resume = _summarise_call_log(call_log_path)
    assert calls_after_resume == calls_before_resume, {
        "before": calls_before_resume,
        "after": calls_after_resume,
    }
    print(
        "[demo]    After recovery:  "
        f"market fetch calls={calls_after_resume['market_fetch_calls']}, "
        f"LLM calls={calls_after_resume['llm_calls']}, "
        f"tokens={calls_after_resume['total_tokens']}"
    )

    print("[demo] 3) Validating a second fully cached run...")
    result2 = run_financial_analyst(
        ticker=ticker,
        wal_dir=wal_dir,
        kill_at=None,
        model=model,
        offline=offline,
        call_log_path=call_log_path,
    )
    assert result2 == result, "Resumed state mismatch across warm runs"
    calls_after_warm_run = _summarise_call_log(call_log_path)
    assert calls_after_warm_run == calls_before_resume
    print(
        "[demo] SUCCESS: zero re-fetching, 0 duplicate LLM calls, "
        f"0 duplicate tokens (saved {calls_before_resume['total_tokens']} tokens per replay)."
    )
    result["recovery_proof"] = {
        "calls_before_resume": calls_before_resume,
        "calls_after_resume": calls_after_resume,
        "calls_after_warm_run": calls_after_warm_run,
        "committed_before_resume": sorted(committed_before_resume),
        "in_memory_fast_forward_ms": fast_forward_ms,
        "overall_resume_ms": round(overall_resume_ms, 6),
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="LangGraph Financial Analyst Cookbook")
    parser.add_argument("--ticker", default="AAPL", help="Ticker symbol (default: AAPL)")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Provider-prefixed LLM model")
    parser.add_argument("--wal-dir", default=WAL_DIR_DEFAULT, help="WAL checkpoint directory")
    parser.add_argument(
        "--kill-at", type=int, choices=range(4), default=None, help="Send SIGKILL after durable step 0-3"
    )
    parser.add_argument("--demo", action="store_true", help="Run end-to-end SIGKILL recovery demonstration")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--offline", dest="offline", action="store_true", help="Use deterministic local adapters")
    mode.add_argument("--live", dest="offline", action="store_false", help="Use injected live adapters")
    parser.set_defaults(offline=True)
    args = parser.parse_args()

    if args.demo:
        demo_sigkill_recovery(
            wal_dir=args.wal_dir,
            ticker=args.ticker,
            model=args.model,
            offline=args.offline,
        )
    else:
        out = run_financial_analyst(
            ticker=args.ticker,
            wal_dir=args.wal_dir,
            kill_at=args.kill_at,
            model=args.model,
            offline=args.offline,
        )
        print(f"Completed: {out['report']['summary']}")


if __name__ == "__main__":
    main()

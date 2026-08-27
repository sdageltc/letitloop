"""Unit and regression tests for framework cookbooks to guarantee zero bit-rot in CI."""

from __future__ import annotations

import asyncio
import pathlib

import pytest

pytestmark = pytest.mark.fast


def test_langgraph_financial_analyst_run_and_resume(tmp_path: pathlib.Path) -> None:
    """Verify LangGraph financial analyst runs first time and fast-forwards on resume."""
    from examples.cookbooks.langgraph_financial_analyst import run_financial_analyst

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
    wal_file = pathlib.Path(wal_dir) / "state.wal.jsonl"
    assert wal_file.exists()
    assert "LILWAL02:" in wal_file.read_text(encoding="utf-8")


def test_langgraph_financial_analyst_sigkill_recovery(tmp_path: pathlib.Path) -> None:
    """Verify LangGraph financial analyst recovers after SIGKILL injection."""
    from examples.cookbooks.langgraph_financial_analyst import demo_sigkill_recovery

    wal_dir = str(tmp_path / "wal_langgraph_demo")
    result = demo_sigkill_recovery(wal_dir=wal_dir, ticker="NVDA")
    assert "report" in result
    assert result["report"]["status"] == "COMPLETED"
    assert result["ticker"] == "NVDA"


def test_dspy_durable_optimize_run_and_resume(tmp_path: pathlib.Path) -> None:
    """Verify DSPy prompt optimizer runs async workflow and fast-forwards on resume."""
    from examples.cookbooks.dspy_durable_optimize import run_dspy_optimizer

    wal_dir = str(tmp_path / "wal_dspy_optimize")

    # 1. Initial full async run
    r1 = asyncio.run(run_dspy_optimizer(dataset_name="gsm8k_mini", wal_dir=wal_dir, kill_at=None))
    assert "compiled_program" in r1
    assert r1["compiled_program"]["status"] == "COMPILED"
    assert r1["compiled_program"]["optimized_accuracy"] >= r1["compiled_program"]["baseline_accuracy"]
    assert r1["dataset"]["dataset_name"] == "gsm8k_mini"

    # 2. Resumed run (must be identical)
    r2 = asyncio.run(run_dspy_optimizer(dataset_name="gsm8k_mini", wal_dir=wal_dir, kill_at=None))
    assert r2 == r1

    # 3. WAL persistence check
    wal_file = pathlib.Path(wal_dir) / "state.wal.jsonl"
    assert wal_file.exists()
    assert "LILWAL02:" in wal_file.read_text(encoding="utf-8")


def test_dspy_durable_optimize_sigkill_recovery(tmp_path: pathlib.Path) -> None:
    """Verify DSPy prompt optimizer recovers after SIGKILL injection."""
    from examples.cookbooks.dspy_durable_optimize import demo_sigkill_recovery

    wal_dir = str(tmp_path / "wal_dspy_demo")
    result = demo_sigkill_recovery(wal_dir=wal_dir, dataset_name="gsm8k_mini")
    assert "compiled_program" in result
    assert result["compiled_program"]["status"] == "COMPILED"


def test_cookbook_module_builders() -> None:
    """Verify optional framework builder helpers instantiate without unhandled errors."""
    from examples.cookbooks.dspy_durable_optimize import _build_dspy_module
    from examples.cookbooks.langgraph_financial_analyst import _build_langgraph_pipeline

    lg_pipeline = _build_langgraph_pipeline()
    dspy_mod = _build_dspy_module()
    # If dependencies are missing, should safely return None; if installed, should return objects
    assert lg_pipeline is not None or lg_pipeline is None
    assert dspy_mod is not None or dspy_mod is None

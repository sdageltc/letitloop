"""Verify framework examples run and resume without error (TDD for Sprint 1)."""

import asyncio
import pathlib

import pytest

pytestmark = pytest.mark.fast


def test_langgraph_example_runs_and_resumes(tmp_path):
    """LangGraph durable pipeline: first run commits, second fast-forwards."""
    from examples.langgraph_durable_pipeline import run_pipeline

    wal_dir = str(tmp_path / "wal_langgraph")
    # first run
    result1 = asyncio.run(run_pipeline(wal_dir=wal_dir, kill_at=None))
    assert "finalized" in result1
    assert result1["finalized"].startswith("final-")
    # second run (resume) — must be identical for durable outputs (trace is ephemeral and not persisted)
    result2 = asyncio.run(run_pipeline(wal_dir=wal_dir, kill_at=None))
    assert result2["finalized"] == result1["finalized"]
    assert result2["fetched"] == result1["fetched"]
    assert result2["processed"] == result1["processed"]
    # WAL must be LILWAL02 framed
    wal_file = pathlib.Path(wal_dir) / "state.wal.jsonl"
    assert wal_file.exists()
    assert "LILWAL02:" in wal_file.read_text(encoding="utf-8")


def test_crewai_example_runs_and_resumes(tmp_path):
    """CrewAI durable tools: first run commits, second fast-forwards."""
    from examples.crewai_durable_tools import run_crew

    wal_dir = str(tmp_path / "wal_crewai")
    r1 = run_crew(topic="test-topic", wal_dir=wal_dir)
    assert r1["review"]["approved"] is True
    assert "research" in r1 and "draft" in r1
    # resume
    r2 = run_crew(topic="test-topic", wal_dir=wal_dir)
    assert r2 == r1
    wal_file = pathlib.Path(wal_dir) / "state.wal.jsonl"
    assert wal_file.exists()
    assert "LILWAL02:" in wal_file.read_text(encoding="utf-8")


def test_langgraph_demo_sigkill_recovery(tmp_path):
    """Demo helper uses real subprocess + SIGKILL and recovers."""
    from examples.langgraph_durable_pipeline import demo_sigkill_recovery

    wal_dir = str(tmp_path / "wal_demo")
    result = demo_sigkill_recovery(wal_dir=wal_dir)
    assert "finalized" in result


def test_crewai_demo_resumption(tmp_path):
    from examples.crewai_durable_tools import demo_step_resumption

    wal_dir = str(tmp_path / "wal_demo2")
    result = demo_step_resumption(wal_dir=wal_dir, topic="demo-topic")
    assert result["review"]["approved"] is True

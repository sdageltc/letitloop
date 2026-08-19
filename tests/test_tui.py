"""Unit tests for orchestrator.tui (Live Terminal Dashboard & DAG Matrix)."""

import json

from orchestrator.tui import TerminalDashboard


def test_terminal_dashboard_goal_summary():
    """Test goal and plan DAG summary rendering."""
    goal = {
        "goal_id": "goal_123",
        "title": "Build distributed cache",
        "status": "RUNNING",
    }
    plan = {
        "contracts": [
            {"task_id": "c1_cache_core", "objective": "Create cache dictionary", "risk_tier": "low"},
            {"task_id": "c2_eviction_lru", "objective": "Implement LRU eviction", "risk_tier": "standard"},
        ]
    }
    summary = TerminalDashboard.render_goal_summary(goal, plan)
    assert "goal_123" in summary
    assert "Build distributed cache" in summary
    assert "c1_cache_core" in summary
    assert "c2_eviction_lru" in summary


def test_terminal_dashboard_run_status(tmp_path):
    """Test live run state matrix rendering from disk states."""
    run_dir = tmp_path / "run_001"
    run_dir.mkdir()

    t1_dir = run_dir / "task_1"
    t1_dir.mkdir()
    (t1_dir / "state.json").write_text(
        json.dumps({"status": "VERIFIED", "attempt": 1, "events": ["created", "verified"]}),
        encoding="utf-8",
    )

    t2_dir = run_dir / "task_2"
    t2_dir.mkdir()
    (t2_dir / "state.json").write_text(
        json.dumps({"status": "RUNNING", "attempt": 2, "events": ["started"]}),
        encoding="utf-8",
    )

    status_out = TerminalDashboard.render_run_status(str(run_dir))
    assert "task_1" in status_out
    assert "[VERIFIED]" in status_out or "VERIFIED" in status_out
    assert "task_2" in status_out
    assert "[RUN]" in status_out or "RUNNING" in status_out

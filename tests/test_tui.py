"""Unit tests for orchestrator.tui (Live Terminal Dashboard & DAG Matrix)."""

import json
import sys
from io import StringIO

import pytest

from orchestrator.tui import (
    STATE_COLORS,
    LiveDashboard,
    TerminalDashboard,
    render_budget_gauge,
    render_dag_tree,
)


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


# ---------------------------------------------------------------------------
# STATE_COLORS coverage
# ---------------------------------------------------------------------------


@pytest.mark.fast
def test_state_colors_covers_core_and_aliases():
    """STATE_COLORS must cover canonical states plus QC/retry aliases."""
    required = [
        "DRAFTED",
        "READY",
        "WORKING",
        "WORK",
        "VERIFYING",
        "QC_REVIEW",
        "QC_PENDING",
        "QC_PASS",
        "QC_FAILED",
        "QC_PASSED",
        "COMPLETE",
        "FAILED",
        "ESCALATED",
        "RETRY",
        "RETRY_PENDING",
    ]
    for status in required:
        assert status in STATE_COLORS, f"missing STATE_COLORS entry for {status}"
        assert STATE_COLORS[status].startswith("\x1b["), f"{status} code is not ANSI"


@pytest.mark.fast
def test_colorize_status_respects_enabled_flag():
    from orchestrator.tui import colorize_status

    colored = colorize_status("READY", "READY")
    assert colored.startswith("\x1b[")
    assert colored.endswith("\x1b[0m")
    plain = colorize_status("READY", "READY", enabled=False)
    assert plain == "READY"
    unknown = colorize_status("TOTALLY_UNKNOWN", "X")
    assert unknown == "X"


# ---------------------------------------------------------------------------
# Budget gauge math
# ---------------------------------------------------------------------------


@pytest.mark.fast
def test_render_budget_gauge_math():
    assert render_budget_gauge(0, 100, width=10) == "[----------] 0/100 (0%)"
    assert render_budget_gauge(50, 100, width=10) == "[#####-----] 50/100 (50%)"
    assert render_budget_gauge(100, 100, width=10) == "[##########] 100/100 (100%)"


@pytest.mark.fast
def test_render_budget_gauge_empty_max():
    """Falsy max yields graceful '-' instead of dividing by zero."""
    assert render_budget_gauge(10, 0) == "-"
    assert render_budget_gauge(10, None) == "-"


@pytest.mark.fast
def test_render_budget_gauge_clamps_overuse():
    out = render_budget_gauge(250, 100, width=10)
    assert "(100%)" in out
    assert "#" * 10 in out


# ---------------------------------------------------------------------------
# DAG tree rendering
# ---------------------------------------------------------------------------


@pytest.mark.fast
def test_render_dag_tree_linear_chain():
    plan = {
        "contracts": [
            {"task_id": "a", "depends_on": []},
            {"task_id": "b", "depends_on": ["a"]},
            {"task_id": "c", "depends_on": ["b"]},
        ]
    }
    lines = render_dag_tree(plan)
    joined = "\n".join(lines)
    assert "a" in joined and "b" in joined and "c" in joined
    assert lines[0].strip() == "a"
    idx_a = joined.index("a")
    idx_b = joined.index("b")
    idx_c = joined.index("c")
    # children are progressively more indented than their dependencies
    assert idx_b > idx_a
    assert idx_c > idx_b
    # unicode box-drawing connectors by default
    assert any("\u251c\u2500\u2500" in l or "\u2514\u2500\u2500" in l for l in lines)


@pytest.mark.fast
def test_render_dag_tree_diamond():
    plan = {
        "contracts": [
            {"task_id": "a", "depends_on": []},
            {"task_id": "b", "depends_on": ["a"]},
            {"task_id": "c", "depends_on": ["a"]},
            {"task_id": "d", "depends_on": ["b", "c"]},
        ]
    }
    lines = render_dag_tree(plan)
    joined = "\n".join(lines)
    # every node present
    for tid in ("a", "b", "c", "d"):
        assert tid in joined
    # diamond join node rendered exactly once (cycle/dup safe)
    d_lines = [l for l in lines if l.strip().endswith("d")]
    assert len(d_lines) == 1
    # d is dependency-indented under its parent branch
    assert d_lines[0].index("d") > lines[0].index("a")


@pytest.mark.fast
def test_render_dag_tree_ascii_fallback():
    plan = {
        "contracts": [
            {"task_id": "root", "depends_on": []},
            {"task_id": "kid1", "depends_on": ["root"]},
            {"task_id": "kid2", "depends_on": ["root"]},
        ]
    }
    lines = render_dag_tree(plan, ascii_mode=True)
    joined = "\n".join(lines)
    assert "|--" in joined
    assert "`--" in joined
    assert "\u251c" not in joined
    assert "\u2514" not in joined


# ---------------------------------------------------------------------------
# LiveDashboard
# ---------------------------------------------------------------------------


def _make_run_dir(tmp_path, name="run_live"):
    """Build a minimal goal/plan/state/metrics/usage fixture on disk."""
    run_dir = tmp_path / name
    run_dir.mkdir()
    (run_dir / "goal.json").write_text(
        json.dumps({"goal_id": "g_live", "title": "Live Goal", "status": "EXECUTING"}),
        encoding="utf-8",
    )
    (run_dir / "plan.json").write_text(
        json.dumps(
            {
                "contracts": [
                    {"task_id": "t1", "depends_on": []},
                    {"task_id": "t2", "depends_on": ["t1"]},
                ]
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "t1").mkdir()
    (run_dir / "t1" / "state.json").write_text(
        json.dumps({"status": "COMPLETE", "attempt": 1}),
        encoding="utf-8",
    )
    (run_dir / "t2").mkdir()
    (run_dir / "t2" / "state.json").write_text(
        json.dumps({"status": "WORKING", "attempt": 2}),
        encoding="utf-8",
    )
    (run_dir / "metrics.json").write_text(
        json.dumps(
            {
                "goal_id": "g_live",
                "phase_elapsed": {"work": 1.5, "verify": 0.5},
                "phase_counts": {"work": 2, "verify": 2},
                "attempt_counts": {"t2": 2},
                "total_attempts": 2,
                "total_elapsed_sec": 2.0,
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "usage.json").write_text(
        json.dumps({"total_tokens": 500, "max_tokens": 1000}),
        encoding="utf-8",
    )
    return str(run_dir)


@pytest.mark.fast
def test_live_dashboard_frame_contains_task_ids(tmp_path):
    out = StringIO()
    dash = LiveDashboard(_make_run_dir(tmp_path), interval=0.01, out=out)
    dash.run(once=True)
    text = out.getvalue()
    assert "t1" in text
    assert "t2" in text
    assert "g_live" in text
    assert "Live Goal" in text
    # budget gauge discovered from usage.json artifact
    assert "500/1000" in text
    assert "(50%)" in text
    # lease/attempt counts footer
    assert "Attempts: 3" in text
    assert "Active leases:" in text


@pytest.mark.fast
def test_live_dashboard_once_renders_exactly_one_frame(tmp_path):
    out = StringIO()
    dash = LiveDashboard(_make_run_dir(tmp_path), interval=0.01, out=out)
    dash.run(once=True)
    assert out.getvalue().count("letitloop LIVE") == 1


@pytest.mark.fast
def test_live_dashboard_key_reader_q_stops_loop(tmp_path):
    out = StringIO()
    presses = iter([None, "q"])

    def key_reader():
        return next(presses)

    dash = LiveDashboard(_make_run_dir(tmp_path), interval=0.01, out=out)
    dash.run(key_reader=key_reader)
    assert out.getvalue().count("letitloop LIVE") == 2


@pytest.mark.fast
def test_live_dashboard_tab_switches_pane(tmp_path):
    out = StringIO()
    presses = iter(["\t", "q"])

    def key_reader():
        return next(presses)

    dash = LiveDashboard(_make_run_dir(tmp_path), interval=0.01, out=out)
    dash.run(key_reader=key_reader)
    text = out.getvalue()
    # first frame: dag pane active
    assert text.count("pane: dag") >= 1
    assert "Task DAG:" in text
    # second frame: metrics pane active with phase timings + attempt counts
    assert "pane: metrics" in text
    assert "Phase Timings:" in text
    assert "work" in text
    assert "total_attempts: 2" in text


@pytest.mark.fast
def test_live_dashboard_no_color_suppresses_escapes(tmp_path, monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    out = StringIO()
    dash = LiveDashboard(_make_run_dir(tmp_path), interval=0.01, out=out)
    data = dash.collect()
    frame = dash.render_frame(data)
    assert "\x1b[" not in frame
    out2 = StringIO()
    LiveDashboard(_make_run_dir(tmp_path, name="run_live2"), interval=0.01, out=out2).run(once=True)
    assert "\x1b[" not in out2.getvalue()


@pytest.mark.fast
def test_live_dashboard_colors_emitted_without_no_color(tmp_path, monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    out = StringIO()
    dash = LiveDashboard(_make_run_dir(tmp_path), interval=0.01, out=out)
    frame = dash.render_frame(dash.collect())
    assert "\x1b[" in frame


@pytest.mark.fast
def test_live_dashboard_missing_artifacts_degrades_gracefully(tmp_path):
    run_dir = tmp_path / "empty_run"
    run_dir.mkdir()
    out = StringIO()
    dash = LiveDashboard(str(run_dir), interval=0.01, out=out)
    dash.run(once=True)
    text = out.getvalue()
    # no usage artifact -> no gauge crash, no token numbers
    assert "Budget:" not in text
    # empty plan renders sentinel instead of raising
    assert "(empty plan)" in text


@pytest.mark.fast
def test_cli_parser_accepts_dashboard_flags(monkeypatch):
    import orchestrator.cli as cli_mod

    captured = {}

    def fake_cmd(args):
        captured["live"] = args.live
        captured["interval"] = args.interval
        captured["once"] = args.once

    monkeypatch.setattr(cli_mod, "cmd_dashboard", fake_cmd)
    monkeypatch.setattr(
        sys,
        "argv",
        ["letitloop", "--run-dir", "unused", "dashboard", "--live", "--interval", "0.5", "--once"],
    )
    cli_mod.main()
    assert captured == {"live": True, "interval": 0.5, "once": True}


@pytest.mark.fast
def test_cli_parser_dashboard_defaults_match_legacy_behavior(monkeypatch):
    import orchestrator.cli as cli_mod

    captured = {}

    def fake_cmd(args):
        captured["live"] = args.live
        captured["interval"] = args.interval
        captured["once"] = args.once

    monkeypatch.setattr(cli_mod, "cmd_dashboard", fake_cmd)
    monkeypatch.setattr(sys, "argv", ["letitloop", "--run-dir", "unused", "dashboard"])
    cli_mod.main()
    assert captured == {"live": False, "interval": 2.0, "once": False}

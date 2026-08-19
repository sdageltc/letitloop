"""Unit tests for orchestrator/metrics.py module."""

import time

import pytest

from orchestrator.metrics import MetricsCollector, PhaseRecord


def test_start_end_phase():
    """Test start and end of a phase records elapsed_sec > 0."""
    mc = MetricsCollector("g1")
    mc.start_phase("compile", "t1")
    time.sleep(0.01)
    rec = mc.end_phase("compile", "t1")

    assert isinstance(rec, PhaseRecord)
    assert rec.phase == "compile"
    assert rec.task_id == "t1"
    assert rec.elapsed_sec > 0


def test_multiple_phases():
    """Test recording 3 phases results in correct phase count in snapshot."""
    mc = MetricsCollector("g1")
    mc.start_phase("p1")
    mc.end_phase("p1")
    mc.start_phase("p2")
    mc.end_phase("p2")
    mc.start_phase("p3")
    mc.end_phase("p3")

    snap = mc.snapshot()
    assert len(mc.phases) == 3
    assert len(snap.phase_counts) == 3
    assert snap.phase_counts["p1"] == 1
    assert snap.phase_counts["p2"] == 1
    assert snap.phase_counts["p3"] == 1


def test_record_attempt():
    """Test record_attempt('t1') increments attempt count to 1."""
    mc = MetricsCollector("g1")
    mc.record_attempt("t1")

    snap = mc.snapshot()
    assert snap.attempt_counts.get("t1") == 1
    assert snap.total_attempts == 1


def test_snapshot_total_elapsed():
    """Test snapshot total_elapsed_sec sums elapsed time across multiple phases."""
    mc = MetricsCollector("g1")
    mc.start_phase("p1")
    time.sleep(0.01)
    rec1 = mc.end_phase("p1")

    mc.start_phase("p2")
    time.sleep(0.01)
    rec2 = mc.end_phase("p2")

    snap = mc.snapshot()
    expected_total = rec1.elapsed_sec + rec2.elapsed_sec
    assert pytest.approx(snap.total_elapsed_sec, abs=1e-4) == expected_total


def test_snapshot_goal_id():
    """Test goal_id is preserved in snapshot."""
    mc = MetricsCollector(goal_id="goal-12345")
    snap = mc.snapshot()

    assert snap.goal_id == "goal-12345"


def test_save_and_load(tmp_path):
    """Test saving MetricsCollector to JSON file and loading it back."""
    mc = MetricsCollector("g1")
    mc.start_phase("build", "t1")
    time.sleep(0.01)
    rec = mc.end_phase("build", "t1")
    mc.record_attempt("t1")

    json_file = str(tmp_path / "metrics.json")
    mc.save(json_file)

    loaded_mc = MetricsCollector.load(json_file)
    assert loaded_mc.goal_id == mc.goal_id
    assert len(loaded_mc.phases) == 1
    assert loaded_mc.phases[0].phase == rec.phase
    assert loaded_mc.phases[0].task_id == rec.task_id
    assert loaded_mc.phases[0].elapsed_sec == rec.elapsed_sec
    assert loaded_mc.attempts == mc.attempts


def test_summary_contains_phases():
    """Test summary() output includes recorded phase names and goal_id."""
    mc = MetricsCollector("g1")
    mc.start_phase("lint")
    mc.end_phase("lint")
    mc.start_phase("test")
    mc.end_phase("test")

    summ = mc.summary()
    assert "Metrics: goal=g1" in summ
    assert "lint" in summ
    assert "test" in summ


def test_to_dict_structure():
    """Test to_dict() returns a dict with all expected keys and structure."""
    mc = MetricsCollector("g1")
    mc.start_phase("p1")
    mc.end_phase("p1")
    mc.record_attempt("t1")

    d = mc.to_dict()
    assert isinstance(d, dict)
    expected_keys = {
        "goal_id",
        "total_elapsed_sec",
        "phase_elapsed",
        "phase_counts",
        "attempt_counts",
        "total_attempts",
        "phases",
    }
    assert expected_keys.issubset(d.keys())
    assert d["goal_id"] == "g1"
    assert d["total_attempts"] == 1
    assert len(d["phases"]) == 1


def test_no_phases():
    """Test an empty collector snapshot has 0 total elapsed and empty counts."""
    mc = MetricsCollector("g1")
    snap = mc.snapshot()

    assert snap.total_elapsed_sec == 0.0
    assert snap.phase_counts == {}
    assert snap.phase_elapsed == {}
    assert snap.total_attempts == 0


def test_phase_counts():
    """Test recording the same phase twice results in count = 2."""
    mc = MetricsCollector("g1")
    mc.start_phase("test")
    mc.end_phase("test")
    mc.start_phase("test")
    mc.end_phase("test")

    snap = mc.snapshot()
    assert snap.phase_counts["test"] == 2

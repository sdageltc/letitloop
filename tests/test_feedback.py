"""Tests for feedback loop module."""

import os
import json
import pytest
from orchestrator.state import create_initial_state, save_state
from orchestrator.failure import FAILURE_CLASS_TIMEOUT, FAILURE_CLASS_VERIFIER_CONTENT_MISMATCH
from orchestrator.feedback import (
    FeedbackRecord,
    collect_feedback,
    store_feedback,
    load_feedback,
    detect_patterns,
    feedback_for_replan,
    format_feedback,
)


def test_feedback_record_creation():
    rec = FeedbackRecord(
        task_id="t1",
        goal_id="g1",
        failure_class="timeout",
        error_code="E006",
        stderr_snippet="timed out after 30s",
        approach="retry with longer timeout",
        attempt=2,
        status="VERIFICATION_FAILED",
    )
    assert rec.task_id == "t1"
    assert rec.goal_id == "g1"
    assert rec.failure_class == "timeout"
    assert rec.error_code == "E006"
    assert rec.attempt == 2


def test_feedback_record_roundtrip():
    rec = FeedbackRecord(
        task_id="t1",
        goal_id="g1",
        failure_class="timeout",
        error_code="E006",
        stderr_snippet="timeout!",
        attempt=1,
        status="VERIFICATION_FAILED",
    )
    d = rec.to_dict()
    rec2 = FeedbackRecord.from_dict(d)
    assert rec2.task_id == rec.task_id
    assert rec2.goal_id == rec.goal_id
    assert rec2.failure_class == rec.failure_class
    assert rec2.error_code == rec.error_code
    assert rec2.attempt == rec.attempt


def test_collect_feedback_from_verification_failed(tmp_path):
    state = create_initial_state("t1")
    state.status = "VERIFICATION_FAILED"
    state.data["last_failure_class"] = "verifier_content_mismatch"
    state.worker_results.append({
        "exit_code": 0,
        "stdout": "",
        "stderr": "verification failed: expected '42', got 'wrong'",
        "elapsed_sec": 5.0,
    })
    rec = collect_feedback("t1", "g1", state)
    assert rec is not None
    assert rec.task_id == "t1"
    assert rec.goal_id == "g1"
    assert rec.failure_class == "verifier_content_mismatch"
    assert rec.error_code == "E009"  # verifier content mismatch
    assert rec.attempt == 1  # default attempt is 1


def test_collect_feedback_skips_complete(tmp_path):
    state = create_initial_state("t1")
    state.status = "COMPLETE"
    rec = collect_feedback("t1", "g1", state)
    assert rec is None


def test_collect_feedback_from_escalated(tmp_path):
    state = create_initial_state("t1")
    state.status = "ESCALATED"
    state.data["last_failure_class"] = FAILURE_CLASS_TIMEOUT
    rec = collect_feedback("t1", "g1", state)
    assert rec is not None
    assert rec.failure_class == FAILURE_CLASS_TIMEOUT
    assert rec.error_code == "E006"


def test_store_and_load_feedback(tmp_path):
    run_dir = str(tmp_path)
    records = [
        FeedbackRecord("t1", "g1", "timeout", "E006", attempt=1, status="VERIFICATION_FAILED"),
        FeedbackRecord("t2", "g1", "scope_violation", "E010", attempt=1, status="BLOCKED"),
    ]
    store_feedback("g1", run_dir, records)

    loaded = load_feedback("g1", run_dir)
    assert len(loaded) == 2
    assert loaded[0].task_id == "t1"
    assert loaded[1].task_id == "t2"


def test_store_feedback_deduplicates(tmp_path):
    run_dir = str(tmp_path)
    r1 = FeedbackRecord("t1", "g1", "timeout", "E006", attempt=1, status="VERIFICATION_FAILED")
    store_feedback("g1", run_dir, [r1])
    store_feedback("g1", run_dir, [r1])  # same (task_id, attempt)
    loaded = load_feedback("g1", run_dir)
    assert len(loaded) == 1


def test_store_feedback_appends_new(tmp_path):
    run_dir = str(tmp_path)
    r1 = FeedbackRecord("t1", "g1", "timeout", "E006", attempt=1, status="VERIFICATION_FAILED")
    r2 = FeedbackRecord("t1", "g1", "timeout", "E006", attempt=2, status="VERIFICATION_FAILED")
    store_feedback("g1", run_dir, [r1])
    store_feedback("g1", run_dir, [r2])
    loaded = load_feedback("g1", run_dir)
    assert len(loaded) == 2


def test_detect_repeated_pattern(tmp_path):
    records = [
        FeedbackRecord("t1", "g1", "timeout", "E006", attempt=1, status="VERIFICATION_FAILED"),
        FeedbackRecord("t1", "g1", "timeout", "E006", attempt=2, status="VERIFICATION_FAILED"),
        FeedbackRecord("t2", "g1", "scope_violation", "E010", attempt=1, status="BLOCKED"),
    ]
    patterns = detect_patterns(records)
    t1_patterns = [p for p in patterns if p["task_id"] == "t1"]
    assert len(t1_patterns) >= 1
    assert t1_patterns[0]["pattern"] == "repeated_same_class"
    assert t1_patterns[0]["failure_class"] == "timeout"


def test_detect_pervasive_pattern(tmp_path):
    records = [
        FeedbackRecord("t1", "g1", "timeout", "E006", attempt=1),
        FeedbackRecord("t2", "g1", "timeout", "E006", attempt=1),
        FeedbackRecord("t3", "g1", "timeout", "E006", attempt=1),
    ]
    patterns = detect_patterns(records)
    pervasive = [p for p in patterns if p["pattern"] == "pervasive_failure_class"]
    assert len(pervasive) >= 1
    assert pervasive[0]["failure_class"] == "timeout"
    assert pervasive[0]["count"] == 3


def test_feedback_for_replan_includes_history(tmp_path):
    run_dir = str(tmp_path)
    goal_id = "g_test"
    os.makedirs(os.path.join(run_dir, goal_id, "feedback"), exist_ok=True)
    records = [
        FeedbackRecord("t1", goal_id, "timeout", "E006", attempt=1, status="VERIFICATION_FAILED"),
    ]
    store_feedback(goal_id, run_dir, records)
    result = feedback_for_replan(goal_id, run_dir)
    assert "Previous failures" in result
    assert "timeout" in result
    assert goal_id in result


def test_feedback_for_replan_empty(tmp_path):
    run_dir = str(tmp_path)
    result = feedback_for_replan("nonexistent", run_dir)
    assert result == ""


def test_format_feedback_with_records(tmp_path):
    records = [
        FeedbackRecord("t1", "g1", "timeout", "E006", stderr_snippet="worker timed out", attempt=1, status="VERIFICATION_FAILED"),
    ]
    result = format_feedback(records)
    assert "t1" in result
    assert "timeout" in result
    assert "E006" in result


def test_format_feedback_empty(tmp_path):
    result = format_feedback([])
    assert "No feedback records" in result

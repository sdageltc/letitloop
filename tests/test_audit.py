"""Unit tests for orchestrator/audit.py module."""

import os

from orchestrator.audit import (
    format_audit_entries,
    load_audit_log,
    query_audit,
    record_action,
)


def test_record_action(tmp_path):
    """Test recording an action creates a file with expected action_type."""
    run_dir = str(tmp_path / "run")
    record_action(run_dir, "pause", goal_id="g1")

    log_file = os.path.join(run_dir, "audit.jsonl")
    assert os.path.exists(log_file)

    entries = load_audit_log(run_dir)
    assert len(entries) == 1
    assert entries[0]["action_type"] == "pause"
    assert entries[0]["goal_id"] == "g1"


def test_load_multiple(tmp_path):
    """Test recording 3 actions and loading returns all 3."""
    run_dir = str(tmp_path / "run")
    record_action(run_dir, "start", goal_id="g1")
    record_action(run_dir, "pause", goal_id="g1")
    record_action(run_dir, "resume", goal_id="g1")

    entries = load_audit_log(run_dir)
    assert len(entries) == 3
    assert [e["action_type"] for e in entries] == ["start", "pause", "resume"]


def test_load_empty(tmp_path):
    """Test loading from an empty existing directory returns empty list."""
    run_dir = str(tmp_path / "empty_run")
    os.makedirs(run_dir, exist_ok=True)

    entries = load_audit_log(run_dir)
    assert entries == []


def test_load_nonexistent_dir(tmp_path):
    """Test loading from a nonexistent directory returns empty list."""
    run_dir = str(tmp_path / "nonexistent_dir")

    entries = load_audit_log(run_dir)
    assert entries == []


def test_query_by_goal_id(tmp_path):
    """Test filtering audit log by goal_id."""
    run_dir = str(tmp_path / "run")
    record_action(run_dir, "start", goal_id="g1")
    record_action(run_dir, "start", goal_id="g2")
    record_action(run_dir, "pause", goal_id="g1")

    g1_entries = query_audit(run_dir, goal_id="g1")
    assert len(g1_entries) == 2
    assert all(e["goal_id"] == "g1" for e in g1_entries)


def test_query_by_action_type(tmp_path):
    """Test filtering audit log by action_type."""
    run_dir = str(tmp_path / "run")
    record_action(run_dir, "pause", goal_id="g1")
    record_action(run_dir, "cancel", goal_id="g1")
    record_action(run_dir, "cancel", goal_id="g2")

    cancel_entries = query_audit(run_dir, action_type="cancel")
    assert len(cancel_entries) == 2
    assert all(e["action_type"] == "cancel" for e in cancel_entries)


def test_query_by_task_id(tmp_path):
    """Test filtering audit log by task_id."""
    run_dir = str(tmp_path / "run")
    record_action(run_dir, "execute", task_id="t1")
    record_action(run_dir, "execute", task_id="t2")
    record_action(run_dir, "retry", task_id="t1")

    t1_entries = query_audit(run_dir, task_id="t1")
    assert len(t1_entries) == 2
    assert all(e["task_id"] == "t1" for e in t1_entries)


def test_query_combined(tmp_path):
    """Test filtering audit log by both goal_id AND action_type."""
    run_dir = str(tmp_path / "run")
    record_action(run_dir, "pause", goal_id="g1")
    record_action(run_dir, "cancel", goal_id="g1")
    record_action(run_dir, "cancel", goal_id="g2")

    results = query_audit(run_dir, goal_id="g1", action_type="cancel")
    assert len(results) == 1
    assert results[0]["goal_id"] == "g1"
    assert results[0]["action_type"] == "cancel"


def test_format_empty():
    """Test format_audit_entries with an empty list returns 'No audit entries.'."""
    formatted = format_audit_entries([])
    assert formatted == "No audit entries."


def test_format_with_entries(tmp_path):
    """Test format_audit_entries produces readable output with entry details."""
    run_dir = str(tmp_path / "run")
    record_action(
        run_dir,
        "cancel",
        goal_id="g1",
        task_id="t1",
        details={"reason": "user_request"},
    )

    entries = load_audit_log(run_dir)
    formatted = format_audit_entries(entries)

    assert "Audit log: 1 entries" in formatted
    assert "cancel" in formatted
    assert "goal=g1" in formatted
    assert "task=t1" in formatted
    assert "reason" in formatted


def test_record_with_details(tmp_path):
    """Test recording an action preserves the details dict."""
    run_dir = str(tmp_path / "run")
    details = {"user": "operator_1", "step": 3}
    record_action(run_dir, "pause", details=details)

    entries = load_audit_log(run_dir)
    assert len(entries) == 1
    assert entries[0]["details"] == details


def test_record_makes_dir(tmp_path):
    """Test record_action creates the run_dir directory if it does not exist."""
    nested_dir = str(tmp_path / "nested" / "sub" / "run_dir")
    assert not os.path.exists(nested_dir)

    record_action(nested_dir, "start")

    assert os.path.exists(nested_dir)
    assert os.path.exists(os.path.join(nested_dir, "audit.jsonl"))

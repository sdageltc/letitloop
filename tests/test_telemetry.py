"""Tests for telemetry collector."""

import os
import pytest
from orchestrator.telemetry import record_event, load_events, summarize


def test_record_and_load(tmp_path):
    run_dir = str(tmp_path)
    record_event(run_dir, "contract_started", task_id="t1", goal_id="g1",
                 payload={"model": "test"})
    events = load_events(run_dir)
    assert len(events) == 1
    assert events[0]["event_type"] == "contract_started"
    assert events[0]["task_id"] == "t1"
    assert events[0]["payload"]["model"] == "test"


def test_multiple_events(tmp_path):
    run_dir = str(tmp_path)
    record_event(run_dir, "start", task_id="t1")
    record_event(run_dir, "complete", task_id="t1")
    events = load_events(run_dir)
    assert len(events) == 2


def test_load_empty_dir(tmp_path):
    events = load_events(str(tmp_path))
    assert events == []


def test_load_nonexistent_path(tmp_path):
    events = load_events(os.path.join(str(tmp_path), "nonexistent"))
    assert events == []


def test_summarize_empty():
    result = summarize([])
    assert "No telemetry events" in result


def test_summarize_with_events(tmp_path):
    run_dir = str(tmp_path)
    record_event(run_dir, "contract_started", goal_id="g1")
    record_event(run_dir, "contract_completed", goal_id="g1")
    record_event(run_dir, "contract_started", goal_id="g2")
    events = load_events(run_dir)
    result = summarize(events)
    assert "3 events" in result
    assert "contract_started: 2" in result
    assert "contract_completed: 1" in result

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from examples.interactive_demo import worker


def _read_events(output: str) -> list[dict]:
    events = []
    for line in output.splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and "event" in value:
            events.append(value)
    return events


def test_durable_demo_fast_forwards_completed_steps_without_reexecution(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(worker, "STEP_DELAY_SECONDS", 0.0)

    first_result = worker.run_workflow(tmp_path)
    first_events = _read_events(capsys.readouterr().out)
    executed = [event for event in first_events if event.get("event") == "step_work_finished"]
    first_completions = [event for event in first_events if event.get("event") == "step_completed"]

    assert len(first_result) == len(worker.WORKFLOW_STEPS) == 5
    assert len(executed) == 5
    assert len(first_completions) == 5
    assert all(not event["cached"] for event in first_completions)

    resumed_result = worker.run_workflow(tmp_path)
    resumed_events = _read_events(capsys.readouterr().out)
    resumed_completions = [event for event in resumed_events if event.get("event") == "step_completed"]

    assert resumed_result == first_result
    assert len(resumed_completions) == 5
    assert all(event["cached"] for event in resumed_completions)
    assert not [event for event in resumed_events if event.get("event") == "step_started"]
    assert not [event for event in resumed_events if event.get("event") == "step_work_finished"]


def test_wal_viewer_reads_framed_wal_records(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(worker, "STEP_DELAY_SECONDS", 0.0)
    worker.run_workflow(tmp_path)
    capsys.readouterr()

    records = worker.read_wal_records(tmp_path / "state.wal.jsonl")

    assert records
    assert any(record.get("event_type") == "INIT" for record in records)
    assert any("step_outputs" in json.dumps(record) for record in records)


def test_demo_rejects_invalid_crash_step(tmp_path):
    with pytest.raises(ValueError, match="crash_at_index must be between 1 and 5"):
        worker.run_workflow(tmp_path, crash_at_index=6)


def test_sigkill_resumes_real_worker_from_wal(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(worker, "STEP_DELAY_SECONDS", 0.0)
    worker_path = Path(worker.__file__).resolve()
    root = str(worker.ROOT)
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONPATH"] = root + (os.pathsep + env.get("PYTHONPATH", "") if env.get("PYTHONPATH") else "")
    process = subprocess.Popen(
        [sys.executable, str(worker_path), "--wal-dir", str(tmp_path), "--crash-at", "2"],
        cwd=root,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    output_queue = queue.Queue()

    def read_output():
        assert process.stdout is not None
        for line in process.stdout:
            output_queue.put(line)
        output_queue.put(None)

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()
    child_events = []
    ready_for_crash = False
    deadline = time.monotonic() + 10.0

    try:
        while time.monotonic() < deadline:
            try:
                line = output_queue.get(timeout=0.1)
            except queue.Empty:
                if process.poll() is not None:
                    break
                continue
            if line is None:
                break
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            child_events.append(event)
            if event.get("event") == "ready_to_crash":
                ready_for_crash = True
                break

        assert ready_for_crash, "worker did not reach the selected crash point"
        process.kill()
        process.wait(timeout=5.0)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5.0)
        reader.join(timeout=2.0)

    first_attempt_steps = [
        event for event in child_events if event.get("event") == "step_completed"
    ]
    assert [event["step_id"] for event in first_attempt_steps] == ["discover_sources"]
    capsys.readouterr()

    resumed_result = worker.run_workflow(tmp_path)
    resumed_events = _read_events(capsys.readouterr().out)
    resumed_steps = [
        event for event in resumed_events if event.get("event") == "step_completed"
    ]

    assert len(resumed_result) == 5
    assert len(resumed_steps) == 5
    assert resumed_steps[0]["cached"] is True
    assert all(event["cached"] is False for event in resumed_steps[1:])
    assert [
        event["step_id"]
        for event in resumed_events
        if event.get("event") == "step_work_finished"
    ] == ["normalize_inputs", "draft_artifact", "validate_result", "publish_result"]

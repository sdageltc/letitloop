"""Unit tests for worker adapter framework."""

import sys
import tempfile

from orchestrator.worker_adapters import (
    MockWorkerAdapter,
    ScriptWorkerAdapter,
    WorkerRegistry,
)


def test_mock_worker_adapter():
    adapter = MockWorkerAdapter()
    resp = adapter.execute("Test prompt", "/tmp", "task_01")
    assert resp["exit_code"] == 0
    assert "Mock execution succeeded" in resp["stdout"]
    assert len(adapter.call_history) == 1


def test_script_worker_adapter():
    with tempfile.TemporaryDirectory() as td:
        adapter = ScriptWorkerAdapter(f'"{sys.executable}" -c "print(\'Hello from script\')"')
        resp = adapter.execute("prompt", td, "task_02")
        assert resp["exit_code"] == 0
        assert "Hello from script" in resp["stdout"]


def test_worker_registry():
    assert "mock" in WorkerRegistry.list_available()
    custom = MockWorkerAdapter("custom_mock")
    WorkerRegistry.register("custom", custom)
    assert WorkerRegistry.get("custom") == custom

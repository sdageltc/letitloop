"""Unit tests for worker adapter framework."""

import sys
import tempfile
from unittest.mock import patch

from orchestrator.worker_adapters import (
    AntigravityCliWorkerAdapter,
    MockWorkerAdapter,
    OmnirouteWorkerAdapter,
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


def test_antigravity_cli_worker_adapter():
    with tempfile.TemporaryDirectory() as td:
        adapter = AntigravityCliWorkerAdapter(config={"binary": sys.executable})
        # Executing python with agy args will return nonzero without agy, handled gracefully
        resp = adapter.execute("do work", td, "task_03")
        assert "exit_code" in resp
        assert "approach" in resp


def test_omniroute_worker_adapter():
    adapter = OmnirouteWorkerAdapter(config={"model": "omniroute:mock"})
    with patch("orchestrator.llm.call_llm", return_value="Generated code response"):
        resp = adapter.execute("Generate code", "/tmp", "task_04")
        assert resp["exit_code"] == 0
        assert resp["stdout"] == "Generated code response"
        assert resp["approach"] == "omniroute_gateway"


def test_worker_registry():
    assert "mock" in WorkerRegistry.list_available()
    assert "antigravity-cli" in WorkerRegistry.list_available()
    assert "omniroute" in WorkerRegistry.list_available()
    custom = MockWorkerAdapter("custom_mock")
    WorkerRegistry.register("custom", custom)
    assert WorkerRegistry.get("custom") == custom

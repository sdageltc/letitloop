"""Unit tests for worker adapter framework."""

import sys
import tempfile
from unittest.mock import patch

from orchestrator.worker_adapters import (
    AiderWorkerAdapter,
    AntigravityCliWorkerAdapter,
    ClineWorkerAdapter,
    CodexWorkerAdapter,
    HermesWorkerAdapter,
    MockWorkerAdapter,
    OmnirouteWorkerAdapter,
    OpenCodeWorkerAdapter,
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
        resp = adapter.execute("do work", td, "task_03")
        assert "exit_code" in resp
        assert "approach" in resp


def test_opencode_worker_adapter():
    with tempfile.TemporaryDirectory() as td:
        adapter = OpenCodeWorkerAdapter(config={"binary": sys.executable})
        resp = adapter.execute("do work", td, "task_04")
        assert "exit_code" in resp
        assert "approach" in resp


def test_hermes_worker_adapter():
    with tempfile.TemporaryDirectory() as td:
        adapter = HermesWorkerAdapter(config={"binary": sys.executable})
        resp = adapter.execute("do work", td, "task_05")
        assert "exit_code" in resp
        assert "approach" in resp


def test_cline_worker_adapter():
    with tempfile.TemporaryDirectory() as td:
        adapter = ClineWorkerAdapter(config={"binary": sys.executable})
        resp = adapter.execute("do work", td, "task_06")
        assert "exit_code" in resp
        assert "approach" in resp


def test_aider_worker_adapter():
    with tempfile.TemporaryDirectory() as td:
        adapter = AiderWorkerAdapter(config={"binary": sys.executable})
        resp = adapter.execute("do work", td, "task_07")
        assert "exit_code" in resp
        assert "approach" in resp


def test_codex_worker_adapter():
    with tempfile.TemporaryDirectory() as td:
        adapter = CodexWorkerAdapter(config={"binary": sys.executable})
        resp = adapter.execute("do work", td, "task_codex")
        assert "exit_code" in resp
        assert "approach" in resp
        assert resp["approach"] == "codex_cli_exec"


def test_omniroute_worker_adapter():
    adapter = OmnirouteWorkerAdapter(config={"model": "omniroute:mock"})
    with patch("orchestrator.llm.call_llm", return_value="Generated code response"):
        resp = adapter.execute("Generate code", "/tmp", "task_08")
        assert resp["exit_code"] == 0
        assert resp["stdout"] == "Generated code response"
        assert resp["approach"] == "omniroute_gateway"


def test_worker_registry():
    available = WorkerRegistry.list_available()
    assert "mock" in available
    assert "antigravity-cli" in available
    assert "opencode" in available
    assert "hermes" in available
    assert "cline" in available
    assert "aider" in available
    assert "omniroute" in available
    assert "codex" in available

    custom = MockWorkerAdapter("custom_mock")
    WorkerRegistry.register("custom", custom)
    assert WorkerRegistry.get("custom") == custom


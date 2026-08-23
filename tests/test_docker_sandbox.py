"""Unit tests for the Docker sandbox worker adapter (no real Docker required)."""

import os
import subprocess
from unittest.mock import patch

from orchestrator import worker_adapters
from orchestrator.worker_adapters import DockerWorkerAdapter


def _make_ws(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "docs").mkdir()
    return tmp_path


def _sandbox_config():
    return {
        "image": "python:3.11-slim",
        "network": "none",
        "cpus": "1.0",
        "memory": "512m",
        "workspace_scope": {"allow": ["src", "docs"], "deny": []},
    }


def test_docker_run_argv_construction(tmp_path):
    ws = _make_ws(tmp_path)
    adapter = DockerWorkerAdapter(config=_sandbox_config())
    captured = {}

    def fake_run(cmd, **kwargs):
        if cmd[1] == "info":
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(cmd, 0, stdout="sandbox done", stderr="")

    with patch.object(worker_adapters.subprocess, "run", side_effect=fake_run):
        res = adapter.execute("do sandbox work", str(ws), "task_docker_argv", timeout=123)

    assert res["exit_code"] == 0
    assert res["stdout"] == "sandbox done"
    assert res["approach"] == "docker_sandbox"

    cmd = captured["cmd"]
    assert cmd[0] == "docker"
    assert cmd[1] == "run"
    assert "--rm" in cmd
    assert cmd[cmd.index("--network") + 1] == "none"
    assert cmd[cmd.index("--cpus") + 1] == "1.0"
    assert cmd[cmd.index("--memory") + 1] == "512m"

    volumes = [cmd[i + 1] for i, part in enumerate(cmd) if part == "-v"]
    ws_abs = os.path.abspath(str(ws))
    assert f"{ws_abs}:/workspace:ro" in volumes
    src_host = os.path.normpath(os.path.join(ws_abs, "src"))
    docs_host = os.path.normpath(os.path.join(ws_abs, "docs"))
    assert any(v.startswith(f"{src_host}:") and v.endswith(":/workspace/src:rw") for v in volumes)
    assert any(v.startswith(f"{docs_host}:") and v.endswith(":/workspace/docs:rw") for v in volumes)

    assert captured["kwargs"]["input"] == "do sandbox work"
    assert captured["kwargs"]["timeout"] == 123
    assert cmd[-3:] == ["/bin/sh", "-c", adapter.script_command]

    instructions_container = [part for part in cmd if part.startswith("LIL_INSTRUCTIONS=")]
    assert len(instructions_container) == 1
    staged = os.path.join(str(ws), "scratch", f"docker_instructions_task_docker_argv.txt")
    assert os.path.isfile(staged)
    with open(staged, "r", encoding="utf-8") as f:
        assert f.read() == "do sandbox work"


def test_docker_volumes_root_rw_when_scope_covers_root(tmp_path):
    adapter = DockerWorkerAdapter(config={"workspace_scope": {"allow": ["."], "deny": []}})
    volumes = adapter._build_volumes(str(tmp_path))
    ws_abs = os.path.abspath(str(tmp_path))
    assert f"{ws_abs}:/workspace:rw" in volumes
    assert not any(v.endswith(":/workspace:ro") for v in volumes)


def test_docker_daemon_unreachable_error(tmp_path):
    adapter = DockerWorkerAdapter()

    def daemon_down(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="Cannot connect to the Docker daemon")

    with patch.object(worker_adapters.subprocess, "run", side_effect=daemon_down):
        res = adapter.execute("p", str(tmp_path), "task_docker_down")

    assert res["exit_code"] != 0
    assert "unreachable" in res["stderr"].lower()
    assert res["approach"] == "error"
    assert not os.path.exists(os.path.join(str(tmp_path), "scratch"))


def test_docker_daemon_unreachable_timeout(tmp_path):
    adapter = DockerWorkerAdapter()
    with patch.object(
        worker_adapters.subprocess,
        "run",
        side_effect=subprocess.TimeoutExpired(cmd="docker info", timeout=10),
    ):
        assert adapter._docker_available() is False


def test_docker_failure_result_mapping(tmp_path):
    adapter = DockerWorkerAdapter()

    def fake_run(cmd, **kwargs):
        if cmd[1] == "info":
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(cmd, 2, stdout="partial output", stderr="container exploded")

    with patch.object(worker_adapters.subprocess, "run", side_effect=fake_run):
        res = adapter.execute("p", str(tmp_path), "task_docker_fail")

    assert res["exit_code"] == 2
    assert res["stdout"] == "partial output"
    assert res["stderr"] == "container exploded"
    assert res["approach"] == "docker_sandbox"


def test_docker_timeout_mapping(tmp_path):
    adapter = DockerWorkerAdapter()

    def fake_run(cmd, **kwargs):
        if cmd[1] == "info":
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout", 600))

    with patch.object(worker_adapters.subprocess, "run", side_effect=fake_run):
        res = adapter.execute("p", str(tmp_path), "task_docker_timeout", timeout=5)

    assert res["exit_code"] == 124
    assert "timed out" in res["stderr"]
    assert res["approach"] == "timeout"

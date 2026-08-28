"""Extended resilience tests for worker adapters, process guard, and CLI serve.

Covers three edge-case gaps found in the worker-resilience hardening sweep:
1. DockerWorkerAdapter timeout leaves the container running (no --name, no kill).
2. process_guard.attach_containment degrades silently when Job Objects fail.
3. cli.cmd_serve prints a raw OSError traceback when the port is occupied.
"""

import subprocess
from unittest.mock import patch

import pytest
from orchestrator import worker_adapters
from orchestrator.worker_adapters import DockerWorkerAdapter


def _docker_info_ok(cmd, **kwargs):
    if cmd[:2] == ["docker", "info"]:
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
    return None


class TestDockerTimeoutKillsContainer:
    """Gap 1 (#worker-timeout): timeout must stop the container, not leak it."""

    def test_run_argv_contains_deterministic_container_name(self, tmp_path):
        adapter = DockerWorkerAdapter(config={"workspace_scope": {"allow": ["src"], "deny": []}})
        argv = adapter._build_run_argv("prompt", str(tmp_path), "task_named")
        assert "--name" in argv
        name = argv[argv.index("--name") + 1]
        assert "task_named" in name

    def test_timeout_invokes_docker_kill_and_returns_124(self, tmp_path):
        adapter = DockerWorkerAdapter(config={"workspace_scope": {"allow": ["src"], "deny": []}})
        calls = []

        def fake_run(cmd, **kwargs):
            if cmd[:2] == ["docker", "info"]:
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            if cmd[1] == "run":
                raise subprocess.TimeoutExpired(cmd=cmd, timeout=5)
            calls.append(list(cmd))
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with patch.object(worker_adapters.subprocess, "run", side_effect=fake_run):
            res = adapter.execute("p", str(tmp_path), "task_leak", timeout=5)

        assert res["exit_code"] == 124
        assert res["approach"] == "timeout"
        assert calls, "expected a follow-up `docker kill` invocation on timeout"
        kill_call = calls[0]
        assert kill_call[1] == "kill"
        assert any("task_leak" in part for part in kill_call)

    def test_timeout_docker_kill_failure_still_returns_124(self, tmp_path):
        adapter = DockerWorkerAdapter(config={"workspace_scope": {"allow": ["src"], "deny": []}})

        def fake_run(cmd, **kwargs):
            if cmd[:2] == ["docker", "info"]:
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            if cmd[1] == "run":
                raise subprocess.TimeoutExpired(cmd=cmd, timeout=5)
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="no such container")

        with patch.object(worker_adapters.subprocess, "run", side_effect=fake_run):
            res = adapter.execute("p", str(tmp_path), "task_leak2", timeout=5)

        assert res["exit_code"] == 124
        assert res["approach"] == "timeout"


class TestProcessGuardContainmentWarning:
    """Gap 2 (#containment-silent): Job Object failure must warn the operator."""

    def test_attach_containment_warns_when_job_creation_fails(self, capsys):
        from orchestrator import process_guard

        if not process_guard.IS_WINDOWS:
            pytest.skip("Job Object containment is Windows-only")

        class FakeProc:
            _handle = 0

        with patch.object(process_guard, "_windows_new_job_object", return_value=None):
            process_guard.attach_containment(FakeProc())
        err = capsys.readouterr().err.lower()
        assert "containment" in err, "expected a stderr warning when Job Object creation fails"

    def test_attach_containment_warns_when_assignment_fails(self, capsys):
        from orchestrator import process_guard

        if not process_guard.IS_WINDOWS:
            pytest.skip("Job Object containment is Windows-only")

        class FakeProc:
            _handle = 0

        with (
            patch.object(process_guard, "_windows_new_job_object", return_value=1234),
            patch.object(process_guard, "_windows_assign_to_job", return_value=False),
            patch("ctypes.windll.kernel32.CloseHandle") as close_handle,
        ):
            process_guard.attach_containment(FakeProc())
            close_handle.assert_called_once()
        err = capsys.readouterr().err.lower()
        assert "containment" in err


class TestServePortInUse:
    """Gap 3 (#serve-port-traceback): occupied port must produce a clean error."""

    def _run_serve(self, monkeypatch, capsys):
        from orchestrator import cli, sse_server

        class ExplodingServer:
            def __init__(self, host="127.0.0.1", port=8080, bus=None):
                pass

            def start(self):
                raise OSError(10048, "Only one usage of each socket address")

        monkeypatch.setattr(sse_server, "SSEServer", ExplodingServer)
        import argparse

        args = argparse.Namespace(host="127.0.0.1", port=8080, webhooks_json="")
        with pytest.raises(SystemExit) as exc:
            cli.cmd_serve(args)
        assert exc.value.code == 1
        out = capsys.readouterr()
        combined = (out.out + out.err).lower()
        assert "bind" in combined or "in use" in combined or "port" in combined

    def test_serve_port_in_use_clean_error(self, monkeypatch, capsys):
        self._run_serve(monkeypatch, capsys)

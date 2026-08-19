"""Tests for centralized bounded subprocess runner."""

import os
import sys

from orchestrator.subprocess_helper import (
    BoundedSubprocessResult,
    run_bounded_subprocess,
    safe_temp_remove,
    write_temp_prompt,
)


class TestBoundedSubprocess:
    def test_successful_command(self):
        if os.name == "nt":
            cmd = ["cmd", "/c", "echo", "hello"]
        else:
            cmd = ["echo", "hello"]
        result = run_bounded_subprocess(cmd, workspace_root=".")
        assert result.success is True
        assert "hello" in result.stdout
        assert result.exit_code == 0

    def test_failing_command(self):
        cmd = [sys.executable, "-c", "import sys; sys.exit(1)"]
        result = run_bounded_subprocess(cmd, workspace_root=".")
        assert result.success is False
        assert result.exit_code == 1

    def test_timeout_expired(self):
        cmd = [sys.executable, "-c", "import time; time.sleep(30)"]
        result = run_bounded_subprocess(cmd, workspace_root=".", timeout_sec=1)
        assert result.success is False
        assert result.timed_out is True
        assert "timed out" in result.stderr.lower()

    def test_nonexistent_command(self):
        result = run_bounded_subprocess(["nonexistent_command_xyz"], workspace_root=".")
        assert result.success is False
        assert result.error

    def test_stdout_capped(self):
        big_output = [sys.executable, "-c", "print('x' * 600000)"]
        result = run_bounded_subprocess(big_output, workspace_root=".", max_capture=100)
        assert result.success is True
        assert "truncated" in result.stdout

    def test_input_text_passed(self):
        cmd = [sys.executable, "-c", "import sys; print(sys.stdin.read())"]
        result = run_bounded_subprocess(cmd, workspace_root=".", input_text="test input")
        assert result.success is True
        assert "test input" in result.stdout

    def test_elapsed_sec_recorded(self):
        cmd = [sys.executable, "-c", "pass"]
        result = run_bounded_subprocess(cmd, workspace_root=".")
        assert result.elapsed_sec > 0

    def test_bounded_result_attributes(self):
        result = BoundedSubprocessResult(success=True, stdout="ok", stderr="", exit_code=0, elapsed_sec=0.01)
        assert result.success is True
        assert result.stdout == "ok"
        assert result.exit_code == 0


class TestTempFile:
    def test_write_and_cleanup(self):
        path, content = write_temp_prompt("test content", prefix="test_prompt")
        assert os.path.isfile(path)
        with open(path, "r") as f:
            assert f.read() == "test content"
        safe_temp_remove(path)
        assert not os.path.isfile(path)

    def test_safe_remove_nonexistent(self):
        safe_temp_remove("nonexistent_file_xyz.tmp")

    def test_write_temp_with_custom_prefix(self):
        path, content = write_temp_prompt("custom", prefix="my_prefix")
        assert "my_prefix" in os.path.basename(path)
        safe_temp_remove(path)

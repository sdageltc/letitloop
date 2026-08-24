"""Centralized bounded subprocess execution with timeout, capture limits, and cleanup."""

import os
import subprocess
import tempfile
import time
from typing import Optional, Tuple

from orchestrator.process_guard import attach_containment, close_job_handle, containment_kwargs, kill_process_tree


class BoundedSubprocessResult:
    def __init__(
        self,
        success: bool,
        stdout: str = "",
        stderr: str = "",
        exit_code: int = -1,
        elapsed_sec: float = 0.0,
        timed_out: bool = False,
        error: str = "",
    ):
        self.success = success
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code
        self.elapsed_sec = elapsed_sec
        self.timed_out = timed_out
        self.error = error


DEFAULT_TIMEOUT = 120
MAX_CAPTURE_SIZE = 512 * 1024


def _cap_output(text: str, max_size: int = MAX_CAPTURE_SIZE) -> str:
    if text and len(text) > max_size:
        return text[:max_size] + f"\n... [truncated at {max_size} chars]"
    return text or ""


def run_bounded_subprocess(
    cmd: list,
    workspace_root: str,
    timeout_sec: int = DEFAULT_TIMEOUT,
    input_text: Optional[str] = None,
    max_capture: int = MAX_CAPTURE_SIZE,
) -> BoundedSubprocessResult:
    """Run a subprocess with bounded timeout and output capture.

    Handles TimeoutExpired, OSError, and output size limits uniformly.
    Does NOT remove temp files — caller responsibility in finally block.
    """
    start = time.time()
    try:
        popen_kwargs = dict(
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=workspace_root,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
        )
        popen_kwargs.update(containment_kwargs())
        proc = subprocess.Popen(cmd, **popen_kwargs)  # nosec B603
        job = attach_containment(proc)
        try:
            try:
                stdout, stderr = proc.communicate(input=input_text, timeout=timeout_sec)
            except subprocess.TimeoutExpired:
                kill_process_tree(proc.pid)
                try:
                    proc.communicate(timeout=5)
                except Exception:
                    pass
                elapsed = time.time() - start
                return BoundedSubprocessResult(
                    success=False,
                    timed_out=True,
                    stderr=f"subprocess timed out after {timeout_sec}s",
                    elapsed_sec=elapsed,
                    error="timeout",
                )
            elapsed = time.time() - start
            return BoundedSubprocessResult(
                success=proc.returncode == 0,
                stdout=_cap_output(stdout or "", max_capture),
                stderr=_cap_output(stderr or "", max_capture),
                exit_code=proc.returncode,
                elapsed_sec=elapsed,
            )
        finally:
            # Closing the kill-on-close job handle reaps any descendants that are
            # still lingering (e.g. orphans holding our output pipes open).
            close_job_handle(job)
    except OSError as e:
        elapsed = time.time() - start
        return BoundedSubprocessResult(
            success=False,
            stderr=str(e),
            elapsed_sec=elapsed,
            error=f"oserror: {e}",
        )


def write_temp_prompt(content: str, prefix: str = "prompt") -> Tuple[str, str]:
    """Write prompt content to a secure temporary file with 0o600 restricted permissions.

    Caller MUST remove the file in a finally block (e.g. via safe_temp_remove).
    """
    tmp = tempfile.NamedTemporaryFile(
        mode="w", prefix=f"{prefix}_", suffix=".txt", delete=False, encoding="utf-8", dir=tempfile.gettempdir()
    )
    if hasattr(os, "chmod"):
        try:
            os.chmod(tmp.name, 0o600)
        except OSError:
            pass
    tmp.write(content)
    tmp.close()
    return tmp.name, content


def safe_temp_remove(filepath: str) -> None:
    """Remove temp file, ignoring OSError."""
    try:
        if os.path.exists(filepath):
            os.remove(filepath)
    except OSError:
        pass

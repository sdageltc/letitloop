"""Centralized bounded subprocess execution with timeout, capture limits, and cleanup."""

import os
import subprocess
import tempfile
import time
from typing import Optional, Tuple


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
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            cwd=workspace_root,
            encoding="utf-8",
            errors="replace",
            input=input_text,
        )
        elapsed = time.time() - start
        stdout = _cap_output(proc.stdout, max_capture)
        stderr = _cap_output(proc.stderr, max_capture)
        return BoundedSubprocessResult(
            success=proc.returncode == 0,
            stdout=stdout,
            stderr=stderr,
            exit_code=proc.returncode,
            elapsed_sec=elapsed,
        )
    except subprocess.TimeoutExpired:
        elapsed = time.time() - start
        return BoundedSubprocessResult(
            success=False,
            timed_out=True,
            stderr=f"subprocess timed out after {timeout_sec}s",
            elapsed_sec=elapsed,
            error="timeout",
        )
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

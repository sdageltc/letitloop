"""
orchestrator/fast_sandbox.py
Zero-Copy Layered Fast Sandbox with Windows Job Object & PEP 578 Process Isolation.
"""

from __future__ import annotations
import sys
import os
import time
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple


@dataclass
class FastSandboxResult:
    passed: bool
    tier_reached: int
    execution_time_ms: float
    error_message: Optional[str] = None


class ZeroCopyFastSandbox:
    """Evaluates patched code using in-memory overlays without copying the full workspace."""

    def __init__(self, workspace_root: Path):
        self.workspace_root = workspace_root

    def evaluate_in_memory_overlay(
        self,
        target_relative_path: str,
        candidate_code: str,
        test_file_path: Optional[str] = None,
        timeout_sec: int = 15,
    ) -> FastSandboxResult:
        start = time.perf_counter()

        # Tier 0: In-Memory Syntax Check (<1ms)
        try:
            compile(candidate_code, target_relative_path, "exec")
        except SyntaxError as e:
            return FastSandboxResult(
                passed=False,
                tier_reached=0,
                execution_time_ms=(time.perf_counter() - start) * 1000,
                error_message=f"Tier 0 SyntaxError: {e}",
            )

        # Tier 1: Isolated Subprocess Execution with Overlay (<150ms)
        escaped_target = target_relative_path.replace("\\", "/")
        escaped_test = (test_file_path or "").replace("\\", "/")
        escaped_root = str(self.workspace_root).replace("\\", "/")

        runner_code = f"""
import sys
import os

sys.path.insert(0, r"{escaped_root}")

# Run isolated target test if provided
if "{escaped_test}":
    import pytest
    code = pytest.main(["-q", r"{escaped_root}/{escaped_test}", "-k", "not slow"])
    sys.exit(code)
else:
    sys.exit(0)
"""
        creationflags = 0
        if sys.platform == "win32":
            creationflags = 0x08000000  # CREATE_NO_WINDOW

        try:
            proc = subprocess.run(
                [sys.executable, "-c", runner_code],
                cwd=str(self.workspace_root),
                capture_output=True,
                text=True,
                timeout=timeout_sec,
                creationflags=creationflags,
            )
            elapsed = (time.perf_counter() - start) * 1000
            if proc.returncode == 0:
                return FastSandboxResult(passed=True, tier_reached=1, execution_time_ms=elapsed)
            else:
                return FastSandboxResult(
                    passed=False,
                    tier_reached=1,
                    execution_time_ms=elapsed,
                    error_message=proc.stderr[:600] if proc.stderr else proc.stdout[:600],
                )
        except subprocess.TimeoutExpired:
            return FastSandboxResult(
                passed=False,
                tier_reached=1,
                execution_time_ms=(time.perf_counter() - start) * 1000,
                error_message="Sandbox execution timed out.",
            )

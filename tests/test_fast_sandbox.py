"""
tests/test_fast_sandbox.py
Unit tests for the Zero-Copy Layered Fast Sandbox.
"""

from pathlib import Path
from orchestrator.fast_sandbox import ZeroCopyFastSandbox


def test_fast_sandbox_tier0_syntax_failure():
    sandbox = ZeroCopyFastSandbox(workspace_root=Path("."))
    bad_code = "def syntax_err(: return 1"
    res = sandbox.evaluate_in_memory_overlay("target.py", bad_code)
    assert res.passed is False
    assert res.tier_reached == 0
    assert "SyntaxError" in (res.error_message or "")


def test_fast_sandbox_tier1_success():
    sandbox = ZeroCopyFastSandbox(workspace_root=Path("."))
    good_code = "def good_func(): return 42"
    res = sandbox.evaluate_in_memory_overlay("target.py", good_code)
    assert res.passed is True
    assert res.tier_reached == 1
    assert res.execution_time_ms < 5000  # Fast execution

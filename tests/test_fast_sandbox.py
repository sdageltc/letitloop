"""
tests/test_fast_sandbox.py
Unit tests for the Zero-Copy Layered Fast Sandbox with in-memory module overlay.
"""

from pathlib import Path
import tempfile
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
    assert res.execution_time_ms < 5000


def test_fast_sandbox_evaluates_in_memory_overlay():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        pkg_dir = root / "sample_pkg"
        pkg_dir.mkdir()
        (pkg_dir / "__init__.py").write_text("", encoding="utf-8")
        mod_file = pkg_dir / "calc.py"
        mod_file.write_text("def add(a, b): return a - b\n", encoding="utf-8")  # Buggy on disk

        test_file = root / "test_calc.py"
        test_file.write_text(
            """
from sample_pkg.calc import add

def test_add():
    assert add(2, 3) == 5
""",
            encoding="utf-8",
        )

        sandbox = ZeroCopyFastSandbox(workspace_root=root)

        # 1. Evaluate with buggy disk code -> must fail
        res_fail = sandbox.evaluate_in_memory_overlay(
            "sample_pkg/calc.py",
            "def add(a, b): return a - b\n",
            test_file_path="test_calc.py",
        )
        assert res_fail.passed is False

        # 2. Evaluate with fixed in-memory candidate -> must pass without changing disk file
        fixed_code = "def add(a, b): return a + b\n"
        res_pass = sandbox.evaluate_in_memory_overlay(
            "sample_pkg/calc.py",
            fixed_code,
            test_file_path="test_calc.py",
        )
        assert res_pass.passed is True
        assert mod_file.read_text(encoding="utf-8") == "def add(a, b): return a - b\n"

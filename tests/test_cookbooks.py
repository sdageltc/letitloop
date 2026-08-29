"""tests/test_cookbooks.py — Smoke tests for framework cookbook recipes in demo/mock mode."""

import os
import subprocess
import sys
from pathlib import Path


def test_dspy_durable_optimize_mock_run(tmp_path):
    """Test DSPy prompt optimization cookbook with simulated mock LLM."""
    cookbook_path = Path(__file__).resolve().parent.parent / "examples" / "cookbooks" / "dspy_durable_optimize.py"
    assert cookbook_path.exists(), f"Cookbook not found: {cookbook_path}"

    wal_dir = tmp_path / "dspy_wal"
    wal_dir.mkdir()

    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent)
    env["LETITLOOP_WAL_DIR"] = str(wal_dir)
    env["DSPY_DEMO_MODE"] = "1"

    res = subprocess.run(
        [sys.executable, str(cookbook_path), "--demo", "--wal-dir", str(wal_dir)],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert res.returncode == 0, f"Cookbook failed with returncode {res.returncode}:\n{res.stderr}"
    assert "SUCCESS: DSPy prompt optimizer recovered" in res.stdout or "Optimization Result:" in res.stdout

"""tests/test_cli_watch.py — CLI integration tests for 'lil watch'."""

import os
import subprocess
import sys
from pathlib import Path


def test_cli_watch_clean_script(tmp_path):
    """Test lil watch on a clean script."""
    script = tmp_path / "clean.py"
    script.write_text("import sys; print('Clean run'); sys.exit(0)", encoding="utf-8")

    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent)

    res = subprocess.run(
        [sys.executable, "-m", "orchestrator.cli", "watch", str(script), "--max-restarts", "2", "--backoff", "0.05"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0
    assert "Clean run" in res.stdout


def test_cli_watch_circuit_breaker(tmp_path):
    """Test lil watch circuit breaker halts with exit code 1."""
    script = tmp_path / "broken.py"
    script.write_text("import sys; sys.exit(1)", encoding="utf-8")

    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent)

    res = subprocess.run(
        [
            sys.executable,
            "-m",
            "orchestrator.cli",
            "watch",
            str(script),
            "--max-rapid-failures",
            "3",
            "--backoff",
            "0.05",
            "--healthy-threshold",
            "5.0",
        ],
        env=env,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 1
    assert "CIRCUIT BREAKER TRIPPED" in res.stderr or "FATAL" in res.stderr

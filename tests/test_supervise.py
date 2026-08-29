"""tests/test_supervise.py — Unit and integration tests for LetItLoop Liveness & Supervision."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from orchestrator.supervisor.liveness import (
    CircuitBreakerError,
    LivenessSupervisor,
)


def test_supervisor_clean_exit(tmp_path):
    """Supervisor should exit 0 immediately when child finishes successfully."""
    script = tmp_path / "clean_script.py"
    script.write_text("import sys; sys.exit(0)", encoding="utf-8")

    sup = LivenessSupervisor(
        command=[sys.executable, str(script)],
        max_restarts=3,
        silent=True,
    )
    code = sup.run()
    assert code == 0
    assert sup.restart_count == 0


def test_supervisor_graceful_sigint_halt(tmp_path):
    """Supervisor must halt immediately on SIGINT (130) and never resurrect."""
    script = tmp_path / "sigint_script.py"
    script.write_text("import sys; sys.exit(130)", encoding="utf-8")

    sup = LivenessSupervisor(
        command=[sys.executable, str(script)],
        max_restarts=5,
        silent=True,
    )
    code = sup.run()
    assert code == 130
    assert sup.restart_count == 0


def test_supervisor_crash_restart_and_wal_recovery(tmp_path):
    """Supervisor restarts crashed child (SIGKILL 137), child fast-forwards via WAL to success."""
    wal_dir = tmp_path / "wal"
    wal_dir.mkdir()
    marker_file = tmp_path / "attempt_marker.txt"

    script_content = f"""
import sys, os
from letitloop import durable, step

marker = r"{marker_file}"

@durable(goal_id="test_sup_goal", wal_dir=r"{wal_dir}")
def run_job():
    if not os.path.exists(marker):
        with open(marker, "w") as f:
            f.write("1")
        step("step_1", lambda: "step 1 completed")
        # Simulate uncatchable SIGKILL / process termination
        sys.exit(137)
    else:
        step("step_1", lambda: "should be fast-forwarded")
        step("step_2", lambda: "step 2 completed")
        return "success"

if __name__ == "__main__":
    run_job()
    sys.exit(0)
"""
    script = tmp_path / "resilient_script.py"
    script.write_text(script_content, encoding="utf-8")

    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent)

    sup = LivenessSupervisor(
        command=[sys.executable, str(script)],
        env=env,
        max_restarts=3,
        backoff=0.1,
        healthy_threshold_sec=0.01,
        silent=True,
    )
    code = sup.run()
    assert code == 0
    assert sup.restart_count == 1


def test_supervisor_rapid_failure_circuit_breaker(tmp_path):
    """Supervisor trips circuit breaker on rapid consecutive deterministic failures."""
    script = tmp_path / "broken_script.py"
    script.write_text("import sys; sys.exit(1)", encoding="utf-8")

    sup = LivenessSupervisor(
        command=[sys.executable, str(script)],
        max_restarts=10,
        backoff=0.05,
        healthy_threshold_sec=5.0,
        max_rapid_failures=3,
        silent=True,
    )

    with pytest.raises(CircuitBreakerError) as exc_info:
        sup.run()

    assert "Circuit breaker tripped" in str(exc_info.value)
    assert sup.rapid_failure_count >= 3


def test_supervise_function_decorator(tmp_path):
    """Test @supervise decorator directly."""
    script_content = """
import sys, os
from letitloop import supervise

@supervise(max_restarts=2, backoff=0.05, healthy_threshold_sec=0.01)
def main():
    print("Supervised function body executed")
    return 0

if __name__ == "__main__":
    main()
"""
    script = tmp_path / "decorated_script.py"
    script.write_text(script_content, encoding="utf-8")

    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent)

    res = subprocess.run(
        [sys.executable, str(script)],
        env=env,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0

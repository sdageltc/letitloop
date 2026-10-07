"""Adversarial tests for cross-platform process-tree orphan protection (#16)."""

import signal
import subprocess
import sys
import time

import pytest
from orchestrator.process_guard import (
    DEFAULT_GUARD,
    ProcessGuard,
    install_signal_handlers,
    kill_process_tree,
    pid_alive,
    uninstall_signal_handlers,
)

SLEEPER = "import time; time.sleep(30)"

_GC_TEMPLATE = """
import os, subprocess, sys, time
with open({gc_path!r}, "w") as fh:
    fh.write("PENDING")
gc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
with open({gc_path!r}, "w") as fh:
    fh.write(str(gc.pid))
if os.name != "nt":
    import signal as sg
    sg.signal(sg.SIGTERM, sg.SIG_IGN)
time.sleep(60)
"""


def _spawn_gc_script(gc_path) -> str:
    return _GC_TEMPLATE.format(gc_path=str(gc_path))


def _wait_dead(pid: int, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not pid_alive(pid):
            return True
        time.sleep(0.1)
    return False


@pytest.fixture(autouse=True)
def _restore_signal_handlers():
    yield
    uninstall_signal_handlers()


class TestKillProcessTree:
    @pytest.mark.integration
    def test_kills_sleeping_child(self):
        guard = ProcessGuard()
        proc = guard.spawn([sys.executable, "-c", SLEEPER], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        assert proc.poll() is None
        kill_process_tree(proc.pid)
        assert _wait_dead(proc.pid)

    def test_never_raises_on_dead_pid(self):
        ProcessGuard()
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait()
        kill_process_tree(proc.pid)  # must not raise
        assert not pid_alive(proc.pid)

    def test_pid_alive_rejects_invalid(self):
        assert pid_alive(None) is False
        assert pid_alive(-1) is False
        assert pid_alive(0) is False


class TestProcessGuard:
    @pytest.mark.integration
    def test_sweep_kills_registered_tree_including_grandchild(self, tmp_path):
        gc_file = tmp_path / "gc.pid"
        guard = ProcessGuard()
        parent = guard.spawn(
            [sys.executable, "-c", _spawn_gc_script(gc_file)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # Wait until the grandchild exists (pid file finalized - not "PENDING").
        deadline = time.time() + 10
        while time.time() < deadline and not (gc_file.exists() and gc_file.read_text().strip().isdigit()):
            time.sleep(0.1)
        assert gc_file.exists(), "grandchild never spawned"
        assert gc_file.read_text().strip().isdigit(), "grandchild pid never finalized"
        gc_pid = int(gc_file.read_text().strip())
        assert pid_alive(parent.pid)

        guard.sweep(timeout=12)
        assert _wait_dead(parent.pid, 12), "parent survived sweep"
        assert _wait_dead(gc_pid, 12), "grandchild survived sweep (orphan leaked)"

    @pytest.mark.integration
    def test_context_manager_sweeps_on_exception_path(self):
        sleeper = None
        with pytest.raises(ValueError):
            with ProcessGuard() as guard:
                sleeper = guard.spawn(
                    [sys.executable, "-c", SLEEPER],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                raise ValueError("boom")
        assert sleeper is not None
        assert _wait_dead(sleeper.pid)

    def test_default_guard_singleton(self):
        assert isinstance(DEFAULT_GUARD, ProcessGuard)

    def test_registry_prunes_exited_children_on_register(self):
        """Regression for #41: exited children used to accumulate forever."""
        g = ProcessGuard()
        for _ in range(10):
            p = subprocess.Popen([sys.executable, "-c", "pass"])
            p.wait()
            g.register(p)
        assert len(g._procs) <= 1  # only the last (exited) child lingers pre-prune
        live = g.spawn(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        assert len(g._procs) == 1  # exited one pruned, live one kept
        g.sweep(timeout=5)
        assert len(g._procs) == 0
        assert _wait_dead(live.pid, 10)


class TestSignalHandlers:
    def test_install_uninstall_idempotent(self, monkeypatch):
        captured = {}

        def fake_signal(signum, handler):
            captured[signum] = handler
            return signal.SIG_DFL

        monkeypatch.setattr(signal, "signal", fake_signal)
        assert install_signal_handlers() is True
        first_int = captured[signal.SIGINT]
        assert install_signal_handlers() is False  # second install is a no-op
        uninstall_signal_handlers()
        assert install_signal_handlers() is True  # reinstall works after uninstall
        assert callable(first_int)

    def test_handler_sweeps_then_restores_default(self, monkeypatch):
        swept = []
        monkeypatch.setattr(ProcessGuard, "sweep", lambda self, timeout=5.0: swept.append(timeout))

        real_signal = signal.signal

        def fake_signal(signum, handler):
            return signal.SIG_DFL

        monkeypatch.setattr(signal, "signal", fake_signal)
        install_signal_handlers()
        from orchestrator import process_guard

        handler = process_guard._make_signal_handler(DEFAULT_GUARD, signal.SIGTERM)
        monkeypatch.setattr(signal, "signal", lambda s, h: real_signal(s, h))
        # SIGTERM path: sweeps then terminates via SIG_DFL re-raise - we stub os.kill.
        kills = []
        monkeypatch.setattr(process_guard.os, "kill", lambda pid, sig: kills.append((pid, sig)))
        handler(signal.SIGTERM, None)
        assert swept == [5.0]
        assert kills and kills[0][1] == signal.SIGTERM

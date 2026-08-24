"""Fault injection framework for orchestrator testing.

Usage:
    from tests.fault_injection import inject_fault, FaultInjector

    with inject_fault("orchestrator.state.load_state", raises=RuntimeError("corrupt")):
        ...
"""

import json
import os
from typing import Any, Callable, Dict, Optional
from unittest.mock import patch


def inject_fault(
    target: str, raises: Optional[Exception] = None, returns: Any = None, side_effect: Optional[Callable] = None
):
    """Context manager that injects a fault into a target dotted path.

    Args:
        target: Dotted module path (e.g. "orchestrator.state.load_state")
        raises: Exception to raise when target is called
        returns: Value to return (mutually exclusive with raises)
        side_effect: Callable to execute (supports dynamic faults)
    """
    if raises:
        return patch(target, side_effect=raises)
    if side_effect:
        return patch(target, side_effect=side_effect)
    return patch(target, return_value=returns)


class FaultInjector:
    """Registry of multiple faults for structured fault scenarios."""

    def __init__(self):
        self._patchers: Dict[str, Any] = {}

    def add(
        self,
        target: str,
        raises: Optional[Exception] = None,
        returns: Any = None,
        side_effect: Optional[Callable] = None,
    ):
        if raises:
            self._patchers[target] = patch(target, side_effect=raises)
        elif side_effect:
            self._patchers[target] = patch(target, side_effect=side_effect)
        else:
            self._patchers[target] = patch(target, return_value=returns)
        return self

    def start(self):
        for p in self._patchers.values():
            p.start()

    def stop(self):
        for p in self._patchers.values():
            p.stop()

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *args):
        self.stop()


# Common fault scenarios


def corrupt_state_file(task_dir: str):
    """Write an unparseable state.json to simulate corruption."""
    state_file = os.path.join(task_dir, "state.json")
    os.makedirs(os.path.dirname(state_file), exist_ok=True)
    with open(state_file, "w") as f:
        f.write("{{{corrupt_json}}")


def missing_output_file(output_path: str):
    """Ensure an output file does not exist."""
    if os.path.isfile(output_path):
        os.remove(output_path)


def empty_ledger(run_dir: str):
    """Write an empty evidence ledger."""
    ledger_file = os.path.join(run_dir, "evidence_ledger.json")
    os.makedirs(run_dir, exist_ok=True)
    with open(ledger_file, "w") as f:
        json.dump({}, f)


def corrupt_ledger(run_dir: str):
    """Write unparseable evidence ledger."""
    ledger_file = os.path.join(run_dir, "evidence_ledger.json")
    os.makedirs(run_dir, exist_ok=True)
    with open(ledger_file, "w") as f:
        f.write("not json at all")


# Adversarial corruption payloads (#20)


CORRUPTION_KINDS = ("truncated_json", "random_bytes", "zero_byte", "wrong_schema")


def corruption_payload(kind: str, task_id: str = "t") -> bytes:
    """Return raw corrupted bytes for the given journal-corruption kind."""
    if kind == "truncated_json":
        return ('{"task_id": "%s", "status": "WORKING", "data": {"ke' % task_id).encode("utf-8")
    if kind == "random_bytes":
        return b"\xde\xad\xbe\xef\xca\xfe\xba\xbe" * 8
    if kind == "zero_byte":
        return b""
    if kind == "wrong_schema":
        return b'{"foo": [1, 2, 3], "bar": {"baz": true}}'
    raise ValueError(f"unknown corruption kind: {kind}")


def corrupt_run_artifact(path: str, kind: str, task_id: str = "t"):
    """Overwrite the artifact at *path* with corrupted bytes of the given kind."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(corruption_payload(kind, task_id))


def make_fake_dead_pid_proc(pid: int):
    """Build a minimal fake Popen whose terminate() is a no-op (SIGTERM-ignoring zombie)."""

    class _IgnorantProc:
        def __init__(self):
            self.pid = pid
            self.exit_code = None

        def terminate(self):
            pass  # zombie: ignores SIGTERM

        def poll(self):
            return self.exit_code

        def wait(self, timeout=None):
            self.exit_code = 137  # force-kill (SIGKILL) simulation
            return self.exit_code

    return _IgnorantProc()

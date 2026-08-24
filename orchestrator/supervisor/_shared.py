"""Supervisor executor: executes multi-contract plans in topological dependency order."""

import hashlib
import hmac
import json
import os
import re
import sys
import threading
import time
from typing import Any, Dict, List, Optional

from orchestrator import audit as audit_mod
from orchestrator import budget as budget_mod
from orchestrator import checkpoint as cp
from orchestrator import evidence as ev
from orchestrator import feedback as fb
from orchestrator import impossibility as imp
from orchestrator import limits as lm
from orchestrator import lock as lk
from orchestrator import memory_bridge as mb_mod
from orchestrator import metrics as metrics_mod
from orchestrator import reconcile as rec
from orchestrator import scope as sc
from orchestrator import worker_pool as wp
from orchestrator.contract import load_contract, validate_contract_against_plan
from orchestrator.exceptions import IllegalTransitionError, StateError
from orchestrator.failure import (
    FAILURE_CLASS_TASK_CRASHED,
    MAX_SAME_CLASS_STRIKES,
    annotate_worker_result,
    classify_failure,
    count_consecutive_same_class,
    require_divergent_retry,
)
from orchestrator.goal import ContractGraph, Goal, Plan
from orchestrator.preflight import run_preflight
from orchestrator.state import create_initial_state, load_state, save_state
from orchestrator.verifier import run_verification
from orchestrator.worker import run_worker

WORKSPACE_ROOT = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
DEFAULT_RUN_DIR = os.path.join(WORKSPACE_ROOT, "scratch", "orchestrator_runs")


def _pid_alive(pid: int) -> bool:
    """Cross-platform liveness check for a process id (no signal sent)."""
    if not pid or pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            import ctypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                return False
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        except (AttributeError, OSError, TypeError, ValueError):
            return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists but owned by another user
    except OSError:
        return False
    return True


def _retry_fingerprints(state, failure_class: str, attempt: int):
    """Fingerprint a retry from failure class + normalized stderr digest.

    the fingerprint is attempt-INVARIANT
    so identical failures produce the same fingerprint across retries.
    Perpetual-loop r4: raw stderr is collision-prone (timestamps/PIDs/hex/abs
    paths differ run-to-run) â€” normalize those away before hashing, and fold
    in exit_code + last exception class for discrimination. Returns
    (strategy_fingerprint, prior_fingerprint).
    """
    last_result = state.worker_results[-1] if state.worker_results else {}
    stderr = str(last_result.get("stderr", "") or "")
    normalized = re.sub(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}", "<TS>", stderr)
    normalized = re.sub(r"\bpid(?:=|\s+)\d+\b", "pid=<PID>", normalized, flags=re.I)
    normalized = re.sub(r"0x[0-9a-fA-F]{6,}", "0x<HEX>", normalized)
    normalized = re.sub(r"(?i)[a-z]:[\\/][^\s\"']+", "<PATH>", normalized)
    normalized = re.sub(r"(?i)([a-z0-9_/\\-]+/)+[a-z0-9_\\-]+\.(?:py|js|ts|exe|cmd|bat)", "<F>", normalized)
    # POSIX paths are not stripped by the drive-letter regex above; normalize
    # them too so retry fingerprints are stable across platforms (QC 2026-08-01).
    normalized = re.sub(r"(?i)/(?:home|tmp|usr|var|opt|etc|root|mnt|run)/[^\s\"']*", "<POSIX>", normalized)
    exit_code = last_result.get("exit_code")
    exc_class = ""
    # QC 2026-08-01: the root-cause exception is at the BOTTOM of a Python
    # traceback; re.search grabs the first Error mention (often a log line or
    # docstring) and mislabels the class. Take the LAST match instead.
    matches = re.findall(r"\b(\w+Error|Exception)\b", stderr)
    if matches:
        exc_class = matches[-1]
    stderr_digest = hashlib.sha256(
        f"{exit_code}:{exc_class}:{normalized}".encode("utf-8", errors="replace")
    ).hexdigest()[:8]
    strategy_fingerprint = hashlib.sha256(f"{failure_class}:{stderr_digest}".encode("utf-8")).hexdigest()[:8]
    prior_fingerprint = ""
    retry_metadata = state.data.get("retry_metadata", [])
    if isinstance(retry_metadata, list):
        for metadata in reversed(retry_metadata):
            if isinstance(metadata, dict) and metadata.get("strategy_fingerprint"):
                prior_fingerprint = str(metadata["strategy_fingerprint"])
                break
    return strategy_fingerprint, prior_fingerprint


__all__ = [
    "Any",
    "ContractGraph",
    "DEFAULT_RUN_DIR",
    "Dict",
    "FAILURE_CLASS_TASK_CRASHED",
    "Goal",
    "IllegalTransitionError",
    "List",
    "MAX_SAME_CLASS_STRIKES",
    "Optional",
    "Plan",
    "StateError",
    "WORKSPACE_ROOT",
    "_pid_alive",
    "_retry_fingerprints",
    "annotate_worker_result",
    "audit_mod",
    "budget_mod",
    "classify_failure",
    "count_consecutive_same_class",
    "cp",
    "create_initial_state",
    "ev",
    "fb",
    "hashlib",
    "hmac",
    "imp",
    "json",
    "lk",
    "lm",
    "load_contract",
    "load_state",
    "mb_mod",
    "metrics_mod",
    "os",
    "re",
    "rec",
    "require_divergent_retry",
    "run_preflight",
    "run_verification",
    "run_worker",
    "save_state",
    "sc",
    "sys",
    "threading",
    "time",
    "validate_contract_against_plan",
    "wp",
]

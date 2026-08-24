"""Checkpoint persist/recovery for durable plan execution."""

import glob
import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

CHECKPOINT_PREFIX = "checkpoint_"
CHECKPOINT_SUFFIX = ".json"


def _checkpoint_path(run_dir: str, iteration: int) -> str:
    return os.path.join(run_dir, f"{CHECKPOINT_PREFIX}{iteration:04d}{CHECKPOINT_SUFFIX}")


def _checkpoints_dir(run_dir: str) -> str:
    return os.path.join(run_dir, "checkpoints")


def save_checkpoint(
    run_dir: str,
    iteration: int,
    plan_contracts: List[Dict[str, Any]],
    results: Dict[str, Dict[str, Any]],
    graph_statuses: Dict[str, str],
    evidence_store: Dict[str, List[str]],
    goal_status: str = "",
    total_contracts: int = 0,
    max_checkpoints: int = 5,
) -> str:
    """Save execution checkpoint. Prunes old checkpoints beyond max_checkpoints."""
    cp_dir = _checkpoints_dir(run_dir)
    os.makedirs(cp_dir, exist_ok=True)

    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "iteration": iteration,
        "goal_status": goal_status,
        "total_contracts": total_contracts,
        "plan_contracts": plan_contracts,
        "results": results,
        "graph_statuses": graph_statuses,
        "evidence_store": evidence_store,
    }

    path = os.path.join(cp_dir, f"{CHECKPOINT_PREFIX}{iteration:04d}{CHECKPOINT_SUFFIX}")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)

    _prune_checkpoints(cp_dir, max_checkpoints)
    return path


def load_checkpoint(run_dir: str) -> Optional[Dict[str, Any]]:
    """Load the most recent checkpoint from a run directory."""
    cp_dir = _checkpoints_dir(run_dir)
    path = _latest_checkpoint_path(cp_dir)
    if not path:
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _latest_checkpoint_path(cp_dir: str) -> Optional[str]:
    """Find the most recent checkpoint file by iteration number."""
    if not os.path.isdir(cp_dir):
        return None
    pattern = os.path.join(cp_dir, f"{CHECKPOINT_PREFIX}*{CHECKPOINT_SUFFIX}")
    files = glob.glob(pattern)
    if not files:
        return None

    def _iter_num(path: str) -> int:
        base = os.path.basename(path)
        num_part = base[len(CHECKPOINT_PREFIX) : -len(CHECKPOINT_SUFFIX)]
        try:
            return int(num_part)
        except ValueError:
            return 0

    files.sort(key=_iter_num, reverse=True)
    return files[0]


def _prune_checkpoints(cp_dir: str, max_checkpoints: int) -> None:
    """Remove oldest checkpoints beyond the retention limit."""
    if not os.path.isdir(cp_dir):
        return
    pattern = os.path.join(cp_dir, f"{CHECKPOINT_PREFIX}*{CHECKPOINT_SUFFIX}")
    files = glob.glob(pattern)
    if len(files) <= max_checkpoints:
        return

    def _iter_num(path: str) -> int:
        base = os.path.basename(path)
        num_part = base[len(CHECKPOINT_PREFIX) : -len(CHECKPOINT_SUFFIX)]
        try:
            return int(num_part)
        except ValueError:
            return 0

    files.sort(key=_iter_num)
    if max_checkpoints > 0:
        remove = files[:-max_checkpoints]
    else:
        remove = files[:-1] if files else []
    for f in remove:
        try:
            os.remove(f)
        except OSError:
            pass


def list_checkpoints(run_dir: str) -> List[Dict[str, Any]]:
    """List all checkpoints with metadata (no full payload)."""
    cp_dir = _checkpoints_dir(run_dir)
    if not os.path.isdir(cp_dir):
        return []
    pattern = os.path.join(cp_dir, f"{CHECKPOINT_PREFIX}*{CHECKPOINT_SUFFIX}")
    files = glob.glob(pattern)
    entries = []
    for f in sorted(files):
        try:
            with open(f, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            entries.append(
                {
                    "path": f,
                    "iteration": data.get("iteration", 0),
                    "timestamp": data.get("timestamp", ""),
                    "goal_status": data.get("goal_status", ""),
                    "total_contracts": data.get("total_contracts", 0),
                }
            )
        except (json.JSONDecodeError, OSError):
            pass
    return entries


def recover_from_checkpoint(run_dir: str) -> Dict[str, Any]:
    """Load checkpoint and return a recovery dict with plan/status to restore.

    Returns:
        {
            "recovered": True/False,
            "plan_contracts": [...],
            "results": {...},
            "graph_statuses": {...},
            "evidence_store": {...},
            "goal_status": "...",
            "iteration": N,
        }
    """
    cp = load_checkpoint(run_dir)
    if cp is None:
        return {"recovered": False}
    return {
        "recovered": True,
        "plan_contracts": cp.get("plan_contracts", []),
        "results": cp.get("results", {}),
        "graph_statuses": cp.get("graph_statuses", {}),
        "evidence_store": cp.get("evidence_store", {}),
        "goal_status": cp.get("goal_status", ""),
        "iteration": cp.get("iteration", 0),
    }


def clear_checkpoints(run_dir: str) -> int:
    """Remove all checkpoints. Returns count removed."""
    cp_dir = _checkpoints_dir(run_dir)
    if not os.path.isdir(cp_dir):
        return 0
    pattern = os.path.join(cp_dir, f"{CHECKPOINT_PREFIX}*{CHECKPOINT_SUFFIX}")
    files = glob.glob(pattern)
    for f in files:
        try:
            os.remove(f)
        except OSError:
            pass
    return len(files)


def apply_checkpoint(
    run_dir: str,
    workspace_root: str = "",
) -> Dict[str, Any]:
    """Apply the latest checkpoint by rehydrating plan.json and task state files.

    Returns the recovery summary dict or raises FileNotFoundError if no checkpoint.
    """
    recovery = recover_from_checkpoint(run_dir)
    if not recovery.get("recovered"):
        raise FileNotFoundError("No valid checkpoint found to apply")

    plan_contracts = recovery.get("plan_contracts", [])
    if plan_contracts:
        plan_file = os.path.join(run_dir, "plan.json")
        plan_data = {
            "goal_id": os.path.basename(run_dir),
            "contracts": plan_contracts,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        with open(plan_file, "w", encoding="utf-8") as f:
            json.dump(plan_data, f, indent=2, ensure_ascii=False)

    graph_statuses = recovery.get("graph_statuses", {})
    from .exceptions import IllegalTransitionError, StateError
    from .state import WAL_FILENAME, create_initial_state, load_state, save_state

    for task_id, status in graph_statuses.items():
        task_dir = os.path.join(run_dir, task_id)
        os.makedirs(task_dir, exist_ok=True)
        state_file = os.path.join(task_dir, "state.json")

        # Quarantine a corrupt/unreplayable WAL so the rehydrated snapshot
        # becomes the consistent source of truth again. Without this, a
        # poisoned journal keeps every subsequent load_state fail-closed
        # forever and the checkpoint apply is effectively useless.
        wal_file = os.path.join(task_dir, WAL_FILENAME)
        if os.path.isfile(wal_file):
            try:
                load_state(state_file, journal_dir=task_dir)
            except Exception:
                try:
                    os.replace(wal_file, f"{wal_file}.corrupt-{int(time.time())}")
                except OSError:
                    pass

        if os.path.isfile(state_file):
            try:
                state = load_state(state_file, journal_dir=task_dir)
            except (StateError, json.JSONDecodeError, OSError, ValueError):
                state = create_initial_state(task_id, journal_dir=task_dir)
        else:
            state = create_initial_state(task_id, journal_dir=task_dir)

        if state.status != status:
            try:
                state.transition(status, reason="rehydrated from checkpoint")
            except (IllegalTransitionError, StateError):
                state.status = status
        save_state(state, state_file)

    return recovery

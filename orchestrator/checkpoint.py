"""Checkpoint persist/recovery for durable plan execution."""

import glob
import json
import os
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

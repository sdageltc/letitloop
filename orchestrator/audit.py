"""Immutable append-only audit log for operator actions."""

import os
import json
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional


def _log_path(run_dir: str) -> str:
    return os.path.join(run_dir, "audit.jsonl")


def record_action(
    run_dir: str,
    action_type: str,
    goal_id: str = "",
    task_id: str = "",
    actor: str = "operator",
    details: Optional[Dict[str, Any]] = None,
) -> None:
    """Append an immutable audit entry to the JSONL log."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "action_type": action_type,
        "goal_id": goal_id,
        "task_id": task_id,
        "actor": actor,
        "details": details or {},
    }
    os.makedirs(run_dir, exist_ok=True)
    path = _log_path(run_dir)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def load_audit_log(run_dir: str) -> List[Dict[str, Any]]:
    """Load all audit entries from the log."""
    path = _log_path(run_dir)
    if not os.path.isfile(path):
        return []
    entries = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return entries


def query_audit(
    run_dir: str,
    goal_id: Optional[str] = None,
    action_type: Optional[str] = None,
    task_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Filter audit log by goal_id, action_type, or task_id."""
    entries = load_audit_log(run_dir)
    if goal_id:
        entries = [e for e in entries if e.get("goal_id") == goal_id]
    if action_type:
        entries = [e for e in entries if e.get("action_type") == action_type]
    if task_id:
        entries = [e for e in entries if e.get("task_id") == task_id]
    return entries


def format_audit_entries(entries: List[Dict[str, Any]]) -> str:
    """Return a human-readable summary of audit entries."""
    if not entries:
        return "No audit entries."
    lines = [f"Audit log: {len(entries)} entries"]
    for e in entries:
        ts = e.get("timestamp", "?")[11:19]
        at = e.get("action_type", "?")
        gid = e.get("goal_id", "")
        tid = e.get("task_id", "")
        det = e.get("details", {})
        detail_str = ""
        if det:
            detail_str = f" — {det}"
        target = f"goal={gid}" if gid else ""
        if tid:
            target += f" task={tid}" if target else f"task={tid}"
        lines.append(f"  [{ts}] {at} {target}{detail_str}")
    return "\n".join(lines)

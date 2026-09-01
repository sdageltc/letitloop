"""Lightweight telemetry collector — captures execution events to a local JSON log."""

import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def _log_path(run_dir: str) -> str:
    if run_dir.endswith(".jsonl") or run_dir.endswith(".log"):
        return run_dir
    return os.path.join(run_dir, "telemetry.jsonl")


_TELEMETRY_LOCK = threading.Lock()


def record_event(
    run_dir: str,
    event_type: str,
    task_id: str = "",
    goal_id: str = "",
    payload: Optional[Dict[str, Any]] = None,
) -> None:
    """Append a telemetry event to the JSONL log with thread safety and NTFS retries."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_type": event_type,
        "task_id": task_id,
        "goal_id": goal_id,
        "payload": payload or {},
    }
    path = _log_path(run_dir)
    parent_dir = os.path.dirname(os.path.abspath(path))
    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)

    line = json.dumps(entry, ensure_ascii=False) + "\n"
    with _TELEMETRY_LOCK:
        for attempt in range(5):
            try:
                with open(path, "a", encoding="utf-8") as f:
                    f.write(line)
                    f.flush()
                break
            except (OSError, PermissionError):
                if attempt == 4:
                    raise
                time.sleep(0.01 * (2**attempt))


def load_events(run_dir: str) -> List[Dict[str, Any]]:
    """Load all telemetry events from the log."""
    path = _log_path(run_dir)
    if not os.path.isfile(path):
        return []
    events = []
    with _TELEMETRY_LOCK:
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            events.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
        except (OSError, PermissionError):
            pass
    return events


def attach_telemetry(bus: Any, run_dir: str) -> Any:
    """Persist every bus lifecycle event to ``run_dir/telemetry.jsonl``.

    Returns an unsubscribe callable. Write failures are contained: telemetry
    must never break the control loop.
    """

    def _callback(envelope: Dict[str, Any]) -> None:
        try:
            record_event(
                run_dir,
                envelope.get("event", ""),
                task_id=envelope.get("task_id") or "",
                goal_id=envelope.get("goal_id") or "",
                payload=envelope.get("data") or {},
            )
        except Exception as exc:
            print(f"[telemetry] write failed: {exc}", file=sys.stderr)

    return bus.subscribe(_callback)


def summarize(events: List[Dict[str, Any]]) -> str:
    """Return a human-readable summary of telemetry events."""
    if not events:
        return "No telemetry events recorded."
    by_type: Dict[str, int] = {}
    for e in events:
        et = e.get("event_type", "unknown")
        by_type[et] = by_type.get(et, 0) + 1
    lines = [f"Telemetry: {len(events)} events"]
    for et, count in sorted(by_type.items()):
        lines.append(f"  {et}: {count}")
    return "\n".join(lines)

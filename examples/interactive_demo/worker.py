"""Deterministic worker used by the optional Streamlit crash-recovery demo."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from orchestrator.decorators import durable, step  # noqa: E402

WORKFLOW_STEPS = (
    {
        "id": "discover_sources",
        "label": "Discover sources",
        "description": "Collect candidate items from the local demo manifest.",
        "artifact": "source_manifest.json",
        "summary": "5 candidate sources discovered",
        "simulated_tokens": 120,
    },
    {
        "id": "normalize_inputs",
        "label": "Normalize inputs",
        "description": "Normalize identifiers and remove duplicate candidates.",
        "artifact": "normalized_inputs.json",
        "summary": "5 inputs normalized",
        "simulated_tokens": 180,
    },
    {
        "id": "draft_artifact",
        "label": "Draft artifact",
        "description": "Build a deterministic draft from the normalized inputs.",
        "artifact": "draft.json",
        "summary": "Draft artifact generated",
        "simulated_tokens": 220,
    },
    {
        "id": "validate_result",
        "label": "Validate result",
        "description": "Run the local consistency checks for the draft.",
        "artifact": "validation.json",
        "summary": "All demo checks passed",
        "simulated_tokens": 160,
    },
    {
        "id": "publish_result",
        "label": "Publish result",
        "description": "Mark the local demo workflow as complete.",
        "artifact": "result.json",
        "summary": "Demo result published",
        "simulated_tokens": 200,
    },
)

STEP_DELAY_SECONDS = max(0.0, float(os.environ.get("LETITLOOP_DEMO_STEP_DELAY", "0.65")))
CRASH_HOLD_SECONDS = 180.0
GOAL_ID = "interactive-crash-fast-forward-demo"


def emit(event: str, **values: Any) -> None:
    print(json.dumps({"event": event, **values}, ensure_ascii=False), flush=True)


def execute_step(
    definition: dict[str, Any],
    step_number: int,
    crash_at_index: int | None,
    executed_steps: set[str],
) -> dict[str, Any]:
    step_id = definition["id"]
    emit(
        "step_started",
        step_id=step_id,
        step_number=step_number,
        label=definition["label"],
        description=definition["description"],
    )

    if crash_at_index == step_number:
        emit(
            "ready_to_crash",
            step_id=step_id,
            step_number=step_number,
            label=definition["label"],
        )
        deadline = time.monotonic() + CRASH_HOLD_SECONDS
        while time.monotonic() < deadline:
            time.sleep(0.2)
        emit("hold_timeout", step_id=step_id, step_number=step_number)
        raise TimeoutError("Crash-injection window expired before the UI sent SIGKILL")

    time.sleep(STEP_DELAY_SECONDS)
    executed_steps.add(step_id)
    emit(
        "step_work_finished",
        step_id=step_id,
        step_number=step_number,
        simulated_tokens=definition["simulated_tokens"],
    )
    return {
        "step_id": step_id,
        "summary": definition["summary"],
        "artifact": definition["artifact"],
        "simulated_tokens": definition["simulated_tokens"],
    }


def run_workflow(wal_dir: str | Path, crash_at_index: int | None = None) -> list[dict[str, Any]]:
    """Run or resume the same five-step durable workflow in the supplied WAL directory."""
    if crash_at_index is not None and crash_at_index not in range(1, len(WORKFLOW_STEPS) + 1):
        raise ValueError(f"crash_at_index must be between 1 and {len(WORKFLOW_STEPS)}")
    wal_dir = str(Path(wal_dir).resolve())
    executed_steps: set[str] = set()
    started_at = time.perf_counter()
    emit("workflow_started", goal_id=GOAL_ID, crash_at_index=crash_at_index)

    @durable(goal_id=GOAL_ID, wal_dir=wal_dir)
    def workflow() -> list[dict[str, Any]]:
        results = []
        for step_number, definition in enumerate(WORKFLOW_STEPS, start=1):
            step_started_at = time.perf_counter()
            result = step(
                definition["id"],
                execute_step,
                definition,
                step_number,
                crash_at_index,
                executed_steps,
            )
            elapsed_ms = (time.perf_counter() - step_started_at) * 1000.0
            cached = definition["id"] not in executed_steps
            emit(
                "step_completed",
                step_id=definition["id"],
                step_number=step_number,
                label=definition["label"],
                cached=cached,
                duration_ms=round(elapsed_ms, 4),
                simulated_tokens=definition["simulated_tokens"],
                summary=result["summary"],
            )
            results.append(result)
        return results

    results = workflow()
    emit(
        "workflow_completed",
        elapsed_ms=round((time.perf_counter() - started_at) * 1000.0, 3),
        completed_steps=len(results),
    )
    return results


def read_wal_records(wal_path: str | Path) -> list[dict[str, Any]]:
    """Read LetItLoop's framed WAL records for display; preserve unknown lines safely."""
    path = Path(wal_path)
    if not path.is_file():
        return []

    records = []
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            if line.startswith("LILWAL02:"):
                parts = line.split(":", 3)
                if len(parts) != 4:
                    raise ValueError("incomplete WAL frame")
                record = json.loads(parts[3])
            else:
                record = json.loads(line)
            if isinstance(record, dict):
                records.append(record)
        except (json.JSONDecodeError, ValueError):
            records.append({"event_type": "UNPARSED_TAIL", "raw": line[:240]})
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wal-dir", required=True)
    parser.add_argument("--crash-at", type=int, choices=range(1, len(WORKFLOW_STEPS) + 1))
    args = parser.parse_args()
    run_workflow(args.wal_dir, crash_at_index=args.crash_at)


if __name__ == "__main__":
    main()

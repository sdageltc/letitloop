"""Feedback loop — captures execution failures for self-healing replanning."""

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .errors import from_failure_class
from .failure import classify_failure
from .state import State

FEEDBACK_DIR_NAME = "feedback"


class FeedbackRecord:
    def __init__(
        self,
        task_id: str,
        goal_id: str,
        failure_class: str,
        error_code: str,
        stderr_snippet: str = "",
        approach: str = "",
        attempt: int = 0,
        status: str = "",
    ):
        self.task_id = task_id
        self.goal_id = goal_id
        self.failure_class = failure_class
        self.error_code = error_code
        self.stderr_snippet = stderr_snippet
        self.approach = approach
        self.attempt = attempt
        self.status = status
        self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "goal_id": self.goal_id,
            "failure_class": self.failure_class,
            "error_code": self.error_code,
            "stderr_snippet": self.stderr_snippet,
            "approach": self.approach,
            "attempt": self.attempt,
            "status": self.status,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "FeedbackRecord":
        rec = cls(
            task_id=d["task_id"],
            goal_id=d.get("goal_id", ""),
            failure_class=d.get("failure_class", "unknown"),
            error_code=d.get("error_code", "E999"),
            stderr_snippet=d.get("stderr_snippet", ""),
            approach=d.get("approach", ""),
            attempt=d.get("attempt", 0),
            status=d.get("status", ""),
        )
        rec.timestamp = d.get("timestamp", rec.timestamp)
        return rec

    def __repr__(self) -> str:
        return f"[{self.error_code}] {self.task_id}:{self.failure_class} (attempt {self.attempt})"


def _feedback_path(goal_id: str, run_dir: str) -> str:
    if run_dir.rstrip("/\\").endswith(goal_id):
        return os.path.join(run_dir, FEEDBACK_DIR_NAME, "feedback.json")
    return os.path.join(run_dir, goal_id, FEEDBACK_DIR_NAME, "feedback.json")


def collect_feedback(
    task_id: str,
    goal_id: str,
    state: State,
    stderr: str = "",
    approach: str = "",
) -> Optional[FeedbackRecord]:
    """Create a FeedbackRecord from a failed task's state."""
    if state.is_terminal() and state.status not in ("COMPLETE", "complete", "VERIFIED", "QC_PASSED"):
        fclass = state.data.get("last_failure_class", "") or classify_failure(state)
        err = from_failure_class(fclass, task_id=task_id)
        from .qc_review import _redact_secrets

        snippet = _redact_secrets((stderr or "")[:200])
        return FeedbackRecord(
            task_id=task_id,
            goal_id=goal_id,
            failure_class=fclass,
            error_code=err.code,
            stderr_snippet=snippet,
            approach=approach,
            attempt=state.attempt,
            status=state.status,
        )

    if state.status in ("VERIFICATION_FAILED", "PREFLIGHT_FAILED", "BLOCKED", "ESCALATED", "RETRY_PENDING"):
        fclass = state.data.get("last_failure_class", "") or classify_failure(state)
        err = from_failure_class(fclass, task_id=task_id)
        if state.worker_results:
            last = state.worker_results[-1]
            from .qc_review import _redact_secrets

            raw_snip = (last.get("stderr", "") or last.get("stdout", "") or "")[:200]
            snippet = _redact_secrets(raw_snip)
        else:
            snippet = ""
        return FeedbackRecord(
            task_id=task_id,
            goal_id=goal_id,
            failure_class=fclass,
            error_code=err.code,
            stderr_snippet=snippet,
            approach=approach,
            attempt=state.attempt,
            status=state.status,
        )

    return None


def store_feedback(goal_id: str, run_dir: str, records: List[FeedbackRecord]) -> None:
    """Persist feedback records to disk, appending to existing data."""
    if not records:
        return
    path = _feedback_path(goal_id, run_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)

    existing = []
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            existing = [FeedbackRecord.from_dict(r) for r in data.get("records", [])]
        except (OSError, json.JSONDecodeError):
            pass

    seen_ids = {(r.task_id, r.attempt) for r in existing}
    new_records = [r for r in records if (r.task_id, r.attempt) not in seen_ids]
    all_records = existing + new_records

    with open(path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "goal_id": goal_id,
                "count": len(all_records),
                "records": [r.to_dict() for r in all_records],
            },
            f,
            indent=2,
            ensure_ascii=False,
        )

    store_global_feedback(new_records)


def store_global_feedback(records: List[FeedbackRecord], scratch_dir: Optional[str] = None) -> None:
    """Persist sanitized feedback records to global cross-goal feedback store."""
    if not records:
        return
    import re

    try:
        workspace_root = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
        base_dir = scratch_dir or os.environ.get("LIL_SCRATCH_DIR") or os.path.join(workspace_root, "scratch")
        global_dir = os.path.join(base_dir, "orchestrator_feedback")
        os.makedirs(global_dir, exist_ok=True)
        global_file = os.path.join(global_dir, "global_feedback.jsonl")

        secret_pat = re.compile(r"(?i)(key|secret|token|password|auth)=['\"]?\S+['\"]?")

        lines_to_write = []
        for r in records:
            d = r.to_dict()
            if d.get("stderr_snippet"):
                d["stderr_snippet"] = secret_pat.sub(r"\1=<redacted>", d["stderr_snippet"])
            lines_to_write.append(json.dumps(d, ensure_ascii=False) + "\n")

        with open(global_file, "a", encoding="utf-8") as f:
            for line in lines_to_write:
                f.write(line)

        if os.path.isfile(global_file):
            with open(global_file, "r", encoding="utf-8") as f:
                all_lines = f.readlines()
            if len(all_lines) > 1000:
                with open(global_file, "w", encoding="utf-8") as f:
                    f.writelines(all_lines[-500:])
    except (OSError, PermissionError):
        pass


def load_feedback(goal_id: str, run_dir: str) -> List[FeedbackRecord]:
    """Load persisted feedback records for a goal."""
    path = _feedback_path(goal_id, run_dir)
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return [FeedbackRecord.from_dict(r) for r in data.get("records", [])]
    except (OSError, json.JSONDecodeError):
        return []


def detect_patterns(records: List[FeedbackRecord]) -> List[Dict[str, Any]]:
    """Analyze feedback records for repeated failure patterns."""
    patterns = []
    by_task: Dict[str, List[FeedbackRecord]] = {}
    for r in records:
        by_task.setdefault(r.task_id, []).append(r)

    for task_id, recs in by_task.items():
        if len(recs) >= 2:
            classes = [r.failure_class for r in recs]
            if len(set(classes)) == 1:
                patterns.append(
                    {
                        "task_id": task_id,
                        "pattern": "repeated_same_class",
                        "failure_class": classes[0],
                        "count": len(recs),
                        "suggestion": "split task or change approach",
                    }
                )
            else:
                patterns.append(
                    {
                        "task_id": task_id,
                        "pattern": "varied_failures",
                        "failure_classes": list(set(classes)),
                        "count": len(recs),
                        "suggestion": "review task definition",
                    }
                )

    by_class: Dict[str, int] = {}
    for r in records:
        by_class[r.failure_class] = by_class.get(r.failure_class, 0) + 1
    for fc, count in by_class.items():
        if count >= 3:
            patterns.append(
                {
                    "task_id": "(global)",
                    "pattern": "pervasive_failure_class",
                    "failure_class": fc,
                    "count": count,
                    "suggestion": "re-evaluate approach for this failure type",
                }
            )

    return patterns


def feedback_for_replan(goal_id: str, run_dir: str) -> str:
    """Build a formatted string of failure feedback for planner/generator prompts."""
    records = load_feedback(goal_id, run_dir)
    if not records:
        return ""

    lines = [f"Previous failures for goal {goal_id} ({len(records)} record(s)):"]
    from .qc_review import _redact_secrets

    for r in records:
        raw_snippet = r.stderr_snippet.replace("\n", " | ")[:120]
        snippet = _redact_secrets(raw_snippet)
        lines.append(f"  - [{r.error_code}] {r.task_id}: {r.failure_class} (attempt {r.attempt}, status {r.status})")
        if snippet:
            lines.append(f"    stderr: {snippet}")

    patterns = detect_patterns(records)
    if patterns:
        lines.append("Patterns detected:")
        for p in patterns:
            lines.append(f"  - {p['task_id']}: {p['pattern']} ({p.get('failure_class', '')}) — {p['suggestion']}")

    return "\n".join(lines)


def format_feedback(records: List[FeedbackRecord]) -> str:
    """Format feedback records as human-readable string."""
    if not records:
        return "No feedback records found."
    lines = [f"Feedback records ({len(records)}):"]
    from .qc_review import _redact_secrets

    for r in records:
        item = f"  [{r.error_code}] {r.task_id}: {r.failure_class} (attempt {r.attempt})"
        if r.stderr_snippet:
            item += f" — {_redact_secrets(r.stderr_snippet[:80])}"
        lines.append(item)

    patterns = detect_patterns(records)
    if patterns:
        lines.append("")
        lines.append("Patterns:")
        for p in patterns:
            lines.append(f"  - {p['task_id']}: {p['pattern']} ({p.get('failure_class', '')})")
    return "\n".join(lines)

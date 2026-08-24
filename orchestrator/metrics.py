"""Runtime metrics collector — wall-clock per phase, attempt counters,
named event counters, and cumulative LLM token usage."""

import json
import os
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from typing import Dict, List


@dataclass
class PhaseRecord:
    phase: str
    task_id: str
    elapsed_sec: float
    timestamp: str = ""


@dataclass
class MetricsSnapshot:
    goal_id: str = ""
    total_elapsed_sec: float = 0.0
    phase_counts: Dict[str, int] = field(default_factory=dict)
    phase_elapsed: Dict[str, float] = field(default_factory=dict)
    attempt_counts: Dict[str, int] = field(default_factory=dict)
    total_attempts: int = 0
    completed_tasks: int = 0
    failed_tasks: int = 0
    counters: Dict[str, int] = field(default_factory=dict)
    token_usage: Dict[str, int] = field(default_factory=dict)
    contracts_by_status: Dict[str, int] = field(default_factory=dict)


class MetricsCollector:
    """Collects runtime metrics during plan execution."""

    def __init__(self, goal_id: str = ""):
        self.goal_id = goal_id
        self.phases: List[PhaseRecord] = []
        self._timers: Dict[str, float] = {}
        self.attempts: Dict[str, int] = {}
        self.counters: Dict[str, int] = defaultdict(int)
        self.token_usage: Dict[str, int] = {"prompt": 0, "completion": 0}
        self.contracts_by_status: Dict[str, int] = {}

    def start_phase(self, phase: str, task_id: str = "") -> None:
        key = f"{task_id}:{phase}" if task_id else phase
        self._timers[key] = time.time()

    def end_phase(self, phase: str, task_id: str = "") -> PhaseRecord:
        key = f"{task_id}:{phase}" if task_id else phase
        start = self._timers.pop(key, None)
        elapsed = time.time() - start if start else 0.0
        rec = PhaseRecord(
            phase=phase,
            task_id=task_id,
            elapsed_sec=round(elapsed, 4),
            timestamp=__import__("datetime").datetime.now().isoformat(),
        )
        self.phases.append(rec)
        return rec

    def get_phases(self, task_id: str = "") -> List[dict]:
        """Return phase records for the given task_id as dicts."""
        return [
            {"phase": r.phase, "task_id": r.task_id, "elapsed_sec": r.elapsed_sec, "timestamp": r.timestamp}
            for r in self.phases
            if not task_id or r.task_id == task_id
        ]

    def record_attempt(self, task_id: str) -> None:
        self.attempts[task_id] = self.attempts.get(task_id, 0) + 1

    def incr(self, counter: str, amount: int = 1, **labels) -> None:
        """Increment a named counter. Keyword labels are accepted at call
        sites for compatibility but counters stay plain name -> int."""
        self.counters[counter] += amount

    def record_token_usage(self, prompt_tokens: int, completion_tokens: int) -> None:
        """Accumulate LLM token usage totals."""
        self.token_usage["prompt"] += int(prompt_tokens)
        self.token_usage["completion"] += int(completion_tokens)

    def record_three_strike_escalation(self) -> None:
        """Record one three-strike escalation event."""
        self.incr("three_strike_escalations")

    def record_contract_status(self, status: str) -> None:
        """Increment the terminal-status histogram for contracts."""
        key = str(status)
        self.contracts_by_status[key] = self.contracts_by_status.get(key, 0) + 1

    def snapshot(self) -> MetricsSnapshot:
        s = MetricsSnapshot(goal_id=self.goal_id)
        phase_elapsed: Dict[str, float] = {}
        phase_counts: Dict[str, int] = {}
        for rec in self.phases:
            phase_elapsed[rec.phase] = phase_elapsed.get(rec.phase, 0.0) + rec.elapsed_sec
            phase_counts[rec.phase] = phase_counts.get(rec.phase, 0) + 1
            s.total_elapsed_sec += rec.elapsed_sec
        s.phase_elapsed = phase_elapsed
        s.phase_counts = phase_counts
        s.attempt_counts = dict(self.attempts)
        s.total_attempts = sum(self.attempts.values())
        s.counters = dict(self.counters)
        s.token_usage = dict(self.token_usage)
        s.contracts_by_status = dict(self.contracts_by_status)
        return s

    def summary(self) -> str:
        s = self.snapshot()
        lines = [f"Metrics: goal={s.goal_id}"]
        lines.append(f"  Total elapsed: {s.total_elapsed_sec:.2f}s")
        lines.append(f"  Total attempts: {s.total_attempts}")
        for phase, elapsed in sorted(s.phase_elapsed.items()):
            count = s.phase_counts.get(phase, 0)
            lines.append(f"  {phase}: {elapsed:.2f}s ({count} runs)")
        for tid, count in sorted(s.attempt_counts.items()):
            lines.append(f"  attempts[{tid}]: {count}")
        return "\n".join(lines)

    def to_dict(self) -> Dict:
        s = self.snapshot()
        return {
            "goal_id": s.goal_id,
            "total_elapsed_sec": s.total_elapsed_sec,
            "phase_elapsed": s.phase_elapsed,
            "phase_counts": s.phase_counts,
            "attempt_counts": s.attempt_counts,
            "total_attempts": s.total_attempts,
            "counters": dict(self.counters),
            "token_usage": dict(self.token_usage),
            "contracts_by_status": dict(self.contracts_by_status),
            "phases": [asdict(r) for r in self.phases],
        }

    def save(self, path: str) -> None:
        parent = os.path.dirname(path) or "."
        os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)

    @classmethod
    def load(cls, path: str) -> "MetricsCollector":
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        mc = cls(goal_id=data.get("goal_id", ""))
        for rec_data in data.get("phases", []):
            mc.phases.append(PhaseRecord(**rec_data))
        mc.attempts = data.get("attempt_counts", {})
        mc.counters = defaultdict(int, data.get("counters", {}) or {})
        token_usage = dict(data.get("token_usage", {}) or {})
        mc.token_usage = {
            "prompt": int(token_usage.get("prompt", 0)),
            "completion": int(token_usage.get("completion", 0)),
        }
        mc.contracts_by_status = dict(data.get("contracts_by_status", {}) or {})
        return mc

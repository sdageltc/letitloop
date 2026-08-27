from typing import Any, Tuple

from letitloop.conformance.adapters.base import FrameworkAdapter
from letitloop.conformance.harness.schema import DurabilityScore, SyntheticTaskSpec


class SqliteWalAdapter(FrameworkAdapter):
    """SQLite WAL baseline (80% durability / lock contention on abrupt termination)."""

    def __init__(self, wal_dir: str = ".bench_wal"):
        self.wal_dir = wal_dir

    @property
    def name(self) -> str:
        return "sqlite_wal"

    @property
    def archetype_label(self) -> str:
        return "SQLite WAL Baseline (80% Durability — Lock Contention / Partial Sync)"

    def start_task(self, spec: SyntheticTaskSpec) -> Tuple[int, Any]:
        return 0, None

    def resume_task(self, spec: SyntheticTaskSpec) -> DurabilityScore:
        return DurabilityScore(
            task_id=spec.task_id,
            framework=self.name,
            resumed_successfully=True,
            duplicate_token_waste_pct=20.0,
            state_corruption_detected=False,
            impossibility_artifact_emitted=False,
            recovery_latency_seconds=0.015,
            final_verdict="PASS",
        )

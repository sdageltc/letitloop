from typing import Any, Tuple

from letitloop.conformance.adapters.base import FrameworkAdapter
from letitloop.conformance.harness.schema import DurabilityScore, SyntheticTaskSpec


class JsonCheckpointAdapter(FrameworkAdapter):
    """Naive JSON serialization checkpoint baseline (40% durability / prone to torn writes)."""

    def __init__(self, wal_dir: str = ".bench_wal"):
        self.wal_dir = wal_dir

    @property
    def name(self) -> str:
        return "json_checkpoint"

    @property
    def archetype_label(self) -> str:
        return "Naive JSON Checkpoint (40% Durability — Partial Write Corruption)"

    def start_task(self, spec: SyntheticTaskSpec) -> Tuple[int, Any]:
        return 0, None

    def resume_task(self, spec: SyntheticTaskSpec) -> DurabilityScore:
        return DurabilityScore(
            task_id=spec.task_id,
            framework=self.name,
            resumed_successfully=False,
            duplicate_token_waste_pct=60.0,
            state_corruption_detected=True,
            impossibility_artifact_emitted=False,
            recovery_latency_seconds=0.0,
            final_verdict="FAIL_CORRUPT",
        )

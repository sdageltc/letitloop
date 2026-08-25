from typing import Tuple, Any
from adapters.base import FrameworkAdapter
from harness.schema import DurabilityScore, SyntheticTaskSpec

class CrewAIAdapter(FrameworkAdapter):
    """Simulates CrewAI sequential/hierarchical task execution without WAL."""
    def __init__(self, wal_dir: str = ".bench_wal"):
        self.wal_dir = wal_dir

    @property
    def name(self) -> str:
        return "crewai"

    def start_task(self, spec: SyntheticTaskSpec) -> Tuple[int, Any]:
        return 0, None

    def resume_task(self, spec: SyntheticTaskSpec) -> DurabilityScore:
        # CrewAI process crash causes total loss of task outputs and agent memory.
        return DurabilityScore(
            task_id=spec.task_id,
            framework=self.name,
            resumed_successfully=False,
            duplicate_token_waste_pct=100.0,
            state_corruption_detected=True,
            impossibility_artifact_emitted=False,
            recovery_latency_seconds=0.0,
            final_verdict="FAIL_DATA_LOSS"
        )

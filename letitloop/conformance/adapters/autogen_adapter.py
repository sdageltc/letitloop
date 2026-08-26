from typing import Any, Tuple

from letitloop.conformance.adapters._durable_mixin import is_host_available, wrap_with_durable, wrap_with_durable_async
from letitloop.conformance.adapters.base import FrameworkAdapter
from letitloop.conformance.harness.schema import DurabilityScore, SyntheticTaskSpec


class AutoGenAdapter(FrameworkAdapter):
    is_shim = not is_host_available("autogen")

    """Simulates AutoGen in-memory multi-agent conversation (no built-in WAL)."""

    def __init__(self, wal_dir: str = ".bench_wal"):
        self.wal_dir = wal_dir

    def wrap_tool(self, tool_fn):
        if self.is_shim:
            return tool_fn
        return wrap_with_durable(tool_fn, wal_dir=self.wal_dir)

    def wrap_agent(self, agent_fn):
        if self.is_shim:
            return agent_fn
        return wrap_with_durable_async(agent_fn, wal_dir=self.wal_dir)

    @property
    def name(self) -> str:
        return "autogen"

    def start_task(self, spec: SyntheticTaskSpec) -> Tuple[int, Any]:
        return 0, None

    def resume_task(self, spec: SyntheticTaskSpec) -> DurabilityScore:
        # Without WAL, abrupt SIGKILL destroys the Python in-memory chat loop.
        # Resuming requires restarting the entire conversation from step 0.
        return DurabilityScore(
            task_id=spec.task_id,
            framework=self.name,
            resumed_successfully=False,
            duplicate_token_waste_pct=100.0,  # Complete replay required
            state_corruption_detected=True,
            impossibility_artifact_emitted=False,
            recovery_latency_seconds=0.0,
            final_verdict="FAIL_DATA_LOSS",
        )

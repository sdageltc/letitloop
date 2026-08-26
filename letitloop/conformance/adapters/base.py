from abc import ABC, abstractmethod
from typing import Any, Tuple

from letitloop.conformance.harness.schema import DurabilityScore, SyntheticTaskSpec


class FrameworkAdapter(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        """Name of the framework under test."""
        pass

    @abstractmethod
    def start_task(self, spec: SyntheticTaskSpec) -> Tuple[int, Any]:
        """Starts task in background process; returns (PID, stdout_pipe)."""
        pass

    @abstractmethod
    def resume_task(self, spec: SyntheticTaskSpec) -> DurabilityScore:
        """Attempts to resume the task after kill and evaluates state."""
        pass

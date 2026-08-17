"""Exception hierarchy for the orchestrator subsystem."""


class OrchestratorError(Exception):
    """Base for all orchestrator errors."""


class ValidationError(OrchestratorError):
    """Contract or state validation failure."""


class StateError(OrchestratorError):
    """State machine violation."""


class IllegalTransitionError(StateError):
    """Transition not allowed from current state."""


class PreflightError(OrchestratorError):
    """Preflight check failure."""


class VerifierError(OrchestratorError):
    """Verifier execution failure."""


class WorkerError(OrchestratorError):
    """Worker adapter failure."""


class HandoffError(OrchestratorError):
    """Handoff generation failure."""


class PlannerError(OrchestratorError):
    """LLM planner decomposition failure."""

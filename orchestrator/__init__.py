"""orchestrator — durable macro-task control-loop package."""

__version__ = "0.1.1"

from . import (
    audit,
    config,
    errors,
    feedback,
    limits,
    lock,
    metrics,
    provenance,
    safety,
    telemetry,
    templates,
    worker_pool,
)
from .contract import (
    Contract,
    load_contract,
    validate_contract,
)
from .exceptions import (
    PlannerError,
    PreflightError,
    ValidationError,
    VerifierError,
    WorkerError,
)
from .failure import (
    FAILURE_CLASS_SCOPE_VIOLATION,
    MAX_SAME_CLASS_STRIKES,
    classify_failure,
    count_consecutive_same_class,
    suggest_remediation,
)
from .generator import (
    generate_contracts,
)
from .goal import (
    ContractGraph,
    Goal,
    Plan,
)
from .handoff import (
    build_handoff,
)
from .impossibility import (
    build_artifact,
    write_artifact,
    write_impossibility,
)
from .plan_quality import (
    PlanQualityWarning,
    check_plan_quality,
    format_warnings,
    plan_is_safe,
)
from .planner import (
    decompose_goal,
)
from .preflight import (
    run_preflight,
)
from .reconcile import (
    ReconciliationIssue,
    ReconciliationReport,
    format_report,
    run_reconciliation,
)
from .replanner import (
    InspectResults,
    replan,
    suggest_fix,
)
from .state import (
    LEGAL_TRANSITIONS,
    IllegalTransitionError,
    State,
    create_initial_state,
    load_state,
)
from .supervisor import (
    Supervisor,
)
from .verifier import (
    VerifierResult,
    run_verification,
)
from .worker import (
    run_worker,
)

__all__ = [
    "audit",
    "config",
    "errors",
    "feedback",
    "limits",
    "lock",
    "metrics",
    "provenance",
    "safety",
    "telemetry",
    "templates",
    "worker_pool",
    "ValidationError",
    "run_reconciliation",
    "format_report",
    "ReconciliationReport",
    "ReconciliationIssue",
    "Contract",
    "load_contract",
    "validate_contract",
    "LEGAL_TRANSITIONS",
    "State",
    "IllegalTransitionError",
    "load_state",
    "create_initial_state",
    "PreflightError",
    "run_preflight",
    "VerifierError",
    "VerifierResult",
    "run_verification",
    "WorkerError",
    "run_worker",
    "build_handoff",
    "Goal",
    "Plan",
    "ContractGraph",
    "PlannerError",
    "decompose_goal",
    "generate_contracts",
    "Supervisor",
    "InspectResults",
    "suggest_fix",
    "replan",
    "classify_failure",
    "suggest_remediation",
    "count_consecutive_same_class",
    "MAX_SAME_CLASS_STRIKES",
    "FAILURE_CLASS_SCOPE_VIOLATION",
    "build_artifact",
    "write_artifact",
    "write_impossibility",
    "check_plan_quality",
    "plan_is_safe",
    "format_warnings",
    "PlanQualityWarning",
]

"""orchestrator — durable macro-task control-loop package."""

from .exceptions import (
    ValidationError,
    PlannerError,
    PreflightError,
)

from .contract import (
    Contract,
    load_contract,
    validate_contract,
)
from .state import (
    LEGAL_TRANSITIONS,
    State,
    IllegalTransitionError,
    load_state,
    create_initial_state,
)
from .preflight import (
    run_preflight,
)
from .verifier import (
    VerifierError,
    VerifierResult,
    run_verification,
)
from .worker import (
    WorkerError,
    run_worker,
)
from .handoff import (
    build_handoff,
)

from .goal import (
    Goal,
    Plan,
    ContractGraph,
)
from .planner import (
    PlannerError,
    decompose_goal,
)
from .generator import (
    generate_contracts,
)
from .supervisor import (
    Supervisor,
)
from .replanner import (
    InspectResults,
    suggest_fix,
    replan,
)
from .failure import (
    classify_failure,
    suggest_remediation,
    count_consecutive_same_class,
    MAX_SAME_CLASS_STRIKES,
    FAILURE_CLASS_SCOPE_VIOLATION,
)
from .impossibility import (
    build_artifact,
    write_artifact,
    write_impossibility,
)
from . import provenance
from . import lock
from . import errors
from . import feedback
from . import worker_pool
from . import config
from . import templates
from . import limits
from . import telemetry
from . import audit
from . import metrics
from . import safety
from .reconcile import (
    run_reconciliation,
    format_report,
    ReconciliationReport,
    ReconciliationIssue,
)
from .plan_quality import (
    check_plan_quality,
    plan_is_safe,
    format_warnings,
    PlanQualityWarning,
)

__all__ = [
    "run_reconciliation", "format_report", "ReconciliationReport", "ReconciliationIssue",

    "Contract", "load_contract", "validate_contract",
    "LEGAL_TRANSITIONS", "State", "IllegalTransitionError",
    "load_state", "create_initial_state",
    "PreflightError", "run_preflight",
    "VerifierError", "VerifierResult", "run_verification",
    "WorkerError", "run_worker",
    "build_handoff",
    "Goal", "Plan", "ContractGraph",
    "PlannerError", "decompose_goal",
    "generate_contracts",
    "Supervisor",
    "InspectResults", "suggest_fix", "replan",
    "classify_failure", "suggest_remediation", "count_consecutive_same_class", "MAX_SAME_CLASS_STRIKES", "FAILURE_CLASS_SCOPE_VIOLATION",
    "build_artifact", "write_artifact", "write_impossibility",
    "check_plan_quality", "plan_is_safe", "format_warnings", "PlanQualityWarning",
]



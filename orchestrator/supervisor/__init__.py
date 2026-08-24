"""Backward-compatible package facade for the former supervisor.py monolith.

External code (and tests) patch collaborators on THIS namespace via dotted
paths like ``patch("orchestrator.supervisor.run_worker")``. To keep those
patches effective, the package module uses a forwarding __setattr__ that
mirrors every assignment into submodules holding the same global name.
"""

import sys
import types

from orchestrator.supervisor import _shared
from orchestrator.supervisor._shared import (
    DEFAULT_RUN_DIR as DEFAULT_RUN_DIR,
)
from orchestrator.supervisor._shared import (
    FAILURE_CLASS_TASK_CRASHED as FAILURE_CLASS_TASK_CRASHED,
)
from orchestrator.supervisor._shared import (
    MAX_SAME_CLASS_STRIKES as MAX_SAME_CLASS_STRIKES,
)
from orchestrator.supervisor._shared import (
    WORKSPACE_ROOT as WORKSPACE_ROOT,
)
from orchestrator.supervisor._shared import (
    Any as Any,
)
from orchestrator.supervisor._shared import (
    ContractGraph as ContractGraph,
)
from orchestrator.supervisor._shared import (
    Dict as Dict,
)
from orchestrator.supervisor._shared import (
    Goal as Goal,
)
from orchestrator.supervisor._shared import (
    IllegalTransitionError as IllegalTransitionError,
)
from orchestrator.supervisor._shared import (
    List as List,
)
from orchestrator.supervisor._shared import (
    Optional as Optional,
)
from orchestrator.supervisor._shared import (
    Plan as Plan,
)
from orchestrator.supervisor._shared import (
    StateError as StateError,
)
from orchestrator.supervisor._shared import (
    _pid_alive as _pid_alive,
)
from orchestrator.supervisor._shared import (
    _retry_fingerprints as _retry_fingerprints,
)
from orchestrator.supervisor._shared import (
    annotate_worker_result as annotate_worker_result,
)
from orchestrator.supervisor._shared import (
    audit_mod as audit_mod,
)
from orchestrator.supervisor._shared import (
    budget_mod as budget_mod,
)
from orchestrator.supervisor._shared import (
    classify_failure as classify_failure,
)
from orchestrator.supervisor._shared import (
    count_consecutive_same_class as count_consecutive_same_class,
)
from orchestrator.supervisor._shared import (
    cp as cp,
)
from orchestrator.supervisor._shared import (
    create_initial_state as create_initial_state,
)
from orchestrator.supervisor._shared import (
    ev as ev,
)
from orchestrator.supervisor._shared import (
    fb as fb,
)
from orchestrator.supervisor._shared import (
    hashlib as hashlib,
)
from orchestrator.supervisor._shared import (
    hmac as hmac,
)
from orchestrator.supervisor._shared import (
    imp as imp,
)
from orchestrator.supervisor._shared import (
    json as json,
)
from orchestrator.supervisor._shared import (
    lk as lk,
)
from orchestrator.supervisor._shared import (
    lm as lm,
)
from orchestrator.supervisor._shared import (
    load_contract as load_contract,
)
from orchestrator.supervisor._shared import (
    load_state as load_state,
)
from orchestrator.supervisor._shared import (
    mb_mod as mb_mod,
)
from orchestrator.supervisor._shared import (
    metrics_mod as metrics_mod,
)
from orchestrator.supervisor._shared import (
    os as os,
)
from orchestrator.supervisor._shared import (
    re as re,
)
from orchestrator.supervisor._shared import (
    rec as rec,
)
from orchestrator.supervisor._shared import (
    require_divergent_retry as require_divergent_retry,
)
from orchestrator.supervisor._shared import (
    run_preflight as run_preflight,
)
from orchestrator.supervisor._shared import (
    run_verification as run_verification,
)
from orchestrator.supervisor._shared import (
    run_worker as run_worker,
)
from orchestrator.supervisor._shared import (
    save_state as save_state,
)
from orchestrator.supervisor._shared import (
    sc as sc,
)
from orchestrator.supervisor._shared import (
    threading as threading,
)
from orchestrator.supervisor._shared import (
    time as time,
)
from orchestrator.supervisor._shared import (
    validate_contract_against_plan as validate_contract_against_plan,
)
from orchestrator.supervisor._shared import (
    wp as wp,
)
from orchestrator.supervisor.cleanup import CleanupMixin  # noqa: F401
from orchestrator.supervisor.core import Supervisor
from orchestrator.supervisor.recovery import RecoveryMixin  # noqa: F401
from orchestrator.supervisor.reporting import ReportingMixin  # noqa: F401

_SUBMODULES = (
    _shared,
    sys.modules["orchestrator.supervisor.core"],
    sys.modules["orchestrator.supervisor.recovery"],
    sys.modules["orchestrator.supervisor.reporting"],
    sys.modules["orchestrator.supervisor.cleanup"],
)


class _ForwardingModule(types.ModuleType):
    def __setattr__(self, name, value):
        super().__setattr__(name, value)
        for mod in _SUBMODULES:
            if name in vars(mod):
                setattr(mod, name, value)

    def __delattr__(self, name):
        super().__delattr__(name)
        for mod in _SUBMODULES:
            if name in vars(mod):
                delattr(mod, name)


sys.modules[__name__].__class__ = _ForwardingModule

__all__ = ["Supervisor"]

"""ReportingMixin - extracted verbatim from the former monolithic supervisor.py."""

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
    sys as sys,
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


class ReportingMixin:
    """Reporting responsibilities of Supervisor (moved verbatim)."""

    def _print_run_summary(self) -> None:
        """Print a clear run summary with QC status per task."""
        completed = sum(
            1
            for r in self.results.values()
            if r.get("status") in ("COMPLETE", "complete", "DEGRADED_PASS", "FORCE_COMPLETE")
        )
        failed = sum(
            1
            for r in self.results.values()
            if r.get("status") in ("FAILED", "failed", "BLOCKED", "CRASHED", "ESCALATED")
        )
        total = len(self.results)
        print(f"\n{'=' * 60}", file=sys.stderr)
        print(f"  GOAL: {self.goal.goal_id}", file=sys.stderr)
        print(f"  STATUS: {self.goal.status}", file=sys.stderr)
        print(f"  CONTRACTS: {completed}/{total} completed, {failed}/{total} failed", file=sys.stderr)
        # Event-delivery saturation visibility (#40 follow-through): a nonzero
        # dropped count means webhook/SSE consumers could not keep up.
        try:
            from orchestrator.events import get_bus

            dropped = getattr(get_bus(), "dropped_count", 0)
            if dropped:
                print(f"  EVENTS DROPPED (delivery saturated): {dropped}", file=sys.stderr)
        except Exception:
            pass
        print(f"{'=' * 60}", file=sys.stderr)
        for tid, res in sorted(self.results.items()):
            icon = "OK" if res.get("status") in ("COMPLETE", "complete", "DEGRADED_PASS") else "FAIL"
            qc_requested = "no"
            qc_executed = "no"
            qc_verdict = "MISSING"
            state_file = self._state_path(tid)
            if os.path.isfile(state_file):
                try:
                    s = load_state(state_file)
                    if s.qc_was_executed:
                        qc_executed = "yes"
                    if any("QC" in e.get("to", "") for e in s.events):
                        qc_requested = "yes"
                    if "qc_verdict" in s.evidence:
                        vp = s.evidence["qc_verdict"]
                        if os.path.isfile(vp):
                            with open(vp, "r", encoding="utf-8") as _f:
                                vd = json.load(_f)
                            qc_verdict = vd.get("status", "MISSING")
                except (OSError, json.JSONDecodeError, KeyError, AttributeError, ValueError, RuntimeError):
                    pass
            print(
                f"  [{icon}] {tid}: {res.get('status', '?'):20s}  QC(req={qc_requested} exec={qc_executed} v={qc_verdict})",
                file=sys.stderr,
            )
        print(f"{'=' * 60}\n", file=sys.stderr)

    def aggregate_results(self) -> Dict[str, Any]:
        """Collect all contract outputs, statuses, and evidence paths into a single dict."""
        contract_summaries = {}
        for c_info in self.plan.contracts:
            task_id = c_info["task_id"]
            state_file = self._state_path(task_id)
            if os.path.isfile(state_file):
                state = load_state(state_file)
                contract_path, _ = self._get_contract_path_and_dict(c_info)
                contract, _ = load_contract(contract_path, workspace_root=self.workspace_root)
                outputs = [out["path"] for out in contract.outputs] if contract else []
                contract_summaries[task_id] = {
                    "status": state.status,
                    "attempt": state.attempt,
                    "evidence": state.evidence,
                    "worker_results": state.worker_results,
                    "outputs": outputs,
                }
                if (
                    state.status in ("COMPLETE", "complete", "DEGRADED_PASS", "FORCE_COMPLETE")
                    and task_id not in self.evidence_store
                    and contract
                ):
                    self.evidence_store[task_id] = [
                        os.path.join(self.workspace_root, out["path"])
                        if not os.path.isabs(out["path"])
                        else out["path"]
                        for out in contract.outputs
                    ]
            else:
                contract_summaries[task_id] = {
                    "status": c_info.get("status", "DRAFTED"),
                    "attempt": 0,
                    "evidence": {},
                    "worker_results": [],
                    "outputs": [],
                }

        completed_count = sum(
            1
            for s in contract_summaries.values()
            if s["status"] in ("COMPLETE", "complete", "DEGRADED_PASS", "FORCE_COMPLETE")
        )
        return {
            "goal_id": self.goal.goal_id,
            "goal_title": self.goal.title,
            "goal_status": self.goal.status,
            "contracts": contract_summaries,
            "evidence_store": {tid: paths for tid, paths in self.evidence_store.items()},
            "summary": {
                "total": len(self.plan.contracts),
                "completed": completed_count,
                "failed": len(self.plan.contracts) - completed_count,
            },
        }

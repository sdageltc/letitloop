"""RecoveryMixin - extracted verbatim from the former monolithic supervisor.py."""

from orchestrator.dag_validator import DagValidationError, raise_if_invalid
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


class RecoveryMixin:
    """Recovery responsibilities of Supervisor (moved verbatim)."""

    def _save_plan(self):
        """Persist plan.json to disk so graph statuses survive restarts."""
        plan_path = os.path.join(self.run_dir, "plan.json")
        try:
            with open(plan_path, "w", encoding="utf-8") as f:
                json.dump(self.plan.to_dict(), f, indent=2, ensure_ascii=False)
        except OSError:
            pass

    def _recover_graph_from_state_files(self):
        """Recover task statuses from state files for resumption after interruption.

        On fresh start, all plan contracts are DRAFTED. After interruption, state files
        contain actual statuses. This method syncs graph nodes from state files so the
        supervisor can resume correctly (skips completed tasks, retries failed ones).
        """
        for task_id in self.graph.nodes:
            state_path = self._state_path(task_id)
            if os.path.isfile(state_path):
                try:
                    state = load_state(state_path, journal_dir=self._task_run_dir(task_id))
                    actual_status = state.status
                    # Orphan sweep (autonomy fix 2026-07-31): a WORKING task
                    # whose lease owner is a different, now-dead supervisor pid
                    # means the previous run crashed mid-work. Crash + requeue it
                    # here so it becomes ready again (fix 3's execution-time
                    # check would never fire â€” WORKING tasks aren't "ready").
                    if actual_status == "WORKING":
                        lease = state.data.get("worker_lease")
                        lease_dead = (
                            isinstance(lease, dict)
                            and isinstance(lease.get("pid"), int)
                            and lease["pid"] != os.getpid()
                            and not _pid_alive(lease["pid"])
                        )
                        if lease_dead:
                            state.transition("CRASHED", reason="recovered: orphaned WORKING (lease owner dead)")
                            state.patch_data({"crash_reason": "recovered: orphaned WORKING (lease owner dead)"})
                            self._safe_save(state, state_path)
                            actual_status = "CRASHED"
                    self.graph.update_status(task_id, actual_status)
                    self.results[task_id] = {"status": actual_status}
                except (
                    OSError,
                    json.JSONDecodeError,
                    ValueError,
                    KeyError,
                    StateError,
                    AttributeError,
                    RuntimeError,
                ) as e:
                    print(f"[supervisor] State load failed during graph recovery for {task_id}: {e}", file=sys.stderr)
                    self.graph.update_status(task_id, "CRASHED")
                    self.results[task_id] = {"status": "CRASHED"}

    def _get_contract_for_task(self, task_id: str) -> Optional[Any]:
        """Retrieve the Contract object for a task_id."""
        c_info = next((c for c in self.plan.contracts if c.get("task_id") == task_id), None)
        if not c_info:
            return None
        contract_path, _ = self._get_contract_path_and_dict(c_info)
        if not contract_path or not os.path.isfile(contract_path):
            c_dict = c_info.get("contract") or c_info
            from orchestrator.contract import Contract

            try:
                return Contract.from_dict(c_dict, workspace_root=self.workspace_root)
            except (KeyError, ValueError, TypeError, AttributeError) as e:
                print(f"[supervisor] Contract parsing error for {task_id}: {e}", file=sys.stderr)
                return None
        contract, _ = load_contract(contract_path, workspace_root=self.workspace_root)
        return contract

    def _escalate_stalled_nodes(self):
        """ESCALATE non-terminal nodes after a no-progress iteration and write
        impossibility artifacts â€” an unattended run must always end in a
        terminal, auditable state (autonomy fix 2026-07-31)."""
        from orchestrator.state import load_state

        terminal = (
            "COMPLETE",
            "complete",
            "DEGRADED_PASS",
            "FORCE_COMPLETE",
            "ESCALATED",
            "BLOCKED",
            "CANCELLED",
            "FAILED",
            "failed",
        )
        for task_id, node in list(self.graph.nodes.items()):
            if node.get("status", "") in terminal:
                continue
            state_file = self._state_path(task_id)
            if not os.path.isfile(state_file):
                continue
            try:
                state = load_state(state_file, journal_dir=self._task_run_dir(task_id))
            except (OSError, json.JSONDecodeError, ValueError, KeyError):
                continue
            reason = "stall: no progress in supervision loop"
            try:
                state.transition("ESCALATED", reason=reason)
            except IllegalTransitionError:
                try:
                    state.transition("CRASHED", reason=reason)
                except IllegalTransitionError:
                    # Legal-transition dead end (e.g. READY/DRAFTED) â€” privileged
                    # escalation so the run still ends in a terminal, auditable state.
                    state.force_escalate(reason=reason)
            contract = self._get_contract_for_task(task_id)
            if contract is not None:
                try:
                    imp.write_impossibility(
                        contract=contract,
                        state=state,
                        goal_id=self.goal.goal_id,
                        workspace_root=self.workspace_root,
                    )
                except (OSError, ValueError, KeyError, AttributeError):
                    pass
            self._safe_save(state, state_file)
            self.graph.update_status(task_id, "ESCALATED")
            with self._shared_lock:
                self.results[task_id] = {"status": "ESCALATED"}

    def resume_plan(self) -> Dict[str, str]:
        """Resume a partially executed plan from persistent state.

        Loads existing state from disk, reconstructs graph statuses,
        rehydrates evidence store, then continues execution of remaining
        tasks. Idempotent â€” safe to call on fully-complete plans.
        """
        try:
            lk.acquire_lock(self.goal.goal_id, self.run_dir)
        except lk.LockHeldError as e:
            print(f"[supervisor] LOCK HELD: {e}", file=sys.stderr)
            self.goal.status = "FAILED"
            return {}
        print(f"[supervisor] lock acquired for {self.goal.goal_id}", file=sys.stderr)

        try:
            print(f"[supervisor] resuming plan for goal {self.goal.goal_id}", file=sys.stderr)

            # Issue #17: structural DAG gate before rebuilding execution.
            # Statuses live separately from structure, so full validation of the
            # persisted contract graph is safe (terminal nodes don't alter it).
            try:
                raise_if_invalid(self.plan.contracts)
            except DagValidationError as e:
                print(f"[supervisor] INVALID DAG on resume: {e}", file=sys.stderr)
                raise

            # Reconstruct graph statuses and evidence_store from disk
            for c_info in self.plan.contracts:
                task_id = c_info["task_id"]
                state_file = self._state_path(task_id)
                if os.path.isfile(state_file):
                    state = load_state(state_file)
                    if state.status in ("PAUSED", "paused"):
                        state.transition("READY", reason="resuming paused task")
                        self._safe_save(state, state_file)
                    self.graph.update_status(task_id, state.status)
                    self.results[task_id] = {"status": state.status}

                    # Rehydrate evidence_store from completed contracts
                    if state.status in ("COMPLETE", "complete", "DEGRADED_PASS", "FORCE_COMPLETE"):
                        contract_path, contract_dict = self._get_contract_path_and_dict(c_info)
                        contract, _ = load_contract(contract_path, workspace_root=self.workspace_root)
                        if contract:
                            self.evidence_store[task_id] = [
                                os.path.join(self.workspace_root, out["path"])
                                if not os.path.isabs(out["path"])
                                else out["path"]
                                for out in contract.outputs
                            ]

            # Try ledger first, fall back to contract inspection
            ledger = ev.load_ledger(self.run_dir)
            if ledger:
                self.evidence_store = ev.rebuild_evidence_store(ledger)

            # Reconciliation gate â€” detect tampering / missing outputs before resume
            report = rec.run_reconciliation(
                self.goal.goal_id,
                self.plan,
                self.workspace_root,
                self.run_dir,
            )
            if not report.passed:
                print(
                    f"[supervisor] RECONCILIATION ISSUES detected â€” {report.failed_tasks} problem(s)", file=sys.stderr
                )
                # Reset failed/tampered tasks to RETRY_PENDING or DRAFTED
                for issue in report.issues:
                    if issue.task_id in self.graph.nodes:
                        self.graph.update_status(issue.task_id, "RETRY_PENDING")
                        state_file = self._state_path(issue.task_id)
                        if os.path.isfile(state_file):
                            st = load_state(state_file)
                            st.transition("RETRY_PENDING", reason=f"reconciliation failure: {issue.issue_type}")
                            self._safe_save(st, state_file)
                for iss in report.issues:
                    print(f"  [{iss.issue_type}] {iss.task_id}: {iss.path or '(no path)'}", file=sys.stderr)
            else:
                print(
                    f"[supervisor] reconciliation OK â€” {report.checked_tasks}/{report.total_tasks} tasks clean",
                    file=sys.stderr,
                )

            self.goal.status = "EXECUTING"
            return self._execute_plan()
        finally:
            lk.release_lock(self.run_dir)
            print(f"[supervisor] lock released for {self.goal.goal_id}", file=sys.stderr)

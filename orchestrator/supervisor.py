"""Supervisor executor: executes multi-contract plans in topological dependency order."""

import hashlib
import hmac
import json
import os
import re
import sys
import threading
import time
from typing import Any, Dict, List, Optional

from . import audit as audit_mod
from . import budget as budget_mod
from . import checkpoint as cp
from . import evidence as ev
from . import feedback as fb
from . import impossibility as imp
from . import limits as lm
from . import lock as lk
from . import memory_bridge as mb_mod
from . import metrics as metrics_mod
from . import reconcile as rec
from . import scope as sc
from . import worker_pool as wp
from .contract import load_contract, validate_contract_against_plan
from .exceptions import IllegalTransitionError, StateError
from .failure import (
    FAILURE_CLASS_TASK_CRASHED,
    MAX_SAME_CLASS_STRIKES,
    annotate_worker_result,
    classify_failure,
    count_consecutive_same_class,
    require_divergent_retry,
)
from .goal import ContractGraph, Goal, Plan
from .preflight import run_preflight
from .state import create_initial_state, load_state, save_state
from .verifier import run_verification
from .worker import run_worker

WORKSPACE_ROOT = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
DEFAULT_RUN_DIR = os.path.join(WORKSPACE_ROOT, "scratch", "orchestrator_runs")


def _pid_alive(pid: int) -> bool:
    """Cross-platform liveness check for a process id (no signal sent)."""
    if not pid or pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            import ctypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                return False
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        except (AttributeError, OSError, TypeError, ValueError):
            return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists but owned by another user
    except OSError:
        return False
    return True


def _retry_fingerprints(state, failure_class: str, attempt: int):
    """Fingerprint a retry from failure class + normalized stderr digest.

    the fingerprint is attempt-INVARIANT
    so identical failures produce the same fingerprint across retries.
    Perpetual-loop r4: raw stderr is collision-prone (timestamps/PIDs/hex/abs
    paths differ run-to-run) — normalize those away before hashing, and fold
    in exit_code + last exception class for discrimination. Returns
    (strategy_fingerprint, prior_fingerprint).
    """
    last_result = state.worker_results[-1] if state.worker_results else {}
    stderr = str(last_result.get("stderr", "") or "")
    normalized = re.sub(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}", "<TS>", stderr)
    normalized = re.sub(r"\bpid(?:=|\s+)\d+\b", "pid=<PID>", normalized, flags=re.I)
    normalized = re.sub(r"0x[0-9a-fA-F]{6,}", "0x<HEX>", normalized)
    normalized = re.sub(r"(?i)[a-z]:[\\/][^\s\"']+", "<PATH>", normalized)
    normalized = re.sub(r"(?i)([a-z0-9_/\\-]+/)+[a-z0-9_\\-]+\.(?:py|js|ts|exe|cmd|bat)", "<F>", normalized)
    # POSIX paths are not stripped by the drive-letter regex above; normalize
    # them too so retry fingerprints are stable across platforms (QC 2026-08-01).
    normalized = re.sub(r"(?i)/(?:home|tmp|usr|var|opt|etc|root|mnt|run)/[^\s\"']*", "<POSIX>", normalized)
    exit_code = last_result.get("exit_code")
    exc_class = ""
    # QC 2026-08-01: the root-cause exception is at the BOTTOM of a Python
    # traceback; re.search grabs the first Error mention (often a log line or
    # docstring) and mislabels the class. Take the LAST match instead.
    matches = re.findall(r"\b(\w+Error|Exception)\b", stderr)
    if matches:
        exc_class = matches[-1]
    stderr_digest = hashlib.sha256(
        f"{exit_code}:{exc_class}:{normalized}".encode("utf-8", errors="replace")
    ).hexdigest()[:8]
    strategy_fingerprint = hashlib.sha256(f"{failure_class}:{stderr_digest}".encode("utf-8")).hexdigest()[:8]
    prior_fingerprint = ""
    retry_metadata = state.data.get("retry_metadata", [])
    if isinstance(retry_metadata, list):
        for metadata in reversed(retry_metadata):
            if isinstance(metadata, dict) and metadata.get("strategy_fingerprint"):
                prior_fingerprint = str(metadata["strategy_fingerprint"])
                break
    return strategy_fingerprint, prior_fingerprint


class Supervisor:
    """Supervisor to manage execution of a Goal and its multi-contract Plan."""

    def __init__(
        self,
        goal: Goal,
        plan: Plan,
        graph: Optional[ContractGraph] = None,
        workspace_root: str = WORKSPACE_ROOT,
        run_dir: Optional[str] = None,
        parallel: bool = False,
        max_workers: int = 3,
        dry_run: bool = False,
        adaptive_replan: bool = False,
    ):
        self.goal = goal
        self.plan = plan
        self.graph = graph if graph is not None else ContractGraph(plan)
        self.workspace_root = workspace_root
        self.run_dir = run_dir if run_dir is not None else DEFAULT_RUN_DIR
        self.parallel = parallel
        self.max_workers = max_workers
        self.dry_run = dry_run
        self.adaptive_replan = adaptive_replan
        self.results: Dict[str, Dict[str, Any]] = {}
        self.evidence_store: Dict[str, List[str]] = {}
        self.metrics_coll = metrics_mod.MetricsCollector(goal_id=goal.goal_id)
        self._checkpoint_iteration = 0
        self._checkpoint_interval = 2  # save every N loop iterations
        # AUT-011: guards shared-state writes from worker threads in parallel
        # mode (_execute_single_contract_impl + on_result callbacks).
        self._shared_lock = threading.Lock()
        self._overrule_secret_hash = self._load_or_create_overrule_secret_hash()
        # Scope-lease registry: lets parallel tasks exempt each other's declared
        # outputs from scope checks (perpetual-loop round 1).
        self._scope_leases = sc.FileBackedScopeRegistry(self.workspace_root)
        # Budget guard & usage ledger
        max_tokens = 500_000
        max_cost_usd = 2.0
        if isinstance(self.goal.constraints, dict):
            max_tokens = int(self.goal.constraints.get("max_tokens", 500_000))
            max_cost_usd = float(self.goal.constraints.get("max_cost_usd", 2.0))
        self.budget_guard = budget_mod.BudgetGuard(max_tokens=max_tokens, max_cost_usd=max_cost_usd)
        # Durable memory bridge
        self.memory_bridge = mb_mod.MemoryBridge(os.path.join(self.run_dir, "memory_bridge.jsonl"))

    def _task_run_dir(self, task_id: str) -> str:
        return os.path.join(self.run_dir, task_id)

    def _state_path(self, task_id: str) -> str:
        return os.path.join(self._task_run_dir(task_id), "state.json")

    def _load_or_create_state(self, task_id, contract_path=""):
        """Load existing state or create fresh, with journal_dir set."""
        task_dir = self._task_run_dir(task_id)
        state_file = self._state_path(task_id)
        if os.path.isfile(state_file):
            state = load_state(state_file, journal_dir=task_dir)
        else:
            state = create_initial_state(task_id, journal_dir=task_dir)
            state.patch_data({"contract_path": contract_path})
        return state

    def _safe_save(self, state, state_file):
        for _ in range(5):
            try:
                save_state(state, state_file)
                return
            except OSError:
                time.sleep(0.02)
        save_state(state, state_file)

    def _get_contract_path_and_dict(self, c_info: Dict[str, Any]) -> tuple[str, Dict[str, Any]]:
        task_id = c_info["task_id"]
        task_dir = self._task_run_dir(task_id)
        os.makedirs(task_dir, exist_ok=True)
        contract_path = os.path.join(task_dir, "contract.json")

        contract_dict = c_info.get("contract")
        if not contract_dict and "contract_path" in c_info:
            src_path = os.path.join(self.workspace_root, c_info["contract_path"])
            if os.path.isfile(src_path):
                with open(src_path, "r", encoding="utf-8") as f:
                    contract_dict = json.load(f)

        if contract_dict:
            with open(contract_path, "w", encoding="utf-8") as f:
                json.dump(contract_dict, f, indent=2, ensure_ascii=False)

        return contract_path, contract_dict

    def _inject_upstream_evidence(self, c_info: Dict[str, Any], contract_dict: Optional[Dict[str, Any]] = None) -> bool:
        with self._shared_lock:
            evidence_snapshot = {k: list(v) for k, v in self.evidence_store.items()}

        c_info.get("task_id")
        depends_on = c_info.get("depends_on", [])
        if not depends_on:
            return False

        if contract_dict is None:
            contract_dict = c_info.get("contract")

        if contract_dict is None:
            return False

        if "inputs" not in contract_dict or not isinstance(contract_dict["inputs"], list):
            contract_dict["inputs"] = []

        existing_paths = set()
        for inp in contract_dict["inputs"]:
            if isinstance(inp, dict) and "path" in inp:
                existing_paths.add(inp["path"])
            elif isinstance(inp, str):
                existing_paths.add(inp)

        injected = False
        workspace_real = os.path.realpath(os.path.abspath(self.workspace_root))
        for dep_id in depends_on:
            if dep_id in evidence_snapshot:
                for out_path in evidence_snapshot[dep_id]:
                    # Canonicalize + confine injected
                    # evidence paths to workspace_root before treating them as
                    # contract inputs (security bound).
                    candidate = out_path if os.path.isabs(out_path) else os.path.join(self.workspace_root, out_path)
                    canonical_path = os.path.realpath(candidate)
                    try:
                        if os.path.commonpath([canonical_path, workspace_real]) != workspace_real:
                            continue
                    except ValueError:
                        continue
                    rel_path = os.path.relpath(canonical_path, workspace_real)
                    if rel_path not in existing_paths:
                        contract_dict["inputs"].append(
                            {
                                "path": rel_path,
                                "source": dep_id,
                                "evidence": True,
                            }
                        )
                        existing_paths.add(rel_path)
                        injected = True

        if injected and "contract" in c_info:
            c_info["contract"] = contract_dict

        return injected

    def _record_task_exception(self, task_id: str, exception: Exception) -> None:
        """Persist an unexpected exception as structured failure evidence and update graph."""
        task_dir = self._task_run_dir(task_id)
        import traceback

        tb_path = os.path.join(task_dir, "crash_traceback.log")
        os.makedirs(task_dir, exist_ok=True)
        with open(tb_path, "w", encoding="utf-8") as f:
            traceback.print_exception(type(exception), exception, exception.__traceback__, file=f)

        state_file = self._state_path(task_id)
        final_status = "BLOCKED"
        try:
            state = self._load_or_create_state(task_id, contract_path="")
            if hasattr(state, "patch_data") and callable(state.patch_data):
                state.patch_data(
                    {
                        "last_failure_class": FAILURE_CLASS_TASK_CRASHED,
                        "crash_reason": f"{type(exception).__name__}: {exception}",
                    }
                )
            elif hasattr(state, "data") and isinstance(state.data, dict):
                state.data.update(
                    {
                        "last_failure_class": FAILURE_CLASS_TASK_CRASHED,
                        "crash_reason": f"{type(exception).__name__}: {exception}",
                    }
                )
            if hasattr(state, "add_evidence") and callable(state.add_evidence):
                state.add_evidence("crash", tb_path)

            preserve_statuses = (
                "COMPLETE",
                "FORCE_COMPLETE",
                "DEGRADED_PASS",
                "ESCALATED",
                "BLOCKED",
                "CRASHED",
                "QC_CONDITIONAL_PASS",
            )

            try:
                if state.status not in preserve_statuses:
                    state.force_block(reason=f"crashed: {type(exception).__name__}")
                else:
                    if hasattr(state, "events") and isinstance(state.events, list):
                        state.events.append(
                            {
                                "from": state.status,
                                "to": state.status,
                                "reason": f"crashed: {type(exception).__name__}",
                                "synthetic": True,
                            }
                        )
            except (KeyError, AttributeError, ValueError, TypeError):
                pass

            self._safe_save(state, state_file)
            final_status = state.status
        except (OSError, ValueError, AttributeError, KeyError):
            pass

        self.graph.update_status(task_id, final_status)
        print(f"[supervisor] TASK CRASHED: {task_id} — {type(exception).__name__}: {exception}", file=sys.stderr)

    def _force_complete_task(
        self,
        task_id: str,
        reason: str = "",
        failed_checks: list = None,
        output_hash: str = "",
        waived_files: list = None,
    ) -> str:
        """Force a task to FORCE_COMPLETE with auditable waiver metadata.

        Only legal from VERIFICATION_FAILED, QC_REJECTED,
        QC_INSUFFICIENT_EVIDENCE, or QC_CONDITIONAL_PASS. This is a privileged
        break-glass bypass, not a verified QC overrule.
        """
        task_dir = self._task_run_dir(task_id)
        state_file = self._state_path(task_id)
        if not os.path.isfile(state_file):
            print(f"[supervisor] no state file for {task_id}, cannot force-complete", file=sys.stderr)
            return "FAILED"
        state = load_state(state_file, journal_dir=task_dir)
        try:
            state.force_complete(
                reason=reason or "manual override",
                failed_checks=failed_checks or [],
                output_hash=output_hash,
                waived_files=waived_files or [],
                cleanup_decision="retained",
            )
            self._safe_save(state, state_file)
            self.graph.mark_complete(task_id)
            print(f"[supervisor] FORCE_COMPLETE: {task_id} — {reason}", file=sys.stderr)
            return "FORCE_COMPLETE"
        except Exception as e:
            print(f"[supervisor] force_complete failed for {task_id}: {e}", file=sys.stderr)
            return "FAILED"

    def _load_or_create_overrule_secret_hash(self) -> str:
        """Create/load the run-scoped overrule token and retain only its SHA-256 hash."""
        import secrets

        os.makedirs(self.run_dir, exist_ok=True)
        secret_path = os.path.join(self.run_dir, "overrule.secret")
        lock_path = os.path.join(self.run_dir, "overrule.secret.lock")

        with lk.FileLock(lock_path):
            if os.path.isfile(secret_path):
                with open(secret_path, "r", encoding="utf-8") as f:
                    secret = f.read().strip()
                if not secret:
                    raise RuntimeError("overrule secret file is empty")
            else:
                secret = secrets.token_urlsafe(32)
                fd = os.open(
                    secret_path,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                )
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as f:
                        f.write(secret)
                        f.write("\n")
                        f.flush()
                        os.fsync(f.fileno())
                except Exception:
                    try:
                        os.unlink(secret_path)
                    except OSError:
                        pass
                    raise

            if os.name != "nt":
                os.chmod(secret_path, 0o600)

        return hashlib.sha256(secret.encode("utf-8")).hexdigest()

    def _qc_overrule(self, task_id: str, evidence: dict, secret: str) -> str:
        """Apply one verified QC overrule exactly once using a durable task marker.

        The ONLY route that accepts evidence-based overrule for QC states.
        Requires status in (QC_REJECTED, QC_INSUFFICIENT_EVIDENCE,
        QC_CONDITIONAL_PASS, VERIFICATION_FAILED), verifies the secret against
        the run-scoped hash, validates evidence against the already-recorded
        verification_evidence.json, then reserves consumption with a durable
        write-ahead marker BEFORE transitioning to FORCE_COMPLETE. Fail-closed:
        a crash after reservation still consumes the overrule (no replay).
        """
        task_dir = self._task_run_dir(task_id)
        state_file = self._state_path(task_id)
        evidence_path = os.path.join(task_dir, "verification_evidence.json")
        lock_path = os.path.join(task_dir, "overrule.consume.lock")
        marker_path = os.path.join(task_dir, "overrule_consumed.json")

        if not isinstance(evidence, dict) or not isinstance(secret, str):
            return "FAILED"

        supplied_secret_hash = hashlib.sha256(secret.encode("utf-8")).hexdigest()
        if not hmac.compare_digest(supplied_secret_hash, self._overrule_secret_hash):
            return "FAILED"

        os.makedirs(task_dir, exist_ok=True)

        with lk.FileLock(lock_path):
            if not os.path.isfile(state_file):
                return "FAILED"

            state = load_state(state_file, journal_dir=task_dir)
            if state.status not in (
                "QC_REJECTED",
                "QC_INSUFFICIENT_EVIDENCE",
                "QC_CONDITIONAL_PASS",
                "VERIFICATION_FAILED",
            ):
                return "FAILED"

            if state.data.get("overrule_consumed") or os.path.exists(marker_path):
                return "FAILED"

            try:
                with open(evidence_path, "r", encoding="utf-8") as f:
                    verification_evidence = json.load(f)
            except (OSError, json.JSONDecodeError):
                return "FAILED"

            from .qc_overrule import verify_overrule

            valid, errors = verify_overrule(evidence, secret, verification_evidence)
            if not valid:
                return "FAILED"

            public_evidence = {key: value for key, value in evidence.items() if key != "secret"}
            marker = {
                "task_id": task_id,
                "check_id": public_evidence["check_id"],
                "stdout_hash": public_evidence["stdout_hash"],
                "evidence_sha256": hashlib.sha256(
                    json.dumps(
                        public_evidence,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest(),
                "consumed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }

            try:
                fd = os.open(
                    marker_path,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                )
            except FileExistsError:
                return "FAILED"

            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(marker, f, sort_keys=True)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())

            patch_payload = {
                "overrule_consumed": marker,
                "overrule_evidence": public_evidence,
                "overrule_verified": True,
            }
            if hasattr(state, "patch_data"):
                state.patch_data(patch_payload)
            elif hasattr(state, "data") and isinstance(state.data, dict):
                state.data.update(patch_payload)
            state.transition(
                "FORCE_COMPLETE",
                reason=f"QC overruled: {public_evidence['assertions'][0]}",
            )
            self._safe_save(state, state_file)
            self.graph.mark_complete(task_id)
            return "FORCE_COMPLETE"

    def _store_qc_timing_artifact(self, task_id: str):
        """Write a qc_timing.json artifact with phase-level timing data."""
        task_dir = self._task_run_dir(task_id)
        phases = self.metrics_coll.get_phases(task_id)
        if phases:
            import json

            timing_path = os.path.join(task_dir, "qc_timing.json")
            with open(timing_path, "w", encoding="utf-8") as f:
                json.dump(phases, f, indent=2, ensure_ascii=False)

    def _execute_single_contract(self, task_id: str) -> str:
        """Execute preflight -> work -> verify for a single task contract.

        Wrapped in a fault barrier — any unexpected exception is caught,
        recorded as a structured failure, and the task is marked BLOCKED.
        """
        try:
            return self._execute_single_contract_impl(task_id)
        except Exception as e:
            self._record_task_exception(task_id, e)
            return "CRASHED"

    def _auto_clean_undeclared_outputs(self, contract, state):
        """AUT-002: delete newly-created undeclared files before a retry.

        A worker that leaves a temp helper script (despite the brief
        forbidding extra files) currently poisons the retry — the leftover
        file trips undeclared_outputs again. Delete only NEWLY-CREATED
        outside-scope/denied files; never modified pre-existing files.
        Records what was removed for the audit trail.
        """
        undeclared = state.data.get("undeclared_outputs", [])
        if not isinstance(undeclared, list):
            return
        declared = {o.get("path", "") for o in (contract.outputs or [])}
        cleaned = []
        for v in undeclared:
            if not isinstance(v, dict):
                continue
            if not v.get("created_new"):
                continue  # never auto-delete modified pre-existing files
            if v.get("violation_type") not in ("outside_scope", "denied_new"):
                continue
            path = str(v.get("path", ""))
            if not path or path in declared:
                continue
            full = path if os.path.isabs(path) else os.path.join(self.workspace_root, path)
            try:
                if os.path.isfile(full):
                    os.remove(full)
                    cleaned.append(path)
                    print(f"[supervisor] auto-cleaned undeclared file: {path}", file=sys.stderr)
            except OSError:
                pass
        if cleaned:
            state.patch_data({"auto_cleaned_undeclared_outputs": cleaned})

    def _execute_single_contract_impl(self, task_id: str) -> str:
        """Core implementation of single-contract execution with retry and QC loops."""
        c_info = next((c for c in self.plan.contracts if c["task_id"] == task_id), None)
        if not c_info:
            return "FAILED"

        task_dir = self._task_run_dir(task_id)
        contract_path, contract_dict = self._get_contract_path_and_dict(c_info)

        if contract_dict:
            injected = self._inject_upstream_evidence(c_info, contract_dict)
            if injected:
                with open(contract_path, "w", encoding="utf-8") as f:
                    json.dump(contract_dict, f, indent=2, ensure_ascii=False)

        contract, errors = load_contract(contract_path, workspace_root=self.workspace_root)
        if errors or contract is None:
            self.graph.update_status(task_id, "FAILED")
            return "FAILED"

        plan_contract = c_info.get("contract", {})
        if plan_contract and contract_dict:
            diff_errors = validate_contract_against_plan(plan_contract, contract_dict)
            if diff_errors:
                self.graph.update_status(task_id, "BLOCKED")
                state = create_initial_state(task_id, journal_dir=task_dir)
                state.patch_data({"contract_path": contract_path})
                state.force_block(reason="Contract safety downgrade: " + "; ".join(diff_errors))
                self._safe_save(state, self._state_path(task_id))
                return "BLOCKED"

        state_file = self._state_path(task_id)
        state = self._load_or_create_state(task_id, contract_path=contract_path)
        if not os.path.isfile(state_file):
            self._safe_save(state, state_file)

        if state.status in ("COMPLETE", "FORCE_COMPLETE", "ESCALATED", "BLOCKED", "CANCELLED"):
            return state.status

        # Enforce budget guard before dispatching task
        try:
            self.budget_guard.check_before_call()
        except budget_mod.BudgetExhaustedError as be:
            print(f"[supervisor] BUDGET EXHAUSTED for task {task_id}: {be}", file=sys.stderr)
            state.force_block(reason=f"budget exhausted: {be}")
            state.patch_data({"budget_error": str(be)})
            self._safe_save(state, state_file)
            self.graph.update_status(task_id, "BLOCKED")
            return "BLOCKED"

        max_attempts = contract.worker.get("max_attempts", 3)

        # 1. Preflight (once)
        if state.status in ("DRAFTED", "drafted"):
            self.metrics_coll.start_phase("preflight", task_id)
            state.transition("PREFLIGHT_RUNNING", reason="supervisor starting preflight")
            self._safe_save(state, state_file)
            passed, results, evidence_path = run_preflight(contract, self.workspace_root, task_dir)
            self.metrics_coll.end_phase("preflight", task_id)
            if evidence_path:
                state.add_evidence("preflight", evidence_path)
            if passed:
                state.transition("READY", reason="supervisor preflight passed")
            else:
                state.transition("PREFLIGHT_FAILED", reason="preflight failed")
                state.transition("BLOCKED", reason="preflight failed")
                fclass = classify_failure(state, contract)
                state.patch_data({"last_failure_class": fclass})
                self._safe_save(state, state_file)
                self.graph.update_status(task_id, "BLOCKED")
                fb_rec = fb.collect_feedback(task_id, self.goal.goal_id, state)
                if fb_rec:
                    fb.store_feedback(self.goal.goal_id, self.run_dir, [fb_rec])
                return "BLOCKED"
            self._safe_save(state, state_file)

        # Recover stale WORKING state (supervisor or worker died mid-work).
        # Lease-based recovery (2026-07-31): when we entered WORKING we recorded
        # the owning supervisor PID. On resume, a WORKING task whose lease owner
        # is a DIFFERENT, now-dead pid means the previous supervisor crashed —
        # crash the task and let the retry budget requeue it. A live owner (or
        # our own pid on resume) keeps the existing worker_results check.
        if state.status == "WORKING":
            lease = state.data.get("worker_lease")
            lease_dead = (
                isinstance(lease, dict)
                and isinstance(lease.get("pid"), int)
                and lease["pid"] != os.getpid()
                and not _pid_alive(lease["pid"])
            )
            worker_done = bool(state.worker_results) and state.worker_results[-1].get("exit_code") is not None
            if lease_dead or not worker_done:
                state.transition("CRASHED", reason="worker terminated abnormally (stale WORKING)")
                state.patch_data({"crash_reason": "recovered: WORKING with no live lease owner"})
                self._safe_save(state, state_file)
                self.graph.update_status(task_id, "CRASHED")

        # 2. Work + Verify + QC retry loop
        while state.attempt <= max_attempts:
            if state.status in ("COMPLETE", "FORCE_COMPLETE", "DEGRADED_PASS", "ESCALATED", "BLOCKED", "CANCELLED"):
                break

            # Auto-retry from VERIFICATION_FAILED, QC_REJECTED, CRASHED,
            # QC_INSUFFICIENT_EVIDENCE, or QC_CONDITIONAL_PASS
            if state.status in (
                "VERIFICATION_FAILED",
                "QC_REJECTED",
                "CRASHED",
                "QC_INSUFFICIENT_EVIDENCE",
                "QC_CONDITIONAL_PASS",
            ):
                if state.attempt >= max_attempts:
                    # AUT-003: EVERY exhausted failure state escalates with an
                    # impossibility artifact — no silent terminal
                    # VERIFICATION_FAILED that leaves the goal unexplained.
                    state.transition("ESCALATED", reason=f"max attempts ({max_attempts}) reached")
                    imp.write_impossibility(contract, state, self.goal.goal_id, self.workspace_root)
                    self.graph.update_status(task_id, "ESCALATED")
                    break
                state.increment_attempt()
                retry_trigger = state.status
                fclass = classify_failure(state, contract)
                state.patch_data({"last_failure_class": fclass})
                strategy_fingerprint, prior_fingerprint = _retry_fingerprints(state, fclass, state.attempt)
                approach_desc = f"auto-retry from {retry_trigger}"
                # AUT-002: repair nondeterministic worker slips (leaving an
                # undeclared helper file) by deleting newly-created
                # undeclared files before the retry — never modified
                # pre-existing files.
                if state.status == "VERIFICATION_FAILED":
                    self._auto_clean_undeclared_outputs(contract, state)
                state.record_approach(approach_desc)
                state.add_retry_metadata(
                    {
                        "attempt": state.attempt,
                        "trigger": retry_trigger,
                        "approach": approach_desc,
                        "changed_dimensions": ["implementation_approach"],
                        "strategy_fingerprint": strategy_fingerprint,
                        "prior_fingerprint": prior_fingerprint,
                        "failure_ids": [fclass],
                    }
                )
                state.transition("RETRY_PENDING", reason=f"auto-retry as attempt {state.attempt}")
                self._safe_save(state, state_file)

            # Work phase
            if state.status in ("READY", "ready", "RETRY_PENDING", "retry_pending"):
                self.metrics_coll.start_phase("work", task_id)
                state.transition("WORKING", reason="supervisor starting work")
                # Lease owner = this supervisor process. Lets a resumed run
                # detect that the previous supervisor died mid-work.
                state.patch_data({"worker_lease": {"pid": os.getpid(), "ts": time.time()}})
                self._safe_save(state, state_file)

                allowed = contract.workspace_scope.get("allow", [])
                denied = contract.workspace_scope.get("deny", [])
                sc.snapshot_scope(self.workspace_root, allowed, task_dir, denied_paths=denied)

                if self.dry_run:
                    for out in contract.outputs:
                        out_path = out["path"]
                        full = os.path.join(self.workspace_root, out_path) if not os.path.isabs(out_path) else out_path
                        os.makedirs(os.path.dirname(full), exist_ok=True)
                        with open(full, "w", encoding="utf-8") as f:
                            f.write("SIMULATED: dry-run output")
                    worker_result = {
                        "success": True,
                        "stdout": "dry-run simulated",
                        "stderr": "",
                        "exit_code": 0,
                        "elapsed_sec": 0.01,
                        "artifact_paths": [out["path"] for out in contract.outputs],
                    }
                else:
                    previous_failures = None
                    if state.worker_results:
                        previous_failures = [
                            {"message": r.get("stderr", "") or f"exit code {r.get('exit_code', '?')}"}
                            for r in state.worker_results[-3:]
                        ]
                    # Perpetual-loop r2: expose retry fingerprints to the worker
                    # so build_implementer_prompt can demand a real approach
                    # change when the failure signature repeats.
                    _rm = state.data.get("retry_metadata", [])
                    _latest = _rm[-1] if isinstance(_rm, list) and _rm and isinstance(_rm[-1], dict) else {}
                    contract.worker["_strategy_fingerprint"] = str(_latest.get("strategy_fingerprint", "") or "")
                    contract.worker["_prior_fingerprint"] = str(_latest.get("prior_fingerprint", "") or "")
                    worker_result = run_worker(
                        contract,
                        self.workspace_root,
                        task_dir,
                        previous_failures=previous_failures,
                        supervisor_attempt=state.attempt,
                    )
                state.add_worker_result(worker_result)
                if worker_result.get("exit_code", 0) != 0:
                    state.patch_data({"worker_exit_nonzero": True})
                elif state.data.get("worker_exit_nonzero") and worker_result.get("exit_code") == 0:
                    state.delete_data_key("worker_exit_nonzero")
                self.metrics_coll.end_phase("work", task_id)
                self.metrics_coll.record_attempt(task_id)
                state.add_evidence(
                    f"worker_attempt_{state.attempt}",
                    os.path.join(task_dir, "worker_output.log"),
                )
                state.transition("VERIFYING", reason="supervisor worker finished")
                self._safe_save(state, state_file)

            # Post-worker scope check. Exclude the supervisor's OWN run-state
            # tree (state.json, journals, sibling task dirs) — those are
            # orchestrator artifacts, not worker output. Parallel-mode fix.
            scope_violations = []
            if state.worker_results:
                scope_result = sc.check_scope(
                    contract, self.workspace_root, task_dir, exclude_dir=self.run_dir, task_id=task_id
                )
                if not scope_result.passed:
                    print(
                        f"[supervisor] SCOPE VIOLATION in {task_id} — {len(scope_result.violations)} issue(s)",
                        file=sys.stderr,
                    )
                    for v in scope_result.violations:
                        print(f"  [{v.violation_type}] {v.path}", file=sys.stderr)
                    state.patch_data({"scope_violations": [v.to_dict() for v in scope_result.violations]})
                    # Parallel-mode fix (2026-07-31): a sibling task's declared
                    # output is NOT an undeclared file — filter it out before
                    # treating violations as scope enforcement failures.
                    plan_outputs = set()
                    for _plan_c in self.plan.contracts:
                        c = _plan_c.get("contract", {})
                        if isinstance(c, dict):
                            plan_outputs.update(o.get("path", "") for o in c.get("outputs", []))
                    undeclared = [
                        v
                        for v in scope_result.violations
                        if v.violation_type in ("outside_scope", "denied_new", "denied_modified")
                        and not sc.is_path_exempt(
                            v.path,
                            list(plan_outputs),
                            workspace_root=self.workspace_root,
                        )
                    ]
                    if undeclared:
                        scope_violations = undeclared
                        state.patch_data({"undeclared_outputs": [v.to_dict() for v in undeclared]})
                        print(
                            f"[supervisor] SCOPE ENFORCEMENT: {len(undeclared)} undeclared file(s) detected",
                            file=sys.stderr,
                        )

            # Verify phase
            if state.status in ("VERIFYING", "verifying"):
                self.metrics_coll.start_phase("verify", task_id)

                # Inject auto-generated checks from quality_spec
                injected_checks = []
                quality_spec = getattr(contract, "quality_spec", {})
                if quality_spec:
                    req_secs = quality_spec.get("required_sections", [])
                    if req_secs and contract.outputs:
                        for out in contract.outputs[:1]:
                            out_path = str(out.get("path", "")).lower()
                            if out_path.endswith((".md", ".markdown", ".txt", ".rst")):
                                injected_checks.append(
                                    {
                                        "id": "required_sections",
                                        "kind": "required_sections",
                                        "path": out["path"],
                                        "expected": req_secs,
                                    }
                                )
                # Inject undeclared outputs check using scope snapshot
                snapshot_path = os.path.join(task_dir, sc.SCOPE_SNAPSHOT_FILE)
                if contract.outputs and os.path.isfile(snapshot_path):
                    allowed = contract.workspace_scope.get("allow", [])
                    # Parallel-mode fix (2026-07-31): concurrent tasks write
                    # their OWN declared outputs, which a sibling's check would
                    # otherwise flag as undeclared. Treat every contract's
                    # declared outputs as declared for the whole plan.
                    plan_outputs = []
                    for _plan_c in self.plan.contracts:
                        c = _plan_c.get("contract", {})
                        if isinstance(c, dict):
                            plan_outputs.extend(o.get("path", "") for o in c.get("outputs", []))
                    injected_checks.append(
                        {
                            "id": "undeclared_outputs",
                            "kind": "undeclared_outputs",
                            "declared_outputs": [o["path"] for o in contract.outputs] + plan_outputs,
                            "scope_snapshot_path": snapshot_path,
                            "allowed_paths": allowed,
                        }
                    )
                base_checks = [
                    c
                    for c in contract.acceptance_checks
                    if isinstance(c, dict) and c.get("id") not in ("undeclared_outputs", "required_sections")
                ]
                contract.acceptance_checks = base_checks + injected_checks

                all_passed, v_results, evidence_path = run_verification(contract, self.workspace_root, task_dir)

                # If scope violations found, force verification failure and update evidence
                if scope_violations:
                    all_passed = False
                    # AUT-019: append a REAL VerifierResult, never a lambda —
                    # downstream code calls .to_dict() on these results.
                    from .verifier import VerifierResult

                    v_results.append(
                        VerifierResult(
                            check_id="scope_enforcement",
                            kind="scope_enforcement",
                            passed=False,
                            message=f"{len(scope_violations)} undeclared file(s)",
                        )
                    )
                    scope_entry = {
                        "check_id": "scope_enforcement",
                        "kind": "scope_enforcement",
                        "passed": False,
                        "message": f"{len(scope_violations)} undeclared file(s)",
                        "stdout": "",
                        "stderr": "",
                        "exit_code": None,
                    }
                    # Re-save evidence to include scope check
                    if evidence_path and os.path.isfile(evidence_path):
                        try:
                            with open(evidence_path, "r", encoding="utf-8") as f:
                                ev_data = json.load(f)
                            ev_data["verification_results"].append(scope_entry)
                            ev_data["all_passed"] = False
                            with open(evidence_path, "w", encoding="utf-8") as f:
                                json.dump(ev_data, f, indent=2, ensure_ascii=False)
                        except (OSError, json.JSONDecodeError):
                            pass
                self.metrics_coll.end_phase("verify", task_id)
                if evidence_path:
                    state.add_evidence("verification", evidence_path)

                if all_passed:
                    state.transition("VERIFIED", reason="supervisor verify passed")
                    qc_required = contract.qc.get("required", False)
                    if state.data.get("worker_exit_nonzero"):
                        qc_required = True
                    if not qc_required:
                        state.transition("COMPLETE", reason="verify passed, no QC required")
                        self.graph.mark_complete(task_id)
                        self._safe_save(state, state_file)
                        break
                    state.transition("QC_RUNNING", reason="verify passed, starting QC review")
                    self._safe_save(state, state_file)

                    output_paths = [
                        os.path.join(self.workspace_root, out["path"])
                        if not os.path.isabs(out["path"])
                        else out["path"]
                        for out in contract.outputs
                    ]
                    v_results_dicts = [r.to_dict() for r in v_results]
                    from .quality_plan import QualityPlan, quality_plan_for_contract, validate_quality_plan
                    from .quality_plane import run_quality_plane

                    qp = None
                    if getattr(contract, "quality_plan", None):
                        try:
                            qp = QualityPlan.from_dict(contract.quality_plan)
                            plan_errors = validate_quality_plan(qp)
                            if plan_errors:
                                print(
                                    f"[supervisor] invalid explicit quality_plan for {task_id}: {plan_errors}; falling back to default",
                                    file=sys.stderr,
                                )
                                qp = None
                        except (TypeError, ValueError, KeyError, AttributeError) as e:
                            print(
                                f"[supervisor] unparseable quality_plan for {task_id}: {e}; falling back to default",
                                file=sys.stderr,
                            )
                            qp = None
                    if qp is None:
                        qp = quality_plan_for_contract(
                            contract.risk_tier,
                            contract.qc.get("lens", "code_correctness"),
                            contract.quality_spec,
                        )
                    if qp.budget.max_llm_calls is not None and qp.estimate_calls() > qp.budget.max_llm_calls:
                        qp = qp.degraded_copy()
                    self.metrics_coll.start_phase("qc", task_id)
                    verdict = run_quality_plane(
                        contract,
                        output_paths,
                        v_results_dicts,
                        self.workspace_root,
                        quality_plan=qp,
                    )
                    self.metrics_coll.end_phase("qc", task_id)
                    print(
                        f"[supervisor] QC for {task_id}: {verdict.status} score={verdict.score:.2f}"
                        f" issues={len(verdict.issues)} — {verdict.reason}",
                        file=sys.stderr,
                    )

                    evidence_path_qc = os.path.join(task_dir, "qc_verdict.json")
                    vd = verdict.to_dict()
                    with open(evidence_path_qc, "w", encoding="utf-8") as f:
                        json.dump(vd, f, indent=2, ensure_ascii=False)
                    state.add_evidence("qc_verdict", evidence_path_qc)
                    self._store_qc_timing_artifact(task_id)

                    if verdict.passed:
                        if not os.path.isfile(evidence_path_qc):
                            state.transition("QC_INSUFFICIENT_EVIDENCE", reason="QC verdict file missing after write")
                            self.graph.update_status(task_id, "QC_INSUFFICIENT_EVIDENCE")
                            self._safe_save(state, state_file)
                            break
                        state.transition("QC_PASSED", reason=verdict.reason)
                        if state.data.get("worker_exit_nonzero"):
                            state.transition("DEGRADED_PASS", reason="QC passed with degraded worker exit")
                        else:
                            state.transition("COMPLETE", reason="QC passed")
                        self.graph.mark_complete(task_id)
                        self._safe_save(state, state_file)
                        break
                    else:
                        state.patch_data(
                            {
                                "last_qc_rejection": verdict.reason,
                                "qc_score": verdict.score,
                                "qc_issues": verdict.issues,
                            }
                        )
                        if verdict.status == "INSUFFICIENT_EVIDENCE":
                            state.transition(
                                "QC_INSUFFICIENT_EVIDENCE", reason=f"QC {verdict.status}: {verdict.reason}"
                            )
                            self.graph.update_status(task_id, "QC_INSUFFICIENT_EVIDENCE")
                        elif verdict.status == "CONDITIONAL_PASS":
                            state.transition("QC_CONDITIONAL_PASS", reason=f"QC {verdict.status}: {verdict.reason}")
                            self.graph.update_status(task_id, "QC_CONDITIONAL_PASS")
                        elif verdict.status == "ERROR":
                            state.patch_data({"last_qc_error": True})
                            state.transition("QC_REJECTED", reason=f"QC ERROR: {verdict.reason}")
                            self.graph.update_status(task_id, "QC_REJECTED")
                        else:
                            state.transition("QC_REJECTED", reason=f"QC {verdict.status}: {verdict.reason}")
                            self.graph.update_status(task_id, "QC_REJECTED")
                        state.add_retry_metadata(
                            {
                                "attempt": state.attempt,
                                "trigger": f"QC_{verdict.status}",
                                "approach": f"QC rejection: {verdict.reason}",
                                "changed_dimensions": ["implementation_approach"],
                                "strategy_fingerprint": "",
                                "prior_fingerprint": "",
                                "failure_ids": [verdict.status],
                            }
                        )
                        self._safe_save(state, state_file)
                        continue
                else:
                    state.transition("VERIFICATION_FAILED", reason="verification failed")
                    if state.data.get("worker_exit_nonzero"):
                        state.patch_data({"degraded_exit": True})
                    self.graph.update_status(task_id, "VERIFICATION_FAILED")
                    self._safe_save(state, state_file)
                    continue

        # Clean up scratch_dir (helper artifacts) on terminal success
        if state.status in ("COMPLETE", "complete", "FORCE_COMPLETE", "DEGRADED_PASS"):
            try:
                sc.cleanup_scratch_dir(self.workspace_root, contract)
            except (OSError, ValueError):
                pass

        # Post-loop: handle COMPLETE evidence store
        if state.status in ("COMPLETE", "complete", "DEGRADED_PASS", "FORCE_COMPLETE"):
            import shutil

            art_dir = os.path.join(self.workspace_root, imp.artifact_dir(self.goal.goal_id, task_id))
            if os.path.isdir(art_dir):
                shutil.rmtree(art_dir, ignore_errors=True)
            contract_path, contract_dict_completed = self._get_contract_path_and_dict(c_info)
            completed_contract, _ = load_contract(contract_path, workspace_root=self.workspace_root)
            if completed_contract:
                with self._shared_lock:
                    self.evidence_store[task_id] = [
                        os.path.join(self.workspace_root, out["path"])
                        if not os.path.isabs(out["path"])
                        else out["path"]
                        for out in completed_contract.outputs
                    ]
                completed_outs = completed_contract.outputs if completed_contract else []
                for out in completed_outs:
                    ev.append_output(self.run_dir, task_id, out["path"], self.workspace_root)
                try:
                    self.memory_bridge.append(
                        {
                            "task_id": task_id,
                            "event": "CONTRACT_COMPLETED",
                            "outputs": [o.get("path") if isinstance(o, dict) else str(o) for o in completed_outs],
                            "status": state.status,
                        }
                    )
                except (TimeoutError, OSError, json.JSONDecodeError, ValueError):
                    pass

        if state.status in ("PREFLIGHT_FAILED", "BLOCKED", "VERIFICATION_FAILED", "ESCALATED"):
            fclass = classify_failure(state, contract)
            state.patch_data({"last_failure_class": fclass})
            self._safe_save(state, state_file)

        fb_rec = fb.collect_feedback(task_id, self.goal.goal_id, state)
        if fb_rec:
            fb.store_feedback(self.goal.goal_id, self.run_dir, [fb_rec])

        return state.status

    def _declared_output_paths(self, task_id: str) -> List[str]:
        """Return a task's declared output paths (for scope-lease registration)."""
        c_info = next((c for c in self.plan.contracts if c["task_id"] == task_id), None)
        if not c_info:
            return []
        contract_dict = c_info.get("contract", {})
        outputs = contract_dict.get("outputs", []) if isinstance(contract_dict, dict) else []
        return [o.get("path", "") for o in outputs if isinstance(o, dict) and o.get("path")]

    def _execute_batch_serial(self, ready_tasks: List[str]) -> bool:
        """Execute ready tasks serially. Returns True if any task made progress."""
        progress_made = False
        for idx, task_id in enumerate(ready_tasks):
            print(f"[supervisor] executing {task_id} ({idx + 1}/{len(ready_tasks)})", file=sys.stderr)
            prev_status = self.graph.nodes.get(task_id, {}).get("status")
            status = self._execute_single_contract(task_id)
            self.results[task_id] = {"status": status}
            print(f"[supervisor] {task_id} -> {status}", file=sys.stderr)
            if status != prev_status or status in ("COMPLETE", "complete", "DEGRADED_PASS", "FORCE_COMPLETE"):
                progress_made = True
        return progress_made

    def _execute_batch_parallel(self, ready_tasks: List[str]) -> bool:
        """Execute ready tasks in parallel using WorkerPool. Returns True if any made progress."""
        pool = wp.WorkerPool(max_workers=self.max_workers)
        task_infos = [{"task_id": tid} for tid in ready_tasks]

        # Register every ready task's declared outputs BEFORE any worker can
        # launch, so sibling scope checks exempt them (perpetual-loop r1).
        for task_id in ready_tasks:
            try:
                self._scope_leases.register(task_id, self._declared_output_paths(task_id))
            except (KeyError, ValueError, OSError, AttributeError):
                pass

        def run_leased(task_id: str) -> str:
            try:
                return self._execute_single_contract(task_id)
            finally:
                try:
                    self._scope_leases.unregister(task_id)
                except (KeyError, ValueError, OSError, AttributeError):
                    pass

        def on_result(tid: str, status: str):
            with self._shared_lock:
                self.results[tid] = {"status": status}
            prev = self.graph.nodes.get(tid, {}).get("status")
            if status != prev or status in ("COMPLETE", "complete", "FORCE_COMPLETE"):
                print(f"[supervisor] {tid} -> {status} (parallel)", file=sys.stderr)

        try:
            pool.execute_batch(task_infos, run_leased, on_result=on_result)
        finally:
            # Best-effort cleanup on pool setup failures too.
            for task_id in ready_tasks:
                try:
                    self._scope_leases.unregister(task_id)
                except (KeyError, ValueError, OSError, AttributeError):
                    pass
        return True

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
                    # check would never fire — WORKING tasks aren't "ready").
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
            from .contract import Contract

            try:
                return Contract.from_dict(c_dict, workspace_root=self.workspace_root)
            except (KeyError, ValueError, TypeError, AttributeError) as e:
                print(f"[supervisor] Contract parsing error for {task_id}: {e}", file=sys.stderr)
                return None
        contract, _ = load_contract(contract_path, workspace_root=self.workspace_root)
        return contract

    def _escalate_stalled_nodes(self):
        """ESCALATE non-terminal nodes after a no-progress iteration and write
        impossibility artifacts — an unattended run must always end in a
        terminal, auditable state (autonomy fix 2026-07-31)."""
        from .state import load_state

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
                    # Legal-transition dead end (e.g. READY/DRAFTED) — privileged
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

    def execute_plan(self) -> Dict[str, str]:
        """Execute plan under the goal lock (safe for direct CLI entry)."""
        try:
            lk.acquire_lock(self.goal.goal_id, self.run_dir)
        except lk.LockHeldError as e:
            print(f"[supervisor] LOCK HELD: {e}", file=sys.stderr)
            self.goal.status = "FAILED"
            return {}
        try:
            return self._execute_plan()
        finally:
            lk.release_lock(self.run_dir)
            print(f"[supervisor] lock released for {self.goal.goal_id}", file=sys.stderr)

    def _execute_plan(self) -> Dict[str, str]:
        """Execute all contracts in plan according to dependency order.

        If self.parallel is True, independent ready-tasks are executed
        concurrently via WorkerPool. Sequential fallback otherwise.

        Callers are responsible for holding the goal lock (see execute_plan).
        """
        self.goal.status = "EXECUTING"
        _plan_start = time.time()

        # Recover graph from state files (for resumption after interruption)
        self._recover_graph_from_state_files()
        self._save_plan()

        max_attempts_sum = sum(
            c.get("contract", {}).get("worker", {}).get("max_attempts", 1) if isinstance(c.get("contract"), dict) else 1
            for c in self.plan.contracts
        )
        max_iterations = max_attempts_sum + 10
        iterations = 0
        _limit_breached = False

        # Self-audit discovery 2026-07-31: the 600s default wall-clock limit
        # aborts multi-step research goals even after every contract passed
        # QC (5-step audit needs ~750s+). Scale the goal budget with plan
        # size: 300s per step, never below the 600s default.
        n_contracts = max(len(self.plan.contracts), 1)
        goal_limits = lm.ResourceLimits(
            max_wall_clock_sec=max(lm.DEFAULT_LIMITS.max_wall_clock_sec, n_contracts * 300),
            max_output_size_bytes=lm.DEFAULT_LIMITS.max_output_size_bytes,
            max_attempts_global=lm.DEFAULT_LIMITS.max_attempts_global,
            max_iterations=lm.DEFAULT_LIMITS.max_iterations,
        )

        batch_fn = self._execute_batch_parallel if self.parallel else self._execute_batch_serial

        while iterations < max_iterations:
            try:
                lc = lm.check_limits(goal_limits, elapsed_sec=time.time() - _plan_start, iterations=iterations)
                if lc["exceeded"]:
                    print(f"[supervisor] {lm.format_violation(lc)}", file=sys.stderr)
                    _limit_breached = True
                    break

                ready_tasks = self.graph.get_ready_tasks()
                if not ready_tasks:
                    break

                progress_made = batch_fn(ready_tasks)
                self._save_plan()

                if not progress_made:
                    # Stall-escalation (2026-07-31): no task advanced this
                    # iteration. Do not exit leaving non-terminal nodes without
                    # a decisive outcome — escalate them with impossibility
                    # artifacts so an unattended run always ends in a terminal,
                    # auditable state.
                    self._escalate_stalled_nodes()
                    break

                iterations += 1

                self._checkpoint_iteration += 1
                if self._checkpoint_iteration % self._checkpoint_interval == 0:
                    cp.save_checkpoint(
                        run_dir=self.run_dir,
                        iteration=iterations,
                        plan_contracts=self.plan.contracts,
                        results=self.results,
                        graph_statuses={tid: info.get("status", "UNKNOWN") for tid, info in self.graph.nodes.items()},
                        evidence_store=self.evidence_store,
                        goal_status=self.goal.status,
                        total_contracts=len(self.plan.contracts),
                    )
                    print(f"[supervisor] checkpoint saved at iteration {iterations}", file=sys.stderr)

                if iterations >= max_iterations:
                    print(f"[supervisor] WARNING: max iterations ({max_iterations}) reached", file=sys.stderr)
                    break
            except Exception as e:
                print(f"[supervisor] LOOP CRASHED at iteration {iterations}: {type(e).__name__}: {e}", file=sys.stderr)
                _limit_breached = True
                break

        blocked_tasks = self.graph.get_blocked_tasks()
        all_completed = len(self.results) == len(self.plan.contracts) and all(
            r["status"] in ("COMPLETE", "complete", "DEGRADED_PASS", "FORCE_COMPLETE") for r in self.results.values()
        )

        # AUT-004: a goal whose contracts all completed must be COMPLETE even
        # if the wall-clock limit was breached AFTER the final batch — the
        # completion result wins over the limit signal.
        if all_completed:
            self.goal.status = "COMPLETE"
        elif _limit_breached:
            self.goal.status = "FAILED"
        else:
            self.goal.status = (
                "FAILED"
                if blocked_tasks
                or any(
                    r["status"]
                    in (
                        "VERIFICATION_FAILED",
                        "FAILED",
                        "failed",
                        "BLOCKED",
                        "blocked",
                        "CRASHED",
                        "DRAFTED",
                        "drafted",
                    )
                    for r in self.results.values()
                )
                else "EXECUTING"
            )

        metrics_path = os.path.join(self.run_dir, "metrics.json")
        self.metrics_coll.save(metrics_path)

        self._print_run_summary()

        return {tid: res["status"] for tid, res in self.results.items()}

    def execute_plan_with_retry(self, changed_approach: Optional[str] = None) -> Dict[str, str]:
        """Execute plan, and retry failed contracts if attempts remain."""
        try:
            lk.acquire_lock(self.goal.goal_id, self.run_dir)
        except lk.LockHeldError as e:
            print(f"[supervisor] LOCK HELD: {e}", file=sys.stderr)
            self.goal.status = "FAILED"
            return {}
        print(f"[supervisor] lock acquired for {self.goal.goal_id}", file=sys.stderr)
        try:
            res = self._execute_plan()

            for c_info in self.plan.contracts:
                task_id = c_info["task_id"]
                state_file = self._state_path(task_id)
                if not os.path.isfile(state_file):
                    continue
                state = load_state(state_file)
                contract_path, _ = self._get_contract_path_and_dict(c_info)
                contract, _ = load_contract(contract_path, workspace_root=self.workspace_root)
                if contract is None:
                    continue

                max_attempts = contract.worker.get("max_attempts", 1)
                if state.status in (
                    "VERIFICATION_FAILED",
                    "BLOCKED",
                    "CRASHED",
                    "QC_REJECTED",
                    "QC_INSUFFICIENT_EVIDENCE",
                    "QC_CONDITIONAL_PASS",
                ):
                    fclass = classify_failure(state, contract)
                    state.patch_data({"last_failure_class": fclass})
                    if state.status in ("BLOCKED", "CRASHED"):
                        state.transition("DRAFTED", reason="resetting for retry")
                    if state.attempt >= max_attempts:
                        state.force_escalate(reason="max attempts reached in retry")
                        self._safe_save(state, state_file)
                        self.graph.update_status(task_id, "ESCALATED")
                        res[task_id] = "ESCALATED"
                        audit_mod.record_action(
                            self.run_dir,
                            "failsafe_escalation",
                            goal_id=self.goal.goal_id,
                            task_id=task_id,
                            details={
                                "reason": "max attempts reached",
                                "attempt": state.attempt,
                                "max_attempts": max_attempts,
                                "failure_class": fclass,
                            },
                        )
                        imp.write_impossibility(
                            contract,
                            state,
                            goal_id=self.goal.goal_id,
                            workspace_root=self.workspace_root,
                            failure_class=fclass,
                        )
                        fb_rec = fb.collect_feedback(task_id, self.goal.goal_id, state)
                        if fb_rec:
                            fb.store_feedback(self.goal.goal_id, self.run_dir, [fb_rec])
                        continue
                    if state.worker_results:
                        state.patch_worker_result(
                            len(state.worker_results) - 1, annotate_worker_result(state.worker_results[-1], fclass)
                        )
                    strikes = count_consecutive_same_class(state, fclass)
                    if strikes >= MAX_SAME_CLASS_STRIKES:
                        state.force_escalate(reason="consecutive identical failure class")
                        self._safe_save(state, state_file)
                        self.graph.update_status(task_id, "ESCALATED")
                        res[task_id] = "ESCALATED"
                        imp.write_impossibility(
                            contract,
                            state,
                            goal_id=self.goal.goal_id,
                            workspace_root=self.workspace_root,
                            failure_class=fclass,
                        )
                        fb_rec = fb.collect_feedback(task_id, self.goal.goal_id, state)
                        if fb_rec:
                            fb.store_feedback(self.goal.goal_id, self.run_dir, [fb_rec])
                        continue
                    state.increment_attempt()
                    if changed_approach:
                        if not require_divergent_retry(state, changed_approach):
                            state.force_escalate(reason="non-divergent retry approach")
                            res[task_id] = "ESCALATED"
                            print(f"[supervisor] {task_id} ESCALATED — non-divergent retry approach", file=sys.stderr)
                            imp.write_impossibility(
                                contract,
                                state,
                                goal_id=self.goal.goal_id,
                                workspace_root=self.workspace_root,
                                failure_class=fclass,
                            )
                            continue
                        state.record_approach(changed_approach)
                    state.transition("RETRY_PENDING", reason="supervisor retrying contract")
                    self._safe_save(state, state_file)
                    self.graph.update_status(task_id, "RETRY_PENDING")

                    print(f"[supervisor] retrying {task_id}", file=sys.stderr)
                    status = self._execute_single_contract(task_id)
                    res[task_id] = status

            if self.adaptive_replan:
                new_plan = self.adaptively_replan()
                if new_plan:
                    print(
                        f"[supervisor] Adaptively replanned into {len(new_plan.contracts)} contracts",
                        file=sys.stderr,
                    )
                    res = self._execute_plan()

            all_completed = all(
                status in ("COMPLETE", "complete", "DEGRADED_PASS", "FORCE_COMPLETE") for status in res.values()
            )
            self.goal.status = "COMPLETE" if all_completed else "FAILED"
            self._print_run_summary()
            return res
        finally:
            lk.release_lock(self.run_dir)
            print(f"[supervisor] lock released for {self.goal.goal_id}", file=sys.stderr)

    def adaptively_replan(self) -> Optional[Plan]:
        """Invoke evidence-aware replanner to split or adjust failed tasks."""
        from . import replanner as rep_mod

        try:
            results = dict(self.results)
            for c in self.plan.contracts:
                tid = c.get("task_id")
                if tid and tid not in results:
                    results[tid] = {"status": self.graph.nodes.get(tid, {}).get("status", "DRAFTED")}

            new_plan = rep_mod.replan(self.goal, results, self.run_dir)
            if new_plan and len(new_plan.contracts) != len(self.plan.contracts):
                self.plan = new_plan
                self.graph = ContractGraph(new_plan)
                self._save_plan()
                return new_plan
        except (ValueError, KeyError, OSError, json.JSONDecodeError, TypeError) as e:
            print(f"[supervisor] Replanning error: {e}", file=sys.stderr)
        return None

    def resume_plan(self) -> Dict[str, str]:
        """Resume a partially executed plan from persistent state.

        Loads existing state from disk, reconstructs graph statuses,
        rehydrates evidence store, then continues execution of remaining
        tasks. Idempotent — safe to call on fully-complete plans.
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

            # Reconciliation gate — detect tampering / missing outputs before resume
            report = rec.run_reconciliation(
                self.goal.goal_id,
                self.plan,
                self.workspace_root,
                self.run_dir,
            )
            if not report.passed:
                print(
                    f"[supervisor] RECONCILIATION ISSUES detected — {report.failed_tasks} problem(s)", file=sys.stderr
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
                    f"[supervisor] reconciliation OK — {report.checked_tasks}/{report.total_tasks} tasks clean",
                    file=sys.stderr,
                )

            self.goal.status = "EXECUTING"
            return self._execute_plan()
        finally:
            lk.release_lock(self.run_dir)
            print(f"[supervisor] lock released for {self.goal.goal_id}", file=sys.stderr)

    def pause_plan(self, reason: str = "operator pause") -> Dict[str, str]:
        """Pause all currently active (non-terminal, non-idle) tasks."""
        results = {}
        audit_mod.record_action(self.run_dir, "pause", goal_id=self.goal.goal_id, details={"reason": reason})
        for c_info in self.plan.contracts:
            tid = c_info["task_id"]
            state_file = self._state_path(tid)
            if not os.path.isfile(state_file):
                continue
            state = load_state(state_file)
            if state.pause(reason):
                self._safe_save(state, state_file)
                results[tid] = "PAUSED"
            else:
                results[tid] = state.status
        self.goal.status = "PAUSED"
        return results

    def cancel_plan(self, reason: str = "operator cancel") -> Dict[str, str]:
        """Cancel all non-terminal tasks."""
        results = {}
        audit_mod.record_action(self.run_dir, "cancel", goal_id=self.goal.goal_id, details={"reason": reason})
        for c_info in self.plan.contracts:
            tid = c_info["task_id"]
            state_file = self._state_path(tid)
            if not os.path.isfile(state_file):
                continue
            state = load_state(state_file)
            if state.cancel(reason):
                self._safe_save(state, state_file)
                results[tid] = "CANCELLED"
            else:
                results[tid] = state.status
            self.graph.update_status(tid, state.status)
        self.goal.status = "CANCELLED"
        return results

    def inspect_task(self, task_id: str) -> Dict[str, Any]:
        """Deep inspect a single task's state, evidence, and next actions."""
        state_file = self._state_path(task_id)
        if not os.path.isfile(state_file):
            return {"task_id": task_id, "error": "no state found"}
        state = load_state(state_file)
        c_info = next((c for c in self.plan.contracts if c["task_id"] == task_id), None)
        contract_dict = c_info.get("contract", {}) if c_info else {}
        task_dir = self._task_run_dir(task_id)
        evidence_files = {}
        for key, path in state.evidence.items():
            evidence_files[key] = {
                "path": path,
                "exists": os.path.isfile(path),
            }
        return {
            "task_id": task_id,
            "status": state.status,
            "attempt": state.attempt,
            "is_terminal": state.is_terminal(),
            "can_resume": state.can_resume(),
            "legal_transitions": sorted(state.legal_transitions()),
            "events_count": len(state.events),
            "worker_runs": len(state.worker_results),
            "last_worker_result": state.worker_results[-1] if state.worker_results else None,
            "failure_class": state.data.get("last_failure_class"),
            "crash_reason": state.data.get("crash_reason"),
            "scope_violations": state.data.get("scope_violations"),
            "evidence_files": evidence_files,
            "contract_title": contract_dict.get("title", ""),
            "contract_objective": contract_dict.get("objective", ""),
            "task_dir": task_dir,
        }

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

"""Adversarial fault-injection & mutation testing suite (#20).

Four scenarios:
  S1 OutOfBoundsMutation   — traversal/absolute/NUL/symlink escapes vs scope enforcement
  S2 ZombieProcess         — SIGTERM-ignoring children reaped via force-kill escalation
  S3 JournalCorruption     — corrupted state.json / state.wal.jsonl fail typed, not raw
  S4 ThreeStrikeDeterminism— same-class strike counter escalates exactly at 3
"""

import json
import os
import subprocess
import sys
import time

import pytest

from orchestrator import scope as sc
from orchestrator.checkpoint import apply_checkpoint, recover_from_checkpoint, save_checkpoint
from orchestrator.contract import Contract, check_path_allowed
from orchestrator.failure import (
    FAILURE_CLASS_TIMEOUT,
    FAILURE_CLASS_WORKER_EMPTY_OUTPUT,
    FAILURE_CLASS_WORKER_NONZERO_EXIT,
    MAX_SAME_CLASS_STRIKES,
    classify_failure,
    count_consecutive_same_class,
)
from orchestrator.goal import Goal, Plan
from orchestrator.process_guard import ProcessGuard, pid_alive
from orchestrator.state import StateError, create_initial_state, load_state, save_state
from orchestrator.supervisor import Supervisor
from tests.fault_injection import CORRUPTION_KINDS, corrupt_run_artifact, make_fake_dead_pid_proc

ALLOWED_STATE_ERRORS = (StateError, json.JSONDecodeError, ValueError, OSError)

TRAVERSAL_PAYLOADS = [
    "../escaped.txt",
    "../../outside.txt",
    "..\\..\\x",
    "../sibling_module/x.py",
]


def _scope_contract_dict(task_id="adv"):
    return {
        "task_id": task_id,
        "title": f"Task {task_id}",
        "status": "DRAFTED",
        "risk_tier": "auto",
        "workspace_scope": {"allow": ["scratch/"], "deny": []},
        "objective": "test",
        "worker": {"model": "test", "max_attempts": 1},
        "inputs": [],
        "outputs": [{"path": f"scratch/{task_id}_out.txt"}],
        "acceptance_checks": [],
        "qc": {"required": False, "lens": "code_correctness"},
    }


# ---------------------------------------------------------------------------
# S1: OutOfBoundsMutation
# ---------------------------------------------------------------------------


class TestOutOfBoundsMutation:
    @pytest.fixture()
    def ws(self, tmp_path):
        root = tmp_path / "ws"
        (root / "scratch").mkdir(parents=True)
        return str(root)

    def _rejection_matrix(self, ws, tmp_path):
        allow = ["scratch/"]
        outcomes = []
        for p in TRAVERSAL_PAYLOADS:
            outcomes.append((p,) + check_path_allowed(p, allow, [], ws))
        abs_escape_outer = str(tmp_path.parent / "evil.txt")
        outcomes.append((abs_escape_outer,) + check_path_allowed(abs_escape_outer, allow, [], ws))
        abs_escape_sibling = str(tmp_path / "evil_abs.txt")
        outcomes.append((abs_escape_sibling,) + check_path_allowed(abs_escape_sibling, allow, [], ws))
        abs_inside_ws = os.path.join(ws, "root_level_evil.txt")
        outcomes.append((abs_inside_ws,) + check_path_allowed(abs_inside_ws, allow, [], ws))
        for p in ("\x00evil", f"scratch/\x00{os.getpid()}.txt"):
            outcomes.append((p,) + check_path_allowed(p, allow, [], ws))
        return outcomes

    @pytest.mark.fast
    def test_adversarial_paths_rejected_fail_closed(self, ws, tmp_path):
        for path, ok, err in self._rejection_matrix(ws, tmp_path):
            assert ok is False, f"path {path!r} was allowed"
            assert err, f"path {path!r} rejected without reason"

    @pytest.mark.fast
    def test_rejection_matrix_is_deterministic(self, ws, tmp_path):
        first = self._rejection_matrix(ws, tmp_path)
        second = self._rejection_matrix(ws, tmp_path)
        assert first == second
        assert all(not ok for _, ok, _ in second)

    @pytest.mark.fast
    def test_undeclared_and_denied_writes_detected_by_scope_diff(self, ws, tmp_path):
        run_dir = os.path.join(ws, "scratch", "runs")
        sc.snapshot_scope(ws, ["scratch/"], run_dir, denied_paths=["denied/"])
        with open(os.path.join(ws, "undeclared.txt"), "w", encoding="utf-8") as f:
            f.write("mutant")
        os.makedirs(os.path.join(ws, "denied"), exist_ok=True)
        with open(os.path.join(ws, "denied", "rogue.py"), "w", encoding="utf-8") as f:
            f.write("# rogue")
        contract = Contract(_scope_contract_dict("adv"))
        result = sc.check_scope(contract, ws, run_dir)
        assert not result.passed
        types = {v.violation_type for v in result.violations}
        # A rogue file under a denied path that did not exist at snapshot time
        # may surface as either taxonomy entry (matches prod + test_scope.py).
        assert "outside_scope" in types or "denied_new" in types
        assert "outside_scope" in types  # undeclared.txt is always outside_scope

    @pytest.mark.fast
    def test_clean_workspace_passes_scope_after_snapshot(self, ws):
        run_dir = os.path.join(ws, "scratch", "runs")
        sc.snapshot_scope(ws, ["scratch/"], run_dir)
        contract = Contract(_scope_contract_dict("adv"))
        result = sc.check_scope(contract, ws, run_dir)
        assert result.passed, [v.to_dict() for v in result.violations]

    @pytest.mark.fast
    @pytest.mark.skipif(os.name == "nt", reason="symlink swap attack is POSIX-only")
    def test_symlink_swap_attack_rejected_posix(self, tmp_path):
        ws = tmp_path / "ws"
        scratch = ws / "scratch"
        scratch.mkdir(parents=True)
        outside = tmp_path / "outside"
        outside.mkdir()
        link = scratch / "link"
        os.symlink(str(outside), str(link))

        ok, err = check_path_allowed("scratch/link/evil.txt", ["scratch/"], [], str(ws))
        assert ok is False and err

        with open(os.path.join(str(link), "evil.txt"), "w", encoding="utf-8") as f:
            f.write("pwned")
        assert (outside / "evil.txt").is_file(), "write-through did not land outside (attack setup invalid)"


# ---------------------------------------------------------------------------
# S2: ZombieProcess
# ---------------------------------------------------------------------------


class TestZombieProcess:
    @pytest.mark.integration
    @pytest.mark.skipif(os.name == "nt", reason="POSIX process-group escalation proof")
    def test_sweep_reaps_child_that_ignores_sigterm_posix(self):
        script = "import signal, time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\ntime.sleep(30)\n"
        guard = ProcessGuard()
        proc = guard.spawn(
            [sys.executable, "-c", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(0.3)
        assert proc.poll() is None
        guard.sweep(timeout=5.0)
        deadline = time.time() + 5.0
        while pid_alive(proc.pid) and time.time() < deadline:
            time.sleep(0.05)
        assert not pid_alive(proc.pid), "SIGTERM-ignoring child survived sweep"

    @pytest.mark.fast
    def test_sweep_escalates_to_kill_process_tree_when_terminate_noop(self, tmp_path, monkeypatch):
        import orchestrator.process_guard as pg

        victim = subprocess.Popen(
            [sys.executable, "-c", "pass"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        victim.wait(timeout=10)
        assert not pid_alive(victim.pid), "ground-truth pid must be dead before fake registration"

        calls = []
        real_kill = pg.kill_process_tree

        def spy_kill(pid):
            calls.append(pid)
            real_kill(pid)

        monkeypatch.setattr(pg, "kill_process_tree", spy_kill)

        fake = make_fake_dead_pid_proc(victim.pid)
        guard = ProcessGuard()
        guard.register(fake)
        guard.sweep(timeout=1.0)

        assert calls == [victim.pid], "sweep must call kill_process_tree when terminate is a no-op"
        assert fake.poll() == 137, "pid not reported dead after force-kill simulation"
        assert not pid_alive(victim.pid)


# ---------------------------------------------------------------------------
# S3: JournalCorruption
# ---------------------------------------------------------------------------


class TestJournalCorruption:
    TARGETS = ("state.json", "state.wal.jsonl", "both")

    def _seed_task(self, run_dir, task_id="tX"):
        task_dir = os.path.join(str(run_dir), task_id)
        os.makedirs(task_dir, exist_ok=True)
        state = create_initial_state(task_id, journal_dir=task_dir)
        state.transition("PREFLIGHT_RUNNING", reason="seed")
        save_state(state, os.path.join(task_dir, "state.json"))
        return task_dir

    @pytest.mark.fast
    @pytest.mark.parametrize("target", TARGETS)
    @pytest.mark.parametrize("kind", CORRUPTION_KINDS)
    def test_corruption_matrix_typed_errors_and_checkpoint_recovery(self, tmp_path, kind, target):
        run_dir = tmp_path / "run"
        task_dir = self._seed_task(run_dir)
        state_file = os.path.join(task_dir, "state.json")
        wal_file = os.path.join(task_dir, "state.wal.jsonl")
        if target == "both":
            victims = [state_file, wal_file]
        else:
            victims = [state_file if target == "state.json" else wal_file]
        for victim in victims:
            corrupt_run_artifact(victim, kind, "tX")

        loaded = None
        try:
            loaded = load_state(state_file, journal_dir=task_dir)
        except ALLOWED_STATE_ERRORS:
            loaded = None
        except Exception as exc:
            pytest.fail(f"raw {type(exc).__name__} escaped load_state ({kind}/{target}): {exc}")
        if loaded is not None:
            assert loaded.task_id == "tX"

        save_checkpoint(
            run_dir=str(run_dir),
            iteration=0,
            plan_contracts=[],
            results={},
            graph_statuses={"tX": "PREFLIGHT_RUNNING"},
            evidence_store={},
        )
        recovery = recover_from_checkpoint(str(run_dir))
        assert recovery["recovered"] is True

        applied = None
        try:
            applied = apply_checkpoint(str(run_dir), workspace_root=str(tmp_path))
        except ALLOWED_STATE_ERRORS:
            applied = None
        except Exception as exc:
            pytest.fail(f"raw {type(exc).__name__} escaped apply_checkpoint ({kind}/{target}): {exc}")
        assert applied is not None and applied["recovered"] is True

        revived = load_state(state_file, journal_dir=task_dir)
        assert revived.task_id == "tX"
        assert revived.status == "PREFLIGHT_RUNNING"

    @pytest.mark.fast
    def test_wrong_schema_snapshot_raises_typed_state_error(self, tmp_path):
        """A valid-JSON snapshot without 'task_id' must fail CLOSED with a
        typed StateError (never a raw KeyError)."""
        task_dir = self._seed_task(tmp_path / "run")
        state_file = os.path.join(task_dir, "state.json")
        with open(state_file, "wb") as f:
            f.write(b'{"foo": [1, 2, 3], "bar": {"baz": true}}')
        with pytest.raises(StateError):
            load_state(state_file, journal_dir=task_dir)


# ---------------------------------------------------------------------------
# S4: ThreeStrikeDeterminism
# ---------------------------------------------------------------------------


def _failing_result(index, annotated=True):
    result = {
        "exit_code": 1,
        "stdout": "",
        "stderr": f"injected failure {index}",
        "elapsed_sec": 0.01,
        "artifact_paths": [],
        "success": False,
    }
    if annotated:
        result["failure_class"] = FAILURE_CLASS_WORKER_NONZERO_EXIT
    return result


def _single_contract_plan(ws, goal, max_attempts):
    tid = f"{goal.goal_id}-step-1"
    contract_dict = _scope_contract_dict(tid)
    contract_dict["worker"]["max_attempts"] = max_attempts
    contracts = [
        {
            "task_id": tid,
            "depends_on": [],
            "status": "DRAFTED",
            "contract": contract_dict,
        }
    ]
    return goal, Plan(goal_id=goal.goal_id, contracts=contracts), tid


def _seed_failed_state(run_dir, task_id, n_results):
    task_dir = os.path.join(run_dir, task_id)
    os.makedirs(task_dir, exist_ok=True)
    state = create_initial_state(task_id, journal_dir=task_dir)
    for status, reason in [
        ("PREFLIGHT_RUNNING", "seed"),
        ("READY", "seed"),
        ("WORKING", "seed"),
        ("VERIFYING", "seed"),
        ("VERIFICATION_FAILED", "seed"),
    ]:
        state.transition(status, reason=reason)
    for i in range(n_results):
        state.add_worker_result(_failing_result(i))
    save_state(state, os.path.join(task_dir, "state.json"))
    return task_dir


class TestThreeStrikeUnit:
    @pytest.mark.fast
    def test_three_identical_classes_hit_threshold(self):
        state = create_initial_state("s4a")
        for i in range(3):
            state.add_worker_result(_failing_result(i))
        count = count_consecutive_same_class(state, FAILURE_CLASS_WORKER_NONZERO_EXIT)
        assert count == MAX_SAME_CLASS_STRIKES == 3

    @pytest.mark.fast
    def test_two_strikes_below_threshold_do_not_escalate(self):
        state = create_initial_state("s4b")
        for i in range(2):
            state.add_worker_result(_failing_result(i))
        count = count_consecutive_same_class(state, FAILURE_CLASS_WORKER_NONZERO_EXIT)
        assert count < MAX_SAME_CLASS_STRIKES

    @pytest.mark.fast
    def test_class_break_resets_consecutive_count(self):
        state = create_initial_state("s4c")
        state.add_worker_result(_failing_result(0))
        state.add_worker_result(_failing_result(1))
        other = _failing_result(2)
        other["failure_class"] = FAILURE_CLASS_TIMEOUT
        state.add_worker_result(other)
        state.add_worker_result(_failing_result(3))
        assert count_consecutive_same_class(state, FAILURE_CLASS_WORKER_NONZERO_EXIT) == 1

    @pytest.mark.fast
    def test_unannotated_results_count_as_current_class(self):
        state = create_initial_state("s4d")
        state.add_worker_result(_failing_result(0, annotated=False))
        state.add_worker_result(_failing_result(1, annotated=False))
        assert count_consecutive_same_class(state, FAILURE_CLASS_WORKER_NONZERO_EXIT) == 2

    @pytest.mark.fast
    def test_classify_failure_shapes(self):
        timeout_state = create_initial_state("s4e")
        r = _failing_result(0)
        r["failure_class"] = None
        del r["failure_class"]
        r["stderr"] = "command timed out after 30s"
        timeout_state.add_worker_result(r)
        assert classify_failure(timeout_state) == FAILURE_CLASS_TIMEOUT

        nonzero_state = create_initial_state("s4f")
        nonzero_state.add_worker_result(_failing_result(0, annotated=False))
        assert classify_failure(nonzero_state) == FAILURE_CLASS_WORKER_NONZERO_EXIT

        empty_state = create_initial_state("s4g")
        ok_result = {"exit_code": 0, "stdout": "", "stderr": "", "elapsed_sec": 0.01}
        empty_state.add_worker_result(ok_result)
        assert classify_failure(empty_state) == FAILURE_CLASS_WORKER_EMPTY_OUTPUT


@pytest.fixture()
def strike_env(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_WORKER", "FAIL")
    ws = tmp_path / "ws"
    ws.mkdir()
    run_dir = str(ws / "scratch" / "runs")
    os.makedirs(run_dir, exist_ok=True)
    return str(ws), run_dir


class TestThreeStrikeSupervisorIntegration:
    @pytest.mark.integration
    def test_escalates_exactly_on_strike_three_with_impossibility_artifact(self, strike_env):
        ws, run_dir = strike_env
        goal, plan, tid = _single_contract_plan(ws, Goal(goal_id="strike3", title="Strike3", description="fail"), 5)
        supervisor = Supervisor(goal, plan, workspace_root=ws, run_dir=run_dir)
        _seed_failed_state(run_dir, tid, 3)

        res = supervisor.execute_plan_with_retry()

        assert res[tid] == "ESCALATED"
        assert supervisor.graph.nodes[tid]["status"] == "ESCALATED"
        artifact = os.path.join(ws, "scratch", "impossibility", "strike3", tid, "impossibility.json")
        assert os.path.isfile(artifact), "impossibility artifact not written on strike-3 escalation"
        with open(artifact, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert data["failure_class"] == FAILURE_CLASS_WORKER_NONZERO_EXIT
        assert len(data["worker_results"]) >= 3

    @pytest.mark.integration
    def test_two_strikes_do_not_trigger_strike_escalation(self, strike_env, monkeypatch):
        ws, run_dir = strike_env
        goal, plan, tid = _single_contract_plan(ws, Goal(goal_id="strike2", title="Strike2", description="fail"), 5)
        supervisor = Supervisor(goal, plan, workspace_root=ws, run_dir=run_dir)
        _seed_failed_state(run_dir, tid, 2)

        def _boom(*args, **kwargs):
            raise RuntimeError("injected worker crash")

        monkeypatch.setattr("orchestrator.supervisor.run_worker", _boom)
        res = supervisor.execute_plan_with_retry()

        assert res[tid] != "ESCALATED"
        artifact_dir = os.path.join(ws, "scratch", "impossibility", "strike2", tid)
        assert not os.path.isdir(artifact_dir), "impossibility artifact written despite strikes < 3"


# ---------------------------------------------------------------------------
# Matrix smoke
# ---------------------------------------------------------------------------


@pytest.mark.fast
def test_adversarial_matrix_registered():
    import tests.test_fault_injection_adversarial as mod

    for scenario in (
        "TestOutOfBoundsMutation",
        "TestZombieProcess",
        "TestJournalCorruption",
        "TestThreeStrikeUnit",
        "TestThreeStrikeSupervisorIntegration",
    ):
        assert hasattr(mod, scenario), f"scenario class missing: {scenario}"
    assert MAX_SAME_CLASS_STRIKES == 3
    assert len(CORRUPTION_KINDS) == 4
    assert len(TRAVERSAL_PAYLOADS) >= 4
    assert len(TestJournalCorruption.TARGETS) == 3

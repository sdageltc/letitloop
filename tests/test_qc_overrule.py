"""Tests for the verified QC overrule path (qc_overrule.py + supervisor wiring)."""

import hashlib
import json
import os
import threading

import pytest

from orchestrator.qc_overrule import verify_overrule
from orchestrator.supervisor import Supervisor


def _verification_evidence(check_id="unit", stdout="recorded verification stdout"):
    return {
        "verification_results": [
            {"check_id": check_id, "stdout": stdout},
        ]
    }


def _valid_evidence(secret="correct-secret", check_id="unit", stdout="recorded verification stdout"):
    return {
        "secret": secret,
        "check_id": check_id,
        "stdout_hash": hashlib.sha256(stdout.encode("utf-8")).hexdigest(),
        "assertions": ["manual review confirms the recorded result"],
    }


# --- verify_overrule pure function ---


def test_verify_overrule_rejects_empty_secret():
    valid, errors = verify_overrule(
        _valid_evidence(),
        "",
        _verification_evidence(),
    )
    assert not valid
    assert errors


def test_verify_overrule_rejects_non_string_secret():
    valid, errors = verify_overrule(
        _valid_evidence(),
        None,
        _verification_evidence(),
    )
    assert not valid
    assert errors


def test_verify_overrule_empty_assertions():
    evidence = _valid_evidence()
    evidence["assertions"] = []
    valid, errors = verify_overrule(evidence, "correct-secret", _verification_evidence())
    assert not valid
    assert any("assertions" in e for e in errors)


def test_verify_overrule_unknown_check_id():
    evidence = _valid_evidence(check_id="ghost")
    valid, errors = verify_overrule(evidence, "correct-secret", _verification_evidence())
    assert not valid
    assert any("not found" in e for e in errors)


def test_verify_overrule_wrong_stdout_hash():
    evidence = _valid_evidence()
    evidence["stdout_hash"] = hashlib.sha256(b"different").hexdigest()
    valid, errors = verify_overrule(evidence, "correct-secret", _verification_evidence())
    assert not valid
    assert any("stdout_hash does not match" in e for e in errors)


def test_verify_overrule_non_dict_evidence():
    valid, errors = verify_overrule("not-a-dict", "correct-secret", _verification_evidence())
    assert not valid
    assert errors


def test_verify_overrule_valid_passes():
    valid, errors = verify_overrule(_valid_evidence(), "correct-secret", _verification_evidence())
    assert valid
    assert errors == []


def test_verify_overrule_secret_hash_bound_in_supervisor(tmp_path, monkeypatch):
    """The pure function trusts the caller's secret hash gate; the supervisor is
    the actual secret-check boundary. A wrong secret fails at the supervisor."""
    state = _State()
    supervisor = _overrule_supervisor(tmp_path, state)
    evidence = _write_verification_evidence(supervisor)
    _write_state(supervisor)

    monkeypatch.setattr(
        "orchestrator.supervisor.load_state",
        lambda state_path, journal_dir: state,
    )

    assert supervisor._qc_overrule("task-1", evidence, "wrong-secret") == "FAILED"
    assert state.status == "QC_REJECTED"


def test_verify_overrule_constant_time_hash_compare():
    stdout = "recorded verification stdout"
    evidence = _valid_evidence()
    evidence["stdout_hash"] = hashlib.sha256(stdout.encode("utf-8")).hexdigest()
    valid, _ = verify_overrule(evidence, "correct-secret", _verification_evidence())
    assert valid
    evidence["stdout_hash"] = hashlib.sha256(b"x").hexdigest()
    valid, _ = verify_overrule(evidence, "correct-secret", _verification_evidence())
    assert not valid


# --- Supervisor._qc_overrule ---


class _Graph:
    def __init__(self):
        self.completed = []

    def mark_complete(self, task_id):
        self.completed.append(task_id)


class _State:
    def __init__(self, status="QC_REJECTED"):
        self.status = status
        self.data = {}
        self.events = []

    def transition(self, status, reason=""):
        self.events.append({"from": self.status, "to": status, "reason": reason})
        self.status = status


def _overrule_supervisor(tmp_path, state):
    supervisor = Supervisor.__new__(Supervisor)
    supervisor.run_dir = str(tmp_path / "run")
    supervisor._overrule_secret_hash = hashlib.sha256(b"correct-secret").hexdigest()
    supervisor.graph = _Graph()
    supervisor._task_run_dir = lambda task_id: str(tmp_path / "tasks" / task_id)
    supervisor._state_path = lambda task_id: str(tmp_path / "tasks" / task_id / "state.json")
    supervisor._safe_save = lambda saved_state, state_path: None
    return supervisor


def _write_verification_evidence(supervisor, task_id="task-1"):
    task_dir = supervisor._task_run_dir(task_id)
    os.makedirs(task_dir, exist_ok=True)
    stdout = "recorded verification stdout"
    with open(os.path.join(task_dir, "verification_evidence.json"), "w", encoding="utf-8") as f:
        json.dump(
            {
                "verification_results": [
                    {"check_id": "unit", "stdout": stdout},
                ]
            },
            f,
        )
    return _valid_evidence()


def _write_state(supervisor, task_id="task-1"):
    task_dir = supervisor._task_run_dir(task_id)
    os.makedirs(task_dir, exist_ok=True)
    state_path = supervisor._state_path(task_id)
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "task_id": task_id,
                "status": "QC_REJECTED",
                "attempt": 1,
                "changed_approaches": [],
                "events": [{"timestamp": "2026-01-01T00:00:00Z", "from": None, "to": "QC_REJECTED"}],
                "evidence": {},
                "worker_results": [],
                "data": {},
            },
            f,
        )


def test_qc_overrule_happy_path(tmp_path, monkeypatch):
    state = _State()
    supervisor = _overrule_supervisor(tmp_path, state)
    evidence = _write_verification_evidence(supervisor)
    _write_state(supervisor)

    monkeypatch.setattr(
        "orchestrator.supervisor.load_state",
        lambda state_path, journal_dir: state,
    )

    assert supervisor._qc_overrule("task-1", evidence, "correct-secret") == "FORCE_COMPLETE"
    assert state.status == "FORCE_COMPLETE"
    assert state.data["overrule_consumed"]["check_id"] == "unit"
    assert state.data["overrule_verified"] is True
    assert "secret" not in state.data["overrule_evidence"]


def test_qc_overrule_refuses_bad_evidence(tmp_path, monkeypatch):
    state = _State()
    supervisor = _overrule_supervisor(tmp_path, state)
    evidence = _write_verification_evidence(supervisor)
    _write_state(supervisor)
    evidence["stdout_hash"] = hashlib.sha256(b"wrong").hexdigest()

    monkeypatch.setattr(
        "orchestrator.supervisor.load_state",
        lambda state_path, journal_dir: state,
    )

    assert supervisor._qc_overrule("task-1", evidence, "correct-secret") == "FAILED"
    assert state.status == "QC_REJECTED"


def test_qc_overrule_wrong_secret(tmp_path, monkeypatch):
    state = _State()
    supervisor = _overrule_supervisor(tmp_path, state)
    evidence = _write_verification_evidence(supervisor)
    _write_state(supervisor)

    monkeypatch.setattr(
        "orchestrator.supervisor.load_state",
        lambda state_path, journal_dir: state,
    )

    assert supervisor._qc_overrule("task-1", evidence, "wrong-secret") == "FAILED"
    assert state.status == "QC_REJECTED"


def test_qc_overrule_refuses_terminal_state(tmp_path, monkeypatch):
    state = _State(status="COMPLETE")
    supervisor = _overrule_supervisor(tmp_path, state)
    evidence = _write_verification_evidence(supervisor)
    _write_state(supervisor)

    monkeypatch.setattr(
        "orchestrator.supervisor.load_state",
        lambda state_path, journal_dir: state,
    )

    assert supervisor._qc_overrule("task-1", evidence, "correct-secret") == "FAILED"
    assert state.status == "COMPLETE"


def test_force_complete_still_works_as_break_glass(tmp_path):
    from orchestrator import state as state_mod

    supervisor = Supervisor.__new__(Supervisor)
    supervisor._task_run_dir = lambda task_id: str(tmp_path / task_id)
    supervisor._state_path = lambda task_id: str(tmp_path / task_id / "state.json")
    supervisor._safe_save = state_mod.save_state
    supervisor.graph = _Graph()

    task_id = "task-1"
    st = state_mod.State(task_id=task_id, status="QC_REJECTED")
    state_path = supervisor._state_path(task_id)
    state_mod.save_state(st, state_path)

    assert supervisor._force_complete_task(task_id, reason="break-glass") == "FORCE_COMPLETE"
    assert supervisor.graph.completed == [task_id]


def test_qc_overrule_consumes_once_across_concurrent_callers_and_restart(tmp_path, monkeypatch):
    state = _State()
    supervisor_a = _overrule_supervisor(tmp_path, state)
    supervisor_b = _overrule_supervisor(tmp_path, state)
    evidence = _write_verification_evidence(supervisor_a)
    _write_state(supervisor_a)

    monkeypatch.setattr(
        "orchestrator.supervisor.load_state",
        lambda state_path, journal_dir: state,
    )

    results = []
    barrier = threading.Barrier(2)

    def invoke(supervisor):
        barrier.wait()
        results.append(supervisor._qc_overrule("task-1", dict(evidence), "correct-secret"))

    first = threading.Thread(target=invoke, args=(supervisor_a,))
    second = threading.Thread(target=invoke, args=(supervisor_b,))
    first.start()
    second.start()
    first.join()
    second.join()

    assert sorted(results) == ["FAILED", "FORCE_COMPLETE"]
    assert state.status == "FORCE_COMPLETE"
    assert state.data["overrule_consumed"]["check_id"] == "unit"

    marker_path = tmp_path / "tasks" / "task-1" / "overrule_consumed.json"
    assert marker_path.is_file()

    restarted_state = _State(status="QC_REJECTED")
    supervisor_after_restart = _overrule_supervisor(tmp_path, restarted_state)
    monkeypatch.setattr(
        "orchestrator.supervisor.load_state",
        lambda state_path, journal_dir: restarted_state,
    )

    assert supervisor_after_restart._qc_overrule("task-1", dict(evidence), "correct-secret") == "FAILED"
    assert restarted_state.status == "QC_REJECTED"


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits only")
def test_overrule_secret_file_is_created_mode_0600(tmp_path):
    supervisor = Supervisor.__new__(Supervisor)
    supervisor.run_dir = str(tmp_path / "run")

    secret_hash = supervisor._load_or_create_overrule_secret_hash()

    secret_path = tmp_path / "run" / "overrule.secret"
    assert secret_path.is_file()
    assert secret_hash == hashlib.sha256(secret_path.read_text(encoding="utf-8").strip().encode("utf-8")).hexdigest()
    assert (secret_path.stat().st_mode & 0o777) == 0o600

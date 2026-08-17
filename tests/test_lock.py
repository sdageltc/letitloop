"""Tests for execution lock module."""

import os
import json
import time
import pytest
from orchestrator import lock as lk
from orchestrator.goal import Goal
from orchestrator.generator import generate_contracts
from orchestrator.supervisor import Supervisor


@pytest.fixture(autouse=True)
def set_fake_worker(monkeypatch):
    monkeypatch.setenv("FAKE_WORKER", "1")


def test_acquire_and_release(tmp_path):
    """Basic acquire and release cycle."""
    run_dir = str(tmp_path)
    lock = lk.acquire_lock("goal-1", run_dir)
    assert lock["goal_id"] == "goal-1"
    assert lock["pid"] == os.getpid()
    assert lk.is_locked(run_dir)
    assert not lk.is_locked_by_other(run_dir)

    released = lk.release_lock(run_dir)
    assert released
    assert not lk.is_locked(run_dir)


def test_double_acquire_fails(tmp_path):
    """Second acquire on same run_dir raises LockHeldError."""
    run_dir = str(tmp_path)
    lk.acquire_lock("goal-1", run_dir)
    with pytest.raises(lk.LockHeldError):
        lk.acquire_lock("goal-1", run_dir)
    lk.release_lock(run_dir)


def test_release_no_lock(tmp_path):
    """release_lock returns False when no lock exists."""
    run_dir = str(tmp_path)
    assert not lk.release_lock(run_dir)


def test_force_acquire_overrides(tmp_path):
    """force=True acquires even if lock exists."""
    run_dir = str(tmp_path)
    lk.acquire_lock("goal-1", run_dir)
    lock = lk.acquire_lock("goal-2", run_dir, force=True)
    assert lock["goal_id"] == "goal-2"
    lk.release_lock(run_dir)


def test_lock_info(tmp_path):
    """lock_info returns lock data or None."""
    run_dir = str(tmp_path)
    assert lk.lock_info(run_dir) is None
    lk.acquire_lock("goal-1", run_dir)
    info = lk.lock_info(run_dir)
    assert info is not None
    assert info["goal_id"] == "goal-1"
    lk.release_lock(run_dir)


def test_stale_lock(tmp_path, monkeypatch):
    """Lock older than STALE_TIMEOUT_SEC is detected as stale."""
    run_dir = str(tmp_path)
    lk.acquire_lock("goal-1", run_dir)

    # Manually age the lock file (heartbeat is authoritative, so age it too)
    lock_path = os.path.join(run_dir, lk.LOCK_FILENAME)
    with open(lock_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    # Set created_at and heartbeat far in the past
    from datetime import datetime, timezone, timedelta
    old = (datetime.now(timezone.utc) - timedelta(seconds=lk.STALE_TIMEOUT_SEC + 60)).isoformat()
    data["created_at"] = old
    data["heartbeat"] = old
    with open(lock_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    # Acquire should raise LockStaleError
    with pytest.raises(lk.LockStaleError):
        lk.acquire_lock("goal-1", run_dir)

    # force=True should work on stale lock
    lock = lk.acquire_lock("goal-1", run_dir, force=True)
    assert lock["goal_id"] == "goal-1"
    lk.release_lock(run_dir)


def test_is_locked_by_other(tmp_path):
    """is_locked_by_other returns True when PID differs."""
    run_dir = str(tmp_path)
    lk.acquire_lock("goal-1", run_dir)

    # Write lock with different PID
    lock_path = os.path.join(run_dir, lk.LOCK_FILENAME)
    with open(lock_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    data["pid"] = 99999
    with open(lock_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    assert lk.is_locked_by_other(run_dir)
    lk.release_lock(run_dir)


def test_lock_integration_supervisor(tmp_path):
    """Supervisor executes with lock acquired."""
    ws_dir = str(tmp_path)
    run_dir = os.path.join(ws_dir, "scratch", "runs")
    goal = Goal(goal_id="lock-sup", title="Lock supervisor", description="Test lock in supervisor")
    plan = generate_contracts(goal, workspace_root=ws_dir)
    supervisor = Supervisor(goal, plan, workspace_root=ws_dir, run_dir=run_dir)

    # execute_plan_with_retry should acquire and release lock
    res = supervisor.execute_plan_with_retry()
    assert all(s in ("COMPLETE", "complete") for s in res.values())

    # Lock should be released after completion
    assert not lk.is_locked(run_dir)


def test_lock_resume(tmp_path):
    """Supervisor resume works with lock."""
    ws_dir = str(tmp_path)
    run_dir = os.path.join(ws_dir, "scratch", "runs")
    goal = Goal(goal_id="lock-resume", title="Lock resume", description="Test lock in resume")
    plan = generate_contracts(goal, workspace_root=ws_dir)
    supervisor = Supervisor(goal, plan, workspace_root=ws_dir, run_dir=run_dir)
    res = supervisor.execute_plan()
    assert all(s in ("COMPLETE", "complete") for s in res.values())

    # Resume should acquire and release
    supervisor2 = Supervisor(goal, plan, workspace_root=ws_dir, run_dir=run_dir)
    res2 = supervisor2.resume_plan()
    assert all(s in ("COMPLETE", "complete") for s in res2.values())
    assert not lk.is_locked(run_dir)


def test_lock_held_during_execution(tmp_path):
    """Lock is held during execution, preventing second acquire."""
    ws_dir = str(tmp_path)
    run_dir = os.path.join(ws_dir, "scratch", "runs")
    goal = Goal(goal_id="lock-held", title="Lock held", description="Test lock prevents second")

    # Acquire lock manually
    lk.acquire_lock(goal.goal_id, run_dir)

    # Supervisor should fail with lock held
    plan = generate_contracts(goal, workspace_root=ws_dir)
    supervisor = Supervisor(goal, plan, workspace_root=ws_dir, run_dir=run_dir)
    res = supervisor.execute_plan_with_retry()
    assert len(res) == 0  # Empty result means lock held
    assert goal.status == "FAILED"

    lk.release_lock(run_dir)


class TestProcessStartToken:
    """PID start-time token prevents Windows PID-reuse false liveness
    (kimi-k2 finding, P1)."""

    def test_acquired_lock_contains_token(self, tmp_path):
        run_dir = str(tmp_path)
        lock = lk.acquire_lock("goal-tok", run_dir)
        assert "process_start_token" in lock
        assert lock["process_start_token"]
        lk.release_lock(run_dir)

    def test_pid_alive_with_matching_token(self):
        assert lk._pid_alive(os.getpid(), lk._process_start_token(os.getpid())) is True

    def test_pid_alive_with_wrong_token(self):
        assert lk._pid_alive(os.getpid(), "win:not-the-real-token") is False

    def test_pid_alive_without_token_falls_back_to_kill(self):
        assert lk._pid_alive(os.getpid()) is True
        assert lk._pid_alive(999999999) is False

    def test_stale_when_token_mismatch_even_fresh_heartbeat(self, tmp_path):
        """A dead/lost process with a fresh heartbeat is still stale — token
        mismatch beats a fresh heartbeat."""
        run_dir = str(tmp_path)
        lk.acquire_lock("goal-stale-tok", run_dir)

        lock_path = os.path.join(run_dir, lk.LOCK_FILENAME)
        with open(lock_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        from datetime import datetime, timezone
        data["heartbeat"] = datetime.now(timezone.utc).isoformat()
        data["process_start_token"] = "win:dead-process-token"
        data["pid"] = 999999999
        with open(lock_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

        assert lk._lock_is_stale(run_dir) is True
        lk.release_lock(run_dir)

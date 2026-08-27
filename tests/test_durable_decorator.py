"""Unit tests for the @durable Trojan decorator and atomic_marker side-effect guards."""

import dataclasses
import threading

import pytest

from letitloop import (
    DurableSerializationError,
    atomic_marker,
    durable,
    step,
)


@dataclasses.dataclass
class UserProfile:
    user_id: int
    username: str
    roles: list[str]


def test_durable_step_skipping_on_resume(tmp_path):
    wal_dir = str(tmp_path / "wal_run")
    step1_calls = 0
    step2_calls = 0

    def fn_step1(x: int) -> int:
        nonlocal step1_calls
        step1_calls += 1
        return x * 2

    def fn_step2(y: int) -> int:
        nonlocal step2_calls
        step2_calls += 1
        return y + 10

    @durable(goal_id="workflow_math", wal_dir=wal_dir)
    def run_math_pipeline(val: int) -> int:
        v1 = step("double", fn_step1, val)
        v2 = step("add_ten", fn_step2, v1)
        return v2

    # First execution: both steps execute
    res1 = run_math_pipeline(5)
    assert res1 == 20
    assert step1_calls == 1
    assert step2_calls == 1

    # Second execution (simulating resume after script restart):
    # Completed steps are skipped from WAL cache
    res2 = run_math_pipeline(5)
    assert res2 == 20
    assert step1_calls == 1  # SKIPPED
    assert step2_calls == 1  # SKIPPED


def test_durable_dataclass_serialization(tmp_path):
    wal_dir = str(tmp_path / "wal_dataclass")
    call_count = 0

    def fetch_user(uid: int) -> UserProfile:
        nonlocal call_count
        call_count += 1
        return UserProfile(user_id=uid, username="alice", roles=["admin"])

    @durable(goal_id="workflow_user", wal_dir=wal_dir)
    def run_user_pipeline(uid: int) -> dict:
        user = step("get_user", fetch_user, uid)
        return user

    res1 = run_user_pipeline(42)
    assert res1["user_id"] == 42
    assert res1["username"] == "alice"
    assert call_count == 1

    # Resume skips execution and returns cached dict
    res2 = run_user_pipeline(42)
    assert res2["user_id"] == 42
    assert call_count == 1


def test_durable_non_serializable_raises_error(tmp_path):
    wal_dir = str(tmp_path / "wal_bad_serial")

    def return_thread_lock():
        return threading.Lock()

    @durable(goal_id="workflow_bad", wal_dir=wal_dir)
    def run_bad():
        return step("lock_step", return_thread_lock)

    with pytest.raises(DurableSerializationError) as exc_info:
        run_bad()
    assert "returned non-serializable object" in str(exc_info.value)


def test_atomic_marker_prevents_duplicate_side_effect(tmp_path):
    wal_dir = str(tmp_path / "wal_marker")
    side_effect_count = 0

    @durable(goal_id="workflow_payment", wal_dir=wal_dir)
    def run_payment():
        nonlocal side_effect_count
        with atomic_marker("charge_card_123", run_dir=wal_dir) as should_run:
            if should_run:
                side_effect_count += 1

    # First run: marker claimed, mutation runs
    run_payment()
    assert side_effect_count == 1

    # Second run: marker already exists on disk, mutation skipped!
    run_payment()
    assert side_effect_count == 1


def test_step_outside_durable_context_raises_by_default(monkeypatch):
    monkeypatch.delenv("LETITLOOP_LENIENT", raising=False)

    def simple_fn(x):
        return x + 1

    with pytest.raises(RuntimeError) as exc_info:
        step("orphan_step", simple_fn, 5)

    assert "step('orphan_step') called outside a @durable context" in str(exc_info.value)
    assert "LETITLOOP_LENIENT=1" in str(exc_info.value)


def test_step_outside_durable_context_lenient(monkeypatch):
    monkeypatch.setenv("LETITLOOP_LENIENT", "1")

    def simple_fn(x):
        return x * 3

    res = step("lenient_step", simple_fn, 4)
    assert res == 12


def test_step_outputs_no_duplicate_keys(tmp_path):
    wal_dir = str(tmp_path / "wal_no_dup")

    @durable(goal_id="workflow_clean_data", wal_dir=wal_dir)
    def run_clean():
        step("s1", lambda: "val1")
        step("s2", lambda: "val2")

    run_clean()

    from orchestrator.state import load_state

    state = load_state(str(tmp_path / "wal_no_dup" / "state.json"))
    # step_outputs dict exists with both keys
    assert state.data.get("step_outputs") == {"s1": "val1", "s2": "val2"}
    # duplicate step_output_s1 / step_output_s2 are not created
    assert "step_output_s1" not in state.data
    assert "step_output_s2" not in state.data

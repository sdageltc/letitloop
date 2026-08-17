"""Tests for Phase 3 operator controls (pause, cancel, inspect)."""

import os
import pytest
from unittest.mock import patch

from orchestrator.state import State, create_initial_state, load_state, save_state
from orchestrator.goal import Goal, Plan
from orchestrator.supervisor import Supervisor


def _make_contract(task_id):
    return {
        'task_id': task_id,
        'depends_on': [],
        'status': 'DRAFTED',
        'contract': {
            'task_id': task_id,
            'title': f'Task {task_id}',
            'status': 'DRAFTED',
            'risk_tier': 'auto',
            'workspace_scope': {'allow': ['scratch/'], 'deny': []},
            'objective': 'operator control test',
            'worker': {'model': 'test', 'max_attempts': 1},
            'inputs': [],
            'outputs': [{'path': f'scratch/{task_id}_out.txt'}],
            'acceptance_checks': [{'id': f'{task_id}-chk', 'kind': 'file_exists', 'path': f'scratch/{task_id}_out.txt', 'expected': True}],
            'qc': {'required': False, 'lens': 'code_correctness'},
        },
    }


def test_pause_state_legal():
    state = State(task_id='t1', status='READY')
    res = state.pause()
    assert res is True
    assert state.status == 'PAUSED'


def test_pause_from_terminal_fails():
    state = State(task_id='t1', status='COMPLETE')
    res = state.pause()
    assert res is False
    assert state.status == 'COMPLETE'


def test_cancel_state_legal():
    state = State(task_id='t1', status='READY')
    res = state.cancel()
    assert res is True
    assert state.status == 'CANCELLED'


def test_cancel_from_terminal_fails():
    state = State(task_id='t1', status='COMPLETE')
    res = state.cancel()
    assert res is False
    assert state.status == 'COMPLETE'


def test_cancel_is_terminal():
    state = State(task_id='t1', status='CANCELLED')
    assert state.is_terminal() is True


def test_paused_can_resume():
    state = State(task_id='t1', status='PAUSED')
    assert 'READY' in state.legal_transitions()


def test_paused_can_cancel():
    state = State(task_id='t1', status='PAUSED')
    assert 'CANCELLED' in state.legal_transitions()


def test_supervisor_pause_plan(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_WORKER", "1")
    ws_dir = str(tmp_path)
    run_dir = os.path.join(ws_dir, "scratch", "runs")
    goal = Goal(goal_id="g1", title="Test Goal", description="Test Description")
    plan = Plan(goal_id="g1", contracts=[_make_contract("t1")])
    supervisor = Supervisor(goal, plan, workspace_root=ws_dir, run_dir=run_dir)

    t1_state_path = supervisor._state_path("t1")
    initial_state = State(task_id="t1", status="READY")
    save_state(initial_state, t1_state_path)

    res = supervisor.pause_plan()
    assert res.get("t1") == "PAUSED"
    assert goal.status == "PAUSED"
    loaded = load_state(t1_state_path)
    assert loaded.status == "PAUSED"


def test_supervisor_cancel_plan(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_WORKER", "1")
    ws_dir = str(tmp_path)
    run_dir = os.path.join(ws_dir, "scratch", "runs")
    goal = Goal(goal_id="g1", title="Test Goal", description="Test Description")
    plan = Plan(goal_id="g1", contracts=[_make_contract("t1")])
    supervisor = Supervisor(goal, plan, workspace_root=ws_dir, run_dir=run_dir)

    t1_state_path = supervisor._state_path("t1")
    initial_state = State(task_id="t1", status="READY")
    save_state(initial_state, t1_state_path)

    res = supervisor.cancel_plan()
    assert res.get("t1") == "CANCELLED"
    assert goal.status == "CANCELLED"
    loaded = load_state(t1_state_path)
    assert loaded.status == "CANCELLED"


def test_supervisor_inspect_task(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_WORKER", "1")
    ws_dir = str(tmp_path)
    run_dir = os.path.join(ws_dir, "scratch", "runs")
    goal = Goal(goal_id="g1", title="Test Goal", description="Test Description")
    plan = Plan(goal_id="g1", contracts=[_make_contract("t1")])
    supervisor = Supervisor(goal, plan, workspace_root=ws_dir, run_dir=run_dir)

    t1_state_path = supervisor._state_path("t1")
    initial_state = State(task_id="t1", status="READY")
    save_state(initial_state, t1_state_path)

    info = supervisor.inspect_task("t1")
    assert info["task_id"] == "t1"
    assert info["status"] == "READY"
    assert info["is_terminal"] is False
    assert "legal_transitions" in info


def test_supervisor_inspect_missing_task(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_WORKER", "1")
    ws_dir = str(tmp_path)
    run_dir = os.path.join(ws_dir, "scratch", "runs")
    goal = Goal(goal_id="g1", title="Test Goal", description="Test Description")
    plan = Plan(goal_id="g1", contracts=[])
    supervisor = Supervisor(goal, plan, workspace_root=ws_dir, run_dir=run_dir)

    info = supervisor.inspect_task("missing_t")
    assert info["task_id"] == "missing_t"
    assert "error" in info


def test_pause_records_reason():
    state = State(task_id="t1", status="READY")
    state.pause("need coffee")
    assert len(state.events) > 0
    assert "need coffee" in state.events[-1]["reason"]


def test_cancel_records_reason():
    state = State(task_id="t1", status="READY")
    state.cancel("scope changed")
    assert len(state.events) > 0
    assert "scope changed" in state.events[-1]["reason"]


def test_supervisor_pause_then_resume(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_WORKER", "1")
    ws_dir = str(tmp_path)
    run_dir = os.path.join(ws_dir, "scratch", "runs")
    goal = Goal(goal_id="g1", title="Test Goal", description="Test Description")
    plan = Plan(goal_id="g1", contracts=[_make_contract("t1")])
    supervisor = Supervisor(goal, plan, workspace_root=ws_dir, run_dir=run_dir)

    t1_state_path = supervisor._state_path("t1")
    initial_state = State(task_id="t1", status="READY")
    save_state(initial_state, t1_state_path)

    supervisor.pause_plan("pause for test")
    assert goal.status == "PAUSED"

    res = supervisor.resume_plan()
    assert res.get("t1") in ("COMPLETE", "complete")
    assert goal.status in ("COMPLETE", "complete")

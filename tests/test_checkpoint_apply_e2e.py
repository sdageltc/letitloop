"""End-to-end tests for checkpoint apply and state rehydration."""

import os
from unittest.mock import MagicMock

import pytest
from orchestrator.checkpoint import (
    apply_checkpoint,
    save_checkpoint,
)
from orchestrator.cli import cmd_checkpoint_recover


@pytest.mark.fast
def test_apply_checkpoint_e2e(tmp_path):
    run_dir = str(tmp_path / "run_g1")
    os.makedirs(run_dir, exist_ok=True)

    plan_contracts = [
        {"task_id": "t1", "title": "Task 1", "objective": "Do task 1", "status": "COMPLETE"},
        {"task_id": "t2", "title": "Task 2", "objective": "Do task 2", "status": "DRAFTED"},
    ]
    results = {"t1": {"status": "COMPLETE"}}
    graph_statuses = {"t1": "COMPLETE", "t2": "DRAFTED"}
    evidence_store = {"t1": ["evidence/t1/out.txt"]}

    # Save a checkpoint
    cp_path = save_checkpoint(
        run_dir=run_dir,
        iteration=3,
        plan_contracts=plan_contracts,
        results=results,
        graph_statuses=graph_statuses,
        evidence_store=evidence_store,
        goal_status="EXECUTING",
        total_contracts=2,
    )
    assert os.path.isfile(cp_path)

    # Corrupt or remove plan.json if it existed
    plan_file = os.path.join(run_dir, "plan.json")
    if os.path.exists(plan_file):
        os.remove(plan_file)

    # Apply the checkpoint
    applied = apply_checkpoint(run_dir=run_dir, workspace_root=str(tmp_path))
    assert applied["recovered"] is True
    assert applied["iteration"] == 3
    assert len(applied["plan_contracts"]) == 2

    # Verify plan.json rehydrated
    assert os.path.isfile(plan_file)

    # Verify state files for each task rehydrated
    t1_state = os.path.join(run_dir, "t1", "state.json")
    t2_state = os.path.join(run_dir, "t2", "state.json")
    assert os.path.isfile(t1_state)
    assert os.path.isfile(t2_state)


@pytest.mark.fast
def test_cli_checkpoint_recover_apply(tmp_path, monkeypatch, capsys):
    goal_id = "g_cp_cli"
    run_dir = str(tmp_path / "runs" / goal_id)
    os.makedirs(run_dir, exist_ok=True)

    # Mock goal loader and run_dir
    mock_goal = MagicMock()
    mock_goal.goal_id = goal_id
    monkeypatch.setattr("orchestrator.cli._load_goal", lambda gid: mock_goal)
    monkeypatch.setattr("orchestrator.cli._run_dir", lambda gid: run_dir)

    plan_contracts = [{"task_id": "c1", "title": "C1", "status": "VERIFIED"}]
    save_checkpoint(
        run_dir=run_dir,
        iteration=5,
        plan_contracts=plan_contracts,
        results={"c1": {"status": "VERIFIED"}},
        graph_statuses={"c1": "VERIFIED"},
        evidence_store={},
        goal_status="EXECUTING",
        total_contracts=1,
    )

    args = MagicMock()
    args.goal_id = goal_id
    args.json = False
    args.apply = True

    cmd_checkpoint_recover(args)
    out = capsys.readouterr().out
    assert "Successfully applied checkpoint" in out
    assert "iteration 5" in out
    assert "Restored 1 contracts" in out

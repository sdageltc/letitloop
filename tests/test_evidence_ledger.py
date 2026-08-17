"""Tests for persistent evidence ledger."""

import os
import pytest
from orchestrator import evidence as ev
from orchestrator.goal import Goal
from orchestrator.generator import generate_contracts
from orchestrator.supervisor import Supervisor


@pytest.fixture(autouse=True)
def set_fake_worker(monkeypatch):
    monkeypatch.setenv("FAKE_WORKER", "1")


def test_ledger_created_after_execution(tmp_path):
    ws_dir = str(tmp_path)
    run_dir = os.path.join(ws_dir, "scratch", "runs")
    goal = Goal(goal_id="ledger-test", title="Ledger test", description="Test evidence ledger")
    plan = generate_contracts(goal, workspace_root=ws_dir)
    supervisor = Supervisor(goal, plan, workspace_root=ws_dir, run_dir=run_dir)
    supervisor.execute_plan()

    ledger = ev.load_ledger(run_dir)
    assert len(ledger) > 0
    # Each completed task should have entries
    for tid in ledger:
        assert len(ledger[tid]) > 0
        for entry in ledger[tid]:
            assert "sha256" in entry
            assert "size_bytes" in entry
            assert "timestamp" in entry


def test_ledger_survives_new_supervisor(tmp_path):
    ws_dir = str(tmp_path)
    run_dir = os.path.join(ws_dir, "scratch", "runs")
    goal = Goal(goal_id="ledger-survive", title="Survive test", description="Test ledger persistence")
    plan = generate_contracts(goal, workspace_root=ws_dir)

    supervisor = Supervisor(goal, plan, workspace_root=ws_dir, run_dir=run_dir)
    supervisor.execute_plan()

    # New supervisor without calling execute — should rebuild from ledger
    supervisor2 = Supervisor(goal, plan, workspace_root=ws_dir, run_dir=run_dir)
    ledger = ev.load_ledger(run_dir)
    store = ev.rebuild_evidence_store(ledger)
    assert len(store) > 0
    for tid, paths in store.items():
        assert all(os.path.isfile(p) for p in paths)


def test_ledger_stale_detection(tmp_path):
    ws_dir = str(tmp_path)
    run_dir = os.path.join(ws_dir, "scratch", "runs")
    goal = Goal(goal_id="ledger-stale", title="Stale test", description="Test staleness")
    plan = generate_contracts(goal, workspace_root=ws_dir)

    supervisor = Supervisor(goal, plan, workspace_root=ws_dir, run_dir=run_dir)
    supervisor.execute_plan()

    # Delete an output file to create staleness
    ledger = ev.load_ledger(run_dir)
    for tid, entries in ledger.items():
        for e in entries:
            ap = e.get("absolute_path", "")
            if ap and os.path.isfile(ap):
                os.remove(ap)
                break

    issues = ev.check_evidence_freshness(run_dir)
    assert len(issues) > 0
    assert any(i["issue"] == "file_missing" for i in issues)


def test_rebuild_evidence_store_from_ledger(tmp_path):
    ws_dir = str(tmp_path)
    run_dir = os.path.join(ws_dir, "scratch", "runs")
    goal = Goal(goal_id="ledger-rebuild", title="Rebuild test", description="Test rebuild")
    plan = generate_contracts(goal, workspace_root=ws_dir)

    supervisor = Supervisor(goal, plan, workspace_root=ws_dir, run_dir=run_dir)
    supervisor.execute_plan()

    ledger = ev.load_ledger(run_dir)
    store = ev.rebuild_evidence_store(ledger)
    assert len(store) > 0
    for tid, paths in store.items():
        assert len(paths) > 0
        # All paths should exist on disk
        assert all(os.path.isfile(p) for p in paths)

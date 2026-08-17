"""Tests for checkpoint persistence and recovery mechanism."""

import os

import pytest

from orchestrator.checkpoint import (
    clear_checkpoints,
    list_checkpoints,
    load_checkpoint,
    recover_from_checkpoint,
    save_checkpoint,
)

pytestmark = pytest.mark.fast


def test_save_checkpoint_creates_file(tmp_path):
    run_dir = str(tmp_path)
    path = save_checkpoint(
        run_dir=run_dir,
        iteration=1,
        plan_contracts=[{"id": "c1"}],
        results={"c1": {"status": "ok"}},
        graph_statuses={"c1": "COMPLETED"},
        evidence_store={"c1": ["ev1"]},
        goal_status="IN_PROGRESS",
        total_contracts=1,
    )
    assert isinstance(path, str)
    assert os.path.exists(path)
    assert os.path.basename(path) == "checkpoint_0001.json"
    assert os.path.exists(os.path.join(run_dir, "checkpoints", "checkpoint_0001.json"))


def test_load_checkpoint_returns_data(tmp_path):
    run_dir = str(tmp_path)
    save_checkpoint(
        run_dir=run_dir,
        iteration=1,
        plan_contracts=[{"id": "c1"}],
        results={"c1": {"res": 42}},
        graph_statuses={"c1": "PASSED"},
        evidence_store={"c1": ["e1"]},
        goal_status="RUNNING",
        total_contracts=5,
    )
    data = load_checkpoint(run_dir)
    assert data is not None
    assert data["iteration"] == 1
    assert data["plan_contracts"] == [{"id": "c1"}]
    assert data["results"] == {"c1": {"res": 42}}
    assert data["graph_statuses"] == {"c1": "PASSED"}
    assert data["evidence_store"] == {"c1": ["e1"]}
    assert data["goal_status"] == "RUNNING"
    assert data["total_contracts"] == 5


def test_load_checkpoint_none_if_missing(tmp_path):
    run_dir = str(tmp_path)
    data = load_checkpoint(run_dir)
    assert data is None


def test_list_checkpoints_empty(tmp_path):
    run_dir = str(tmp_path)
    entries = list_checkpoints(run_dir)
    assert entries == []


def test_list_checkpoints_multiple(tmp_path):
    run_dir = str(tmp_path)
    save_checkpoint(run_dir, 1, [], {}, {}, {}, goal_status="INIT", total_contracts=2)
    save_checkpoint(run_dir, 2, [], {}, {}, {}, goal_status="PROGRESS", total_contracts=2)

    entries = list_checkpoints(run_dir)
    assert len(entries) == 2
    assert entries[0]["iteration"] == 1
    assert entries[0]["goal_status"] == "INIT"
    assert entries[1]["iteration"] == 2
    assert entries[1]["goal_status"] == "PROGRESS"
    for entry in entries:
        assert "path" in entry
        assert "timestamp" in entry
        assert "total_contracts" in entry


def test_prune_old_checkpoints(tmp_path):
    run_dir = str(tmp_path)
    for i in range(1, 5):
        save_checkpoint(run_dir, i, [], {}, {}, {}, max_checkpoints=2)

    entries = list_checkpoints(run_dir)
    assert len(entries) == 2
    assert [e["iteration"] for e in entries] == [3, 4]

    cp_dir = os.path.join(run_dir, "checkpoints")
    assert not os.path.exists(os.path.join(cp_dir, "checkpoint_0001.json"))
    assert not os.path.exists(os.path.join(cp_dir, "checkpoint_0002.json"))
    assert os.path.exists(os.path.join(cp_dir, "checkpoint_0003.json"))
    assert os.path.exists(os.path.join(cp_dir, "checkpoint_0004.json"))


def test_recover_from_checkpoint_success(tmp_path):
    run_dir = str(tmp_path)
    save_checkpoint(
        run_dir,
        3,
        [{"id": "c1"}],
        {"c1": {"res": "ok"}},
        {"c1": "SUCCESS"},
        {"c1": ["e1"]},
        goal_status="ACTIVE",
        total_contracts=10,
    )
    rec = recover_from_checkpoint(run_dir)
    assert rec["recovered"] is True
    assert rec["iteration"] == 3
    assert rec["plan_contracts"] == [{"id": "c1"}]
    assert rec["results"] == {"c1": {"res": "ok"}}
    assert rec["graph_statuses"] == {"c1": "SUCCESS"}
    assert rec["evidence_store"] == {"c1": ["e1"]}
    assert rec["goal_status"] == "ACTIVE"


def test_recover_from_checkpoint_missing(tmp_path):
    run_dir = str(tmp_path)
    rec = recover_from_checkpoint(run_dir)
    assert rec == {"recovered": False}


def test_clear_checkpoints(tmp_path):
    run_dir = str(tmp_path)
    for i in range(1, 4):
        save_checkpoint(run_dir, i, [], {}, {}, {})

    assert len(list_checkpoints(run_dir)) == 3
    removed_count = clear_checkpoints(run_dir)
    assert removed_count == 3
    assert list_checkpoints(run_dir) == []
    assert load_checkpoint(run_dir) is None


def test_checkpoint_preserves_structure(tmp_path):
    run_dir = str(tmp_path)
    save_checkpoint(
        run_dir=run_dir,
        iteration=1,
        plan_contracts=[{"key": "val"}],
        results={"key": "res"},
        graph_statuses={"key": "DONE"},
        evidence_store={"key": ["file.txt"]},
        goal_status="DONE",
        total_contracts=3,
    )
    data = load_checkpoint(run_dir)
    expected_keys = {
        "timestamp",
        "iteration",
        "goal_status",
        "total_contracts",
        "plan_contracts",
        "results",
        "graph_statuses",
        "evidence_store",
    }
    assert expected_keys.issubset(data.keys())


def test_checkpoint_iteration_increments(tmp_path):
    run_dir = str(tmp_path)
    p1 = save_checkpoint(run_dir, 1, [], {}, {}, {})
    p2 = save_checkpoint(run_dir, 2, [], {}, {}, {})
    p3 = save_checkpoint(run_dir, 3, [], {}, {}, {})

    assert os.path.basename(p1) == "checkpoint_0001.json"
    assert os.path.basename(p2) == "checkpoint_0002.json"
    assert os.path.basename(p3) == "checkpoint_0003.json"

    entries = list_checkpoints(run_dir)
    iterations = [e["iteration"] for e in entries]
    assert iterations == [1, 2, 3]


def test_load_latest_checkpoint(tmp_path):
    run_dir = str(tmp_path)
    save_checkpoint(run_dir, 1, [], {}, {}, {}, goal_status="FIRST")
    save_checkpoint(run_dir, 5, [], {}, {}, {}, goal_status="LATEST")
    save_checkpoint(run_dir, 3, [], {}, {}, {}, goal_status="MIDDLE")

    data = load_checkpoint(run_dir)
    assert data is not None
    assert data["iteration"] == 5
    assert data["goal_status"] == "LATEST"

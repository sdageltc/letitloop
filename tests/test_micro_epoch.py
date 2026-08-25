"""
tests/test_micro_epoch.py
Unit tests for the Write-Ahead Log (WAL) Micro-Epoch Manager.
"""

import json
import tempfile
from pathlib import Path

from orchestrator.micro_epoch import MicroEpochManager


def test_micro_epoch_records_and_checkpoints():
    with tempfile.TemporaryDirectory() as td:
        state_dir = Path(td) / "state"
        mgr = MicroEpochManager(state_dir)

        rec1 = mgr.record_task_completion("task-1", "Mutated core.py: +5 / -2 lines.")
        assert rec1.task_id == "task-1"
        assert rec1.is_committed is True
        assert mgr.wal_file.exists()

        lines = mgr.wal_file.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["task_id"] == "task-1"

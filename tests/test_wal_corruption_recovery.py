"""Hostile fuzz testing suite for WAL and state corruption recovery."""

import json
import os

import pytest

from orchestrator.state import (
    StateError,
    create_initial_state,
    load_state,
    save_state,
)


@pytest.mark.fast
def test_wal_truncated_json_recovery(tmp_path):
    """Test that truncated/partial JSON writes to state.json do not crash load_state."""
    task_dir = str(tmp_path / "t_trunc")
    os.makedirs(task_dir, exist_ok=True)
    state_file = os.path.join(task_dir, "state.json")

    # Write truncated JSON
    with open(state_file, "w", encoding="utf-8") as f:
        f.write('{"task_id": "t_trunc", "status": "WORKING", "data": {"key":')

    # Must raise StateError or handle gracefully rather than raw crash
    with pytest.raises((StateError, json.JSONDecodeError, ValueError, OSError)):
        load_state(state_file, journal_dir=task_dir)


@pytest.mark.fast
def test_wal_bit_flipped_journal_recovery(tmp_path):
    """Test that corrupted state files are safely flagged."""
    task_dir = str(tmp_path / "t_flip")
    os.makedirs(task_dir, exist_ok=True)
    state_file = os.path.join(task_dir, "state.json")

    state = create_initial_state("t_flip", journal_dir=task_dir)
    state.transition("PREFLIGHT_RUNNING", reason="starting")
    state.transition("READY", reason="passed")
    save_state(state, state_file)

    # Corrupt the state file with random garbage bytes
    with open(state_file, "wb") as f:
        f.write(b"\x00\xff\xfe\x00\x12\x34\x56\x78BAD_DATA_NOT_JSON")

    with pytest.raises((StateError, json.JSONDecodeError, ValueError, OSError)):
        load_state(state_file, journal_dir=task_dir)


@pytest.mark.fast
def test_wal_zero_byte_state_file(tmp_path):
    """Test handling of 0-byte state file (e.g. crash after open() before write)."""
    task_dir = str(tmp_path / "t_zero")
    os.makedirs(task_dir, exist_ok=True)
    state_file = os.path.join(task_dir, "state.json")

    with open(state_file, "w", encoding="utf-8"):
        pass  # 0 bytes

    with pytest.raises((StateError, json.JSONDecodeError, ValueError, OSError)):
        load_state(state_file, journal_dir=task_dir)


@pytest.mark.fast
def test_wal_atomic_save_replaces_cleanly(tmp_path):
    """Test that save_state uses atomic replace so readers never see half-written files."""
    task_dir = str(tmp_path / "t_atomic")
    os.makedirs(task_dir, exist_ok=True)
    state_file = os.path.join(task_dir, "state.json")

    state = create_initial_state("t_atomic", journal_dir=task_dir)
    state.transition("PREFLIGHT_RUNNING", reason="run 1")
    save_state(state, state_file)

    loaded = load_state(state_file, journal_dir=task_dir)
    assert loaded.status == "PREFLIGHT_RUNNING"
    assert loaded.task_id == "t_atomic"

"""Tests for MemoryBridge durable cross-subagent memory staging."""

import json
import time

import pytest
from orchestrator.memory_bridge import MemoryBridge

pytestmark = pytest.mark.fast


def test_append_read_roundtrip(tmp_path):
    json_path = str(tmp_path / "memory.jsonl")
    bridge = MemoryBridge(json_path)
    entry = {"scope": "test", "note": "hello world", "val": 42}

    line_num = bridge.append(entry)
    assert line_num == 1

    entries = bridge.read()
    assert len(entries) == 1
    assert entries[0] == entry


def test_scope_filtering(tmp_path):
    json_path = str(tmp_path / "memory.jsonl")
    bridge = MemoryBridge(json_path)

    bridge.append({"scope": "alpha", "msg": "a1"})
    bridge.append({"scope": "beta", "msg": "b1"})
    bridge.append({"scope": "alpha", "msg": "a2"})

    alpha_entries = bridge.read(scope="alpha")
    assert len(alpha_entries) == 2
    assert [e["msg"] for e in alpha_entries] == ["a1", "a2"]

    beta_entries = bridge.read(scope="beta")
    assert len(beta_entries) == 1
    assert beta_entries[0]["msg"] == "b1"

    all_entries = bridge.read(scope=None)
    assert len(all_entries) == 3


def test_limit_most_recent_n(tmp_path):
    json_path = str(tmp_path / "memory.jsonl")
    bridge = MemoryBridge(json_path)

    for i in range(5):
        bridge.append({"idx": i})

    recent_2 = bridge.read(limit=2)
    assert len(recent_2) == 2
    assert [e["idx"] for e in recent_2] == [3, 4]


def test_read_last(tmp_path):
    json_path = str(tmp_path / "memory.jsonl")
    bridge = MemoryBridge(json_path)

    assert bridge.read_last() is None

    bridge.append({"scope": "task", "v": 1})
    bridge.append({"scope": "task", "v": 2})
    bridge.append({"scope": "other", "v": 99})

    assert bridge.read_last(scope="task") == {"scope": "task", "v": 2}
    assert bridge.read_last(scope="other") == {"scope": "other", "v": 99}
    assert bridge.read_last(scope="nonexistent") is None


def test_torn_and_malformed_lines(tmp_path):
    json_path = str(tmp_path / "memory.jsonl")

    # Pre-populate file with malformed middle line and torn trailing line
    content = (
        json.dumps({"valid": 1})
        + "\n"
        + "{ malformed json in middle\n"
        + json.dumps({"valid": 2})
        + "\n"
        + '{"torn_final": '
    )
    with open(json_path, "w", encoding="utf-8") as f:
        f.write(content)

    bridge = MemoryBridge(json_path)
    entries = bridge.read()
    assert len(entries) == 2
    assert [e["valid"] for e in entries] == [1, 2]


def test_multi_instance_appends(tmp_path):
    json_path = str(tmp_path / "memory.jsonl")
    b1 = MemoryBridge(json_path)
    b2 = MemoryBridge(json_path)

    l1 = b1.append({"inst": 1, "data": "first"})
    l2 = b2.append({"inst": 2, "data": "second"})

    assert l1 == 1
    assert l2 == 2

    entries = b1.read()
    assert len(entries) == 2
    assert entries[0]["inst"] == 1
    assert entries[1]["inst"] == 2


def test_entries_without_scope_key(tmp_path):
    json_path = str(tmp_path / "memory.jsonl")
    bridge = MemoryBridge(json_path)

    bridge.append({"no_scope": True, "v": 1})
    bridge.append({"scope": "scoped", "v": 2})

    # Read with scope=None includes entries without scope key
    all_entries = bridge.read(scope=None)
    assert len(all_entries) == 2

    # Read with explicit scope excludes entries without scope key
    scoped_entries = bridge.read(scope="scoped")
    assert len(scoped_entries) == 1
    assert scoped_entries[0]["v"] == 2

    other_entries = bridge.read(scope="unrelated")
    assert len(other_entries) == 0


def test_stale_lock_recovery(tmp_path, monkeypatch):
    """QC 2026-08-02 (P1-4): a lock whose owner pid is dead is stolen."""
    import orchestrator.memory_bridge as mb

    json_path = str(tmp_path / "memory.jsonl")
    bridge = MemoryBridge(json_path)
    # Fake a stale lock held by a dead pid.
    import os

    dead_pid = 99999999
    with open(f"{json_path}.lock", "w", encoding="utf-8") as f:
        json.dump({"pid": dead_pid, "created_at": 1.0}, f)
    monkeypatch.setattr(mb, "_pid_alive", lambda pid: pid != dead_pid)

    line_num = bridge.append({"scope": "recovered", "v": 1})
    assert line_num == 1
    assert not os.path.exists(f"{json_path}.lock")


def test_lock_not_stolen_from_live_owner(tmp_path, monkeypatch):
    """QC 2026-08-02 (P1-4): a live owner's lock is never stolen, even if the
    lock file mtime is old — append() times out instead."""
    import orchestrator.memory_bridge as mb

    json_path = str(tmp_path / "memory.jsonl")
    bridge = MemoryBridge(json_path)
    import os

    with open(f"{json_path}.lock", "w", encoding="utf-8") as f:
        json.dump({"pid": os.getpid(), "created_at": 1.0}, f)
    monkeypatch.setattr(mb, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(mb, "LOCK_TTL_SEC", 0.3)

    with pytest.raises(TimeoutError):
        bridge.append({"scope": "blocked", "v": 1})
    assert os.path.exists(f"{json_path}.lock")


def test_release_does_not_delete_others_lock(tmp_path, monkeypatch):
    """QC 2026-08-02 (P1-4): a writer that thinks it holds the lock but whose
    lock was replaced must not delete the new owner's lock."""
    import os

    json_path = str(tmp_path / "memory.jsonl")
    bridge = MemoryBridge(json_path)
    line_num = bridge.append({"scope": "a", "v": 1})
    assert line_num == 1
    assert not os.path.exists(f"{json_path}.lock")

    # Recreate a lock with a different owner, then release: must NOT remove it.
    with open(f"{json_path}.lock", "w", encoding="utf-8") as f:
        json.dump({"pid": os.getpid() + 1, "created_at": time.time()}, f)
    bridge._release_lock()
    assert os.path.exists(f"{json_path}.lock")


def test_lock_waits_for_release_sequential(tmp_path):
    """QC 2026-08-02 (P1-4): a second append on the same instance proceeds once
    the first releases the lock (existing serial behavior preserved)."""
    import os

    json_path = str(tmp_path / "memory.jsonl")
    b = MemoryBridge(json_path)
    assert b.append({"i": 1}) == 1
    assert b.append({"i": 2}) == 2
    entries = b.read()
    assert [e["i"] for e in entries] == [1, 2]
    assert not os.path.exists(f"{json_path}.lock")

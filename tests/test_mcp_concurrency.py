"""Concurrency and thread-safety tests for locks, registry, and memory bridge."""

import concurrent.futures

import pytest

from orchestrator.memory_bridge import MemoryBridge
from orchestrator.scope import FileBackedScopeRegistry


@pytest.mark.fast
def test_memory_bridge_concurrent_writes(tmp_path):
    """Test that concurrent thread writes to MemoryBridge never lose records or corrupt JSONL."""
    bridge_file = str(tmp_path / "concurrent_bridge.jsonl")
    bridge = MemoryBridge(bridge_file)

    num_threads = 10
    writes_per_thread = 10

    def _worker(thread_idx: int):
        for i in range(writes_per_thread):
            bridge.append({"thread_idx": thread_idx, "write_idx": i, "payload": f"data_{thread_idx}_{i}"})

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(_worker, t) for t in range(num_threads)]
        for f in concurrent.futures.as_completed(futures):
            f.result()  # raise if any crashed

    # Read back all entries
    entries = bridge.read()
    assert len(entries) == num_threads * writes_per_thread


@pytest.mark.fast
def test_scope_registry_thread_safety(tmp_path):
    """Test that FileBackedScopeRegistry handles concurrent register/unregister across threads."""
    reg = FileBackedScopeRegistry(str(tmp_path))

    num_threads = 6
    ops_per_thread = 5

    def _worker(thread_idx: int):
        for i in range(ops_per_thread):
            tid = f"task_{thread_idx}_{i}"
            reg.register(tid, [f"out_{thread_idx}_{i}.txt"])
            outs = reg.sibling_declared_outputs(tid)
            assert isinstance(outs, list)
            reg.unregister(tid)

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(_worker, t) for t in range(num_threads)]
        for f in concurrent.futures.as_completed(futures):
            f.result()

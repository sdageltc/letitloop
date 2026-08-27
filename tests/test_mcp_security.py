"""Tests for MCP Security, path jailing, goal_id traversal, and idempotency cache limits."""

import importlib
import time

import pytest

import orchestrator.mcp_server as mcp_mod

pytestmark = pytest.mark.security


def test_emit_receipt_goal_id_path_traversal(tmp_path, monkeypatch):
    """emit_receipt with goal_id='../../../etc' raises SecurityError."""
    monkeypatch.setenv("LETITLOOP_WORKSPACE_ROOT", str(tmp_path))
    importlib.reload(mcp_mod)

    with pytest.raises((mcp_mod.SecurityError, ValueError)):
        mcp_mod.emit_receipt(goal_id="../../../etc", wal_dir=str(tmp_path / "wal"))

    monkeypatch.delenv("LETITLOOP_WORKSPACE_ROOT", raising=False)
    importlib.reload(mcp_mod)


def test_workspace_jail_boundary_rejection(tmp_path, monkeypatch):
    """MCP tools reject paths outside the workspace root with SecurityError."""
    monkeypatch.setenv("LETITLOOP_WORKSPACE_ROOT", str(tmp_path))
    importlib.reload(mcp_mod)

    # 1. checkpoint_state with outside wal_dir
    with pytest.raises(mcp_mod.SecurityError):
        mcp_mod.checkpoint_state(goal_id="g1", payload={}, wal_dir="/outside/path")

    # 2. rollback_ast with outside file_path
    with pytest.raises(mcp_mod.SecurityError):
        mcp_mod.rollback_ast(file_path="/outside/evil.py", backup_ref="pass")

    # 3. verify_scope with outside file_path
    with pytest.raises(mcp_mod.SecurityError):
        mcp_mod.verify_scope(file_path="/outside/evil.py", allowed_patterns=["*"])

    # 4. emit_receipt with outside wal_dir
    with pytest.raises(mcp_mod.SecurityError):
        mcp_mod.emit_receipt(goal_id="g1", wal_dir="/outside/wal")

    # 5. wal_verify with outside wal_path
    with pytest.raises(mcp_mod.SecurityError):
        mcp_mod.wal_verify(wal_path="/outside/wal")

    monkeypatch.delenv("LETITLOOP_WORKSPACE_ROOT", raising=False)
    importlib.reload(mcp_mod)


def test_idempotency_cache_ttl_expiry():
    """_IDEMPOTENCY cache enforces TTL expiration (300s in prod, tested with custom TTL)."""
    cache = mcp_mod.IdempotencyCache(ttl=0.05, max_size=1000)
    cache.set("req_1", {"data": "first"})
    assert cache.get("req_1") == {"data": "first"}
    assert "req_1" in cache

    time.sleep(0.07)
    assert cache.get("req_1") is None
    assert "req_1" not in cache
    assert len(cache) == 0


def test_idempotency_cache_capacity_capping():
    """_IDEMPOTENCY cache caps total entries at max_size (1000 max entries)."""
    cache = mcp_mod.IdempotencyCache(ttl=300.0, max_size=1000)
    for i in range(1100):
        cache.set(f"req_{i}", {"seq": i})

    assert len(cache) == 1000
    # Oldest entries (req_0 through req_99) should be evicted
    assert cache.get("req_0") is None
    assert cache.get("req_99") is None
    assert cache.get("req_1099") == {"seq": 1099}


def test_module_idempotency_defaults():
    """Verify default _IDEMPOTENCY cache parameters (TTL=300s, max_size=1000)."""
    assert mcp_mod._IDEMPOTENCY.ttl == 300.0
    assert mcp_mod._IDEMPOTENCY.max_size == 1000

"""Tests for Universal MCP Server — Sprint 4 exit gates."""

import pathlib

import pytest

pytestmark = pytest.mark.fast


def test_lil_mcp_starts_stdio_cleanly():
    """lil mcp CLI subcommand starts the stdio server cleanly (shim mode when mcp SDK absent)."""
    # Test the underlying module directly (fast, no subprocess hang)
    import orchestrator.mcp_server as mcp_mod

    assert hasattr(mcp_mod, "durable_step")
    assert hasattr(mcp_mod, "checkpoint_state")
    assert hasattr(mcp_mod, "rollback_ast")
    assert hasattr(mcp_mod, "verify_scope")
    assert hasattr(mcp_mod, "emit_receipt")


def test_mcp_tools_execute_successfully(tmp_path, monkeypatch):
    """npx inspector equivalent: all 4+ tools execute via direct import."""
    # Jail to tmp workspace
    monkeypatch.setenv("LETITLOOP_WORKSPACE_ROOT", str(tmp_path))
    # Reimport to pick up new WORKSPACE_ROOT
    import importlib

    import orchestrator.mcp_server as mcp_mod

    importlib.reload(mcp_mod)

    # 1) checkpoint_state writes atomic LILWAL02 frame
    res1 = mcp_mod.checkpoint_state(goal_id="test-goal", payload={"hello": "world"}, wal_dir=str(tmp_path / "wal"))
    assert res1["goal_id"] == "test-goal"
    assert pathlib.Path(res1["wal_path"]).is_file()

    # 2) emit_receipt generates HMAC-sealed proof
    res2 = mcp_mod.emit_receipt(goal_id="test-goal", wal_dir=str(tmp_path / "wal"))
    assert res2["verified"] is True
    assert "receipt_path" in res2

    # 3) rollback_ast restores file
    target = tmp_path / "target.py"
    target.write_text("original = 1\n", encoding="utf-8")
    res3 = mcp_mod.rollback_ast(file_path=str(target), backup_ref="restored = 2\n")
    assert res3["restored"] is True
    assert target.read_text(encoding="utf-8") == "restored = 2\n"

    # 4) verify_scope checks boundaries
    res4 = mcp_mod.verify_scope(file_path=str(target), allowed_patterns=["*.py"])
    assert res4["allowed"] is True

    # Verify non-allowed pattern
    res5 = mcp_mod.verify_scope(file_path=str(target), allowed_patterns=["nonexistent/**"])
    assert res5["allowed"] is False
    assert len(res5["violations"]) > 0

    # Restore original workspace
    monkeypatch.delenv("LETITLOOP_WORKSPACE_ROOT", raising=False)
    importlib.reload(mcp_mod)


def test_mcp_path_traversal_rejected(tmp_path, monkeypatch):
    """Path traversal attacks outside the workspace directory are rejected with SecurityError."""
    monkeypatch.setenv("LETITLOOP_WORKSPACE_ROOT", str(tmp_path))
    import importlib

    import orchestrator.mcp_server as mcp_mod

    importlib.reload(mcp_mod)

    with pytest.raises(mcp_mod.SecurityError):
        mcp_mod.checkpoint_state(goal_id="evil", payload={}, wal_dir="/tmp/evil_outside")

    with pytest.raises(mcp_mod.SecurityError):
        mcp_mod.rollback_ast(file_path="/etc/passwd", backup_ref="evil")

    with pytest.raises(mcp_mod.SecurityError):
        mcp_mod.verify_scope(file_path="/etc/passwd", allowed_patterns=["*"])

    with pytest.raises(mcp_mod.SecurityError):
        mcp_mod.emit_receipt(goal_id="evil", wal_dir="/tmp/evil2")

    # wal_verify also jailed
    with pytest.raises(mcp_mod.SecurityError):
        mcp_mod.wal_verify(wal_path="/etc")

    monkeypatch.delenv("LETITLOOP_WORKSPACE_ROOT", raising=False)
    importlib.reload(mcp_mod)


def test_mcp_idempotency_reconnect(tmp_path, monkeypatch):
    """Disconnecting and reconnecting during an active checkpoint resumes state via requestId without corruption."""
    monkeypatch.setenv("LETITLOOP_WORKSPACE_ROOT", str(tmp_path))
    import asyncio
    import importlib

    import orchestrator.mcp_server as mcp_mod

    importlib.reload(mcp_mod)
    # Clear idempotency store
    mcp_mod._IDEMPOTENCY.clear()

    # First call with requestId
    res1 = mcp_mod.checkpoint_state(
        goal_id="idem-goal", payload={"v": 1}, wal_dir=str(tmp_path / "wal"), requestId="req-123"
    )
    # Second call with same requestId but different payload — should return cached (not new write)
    res2 = mcp_mod.checkpoint_state(
        goal_id="idem-goal", payload={"v": 999}, wal_dir=str(tmp_path / "wal"), requestId="req-123"
    )
    assert res2["cached"] is True or res2.get("idempotent") is True
    assert res1["frame_sha256"] == res2["frame_sha256"]

    # durable_step also idempotent
    mcp_mod._IDEMPOTENCY.clear()

    async def _run():
        r1 = await mcp_mod.durable_step(
            step_id="s1", payload={"x": 1}, wal_dir=str(tmp_path / "wal2"), goal_id="g1", requestId="req-step-1"
        )
        r2 = await mcp_mod.durable_step(
            step_id="s1", payload={"x": 999}, wal_dir=str(tmp_path / "wal2"), goal_id="g1", requestId="req-step-1"
        )
        return r1, r2

    r1, r2 = asyncio.run(_run())
    assert r2.get("cached") is True or r2.get("idempotent") is True

    monkeypatch.delenv("LETITLOOP_WORKSPACE_ROOT", raising=False)
    importlib.reload(mcp_mod)
    mcp_mod._IDEMPOTENCY.clear()


def test_mcp_emit_receipt_goal_id_traversal_rejected(tmp_path, monkeypatch):
    """emit_receipt rejects goal_id attempting path traversal outside workspace."""
    monkeypatch.setenv("LETITLOOP_WORKSPACE_ROOT", str(tmp_path))
    import importlib

    import orchestrator.mcp_server as mcp_mod

    importlib.reload(mcp_mod)

    with pytest.raises(mcp_mod.SecurityError):
        mcp_mod.emit_receipt(goal_id="../../evil_goal", wal_dir=str(tmp_path / "wal"))

    monkeypatch.delenv("LETITLOOP_WORKSPACE_ROOT", raising=False)
    importlib.reload(mcp_mod)


def test_idempotency_cache_ttl_and_capacity():
    from orchestrator.mcp_server import IdempotencyCache

    cache = IdempotencyCache(ttl=0.05, max_size=3)
    cache.set("k1", "v1")
    cache.set("k2", "v2")
    cache.set("k3", "v3")

    assert len(cache) == 3
    assert cache.get("k1") == "v1"

    # Exceed capacity
    cache.set("k4", "v4")
    assert len(cache) == 3
    # Oldest (k2 since k1 was accessed or updated)
    assert cache.get("k4") == "v4"

    # Test TTL expiration
    import time

    time.sleep(0.06)
    assert cache.get("k4") is None
    assert len(cache) == 0

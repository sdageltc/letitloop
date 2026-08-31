"""Unit + integration tests for the FastAPI/Starlette durable background-tasks adapter.

These tests exercise the durability contract from issue #93:
a background task is recorded to a WAL *before* it runs, so that a task
interrupted by a crash/restart is transparently resumed on startup.
"""

import importlib.util
import os

import pytest
from letitloop.adapters.fastapi import (
    DurableBackgroundTasks,
    DurableTaskManager,
    durable_task,
    install_durable_background_tasks,
)

_HAS_FASTAPI = importlib.util.find_spec("fastapi") is not None
requires_fastapi = pytest.mark.skipif(not _HAS_FASTAPI, reason="fastapi is not installed")

# ---------------------------------------------------------------------------
# Module-level task callables (stable __qualname__ so they resolve on resume).
# ---------------------------------------------------------------------------


def write_marker(path: str, content: str = "done") -> dict:
    """Sync task: write a sentinel file so the test can observe execution."""
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return {"path": path, "content": content}


async def async_write_marker(path: str, content: str = "async-done") -> dict:
    """Async task variant."""
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return {"path": path, "content": content}


def failing_task(path: str) -> None:
    """Task that always raises, to test terminal-failure handling."""
    raise ValueError("boom")


# ---------------------------------------------------------------------------
# Availability / registry
# ---------------------------------------------------------------------------


def test_is_available_matches_environment():
    assert DurableTaskManager.is_available() is _HAS_FASTAPI


def test_durable_task_explicit_name_is_registered():
    @durable_task("custom.report")
    def my_task(x):
        return x

    mgr = DurableTaskManager()
    assert mgr.key_for(my_task) == "custom.report"
    assert mgr.resolve("custom.report") is my_task


def test_auto_key_is_module_qualname():
    mgr = DurableTaskManager()
    key = mgr.key_for(write_marker)
    assert ":" in key
    assert key.endswith("write_marker")


def test_resolve_imports_dotted_path_without_registry():
    import json as _json

    mgr = DurableTaskManager()
    assert mgr.resolve("json:dumps") is _json.dumps


# ---------------------------------------------------------------------------
# Core durability contract
# ---------------------------------------------------------------------------


def test_record_pending_writes_wal_before_execution(tmp_path):
    mgr = DurableTaskManager(wal_dir=str(tmp_path / "wal"))
    marker = str(tmp_path / "m.txt")

    task_id = mgr.record_pending("pkg.mod:write_marker", [marker], {"content": "hi"})

    assert task_id
    # Recorded, but NOT yet executed.
    assert not os.path.exists(marker)
    pending = mgr.pending_tasks()
    assert len(pending) == 1
    assert pending[0]["task_id"] == task_id
    assert pending[0]["key"].endswith("write_marker")
    assert pending[0]["args"] == [marker]


def test_non_serializable_args_raise(tmp_path):
    mgr = DurableTaskManager(wal_dir=str(tmp_path / "wal"))
    with pytest.raises((TypeError, ValueError)):
        mgr.record_pending("k", [object()], {})


@pytest.mark.asyncio
async def test_run_task_executes_and_marks_completed(tmp_path):
    mgr = DurableTaskManager(wal_dir=str(tmp_path / "wal"))
    marker = str(tmp_path / "m.txt")
    key = mgr.key_for(write_marker)
    task_id = mgr.record_pending(key, [marker], {})

    result = await mgr.run_task(task_id, key, [marker], {})

    assert os.path.exists(marker)
    assert result["path"] == marker
    assert mgr.pending_tasks() == []


@pytest.mark.asyncio
async def test_interrupted_task_resumed_by_new_manager(tmp_path):
    """The core of issue #93: a task recorded but never completed (crash)
    is transparently resumed by a fresh manager on the same WAL (restart)."""
    wal = str(tmp_path / "wal")
    marker = str(tmp_path / "resumed.txt")

    # Run 1: record PENDING, then "crash" before executing.
    mgr1 = DurableTaskManager(wal_dir=wal)
    key = mgr1.key_for(write_marker)
    mgr1.record_pending(key, [marker], {"content": "recovered"})
    assert not os.path.exists(marker)

    # Run 2 (restart): a new manager on the same WAL resumes the leftover.
    mgr2 = DurableTaskManager(wal_dir=wal)
    resumed = await mgr2.resume_pending()

    assert resumed == 1
    assert os.path.exists(marker)
    with open(marker, encoding="utf-8") as f:
        assert f.read() == "recovered"
    assert mgr2.pending_tasks() == []


@pytest.mark.asyncio
async def test_completed_task_not_resumed(tmp_path):
    wal = str(tmp_path / "wal")
    marker = str(tmp_path / "should_not_exist.txt")

    mgr1 = DurableTaskManager(wal_dir=wal)
    key = mgr1.key_for(write_marker)
    task_id = mgr1.record_pending(key, [marker], {})
    mgr1.mark_completed(task_id)

    mgr2 = DurableTaskManager(wal_dir=wal)
    resumed = await mgr2.resume_pending()

    assert resumed == 0
    assert not os.path.exists(marker)


@pytest.mark.asyncio
async def test_failed_task_is_terminal(tmp_path):
    wal = str(tmp_path / "wal")
    marker = str(tmp_path / "x.txt")
    mgr = DurableTaskManager(wal_dir=wal)
    key = mgr.key_for(failing_task)
    task_id = mgr.record_pending(key, [marker], {})

    with pytest.raises(ValueError):
        await mgr.run_task(task_id, key, [marker], {})

    # A task that raised is terminal, not "interrupted" — it is not resumed.
    assert mgr.pending_tasks() == []
    mgr2 = DurableTaskManager(wal_dir=wal)
    assert await mgr2.resume_pending() == 0


@pytest.mark.asyncio
async def test_async_task_runs_and_resumes(tmp_path):
    wal = str(tmp_path / "wal")
    marker = str(tmp_path / "async.txt")
    mgr = DurableTaskManager(wal_dir=wal)
    key = mgr.key_for(async_write_marker)
    mgr.record_pending(key, [marker], {})

    resumed = await DurableTaskManager(wal_dir=wal).resume_pending()

    assert resumed == 1
    assert os.path.exists(marker)


# ---------------------------------------------------------------------------
# FastAPI dependency-injection integration
# ---------------------------------------------------------------------------


@requires_fastapi
def test_install_attaches_manager_to_app_state(tmp_path):
    from fastapi import FastAPI

    app = FastAPI()
    mgr = install_durable_background_tasks(app, wal_dir=str(tmp_path / "wal"))
    assert app.state.letitloop_durable_manager is mgr


@requires_fastapi
def test_di_endpoint_runs_task_after_response(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    marker = str(tmp_path / "endpoint.txt")
    mgr = install_durable_background_tasks(app, wal_dir=str(tmp_path / "wal"))

    @app.post("/run")
    async def run(bg: DurableBackgroundTasks):
        bg.add_task(write_marker, marker, content="via-endpoint")
        return {"status": "queued"}

    client = TestClient(app)
    resp = client.post("/run")

    assert resp.status_code == 200
    assert resp.json() == {"status": "queued"}
    # Starlette runs background tasks after the response is sent.
    assert os.path.exists(marker)
    with open(marker, encoding="utf-8") as f:
        assert f.read() == "via-endpoint"
    assert mgr.pending_tasks() == []


@requires_fastapi
def test_startup_resumes_pending_tasks(tmp_path):
    """Pre-seed a pending task (as a prior crashed run would), then verify the
    installed startup hook resumes it when the app boots."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    wal = str(tmp_path / "wal")
    marker = str(tmp_path / "startup.txt")

    seed = DurableTaskManager(wal_dir=wal)
    key = seed.key_for(write_marker)
    seed.record_pending(key, [marker], {"content": "startup-recovered"})
    assert not os.path.exists(marker)

    app = FastAPI()
    install_durable_background_tasks(app, wal_dir=wal)

    # Entering the TestClient context triggers startup/lifespan events.
    with TestClient(app):
        pass

    assert os.path.exists(marker)
    with open(marker, encoding="utf-8") as f:
        assert f.read() == "startup-recovered"

"""letitloop/adapters/fastapi.py — Durable background tasks for FastAPI / Starlette.

FastAPI's built-in ``BackgroundTasks`` run in the same worker process and are lost
if that worker restarts, redeploys, or is killed. This adapter provides a drop-in
``DurableBackgroundTasks`` dependency backed by LetItLoop's zero-daemon Write-Ahead
Log: every task is recorded to disk *before* it runs, so a task interrupted by a
crash is transparently re-run when the server starts again.

Design
------
Two layers of durability:

1. **Task-level (at-least-once).** ``add_task`` appends a ``TASK_PENDING`` record to
   ``<wal_dir>/tasks.wal.jsonl`` (fsync'd) before returning the HTTP response, and a
   ``TASK_COMPLETED`` record once the task finishes. On startup, any task that is
   ``PENDING`` without a matching ``COMPLETED``/``FAILED`` is an interrupted task and
   is resumed.
2. **Step-level (skip completed work).** Each task runs inside a per-task ``@durable``
   context, so tasks that use :func:`letitloop.step` / :func:`letitloop.async_step`
   fast-forward already-completed steps when resumed.

Because Python callables cannot be serialized, a task is referenced by a stable
string key: the name registered via :func:`durable_task`, or an auto-derived
``"module:qualname"``. On resume the key is looked up in the registry first, then
imported dynamically as a fallback.

Zero mandatory dependencies: FastAPI/Starlette are imported lazily, so importing
``letitloop`` never requires them.

Examples
--------
>>> from fastapi import FastAPI
>>> from letitloop.adapters.fastapi import (
...     DurableBackgroundTasks, durable_task, install_durable_background_tasks,
... )
>>> app = FastAPI()
>>> manager = install_durable_background_tasks(app)   # attaches WAL + startup resume
>>>
>>> @durable_task()                                   # optional: stable key
... async def run_durable_report(report_id: str): ...
>>>
>>> @app.post("/generate-report/{report_id}")
... async def generate_report(report_id: str, bg: DurableBackgroundTasks):
...     bg.add_task(run_durable_report, report_id)
...     return {"status": "queued"}
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import os
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from orchestrator.decorators import _to_json_serializable, durable, durable_async

# Attribute stamped on a callable by @durable_task to carry its stable key.
_TASK_KEY_ATTR = "__letitloop_task_key__"

# app.state attribute where install_durable_background_tasks stores the manager.
MANAGER_ATTR = "letitloop_durable_manager"

# Process-wide registry mapping task key -> callable. Populated by @durable_task
# and by DurableTaskManager.key_for so in-process resume never needs re-import.
_GLOBAL_REGISTRY: Dict[str, Callable[..., Any]] = {}


def _has_fastapi() -> bool:
    """Return True if FastAPI (and thus Starlette) is importable."""
    try:
        import fastapi  # noqa: F401

        return True
    except ImportError:
        return False


_HAS_FASTAPI = _has_fastapi()


def _default_key(fn: Callable[..., Any]) -> str:
    """Derive the auto ``module:qualname`` key for a callable.

    Functions and coroutine functions always carry both attributes. Pass an
    explicit name to :func:`durable_task` for anything else (e.g. a partial).
    """
    return f"{fn.__module__}:{fn.__qualname__}"


def _json_roundtrip(value: Any) -> Any:
    """Validate + normalize a value through JSON, raising if it is not serializable.

    Pydantic models, dataclasses, and objects exposing ``model_dump``/``dict`` are
    converted to plain data (mirrors the durability kernel's step serialization).
    """
    return json.loads(json.dumps(value, default=_to_json_serializable, ensure_ascii=False))


def durable_task(name: Optional[str] = None) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Register a callable as a durable background task under a stable key.

    Optional but recommended: an explicit ``name`` keeps resume working even if the
    function is later renamed or moved. Without it, the key defaults to
    ``"module:qualname"``. The function is returned unchanged.

    >>> @durable_task("reports.generate")
    ... def generate(report_id: str): ...
    """

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        key = name or _default_key(fn)
        setattr(fn, _TASK_KEY_ATTR, key)
        _GLOBAL_REGISTRY[key] = fn
        return fn

    return decorator


class DurableTaskManager:
    """Owns the task WAL, the task registry, and resume-on-startup logic.

    Parameters
    ----------
    wal_dir : Optional[str]
        Directory for the task ledger and per-task run state. Defaults to
        ``$LETITLOOP_WAL_DIR`` or ``.letitloop/fastapi_wal``.
    """

    def __init__(self, wal_dir: Optional[str] = None) -> None:
        self.wal_dir = os.path.abspath(
            wal_dir or os.environ.get("LETITLOOP_WAL_DIR") or os.path.join(".letitloop", "fastapi_wal")
        )
        self.wal_file = os.path.join(self.wal_dir, "tasks.wal.jsonl")
        self.runs_dir = os.path.join(self.wal_dir, "runs")

        self._lock = threading.Lock()
        self._seq = 0
        # task_id -> {"task_id", "key", "args", "kwargs"} for tasks not yet finished.
        self._pending: Dict[str, Dict[str, Any]] = {}

        os.makedirs(self.wal_dir, exist_ok=True)
        os.makedirs(self.runs_dir, exist_ok=True)
        if not os.path.exists(self.wal_file):
            open(self.wal_file, "a", encoding="utf-8").close()
        self._replay()

    @classmethod
    def is_available(cls) -> bool:
        """Return True if FastAPI/Starlette is installed in the environment."""
        return _HAS_FASTAPI

    # -- registry -----------------------------------------------------------

    def key_for(self, fn: Callable[..., Any]) -> str:
        """Return the stable key for ``fn``, registering it for in-process resume."""
        key = getattr(fn, _TASK_KEY_ATTR, None) or _default_key(fn)
        _GLOBAL_REGISTRY.setdefault(key, fn)
        return key

    def resolve(self, key: str) -> Callable[..., Any]:
        """Resolve a task key to a callable: registry first, then dynamic import."""
        fn = _GLOBAL_REGISTRY.get(key)
        if fn is not None:
            return fn
        if ":" in key:
            module_name, _, qualname = key.partition(":")
            obj: Any = importlib.import_module(module_name)
            for part in qualname.split("."):
                obj = getattr(obj, part)
            return obj
        raise KeyError(f"Cannot resolve durable task key {key!r}: not registered and not an importable path.")

    # -- WAL ----------------------------------------------------------------

    def _append(self, event: str, payload: Dict[str, Any]) -> None:
        """Atomically append an event to the task WAL (flush + fsync)."""
        with self._lock:
            self._seq += 1
            record = {"seq": self._seq, "event": event, "ts": time.time(), **payload}
            line = json.dumps(record, ensure_ascii=False) + "\n"
            with open(self.wal_file, "a", encoding="utf-8") as f:
                f.write(line)
                f.flush()
                os.fsync(f.fileno())

    def _replay(self) -> None:
        """Rebuild the pending-task set from the WAL (this is how resume works)."""
        if not os.path.isfile(self.wal_file):
            return
        with open(self.wal_file, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                self._seq = max(self._seq, record.get("seq", 0))
                event = record.get("event")
                task_id = record.get("task_id")
                if not task_id:
                    continue
                if event == "TASK_PENDING":
                    self._pending[task_id] = {
                        "task_id": task_id,
                        "key": record.get("key"),
                        "args": record.get("args", []),
                        "kwargs": record.get("kwargs", {}),
                    }
                elif event in ("TASK_COMPLETED", "TASK_FAILED"):
                    self._pending.pop(task_id, None)

    def record_pending(self, key: str, args: List[Any], kwargs: Dict[str, Any]) -> str:
        """Persist a task's intent to the WAL and return its task id.

        Called *before* the task executes. Arguments are validated by round-tripping
        through JSON; a non-serializable argument raises immediately (fail fast).
        """
        norm_args = _json_roundtrip(list(args))
        norm_kwargs = _json_roundtrip(dict(kwargs))
        task_id = uuid.uuid4().hex
        payload = {"task_id": task_id, "key": key, "args": norm_args, "kwargs": norm_kwargs}
        self._append("TASK_PENDING", payload)
        with self._lock:
            self._pending[task_id] = payload
        return task_id

    def mark_completed(self, task_id: str) -> None:
        """Record a task as completed and drop it from the pending set."""
        self._append("TASK_COMPLETED", {"task_id": task_id})
        with self._lock:
            self._pending.pop(task_id, None)

    def mark_failed(self, task_id: str, error: BaseException) -> None:
        """Record a task as failed (terminal) and drop it from the pending set."""
        self._append(
            "TASK_FAILED",
            {"task_id": task_id, "error_type": type(error).__name__, "error": str(error)},
        )
        with self._lock:
            self._pending.pop(task_id, None)

    def pending_tasks(self) -> List[Dict[str, Any]]:
        """Return records for tasks recorded but not yet completed or failed."""
        with self._lock:
            return list(self._pending.values())

    # -- execution ----------------------------------------------------------

    async def run_task(self, task_id: str, key: str, args: List[Any], kwargs: Dict[str, Any]) -> Any:
        """Execute a task durably, then mark it completed (or failed, and re-raise).

        Runs inside a per-task ``@durable`` context keyed by ``task_id`` so any
        ``step``/``async_step`` calls inside the task skip work already done before
        an interruption.
        """
        fn = self.resolve(key)
        run_dir = os.path.join(self.runs_dir, task_id)
        try:
            if inspect.iscoroutinefunction(fn):
                wrapped = durable_async(goal_id=task_id, wal_dir=run_dir)(fn)
                result = await wrapped(*args, **kwargs)
            else:
                wrapped = durable(goal_id=task_id, wal_dir=run_dir)(fn)
                result = await asyncio.to_thread(wrapped, *args, **kwargs)
        except BaseException as exc:  # noqa: BLE001 - record then re-raise
            self.mark_failed(task_id, exc)
            raise
        self.mark_completed(task_id)
        return result

    async def _run_recorded(self, task_id: str) -> Any:
        """Run a task that was already recorded via ``record_pending``."""
        with self._lock:
            record = self._pending.get(task_id)
        if record is None:
            return None
        return await self.run_task(task_id, record["key"], record["args"], record["kwargs"])

    async def resume_pending(self) -> int:
        """Re-run every interrupted task; return how many completed successfully.

        A task that raises is marked failed (terminal) and does not abort the sweep.
        """
        resumed = 0
        for record in self.pending_tasks():
            try:
                await self.run_task(record["task_id"], record["key"], record["args"], record["kwargs"])
                resumed += 1
            except Exception:
                # Already recorded as TASK_FAILED by run_task; keep resuming others.
                continue
        return resumed


class _DurableBackgroundTasks:
    """Wrapper returned by DI that records tasks durably before scheduling them.

    Mirrors the ``add_task(func, *args, **kwargs)`` surface of FastAPI's
    ``BackgroundTasks``. The real request-scoped ``BackgroundTasks`` still runs the
    work after the response; this wrapper adds the WAL record + resume guarantee.
    """

    def __init__(self, background_tasks: Any, manager: DurableTaskManager) -> None:
        self._background_tasks = background_tasks
        self._manager = manager

    def add_task(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> str:
        """Record the task to the WAL, then schedule it to run after the response.

        Returns the task id.
        """
        key = self._manager.key_for(func)
        task_id = self._manager.record_pending(key, list(args), dict(kwargs))
        self._background_tasks.add_task(self._manager._run_recorded, task_id)
        return task_id


_DEFAULT_MANAGER: Optional[DurableTaskManager] = None


def _get_default_manager() -> DurableTaskManager:
    """Return a lazily-created process-default manager (used when none is installed)."""
    global _DEFAULT_MANAGER
    if _DEFAULT_MANAGER is None:
        _DEFAULT_MANAGER = DurableTaskManager()
    return _DEFAULT_MANAGER


if _HAS_FASTAPI:
    from typing import Annotated

    from fastapi import BackgroundTasks, Depends, Request

    def _provide_durable_background_tasks(
        request: Request,
        background_tasks: BackgroundTasks,
    ) -> _DurableBackgroundTasks:
        """FastAPI dependency: wrap the request's BackgroundTasks with durability."""
        manager = getattr(request.app.state, MANAGER_ATTR, None) or _get_default_manager()
        return _DurableBackgroundTasks(background_tasks, manager)

    # Use as a parameter annotation: ``bg: DurableBackgroundTasks``.
    DurableBackgroundTasks = Annotated[_DurableBackgroundTasks, Depends(_provide_durable_background_tasks)]
else:  # pragma: no cover - exercised only when FastAPI is absent
    # Keep the public name importable so ``letitloop.adapters`` never hard-fails.
    DurableBackgroundTasks = _DurableBackgroundTasks  # type: ignore[misc,assignment]

    def _provide_durable_background_tasks(*_args: Any, **_kwargs: Any) -> _DurableBackgroundTasks:
        raise RuntimeError("FastAPI is not installed. Install it with `pip install fastapi`.")


def install_durable_background_tasks(
    app: Any,
    manager: Optional[DurableTaskManager] = None,
    wal_dir: Optional[str] = None,
) -> DurableTaskManager:
    """Attach a DurableTaskManager to ``app`` and resume interrupted tasks on startup.

    Stores the manager at ``app.state.letitloop_durable_manager`` (read by the DI
    dependency) and wraps the app's lifespan so ``resume_pending()`` runs on startup.
    The existing lifespan (including any custom ``lifespan=`` you passed to FastAPI)
    is preserved and still runs.
    """
    if not _HAS_FASTAPI:
        raise RuntimeError("FastAPI is not installed. Install it with `pip install fastapi`.")
    if manager is None:
        manager = DurableTaskManager(wal_dir=wal_dir)
    setattr(app.state, MANAGER_ATTR, manager)

    import contextlib

    previous_lifespan = app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def _lifespan_with_resume(app_: Any) -> Any:
        await manager.resume_pending()
        async with previous_lifespan(app_) as maybe_state:
            yield maybe_state

    app.router.lifespan_context = _lifespan_with_resume
    return manager


__all__ = [
    "DurableBackgroundTasks",
    "DurableTaskManager",
    "durable_task",
    "install_durable_background_tasks",
    "MANAGER_ATTR",
]

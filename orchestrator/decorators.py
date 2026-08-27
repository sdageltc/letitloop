"""orchestrator/decorators.py — Zero-server, single-file WAL durability decorator for Python."""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import dataclasses
import functools
import json
import os
import sys
import threading
import time
from typing import Any, Callable, Dict, Optional

from .lock import acquire_lock, release_lock
from .state import State, create_initial_state, load_state, save_state


class DurableSerializationError(TypeError):
    """Raised when a durable step returns an object that cannot be serialized to JSON/WAL."""


# Thread-local active context: concurrent @durable workflows in one process
# each get an isolated context (module globals would cross-contaminate them).
_CONTEXT_STORAGE = threading.local()

# Async task-local context: isolated across concurrent asyncio.gather() tasks.
# ContextVar is propagated to child tasks on creation, giving each workflow
# invocation its own DurableAsyncContext while child async_steps share it.
_ASYNC_CONTEXT_VAR: contextvars.ContextVar[Optional["DurableAsyncContext"]] = contextvars.ContextVar(
    "_async_durable_context", default=None
)


def _get_active_context() -> Optional["DurableContext"]:
    return getattr(_CONTEXT_STORAGE, "active", None)


def _set_active_context(ctx: Optional["DurableContext"]) -> None:
    _CONTEXT_STORAGE.active = ctx


def _get_async_context() -> Optional["DurableAsyncContext"]:
    return _ASYNC_CONTEXT_VAR.get()


def _set_async_context(ctx: Optional["DurableAsyncContext"]) -> contextvars.Token:
    return _ASYNC_CONTEXT_VAR.set(ctx)


def _to_json_serializable(val: Any) -> Any:
    """Recursively convert Pydantic models, dataclasses, and custom objects to dicts."""
    if hasattr(val, "model_dump") and callable(val.model_dump):
        return val.model_dump()
    if hasattr(val, "dict") and callable(val.dict):
        return val.dict()
    if dataclasses.is_dataclass(val):
        return dataclasses.asdict(val)
    return val


def _serialize_step_output(step_id: str, result: Any) -> Any:
    """Validate and serialize step output. Fail fast with DurableSerializationError."""
    try:
        raw_json = json.dumps(result, default=_to_json_serializable, ensure_ascii=False)
        return json.loads(raw_json)
    except (TypeError, ValueError) as exc:
        raise DurableSerializationError(
            f"Step '{step_id}' returned non-serializable object: {exc}. "
            "Ensure return value is a dict, dataclass, Pydantic model, or JSON primitive."
        ) from exc


class DurableContext:
    """Thread-local context tracking active durable workflow state and step cache."""

    def __init__(self, goal_id: str, run_dir: str):
        self.goal_id = goal_id
        self.run_dir = os.path.abspath(run_dir)
        self.state_file = os.path.join(self.run_dir, "state.json")
        self.state: Optional[State] = None
        self.completed_steps: Dict[str, Any] = {}

    def initialize(self) -> None:
        os.makedirs(self.run_dir, exist_ok=True)
        acquire_lock(self.goal_id, self.run_dir, force=False)
        wal_file = os.path.join(self.run_dir, "state.wal.jsonl")
        if os.path.isfile(self.state_file) or os.path.isfile(wal_file):
            self.state = load_state(self.state_file, journal_dir=self.run_dir)
            for k, v in self.state.data.items():
                if k.startswith("step_output_"):
                    step_id = k[len("step_output_") :]
                    self.completed_steps[step_id] = v
                elif k == "step_outputs" and isinstance(v, dict):
                    self.completed_steps.update(v)
        else:
            self.state = create_initial_state(self.goal_id, journal_dir=self.run_dir)
            save_state(self.state, self.state_file)

    def close(self) -> None:
        if self.state:
            try:
                save_state(self.state, self.state_file)
            except Exception:
                pass
        release_lock(self.run_dir)


class DurableAsyncContext:
    """Async task-local context for @durable_async workflows.

    Mirrors DurableContext but uses asyncio.Lock for concurrent async_step
    isolation and is stored in a ContextVar so concurrent asyncio.gather()
    workflows each get isolated contexts while steps within one workflow share
    the same context object.
    """

    def __init__(self, goal_id: str, run_dir: str):
        self.goal_id = goal_id
        self.run_dir = os.path.abspath(run_dir)
        self.state_file = os.path.join(self.run_dir, "state.json")
        self.state: Optional[State] = None
        self.completed_steps: Dict[str, Any] = {}
        self._lock = asyncio.Lock()

    def initialize(self) -> None:
        os.makedirs(self.run_dir, exist_ok=True)
        acquire_lock(self.goal_id, self.run_dir, force=False)
        wal_file = os.path.join(self.run_dir, "state.wal.jsonl")
        if os.path.isfile(self.state_file) or os.path.isfile(wal_file):
            self.state = load_state(self.state_file, journal_dir=self.run_dir)
            for k, v in self.state.data.items():
                if k.startswith("step_output_"):
                    step_id = k[len("step_output_") :]
                    self.completed_steps[step_id] = v
                elif k == "step_outputs" and isinstance(v, dict):
                    self.completed_steps.update(v)
        else:
            self.state = create_initial_state(self.goal_id, journal_dir=self.run_dir)
            save_state(self.state, self.state_file)

    def close(self) -> None:
        if self.state:
            try:
                save_state(self.state, self.state_file)
            except Exception:
                pass
        release_lock(self.run_dir)


@contextlib.contextmanager
def atomic_marker(marker_id: str, run_dir: Optional[str] = None):
    """Context manager guarding non-idempotent external side effects via O_CREAT | O_EXCL.

    Yields True if this execution is the first to claim the marker (should execute mutation).
    Yields False if the marker already exists on disk (was already executed prior to crash).
    """
    ctx = _get_active_context()
    if ctx is None and run_dir is None:
        if os.environ.get("LETITLOOP_LENIENT") != "1":
            raise RuntimeError(
                f"atomic_marker('{marker_id}') called outside a @durable context without run_dir. "
                "Wrap your function with @durable or pass run_dir explicitly. Set LETITLOOP_LENIENT=1 to allow non-durable fallback."
            )
    active_dir = run_dir or (ctx.run_dir if ctx else ".durable_wal")
    markers_dir = os.path.join(active_dir, "markers")
    os.makedirs(markers_dir, exist_ok=True)
    marker_file = os.path.join(markers_dir, f"{marker_id}.marker")
    try:
        fd = os.open(marker_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps({"marker_id": marker_id, "pid": os.getpid(), "ts": time.time()}))
        yield True
    except FileExistsError:
        # Marker exists: side-effect was already executed prior to crash
        yield False


def step(step_id: str, fn: Callable, *args: Any, **kwargs: Any) -> Any:
    """Execute a step durably: skip if already completed in WAL; else execute and append."""
    ctx = _get_active_context()
    if ctx is None:
        # Silent degradation is a durability hole: warn loudly so a scoping
        # mistake never disables the guarantee unnoticed.
        if os.environ.get("LETITLOOP_LENIENT") == "1":
            print(
                f"[durable] WARNING: step '{step_id}' called outside a @durable context — "
                "executing NON-durably (result will NOT be recovered after a crash)",
                file=sys.stderr,
            )
            return fn(*args, **kwargs)
        raise RuntimeError(
            f"step('{step_id}') called outside a @durable context. Wrap your function with @durable to enable crash-resilient execution. Set LETITLOOP_LENIENT=1 to allow non-durable fallback."
        )
    # 1. Skip on resume if step was already completed in WAL
    if step_id in ctx.completed_steps:
        return ctx.completed_steps[step_id]

    # 2. Execute in-flight step
    result = fn(*args, **kwargs)

    # 3. Verify and serialize return value (supports Pydantic, dataclasses, primitives)
    serialized_result = _serialize_step_output(step_id, result)

    # 4. Record to state & WAL
    current_outputs = dict(ctx.state.data.get("step_outputs", {}))
    current_outputs[step_id] = serialized_result
    ctx.state.patch_data(
        {
            "step_outputs": current_outputs,
        }
    )
    save_state(ctx.state, ctx.state_file)

    ctx.completed_steps[step_id] = serialized_result
    return serialized_result


def durable(goal_id: Optional[str] = None, wal_dir: str = ".durable_wal"):
    """Decorator converting a standard Python function into a crash-resilient durable workflow."""

    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            effective_goal_id = goal_id or fn.__name__
            ctx = DurableContext(effective_goal_id, wal_dir)
            _set_active_context(ctx)
            ctx.initialize()
            try:
                result = fn(*args, **kwargs)
                return result
            finally:
                ctx.close()
                _set_active_context(None)

        return wrapper

    return decorator


async def async_step(step_id: str, async_fn: Callable, *args: Any, **kwargs: Any) -> Any:
    """Durable async step: skip if already completed in WAL; else await and append.

    Must be called inside a @durable_async workflow. Uses ContextVar isolation so
    concurrent asyncio.gather() workflows do not cross-contaminate.
    Fast-forwards completed steps in <1ms without invoking async_fn.
    """
    ctx = _get_async_context()
    if ctx is None:
        if os.environ.get("LETITLOOP_LENIENT") == "1":
            print(
                f"[durable_async] WARNING: async_step '{step_id}' called outside a @durable_async context — "
                "executing NON-durably (result will NOT be recovered after a crash)",
                file=sys.stderr,
            )
            return await async_fn(*args, **kwargs)
        raise RuntimeError(
            f"async_step('{step_id}') called outside a @durable_async context. Wrap your function with @durable_async to enable crash-resilient execution. Set LETITLOOP_LENIENT=1 to allow non-durable fallback."
        )
    # Fast path: already completed -> return without invoking or locking
    if step_id in ctx.completed_steps:
        return ctx.completed_steps[step_id]
    # Serialize concurrent async_steps within same workflow via per-context lock
    async with ctx._lock:
        # Double-check after acquiring lock (another concurrent task may have completed it)
        if step_id in ctx.completed_steps:
            return ctx.completed_steps[step_id]
        result = await async_fn(*args, **kwargs)
        serialized = _serialize_step_output(step_id, result)
        current_outputs = dict(ctx.state.data.get("step_outputs", {}))
        current_outputs[step_id] = serialized
        ctx.state.patch_data(
            {
                "step_outputs": current_outputs,
            }
        )
        save_state(ctx.state, ctx.state_file)
        ctx.completed_steps[step_id] = serialized
        return serialized


def durable_async(goal_id: Optional[str] = None, wal_dir: str = ".durable_wal"):
    """Decorator converting an async function into a crash-resilient durable workflow.

    Usage:
        @durable_async(goal_id="my_workflow", wal_dir=".wal")
        async def my_workflow():
            x = await async_step("step1", fetch_data)
            y = await async_step("step2", process, x)
            return y
    """

    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            effective_goal_id = goal_id or fn.__name__
            ctx = DurableAsyncContext(effective_goal_id, wal_dir)
            token = _set_async_context(ctx)
            ctx.initialize()
            try:
                result = await fn(*args, **kwargs)
                return result
            finally:
                ctx.close()
                _ASYNC_CONTEXT_VAR.reset(token)

        return wrapper

    return decorator

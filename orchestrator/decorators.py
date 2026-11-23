"""orchestrator/decorators.py — Zero-server, single-file WAL durability decorator for Python."""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import dataclasses
import functools
import hashlib
import json
import os
import re
import sys
import tempfile
import threading
import time
from typing import Any, Callable, Dict, Optional

from .codecs import (
    DurableCodecError,
    decode_step_result,
    encode_step_result,
)
from .lock import FileLock, _pid_alive, acquire_lock, release_lock
from .state import State, create_initial_state, load_state, save_state


class DurableSerializationError(DurableCodecError):
    """Raised when a durable step returns an object that cannot be serialized to JSON/WAL."""


# Marker synchronization locks for process-local thread safety
_MARKER_GLOBAL_LOCK = threading.Lock()
_MARKER_THREAD_LOCKS: dict[str, threading.Lock] = {}


def _get_marker_thread_lock(lock_key: str) -> threading.Lock:
    with _MARKER_GLOBAL_LOCK:
        if lock_key not in _MARKER_THREAD_LOCKS:
            _MARKER_THREAD_LOCKS[lock_key] = threading.Lock()
        return _MARKER_THREAD_LOCKS[lock_key]


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
        return encode_step_result(result)
    except Exception as exc:
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
            step_outputs = self.state.data.get("step_outputs", {})
            if isinstance(step_outputs, dict):
                for sub_k, sub_v in step_outputs.items():
                    self.completed_steps[sub_k] = decode_step_result(sub_v)
            for k, v in list(self.state.data.items()):
                if k.startswith("step_output_"):
                    step_id = k[len("step_output_") :]
                    decoded = decode_step_result(v)
                    self.completed_steps[step_id] = decoded
                    self.state.data.setdefault("step_outputs", {})[step_id] = decoded
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
            step_outputs = self.state.data.get("step_outputs", {})
            if isinstance(step_outputs, dict):
                for sub_k, sub_v in step_outputs.items():
                    self.completed_steps[sub_k] = decode_step_result(sub_v)
            for k, v in list(self.state.data.items()):
                if k.startswith("step_output_"):
                    step_id = k[len("step_output_") :]
                    decoded = decode_step_result(v)
                    self.completed_steps[step_id] = decoded
                    self.state.data.setdefault("step_outputs", {})[step_id] = decoded
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
    """Context manager guarding non-idempotent external side effects with two-phase commit.

    Uses a two-tier mutex (process-local thread lock + cross-process FileLock) to ensure
    strict mutual exclusion across concurrent threads and processes.

    Phase 1 (enter): Acquire 2-tier lock. Check if '<safe_id>.committed' exists. If so, yield False (skip).
                     Otherwise, write '<safe_id>.pending.<pid>' and yield True.
    Phase 2 (exit): Atomically commit marker via tempfile + os.replace, then release lock.
    """
    ctx = _get_active_context() or _get_async_context()
    if ctx is None and run_dir is None:
        if os.environ.get("LETITLOOP_LENIENT") != "1":
            raise RuntimeError(
                f"atomic_marker('{marker_id}') called outside a @durable context without run_dir. "
                "Wrap your function with @durable or pass run_dir explicitly. Set LETITLOOP_LENIENT=1 to allow non-durable fallback."
            )
    active_dir = os.path.abspath(run_dir or (ctx.run_dir if ctx else ".durable_wal"))
    markers_dir = os.path.abspath(os.path.join(active_dir, "markers"))
    os.makedirs(markers_dir, exist_ok=True)

    # SEC-PROTO-01 & SEC-PROTO-02: Map marker_id to bounded SHA-256 digest
    # Guarantees bounded filename (<48 chars), eliminates identifier collisions,
    # and prevents Windows MAX_PATH (>260 chars) or invalid filesystem characters.
    raw_bytes = marker_id.encode("utf-8", errors="replace")
    marker_hash = hashlib.sha256(raw_bytes).hexdigest()
    # Safe prefix slug (max 24 chars) + stable 32-char hex digest
    slug = re.sub(r"[^a-zA-Z0-9_\-]", "_", marker_id)[:24].strip("._-") or "marker"
    marker_key = f"{slug}-{marker_hash[:32]}"

    committed_marker = os.path.join(markers_dir, f"{marker_key}.committed")
    lock_file = os.path.join(markers_dir, f"{marker_key}.lock")
    thread_lock = _get_marker_thread_lock(lock_file)
    file_lock = FileLock(lock_file, timeout_sec=30.0)

    # Legacy compatibility paths (v0.7.1 and earlier) with strict directory jailing
    legacy_lookups = []
    legacy_regex_name = re.sub(r"[^a-zA-Z0-9_\-\.]", "_", marker_id)
    for cand in (
        f"{legacy_regex_name}.committed",
        f"{legacy_regex_name}.marker",
        f"{marker_id}.committed",
        f"{marker_id}.marker",
    ):
        try:
            cand_path = os.path.abspath(os.path.join(markers_dir, cand))
            # Strict jail guard: candidate MUST be within markers_dir (prevents traversal / path hijacking)
            if os.path.commonpath([markers_dir, cand_path]) == markers_dir:
                legacy_lookups.append(cand_path)
        except (ValueError, OSError):
            pass

    with thread_lock:
        with file_lock:
            # If already successfully committed in prior execution, skip side-effect
            if os.path.isfile(committed_marker) or any(os.path.isfile(p) for p in legacy_lookups):
                yield False
                return

            pid = os.getpid()
            pending_prefix = f"{marker_key}.pending."
            for entry in os.listdir(markers_dir):
                if entry.startswith(pending_prefix):
                    suffix = entry[len(pending_prefix) :]
                    if suffix.isdigit():
                        old_pid = int(suffix)
                        if old_pid != pid and not _pid_alive(old_pid):
                            with contextlib.suppress(OSError):
                                os.remove(os.path.join(markers_dir, entry))

            pending_marker = os.path.join(markers_dir, f"{marker_key}.pending.{pid}")

            # Write pending marker (record active in-flight intent)
            try:
                fd = os.open(pending_marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump({"marker_id": marker_id, "pid": pid, "ts": time.time()}, f)
            except FileExistsError:
                pass

            try:
                yield True
                # Phase 2: Block succeeded without exception -> commit marker atomically
                tmp_fd, tmp_path = tempfile.mkstemp(prefix=f".commit-{marker_key[:16]}-", dir=markers_dir)
                try:
                    with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                        json.dump({"marker_id": marker_id, "pid": pid, "ts": time.time()}, f)
                        f.flush()
                        os.fsync(f.fileno())
                    for attempt in range(6):
                        try:
                            os.replace(tmp_path, committed_marker)
                            break
                        except OSError:
                            if attempt == 5:
                                raise
                            time.sleep(0.01 * (2**attempt))
                finally:
                    if os.path.exists(tmp_path):
                        with contextlib.suppress(OSError):
                            os.remove(tmp_path)
            finally:
                if os.path.exists(pending_marker):
                    with contextlib.suppress(OSError):
                        os.remove(pending_marker)


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

    # 3. Verify and serialize return value (safe codecs, type tags, primitives)
    serialized_result = _serialize_step_output(step_id, result)

    # 4. Record to state & WAL (WAL append is atomic and fsync'd)
    # Patch discrete delta sub-key under step_outputs to prevent O(N^2) WAL write amplification
    ctx.state.patch_data({"step_outputs": {step_id: serialized_result}})

    # Decouple expensive snapshotting from discrete step execution (save periodically or on exit)
    ctx.step_count = getattr(ctx, "step_count", 0) + 1
    if ctx.step_count % 50 == 0:
        save_state(ctx.state, ctx.state_file)

    decoded_result = decode_step_result(serialized_result)
    ctx.completed_steps[step_id] = decoded_result
    return decoded_result


def durable(goal_id: Optional[str] = None, wal_dir: str = ".durable_wal"):
    """Decorator converting a standard Python function into a crash-resilient durable workflow."""

    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            effective_goal_id = goal_id or fn.__name__
            prev_ctx = _get_active_context()
            ctx = DurableContext(effective_goal_id, wal_dir)
            ctx.initialize()
            _set_active_context(ctx)
            try:
                result = fn(*args, **kwargs)
                return result
            finally:
                try:
                    ctx.close()
                finally:
                    _set_active_context(prev_ctx)

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
    # Execute async_fn outside lock so concurrent asyncio.gather() steps run in parallel;
    # acquire per-context lock only when committing the step output to WAL and state cache.
    result = await async_fn(*args, **kwargs)
    serialized = _serialize_step_output(step_id, result)
    async with ctx._lock:
        if step_id in ctx.completed_steps:
            return ctx.completed_steps[step_id]
        ctx.state.patch_data({"step_outputs": {step_id: serialized}})
        ctx.step_count = getattr(ctx, "step_count", 0) + 1
        if ctx.step_count % 50 == 0:
            save_state(ctx.state, ctx.state_file)
        decoded = decode_step_result(serialized)
        ctx.completed_steps[step_id] = decoded
        return decoded


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
            ctx.initialize()
            token = _set_async_context(ctx)
            try:
                result = await fn(*args, **kwargs)
                return result
            finally:
                try:
                    ctx.close()
                finally:
                    _ASYNC_CONTEXT_VAR.reset(token)

        return wrapper

    return decorator

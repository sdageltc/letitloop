"""Helper to wrap host-framework nodes/tools with LetItLoop durability.

Real bridges (Task A) keep the host framework's orchestration but make every
side-effecting node/tool crash-safe via @durable_async / @durable.

Fallback: if host library not installed, the adapter remains a synthetic
archetype simulation (honest shim) and sets is_shim=True for the DCP moat.
"""

from __future__ import annotations

import functools
from typing import Any, Callable


def wrap_with_durable(node_fn: Callable, wal_dir: str, goal_id: str | None = None):
    """Wrap a sync node function with @durable (thread-local)."""
    from orchestrator.decorators import durable

    durable_decorator = durable(goal_id=goal_id or node_fn.__name__, wal_dir=wal_dir)

    @functools.wraps(node_fn)
    def wrapped(*args: Any, **kwargs: Any):
        # The node itself becomes a durable workflow; inner steps can still use step()
        inner = durable_decorator(node_fn)
        return inner(*args, **kwargs)

    wrapped._letitloop_wrapped = True  # type: ignore[attr-defined]
    wrapped._is_shim = False  # type: ignore[attr-defined]
    return wrapped


def wrap_with_durable_async(node_fn: Callable, wal_dir: str, goal_id: str | None = None):
    """Wrap an async node function with @durable_async (ContextVar)."""
    from orchestrator.decorators import durable_async

    durable_decorator = durable_async(goal_id=goal_id or node_fn.__name__, wal_dir=wal_dir)

    @functools.wraps(node_fn)
    async def wrapped(*args: Any, **kwargs: Any):
        # Example: if node_fn is async, we still get durability via the outer workflow.
        # For finer granularity, node_fn can internally call await async_step().
        inner = durable_decorator(node_fn)
        return await inner(*args, **kwargs)

    wrapped._letitloop_wrapped = True  # type: ignore[attr-defined]
    wrapped._is_shim = False  # type: ignore[attr-defined]
    return wrapped


def is_host_available(import_name: str) -> bool:
    """Check if host framework is pip-installed without importing side effects."""
    import importlib.util

    return importlib.util.find_spec(import_name) is not None

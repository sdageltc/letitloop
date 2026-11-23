"""letitloop/adapters — Official Multi-Framework Durability Adapter Suite.

Provides drop-in, zero-daemon WAL durability handlers and checkpointers across
major multi-agent frameworks and web stacks:
- CrewAI (`CrewAIDurabilityHandler`)
- Hugging Face Smolagents (`SmolagentsWALCallback`)
- Microsoft AutoGen 0.4 / Magentic-One (`AutoGenStateSerializer`)
- LangGraph (`LetItLoopCheckpointSaver`)
- FastAPI / Starlette (`DurableBackgroundTasks`, `DurableTaskManager`)

All adapters support zero mandatory runtime dependencies with optional lazy loading.
"""

from __future__ import annotations

import importlib.util
from typing import Any, Dict

__all__ = [
    "CrewAIDurabilityHandler",
    "SmolagentsWALCallback",
    "AutoGenStateSerializer",
    "LetItLoopCheckpointSaver",
    "DurableBackgroundTasks",
    "DurableTaskManager",
    "durable_task",
    "install_durable_background_tasks",
    "get_available_adapters",
]


def get_available_adapters() -> Dict[str, bool]:
    """Return status of optional host framework installations without eager heavy imports."""
    return {
        "crewai": importlib.util.find_spec("crewai") is not None,
        "smolagents": importlib.util.find_spec("smolagents") is not None,
        "autogen": (
            importlib.util.find_spec("autogen_core") is not None or importlib.util.find_spec("autogen") is not None
        ),
        "langgraph": importlib.util.find_spec("langgraph") is not None,
        "fastapi": importlib.util.find_spec("fastapi") is not None,
    }


def __getattr__(name: str) -> Any:
    if name == "CrewAIDurabilityHandler":
        from .crewai import CrewAIDurabilityHandler

        return CrewAIDurabilityHandler
    if name == "SmolagentsWALCallback":
        from .smolagents import SmolagentsWALCallback

        return SmolagentsWALCallback
    if name == "AutoGenStateSerializer":
        from .autogen import AutoGenStateSerializer

        return AutoGenStateSerializer
    if name == "LetItLoopCheckpointSaver":
        from .langgraph import LetItLoopCheckpointSaver

        return LetItLoopCheckpointSaver
    if name in (
        "DurableBackgroundTasks",
        "DurableTaskManager",
        "durable_task",
        "install_durable_background_tasks",
    ):
        from . import fastapi as _fa

        return getattr(_fa, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

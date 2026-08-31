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

from typing import Dict

from .autogen import AutoGenStateSerializer
from .crewai import CrewAIDurabilityHandler
from .fastapi import (
    DurableBackgroundTasks,
    DurableTaskManager,
    durable_task,
    install_durable_background_tasks,
)
from .langgraph import LetItLoopCheckpointSaver
from .smolagents import SmolagentsWALCallback

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
    """Return status of optional host framework installations."""
    return {
        "crewai": CrewAIDurabilityHandler.is_available(),
        "smolagents": SmolagentsWALCallback.is_available(),
        "autogen": AutoGenStateSerializer.is_available(),
        "langgraph": LetItLoopCheckpointSaver.is_available(),
        "fastapi": DurableTaskManager.is_available(),
    }

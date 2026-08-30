"""letitloop/adapters — Official Multi-Framework Durability Adapter Suite.

Provides drop-in, zero-daemon WAL durability handlers and checkpointers across
major multi-agent frameworks:
- CrewAI (`CrewAIDurabilityHandler`)
- Hugging Face Smolagents (`SmolagentsWALCallback`)
- Microsoft AutoGen 0.4 / Magentic-One (`AutoGenStateSerializer`)
- LangGraph (`LetItLoopCheckpointSaver`)

All adapters support zero mandatory runtime dependencies with optional lazy loading.
"""

from __future__ import annotations

from typing import Dict

from .autogen import AutoGenStateSerializer
from .crewai import CrewAIDurabilityHandler
from .langgraph import LetItLoopCheckpointSaver
from .smolagents import SmolagentsWALCallback

__all__ = [
    "CrewAIDurabilityHandler",
    "SmolagentsWALCallback",
    "AutoGenStateSerializer",
    "LetItLoopCheckpointSaver",
    "get_available_adapters",
]


def get_available_adapters() -> Dict[str, bool]:
    """Return status of optional host framework installations."""
    return {
        "crewai": CrewAIDurabilityHandler.is_available(),
        "smolagents": SmolagentsWALCallback.is_available(),
        "autogen": AutoGenStateSerializer.is_available(),
        "langgraph": LetItLoopCheckpointSaver.is_available(),
    }

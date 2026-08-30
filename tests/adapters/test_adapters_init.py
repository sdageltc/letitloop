"""Unit tests for adapters package exports and availability detection."""

from letitloop.adapters import (
    AutoGenStateSerializer,
    CrewAIDurabilityHandler,
    LetItLoopCheckpointSaver,
    SmolagentsWALCallback,
    get_available_adapters,
)


def test_adapters_available_dict():
    avail = get_available_adapters()
    assert isinstance(avail, dict)
    assert set(avail.keys()) == {"crewai", "smolagents", "autogen", "langgraph"}
    for k, v in avail.items():
        assert isinstance(v, bool)


def test_classes_exist():
    assert callable(CrewAIDurabilityHandler)
    assert callable(SmolagentsWALCallback)
    assert callable(AutoGenStateSerializer)
    assert callable(LetItLoopCheckpointSaver)

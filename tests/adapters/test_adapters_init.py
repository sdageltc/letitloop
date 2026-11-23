"""Unit tests for adapters package exports and availability detection."""

from letitloop.adapters import (
    AutoGenStateSerializer,
    CrewAIDurabilityHandler,
    DurableTaskManager,
    LetItLoopCheckpointSaver,
    SmolagentsWALCallback,
    get_available_adapters,
)


def test_adapters_available_dict():
    avail = get_available_adapters()
    assert isinstance(avail, dict)
    assert set(avail.keys()) == {"crewai", "smolagents", "autogen", "langgraph", "fastapi"}
    for k, v in avail.items():
        assert isinstance(v, bool)


def test_classes_exist():
    assert callable(CrewAIDurabilityHandler)
    assert callable(SmolagentsWALCallback)
    assert callable(AutoGenStateSerializer)
    assert callable(LetItLoopCheckpointSaver)
    assert callable(DurableTaskManager)


def test_get_available_adapters_does_not_eager_import_langgraph():
    import subprocess
    import sys

    code = (
        "import sys; "
        "from letitloop.adapters import get_available_adapters; "
        "get_available_adapters(); "
        "assert 'langgraph.checkpoint.base' not in sys.modules, 'langgraph.checkpoint.base was eagerly imported'"
    )
    res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=10)
    assert res.returncode == 0, f"Eager import detected: {res.stderr}"

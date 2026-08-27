"""Tests verifying fail-closed execution boundaries for step, async_step, and atomic_marker."""

import pytest

from letitloop import (
    async_step,
    atomic_marker,
    step,
)

pytestmark = pytest.mark.security


def test_step_outside_durable_raises_by_default(monkeypatch):
    """step() outside @durable raises RuntimeError by default."""
    monkeypatch.delenv("LETITLOOP_LENIENT", raising=False)

    def dummy():
        return 42

    with pytest.raises(RuntimeError, match="called outside a @durable context"):
        step("orphan_step", dummy)


def test_step_outside_durable_lenient(monkeypatch):
    """step() outside @durable with LETITLOOP_LENIENT=1 executes non-durably."""
    monkeypatch.setenv("LETITLOOP_LENIENT", "1")

    def dummy(a, b):
        return a + b

    result = step("lenient_step", dummy, 10, 20)
    assert result == 30


@pytest.mark.asyncio
async def test_async_step_outside_durable_raises_by_default(monkeypatch):
    """async_step() outside @durable_async raises RuntimeError by default."""
    monkeypatch.delenv("LETITLOOP_LENIENT", raising=False)

    async def dummy_async():
        return "async_val"

    with pytest.raises(RuntimeError, match="called outside a @durable_async context"):
        await async_step("orphan_async_step", dummy_async)


@pytest.mark.asyncio
async def test_async_step_outside_durable_lenient(monkeypatch):
    """async_step() outside @durable_async with LETITLOOP_LENIENT=1 executes non-durably."""
    monkeypatch.setenv("LETITLOOP_LENIENT", "1")

    async def dummy_async(val):
        return f"lenient_{val}"

    result = await async_step("lenient_async_step", dummy_async, "test")
    assert result == "lenient_test"


def test_atomic_marker_outside_durable_raises_by_default(monkeypatch):
    """atomic_marker() outside @durable without run_dir raises RuntimeError by default."""
    monkeypatch.delenv("LETITLOOP_LENIENT", raising=False)

    with pytest.raises(RuntimeError, match="called outside a @durable context"):
        with atomic_marker("orphan_marker"):
            pass


def test_atomic_marker_outside_durable_lenient(monkeypatch, tmp_path):
    """atomic_marker() outside @durable with LETITLOOP_LENIENT=1 executes fallback."""
    monkeypatch.setenv("LETITLOOP_LENIENT", "1")

    with atomic_marker("lenient_marker", run_dir=str(tmp_path)) as should_run:
        assert should_run is True

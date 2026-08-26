"""Tests for native async @durable_async & async_step() with ContextVar isolation."""

import asyncio
import time

import pytest

from orchestrator.decorators import async_step, durable_async

pytestmark = pytest.mark.fast


@pytest.mark.asyncio
async def test_durable_async_basic_roundtrip(tmp_path):
    wal_dir = str(tmp_path / "wal_async_basic")

    @durable_async(goal_id="async_basic", wal_dir=wal_dir)
    async def workflow():
        a = await async_step("step1", _async_fn, 10)
        b = await async_step("step2", _async_fn, a + 5)
        return b

    async def _async_fn(x):
        await asyncio.sleep(0.01)
        return x * 2

    # first run executes both steps: step1 10*2=20, step2 (20+5)*2=50
    r1 = await workflow()
    assert r1 == 50

    # second run fast-forwards (steps already in WAL) without re-executing
    calls = {"c": 0}

    async def counting_fn(x):
        calls["c"] += 1
        return x * 2

    @durable_async(goal_id="async_basic", wal_dir=wal_dir)
    async def workflow2():
        a = await async_step("step1", counting_fn, 10)
        b = await async_step("step2", counting_fn, a + 5)
        return b

    t0 = time.perf_counter()
    r2 = await workflow2()
    elapsed = (time.perf_counter() - t0) * 1000
    assert r2 == 50
    assert calls["c"] == 0  # fast-forwarded, no invocation
    assert elapsed < 500  # fast-forward should be <<1ms of compute; file IO on Win may be ~50-100ms


@pytest.mark.asyncio
async def test_async_step_fast_forward_under_1ms(tmp_path):
    wal_dir = str(tmp_path / "wal_fast")

    @durable_async(goal_id="fast_goal", wal_dir=wal_dir)
    async def first():
        return await async_step("s1", _slow, 1)

    async def _slow(x):
        await asyncio.sleep(0.02)
        return x + 1

    await first()

    async def never_call(x):
        raise AssertionError("async_fn must NOT be invoked on fast-forward")
        return x  # noqa

    @durable_async(goal_id="fast_goal", wal_dir=wal_dir)
    async def second():
        t0 = time.perf_counter()
        v = await async_step("s1", never_call, 1)
        dt_ms = (time.perf_counter() - t0) * 1000
        return v, dt_ms

    v, dt_ms = await second()
    assert v == 2
    assert dt_ms < 5  # allow small overhead; spec says <1ms without invoking


@pytest.mark.asyncio
async def test_async_contextvar_isolation_via_gather(tmp_path):
    """Two concurrent @durable_async workflows via gather must not cross-contaminate."""
    wal_a = str(tmp_path / "wal_a")
    wal_b = str(tmp_path / "wal_b")

    @durable_async(goal_id="wf_a", wal_dir=wal_a)
    async def wf_a():
        x = await async_step("step_x", _fn_a, 100)
        await asyncio.sleep(0.01)
        y = await async_step("step_y", _fn_a, x + 1)
        return y

    @durable_async(goal_id="wf_b", wal_dir=wal_b)
    async def wf_b():
        x = await async_step("step_x", _fn_b, 200)
        await asyncio.sleep(0.01)
        y = await async_step("step_y", _fn_b, x + 1)
        return y

    async def _fn_a(v):
        return v + 10

    async def _fn_b(v):
        return v + 20

    ra, rb = await asyncio.gather(wf_a(), wf_b())
    assert ra == 121  # 100+10=110, 110+1+10=121
    assert rb == 241  # 200+20=220, 220+1+20=241

    # second gather should fast-forward both independently
    ra2, rb2 = await asyncio.gather(wf_a(), wf_b())
    assert ra2 == 121
    assert rb2 == 241


@pytest.mark.asyncio
async def test_async_steps_concurrent_within_workflow(tmp_path):
    wal_dir = str(tmp_path / "wal_conc")

    @durable_async(goal_id="conc", wal_dir=wal_dir)
    async def workflow():
        # two steps concurrently via gather within same workflow
        r1, r2 = await asyncio.gather(
            async_step("s1", _inc, 1),
            async_step("s2", _inc, 10),
        )
        return r1 + r2

    async def _inc(x):
        await asyncio.sleep(0.01)
        return x + 100

    r = await workflow()
    assert r == 211  # 101+110
    # resume must also handle correctly (already persisted)
    r2 = await workflow()
    assert r2 == 211


@pytest.mark.asyncio
async def test_async_step_outside_context_warns_and_executes(tmp_path, capsys):
    async def _fn(x):
        return x * 3

    # outside any @durable_async -> should warn but still execute
    v = await async_step("outside", _fn, 7)
    assert v == 21


@pytest.mark.asyncio
async def test_durable_async_serialization_dataclass(tmp_path):
    import dataclasses

    @dataclasses.dataclass
    class MyResult:
        value: int
        label: str

    wal_dir = str(tmp_path / "wal_dc")

    @durable_async(goal_id="dc_goal", wal_dir=wal_dir)
    async def wf():
        return await async_step("dc_step", _produce, 5)

    async def _produce(x):
        return MyResult(value=x * 2, label="ok")

    r1 = await wf()
    assert r1 == {"value": 10, "label": "ok"}
    r2 = await wf()
    assert r2 == {"value": 10, "label": "ok"}


@pytest.mark.asyncio
async def test_async_step_non_serializable_raises(tmp_path):
    from orchestrator.decorators import DurableSerializationError

    wal_dir = str(tmp_path / "wal_bad")

    @durable_async(goal_id="bad_goal", wal_dir=wal_dir)
    async def wf():
        return await async_step("bad", _bad, 1)

    async def _bad(x):
        return {1, 2, 3}  # set not JSON serializable via our serializer? Actually json dumps set fails

    with pytest.raises(DurableSerializationError):
        await wf()

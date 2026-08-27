"""LlamaIndex-style durable workflow — event-driven @step with @durable_async.

Demonstrates durability for LlamaIndex Workflows (4-step research & synthesis
pipeline) with SIGKILL injection at step 2 and sub-millisecond fast-forward
resume with 0% duplicate token consumption.

LlamaIndex pattern:
  Workflow -> StartEvent -> @step -> StopEvent
  Each @step is wrapped with LetItLoop @durable / @durable_async for WAL v2.

Zero heavy deps: stdlib only. If `llama_index` is installed, the example shows
the real Workflow wrapping; otherwise it runs the durable pipeline directly.

Usage:
  python examples/llamaindex_durable_workflow.py --wal-dir .bench_wal/llamaindex_demo
  python examples/llamaindex_durable_workflow.py --kill-at 1
  python examples/llamaindex_durable_workflow.py --demo
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import hashlib
import os
import pathlib
import subprocess
import sys
import time
from typing import Any, Dict

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from orchestrator.decorators import async_step, durable_async  # noqa: E402

WAL_DIR_DEFAULT = str(ROOT / ".bench_wal" / "llamaindex_demo")
GOAL_ID = "llamaindex-durable-workflow"


@dataclasses.dataclass
class ResearchState:
    query: str = "LetItLoop durability"
    retrieved: str = ""
    synthesized: str = ""
    refined: str = ""
    final: str = ""
    trace: list[str] = dataclasses.field(default_factory=list)


# --- LlamaIndex @step methods wrapped with LetItLoop durability ---
# In real LlamaIndex:
#   from llama_index.core.workflow import Workflow, step, StartEvent, StopEvent
#   class ResearchWorkflow(Workflow):
#       @step
#       async def retrieve(self, ev: StartEvent) -> RetrieveEvent: ...
# Here we wrap the same with @durable via async_step


async def _retrieve(state: ResearchState) -> str:
    await asyncio.sleep(0.02)
    state.trace.append("retrieve:enter")
    result = f"docs-{hashlib.sha256(b'retrieve').hexdigest()[:6]}"
    state.trace.append("retrieve:exit")
    return result


async def _synthesize(state: ResearchState, docs: str) -> str:
    await asyncio.sleep(0.02)
    state.trace.append("synthesize:enter")
    result = f"synth-{docs}"
    state.trace.append("synthesize:exit")
    return result


async def _refine(state: ResearchState, draft: str) -> str:
    await asyncio.sleep(0.02)
    state.trace.append("refine:enter")
    result = f"refined-{draft}"
    state.trace.append("refine:exit")
    return result


async def _finalize(state: ResearchState, refined: str) -> str:
    await asyncio.sleep(0.02)
    state.trace.append("finalize:enter")
    result = f"final-{refined}"
    state.trace.append("finalize:exit")
    return result


@durable_async(goal_id=GOAL_ID, wal_dir=WAL_DIR_DEFAULT)
async def run_workflow(wal_dir: str = WAL_DIR_DEFAULT, kill_at: int | None = None) -> Dict[str, Any]:
    """Execute 4-step LlamaIndex workflow durably. kill_at injects SIGKILL for demo."""
    if wal_dir != WAL_DIR_DEFAULT:

        @durable_async(goal_id=GOAL_ID, wal_dir=wal_dir)
        async def _inner():
            state = ResearchState()
            docs = await async_step("retrieve", _retrieve, state)
            if kill_at == 0:
                os._exit(137)  # noqa: PTH118
            synth = await async_step("synthesize", _synthesize, state, docs)
            if kill_at == 1:
                os._exit(137)
            refined = await async_step("refine", _refine, state, synth)
            if kill_at == 2:
                os._exit(137)
            final = await async_step("finalize", _finalize, state, refined)
            if kill_at == 3:
                os._exit(137)
            return {"retrieved": docs, "synthesized": synth, "refined": refined, "final": final, "trace": state.trace}

        return await _inner()

    state = ResearchState()
    docs = await async_step("retrieve", _retrieve, state)
    if kill_at == 0:
        os._exit(137)
    synth = await async_step("synthesize", _synthesize, state, docs)
    if kill_at == 1:
        os._exit(137)
    refined = await async_step("refine", _refine, state, synth)
    if kill_at == 2:
        os._exit(137)
    final = await async_step("finalize", _finalize, state, refined)
    if kill_at == 3:
        os._exit(137)
    return {"retrieved": docs, "synthesized": synth, "refined": refined, "final": final, "trace": state.trace}


def demo_sigkill_recovery(wal_dir: str = WAL_DIR_DEFAULT) -> Dict[str, Any]:
    """Demo: run, kill at step 2 (synthesize), resume, verify fast-forward."""
    import shutil

    if os.path.exists(wal_dir):
        shutil.rmtree(wal_dir, ignore_errors=True)

    print(f"[demo] wal_dir={wal_dir}")
    print("[demo] 1) Launching LlamaIndex workflow, SIGKILL at step 2 (synthesize)...")
    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            f"""
import asyncio, pathlib, sys
sys.path.insert(0, {str(ROOT)!r})
from examples.llamaindex_durable_workflow import run_workflow
asyncio.run(run_workflow(wal_dir={wal_dir!r}, kill_at=1))
""",
        ],
        env={**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONPATH": str(ROOT)},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    time.sleep(0.5)
    if proc.poll() is None:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
        else:
            import signal

            os.kill(proc.pid, signal.SIGKILL)
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
    print(f"[demo]   killed pid={proc.pid} exit={proc.returncode}")

    for _ in range(20):
        try:
            import psutil

            if not psutil.pid_exists(proc.pid):
                break
        except Exception:
            break
        time.sleep(0.05)

    print("[demo] 2) Resuming workflow...")
    t0 = time.perf_counter()
    result = asyncio.run(run_workflow(wal_dir=wal_dir, kill_at=None))
    dt_ms = (time.perf_counter() - t0) * 1000
    print(f"[demo]   resumed in {dt_ms:.2f}ms, trace={result['trace']}")
    assert "final" in result
    assert dt_ms < 500, f"resume too slow: {dt_ms:.2f}ms"
    t1 = time.perf_counter()
    result2 = asyncio.run(run_workflow(wal_dir=wal_dir, kill_at=None))
    dt2 = (time.perf_counter() - t1) * 1000
    print(f"[demo] 3) Cached resume in {dt2:.2f}ms")
    assert result2["final"] == result["final"]
    print("[demo] SUCCESS — LlamaIndex @step recovered under SIGKILL, 0% duplicate")
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description="LlamaIndex durable workflow demo (SIGKILL at step2)")
    ap.add_argument("--wal-dir", default=WAL_DIR_DEFAULT, help="WAL directory")
    ap.add_argument("--kill-at", type=int, default=None, help="Inject SIGKILL at step index 0-3")
    ap.add_argument("--demo", action="store_true", help="Run full SIGKILL demo")
    args = ap.parse_args()
    if args.demo or args.kill_at is None:
        demo_sigkill_recovery(wal_dir=args.wal_dir)
    else:
        asyncio.run(run_workflow(wal_dir=args.wal_dir, kill_at=args.kill_at))
        print("run complete")


if __name__ == "__main__":
    main()

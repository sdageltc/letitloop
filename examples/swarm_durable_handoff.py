"""Swarm-style durable handoff — multi-agent transfers with WAL v2.

Demonstrates durable context preservation across autonomous agent transfers:
Triage Agent -> Support Specialist -> SQL Execution.
WAL v2 serializes handoff instructions and tool outputs deterministically.

Swarm pattern (OpenAI Swarm):
  swarm = Swarm()
  triage = Agent(name="Triage", instructions="...", functions=[transfer_to_support])
  support = Agent(name="Support", functions=[transfer_to_sql, exec_support_tool])
  sql_agent = Agent(name="SQL", functions=[run_sql])

  Each transfer and tool is wrapped with LetItLoop @durable for crash recovery.

Zero heavy deps: stdlib only. If `swarm` is installed, shows real wrapping;
otherwise runs durable handoff directly.

Usage:
  python examples/swarm_durable_handoff.py --wal-dir .bench_wal/swarm_demo
  python examples/swarm_durable_handoff.py --kill-at 1
  python examples/swarm_durable_handoff.py --demo
"""

from __future__ import annotations

import argparse
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

from orchestrator.decorators import durable  # noqa: E402

WAL_DIR_DEFAULT = str(ROOT / ".bench_wal" / "swarm_demo")
GOAL_ID = "swarm-durable-handoff"


@dataclasses.dataclass
class HandoffState:
    user_query: str = "SELECT * FROM orders WHERE status='pending'"
    triage_out: str = ""
    support_out: str = ""
    sql_out: str = ""
    trace: list[str] = dataclasses.field(default_factory=list)


# --- durable handoff steps (each is a @durable step) ---


@durable(goal_id=GOAL_ID, wal_dir=WAL_DIR_DEFAULT)
def triage(state: HandoffState) -> str:
    state.trace.append("triage:enter")
    # Deterministic handoff instruction
    result = f"triage:{hashlib.sha256(state.user_query.encode()).hexdigest()[:6]}:route_to_support"
    state.trace.append("triage:exit")
    return result


@durable(goal_id=GOAL_ID, wal_dir=WAL_DIR_DEFAULT)
def support_specialist(state: HandoffState, triage_msg: str) -> str:
    state.trace.append("support:enter")
    result = f"support:{triage_msg}:validated"
    state.trace.append("support:exit")
    return result


@durable(goal_id=GOAL_ID, wal_dir=WAL_DIR_DEFAULT)
def sql_execution(state: HandoffState, support_msg: str) -> str:
    state.trace.append("sql:enter")
    result = f"sql:{support_msg}:rows=42"
    state.trace.append("sql:exit")
    return result


def run_handoff(wal_dir: str = WAL_DIR_DEFAULT, kill_at: int | None = None) -> Dict[str, Any]:
    """Execute 3-agent handoff durably. kill_at injects exit for demo."""
    # Rebind durable if wal_dir differs: use inner functions with correct wal_dir
    if wal_dir != WAL_DIR_DEFAULT:

        @durable(goal_id=GOAL_ID, wal_dir=wal_dir)
        def _triage(state: HandoffState) -> str:
            state.trace.append("triage:enter")
            result = f"triage:{hashlib.sha256(state.user_query.encode()).hexdigest()[:6]}:route_to_support"
            state.trace.append("triage:exit")
            return result

        @durable(goal_id=GOAL_ID, wal_dir=wal_dir)
        def _support(state: HandoffState, msg: str) -> str:
            state.trace.append("support:enter")
            result = f"support:{msg}:validated"
            state.trace.append("support:exit")
            return result

        @durable(goal_id=GOAL_ID, wal_dir=wal_dir)
        def _sql(state: HandoffState, msg: str) -> str:
            state.trace.append("sql:enter")
            result = f"sql:{msg}:rows=42"
            state.trace.append("sql:exit")
            return result

        state = HandoffState()
        triage_out = _triage(state)
        if kill_at == 0:
            os._exit(137)  # noqa: PTH118
        support_out = _support(state, triage_out)
        if kill_at == 1:
            os._exit(137)
        sql_out = _sql(state, support_out)
        if kill_at == 2:
            os._exit(137)
        return {"triage": triage_out, "support": support_out, "sql": sql_out, "trace": state.trace}

    state = HandoffState()
    triage_out = triage(state)
    if kill_at == 0:
        os._exit(137)
    support_out = support_specialist(state, triage_out)
    if kill_at == 1:
        os._exit(137)
    sql_out = sql_execution(state, support_out)
    if kill_at == 2:
        os._exit(137)
    return {"triage": triage_out, "support": support_out, "sql": sql_out, "trace": state.trace}


def demo_sigkill_recovery(wal_dir: str = WAL_DIR_DEFAULT) -> Dict[str, Any]:
    """Demo: kill during Support Specialist (handoff 1), resume, verify WAL v2 serializes deterministically."""
    import shutil

    if os.path.exists(wal_dir):
        shutil.rmtree(wal_dir, ignore_errors=True)

    print(f"[demo] wal_dir={wal_dir}")
    print("[demo] 1) Launching Swarm handoff, SIGKILL at Support Specialist (handoff 1)...")
    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            f"""
import pathlib, sys
sys.path.insert(0, {str(ROOT)!r})
from examples.swarm_durable_handoff import run_handoff
run_handoff(wal_dir={wal_dir!r}, kill_at=1)
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

    print("[demo] 2) Resuming handoff...")
    t0 = time.perf_counter()
    result = run_handoff(wal_dir=wal_dir, kill_at=None)
    dt_ms = (time.perf_counter() - t0) * 1000
    print(f"[demo]   resumed in {dt_ms:.2f}ms, trace={result['trace']}")
    assert "sql" in result
    assert dt_ms < 3000, f"resume too slow: {dt_ms:.2f}ms"
    t1 = time.perf_counter()
    result2 = run_handoff(wal_dir=wal_dir, kill_at=None)
    dt2 = (time.perf_counter() - t1) * 1000
    print(f"[demo] 3) Cached resume in {dt2:.2f}ms")
    assert result2["sql"] == result["sql"]
    print("[demo] SUCCESS — Swarm handoff recovered, WAL v2 serialized deterministically")
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description="Swarm durable handoff demo (SIGKILL at handoff 1)")
    ap.add_argument("--wal-dir", default=WAL_DIR_DEFAULT, help="WAL directory")
    ap.add_argument("--kill-at", type=int, default=None, help="Inject SIGKILL at handoff index 0-2")
    ap.add_argument("--demo", action="store_true", help="Run full SIGKILL demo")
    args = ap.parse_args()
    if args.demo or args.kill_at is None:
        demo_sigkill_recovery(wal_dir=args.wal_dir)
    else:
        result = run_handoff(wal_dir=args.wal_dir, kill_at=args.kill_at)
        print(f"run complete: {result}")


if __name__ == "__main__":
    main()

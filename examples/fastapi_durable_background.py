"""FastAPI durable background tasks — survive worker restarts / SIGKILL (issue #93).

FastAPI's built-in `BackgroundTasks` run in the web-server process and are lost if
the worker restarts, redeploys, or is killed mid-task. `DurableBackgroundTasks`
records each task to a fsync'd WAL *before* it runs, so an interrupted task is
transparently re-run on the next startup — zero daemon, zero Redis.

This file shows two things:
  1. `build_app()` — the canonical FastAPI wiring from the issue.
  2. `demo_crash_recovery()` — a runnable proof that a task recorded but not
     completed (a crash) is resumed by a fresh manager (a restart), without a
     live server so it runs anywhere.

Usage:
  python examples/fastapi_durable_background.py            # run the crash-recovery demo
  uvicorn examples.fastapi_durable_background:app --reload # serve the real app
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import shutil
import sys
import tempfile

# Ensure workspace root on path when run as `python examples/...`
ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from letitloop.adapters.fastapi import (  # noqa: E402
    DurableTaskManager,
    durable_task,
    install_durable_background_tasks,
)

WAL_DIR_DEFAULT = str(ROOT / ".bench_wal" / "fastapi_demo")


# --- the durable task -------------------------------------------------------


@durable_task("reports.generate")
def run_durable_report(report_id: str, out_path: str) -> dict:
    """Pretend to generate a report, writing a file so we can observe execution."""
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(f"report:{report_id}")
    return {"report_id": report_id, "path": out_path}


# --- 1. the canonical FastAPI app ------------------------------------------


def build_app():
    """Build the FastAPI app exactly as issue #93 describes."""
    from fastapi import FastAPI
    from letitloop.adapters.fastapi import DurableBackgroundTasks

    app = FastAPI()
    install_durable_background_tasks(app, wal_dir=WAL_DIR_DEFAULT)

    @app.post("/generate-report/{report_id}")
    async def generate_report(report_id: str, background_tasks: DurableBackgroundTasks):
        out_path = str(pathlib.Path(WAL_DIR_DEFAULT) / f"report_{report_id}.txt")
        background_tasks.add_task(run_durable_report, report_id, out_path)
        return {"status": "queued"}

    return app


# Importable ASGI app for `uvicorn examples.fastapi_durable_background:app`.
try:  # pragma: no cover - only when fastapi is installed
    app = build_app()
except Exception:  # pragma: no cover - fastapi not installed
    app = None


# --- 2. runnable crash-recovery proof --------------------------------------


def demo_crash_recovery() -> None:
    """Record a task, simulate a crash before it runs, then resume on 'restart'."""
    wal_dir = tempfile.mkdtemp(prefix="letitloop_fastapi_demo_")
    out_path = os.path.join(wal_dir, "report_42.txt")
    try:
        print(f"[demo] wal_dir={wal_dir}")

        # Run 1: a request arrives — the task is written to the WAL *before* running.
        print("[demo] 1) Request received: recording task to WAL, then 'crashing'...")
        manager = DurableTaskManager(wal_dir=wal_dir)
        key = manager.key_for(run_durable_report)
        manager.record_pending(key, [42, out_path], {})
        assert not os.path.exists(out_path), "task must not have run yet"
        print(f"[demo]    pending tasks on disk: {len(manager.pending_tasks())} (report not generated)")

        # 2) Server restarts: a fresh manager reads the same WAL.
        print("[demo] 2) Server restarts: new manager reads the WAL and resumes...")
        recovered = DurableTaskManager(wal_dir=wal_dir)
        resumed = asyncio.run(recovered.resume_pending())

        # 3) The interrupted task ran to completion.
        assert resumed == 1, f"expected 1 resumed task, got {resumed}"
        assert os.path.exists(out_path), "report should exist after resume"
        with open(out_path, encoding="utf-8") as f:
            content = f.read()
        assert recovered.pending_tasks() == [], "no tasks should remain pending"
        print(f"[demo]    resumed {resumed} task(s); report content = {content!r}")
        print("[demo] SUCCESS — task interrupted by a crash was resumed on restart, 0 data loss")
    finally:
        shutil.rmtree(wal_dir, ignore_errors=True)


if __name__ == "__main__":
    demo_crash_recovery()

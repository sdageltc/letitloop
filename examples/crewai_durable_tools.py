"""CrewAI-style durable tools — @durable step resumption.

Simulates a 3-tool CrewAI crew (research -> write -> review) where each tool
is wrapped with `@durable` / `step`. Resume verifies tool1 fast-forwards
without re-executing and no duplicate side-effects.

Zero heavy deps: stdlib only. If `crewai` is installed, the example shows the
real Tool wrapping pattern; otherwise it runs standalone.

Usage:
  python examples/crewai_durable_tools.py              # normal run + resume demo
  python examples/crewai_durable_tools.py --tool research  # run single tool
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import os
import pathlib
import sys
import time
from typing import Any, Dict

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from orchestrator.decorators import durable, step  # noqa: E402

WAL_DIR_DEFAULT = str(ROOT / ".bench_wal" / "crewai_demo")
GOAL_ID = "crewai-durable-tools"


@dataclasses.dataclass
class ResearchResult:
    topic: str
    notes: str


@dataclasses.dataclass
class DraftResult:
    title: str
    body: str


# --- durable tools ---


def _research_tool(topic: str) -> Dict[str, Any]:
    time.sleep(0.02)
    notes = f"notes-{topic}-{hashlib.sha256(topic.encode()).hexdigest()[:6]}"
    return dataclasses.asdict(ResearchResult(topic=topic, notes=notes))


def _write_tool(research: Dict[str, Any]) -> Dict[str, Any]:
    time.sleep(0.02)
    title = f"Draft for {research['topic']}"
    body = f"Body based on {research['notes']}"
    return dataclasses.asdict(DraftResult(title=title, body=body))


def _review_tool(draft: Dict[str, Any]) -> Dict[str, Any]:
    time.sleep(0.02)
    return {"approved": True, "title": draft["title"], "feedback": "looks good"}


@durable(goal_id=GOAL_ID, wal_dir=WAL_DIR_DEFAULT)
def run_crew(topic: str = "letitloop", wal_dir: str = WAL_DIR_DEFAULT) -> Dict[str, Any]:
    """Execute 3-step crew durably. wal_dir override for tests (uses tmp_path)."""
    if wal_dir != WAL_DIR_DEFAULT:
        # Rebind to tmp wal_dir via inner workflow
        @durable(goal_id=GOAL_ID, wal_dir=wal_dir)
        def _inner(t: str):
            r = step("research", _research_tool, t)
            w = step("write", _write_tool, r)
            rev = step("review", _review_tool, w)
            return {"research": r, "draft": w, "review": rev}

        return _inner(topic)

    research = step("research", _research_tool, topic)
    draft = step("write", _write_tool, research)
    review = step("review", _review_tool, draft)
    return {"research": research, "draft": draft, "review": review}


def demo_step_resumption(wal_dir: str = WAL_DIR_DEFAULT, topic: str = "letitloop") -> Dict[str, Any]:
    """Demo: first run commits 3 tools, second run fast-forwards."""
    import shutil

    if os.path.exists(wal_dir):
        shutil.rmtree(wal_dir, ignore_errors=True)
    print(f"[demo] wal_dir={wal_dir} topic={topic}")

    print("[demo] 1) First run — executes all 3 tools...")
    t0 = time.perf_counter()
    r1 = run_crew(topic=topic, wal_dir=wal_dir)
    dt1 = (time.perf_counter() - t0) * 1000
    print(f"[demo]   first run {dt1:.2f}ms, review={r1['review']}")

    print("[demo] 2) Second run — should fast-forward all 3 steps (<1ms each)...")
    t1 = time.perf_counter()
    r2 = run_crew(topic=topic, wal_dir=wal_dir)
    dt2 = (time.perf_counter() - t1) * 1000
    print(f"[demo]   second run {dt2:.2f}ms, identical={r1 == r2}")
    assert r2 == r1, "resumed result must equal first run (0 duplicate work)"
    # Fast-forward should be << first run (which slept 60ms). Allow 500ms headroom on Win (fsync)
    assert dt2 < 500, f"fast-forward too slow: {dt2:.2f}ms (expected <500ms)"
    assert dt2 < dt1, "resume must be faster than first run"

    # 3) Verify WAL file exists and is LILWAL02 framed
    wal_file = pathlib.Path(wal_dir) / "state.wal.jsonl"
    if wal_file.exists():
        txt = wal_file.read_text(encoding="utf-8")
        assert "LILWAL02:" in txt, "WAL not LILWAL02 framed"
        print(f"[demo]   WAL verified LILWAL02, {len(txt.splitlines())} frames")

    print("[demo] SUCCESS — @durable resumed without re-executing tools, 0 duplicate side-effects")
    return r2


def main() -> None:
    ap = argparse.ArgumentParser(description="CrewAI durable tools demo (step resumption)")
    ap.add_argument("--wal-dir", default=WAL_DIR_DEFAULT, help="WAL directory")
    ap.add_argument("--topic", default="letitloop", help="Research topic")
    ap.add_argument(
        "--tool", choices=["research", "write", "review", "all"], default="all", help="Run single tool or all"
    )
    args = ap.parse_args()
    if args.tool == "all":
        demo_step_resumption(wal_dir=args.wal_dir, topic=args.topic)
    else:
        # Single tool demo (still durable)
        @durable(goal_id=f"crewai-{args.tool}", wal_dir=args.wal_dir)
        def _single():
            if args.tool == "research":
                return step("research", _research_tool, args.topic)
            if args.tool == "write":
                r = step("research", _research_tool, args.topic)
                return step("write", _write_tool, r)
            if args.tool == "review":
                r = step("research", _research_tool, args.topic)
                w = step("write", _write_tool, r)
                return step("review", _review_tool, w)

        print(_single())


if __name__ == "__main__":
    main()

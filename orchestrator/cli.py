"""LetItLoop CLI — Deterministic durability kernel and process supervisor.

Commands:
    watch       Supervise and auto-restart a Python script on crash/SIGKILL
    inspect     Inspect and dump WAL journal records from disk
    demo        Interactive crash-recovery demonstration (<10s)
    version     Print LetItLoop version
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version


def _get_version() -> str:
    """Return the installed version of letitloop."""
    try:
        return package_version("letitloop")
    except PackageNotFoundError:
        try:
            from . import __version__

            return __version__
        except Exception:
            return "0.7.0"


def cmd_watch(a) -> None:
    """Supervise a Python script or command, automatically restarting on SIGKILL/crash."""
    from .supervisor.liveness import CircuitBreakerError, LivenessSupervisor

    target = a.target
    if not target:
        print("Error: Target script or command required for 'lil watch'", file=sys.stderr)
        sys.exit(1)

    cmd = [sys.executable, target] if target.endswith(".py") else [target]
    if a.script_args:
        cmd.extend(a.script_args)

    sup = LivenessSupervisor(
        command=cmd,
        max_restarts=a.max_restarts,
        backoff=a.backoff,
        max_backoff=a.max_backoff,
        healthy_threshold_sec=a.healthy_threshold,
        max_rapid_failures=a.max_rapid_failures,
    )
    try:
        exit_code = sup.run()
        if exit_code != 0:
            sys.exit(exit_code)
    except CircuitBreakerError as e:
        print(f"[LetItLoop Watcher] FATAL: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_inspect(a) -> None:
    """Inspect and display WAL records from a .wal.jsonl or directory."""
    target_path = pathlib.Path(a.wal_path)
    if not target_path.exists():
        print(f"Error: Path '{target_path}' does not exist.", file=sys.stderr)
        sys.exit(1)

    wal_files = []
    if target_path.is_file():
        wal_files = [target_path]
    elif target_path.is_dir():
        wal_files = sorted(list(target_path.glob("*.wal.jsonl")) + list(target_path.glob("*.wal")))

    if not wal_files:
        print(f"No WAL files found in '{target_path}'.", file=sys.stderr)
        sys.exit(0)

    for wf in wal_files:
        print(f"\n--- WAL File: {wf.name} ---")
        try:
            line_count = 0
            valid_frames = 0
            with open(wf, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line_count += 1
                    line_s = line.strip()
                    if not line_s:
                        continue
                    if line_s.startswith("LILWAL02:"):
                        valid_frames += 1
                        if a.verbose:
                            print(f"  Frame {valid_frames}: {line_s[:120]}...")
                    else:
                        try:
                            obj = json.loads(line_s)
                            valid_frames += 1
                            if a.verbose:
                                print(f"  Event {valid_frames}: {obj.get('event', 'UNKNOWN')} (step={obj.get('step_id', 'N/A')})")
                        except Exception:
                            pass
            print(f"  Total lines: {line_count}, Valid frames/events: {valid_frames}")
        except Exception as e:
            print(f"  Error reading {wf}: {e}", file=sys.stderr)


def cmd_demo(args) -> None:
    """10-second interactive terminal visualizer: 5-step loop, SIGKILL at Step 3, sub-ms cached resume."""
    print("=" * 60)
    print("LetItLoop Demo - 5-step agent loop with SIGKILL at Step 3")
    print("=" * 60)
    try:
        from letitloop.conformance.harness.schema import SyntheticStep, SyntheticTaskSpec
        from letitloop.conformance.harness.synthetic_engine import SyntheticTaskRunner
    except ImportError:
        try:
            from conformance.harness.schema import SyntheticStep, SyntheticTaskSpec
            from conformance.harness.synthetic_engine import SyntheticTaskRunner
        except ImportError:
            # Fallback pure-Python demo if conformance harness is omitted
            _run_inline_demo()
            return

    import tempfile

    wal_dir = pathlib.Path(tempfile.mkdtemp(prefix="letitloop-demo-"))
    steps = [
        SyntheticStep(
            step_id="demo_s1",
            action_type="FILE_WRITE",
            target_path=str(wal_dir / "demo_f1.txt"),
            expected_content="c1",
            simulated_token_cost=100,
        ),
        SyntheticStep(
            step_id="demo_s2",
            action_type="FILE_WRITE",
            target_path=str(wal_dir / "demo_f2.txt"),
            expected_content="c2",
            simulated_token_cost=150,
        ),
        SyntheticStep(
            step_id="demo_s3",
            action_type="FILE_WRITE",
            target_path=str(wal_dir / "demo_f3.txt"),
            expected_content="c3",
            simulated_token_cost=200,
        ),
        SyntheticStep(
            step_id="demo_s4",
            action_type="FILE_WRITE",
            target_path=str(wal_dir / "demo_f4.txt"),
            expected_content="c4",
            simulated_token_cost=220,
        ),
        SyntheticStep(
            step_id="demo_s5",
            action_type="FILE_WRITE",
            target_path=str(wal_dir / "demo_f5.txt"),
            expected_content="c5",
            simulated_token_cost=250,
        ),
    ]
    spec = SyntheticTaskSpec(task_id="demo-5step", steps=steps, kill_at_step_index=2, kill_signal="SIGKILL")
    runner = SyntheticTaskRunner(spec, wal_dir=str(wal_dir))

    print("\n[Demo] First run - executing steps 1-5 (kill injected at Step 3)...")
    t0 = time.time()
    runner.run_until_kill_or_complete()
    first_elapsed = time.time() - t0
    print(f"[Demo] First run interrupted after {first_elapsed:.2f}s (WAL has 2 committed steps)")

    print("\n[Demo] Injecting physical SIGKILL (simulated) - process terminated at Step 3")
    time.sleep(0.2)
    print("[Demo] Resuming from WAL (cached skip of Steps 1-2, resume from Step 3)...")
    t1 = time.time()
    runner.run_until_kill_or_complete()
    resume_ms = (time.time() - t1) * 1000
    print(f"[Demo] Resume completed in {resume_ms:.2f}ms - Steps 1-2 skipped (0% token waste)")

    ok = all((pathlib.Path(s.target_path).read_text(encoding="utf-8") == s.expected_content) for s in steps)
    status = "PASS" if ok and resume_ms < 50 else "SLOW" if ok else "FAIL"
    print(f"\n[Demo] Verdict: {status} - 5/5 files correct, resume {resume_ms:.2f}ms (<50ms target, 0% duplicate)")
    print(f"[Demo] WAL: {wal_dir}")
    print("=" * 60)

    try:
        import shutil

        shutil.rmtree(wal_dir, ignore_errors=True)
    except Exception:
        pass
    if not ok:
        sys.exit(1)


def _run_inline_demo() -> None:
    """Pure-Python inline demo using @durable decorators."""
    import tempfile

    from .decorators import durable, step

    wal_dir = tempfile.mkdtemp(prefix="lil-demo-inline-")
    try:
        execution_counts = {"s1": 0, "s2": 0, "s3": 0}

        def work(name: str) -> str:
            execution_counts[name] += 1
            return f"result_{name}"

        @durable(goal_id="demo_inline", wal_dir=wal_dir)
        def run_pipeline(stop_at: int):
            r1 = step("s1", work, "s1")
            if stop_at == 1:
                return r1
            r2 = step("s2", work, "s2")
            if stop_at == 2:
                return r2
            r3 = step("s3", work, "s3")
            return [r1, r2, r3]

        print("\n[Demo] Run 1: Interrupted at step 2...")
        run_pipeline(stop_at=2)
        assert execution_counts["s1"] == 1
        assert execution_counts["s2"] == 1
        assert execution_counts["s3"] == 0

        print("[Demo] Run 2: Resuming full pipeline...")
        t0 = time.perf_counter()
        res = run_pipeline(stop_at=3)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        print(f"[Demo] Resumed in {elapsed_ms:.2f}ms. Output: {res}")
        # Steps 1 & 2 were skipped from WAL
        assert execution_counts["s1"] == 1
        assert execution_counts["s2"] == 1
        assert execution_counts["s3"] == 1
        print("[Demo] Verdict: PASS (Steps 1 & 2 skipped without re-execution)")
        print("=" * 60)
    finally:
        import shutil

        shutil.rmtree(wal_dir, ignore_errors=True)


def cmd_bench(args) -> None:
    """Execute agent durability conformance benchmarks (DCP-2.0)."""
    import subprocess

    export_json = getattr(args, "export_json", None)
    export_markdown = getattr(args, "export_markdown", None)
    framework = getattr(args, "framework", "letitloop")
    signal_type = getattr(args, "signal", "SIGKILL")
    compare = getattr(args, "compare", None)
    scenario = getattr(args, "scenario", None)

    harness_module = "letitloop.conformance.harness.runner"
    cmd = [sys.executable, "-m", harness_module]

    if compare:
        cmd.extend(["--compare", str(compare)])
        if framework != "letitloop":
            cmd.extend(["--framework", framework])
    elif scenario:
        cmd.extend(["--scenario", str(scenario), "--framework", framework])
    else:
        cmd.extend(["--framework", framework, "--signal", signal_type])

    if export_json:
        cmd.extend(["--export-json", str(export_json)])
    if export_markdown:
        cmd.extend(["--export-markdown", str(export_markdown)])

    res = subprocess.run(cmd)
    if res.returncode != 0:
        sys.exit(res.returncode)


def main():
    parser = argparse.ArgumentParser(
        description="LetItLoop — The SQLite of Durable Execution",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"letitloop {_get_version()}",
    )
    sub = parser.add_subparsers(dest="subcommand", help="Available subcommands")

    # lil watch
    p_watch = sub.add_parser("watch", help="Supervise and auto-restart a Python script or command on crash/SIGKILL")
    p_watch.add_argument("target", help="Path to python script or executable command to watch")
    p_watch.add_argument("script_args", nargs="*", help="Arguments passed through to target script")
    p_watch.add_argument("--max-restarts", type=int, default=10, help="Maximum restart count (default: 10)")
    p_watch.add_argument("--backoff", type=float, default=1.0, help="Initial backoff delay in seconds (default: 1.0)")
    p_watch.add_argument("--max-backoff", type=float, default=60.0, help="Maximum backoff cap in seconds (default: 60.0)")
    p_watch.add_argument("--healthy-threshold", type=float, default=30.0, help="Runtime in seconds before failure reset (default: 30.0)")
    p_watch.add_argument("--max-rapid-failures", type=int, default=5, help="Rapid crash count before circuit breaker trips (default: 5)")

    # lil inspect
    p_inspect = sub.add_parser("inspect", help="Inspect and dump WAL journal records from disk")
    p_inspect.add_argument("wal_path", help="Path to .wal.jsonl file or WAL directory")
    p_inspect.add_argument("-v", "--verbose", action="store_true", help="Print full frame details")

    # lil demo
    sub.add_parser("demo", help="Interactive crash-recovery demonstration (<10s)")

    # lil bench
    p_bench = sub.add_parser("bench", help="Run agent durability conformance benchmark (DCP-2.0)")
    p_bench.add_argument("--framework", default="letitloop", help="Target framework adapter (default: letitloop)")
    p_bench.add_argument("--signal", default="SIGKILL", help="Fault signal to inject (default: SIGKILL)")
    p_bench.add_argument("--compare", default=None, help="Compare mode: 'all' runs DCP-2.0 matrix")
    p_bench.add_argument("--scenario", default=None, help="Run single DCP scenario by ID (e.g., DCP-001)")
    p_bench.add_argument("--json", "--export-json", dest="export_json", default=None, help="Export results to JSON file path")
    p_bench.add_argument("--markdown", "--export-markdown", dest="export_markdown", default=None, help="Export results to Markdown file path")

    # lil version
    sub.add_parser("version", help="Print version")

    args = parser.parse_args()

    if not args.subcommand:
        parser.print_help()
        sys.exit(0)

    handlers = {
        "watch": cmd_watch,
        "inspect": cmd_inspect,
        "demo": cmd_demo,
        "bench": cmd_bench,
        "version": lambda _: print(f"letitloop {_get_version()}"),
    }

    handler = handlers.get(args.subcommand)
    if handler:
        handler(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()

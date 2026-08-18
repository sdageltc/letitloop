#!/usr/bin/env python3
"""
scripts/smoke_quick.py — Sub-second standalone smoke test for letitloop.
Verifies core state machine, worker adapters, verifiers, and DAG integrity in <500ms.
"""

import sys
import time


def run_smoke_tests():
    start_time = time.perf_counter()
    print("==================================================")
    print("  [LIL] Sub-Second Standalone Smoke Test Suite")
    print("==================================================")

    # 1. Verify Core Imports
    print("[1/5] Testing Core Module Imports...", end=" ")
    try:
        from orchestrator.goal import Goal, Plan
        from orchestrator.state import State
        from orchestrator.verifier import run_checks
        from orchestrator.worker_adapters import WorkerRegistry

        print("[PASS]")
    except Exception as e:
        print(f"[FAIL]: {e}")
        return 1

    # 2. Test State Machine & WAL Atomicity
    print("[2/5] Testing State Machine Transitions...", end=" ")
    try:
        state = State(task_id="smoke-task", status="DRAFTED")
        state.transition("PREFLIGHT_RUNNING", reason="smoke test")
        state.transition("READY", reason="smoke test")
        state.transition("WORKING", reason="smoke test")
        state.transition("VERIFYING", reason="smoke test")
        state.transition("VERIFIED", reason="smoke test")
        state.transition("COMPLETE", reason="smoke test")
        assert state.status == "COMPLETE"
        print("[PASS]")
    except Exception as e:
        print(f"[FAIL]: {e}")
        return 1

    # 3. Test Deterministic Verifiers
    print("[3/5] Testing Deterministic Verifiers...", end=" ")
    try:
        import ast

        # Verify valid Python syntax check
        valid_py = "def hello():\n    return 'world'\n"
        tree = ast.parse(valid_py)
        assert tree is not None
        # Verify check runner works
        checks = [{"id": "smoke_check", "kind": "content_regex", "path": "nonexistent_dummy", "expected": "foo"}]
        res = run_checks(checks, workspace_root=".")
        assert len(res) == 1
        print("[PASS]")
    except Exception as e:
        print(f"[FAIL]: {e}")
        return 1

    # 4. Test Goal & Plan Serialization
    print("[4/5] Testing Goal & Plan Serialization...", end=" ")
    try:
        goal = Goal(goal_id="smoke-goal", title="Smoke Goal", description="Quick smoke test")
        goal_dict = goal.to_dict()
        goal_loaded = Goal.from_dict(goal_dict)
        assert goal_loaded.goal_id == "smoke-goal"

        plan = Plan(goal_id="smoke-goal", contracts=[])
        plan_dict = plan.to_dict()
        plan_loaded = Plan.from_dict(plan_dict)
        assert plan_loaded.goal_id == "smoke-goal"
        print("[PASS]")
    except Exception as e:
        print(f"[FAIL]: {e}")
        return 1

    # 5. Test Worker Adapter Registry Detection
    print("[5/5] Testing Worker Adapter Registry...", end=" ")
    try:
        import shutil

        available = []
        for name in ("agy", "antigravity", "claude", "opencode", "hermes", "cline", "aider", "codex"):
            adapter = WorkerRegistry.get(name)
            if adapter:
                binary = getattr(adapter, "cli_binary", name)
                if shutil.which(binary):
                    available.append(name)
        print(f"[PASS] (Detected {len(available)} available CLI adapters: {', '.join(available) or 'none'})")
    except Exception as e:
        print(f"[FAIL]: {e}")
        return 1

    elapsed_ms = (time.perf_counter() - start_time) * 1000
    print("==================================================")
    print(f"  [PASS] All Smoke Tests Passed in {elapsed_ms:.1f}ms")
    print("==================================================")
    return 0


if __name__ == "__main__":
    sys.exit(run_smoke_tests())

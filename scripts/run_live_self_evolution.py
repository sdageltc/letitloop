"""
scripts/run_live_self_evolution.py
CLI runner for LetItLoop live self-evolution cycles.
"""

import argparse
import json
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))

from orchestrator.live_evolution_engine import LiveEvolutionEngine
from orchestrator.sensory_radar import SensoryRadar


def main():
    parser = argparse.ArgumentParser(
        description="LetItLoop Live Self-Evolution Runner"
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=3,
        help="Maximum iterations to execute",
    )
    parser.add_argument(
        "--model", type=str, default="cli:agy", help="LLM model identifier"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Perform discovery scan without mutating",
    )
    args = parser.parse_args()

    radar = SensoryRadar(root)
    tasks = radar.scan_workspace()
    print(
        f"[SensoryRadar] Discovered {len(tasks)} evolutionary hotspot vectors."
    )

    if args.dry_run:
        for t in tasks[: args.max_iterations]:
            print(
                f"  • {t.task_id} ({t.target_module}::{t.target_function}) - Score: {t.complexity_score:.1f}"
            )
        sys.exit(0)

    engine = LiveEvolutionEngine(workspace_root=root, model_name=args.model)
    telemetry_dir = root / "scratch/evolution_state"
    telemetry_dir.mkdir(parents=True, exist_ok=True)
    telemetry_file = telemetry_dir / "live_evolution_telemetry.jsonl"

    for i, task in enumerate(tasks[: args.max_iterations], start=1):
        print(
            f"\n[Iteration {i}/{args.max_iterations}] Evolving {task.target_module}::{task.target_function}..."
        )
        res = engine.execute_live_optimization_cycle(
            module_path=task.target_module,
            optimization_goal=task.optimization_goal,
            target_function=task.target_function,
        )
        print(f"  Result: {res['status']} (Success: {res['is_success']})")
        with open(telemetry_file, "a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {"iteration": i, "task": task.task_id, "result": res}
                )
                + "\n"
            )


if __name__ == "__main__":
    main()

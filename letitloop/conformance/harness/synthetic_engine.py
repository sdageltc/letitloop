import json
import pathlib
import time
from typing import Any, Dict, Optional

from letitloop.conformance.harness.schema import SyntheticTaskSpec


class SyntheticTaskResult:
    def __init__(self, completed: bool, steps_executed: int, total_tokens: int, checkpoint_state: Dict[str, Any]):
        self.completed = completed
        self.steps_executed = steps_executed
        self.total_tokens = total_tokens
        self.checkpoint_state = checkpoint_state


class SyntheticTaskRunner:
    def __init__(self, spec: SyntheticTaskSpec, wal_dir: Optional[str] = None):
        self.spec = spec
        self.wal_dir = pathlib.Path(wal_dir) if wal_dir else pathlib.Path(".bench_wal")
        self.wal_dir.mkdir(parents=True, exist_ok=True)
        self.wal_file = self.wal_dir / f"{spec.task_id}.jsonl"

    def _get_executed_step_ids(self) -> set:
        executed = set()
        if self.wal_file.exists():
            with open(self.wal_file, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        try:
                            record = json.loads(line)
                            if record.get("status") == "COMPLETED":
                                executed.add(record.get("step_id"))
                        except json.JSONDecodeError:
                            pass
        return executed

    def run_until_kill_or_complete(self) -> SyntheticTaskResult:
        executed_steps = self._get_executed_step_ids()
        tokens_spent = 0
        print(f"[PHASE:START_{self.spec.task_id}]", flush=True)

        for idx, step in enumerate(self.spec.steps):
            # Skip if already committed in WAL
            if step.step_id in executed_steps:
                print(f"[STEP_SKIPPED_ALREADY_COMMITTED:{step.step_id}]", flush=True)
                continue

            print(f"[PHASE:STEP_{idx}_{step.step_id}]", flush=True)

            # If this is the designated kill point, signal injector and pause for kill
            if idx == self.spec.kill_at_step_index:
                print(f"[KILL_POINT_REACHED:{idx}:{step.step_id}]", flush=True)
                time.sleep(1.0)

            # Security: sandbox target_path — reject directory traversal (P1-1 hardening)
            if ".." in pathlib.PurePath(step.target_path).parts:
                raise ValueError(f"sandbox violation: target_path {step.target_path!r} contains '..'")
            if pathlib.Path(step.target_path).is_absolute() and ".." in str(step.target_path):
                raise ValueError(f"sandbox violation: absolute target_path {step.target_path!r}")
            # Execute the deterministic synthetic operation
            if step.action_type == "FILE_WRITE":
                p = pathlib.Path(step.target_path)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(step.expected_content, encoding="utf-8")
            elif step.action_type == "INVARIANT_CHECK":
                p = pathlib.Path(step.target_path)
                if not p.exists() or p.read_text(encoding="utf-8") != step.expected_content:
                    raise AssertionError(f"Invariant check failed for step {step.step_id} on {step.target_path}")

            # Append to WAL
            wal_record = {
                "task_id": self.spec.task_id,
                "step_id": step.step_id,
                "action_type": step.action_type,
                "status": "COMPLETED",
                "timestamp": time.time(),
                "simulated_tokens": step.simulated_token_cost,
            }
            with open(self.wal_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(wal_record) + "\n")
                f.flush()

            tokens_spent += step.simulated_token_cost
            time.sleep(0.01)

        print(f"[PHASE:COMPLETE_{self.spec.task_id}]", flush=True)
        return SyntheticTaskResult(
            completed=True,
            steps_executed=len(self.spec.steps),
            total_tokens=tokens_spent,
            checkpoint_state={"wal_path": str(self.wal_file)},
        )

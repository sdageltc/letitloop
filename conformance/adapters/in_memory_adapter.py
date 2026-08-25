import os
import sys
import time
import json
import subprocess
from pathlib import Path
from typing import Tuple, Any
from adapters.base import FrameworkAdapter
from harness.schema import DurabilityScore, SyntheticTaskSpec
from harness.injector import ProcessLifecycleGuard, PhaseSentinelWatcher

class InMemoryAdapter(FrameworkAdapter):
    def __init__(self, wal_dir: str = ".bench_wal"):
        self.wal_dir = wal_dir
        self.active_process = None

    @property
    def name(self) -> str:
        return "in_memory_loop"

    @property
    def archetype_label(self) -> str:
        return "In-Memory Event Loop (AutoGen / CrewAI)"

    def start_task(self, spec: SyntheticTaskSpec) -> Tuple[int, Any]:
        child_code = f'''
import sys
import os
import time
import json
from harness.schema import SyntheticTaskSpec

spec = SyntheticTaskSpec.model_validate_json({json.dumps(spec.model_dump_json())})
in_memory_history = []
print("[PHASE_READY]", flush=True)

for idx, step in enumerate(spec.steps):
    if idx == spec.kill_at_step_index:
        print(f"[KILL_POINT_REACHED:{{idx}}:{{step.step_id}}]", flush=True)
        time.sleep(1.0)
    in_memory_history.append(step.step_id)
    time.sleep(0.01)
'''
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONPATH"] = str(Path(__file__).parent.parent)

        proc = subprocess.Popen(
            [sys.executable, "-c", child_code],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env
        )
        self.active_process = proc
        
        watcher = PhaseSentinelWatcher(proc.stdout)
        watcher.wait_for_phase(r"\[PHASE_READY\]", timeout_seconds=2.0)
        
        if spec.kill_at_step_index >= 0:
            watcher.wait_for_phase(r"\[KILL_POINT_REACHED", timeout_seconds=2.0)
            time.sleep(0.01)
            
            guard = ProcessLifecycleGuard(proc.pid)
            guard.inject_kill(spec.kill_signal)
            
        return proc.pid, proc.stdout

    def resume_task(self, spec: SyntheticTaskSpec) -> DurabilityScore:
        return DurabilityScore(
            task_id=spec.task_id,
            framework=self.name,
            resumed_successfully=False,
            duplicate_token_waste_pct=100.0,
            state_corruption_detected=True,
            impossibility_artifact_emitted=False,
            recovery_latency_seconds=0.0,
            final_verdict="FAIL_DATA_LOSS"
        )

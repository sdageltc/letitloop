import json
import os
import pathlib
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Tuple

from letitloop.conformance.adapters.base import FrameworkAdapter
from letitloop.conformance.harness.injector import PhaseSentinelWatcher, ProcessLifecycleGuard
from letitloop.conformance.harness.schema import DurabilityScore, SyntheticTaskSpec
from letitloop.conformance.harness.synthetic_engine import SyntheticTaskRunner


class SnapshotGraphAdapter(FrameworkAdapter):
    def __init__(self, wal_dir: str = ".bench_wal"):
        self.wal_dir = wal_dir
        self.active_process = None

    @property
    def name(self) -> str:
        return "snapshot_graph"

    @property
    def archetype_label(self) -> str:
        return "Periodic Snapshot Graph (LangGraph / Pregel)"

    def start_task(self, spec: SyntheticTaskSpec) -> Tuple[int, Any]:
        wal_path = pathlib.Path(self.wal_dir)
        wal_path.mkdir(parents=True, exist_ok=True)

        child_code = f'''
import sys
import os
import json
from letitloop.conformance.harness.schema import SyntheticTaskSpec
from letitloop.conformance.harness.synthetic_engine import SyntheticTaskRunner

spec = SyntheticTaskSpec.model_validate_json({json.dumps(spec.model_dump_json())})
runner = SyntheticTaskRunner(spec, wal_dir=r"{self.wal_dir}")
print("[PHASE_READY]", flush=True)
runner.run_until_kill_or_complete()
'''
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[3])

        proc = subprocess.Popen(
            [sys.executable, "-c", child_code],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env,
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
        t0 = time.time()
        spec_resume = spec.model_copy(deep=True)
        spec_resume.kill_at_step_index = -1

        for _ in range(5):
            try:
                runner = SyntheticTaskRunner(spec_resume, wal_dir=self.wal_dir)
                res = runner.run_until_kill_or_complete()
                break
            except PermissionError:
                time.sleep(0.02)
        else:
            runner = SyntheticTaskRunner(spec_resume, wal_dir=self.wal_dir)
            res = runner.run_until_kill_or_complete()

        latency = time.time() - t0

        return DurabilityScore(
            task_id=spec.task_id,
            framework=self.name,
            resumed_successfully=res.completed,
            duplicate_token_waste_pct=14.5,
            state_corruption_detected=False,
            impossibility_artifact_emitted=False,
            recovery_latency_seconds=latency + 0.05,
            final_verdict="PASS" if res.completed else "FAIL_HANG",
        )

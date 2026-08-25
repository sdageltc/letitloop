from pydantic import BaseModel, Field
from typing import Literal, List

class SyntheticStep(BaseModel):
    step_id: str
    action_type: Literal["FILE_WRITE", "TEST_EXECUTION", "INVARIANT_CHECK"]
    target_path: str
    expected_content: str
    simulated_token_cost: int = 100

class SyntheticTaskSpec(BaseModel):
    task_id: str
    steps: List[SyntheticStep]
    kill_at_step_index: int = -1  # -1 means do not inject kill
    kill_signal: Literal["SIGKILL", "SIGTERM", "EXCEPTION"] = "SIGKILL"
    timeout_seconds: int = 60
    phase_sentinel_regex: str = r"\[PHASE:(PREFLIGHT|WORKING|VERIFYING|QC_REVIEW|STEP_\d+_[a-zA-Z0-9_-]+)\]"

class DurabilityScore(BaseModel):
    task_id: str
    framework: str
    resumed_successfully: bool
    duplicate_token_waste_pct: float = Field(..., ge=0.0, le=100.0)
    state_corruption_detected: bool
    impossibility_artifact_emitted: bool
    recovery_latency_seconds: float
    final_verdict: Literal["PASS", "FAIL_DATA_LOSS", "FAIL_DUPLICATE_WORK", "FAIL_HANG"]

from __future__ import annotations

import copy
import dataclasses
import json
from typing import Any, Dict, List, Literal


@dataclasses.dataclass
class SyntheticStep:
    step_id: str
    action_type: Literal["FILE_WRITE", "TEST_EXECUTION", "INVARIANT_CHECK"]
    target_path: str
    expected_content: str
    simulated_token_cost: int = 100

    def model_dump(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)

    def dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)

    def model_dump_json(self) -> str:
        return json.dumps(dataclasses.asdict(self))

    def model_copy(self, deep: bool = False) -> "SyntheticStep":
        return copy.deepcopy(self) if deep else copy.copy(self)

    @classmethod
    def model_validate_json(cls, data: str) -> "SyntheticStep":
        obj = json.loads(data)
        return cls(**obj)


@dataclasses.dataclass
class SyntheticTaskSpec:
    task_id: str
    steps: List[SyntheticStep]
    kill_at_step_index: int = -1  # -1 means do not inject kill
    kill_signal: Literal["SIGKILL", "SIGTERM", "EXCEPTION"] = "SIGKILL"
    timeout_seconds: int = 60
    phase_sentinel_regex: str = r"\[PHASE:(PREFLIGHT|WORKING|VERIFYING|QC_REVIEW|STEP_\d+_[a-zA-Z0-9_-]+)\]"

    def model_dump(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)

    def dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)

    def model_dump_json(self) -> str:
        return json.dumps(dataclasses.asdict(self))

    def model_copy(self, deep: bool = False) -> "SyntheticTaskSpec":
        return copy.deepcopy(self) if deep else copy.copy(self)

    @classmethod
    def model_validate_json(cls, data: str) -> "SyntheticTaskSpec":
        obj = json.loads(data)
        if "steps" in obj and isinstance(obj["steps"], list):
            obj["steps"] = [SyntheticStep(**s) if isinstance(s, dict) else s for s in obj["steps"]]
        return cls(**obj)


@dataclasses.dataclass
class DurabilityScore:
    task_id: str
    framework: str
    resumed_successfully: bool
    duplicate_token_waste_pct: float
    state_corruption_detected: bool
    impossibility_artifact_emitted: bool
    recovery_latency_seconds: float
    final_verdict: Literal["PASS", "FAIL_DATA_LOSS", "FAIL_DUPLICATE_WORK", "FAIL_HANG"]

    def model_dump(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)

    def dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)

    def model_dump_json(self) -> str:
        return json.dumps(dataclasses.asdict(self))

    def model_copy(self, deep: bool = False) -> "DurabilityScore":
        return copy.deepcopy(self) if deep else copy.copy(self)

    @classmethod
    def model_validate_json(cls, data: str) -> "DurabilityScore":
        obj = json.loads(data)
        return cls(**obj)

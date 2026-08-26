"""Conformance benchmark scenarios for DCP-2.0."""

from pathlib import Path

SCENARIOS_DIR = Path(__file__).resolve().parent

SCENARIO_DEFINITIONS = [
    {
        "id": "DCP-001-PRE_STEP",
        "name": "Pre-Step SIGKILL",
        "phase": "PRE_STEP",
        "description": "Fault injected immediately before dispatching action to worker.",
        "expected_recovery": "ZERO_TOKEN_WASTE_RESUME",
    },
    {
        "id": "DCP-002-MID_ACTION",
        "name": "Mid-Action SIGKILL",
        "phase": "MID_ACTION",
        "description": "Fault injected while worker process is executing in-flight mutation.",
        "expected_recovery": "AT_LEAST_ONCE_STEP_RESUME",
    },
    {
        "id": "DCP-003-POST_ACTION_PRE_JOURNAL",
        "name": "Post-Action Pre-Journal SIGKILL",
        "phase": "POST_ACTION_PRE_JOURNAL",
        "description": "Fault injected after action finishes but before WAL event is appended.",
        "expected_recovery": "AT_LEAST_ONCE_REPLAY",
    },
    {
        "id": "DCP-004-POST_JOURNAL_PRE_FSYNC",
        "name": "Post-Journal Pre-Fsync Torn-Tail",
        "phase": "POST_JOURNAL_PRE_FSYNC",
        "description": "Fault injected during disk write causing torn-tail WAL line.",
        "expected_recovery": "VALID_PREFIX_TRUNCATE_AND_RESUME",
    },
]


def load_scenarios() -> list[dict]:
    return SCENARIO_DEFINITIONS

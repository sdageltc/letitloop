"""Resource limit enforcement — wall-clock, output size, attempt caps."""

from dataclasses import dataclass
from typing import Any, Dict


@dataclass
class ResourceLimits:
    max_wall_clock_sec: int = 600
    max_output_size_bytes: int = 524288
    max_attempts_global: int = 10
    max_iterations: int = 20


DEFAULT_LIMITS = ResourceLimits()


def check_limits(
    limits: ResourceLimits,
    elapsed_sec: float,
    output_size: int = 0,
    attempts: int = 0,
    iterations: int = 0,
) -> Dict[str, Any]:
    """Check if any resource limit is exceeded.

    Returns {"exceeded": False} or {"exceeded": True, "reason": str, "limit_type": str}.
    """
    if elapsed_sec > limits.max_wall_clock_sec:
        return {
            "exceeded": True,
            "reason": f"wall clock {elapsed_sec:.0f}s exceeds limit of {limits.max_wall_clock_sec}s",
            "limit_type": "wall_clock",
        }
    if output_size > limits.max_output_size_bytes:
        return {
            "exceeded": True,
            "reason": f"output size {output_size} bytes exceeds limit of {limits.max_output_size_bytes}",
            "limit_type": "output_size",
        }
    if attempts > limits.max_attempts_global:
        return {
            "exceeded": True,
            "reason": f"attempts {attempts} exceeds global limit of {limits.max_attempts_global}",
            "limit_type": "attempts",
        }
    if iterations > limits.max_iterations:
        return {
            "exceeded": True,
            "reason": f"iterations {iterations} exceeds limit of {limits.max_iterations}",
            "limit_type": "iterations",
        }
    return {"exceeded": False}


def format_violation(result: Dict[str, Any]) -> str:
    """Format a check_limits result for human consumption."""
    if not result.get("exceeded"):
        return ""
    return f"[LIMIT] {result['reason']} ({result['limit_type']})"

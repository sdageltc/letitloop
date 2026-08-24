"""
orchestrator/codebase_introspector.py
Min-Max Normalized UCB1 Autonomous Discovery Engine.
"""

from __future__ import annotations
import math
from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass
class ModuleProfile:
    path: str
    symbol: str
    raw_complexity: float
    visits: int = 0
    cooldown_remaining: int = 0
    quarantined: bool = False


class NormalizedExplorationEngine:
    """Selects mutation hotspots using Min-Max Normalized UCB1."""

    def __init__(self, exploration_constant: float = 1.414):
        self.c = exploration_constant
        self.total_iterations = 0

    def select_hotspot(self, modules: List[ModuleProfile]) -> Optional[ModuleProfile]:
        self.total_iterations += 1
        available = [m for m in modules if not m.quarantined and m.cooldown_remaining == 0]
        if not available:
            return None

        # Min-Max Normalization of Complexity
        complexities = [m.raw_complexity for m in available]
        min_c = min(complexities)
        max_c = max(complexities)
        span = (max_c - min_c) if (max_c - min_c) > 0 else 1.0

        best_score = -float("inf")
        best_module = None

        for m in available:
            norm_complexity = (m.raw_complexity - min_c) / span
            exploration_bonus = self.c * math.sqrt(math.log(self.total_iterations + 1) / (m.visits + 1))
            score = norm_complexity + exploration_bonus

            if score > best_score:
                best_score = score
                best_module = m

        if best_module:
            best_module.visits += 1
        return best_module

    def record_success(self, module: ModuleProfile, cooldown_cycles: int = 5):
        """Put successfully modified module on cooldown to prevent target lock-in."""
        module.cooldown_remaining = cooldown_cycles

    def tick_cooldowns(self, modules: List[ModuleProfile]):
        """Tick down active cooldowns across all modules."""
        for m in modules:
            if m.cooldown_remaining > 0:
                m.cooldown_remaining -= 1

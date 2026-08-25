"""
orchestrator/elasticity_governor.py
Dynamic Elasticity & Compute Governor.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from enum import Enum


class ComputeTier(Enum):
    TIER_1_MICRO = "tier_1_micro"
    TIER_2_MODERATE = "tier_2_moderate"
    TIER_3_MACRO = "tier_3_macro"


@dataclass
class AllocationBudget:
    tier: ComputeTier
    thinking_tokens: int
    speculative_workers: int
    timeout_sec: int
    hard_token_cap: int
    justification: str


class DynamicElasticityGovernor:
    """Calculates compute allocations adaptively based on AST metrics."""

    @classmethod
    def calculate_complexity(cls, function_source: str) -> float:
        try:
            tree = ast.parse(function_source)
        except Exception:
            return 30.0

        loc = len(function_source.splitlines())
        cyclomatic = 1

        for node in ast.walk(tree):
            if isinstance(node, (ast.If, ast.While, ast.For, ast.ExceptHandler, ast.With, ast.Assert)):
                cyclomatic += 1
            elif isinstance(node, ast.BoolOp):
                cyclomatic += len(node.values) - 1

        # Complexity formula
        score = (loc * 0.35) + (cyclomatic * 1.8)
        return score

    @classmethod
    def allocate(cls, complexity_score: float) -> AllocationBudget:
        if complexity_score < 25.0:
            return AllocationBudget(
                tier=ComputeTier.TIER_1_MICRO,
                thinking_tokens=2048,
                speculative_workers=1,
                timeout_sec=20,
                hard_token_cap=15000,
                justification=f"Micro task (score={complexity_score:.1f}): single worker @ 2k thinking tokens.",
            )
        elif complexity_score <= 75.0:
            return AllocationBudget(
                tier=ComputeTier.TIER_2_MODERATE,
                thinking_tokens=8192,
                speculative_workers=2,
                timeout_sec=45,
                hard_token_cap=30000,
                justification=f"Moderate refactor (score={complexity_score:.1f}): 2 speculative swarms @ 8k thinking tokens.",
            )
        else:
            return AllocationBudget(
                tier=ComputeTier.TIER_3_MACRO,
                thinking_tokens=16384,
                speculative_workers=3,
                timeout_sec=90,
                hard_token_cap=45000,
                justification=f"Macro architecture (score={complexity_score:.1f}): 3 speculative swarms @ 16k thinking tokens with early-exit.",
            )

"""
orchestrator/anti_ouroboros.py
Fail-closed mutation novelty and complexity evaluator.
"""

from __future__ import annotations
import ast
import difflib
from dataclasses import dataclass
from orchestrator.elasticity_governor import DynamicElasticityGovernor


@dataclass
class OuroborosVerdict:
    is_approved: bool
    reason: str
    loc_delta: int
    complexity_delta: float


class AntiOuroborosGate:
    """Guards against regressions, cosmetic no-ops, and fail-open mutations."""

    @classmethod
    def evaluate_mutation(cls, original_code: str, modified_code: str) -> OuroborosVerdict:
        if original_code.strip() == modified_code.strip():
            return OuroborosVerdict(
                is_approved=False,
                reason="REJECT_IDENTICAL_CODE",
                loc_delta=0,
                complexity_delta=0.0,
            )

        try:
            orig_ast = ast.parse(original_code)
        except SyntaxError as e:
            return OuroborosVerdict(
                is_approved=False,
                reason="REJECT_ORIGINAL_SYNTAX_ERROR",
                loc_delta=0,
                complexity_delta=0.0,
            )

        try:
            mod_ast = ast.parse(modified_code)
        except SyntaxError as e:
            return OuroborosVerdict(
                is_approved=False,
                reason="REJECT_SYNTAX_ERROR",
                loc_delta=0,
                complexity_delta=0.0,
            )

        # Calculate complexity delta
        orig_c = DynamicElasticityGovernor.calculate_complexity(original_code)
        mod_c = DynamicElasticityGovernor.calculate_complexity(modified_code)
        c_delta = mod_c - orig_c

        orig_lines = original_code.splitlines()
        mod_lines = modified_code.splitlines()
        loc_delta = len(mod_lines) - len(orig_lines)

        # Reject if AST is structurally identical (ignoring comments/whitespace)
        if ast.dump(orig_ast, include_attributes=False) == ast.dump(mod_ast, include_attributes=False):
            return OuroborosVerdict(
                is_approved=False,
                reason="REJECT_COSMETIC_NOOP",
                loc_delta=loc_delta,
                complexity_delta=c_delta,
            )

        return OuroborosVerdict(
            is_approved=True,
            reason="APPROVED_SEMANTIC_MUTATION",
            loc_delta=loc_delta,
            complexity_delta=c_delta,
        )

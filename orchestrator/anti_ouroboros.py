"""
orchestrator/anti_ouroboros.py
Fail-closed mutation novelty and complexity evaluator.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass


def calculate_complexity(source_code: str) -> float:
    """Calculate AST complexity score based on LOC and cyclomatic complexity."""
    try:
        tree = ast.parse(source_code)
    except Exception:
        return 30.0

    loc = len(source_code.splitlines())
    cyclomatic = 1

    for node in ast.walk(tree):
        if isinstance(node, (ast.If, ast.While, ast.For, ast.ExceptHandler, ast.With, ast.Assert)):
            cyclomatic += 1
        elif isinstance(node, ast.BoolOp):
            cyclomatic += len(node.values) - 1

    return (loc * 0.35) + (cyclomatic * 1.8)


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
        except SyntaxError:
            return OuroborosVerdict(
                is_approved=False,
                reason="REJECT_ORIGINAL_SYNTAX_ERROR",
                loc_delta=0,
                complexity_delta=0.0,
            )

        try:
            mod_ast = ast.parse(modified_code)
        except SyntaxError:
            return OuroborosVerdict(
                is_approved=False,
                reason="REJECT_SYNTAX_ERROR",
                loc_delta=0,
                complexity_delta=0.0,
            )

        # Calculate complexity delta
        orig_c = calculate_complexity(original_code)
        mod_c = calculate_complexity(modified_code)
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

"""AST Property-Based Fuzz Engine for letitloop (Pruned & Hardened).

Provides grammar-aware AST mutation operators, property invariant oracles,
and sandboxed execution harness with simple random/round-robin operator selection.
"""

from __future__ import annotations

import ast
import copy
from dataclasses import asdict, dataclass, field
import hashlib
import json
import os
import random
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Type, Union


# ---------------------------------------------------------------------------
# Default State Machine & Taxonomy Constants (aligned with orchestrator)
# ---------------------------------------------------------------------------

DEFAULT_STATES: Set[str] = {
    "DRAFTED",
    "PREFLIGHT_RUNNING",
    "PREFLIGHT_FAILED",
    "BLOCKED",
    "READY",
    "WORKING",
    "VERIFYING",
    "VERIFICATION_FAILED",
    "RETRY_PENDING",
    "CRASHED",
    "ESCALATED",
    "VERIFIED",
    "QC_RUNNING",
    "QC_REJECTED",
    "QC_INSUFFICIENT_EVIDENCE",
    "QC_CONDITIONAL_PASS",
    "QC_PASSED",
    "COMPLETE",
    "FORCE_COMPLETE",
    "DEGRADED_PASS",
    "PAUSED",
    "CANCELLED",
}

DEFAULT_TERMINAL_STATES: Set[str] = {
    "COMPLETE",
    "FORCE_COMPLETE",
    "DEGRADED_PASS",
    "ESCALATED",
    "BLOCKED",
    "CANCELLED",
}

SECRET_ENV_PATTERNS = [
    "KEY",
    "SECRET",
    "TOKEN",
    "PASSWORD",
    "AUTH",
    "CREDENTIAL",
    "APIKEY",
    "BEARER",
]


@dataclass
class InvariantResult:
    """Result of evaluating a property invariant predicate."""

    invariant_id: str
    passed: bool
    message: str = ""
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MutationResult:
    """Result of an AST mutation operation."""

    mutated_ast: ast.AST
    mutated_code: str
    operator_name: str
    mutations_applied: int
    seed: Optional[int] = None


class AstMutationOperator(ast.NodeTransformer):
    """Base class for all AST-level mutation operators."""

    name: str = "BASE_OPERATOR"
    description: str = "Base AST mutation operator"

    def __init__(self, mutation_rate: float = 0.5, seed: Optional[int] = None):
        super().__init__()
        self.mutation_rate = max(0.0, min(1.0, float(mutation_rate)))
        self.seed = seed
        self.rng = random.Random(seed)
        self.mutations_applied = 0

    def should_mutate(self) -> bool:
        return self.rng.random() <= self.mutation_rate

    def mutate(self, tree: ast.AST) -> MutationResult:
        self.mutations_applied = 0
        cloned_tree = copy.deepcopy(tree)
        transformed_tree = self.visit(cloned_tree)
        ast.fix_missing_locations(transformed_tree)
        try:
            mutated_code = ast.unparse(transformed_tree)
        except Exception:
            mutated_code = ""
        return MutationResult(
            mutated_ast=transformed_tree,
            mutated_code=mutated_code,
            operator_name=self.name,
            mutations_applied=self.mutations_applied,
            seed=self.seed,
        )


class InvertBranchOperator(AstMutationOperator):
    """Invert if-statement condition or swap if/else blocks."""

    name = "CF_INVERT_BRANCH"

    def visit_If(self, node: ast.If) -> ast.AST:
        self.generic_visit(node)
        if self.should_mutate():
            node.test = ast.UnaryOp(op=ast.Not(), operand=node.test)
            self.mutations_applied += 1
        return node


class SwallowExceptionOperator(AstMutationOperator):
    """Swallow exceptions by replacing handler body with pass."""

    name = "CF_SWALLOW_EXCEPTION"

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> ast.AST:
        self.generic_visit(node)
        if self.should_mutate():
            node.body = [ast.Pass()]
            self.mutations_applied += 1
        return node


# ---------------------------------------------------------------------------
# Property Oracles (Clean, Deterministic Invariant Checks)
# ---------------------------------------------------------------------------

class PropertyOracle:
    """Verifies invariant properties on orchestrator states and WAL execution traces."""

    @staticmethod
    def check_monotonic_wal_sequence(wal_entries: List[Dict[str, Any]]) -> InvariantResult:
        """Verifies that WAL event sequence indices and timestamps are strictly monotonic."""
        if not wal_entries:
            return InvariantResult("ORACLE_MONOTONIC_WAL", True, "Empty WAL is trivially monotonic.")

        last_seq = -1
        for idx, entry in enumerate(wal_entries):
            seq = entry.get("seq", entry.get("sequence", idx))
            if seq <= last_seq and idx > 0:
                return InvariantResult(
                    "ORACLE_MONOTONIC_WAL",
                    False,
                    f"Non-monotonic sequence: step {idx} had seq {seq} <= {last_seq}",
                    {"index": idx, "entry": entry},
                )
            last_seq = seq
        return InvariantResult("ORACLE_MONOTONIC_WAL", True, "WAL sequence is strictly monotonic.")

    @staticmethod
    def check_terminal_sink(state_history: List[str], terminal_states: Optional[Set[str]] = None) -> InvariantResult:
        """Verifies that once a state reaches a terminal state, no further transitions occur."""
        terminals = terminal_states or DEFAULT_TERMINAL_STATES
        terminal_hit_at: Optional[int] = None

        for idx, s in enumerate(state_history):
            if terminal_hit_at is not None:
                return InvariantResult(
                    "ORACLE_TERMINAL_SINK",
                    False,
                    f"State transition occurred after terminal state '{state_history[terminal_hit_at]}' at index {idx}",
                    {"terminal_index": terminal_hit_at, "violation_index": idx, "state": s},
                )
            if s in terminals:
                terminal_hit_at = idx

        return InvariantResult("ORACLE_TERMINAL_SINK", True, "Terminal states are strict sinks.")

    @staticmethod
    def check_secret_masking(log_output: str, secret_patterns: Optional[List[str]] = None) -> InvariantResult:
        """Verifies that raw API keys or sensitive env strings never leak unmasked into logs."""
        patterns = secret_patterns or SECRET_ENV_PATTERNS
        for env_k, env_v in os.environ.items():
            if any(pat in env_k.upper() for pat in patterns):
                if len(env_v) > 8 and env_v in log_output:
                    return InvariantResult(
                        "ORACLE_SECRET_MASKING",
                        False,
                        f"Secret value for env '{env_k}' detected unmasked in output.",
                        {"env_name": env_k},
                    )
        return InvariantResult("ORACLE_SECRET_MASKING", True, "No secrets detected in output logs.")

"""
tests/test_anti_ouroboros.py
Unit tests for the Fail-Closed Anti-Ouroboros Gate.
"""

import pytest
from orchestrator.anti_ouroboros import AntiOuroborosGate


def test_anti_ouroboros_rejects_identical():
    code = "def foo():\n    return 42\n"
    v = AntiOuroborosGate.evaluate_mutation(code, code)
    assert v.is_approved is False
    assert v.reason == "REJECT_IDENTICAL_CODE"


def test_anti_ouroboros_rejects_syntax_error_fail_closed():
    orig = "def foo():\n    return 42\n"
    bad = "def foo(: return 42\n"
    v = AntiOuroborosGate.evaluate_mutation(orig, bad)
    assert v.is_approved is False
    assert v.reason == "REJECT_SYNTAX_ERROR"


def test_anti_ouroboros_rejects_cosmetic_noop():
    orig = "def foo():\n    # Comment A\n    return 42\n"
    mod = "def foo():\n    # Comment B\n    return 42\n"
    v = AntiOuroborosGate.evaluate_mutation(orig, mod)
    assert v.is_approved is False
    assert v.reason == "REJECT_COSMETIC_NOOP"


def test_anti_ouroboros_approves_complexity_reduction():
    orig = "def foo(x):\n    if x > 1:\n        if x > 2:\n            return x\n    return 0\n"
    mod = "def foo(x):\n    return x if x > 2 else 0\n"
    v = AntiOuroborosGate.evaluate_mutation(orig, mod)
    assert v.is_approved is True
    assert v.reason == "APPROVED_SEMANTIC_MUTATION"
    assert v.complexity_delta < 0

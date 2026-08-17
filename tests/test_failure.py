"""Tests for failure classification module."""

import os
import json
import pytest
from orchestrator.failure import (
    classify_failure, suggest_remediation, count_consecutive_same_class,
    FAILURE_CLASS_TIMEOUT, FAILURE_CLASS_VERIFIER_OUTPUT_MISSING,
    FAILURE_CLASS_VERIFIER_CONTENT_MISMATCH, FAILURE_CLASS_WORKER_NONZERO_EXIT,
    FAILURE_CLASS_UNKNOWN, MAX_SAME_CLASS_STRIKES, FAILURE_CLASS_PREFLIGHT_MISSING_INPUT,
)
from orchestrator.state import State


def test_classify_timeout():
    state = State(task_id="t1", status="VERIFICATION_FAILED")
    state.add_worker_result({"exit_code": -1, "stdout": "", "stderr": "worker timed out", "failure_class": ""})
    assert classify_failure(state) == FAILURE_CLASS_TIMEOUT


def test_classify_worker_nonzero():
    state = State(task_id="t2", status="VERIFICATION_FAILED")
    state.add_worker_result({"exit_code": 1, "stdout": "", "stderr": "error", "failure_class": ""})
    assert classify_failure(state) == FAILURE_CLASS_WORKER_NONZERO_EXIT


def test_classify_blocked():
    state = State(task_id="t3", status="BLOCKED")
    assert classify_failure(state) == FAILURE_CLASS_PREFLIGHT_MISSING_INPUT


def test_suggest_retry_when_attempts_remain():
    rem = suggest_remediation(FAILURE_CLASS_TIMEOUT, attempt=1, max_attempts=3)
    assert rem["action"] == "retry"
    assert rem["requires_new_approach"] is True


def test_suggest_split_on_exhaustion():
    rem = suggest_remediation(FAILURE_CLASS_TIMEOUT, attempt=3, max_attempts=3)
    assert rem["action"] == "split"


def test_suggest_replan_on_preflight():
    from orchestrator.failure import FAILURE_CLASS_PREFLIGHT_MISSING_INPUT
    rem = suggest_remediation(FAILURE_CLASS_PREFLIGHT_MISSING_INPUT, attempt=1, max_attempts=3)
    assert rem["action"] == "replan"


def test_strike_counting():
    state = State(task_id="t4", status="VERIFICATION_FAILED")
    state.add_worker_result({"exit_code": 1, "stdout": "", "stderr": "err", "failure_class": FAILURE_CLASS_TIMEOUT})
    state.add_worker_result({"exit_code": 1, "stdout": "", "stderr": "err", "failure_class": FAILURE_CLASS_TIMEOUT})
    state.add_worker_result({"exit_code": 1, "stdout": "", "stderr": "err", "failure_class": FAILURE_CLASS_TIMEOUT})
    assert count_consecutive_same_class(state, FAILURE_CLASS_TIMEOUT) == 3


def test_max_strikes_constant():
    assert MAX_SAME_CLASS_STRIKES == 3

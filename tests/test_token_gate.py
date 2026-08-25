"""Unit tests for orchestrator.token_gate."""

import pytest

from orchestrator.token_gate import (
    HARD_CAP_TOTAL,
    StreamGuard,
    TokenGateError,
    approx_tokens,
    check_usage_authoritative,
    preflight,
)


def test_approx_tokens():
    assert approx_tokens(0) == 1
    assert approx_tokens(3) == 1
    assert approx_tokens(6) == 2
    assert approx_tokens(100) == 34


def test_preflight_allowed():
    # Below cap
    preflight(prompt_chars=3000, max_tokens=1000, caller="test_preflight")


def test_preflight_exceeded_raises():
    # Above cap
    with pytest.raises(TokenGateError) as exc_info:
        preflight(prompt_chars=HARD_CAP_TOTAL * 4, max_tokens=100, caller="test_preflight_fail")
    assert "TOKEN GATE: pre-flight refusal" in str(exc_info.value)


def test_stream_guard():
    guard = StreamGuard(prompt_chars=300, caller="test_stream", model="gpt-4o")
    assert not guard.exceeded
    # Add small chunk
    guard.add("hello world")
    assert not guard.exceeded

    # Exceed limit
    huge_text = "x" * (HARD_CAP_TOTAL * 4)
    guard.add(huge_text)
    assert guard.exceeded
    guard.report()


def test_check_usage_authoritative():
    # Normal usage (not over cap -> returns False)
    usage = {"prompt_tokens": 100, "completion_tokens": 200, "total_tokens": 300}
    assert check_usage_authoritative(usage, est_prompt=100, caller="test_auth") is False

    # Violating usage (over cap -> returns True)
    violating_usage = {"prompt_tokens": 500_000, "completion_tokens": 600_000, "total_tokens": 1_100_000}
    assert check_usage_authoritative(violating_usage, est_prompt=500_000, caller="test_auth_viol") is True

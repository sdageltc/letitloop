"""Tests for Policy Gatekeeper — Sprint 6 exit gates (fast)."""

import pytest

pytestmark = pytest.mark.fast


def test_forbidden_file_blocked():
    """Modifying a forbidden file (e.g. .github/workflows/ci.yml) is blocked immediately."""
    from orchestrator.gate import evaluate_policy, load_policy

    policy = load_policy()
    # Default policy forbids .github/workflows/ci.yml
    result = evaluate_policy(file_path=".github/workflows/ci.yml", policy=policy)
    assert result["allowed"] is False
    assert any("forbidden" in v for v in result["violations"])

    # Also pattern should block any workflow
    result2 = evaluate_policy(file_path=".github/workflows/secret.yml", policy=policy)
    assert result2["allowed"] is False


def test_api_key_masked_in_wal():
    """Attempting to write an API key (e.g. sk-...) masks the token in the WAL."""
    from orchestrator.scrubber import scrub_text

    raw = "let me use sk-1234567890abcdefghijklmnop for openai and also ghp_123456789012345678901234567890123456 and AWS AKIAIOSFODNN7EXAMPLE"
    scrubbed = scrub_text(raw)
    assert "sk-123456" not in scrubbed
    assert "<secret:REDACTED" in scrubbed
    assert "AKIAIOSFODNN7EXAMPLE" not in scrubbed

    # High-entropy generic
    high = "aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456789ABCD1234"
    scrubbed2 = scrub_text(f"token={high}")
    assert high not in scrubbed2 or "<secret" in scrubbed2


def test_token_budget_exceeded():
    """Exceeding the token budget halts execution cleanly with BudgetExceededError."""
    from orchestrator.gate import BudgetExceededError, Policy

    policy = Policy(token_budget=1000)
    from orchestrator.gate import evaluate_policy

    # Within budget should pass
    result = evaluate_policy(tokens_used=500, policy=policy)
    assert result["allowed"] is True

    # Over budget should raise
    with pytest.raises(BudgetExceededError):
        evaluate_policy(tokens_used=2000, policy=policy)


def test_path_jailing_blocks_traversal():
    """Path jailing blocks directory traversal (.., symlink swaps)."""
    from orchestrator.gate import Policy, evaluate_policy

    policy = Policy(blocked_paths=["..", ".git/"])
    result = evaluate_policy(file_path="../outside.txt", policy=policy)
    assert result["allowed"] is False
    assert any("path_jail" in v for v in result["violations"])

    result2 = evaluate_policy(file_path=".git/config", policy=policy)
    assert result2["allowed"] is False


def test_gate_check_api(tmp_path):
    """gate_check evaluates multiple file_paths + diff + tokens."""
    from orchestrator.gate import gate_check

    # Create a temporary policy file with strict limits
    policy_data = {
        "max_lines_changed": 10,
        "forbidden_files": [".github/workflows/ci.yml"],
        "forbidden_patterns": [r".*\.key$"],
        "token_budget": 5000,
        "blocked_paths": [".."],
    }
    policy_path = tmp_path / "letitloop.policy.json"
    import json

    policy_path.write_text(json.dumps(policy_data), encoding="utf-8")

    # Passing case
    report = gate_check(
        file_paths=["orchestrator/cli.py"],
        diff_text="+ line1\n+ line2\n",
        tokens_used=100,
        policy_path=str(policy_path),
    )
    assert report["passed"] is True

    # Failing: forbidden file
    report2 = gate_check(file_paths=[".github/workflows/ci.yml"], policy_path=str(policy_path))
    assert report2["passed"] is False

    # Failing: too many lines
    big_diff = "\n".join([f"+ line{i}" for i in range(20)])
    report3 = gate_check(diff_text=big_diff, policy_path=str(policy_path))
    assert report3["passed"] is False


def test_scrubber_high_entropy_detection():
    """High-entropy strings are flagged and scrubbed."""
    from orchestrator.scrubber import is_high_entropy

    # High entropy base64-like string
    high = "s3cr3tHighEntropyStringWithMixedCase12345+/=AB"
    # Lower threshold for test
    assert is_high_entropy(high, threshold=3.5) is True

    # Low entropy should not be flagged
    assert is_high_entropy("hello world hello world", threshold=4.5) is False
    assert is_high_entropy("aaaaabbbbbccccc", threshold=4.5) is False

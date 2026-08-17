"""Tests for orchestrator/prompts.py — prompt builders."""

from orchestrator.prompts import (
    build_implementer_prompt,
    build_critic_prompt,
    summarize_acceptance,
    summarize_verifier_results,
)


class TestBuildImplementerPrompt:
    def test_includes_title_and_objective(self):
        prompt = build_implementer_prompt(
            title="Test Task",
            objective="Do the thing",
            output_paths=["scratch/out.py"],
            acceptance_summary="file exists, syntax valid",
        )
        assert "Test Task" in prompt
        assert "Do the thing" in prompt

    def test_includes_output_paths(self):
        prompt = build_implementer_prompt(
            title="T", objective="O",
            output_paths=["a.py", "b.py"],
            acceptance_summary="checks",
        )
        assert "a.py" in prompt
        assert "b.py" in prompt

    def test_includes_acceptance_summary(self):
        prompt = build_implementer_prompt(
            title="T", objective="O",
            output_paths=["out.py"],
            acceptance_summary="some checks here",
        )
        assert "some checks here" in prompt

    def test_includes_prior_failures(self):
        prompt = build_implementer_prompt(
            title="T", objective="O",
            output_paths=["out.py"],
            acceptance_summary="chk",
            prior_failures=[{"message": "syntax error on line 5"}],
            supervisor_attempt=2,
        )
        assert "PREVIOUS SUPERVISOR ATTEMPT FAILED" in prompt
        assert "syntax error" in prompt

    def test_prior_failures_omitted_on_first_attempt(self):
        prompt = build_implementer_prompt(
            title="T", objective="O",
            output_paths=["out.py"],
            acceptance_summary="chk",
            prior_failures=[{"message": "syntax error on line 5"}],
            supervisor_attempt=1,
        )
        assert "PREVIOUS SUPERVISOR ATTEMPT FAILED" not in prompt

    def test_includes_critic_feedback(self):
        prompt = build_implementer_prompt(
            title="T", objective="O",
            output_paths=["out.py"],
            acceptance_summary="chk",
            critic_feedback="missing error handling",
        )
        assert "CRITIC FEEDBACK" in prompt
        assert "missing error handling" in prompt

    def test_includes_verifier_feedback(self):
        prompt = build_implementer_prompt(
            title="T", objective="O",
            output_paths=["out.py"],
            acceptance_summary="chk",
            verifier_feedback="file not found",
        )
        assert "VERIFIER FEEDBACK" in prompt
        assert "file not found" in prompt

    def test_includes_turn_count(self):
        prompt = build_implementer_prompt(
            title="T", objective="O",
            output_paths=["out.py"],
            acceptance_summary="chk",
            current_turn=2, max_turns=3,
        )
        assert "TURN 2" in prompt
        assert "of 3" in prompt

    def enforces_json_output_schema(self):
        prompt = build_implementer_prompt(
            title="T", objective="O",
            output_paths=["out.py"],
            acceptance_summary="chk",
        )
        assert "OUTPUT SCHEMA" in prompt
        assert "path" in prompt
        assert "content" in prompt
        assert "Return ONLY valid JSON" in prompt


class TestBuildCriticPrompt:
    def test_includes_task_info(self):
        prompt = build_critic_prompt(
            title="Review Me",
            objective="Write a function",
            output_paths=["func.py"],
            acceptance_summary="syntax valid",
            artifact_summaries=[{"path": "func.py", "content": "def foo(): pass"}],
        )
        assert "Review Me" in prompt
        assert "Write a function" in prompt

    def test_includes_artifact_content(self):
        prompt = build_critic_prompt(
            title="T", objective="O",
            output_paths=["a.py"],
            acceptance_summary="chk",
            artifact_summaries=[{"path": "a.py", "content": "x = 1"}],
        )
        assert "--- a.py ---" in prompt
        assert "x = 1" in prompt

    def test_truncates_long_content(self):
        long = "x" * 3000
        prompt = build_critic_prompt(
            title="T", objective="O",
            output_paths=["big.py"],
            acceptance_summary="chk",
            artifact_summaries=[{"path": "big.py", "content": long}],
        )
        assert "[truncated" in prompt

    def test_includes_verifier_results(self):
        prompt = build_critic_prompt(
            title="T", objective="O",
            output_paths=["a.py"],
            acceptance_summary="chk",
            artifact_summaries=[{"path": "a.py", "content": "x"}],
            verifier_results=[{"check_id": "syntax", "passed": True, "message": "ok"}],
        )
        assert "DETERMINISTIC VERIFIER RESULTS" in prompt
        assert "[PASS]" in prompt

    def enforces_pass_fail_json_schema(self):
        prompt = build_critic_prompt(
            title="T", objective="O",
            output_paths=["a.py"],
            acceptance_summary="chk",
            artifact_summaries=[{"path": "a.py", "content": "x"}],
        )
        assert "PASS" in prompt
        assert "FAIL" in prompt
        assert "implementer_guidance" in prompt
        assert "Return ONLY valid JSON" in prompt


class TestSummarizeAcceptance:
    def test_generates_compact_summary(self):
        checks = [
            {"kind": "file_exists", "path": "out.py", "expected": True},
            {"kind": "syntax", "path": "out.py", "expected": "python"},
        ]
        result = summarize_acceptance(checks)
        assert "[file_exists]" in result
        assert "[syntax]" in result
        assert "out.py" in result


class TestSummarizeVerifierResults:
    def test_generates_compact_summary(self):
        results = [
            {"check_id": "s1", "passed": True, "message": "syntax ok"},
            {"check_id": "h1", "passed": False, "message": "placeholder detected"},
        ]
        result = summarize_verifier_results(results)
        assert "[PASS]" in result
        assert "[FAIL]" in result
        assert "syntax ok" in result
        assert "placeholder" in result

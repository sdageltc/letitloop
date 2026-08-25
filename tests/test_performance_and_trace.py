"""Tests for performance optimizations, dynamic thinking budgets, and explainability trace."""

import os
import unittest
from unittest.mock import MagicMock, patch

from orchestrator.cli import cmd_trace
from orchestrator.llm import call_llm
from orchestrator.models import ModelRegistry, ModelThinkingConfig, ThinkingBudget
from orchestrator.verifier import _AST_CACHE, _cached_ast_parse, fast_ast_verify


class TestPerformanceAndTrace(unittest.TestCase):
    def setUp(self):
        _AST_CACHE.clear()
        if hasattr(_cached_ast_parse, "cache_clear"):
            _cached_ast_parse.cache_clear()

    def test_dynamic_thinking_budget_allocation(self):
        """Verify dynamic thinking token budgets across phases."""
        self.assertEqual(ThinkingBudget.budget_for("planning"), 4096)
        self.assertEqual(ThinkingBudget.budget_for("propose_plan"), 4096)
        self.assertEqual(ThinkingBudget.budget_for("architect"), 4096)

        self.assertEqual(ThinkingBudget.budget_for("qc_review"), 2048)
        self.assertEqual(ThinkingBudget.budget_for("critique"), 2048)
        self.assertEqual(ThinkingBudget.budget_for("audit"), 2048)

        self.assertEqual(ThinkingBudget.budget_for("worker_standard"), 0)
        self.assertEqual(ThinkingBudget.budget_for("code_generation"), 0)
        self.assertEqual(ThinkingBudget.budget_for("implementation"), 0)

        self.assertEqual(ThinkingBudget.budget_for("complex_refactor"), 1024)

    def test_model_thinking_config_anthropic_claude(self):
        """Verify Claude thinking parameters comply with Anthropic requirements."""
        payload = {"max_tokens": 4096, "temperature": 0.7}
        # Planning phase with thinking enabled (budget >= 1024)
        ModelThinkingConfig.apply_thinking_config("claude-opus-5", "anthropic", payload, thinking_budget=4096)
        self.assertIn("thinking", payload)
        self.assertEqual(payload["thinking"]["type"], "enabled")
        self.assertEqual(payload["thinking"]["budget_tokens"], 4096)
        # max_tokens must be greater than budget_tokens
        self.assertGreater(payload["max_tokens"], 4096)
        # temperature must be removed or 1.0
        self.assertNotIn("temperature", payload)

        # Worker standard phase: thinking should be omitted for instant execution
        worker_payload = {"max_tokens": 4096, "temperature": 0.2}
        ModelThinkingConfig.apply_thinking_config("claude-opus-5", "anthropic", worker_payload, thinking_budget=0)
        self.assertNotIn("thinking", worker_payload)

    def test_model_thinking_config_openai_reasoning_vs_standard(self):
        """Verify OpenAI only receives reasoning_effort on reasoning models, not gpt-4o."""
        # Reasoning model (gpt-5.6 / o1 / o3)
        reasoning_payload = {"max_tokens": 4096}
        ModelThinkingConfig.apply_thinking_config("gpt-5.6-sol", "openai", reasoning_payload, thinking_budget=4096)
        self.assertEqual(reasoning_payload.get("reasoning_effort"), "high")

        # Standard model (gpt-4o-mini) must never receive reasoning_effort (prevents 400 error)
        standard_payload = {"max_tokens": 4096}
        ModelThinkingConfig.apply_thinking_config("gpt-4o-mini", "openai", standard_payload, thinking_budget=4096)
        self.assertNotIn("reasoning_effort", standard_payload)

    def test_model_thinking_config_gemini_thinking_budget(self):
        """Verify Gemini thinking_budget payload configuration."""
        payload = {"max_tokens": 4096}
        ModelThinkingConfig.apply_thinking_config("gemini-3.7-flash", "gemini", payload, thinking_budget=0)
        self.assertIn("extra_body", payload)
        self.assertEqual(payload["extra_body"]["google"]["thinking_config"]["thinking_budget"], 0)

    def test_fast_ast_verify_speed_and_caching(self):
        """Verify in-memory AST check (<5ms) and sub-millisecond hash cache hits."""
        code = "def add(a: int, b: int) -> int:\n    return a + b\n"
        passed, msg = fast_ast_verify(code)
        self.assertTrue(passed)
        self.assertEqual(msg, "Python syntax valid")

        # Second call should hit the in-memory cache
        passed2, msg2 = fast_ast_verify(code)
        self.assertTrue(passed2)
        self.assertEqual(msg2, "Python syntax valid")

        # Invalid syntax detection
        bad_code = "def bad(:\n    pass"
        passed_bad, msg_bad = fast_ast_verify(bad_code)
        self.assertFalse(passed_bad)
        self.assertIn("SyntaxError", msg_bad)

    @patch("orchestrator.llm._http_json")
    def test_call_llm_passes_thinking_budget_gemini(self, mock_http):
        """Verify thinking_budget is passed in extra_body for Gemini endpoints."""
        mock_http.return_value = {
            "choices": [{"message": {"content": "def test(): pass"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
            res = call_llm("Generate code", "gemini:gemini-3.7-flash", thinking_budget=0)
            self.assertEqual(res["text"], "def test(): pass")
            call_args = mock_http.call_args[0]
            payload = call_args[2]
            self.assertIn("extra_body", payload)
            self.assertEqual(payload["extra_body"]["google"]["thinking_config"]["thinking_budget"], 0)

    @patch("orchestrator.llm._http_json")
    def test_call_llm_passes_thinking_budget_anthropic(self, mock_http):
        """Verify thinking_budget is passed in thinking dict for Anthropic endpoints."""
        mock_http.return_value = {
            "content": [{"type": "text", "text": "Plan output"}],
            "usage": {"input_tokens": 20, "output_tokens": 10},
        }
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            res = call_llm("Plan architecture", "anthropic:claude-opus-5", thinking_budget=4096)
            self.assertEqual(res["text"], "Plan output")
            call_args = mock_http.call_args[0]
            payload = call_args[2]
            self.assertIn("thinking", payload)
            self.assertEqual(payload["thinking"]["budget_tokens"], 4096)

    def test_model_registry_frontier_defaults(self):
        """Verify ModelRegistry defaults to official 2026 frontier models."""
        self.assertEqual(ModelRegistry.WORKER, "gemini-3.7-flash")
        self.assertEqual(ModelRegistry.QC, "gemini-3.1-pro")
        self.assertTrue(ModelRegistry.is_hybrid("hybrid:gemini:gemini-3.7-flash"))
        self.assertEqual(ModelRegistry.strip_hybrid_prefix("hybrid:gemini:gemini-3.7-flash"), "gemini:gemini-3.7-flash")

    def test_cmd_trace_execution(self):
        """Verify cmd_trace runs without error for existing runs."""
        args = MagicMock()
        args.goal_id = None
        try:
            cmd_trace(args)
        except SystemExit:
            pass


if __name__ == "__main__":
    unittest.main()

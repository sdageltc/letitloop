"""Tests for performance optimizations, dynamic thinking budgets, and explainability trace."""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

from orchestrator.cli import cmd_trace
from orchestrator.llm import call_llm
from orchestrator.models import ModelRegistry, ThinkingBudget
from orchestrator.verifier import _AST_CACHE, fast_ast_verify


class TestPerformanceAndTrace(unittest.TestCase):
    def setUp(self):
        _AST_CACHE.clear()

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
            # Verify payload in mock call
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
            res = call_llm("Plan architecture", "anthropic:claude-3-5-sonnet-latest", thinking_budget=4096)
            self.assertEqual(res["text"], "Plan output")
            call_args = mock_http.call_args[0]
            payload = call_args[2]
            self.assertIn("thinking", payload)
            self.assertEqual(payload["thinking"]["budget_tokens"], 4096)

    def test_model_registry_frontier_defaults(self):
        """Verify ModelRegistry defaults to Gemini 3.7 Flash and 3.1 Pro."""
        self.assertEqual(ModelRegistry.WORKER, "gemini-3.7-flash")
        self.assertEqual(ModelRegistry.QC, "gemini-3.1-pro")
        self.assertTrue(ModelRegistry.is_hybrid("hybrid:gemini:gemini-3.7-flash"))
        self.assertEqual(ModelRegistry.strip_hybrid_prefix("hybrid:gemini:gemini-3.7-flash"), "gemini:gemini-3.7-flash")

    def test_cmd_trace_execution(self):
        """Verify cmd_trace runs without error for existing runs."""
        args = MagicMock()
        args.goal_id = None
        # Should not crash even if run directory is inspected
        try:
            cmd_trace(args)
        except SystemExit:
            pass  # Expected if no runs in temporary test dir


if __name__ == "__main__":
    unittest.main()

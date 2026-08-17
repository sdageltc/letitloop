"""Tests for worker adapter."""

import os
import tempfile
from unittest.mock import patch

import pytest

from orchestrator.contract import Contract
from orchestrator.llm import LLMError
from orchestrator.models import ModelRegistry
from orchestrator.worker import DEFAULT_MODEL, _build_brief, run_worker

pytestmark = pytest.mark.fast


def _make_contract(overrides=None):
    base = {
        "task_id": "worker-test",
        "title": "Worker test",
        "status": "drafted",
        "risk_tier": "auto",
        "workspace_scope": {"allow": ["scratch/test/"], "deny": ["AGENTS.md"]},
        "objective": "Create a test file",
        "worker": {"model": "openai:gpt-4o-mini", "max_attempts": 3},
        "inputs": [],
        "outputs": [{"path": "scratch/test/output.txt"}],
        "acceptance_checks": [{"id": "c1", "kind": "file_exists", "path": "scratch/test/output.txt", "expected": True}],
        "qc": {"required": False, "lens": "code_correctness"},
    }
    if overrides:
        base.update(overrides)
    return Contract(base)


def _ok_response(text="LLM_OUTPUT"):
    return {
        "text": text,
        "usage": None,
        "model": "gpt-4o-mini",
        "provider": "openai",
        "elapsed_sec": 0.01,
    }


class TestBuildBrief:
    def test_contains_objective(self):
        contract = _make_contract()
        brief = _build_brief(contract)
        assert "Worker test" in brief
        assert "Create a test file" in brief

    def test_contains_allowed_paths(self):
        contract = _make_contract()
        brief = _build_brief(contract)
        assert "scratch/test/" in brief

    def test_contains_denied_paths(self):
        contract = _make_contract()
        brief = _build_brief(contract)
        assert "AGENTS.md" in brief

    def test_contains_acceptance_criteria(self):
        contract = _make_contract()
        brief = _build_brief(contract)
        assert "file_exists" in brief

    def test_contains_constraints(self):
        contract = _make_contract()
        brief = _build_brief(contract)
        assert "CONSTRAINTS" in brief
        assert "Do NOT modify" in brief

    def test_contains_previous_failures(self):
        contract = _make_contract()
        previous = [{"message": "file not found: output.txt"}]
        brief = _build_brief(contract, previous_failures=previous)
        assert "PREVIOUS ATTEMPT FAILED" in brief
        assert "file not found" in brief

    def test_contains_changed_approach(self):
        contract = _make_contract()
        brief = _build_brief(contract, changed_approach="try a different method")
        assert "structurally different approach" in brief.lower()
        assert "different method" in brief


class TestRunWorker:
    def test_returns_result_keys(self):
        contract = _make_contract()
        with tempfile.TemporaryDirectory() as td:
            with patch("orchestrator.worker.call_llm", return_value=_ok_response()):
                result = run_worker(contract, td, td, timeout_sec=5)
            assert "success" in result
            assert "stdout" in result
            assert "stderr" in result
            assert "exit_code" in result
            assert "elapsed_sec" in result
            assert "artifact_paths" in result

    def test_writes_brief_file(self):
        contract = _make_contract()
        with tempfile.TemporaryDirectory() as td:
            with patch("orchestrator.worker.call_llm", return_value=_ok_response()):
                run_worker(contract, td, td, timeout_sec=5)
            brief_path = os.path.join(td, "worker_brief.txt")
            assert os.path.isfile(brief_path)

    def test_success_returns_llm_text(self):
        contract = _make_contract()
        with tempfile.TemporaryDirectory() as td:
            with patch("orchestrator.worker.call_llm", return_value=_ok_response("hello world")) as llm_mock:
                result = run_worker(contract, td, td, timeout_sec=5)
        assert result["success"] is True
        assert result["stdout"] == "hello world"
        assert result["exit_code"] == 0
        assert result["provider"] == "openai:gpt-4o-mini"
        brief_arg = llm_mock.call_args.args[0]
        assert "Worker test" in brief_arg
        assert llm_mock.call_args.args[1] == "openai:gpt-4o-mini"

    def test_default_model_fallback_constant(self):
        assert DEFAULT_MODEL == ModelRegistry.FALLBACK


class TestProviderFallback:
    """Provider fallback: primary LLM fails -> backup provider, and vice versa."""

    def test_primary_failure_falls_back_to_backup(self):
        contract = _make_contract()
        calls = []

        def side_effect(prompt, model, **kwargs):
            calls.append(model)
            if calls[-1] == ModelRegistry.WORKER_PREFIXED:
                return _ok_response("backup output")
            raise LLMError("provider down")

        with tempfile.TemporaryDirectory() as td:
            with patch("orchestrator.worker.call_llm", side_effect=side_effect):
                result = run_worker(contract, td, td, timeout_sec=5)

        assert result["success"] is True
        assert result["fallback_used"] is True
        assert result["fallback_from"] == ModelRegistry.FALLBACK
        assert result["fallback_to"] == ModelRegistry.WORKER_PREFIXED
        assert result["provider"] == ModelRegistry.WORKER_PREFIXED
        assert "backup output" in result["stdout"]
        assert len(calls) == 2

    def test_worker_primary_failure_falls_back_to_default(self):
        contract = _make_contract({"worker": {"model": ModelRegistry.WORKER_PREFIXED, "max_attempts": 3}})
        calls = []

        def side_effect(prompt, model, **kwargs):
            calls.append(model)
            if calls[-1] == ModelRegistry.FALLBACK:
                return _ok_response("default fallback output")
            raise LLMError("provider down")

        with tempfile.TemporaryDirectory() as td:
            with patch("orchestrator.worker.call_llm", side_effect=side_effect):
                result = run_worker(contract, td, td, timeout_sec=5)

        assert result["success"] is True
        assert result["fallback_used"] is True
        assert result["fallback_from"] == ModelRegistry.WORKER_PREFIXED
        assert result["fallback_to"] == ModelRegistry.FALLBACK
        assert "default fallback output" in result["stdout"]
        assert len(calls) == 2

    def test_no_fallback_on_success(self):
        contract = _make_contract()
        with tempfile.TemporaryDirectory() as td:
            with patch("orchestrator.worker.call_llm", return_value=_ok_response("ok")) as llm_mock:
                result = run_worker(contract, td, td, timeout_sec=5)
        assert result["success"] is True
        assert result.get("fallback_used") is None
        assert llm_mock.call_count == 1

    def test_no_fallback_when_backup_also_fails(self):
        contract = _make_contract()
        with tempfile.TemporaryDirectory() as td:
            with patch("orchestrator.worker.call_llm", side_effect=LLMError("both down")) as llm_mock:
                result = run_worker(contract, td, td, timeout_sec=5)
        assert result["success"] is False
        assert result["fallback_used"] is True
        assert result["fallback_to"] == ModelRegistry.WORKER_PREFIXED
        assert llm_mock.call_count == 2

    def test_contract_fallback_model_override(self):
        contract = _make_contract(
            {
                "worker": {
                    "model": "openai:gpt-4o-mini",
                    "fallback_model": "anthropic:claude-opus-5",
                    "max_attempts": 3,
                },
            }
        )

        def side_effect(prompt, model, **kwargs):
            if model == "anthropic:claude-opus-5":
                return _ok_response("custom fallback")
            raise LLMError("primary down")

        with tempfile.TemporaryDirectory() as td:
            with patch("orchestrator.worker.call_llm", side_effect=side_effect):
                result = run_worker(contract, td, td, timeout_sec=5)
        assert result["success"] is True
        assert result["fallback_to"] == "anthropic:claude-opus-5"

    def test_no_fallback_env_kill_switch(self, monkeypatch):
        monkeypatch.setenv("WORKER_NO_FALLBACK", "1")
        contract = _make_contract()
        with tempfile.TemporaryDirectory() as td:
            with patch("orchestrator.worker.call_llm", side_effect=LLMError("down")) as llm_mock:
                result = run_worker(contract, td, td, timeout_sec=5)
        assert result["success"] is False
        assert result.get("fallback_used") is None
        assert llm_mock.call_count == 1

    def test_hybrid_worker_no_auto_fallback(self):
        contract = _make_contract({"worker": {"model": "hybrid:gemini:gemini-3.6-flash", "max_attempts": 3}})
        with tempfile.TemporaryDirectory() as td:
            with patch("orchestrator.hybrid_worker.run_hybrid_worker") as hybrid_mock:
                hybrid_mock.return_value = {
                    "success": False,
                    "stdout": "",
                    "stderr": "hybrid failed",
                    "exit_code": 2,
                    "elapsed_sec": 1.0,
                    "artifact_paths": [],
                }
                result = run_worker(contract, td, td, timeout_sec=5)
        assert result["success"] is False
        assert result.get("fallback_used") is None
        hybrid_mock.assert_called_once()

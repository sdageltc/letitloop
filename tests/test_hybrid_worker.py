"""Tests for hybrid worker module and its integration with worker routing."""

import json
from unittest.mock import patch

import pytest

from orchestrator.contract import Contract
from orchestrator.worker import run_worker

pytestmark = pytest.mark.fast


def _make_contract(
    task_id="hy1",
    model="hybrid:local",
    output_path=None,
    max_attempts=1,
    extra_worker=None,
    scope_allow=None,
    scope_deny=None,
):
    if output_path is None:
        output_path = "scratch/test_hybrid/output.txt"

    allow_paths = scope_allow if scope_allow is not None else ["scratch/test_hybrid/"]
    deny_paths = scope_deny if scope_deny is not None else ["AGENTS.md", "memory/"]

    worker_dict = {"model": model, "max_attempts": max_attempts}
    if extra_worker:
        worker_dict.update(extra_worker)
    if "hybrid_profile" not in worker_dict:
        worker_dict["hybrid_profile"] = "success"

    raw = {
        "task_id": task_id,
        "title": f"Test Task {task_id}",
        "status": "drafted",
        "risk_tier": "auto",
        "workspace_scope": {
            "allow": allow_paths,
            "deny": deny_paths,
        },
        "objective": "Test hybrid worker inner loop",
        "worker": worker_dict,
        "inputs": [],
        "outputs": [{"path": output_path}],
        "acceptance_checks": [{"id": "c1", "kind": "file_exists", "path": output_path}],
        "qc": {"required": False, "lens": "code_correctness"},
    }
    return Contract(raw)


def test_hybrid_routes_from_run_worker(tmp_path):
    """Ensure run_worker routes to run_hybrid_worker when model starts with hybrid:."""
    contract = _make_contract(model="hybrid:local")
    sentinel_result = {
        "success": True,
        "stdout": "sentinel",
        "stderr": "",
        "exit_code": 0,
        "elapsed_sec": 0.1,
        "artifact_paths": [],
        "sentinel": "hybrid_routed",
    }
    with patch("orchestrator.hybrid_worker.run_hybrid_worker", return_value=sentinel_result) as mock_hybrid:
        res = run_worker(contract, str(tmp_path), str(tmp_path))
        assert mock_hybrid.called
        assert res.get("sentinel") == "hybrid_routed"


def test_non_hybrid_worker_unchanged(tmp_path):
    """Ensure non-hybrid worker models use standard execution path."""
    contract = _make_contract(model="openai:gpt-4o-mini")
    response = {
        "text": "normal worker stdout",
        "usage": None,
        "model": "gpt-4o-mini",
        "provider": "openai",
        "elapsed_sec": 0.01,
    }

    with (
        patch("orchestrator.worker.call_llm", return_value=response) as mock_llm,
        patch("orchestrator.hybrid_worker.run_hybrid_worker") as mock_hybrid,
    ):
        res = run_worker(contract, str(tmp_path), str(tmp_path))
        assert not mock_hybrid.called
        assert mock_llm.called
        assert res["success"] is True
        assert res["stdout"] == "normal worker stdout"


def test_hybrid_success_path(tmp_path):
    """Ensure real run_worker executes hybrid worker successfully to exit_code 0."""
    contract = _make_contract(model="hybrid:local", output_path="scratch/test_hybrid/output.txt")
    res = run_worker(contract, str(tmp_path), str(tmp_path))

    assert res["success"] is True
    assert res["exit_code"] == 0
    output_file = tmp_path / "scratch" / "test_hybrid" / "output.txt"
    assert output_file.exists()
    assert "HYBRID_OUTPUT" in output_file.read_text(encoding="utf-8")


def test_hybrid_respects_max_turns(tmp_path):
    """Ensure hybrid worker halts when turns exceed hybrid_max_turns under budget_exhausted profile."""
    contract = _make_contract(
        model="hybrid:local",
        extra_worker={"hybrid_max_turns": 1, "hybrid_profile": "budget_exhausted"},
    )
    res = run_worker(contract, str(tmp_path), str(tmp_path))

    assert res["success"] is False
    assert res["exit_code"] != 0
    assert res["turns"] <= 2
    assert "budget exhausted" in res["stderr"].lower()


def test_hybrid_repair_cycle(tmp_path):
    """Ensure repair_then_success profile performs at least one repair cycle before success."""
    contract = _make_contract(
        model="hybrid:local",
        extra_worker={"hybrid_profile": "repair_then_success"},
    )
    res = run_worker(contract, str(tmp_path), str(tmp_path))

    assert res["success"] is True
    assert res["exit_code"] == 0

    trace_file = tmp_path / "hybrid_trace.json"
    assert trace_file.exists()

    with open(trace_file, "r", encoding="utf-8") as f:
        trace_data = json.load(f)

    repair_actions = [step for step in trace_data if step.get("action") == "repair_outputs"]
    assert len(repair_actions) >= 1


def test_hybrid_trace_written(tmp_path):
    """Ensure hybrid_trace.json is created and contains a valid list of step dicts."""
    contract = _make_contract(model="hybrid:local")
    res = run_worker(contract, str(tmp_path), str(tmp_path))

    assert res["success"] is True
    trace_file = tmp_path / "hybrid_trace.json"
    assert trace_file.exists()

    with open(trace_file, "r", encoding="utf-8") as f:
        trace_data = json.load(f)

    assert isinstance(trace_data, list)
    assert len(trace_data) > 0
    assert "turn" in trace_data[0]
    assert "role" in trace_data[0]


def test_hybrid_scope_violation_fails(tmp_path):
    """Ensure hybrid worker fails cleanly when output path violates workspace scope."""
    contract = _make_contract(
        model="hybrid:local",
        output_path="forbidden_dir/secret.txt",
        scope_allow=["scratch/test_hybrid/"],
    )
    res = run_worker(contract, str(tmp_path), str(tmp_path))

    assert res["success"] is False
    assert res["exit_code"] != 0

    trace_file = tmp_path / "hybrid_trace.json"
    assert trace_file.exists()
    with open(trace_file, "r", encoding="utf-8") as f:
        trace_data = json.load(f)
    assert any("scope_violation" in step.get("message", "") for step in trace_data)


def test_hybrid_fake_worker_isolated(tmp_path, monkeypatch):
    """Ensure FAKE_WORKER env var bypasses hybrid worker logic and uses fake worker path."""
    monkeypatch.setenv("FAKE_WORKER", "1")
    contract = _make_contract(model="hybrid:local")
    res = run_worker(contract, str(tmp_path), str(tmp_path))

    assert res["success"] is True
    assert res["stdout"] == "fake worker output"
    assert res["elapsed_sec"] == 0.01
    assert not (tmp_path / "hybrid_trace.json").exists()


# ---------------------------------------------------------------
# LLM loop tests (mocked _call_llm)
# ---------------------------------------------------------------


def _make_llm_contract(
    model="hybrid:test-model",
    output_path="scratch/test_llm/output.txt",
    extra_worker=None,
    scope_allow=None,
    acceptance_checks=None,
):
    """Helper for LLM loop tests — no hybrid_profile set."""
    allow_paths = scope_allow if scope_allow is not None else ["scratch/test_llm/"]
    worker_dict = {"model": model, "max_attempts": 3}
    if extra_worker:
        worker_dict.update(extra_worker)
    if acceptance_checks is None:
        acceptance_checks = [
            {"id": "c1", "kind": "file_exists", "path": output_path},
        ]
    raw = {
        "task_id": "llm-test",
        "title": "LLM loop test",
        "status": "drafted",
        "risk_tier": "auto",
        "workspace_scope": {"allow": allow_paths, "deny": ["AGENTS.md", "memory/"]},
        "objective": "Test LLM hybrid loop",
        "worker": worker_dict,
        "inputs": [],
        "outputs": [{"path": output_path}],
        "acceptance_checks": acceptance_checks,
        "qc": {"required": False, "lens": "code_correctness"},
    }
    return Contract(raw)


def _mock_llm_result(ok=True, raw='[{"path": "scratch/test_llm/output.txt", "content": "hello world"}]', stderr=""):
    """Create a mock _call_llm return value."""
    return {
        "ok": ok,
        "raw": raw,
        "stderr": stderr,
        "exit_code": 0 if ok else 1,
        "prompt_tokens": 100,
        "completion_tokens": 50,
    }


def _mock_critic_pass():
    return _mock_llm_result(
        raw=json.dumps({"status": "PASS", "summary": "ok", "issues": [], "implementer_guidance": ""})
    )


def _mock_critic_fail(guidance="needs work"):
    return _mock_llm_result(
        raw=json.dumps(
            {
                "status": "FAIL",
                "summary": "bad",
                "issues": [
                    {
                        "severity": "MAJOR",
                        "location": "output.txt",
                        "description": "issue",
                        "suggested_remediation": guidance,
                    }
                ],
                "implementer_guidance": guidance,
            }
        )
    )


def test_llm_success_path(tmp_path, monkeypatch):
    """Happy path: Implementer succeeds, Critic passes, Verifier passes."""
    calls = iter([_mock_llm_result(), _mock_critic_pass()])
    monkeypatch.setattr("orchestrator.hybrid_worker._call_llm", lambda *a, **kw: next(calls))
    contract = _make_llm_contract()
    res = run_worker(contract, str(tmp_path), str(tmp_path), timeout_sec=30)
    assert res["success"] is True
    assert res["exit_code"] == 0
    trace_file = tmp_path / "hybrid_trace.json"
    assert trace_file.exists()
    with open(trace_file, encoding="utf-8") as f:
        trace = json.load(f)
    steps = [(s.get("role", ""), s.get("action", ""), s.get("status", "")) for s in trace]
    assert any(r == "Implementer" for r, a, st in steps), f"no Implementer in trace: {steps}"
    assert any(r == "Critic" for r, a, st in steps), f"no Critic in trace: {steps}"
    assert any(r == "Critic" for r, a, st in steps), f"no Critic in trace: {steps}"
    output_file = tmp_path / "scratch" / "test_llm" / "output.txt"
    assert output_file.exists()
    assert output_file.read_text(encoding="utf-8") == "hello world"


def test_llm_parse_fallback(tmp_path, monkeypatch):
    """Parser falls back through tiers and produces valid output."""
    raw = "```\nresult content\n```"
    calls = iter([_mock_llm_result(raw=raw), _mock_critic_pass()])
    monkeypatch.setattr("orchestrator.hybrid_worker._call_llm", lambda *a, **kw: next(calls))
    contract = _make_llm_contract()
    res = run_worker(contract, str(tmp_path), str(tmp_path), timeout_sec=30)
    assert res["success"] is True
    output_file = tmp_path / "scratch" / "test_llm" / "output.txt"
    assert output_file.exists()
    assert "result content" in output_file.read_text(encoding="utf-8")


def test_llm_parse_failure_retry(tmp_path, monkeypatch):
    """Parse failure triggers retry, then success on second attempt."""
    bad_result = _mock_llm_result(raw="")  # empty → parse failure
    good_result = _mock_llm_result()
    calls = iter([bad_result, good_result, _mock_critic_pass()])
    monkeypatch.setattr("orchestrator.hybrid_worker._call_llm", lambda *a, **kw: next(calls))
    contract = _make_llm_contract()
    res = run_worker(contract, str(tmp_path), str(tmp_path), timeout_sec=30)
    assert res["success"] is True
    trace_file = tmp_path / "hybrid_trace.json"
    with open(trace_file, encoding="utf-8") as f:
        trace = json.load(f)
    parse_fails = [s for s in trace if s.get("action") == "parse_output" and s.get("status") == "fail"]
    assert len(parse_fails) >= 1


def test_llm_critic_reject_then_repair(tmp_path, monkeypatch):
    """Critic rejects first attempt, Implementer fixes, Critic passes."""
    calls = iter(
        [
            _mock_llm_result(),
            _mock_critic_fail(guidance="add error handling"),
            _mock_llm_result(raw='[{"path": "scratch/test_llm/output.txt", "content": "fixed version"}]'),
            _mock_critic_pass(),
        ]
    )
    monkeypatch.setattr("orchestrator.hybrid_worker._call_llm", lambda *a, **kw: next(calls))
    contract = _make_llm_contract()
    res = run_worker(contract, str(tmp_path), str(tmp_path), timeout_sec=30)
    assert res["success"] is True
    output_file = tmp_path / "scratch" / "test_llm" / "output.txt"
    assert output_file.exists()
    assert "fixed version" in output_file.read_text(encoding="utf-8")


def test_llm_turn_budget_exhaustion(tmp_path, monkeypatch):
    """Turn budget exhausted after max_turns with persistent critic failure."""
    calls = iter(
        [
            _mock_llm_result(raw='[{"path": "scratch/test_llm/output.txt", "content": "v1"}]'),
            _mock_critic_fail(guidance="fix1"),
            _mock_llm_result(raw='[{"path": "scratch/test_llm/output.txt", "content": "v2"}]'),
            _mock_critic_fail(guidance="fix2"),
            _mock_llm_result(raw='[{"path": "scratch/test_llm/output.txt", "content": "v3"}]'),
            _mock_critic_fail(guidance="fix3"),
        ]
    )
    monkeypatch.setattr("orchestrator.hybrid_worker._call_llm", lambda *a, **kw: next(calls))
    contract = _make_llm_contract(
        extra_worker={"hybrid_max_turns": 3, "hybrid_repair_budget": 3, "hybrid_max_identical_verdicts": 10}
    )
    res = run_worker(contract, str(tmp_path), str(tmp_path), timeout_sec=30)
    assert res["success"] is False
    assert "turn budget" in res["stderr"].lower()


def test_llm_scope_violation(tmp_path, monkeypatch):
    """LLM loop detects scope violation when artifact path is out of bounds."""
    result = _mock_llm_result(raw='[{"path": "scratch/test_llm/output.txt", "content": "hello"}]')
    monkeypatch.setattr("orchestrator.hybrid_worker._call_llm", lambda *a, **kw: result)
    contract = _make_llm_contract(
        output_path="scratch/evil/output.txt",
        scope_allow=["scratch/test_llm/"],
    )
    res = run_worker(contract, str(tmp_path), str(tmp_path), timeout_sec=30)
    assert res["success"] is False


def test_llm_repair_budget_exhausted(tmp_path, monkeypatch):
    """Repair budget exhausted when critic keeps rejecting."""
    calls = iter(
        [
            _mock_llm_result(raw='[{"path": "scratch/test_llm/output.txt", "content": "v1"}]'),
            _mock_critic_fail(guidance="fix"),
            _mock_llm_result(raw='[{"path": "scratch/test_llm/output.txt", "content": "v2"}]'),
            _mock_critic_fail(guidance="fix again"),
        ]
    )
    monkeypatch.setattr("orchestrator.hybrid_worker._call_llm", lambda *a, **kw: next(calls))
    contract = _make_llm_contract(extra_worker={"hybrid_repair_budget": 1, "hybrid_max_identical_verdicts": 10})
    res = run_worker(contract, str(tmp_path), str(tmp_path), timeout_sec=30)
    assert res["success"] is False
    assert "repair budget" in res["stderr"].lower()


def test_llm_stuck_detection_identical_output(tmp_path, monkeypatch):
    """Stuck detector catches repeated identical output from Implementer."""
    result = _mock_llm_result()
    monkeypatch.setattr("orchestrator.hybrid_worker._call_llm", lambda *a, **kw: result)
    contract = _make_llm_contract(extra_worker={"hybrid_max_identical_outputs": 1})
    res = run_worker(contract, str(tmp_path), str(tmp_path), timeout_sec=30)
    assert res["success"] is False
    assert "stuck" in res["stderr"].lower()


def test_llm_trace_has_budget_info(tmp_path, monkeypatch):
    """LLM loop trace includes budget snapshots."""
    calls = iter([_mock_llm_result(), _mock_critic_pass()])
    monkeypatch.setattr("orchestrator.hybrid_worker._call_llm", lambda *a, **kw: next(calls))
    contract = _make_llm_contract()
    res = run_worker(contract, str(tmp_path), str(tmp_path), timeout_sec=30)
    assert res["success"] is True
    trace_file = tmp_path / "hybrid_trace.json"
    with open(trace_file, encoding="utf-8") as f:
        trace = json.load(f)
    steps_with_budget = [s for s in trace if "budget" in s]
    assert len(steps_with_budget) > 0


def test_guard_paid_model_enforcement():
    """Verify _guard_paid_model refuses bare prompts to expensive models."""
    from orchestrator.hybrid_worker import _guard_paid_model

    # Free/local models pass freely
    _guard_paid_model("local:mistral", "short prompt")

    # Paid model with bare prompt fails closed
    with pytest.raises(RuntimeError) as exc_info:
        _guard_paid_model("anthropic:claude-3-opus", "bare prompt without marker")
    assert "requires a prepared master-context" in str(exc_info.value)

    # Paid model with marker passes
    _guard_paid_model("anthropic:claude-3-opus", "Prompt with [CONTEXT_COMPLETE] attached")

"""Deterministic QC path tests using FAKE_QC env var — no live provider calls."""

import json
import os
import pytest

from orchestrator.goal import Goal, Plan
from orchestrator.supervisor import Supervisor
from orchestrator.state import load_state
from orchestrator.exceptions import PlannerError


@pytest.fixture(autouse=True)
def prevent_real_llm_calls(monkeypatch):
    def mock_planner(*args, **kwargs):
        raise PlannerError("Real LLM disabled in tests")
    monkeypatch.setattr("orchestrator.generator.decompose_goal", mock_planner)


def _make_contract_dict(task_id, output_path, max_attempts=1, qc_required=True,
                        check_kind="file_exists", check_expected=None):
    checks = [{"id": f"check-{check_kind}", "kind": check_kind, "path": output_path}]
    if check_expected is not None:
        checks[0]["expected"] = check_expected
    return {
        "task_id": task_id,
        "title": f"QC Test {task_id}",
        "status": "drafted",
        "risk_tier": "auto",
        "workspace_scope": {"allow": ["scratch/test-qc/"], "deny": []},
        "objective": f"Test QC path: {task_id}",
        "worker": {"model": "openai:gpt-4o-mini", "max_attempts": max_attempts},
        "inputs": [],
        "outputs": [{"path": output_path}],
        "acceptance_checks": checks,
        "qc": {"required": qc_required, "lens": "code_correctness"},
    }


def _run_supervisor(tmp_path, task_id, output_path, contract_dict, monkeypatch):
    ws_dir = str(tmp_path)
    run_dir = os.path.join(ws_dir, "scratch", "runs")
    goal = Goal(goal_id=f"qc-test-{task_id}", title=f"QC Test {task_id}", description="")
    plan = Plan(goal_id=goal.goal_id, contracts=[{"task_id": task_id, "contract": contract_dict}])
    supervisor = Supervisor(goal, plan, workspace_root=ws_dir, run_dir=run_dir)
    res = supervisor.execute_plan()
    state_file = os.path.join(run_dir, task_id, "state.json")
    state = load_state(state_file) if os.path.isfile(state_file) else None
    qc_path = os.path.join(run_dir, task_id, "qc_verdict.json")
    qc_verdict = None
    if os.path.isfile(qc_path):
        with open(qc_path) as f:
            qc_verdict = json.load(f)
    return ws_dir, run_dir, res, state, qc_verdict


class TestQcPass:
    def test_qc_pass_completes(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FAKE_WORKER", "1")
        monkeypatch.setenv("FAKE_QC", "PASS")
        task_id = "qc-pass-1"
        output_path = "scratch/test-qc/pass.txt"
        contract = _make_contract_dict(task_id, output_path)
        _, _, res, state, qc_v = _run_supervisor(tmp_path, task_id, output_path, contract, monkeypatch)
        assert res.get(task_id) in ("COMPLETE", "complete")
        assert qc_v is not None
        assert qc_v["status"] == "PASS"
        assert qc_v["passed"] is True


class TestQcReject:
    def test_qc_reject_escalates(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FAKE_WORKER", "1")
        monkeypatch.setenv("FAKE_QC", "REJECT")
        task_id = "qc-reject-1"
        output_path = "scratch/test-qc/reject.txt"
        contract = _make_contract_dict(task_id, output_path, max_attempts=1)
        _, _, res, state, qc_v = _run_supervisor(tmp_path, task_id, output_path, contract, monkeypatch)
        assert res.get(task_id) not in ("COMPLETE", "complete")
        assert qc_v is not None
        assert qc_v["status"] == "REJECT"
        assert state is None or state.status not in ("COMPLETE", "complete", "QC_PASSED")

    def test_qc_reject_with_retry(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FAKE_WORKER", "1")
        monkeypatch.setenv("FAKE_QC", "REJECT")
        task_id = "qc-reject-retry-1"
        output_path = "scratch/test-qc/reject-retry.txt"
        contract = _make_contract_dict(task_id, output_path, max_attempts=2)
        ws_dir = str(tmp_path)
        run_dir = os.path.join(ws_dir, "scratch", "runs")
        goal = Goal(goal_id="qc-reject-retry-goal", title="QC Reject Retry", description="")
        plan = Plan(goal_id=goal.goal_id, contracts=[{"task_id": task_id, "contract": contract}])
        supervisor = Supervisor(goal, plan, workspace_root=ws_dir, run_dir=run_dir)
        res = supervisor.execute_plan()
        assert res.get(task_id) not in ("COMPLETE", "complete")
        state_file = os.path.join(run_dir, task_id, "state.json")
        assert os.path.isfile(state_file)
        state = load_state(state_file)
        assert state.attempt >= 2
        assert state.status in ("ESCALATED", "QC_REJECTED")


class TestQcInsufficientEvidence:
    def test_insufficient_evidence_never_completes(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FAKE_WORKER", "1")
        monkeypatch.setenv("FAKE_QC", "INSUFFICIENT_EVIDENCE")
        task_id = "qc-ie-1"
        output_path = "scratch/test-qc/ie.txt"
        contract = _make_contract_dict(task_id, output_path, max_attempts=1)
        _, _, res, state, qc_v = _run_supervisor(tmp_path, task_id, output_path, contract, monkeypatch)
        assert res.get(task_id) not in ("COMPLETE", "complete")
        assert qc_v is not None
        assert qc_v["status"] == "INSUFFICIENT_EVIDENCE"
        if state:
            assert state.status not in ("COMPLETE", "complete", "QC_PASSED")

    def test_insufficient_evidence_then_retry(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FAKE_WORKER", "1")
        monkeypatch.setenv("FAKE_QC", "INSUFFICIENT_EVIDENCE")
        task_id = "qc-ie-retry-1"
        output_path = "scratch/test-qc/ie-retry.txt"
        contract = _make_contract_dict(task_id, output_path, max_attempts=3)
        ws_dir = str(tmp_path)
        run_dir = os.path.join(ws_dir, "scratch", "runs")
        goal = Goal(goal_id="qc-ie-retry-goal", title="QC IE Retry", description="")
        plan = Plan(goal_id=goal.goal_id, contracts=[{"task_id": task_id, "contract": contract}])
        supervisor = Supervisor(goal, plan, workspace_root=ws_dir, run_dir=run_dir)
        res = supervisor.execute_plan()
        assert res.get(task_id) not in ("COMPLETE", "complete")
        state_file = os.path.join(run_dir, task_id, "state.json")
        assert os.path.isfile(state_file)
        state = load_state(state_file)
        assert state.attempt >= 2


class TestQcError:
    def test_qc_error_never_completes(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FAKE_WORKER", "1")
        monkeypatch.setenv("FAKE_QC", "ERROR")
        task_id = "qc-error-1"
        output_path = "scratch/test-qc/error.txt"
        contract = _make_contract_dict(task_id, output_path, max_attempts=1)
        _, _, res, state, qc_v = _run_supervisor(tmp_path, task_id, output_path, contract, monkeypatch)
        assert res.get(task_id) not in ("COMPLETE", "complete")
        assert qc_v is not None
        assert qc_v["status"] == "ERROR"
        if state:
            assert state.status not in ("COMPLETE", "complete", "QC_PASSED")

    def test_qc_error_with_retry(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FAKE_WORKER", "1")
        monkeypatch.setenv("FAKE_QC", "ERROR")
        task_id = "qc-error-retry-1"
        output_path = "scratch/test-qc/error-retry.txt"
        contract = _make_contract_dict(task_id, output_path, max_attempts=2)
        ws_dir = str(tmp_path)
        run_dir = os.path.join(ws_dir, "scratch", "runs")
        goal = Goal(goal_id="qc-error-retry-goal", title="QC Error Retry", description="")
        plan = Plan(goal_id=goal.goal_id, contracts=[{"task_id": task_id, "contract": contract}])
        supervisor = Supervisor(goal, plan, workspace_root=ws_dir, run_dir=run_dir)
        res = supervisor.execute_plan()
        assert res.get(task_id) not in ("COMPLETE", "complete")
        state_file = os.path.join(run_dir, task_id, "state.json")
        assert os.path.isfile(state_file)
        state = load_state(state_file)
        assert state.attempt >= 2


class TestQcMalformed:
    def test_qc_malformed_is_safe(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FAKE_WORKER", "1")
        monkeypatch.setenv("FAKE_QC", "MALFORMED")
        task_id = "qc-malformed-1"
        output_path = "scratch/test-qc/malformed.txt"
        contract = _make_contract_dict(task_id, output_path, max_attempts=1)
        _, _, res, state, qc_v = _run_supervisor(tmp_path, task_id, output_path, contract, monkeypatch)
        assert res.get(task_id) not in ("COMPLETE", "complete")
        assert qc_v is not None
        assert qc_v["status"] == "ERROR"
        if state:
            assert state.status not in ("COMPLETE", "complete", "QC_PASSED")


class TestQcSkip:
    def test_qc_not_called_when_verification_fails(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FAKE_WORKER", "FAIL")
        monkeypatch.setenv("FAKE_QC", "PASS")
        task_id = "qc-skip-1"
        output_path = "scratch/test-qc/skip.txt"
        contract = _make_contract_dict(task_id, output_path, check_kind="min_size", check_expected=50)
        _, run_dir, res, state, qc_v = _run_supervisor(tmp_path, task_id, output_path, contract, monkeypatch)
        assert res.get(task_id) not in ("COMPLETE", "complete")
        assert qc_v is None, "QC verdict should not exist when verification fails"

    def test_qc_not_required_skips_qc(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FAKE_WORKER", "1")
        monkeypatch.setenv("FAKE_QC", "REJECT")
        task_id = "qc-notreq-1"
        output_path = "scratch/test-qc/not-required.txt"
        contract = _make_contract_dict(task_id, output_path, qc_required=False)
        _, run_dir, res, state, qc_v = _run_supervisor(tmp_path, task_id, output_path, contract, monkeypatch)
        assert res.get(task_id) in ("COMPLETE", "complete")
        assert qc_v is None, "QC verdict should not exist when qc.required=False"


class TestQcPolicy:
    def test_requires_semantic_qc_checks(self):
        """Verify requires_semantic_qc for various risk/output/check combinations."""
        from orchestrator.contract import requires_semantic_qc
        assert requires_semantic_qc("qc_required", [{"path": "scratch/x.txt"}], []) is True
        assert requires_semantic_qc("human_required", [{"path": "scratch/x.txt"}], []) is True
        assert requires_semantic_qc("auto", [{"path": "scratch/x.txt"}], [{"kind": "file_exists"}]) is False
        assert requires_semantic_qc("auto", [{"path": "scratch/x.txt"}, {"path": "scratch/y.txt"}], []) is True
        assert requires_semantic_qc("auto", [{"path": "src/code.py"}], [{"kind": "file_exists"}]) is True
        assert requires_semantic_qc("auto", [{"path": "tests/test_x.py"}], [{"kind": "file_exists"}]) is True

    def test_requires_semantic_qc_policy_matrix(self):
        """Decision table for requires_semantic_qc."""
        from orchestrator.contract import requires_semantic_qc
        # Always QC: high risk tiers
        assert requires_semantic_qc("qc_required", [{"path": "scratch/x.txt"}], [{"kind": "file_exists"}]) is True
        assert requires_semantic_qc("human_required", [{"path": "scratch/x.txt"}], [{"kind": "file_exists"}]) is True
        # Always QC: src/ paths
        assert requires_semantic_qc("auto", [{"path": "src/module.py"}], [{"kind": "file_exists"}]) is True
        # Always QC: tests/ paths
        assert requires_semantic_qc("auto", [{"path": "tests/test_module.py"}], [{"kind": "file_exists"}]) is True
        # Always QC: Windows paths normalized
        assert requires_semantic_qc("auto", [{"path": "src\\module.py"}], [{"kind": "file_exists"}]) is True
        assert requires_semantic_qc("auto", [{"path": "tests\\test_module.py"}], [{"kind": "file_exists"}]) is True
        # Always QC: multiple outputs
        assert requires_semantic_qc("auto", [{"path": "scratch/a.txt"}, {"path": "scratch/b.txt"}], []) is True
        # Always QC: required_sections check
        assert requires_semantic_qc("auto", [{"path": "scratch/x.txt"}], [{"kind": "required_sections"}]) is True
        # Always QC: render check
        assert requires_semantic_qc("auto", [{"path": "scratch/x.txt"}], [{"kind": "render"}]) is True
        # Always QC: json_schema check
        assert requires_semantic_qc("auto", [{"path": "scratch/x.txt"}], [{"kind": "json_schema"}]) is True
        # Always QC: content_exact check
        assert requires_semantic_qc("auto", [{"path": "scratch/x.txt"}], [{"kind": "content_exact"}]) is True
        # Always QC: quality_spec with hard_failures
        assert requires_semantic_qc("auto", [{"path": "scratch/x.txt"}], [], {"hard_failures": ["no placeholders"]}) is True
        # Always QC: quality_spec with minimum_score
        assert requires_semantic_qc("auto", [{"path": "scratch/x.txt"}], [], {"minimum_score": 0.8}) is True
        # Usually no QC: single scratch, file_exists only
        assert requires_semantic_qc("auto", [{"path": "scratch/x.txt"}], [{"kind": "file_exists"}]) is False
        # Usually no QC: trivial content_regex
        assert requires_semantic_qc("auto", [{"path": "scratch/x.txt"}], [{"kind": "content_regex", "expected": ".+"}]) is False
        assert requires_semantic_qc("auto", [{"path": "scratch/x.txt"}], [{"kind": "content_regex", "expected": ".*"}]) is False
        # Usually no QC: syntax + hygiene checks only
        assert requires_semantic_qc("auto", [{"path": "scratch/x.py"}], [
            {"kind": "syntax"}, {"kind": "hygiene"}, {"kind": "min_size"},
        ]) is False
        # QC for meaningful content_regex
        assert requires_semantic_qc("auto", [{"path": "scratch/x.txt"}], [{"kind": "content_regex", "expected": "def main"}]) is True
        # QC for unknown check kind
        assert requires_semantic_qc("auto", [{"path": "scratch/x.txt"}], [{"kind": "unknown_check"}]) is True

    def test_qc_verdict_roundtrip(self, tmp_path):
        from orchestrator.qc_review import QCVerdict
        v = QCVerdict(passed=True, reason="test", status="PASS", score=0.9,
                       issues=[{"severity": "MAINOR", "description": "test"}])
        d = v.to_dict()
        assert d["passed"] is True
        assert d["status"] == "PASS"
        assert d["score"] == 0.9
        assert len(d["issues"]) == 1


class TestQualityPlanWiring:
    """End-to-end supervisor wiring: explicit quality_plan in the contract
    must reach run_quality_plane and drive the component/panel paths."""

    def test_explicit_component_panel_plan_completes(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FAKE_WORKER", "1")
        monkeypatch.setenv("FAKE_QC", "PASS")
        task_id = "qp-wire-component-1"
        output_path = "scratch/test-qc/qp-component.txt"
        contract = _make_contract_dict(task_id, output_path)
        contract["quality_plan"] = {
            "mode": "component_panel",
            "lens": "code_correctness",
            "reviewers": [{"role": "maintainer", "model_policy": "default"}],
            "synthesis": {"required": True},
            "arbitration": {"enabled": False},
            "budget": {"max_llm_calls": 8},
        }
        _, _, res, state, qc_v = _run_supervisor(tmp_path, task_id, output_path, contract, monkeypatch)
        assert res.get(task_id) in ("COMPLETE", "complete")
        assert qc_v is not None
        assert qc_v["passed"] is True
        assert qc_v["status"] == "PASS"
        assert qc_v["component_verdicts"], "component path should produce component verdicts"

    def test_explicit_invalid_plan_falls_back_to_default(self, tmp_path, monkeypatch):
        """An invalid explicit quality_plan must not crash — fall back to default plan."""
        monkeypatch.setenv("FAKE_WORKER", "1")
        monkeypatch.setenv("FAKE_QC", "PASS")
        task_id = "qp-wire-invalid-1"
        output_path = "scratch/test-qc/qp-invalid.txt"
        contract = _make_contract_dict(task_id, output_path)
        contract["quality_plan"] = {
            "mode": "not_a_valid_mode",
            "lens": "code_correctness",
            "reviewers": [],
        }
        _, _, res, state, qc_v = _run_supervisor(tmp_path, task_id, output_path, contract, monkeypatch)
        assert res.get(task_id) in ("COMPLETE", "complete")
        assert qc_v is not None
        assert qc_v["passed"] is True

    def test_default_wiring_still_legacy_for_code_correctness(self, tmp_path, monkeypatch):
        """auto + code_correctness without explicit plan must keep legacy behavior."""
        monkeypatch.setenv("FAKE_WORKER", "1")
        monkeypatch.setenv("FAKE_QC", "PASS")
        task_id = "qp-wire-default-1"
        output_path = "scratch/test-qc/qp-default.txt"
        contract = _make_contract_dict(task_id, output_path)
        _, _, res, state, qc_v = _run_supervisor(tmp_path, task_id, output_path, contract, monkeypatch)
        assert res.get(task_id) in ("COMPLETE", "complete")
        assert qc_v is not None
        assert qc_v["passed"] is True
        assert qc_v.get("component_verdicts") == [], "legacy path must not produce component verdicts"


class TestQcStateTransitions:
    def test_qc_running_legal_transitions(self):
        from orchestrator.state import LEGAL_TRANSITIONS, STATES
        assert "QC_RUNNING" in STATES
        transitions = LEGAL_TRANSITIONS["QC_RUNNING"]
        assert "QC_PASSED" in transitions
        assert "QC_REJECTED" in transitions
        assert "QC_INSUFFICIENT_EVIDENCE" in transitions

    def test_qc_insufficient_evidence_legal_transitions(self):
        from orchestrator.state import LEGAL_TRANSITIONS, STATES
        assert "QC_INSUFFICIENT_EVIDENCE" in STATES
        transitions = LEGAL_TRANSITIONS["QC_INSUFFICIENT_EVIDENCE"]
        assert "RETRY_PENDING" in transitions
        assert "ESCALATED" in transitions


class _ConditionalState:
    def __init__(self):
        self.status = "QC_CONDITIONAL_PASS"
        self.data = {}
        self.events = []
        self.evidence = []
        self.transition_calls = []
        self.journal_events = []

    def add_evidence(self, kind, path):
        self.evidence.append((kind, path))

    def transition(self, status, reason="", evidence_path=None):
        self.transition_calls.append((status, reason, evidence_path))
        raise AssertionError("conditional state must be preserved, not blocked")

    def _write_journal(self, event):
        self.journal_events.append(event)


def test_record_task_exception_preserves_qc_conditional_pass(tmp_path):
    from types import SimpleNamespace

    state = _ConditionalState()
    graph_updates = []

    supervisor = Supervisor.__new__(Supervisor)
    supervisor._task_run_dir = lambda task_id: str(tmp_path / task_id)
    supervisor._state_path = lambda task_id: str(tmp_path / task_id / "state.json")
    supervisor._load_or_create_state = lambda task_id, contract_path: state
    supervisor._safe_save = lambda saved_state, state_path: None
    supervisor.graph = SimpleNamespace(
        update_status=lambda task_id, status: graph_updates.append((task_id, status))
    )

    supervisor._record_task_exception("task-1", RuntimeError("boom"))

    assert state.status == "QC_CONDITIONAL_PASS"
    assert state.transition_calls == []
    assert state.events[-1]["from"] == "QC_CONDITIONAL_PASS"
    assert state.events[-1]["to"] == "QC_CONDITIONAL_PASS"
    assert state.events[-1]["synthetic"] is True
    assert graph_updates == [("task-1", "QC_CONDITIONAL_PASS")]

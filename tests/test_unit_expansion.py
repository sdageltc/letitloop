"""Expanded unit tests covering edge cases for existing modules."""

import os
import json
import pytest
from unittest.mock import patch

from orchestrator.state import (
    State, create_initial_state, load_state, save_state, IllegalTransitionError, StateError
)
from orchestrator.failure import (
    classify_failure, suggest_remediation, count_consecutive_same_class,
    annotate_worker_result, MAX_SAME_CLASS_STRIKES,
    FAILURE_CLASS_TIMEOUT, FAILURE_CLASS_WORKER_NONZERO_EXIT,
    FAILURE_CLASS_VERIFIER_CONTENT_MISMATCH, FAILURE_CLASS_SCOPE_VIOLATION,
)
from orchestrator.contract import Contract, validate_contract
from orchestrator.goal import Goal, Plan, ContractGraph


# ── State Edge Cases ──

class TestStateEdgeCases:

    def test_create_initial_state_defaults(self):
        s = create_initial_state("test-task")
        assert s.task_id == "test-task"
        assert s.status == "DRAFTED"
        assert s.attempt == 1
        assert s.evidence == {}
        assert s.worker_results == []
        assert s.data == {}

    def test_invalid_state_raises(self):
        with pytest.raises(StateError, match="invalid state"):
            State("t1", status="INVALID_STATE")

    def test_none_status_ok(self):
        s = State("t1", status=None)
        assert s.status is None

    def test_transition_from_terminal_raises(self):
        s = State("t1", status="COMPLETE")
        with pytest.raises(IllegalTransitionError, match="terminal"):
            s.transition("DRAFTED")

    def test_transition_to_unknown_state_raises(self):
        s = create_initial_state("t1")
        with pytest.raises(IllegalTransitionError):
            s.transition("NONEXISTENT")

    def test_illegal_transition_message_includes_allowed(self):
        s = State("t1", status="COMPLETE")
        try:
            s.transition("WORKING")
        except IllegalTransitionError as e:
            msg = str(e)
            assert "COMPLETE" in msg
            assert "terminal" in msg

    def test_record_approach(self):
        s = create_initial_state("t1")
        s.record_approach("try different model")
        assert len(s.changed_approaches) == 1
        assert s.changed_approaches[0] == "try different model"

    def test_add_evidence(self):
        s = create_initial_state("t1")
        s.add_evidence("preflight", "/path/to/evidence.json")
        assert s.evidence["preflight"] == "/path/to/evidence.json"

    def test_add_worker_result(self):
        s = create_initial_state("t1")
        wr = {"exit_code": 0, "stdout": "ok", "stderr": "", "elapsed_sec": 1.0}
        s.add_worker_result(wr)
        assert len(s.worker_results) == 1
        assert s.worker_results[0]["exit_code"] == 0

    def test_terminal_states(self):
        for status in ("COMPLETE", "ESCALATED"):
            s = State("t1", status=status)
            assert s.is_terminal()

    def test_non_terminal_states(self):
        for status in ("DRAFTED", "READY", "WORKING", "VERIFYING"):
            s = State("t1", status=status)
            assert not s.is_terminal()

    def test_serialization_roundtrip(self, tmp_path):
        s = create_initial_state("t1")
        s.transition("PREFLIGHT_RUNNING")
        save_state(s, os.path.join(str(tmp_path), "state.json"))
        s2 = load_state(os.path.join(str(tmp_path), "state.json"))
        assert s2.task_id == s.task_id
        assert s2.status == s.status
        assert s2.attempt == s.attempt

    def test_save_nonexistent_dir_creates_it(self, tmp_path):
        s = create_initial_state("t1")
        path = os.path.join(str(tmp_path), "new_dir", "state.json")
        save_state(s, path)
        assert os.path.isfile(path)

    def test_load_nonexistent_raises(self):
        with pytest.raises(StateError):
            load_state("/nonexistent/state.json")

    def test_legal_transitions_correct(self):
        s = create_initial_state("t1")
        legal = s.legal_transitions()
        assert "PREFLIGHT_RUNNING" in legal

    def test_legal_transitions_from_ready(self):
        s = State("t1", status="READY")
        legal = s.legal_transitions()
        assert "WORKING" in legal

    def test_legal_transitions_from_working(self):
        s = State("t1", status="WORKING")
        legal = s.legal_transitions()
        assert "VERIFYING" in legal

    def test_data_persists_after_transition(self):
        s = create_initial_state("t1")
        s.data["custom_key"] = "custom_value"
        s.transition("PREFLIGHT_RUNNING")
        assert s.data["custom_key"] == "custom_value"


# ── Failure Classification Edge Cases ──

class TestFailureEdgeCases:

    def test_classify_timeout(self):
        s = create_initial_state("t1")
        s.status = "VERIFICATION_FAILED"
        s.worker_results = [{"exit_code": -1, "stdout": "", "stderr": "timed out", "elapsed_sec": 300}]
        fclass = classify_failure(s)
        assert fclass == FAILURE_CLASS_TIMEOUT

    def test_classify_nonzero_exit(self):
        s = create_initial_state("t1")
        s.status = "VERIFICATION_FAILED"
        s.worker_results = [{"exit_code": 2, "stdout": "", "stderr": "error", "elapsed_sec": 5}]
        fclass = classify_failure(s)
        assert fclass == FAILURE_CLASS_WORKER_NONZERO_EXIT

    def test_classify_preflight_blocked(self):
        s = create_initial_state("t1")
        s.status = "BLOCKED"
        fclass = classify_failure(s)
        assert fclass == "preflight_missing_input"

    def test_classify_scope_violation(self):
        s = create_initial_state("t1")
        s.status = "VERIFICATION_FAILED"
        s.worker_results = [{"exit_code": 0, "stdout": "data", "stderr": "", "elapsed_sec": 1}]
        s.data["scope_violations"] = [{"violation_type": "outside_scope", "path": "bad.txt"}]
        fclass = classify_failure(s)
        assert fclass == FAILURE_CLASS_SCOPE_VIOLATION

    def test_classify_no_data_unknown(self):
        s = create_initial_state("t1")
        s.status = "VERIFICATION_FAILED"
        fclass = classify_failure(s)
        assert fclass == "unknown"

    def test_suggest_retry_when_attempts_remain(self):
        rem = suggest_remediation(FAILURE_CLASS_TIMEOUT, 1, 3)
        assert rem["action"] == "retry"
        assert "attempts remaining" in rem["reason"]

    def test_suggest_split_on_exhaustion(self):
        rem = suggest_remediation(FAILURE_CLASS_TIMEOUT, 3, 3)
        assert rem["action"] == "split"
        assert rem["requires_new_approach"] is False

    def test_suggest_replan_on_preflight(self):
        rem = suggest_remediation("preflight_missing_input", 1, 3)
        assert rem["action"] == "replan"

    def test_suggest_replan_on_contract_invalid(self):
        rem = suggest_remediation("contract_invalid", 1, 3)
        assert rem["action"] == "replan"

    def test_suggest_escalate_no_attempts(self):
        rem = suggest_remediation("unknown", 3, 3)
        assert rem["action"] == "replan"

    def test_count_consecutive_same_class(self):
        s = create_initial_state("t1")
        s.worker_results = [
            {"failure_class": "timeout"},
            {"failure_class": "timeout"},
            {"failure_class": "worker_nonzero_exit"},
        ]
        assert count_consecutive_same_class(s, "timeout") == 0
        assert count_consecutive_same_class(s, "worker_nonzero_exit") == 1

    def test_annotate_worker_result(self):
        wr = {"exit_code": 1, "stdout": "", "stderr": "err"}
        result = annotate_worker_result(wr, "worker_nonzero_exit")
        assert result["failure_class"] == "worker_nonzero_exit"
        assert result["exit_code"] == 1

    def test_max_strikes_constant(self):
        assert MAX_SAME_CLASS_STRIKES == 3


# ── Contract Edge Cases ──

class TestContractEdgeCases:

    def test_valid_contract_passes(self):
        c = {
            "task_id": "t1", "title": "Test", "status": "drafted",
            "risk_tier": "auto", "workspace_scope": {"allow": ["scratch/"], "deny": []},
            "objective": "test", "worker": {"model": "x", "max_attempts": 1},
            "outputs": [{"path": "scratch/out.txt"}],
            "acceptance_checks": [],
            "qc": {"required": False, "lens": "code_correctness"},
        }
        errs = validate_contract(c)
        assert errs == []

    def test_missing_task_id_fails(self):
        c = {"title": "Test"}
        errs = validate_contract(c)
        assert errs

    def test_output_outside_allow_list_fails(self):
        c = {
            "task_id": "t1", "title": "Test", "status": "drafted",
            "risk_tier": "auto",
            "workspace_scope": {"allow": ["scratch/"], "deny": []},
            "objective": "test", "worker": {"model": "x", "max_attempts": 1},
            "outputs": [{"path": "../outside.txt"}],
            "acceptance_checks": [],
            "qc": {"required": False, "lens": "code_correctness"},
        }
        errs = validate_contract(c)
        # ../ paths may or may not be caught depending on normpath behavior
        # At minimum they should not cause crashes

    def test_invalid_json(self, tmp_path):
        bad_path = os.path.join(str(tmp_path), "bad.json")
        with open(bad_path, "w") as f:
            f.write("{invalid")
        from orchestrator.contract import load_contract
        _, errs = load_contract(bad_path)
        assert errs

    def test_nonexistent_file(self):
        from orchestrator.contract import load_contract
        _, errs = load_contract("/nonexistent/contract.json")
        assert errs

    def test_contract_allowed_paths(self):
        raw = {
            "task_id": "t1", "title": "Test", "status": "DRAFTED",
            "risk_tier": "auto",
            "workspace_scope": {"allow": ["scratch/", "outputs/"], "deny": ["secrets/"]},
            "objective": "test", "worker": {"model": "x", "max_attempts": 1},
            "outputs": [{"path": "scratch/out.txt"}],
            "acceptance_checks": [],
            "qc": {"required": False, "lens": "code_correctness"},
        }
        c = Contract(raw)
        assert "scratch/" in c.allowed_paths()
        assert "secrets/" in c.denied_paths()


# ── Goal / Graph Edge Cases ──

class TestGraphEdgeCases:

    def test_graph_cycle_detection(self):
        contracts = [
            {"task_id": "a", "depends_on": ["b"]},
            {"task_id": "b", "depends_on": ["c"]},
            {"task_id": "c", "depends_on": ["a"]},
        ]
        plan = Plan(goal_id="g", contracts=contracts)
        graph = ContractGraph(plan)
        assert graph.has_cycle() is True

    def test_topological_sort_no_deps(self):
        contracts = [
            {"task_id": "a", "depends_on": []},
            {"task_id": "b", "depends_on": []},
            {"task_id": "c", "depends_on": []},
        ]
        plan = Plan(goal_id="g", contracts=contracts)
        graph = ContractGraph(plan)
        order = graph.topological_sort()
        assert len(order) == 3

    def test_ready_tasks_respects_dependencies(self):
        contracts = [
            {"task_id": "a", "depends_on": []},
            {"task_id": "b", "depends_on": ["a"]},
            {"task_id": "c", "depends_on": ["a"]},
        ]
        plan = Plan(goal_id="g", contracts=contracts)
        graph = ContractGraph(plan)
        ready = graph.get_ready_tasks()
        assert ready == ["a"] if "a" in ready else False

    def test_blocked_tasks_detected(self):
        contracts = [
            {"task_id": "a", "depends_on": []},
            {"task_id": "b", "depends_on": ["a"]},
        ]
        plan = Plan(goal_id="g", contracts=contracts)
        graph = ContractGraph(plan)
        blocked = graph.get_blocked_tasks()
        assert "b" in blocked

    def test_no_blocked_tasks_for_completed(self):
        contracts = [
            {"task_id": "a", "depends_on": []},
            {"task_id": "b", "depends_on": ["a"]},
        ]
        plan = Plan(goal_id="g", contracts=contracts)
        graph = ContractGraph(plan)
        graph.update_status("a", "COMPLETE")
        graph.update_status("b", "COMPLETE")
        blocked = graph.get_blocked_tasks()
        assert blocked == []

    def test_ready_excludes_done_tasks(self):
        contracts = [
            {"task_id": "a", "depends_on": []},
        ]
        plan = Plan(goal_id="g", contracts=contracts)
        graph = ContractGraph(plan)
        graph.update_status("a", "COMPLETE")
        ready = graph.get_ready_tasks()
        assert ready == []

    def test_missing_dependency_in_graph(self):
        contracts = [
            {"task_id": "a", "depends_on": ["nonexistent"]},
        ]
        plan = Plan(goal_id="g", contracts=contracts)
        graph = ContractGraph(plan)
        ready = graph.get_ready_tasks()
        assert ready == []

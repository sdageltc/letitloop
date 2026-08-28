"""Tests for state machine and transitions."""

import os
import tempfile

import pytest
from orchestrator.state import (
    LEGAL_TRANSITIONS,
    STATES,
    TERMINAL_STATES,
    IllegalTransitionError,
    State,
    StateError,
    create_initial_state,
    load_state,
    save_state,
)

pytestmark = pytest.mark.fast


class TestCreateInitialState:
    def test_creates_drafted_state(self):
        s = create_initial_state("test-001")
        assert s.task_id == "test-001"
        assert s.status == "DRAFTED"
        assert s.attempt == 1
        assert len(s.events) == 1
        assert s.events[0]["to"] == "DRAFTED"

    def test_state_data_defaults(self):
        s = create_initial_state("x")
        assert s.changed_approaches == []
        assert s.evidence == {}
        assert s.worker_results == []
        assert s.data == {}


class TestLegalTransitions:
    def test_all_states_have_entry(self):
        for s in STATES:
            assert s in LEGAL_TRANSITIONS, f"missing transition entry for {s}"


class TestTransition:
    def test_legal_transition_works(self):
        s = create_initial_state("t1")
        s.transition("PREFLIGHT_RUNNING", reason="testing")
        assert s.status == "PREFLIGHT_RUNNING"
        assert len(s.events) == 2
        assert s.events[-1]["from"] == "DRAFTED"
        assert s.events[-1]["to"] == "PREFLIGHT_RUNNING"
        assert s.events[-1]["reason"] == "testing"

    def test_illegal_transition_raises(self):
        s = create_initial_state("t1")
        with pytest.raises(IllegalTransitionError):
            s.transition("COMPLETE", reason="shortcut")

    def test_illegal_transition_message_includes_allowed(self):
        s = create_initial_state("t1")

        with pytest.raises(IllegalTransitionError) as exc:
            s.transition("WORKING")
        assert "DRAFTED" in str(exc.value)
        assert "PREFLIGHT_RUNNING" in str(exc.value)

    def test_terminal_state_blocks_all(self):
        s = create_initial_state("t1")
        s.events = []
        s.status = "COMPLETE"
        with pytest.raises(IllegalTransitionError) as exc:
            s.transition("DRAFTED")
        assert "terminal" in str(exc.value).lower()

    def test_unknown_target_raises(self):
        s = create_initial_state("t1")
        with pytest.raises(StateError):
            s.transition("NONEXISTENT")

    def test_transition_appends_evidence_path(self):
        s = create_initial_state("t1")
        s.events = []
        s.status = "READY"
        s.transition("WORKING", evidence_path="/tmp/evidence.json")
        assert s.events[-1].get("evidence_path") == "/tmp/evidence.json"


class TestHappyPathTransitionSequence:
    def test_full_drafted_to_complete(self):
        s = create_initial_state("happy")
        s.events = []
        s.status = "DRAFTED"

        s.transition("PREFLIGHT_RUNNING")
        s.transition("READY")
        s.transition("WORKING")
        s.transition("VERIFYING")
        s.transition("VERIFIED")
        s.transition("QC_RUNNING")
        s.transition("QC_PASSED")
        s.transition("COMPLETE")
        assert s.status == "COMPLETE"
        assert s.is_terminal()

    def test_verification_failure_path(self):
        s = create_initial_state("vf")
        s.events = []
        s.status = "VERIFYING"
        s.transition("VERIFICATION_FAILED")
        s.transition("RETRY_PENDING")
        assert s.status == "RETRY_PENDING"

    def test_escalation_path(self):
        s = create_initial_state("esc")
        s.events = []
        s.status = "RETRY_PENDING"
        s.transition("ESCALATED")
        assert s.is_terminal()

    def test_qc_rejected_path(self):
        s = create_initial_state("qcr")
        s.events = []
        s.status = "VERIFIED"
        s.transition("QC_RUNNING")
        s.transition("QC_REJECTED")
        assert s.status == "QC_REJECTED"


class TestRecordApproach:
    def test_records_approach(self):
        s = create_initial_state("t")
        s.record_approach("try a different algorithm")
        assert any("different algorithm" in a for a in s.changed_approaches)

    def test_multiple_approaches(self):
        s = create_initial_state("t")
        s.record_approach("first")
        s.record_approach("second")
        assert len(s.changed_approaches) == 2


class TestIncrementAttempt:
    def test_increment(self):
        s = create_initial_state("t")
        assert s.attempt == 1
        s.increment_attempt()
        assert s.attempt == 2
        s.increment_attempt()
        assert s.attempt == 3


class TestEvidence:
    def test_add_evidence(self):
        s = create_initial_state("t")
        s.add_evidence("preflight", "/tmp/preflight.json")
        assert s.evidence["preflight"] == "/tmp/preflight.json"


class TestWorkerResult:
    def test_add_worker_result(self):
        s = create_initial_state("t")
        result = {"exit_code": 0, "stdout": "ok"}
        s.add_worker_result(result)
        assert len(s.worker_results) == 1
        assert s.worker_results[0]["exit_code"] == 0


class TestCanResume:
    def test_drafted_cannot_resume(self):
        s = create_initial_state("t")
        assert not s.can_resume()

    def test_ready_can_resume(self):
        s = create_initial_state("t")
        s.status = "READY"
        assert s.can_resume()

    def test_terminal_cannot_resume(self):
        s = create_initial_state("t")
        for terminal in TERMINAL_STATES:
            s.status = terminal
            assert not s.can_resume(), f"{terminal} should not be resumable"


class TestIsTerminal:
    def test_terminal_states(self):
        for st in TERMINAL_STATES:
            s = create_initial_state("t")
            s.status = st
            assert s.is_terminal()

    def test_non_terminal(self):
        s = create_initial_state("t")
        s.status = "WORKING"
        assert not s.is_terminal()


class TestSerialization:
    def test_round_trip_dict(self):
        s = create_initial_state("serialize-test")
        s.transition("PREFLIGHT_RUNNING", reason="test")
        s.add_evidence("check", "/tmp/evidence.json")
        s.record_approach("new approach")
        s.increment_attempt()
        d = s.to_dict()
        s2 = State.from_dict(d)
        assert s2.task_id == s.task_id
        assert s2.status == s.status
        assert s2.attempt == s.attempt
        assert s2.changed_approaches == s.changed_approaches
        assert s2.evidence == s.evidence
        assert len(s2.events) == len(s.events)

    def test_save_and_load(self):
        s = create_initial_state("disk-test")
        s.transition("PREFLIGHT_RUNNING")
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "state.json")
            save_state(s, path)
            assert os.path.isfile(path)
            s2 = load_state(path)
            assert s2.task_id == "disk-test"
            assert s2.status == "PREFLIGHT_RUNNING"

    def test_load_nonexistent_raises(self):
        with pytest.raises(StateError):
            load_state("/nonexistent/state.json")


class TestIllegalTransitionsFromPlan:
    def test_invalid_drafted_to_working(self):
        s = create_initial_state("t")
        with pytest.raises(IllegalTransitionError):
            s.transition("WORKING")

    def test_invalid_working_to_complete(self):
        s = create_initial_state("t")
        s.status = "WORKING"
        with pytest.raises(IllegalTransitionError):
            s.transition("COMPLETE")

    def test_invalid_working_to_drafted(self):
        s = create_initial_state("t")
        s.status = "WORKING"
        with pytest.raises(IllegalTransitionError):
            s.transition("DRAFTED")


class TestSchemaForwardCompatibility:
    def test_from_dict_future_schema_raises(self):
        d = {
            "task_id": "future-task",
            "status": "DRAFTED",
            "schema_version": 999,
        }
        with pytest.raises(StateError) as exc_info:
            State.from_dict(d)
        assert "WAL schema v999 not supported" in str(exc_info.value)

    def test_load_state_future_schema_raises(self, tmp_path):
        state_file = tmp_path / "state.json"
        import json

        state_file.write_text(json.dumps({"task_id": "future-task", "schema_version": 999}), encoding="utf-8")
        with pytest.raises(StateError) as exc_info:
            load_state(str(state_file))
        assert "WAL schema v999 not supported" in str(exc_info.value)

    def test_replay_wal_legacy_fails_closed(self, tmp_path):
        wal_file = tmp_path / "state.wal.jsonl"
        import json

        # Legacy WAL without INIT seq=1
        wal_file.write_text(json.dumps({"event_type": "TRANSITION", "to": "READY"}) + "\n", encoding="utf-8")
        state_file = tmp_path / "state.json"
        state_file.write_text(
            json.dumps(
                {"task_id": "t1", "status": "DRAFTED", "schema_version": 2, "data": {"migrated_from_snapshot_v1": True}}
            ),
            encoding="utf-8",
        )
        from orchestrator.state import replay_wal

        with pytest.raises(StateError) as exc_info:
            replay_wal(str(state_file))
        assert "WAL does not start with INIT (seq=1)" in str(exc_info.value)

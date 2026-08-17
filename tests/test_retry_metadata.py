"""Tests for structured retry metadata persistence and divergence enforcement."""

import os

from orchestrator.failure import (
    compute_strategy_fingerprint,
    is_divergent,
    require_divergent_retry,
)
from orchestrator.state import create_initial_state, load_state, save_state


class TestRetryMetadata:
    def test_retry_metadata_added_to_state(self):
        state = create_initial_state("test-retry-1")
        meta = {
            "attempt": 2,
            "trigger": "VERIFICATION_FAILED",
            "approach": "retry with different implementation",
            "changed_dimensions": ["implementation_approach"],
            "strategy_fingerprint": "abc123",
            "prior_fingerprint": "def456",
            "failure_ids": ["verifier_content_mismatch"],
        }
        state.add_retry_metadata(meta)
        assert "retry_metadata" in state.data
        assert len(state.data["retry_metadata"]) == 1
        assert state.data["retry_metadata"][0]["attempt"] == 2

    def test_retry_metadata_multiple_entries(self):
        state = create_initial_state("test-retry-2")
        for i in range(3):
            state.add_retry_metadata({"attempt": i + 2, "trigger": "QC_REJECTED", "approach": f"retry {i}"})
        assert len(state.data["retry_metadata"]) == 3
        assert state.data["retry_metadata"][-1]["attempt"] == 4

    def test_retry_metadata_survives_serialization(self, tmp_path):
        state = create_initial_state("test-retry-serial")
        state.add_retry_metadata({"attempt": 2, "trigger": "VERIFICATION_FAILED", "approach": "retry"})
        path = os.path.join(str(tmp_path), "state.json")
        save_state(state, path)
        loaded = load_state(path)
        assert "retry_metadata" in loaded.data
        assert loaded.data["retry_metadata"][0]["attempt"] == 2

    def test_retry_metadata_in_to_dict(self):
        state = create_initial_state("test-retry-dict")
        state.add_retry_metadata({"attempt": 3, "trigger": "QC_REJECTED"})
        d = state.to_dict()
        assert "data" in d
        assert "retry_metadata" in d["data"]
        assert d["data"]["retry_metadata"][0]["attempt"] == 3

    def test_retry_metadata_missing_dimensions_defaults(self):
        state = create_initial_state("test-retry-defaults")
        meta = {"attempt": 2, "trigger": "VERIFICATION_FAILED"}
        state.add_retry_metadata(meta)
        assert state.data["retry_metadata"][0]["attempt"] == 2
        assert "trigger" in state.data["retry_metadata"][0]


class TestDivergence:
    def test_identical_approach_rejected_when_duplicate_in_history(self):
        state = create_initial_state("test-div-1")
        state.changed_approaches = ["same approach", "another approach"]
        result = require_divergent_retry(state, "same approach")
        assert result is False, "duplicate approach should be rejected"

    def test_different_approach_accepted(self):
        state = create_initial_state("test-div-2")
        state.changed_approaches = ["old approach"]
        result = require_divergent_retry(state, "new approach")
        assert result is True, "different approach should be accepted"

    def test_empty_changed_approaches_accepted(self):
        state = create_initial_state("test-div-3")
        result = require_divergent_retry(state, "first approach")
        assert result is True, "no prior approaches should be accepted"

    def test_fingerprint_stable_for_same_approach(self):
        state = create_initial_state("test-fp-1")
        state.changed_approaches = ["use a tree structure"]
        fp1 = compute_strategy_fingerprint(state)
        state.changed_approaches.append("use a tree structure")
        fp2 = compute_strategy_fingerprint(state)
        assert fp1 == fp2

    def test_fingerprint_differs_for_different_approach(self):
        state = create_initial_state("test-fp-2")
        state.changed_approaches = ["use a list"]
        fp1 = compute_strategy_fingerprint(state)
        state.changed_approaches.append("use a dict")
        fp2 = compute_strategy_fingerprint(state)
        assert fp1 != fp2

    def test_is_divergent_detects_duplicate(self):
        state = create_initial_state("test-div-exact")
        state.changed_approaches = ["original", "different"]
        assert is_divergent(state, "original") is False, "original already failed, should not be divergent"
        assert is_divergent(state, "new") is True, "new approach should be divergent"

    def test_is_divergent_no_prior(self):
        state = create_initial_state("test-div-noprior")
        assert is_divergent(state, "anything") is True


class TestRetryInSupervisor:
    def test_retry_metadata_recorded_during_qc_reject(self, tmp_path, monkeypatch):
        """Verify that QC rejection records retry metadata in state."""
        monkeypatch.setenv("FAKE_WORKER", "1")
        monkeypatch.setenv("FAKE_QC", "REJECT")
        ws_dir = str(tmp_path)
        run_dir = os.path.join(ws_dir, "scratch", "runs")
        task_id = "retry-meta-qc-1"
        contract_dict = {
            "task_id": task_id,
            "title": "Retry Meta QC Test",
            "status": "drafted",
            "risk_tier": "auto",
            "workspace_scope": {"allow": ["scratch/test-retry/"], "deny": []},
            "objective": "Test retry metadata from QC rejection",
            "worker": {"model": "openai:gpt-4o-mini", "max_attempts": 2},
            "inputs": [],
            "outputs": [{"path": "scratch/test-retry/meta.txt"}],
            "acceptance_checks": [
                {"id": "check-exists", "kind": "file_exists", "path": "scratch/test-retry/meta.txt"},
            ],
            "qc": {"required": True, "lens": "code_correctness"},
        }
        from orchestrator.goal import Goal, Plan
        from orchestrator.supervisor import Supervisor

        goal = Goal(goal_id="retry-meta-goal", title="Retry Meta", description="")
        plan = Plan(goal_id=goal.goal_id, contracts=[{"task_id": task_id, "contract": contract_dict}])
        supervisor = Supervisor(goal, plan, workspace_root=ws_dir, run_dir=run_dir)
        supervisor.execute_plan()
        state_file = os.path.join(run_dir, task_id, "state.json")
        assert os.path.isfile(state_file)
        state = load_state(state_file)
        assert "retry_metadata" in state.data
        assert len(state.data["retry_metadata"]) >= 1
        rm = state.data["retry_metadata"][0]
        assert "attempt" in rm
        assert "trigger" in rm

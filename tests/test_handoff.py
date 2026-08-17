"""Tests for session handoff generation."""

import json
import os
import tempfile

import pytest

from orchestrator.handoff import build_handoff
from orchestrator.state import create_initial_state

pytestmark = pytest.mark.fast


class TestBuildHandoff:
    def test_handoff_contains_expected_keys(self):
        s = create_initial_state("handoff-test")
        s.transition("PREFLIGHT_RUNNING", reason="test")
        handoff = build_handoff(s, run_dir=None)
        assert handoff["task_id"] == "handoff-test"
        assert handoff["status"] == "PREFLIGHT_RUNNING"
        assert "handoff_id" in handoff
        assert "generated_at" in handoff
        assert "next_legal_actions" in handoff
        assert "completed_checks" in handoff
        assert "evidence_paths" in handoff
        assert "outcome_classification" in handoff
        assert "unresolved_human_decisions" in handoff

    def test_handoff_next_actions_from_state(self):
        s = create_initial_state("t")
        s.status = "READY"
        handoff = build_handoff(s, run_dir=None)
        assert "WORKING" in handoff["next_legal_actions"]

    def test_handoff_evidence_paths(self):
        s = create_initial_state("t")
        s.add_evidence("preflight", "/tmp/preflight.json")
        handoff = build_handoff(s, run_dir=None)
        assert "/tmp/preflight.json" in handoff["evidence_paths"]

    def test_handoff_blocker_for_blocked(self):
        s = create_initial_state("t")
        s.events = []
        s.status = "BLOCKED"
        s.transition = lambda *a, **kw: None
        handoff = build_handoff(s, run_dir=None)
        assert handoff["blocker"] is not None

    def test_handoff_blocker_for_escalated(self):
        s = create_initial_state("t")
        s.events = []
        s.status = "ESCALATED"
        s.transition = lambda *a, **kw: None
        handoff = build_handoff(s, run_dir=None)
        assert handoff["blocker"] is not None

    def test_handoff_no_blocker_for_active(self):
        s = create_initial_state("t")
        s.status = "WORKING"
        handoff = build_handoff(s, run_dir=None)
        assert handoff["blocker"] is None

    def test_unresolved_decisions_for_blocked(self):
        s = create_initial_state("t")
        s.status = "BLOCKED"
        handoff = build_handoff(s, run_dir=None)
        assert len(handoff["unresolved_human_decisions"]) > 0

    def test_unresolved_decisions_for_escalated(self):
        s = create_initial_state("t")
        s.status = "ESCALATED"
        handoff = build_handoff(s, run_dir=None)
        assert len(handoff["unresolved_human_decisions"]) > 0

    def test_outcome_classification_complete(self):
        s = create_initial_state("t")
        s.status = "COMPLETE"
        handoff = build_handoff(s, run_dir=None)
        assert handoff["outcome_classification"] == "lesson_candidate"

    def test_outcome_classification_escalated(self):
        s = create_initial_state("t")
        s.status = "ESCALATED"
        handoff = build_handoff(s, run_dir=None)
        assert handoff["outcome_classification"] == "skill_candidate"

    def test_outcome_classification_blocked(self):
        s = create_initial_state("t")
        s.status = "BLOCKED"
        handoff = build_handoff(s, run_dir=None)
        assert handoff["outcome_classification"] == "observation"

    def test_outcome_classification_active(self):
        s = create_initial_state("t")
        for st in ("WORKING", "READY", "VERIFYING"):
            s.status = st
            handoff = build_handoff(s, run_dir=None)
            assert handoff["outcome_classification"] == "observation"

    def test_writes_to_run_dir(self):
        s = create_initial_state("write-test")
        with tempfile.TemporaryDirectory() as td:
            build_handoff(s, run_dir=td)
            handoff_path = os.path.join(td, "handoff.json")
            assert os.path.isfile(handoff_path)
            with open(handoff_path) as f:
                loaded = json.load(f)
            assert loaded["task_id"] == "write-test"

    def test_handoff_deterministic_derived(self):
        s = create_initial_state("det-test")
        s.transition("PREFLIGHT_RUNNING")
        s.transition("READY")
        h1 = build_handoff(s, run_dir=None)
        h2 = build_handoff(s, run_dir=None)
        assert h1["status"] == h2["status"]
        assert h1["completed_checks"] == h2["completed_checks"]
        assert h1["next_legal_actions"] == h2["next_legal_actions"]


class TestHandoffRetryPending:
    def test_unresolved_decisions_for_retry_pending(self):
        s = create_initial_state("t")
        s.status = "RETRY_PENDING"
        handoff = build_handoff(s, run_dir=None)
        assert len(handoff["unresolved_human_decisions"]) > 0

    def test_unresolved_decisions_for_qc_rejected(self):
        s = create_initial_state("t")
        s.status = "QC_REJECTED"
        handoff = build_handoff(s, run_dir=None)
        assert len(handoff["unresolved_human_decisions"]) > 0

"""
tests/test_proposal_ledger.py
Unit and integration tests for ProposalLedger, ArchitecturalProposal staging, and HITL approval workflow.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch
from orchestrator.feasibility_gate import FeasibilityVerdict
from orchestrator.live_evolution_engine import LiveEvolutionEngine
from orchestrator.proposal_ledger import ArchitecturalProposal, ProposalLedger


def test_proposal_ledger_record_and_load(tmp_path: Path):
    ledger = ProposalLedger(tmp_path)

    prop = ledger.record_proposal(
        target_module="orchestrator/plan_quality.py",
        target_function="check_plan_quality",
        complexity_score=238.1,
        deliberation_verdict="DEFER",
        risk_score=0.85,
        rationale="State machine complexity is too high for zero-shot mutation.",
        suggested_strategy="Decompose into distinct validator classes.",
        research_findings=[
            {
                "title": "Refactoring Large Functions",
                "summary": "Step-by-step breakdown using strategy pattern.",
                "source_url": "https://example.com/refactoring",
                "provider_name": "GitHub",
            }
        ],
        proposed_approach="Split check_plan_quality into 4 helper passes.",
    )

    assert prop.proposal_id == "PROP-orchestrator_plan_quality-check_plan_quality"
    assert prop.status == "PENDING_HUMAN_REVIEW"

    # Verify JSON file exists
    json_path = (
        tmp_path
        / "scratch/evolution_state/proposals"
        / f"{prop.proposal_id}.json"
    )
    assert json_path.exists()
    loaded_data = json.loads(json_path.read_text(encoding="utf-8"))
    assert loaded_data["risk_score"] == 0.85

    # Verify Markdown artifact exists
    md_path = (
        tmp_path
        / "scratch/evolution_state/proposals"
        / f"{prop.proposal_id}.md"
    )
    assert md_path.exists()
    md_content = md_path.read_text(encoding="utf-8")
    assert "**McCabe Complexity Score**: 238.1" in md_content
    assert "https://example.com/refactoring" in md_content

    # Load from ledger
    fetched = ledger.get_proposal(prop.proposal_id)
    assert fetched is not None
    assert fetched.proposal_id == prop.proposal_id
    assert fetched.complexity_score == 238.1

    # Update status
    assert ledger.mark_status(prop.proposal_id, "APPROVED") is True
    updated = ledger.get_proposal(prop.proposal_id)
    assert updated.status == "APPROVED"


def test_proposal_ledger_idempotent_staging(tmp_path: Path):
    ledger = ProposalLedger(tmp_path)

    p1 = ledger.record_proposal(
        target_module="orchestrator/auth.py",
        target_function="verify_token",
        complexity_score=45.0,
        deliberation_verdict="DEFER",
        risk_score=0.90,
        rationale="Initial scan",
        suggested_strategy="Strategy 1",
    )
    time_created = p1.created_at

    p2 = ledger.record_proposal(
        target_module="orchestrator/auth.py",
        target_function="verify_token",
        complexity_score=48.0,
        deliberation_verdict="DEFER",
        risk_score=0.92,
        rationale="Updated scan",
        suggested_strategy="Strategy 2",
    )

    assert p1.proposal_id == p2.proposal_id
    assert p2.created_at == time_created
    assert p2.suggested_strategy == "Strategy 2"

    all_props = ledger.list_proposals()
    assert len(all_props) == 1


def test_live_evolution_stages_proposal_on_deferral(tmp_path: Path):
    target_mod = tmp_path / "mod.py"
    target_mod.write_text("def complex_fn():\n    return 42\n", encoding="utf-8")

    engine = LiveEvolutionEngine(
        workspace_root=tmp_path,
        model_name="mock:verified",
        enable_research=False,
    )

    deferred_verdict = FeasibilityVerdict(
        verdict="DEFER",
        is_approved=False,
        rationale="Critical invariants must not be mutated autonomously.",
        suggested_strategy="Decompose into pure helper.",
        risk_score=0.88,
        requires_research=False,
    )

    with patch(
        "orchestrator.feasibility_gate.CognitiveFeasibilityGate.deliberate",
        return_value=deferred_verdict,
    ):
        res = engine.execute_live_optimization_cycle(
            module_path="mod.py",
            optimization_goal="Reduce complexity",
            target_function="complex_fn",
        )

        assert res["status"] == "PROPOSAL_STAGED_FOR_REVIEW"
        assert res["is_success"] is False
        assert "proposal_id" in res

        # Verify proposal was saved in ledger
        proposal = engine.proposal_ledger.get_proposal(res["proposal_id"])
        assert proposal is not None
        assert proposal.status == "PENDING_HUMAN_REVIEW"
        assert proposal.risk_score == 0.88

"""
tests/test_live_evolution_feasibility.py
Integration tests for Cognitive Feasibility Deliberation & Adaptive Research in LiveEvolutionEngine.
"""

import tempfile
from pathlib import Path
from unittest.mock import patch

from orchestrator.feasibility_gate import FeasibilityVerdict
from orchestrator.live_evolution_engine import LiveEvolutionEngine
from orchestrator.research import ResearchFinding


def test_live_evolution_skips_when_feasibility_is_deferred():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        mod_file = root / "core.py"
        mod_file.write_text("def sensitive_security_func(): pass\n", encoding="utf-8")

        engine = LiveEvolutionEngine(workspace_root=root, model_name="mock:test")

        deferred_verdict = FeasibilityVerdict(
            verdict="DEFER",
            is_approved=False,
            rationale="Critical security sensitivity.",
            suggested_strategy="Do not refactor",
            risk_score=0.9,
            requires_research=False,
        )

        with patch("orchestrator.feasibility_gate.CognitiveFeasibilityGate.deliberate", return_value=deferred_verdict):
            res = engine.execute_live_optimization_cycle(
                module_path="core.py",
                optimization_goal="Simplify logic",
                target_function="sensitive_security_func",
            )
            assert res["is_success"] is False
            assert res["status"] == "PROPOSAL_STAGED_FOR_REVIEW"
            assert "Critical security sensitivity" in res["rationale"]
            # Disk file must remain untouched
            assert mod_file.read_text(encoding="utf-8") == "def sensitive_security_func(): pass\n"


def test_live_evolution_injects_research_findings_into_prompt():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        mod_file = root / "solver.py"
        mod_file.write_text("def solve_problem(): return 1\n", encoding="utf-8")

        engine = LiveEvolutionEngine(workspace_root=root, model_name="mock:test", enable_research=True)

        feasible_with_research = FeasibilityVerdict(
            verdict="FEASIBLE",
            is_approved=True,
            rationale="Can use branch and bound technique.",
            suggested_strategy="Branch and bound",
            risk_score=0.3,
            requires_research=True,
        )

        mock_findings = [
            ResearchFinding(
                title="Branch and Bound in Python",
                summary="Fast pruning algorithm snippet.",
                source_url="https://example.com/paper",
                provider_name="arXiv",
            )
        ]

        mock_delta = """
<<<<<<< SEARCH
def solve_problem(): return 1
=======
def solve_problem(): return 2
>>>>>>> REPLACE
"""
        with (
            patch(
                "orchestrator.feasibility_gate.CognitiveFeasibilityGate.deliberate", return_value=feasible_with_research
            ),
            patch("orchestrator.research.AdaptiveResearchCoordinator.research", return_value=mock_findings),
            patch("orchestrator.live_evolution_engine.call_llm", return_value=mock_delta),
        ):
            res = engine.execute_live_optimization_cycle(
                module_path="solver.py",
                optimization_goal="Optimize solver",
                target_function="solve_problem",
            )
            assert res["is_success"] is True
            assert res["status"] == "VERIFIED_FIX"
            assert res["research_findings_count"] == 1
            assert "return 2" in mod_file.read_text(encoding="utf-8")

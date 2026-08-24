"""
tests/test_live_evolution.py
Integration tests for the unified LiveEvolutionEngine with mock LLM.
"""

from pathlib import Path
import tempfile
from unittest.mock import patch
from orchestrator.live_evolution_engine import LiveEvolutionEngine
from orchestrator.feasibility_gate import FeasibilityVerdict


def test_live_evolution_engine_cycle_success():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        mod_file = root / "core.py"
        mod_file.write_text(
            """
def process_value(x: int) -> int:
    # Initial comment
    return x * 1
""",
            encoding="utf-8",
        )

        engine = LiveEvolutionEngine(workspace_root=root, model_name="mock:test")
        mock_delta = """
<<<<<<< SEARCH
def process_value(x: int) -> int:
    # Initial comment
    return x * 1
=======
def process_value(x: int) -> int:
    # Initial comment
    return x * 2
>>>>>>> REPLACE
"""
        approved_feasibility = FeasibilityVerdict(
            verdict="FEASIBLE",
            is_approved=True,
            rationale="Approved for optimization.",
            suggested_strategy="Multiply by 2",
            risk_score=0.1,
            requires_research=False,
        )

        with patch("orchestrator.live_evolution_engine.CognitiveFeasibilityGate.deliberate", return_value=approved_feasibility), \
             patch("orchestrator.live_evolution_engine.call_llm", return_value=mock_delta):
            res = engine.execute_live_optimization_cycle(
                module_path="core.py",
                optimization_goal="Double return value",
                target_function="process_value",
            )
            assert res["is_success"] is True
            assert "process_value" in res["diff_summary"]
            assert (
                "return x * 2" in mod_file.read_text(encoding="utf-8")
            )  # Physical disk write-back

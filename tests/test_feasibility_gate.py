"""
tests/test_feasibility_gate.py
Unit tests for the Cognitive Feasibility Gate.
"""

from unittest.mock import patch
from orchestrator.feasibility_gate import CognitiveFeasibilityGate, FeasibilityVerdict


def test_feasibility_gate_approves_reducible_hotspot():
    sample_code = """
def complex_calc(x, y, z):
    if x > 0:
        if y > 0:
            if z > 0:
                return x + y + z
    return 0
"""
    mock_llm_json = """
```json
{
    "verdict": "FEASIBLE",
    "rationale": "High nested cyclomatic complexity can be flattened into guard clauses.",
    "suggested_strategy": "Early return guard clauses",
    "risk_score": 0.2,
    "requires_research": false
}
```
"""
    with patch("orchestrator.feasibility_gate.call_llm", return_value=mock_llm_json):
        res = CognitiveFeasibilityGate.deliberate(
            target_symbol="complex_calc",
            source_code=sample_code,
            complexity_score=18.5,
            model_name="mock:test",
        )
        assert res.verdict == "FEASIBLE"
        assert res.is_approved is True
        assert res.risk_score == 0.2
        assert "guard clauses" in res.suggested_strategy.lower()
        assert res.requires_research is False


def test_feasibility_gate_defers_high_risk_dependency():
    sample_code = """
def critical_auth_handler(token, secret, db_session):
    # Core cryptographic signature verification
    return verify_jwt(token, secret, db_session)
"""
    mock_llm_json = """
{
    "verdict": "DEFER",
    "rationale": "Cryptographic authentication logic carries critical regression risk.",
    "suggested_strategy": "Maintain existing implementation",
    "risk_score": 0.95,
    "requires_research": false
}
"""
    with patch("orchestrator.feasibility_gate.call_llm", return_value=mock_llm_json):
        res = CognitiveFeasibilityGate.deliberate(
            target_symbol="critical_auth_handler",
            source_code=sample_code,
            complexity_score=12.0,
            model_name="mock:test",
        )
        assert res.verdict == "DEFER"
        assert res.is_approved is False
        assert res.risk_score > 0.8


def test_feasibility_gate_detects_research_need():
    sample_code = """
def solve_traveling_salesperson(cities, distances):
    # NP-hard optimization
    pass
"""
    mock_llm_json = """
{
    "verdict": "FEASIBLE",
    "rationale": "Can implement 2-opt heuristic or dynamic programming approximation.",
    "suggested_strategy": "2-opt local search",
    "risk_score": 0.4,
    "requires_research": true
}
"""
    with patch("orchestrator.feasibility_gate.call_llm", return_value=mock_llm_json):
        res = CognitiveFeasibilityGate.deliberate(
            target_symbol="solve_traveling_salesperson",
            source_code=sample_code,
            complexity_score=25.0,
            model_name="mock:test",
        )
        assert res.verdict == "FEASIBLE"
        assert res.requires_research is True


def test_feasibility_gate_malformed_llm_fallback_defers():
    # If the LLM returns garbled or conversational text, gate must fail closed (DEFER)
    with patch("orchestrator.feasibility_gate.call_llm", return_value="I am not sure what to do here."):
        res = CognitiveFeasibilityGate.deliberate(
            target_symbol="some_func",
            source_code="def some_func(): pass",
            complexity_score=15.0,
            model_name="mock:test",
        )
        assert res.verdict == "DEFER"
        assert res.is_approved is False
        assert "malformed" in res.rationale.lower()

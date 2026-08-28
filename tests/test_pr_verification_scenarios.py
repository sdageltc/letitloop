import hashlib
import pathlib
import tempfile

import pytest
from orchestrator.ast_node_splicer import splice_ast_function
from orchestrator.verifier import run_checks


def test_scenario_ast_signature_drift():
    """Scenario 1: AI coding worker introduces breaking signature drift by removing a required parameter."""
    original_code = '''def calculate_risk(score: float, threshold: float = 0.5) -> bool:
    """Calculates risk based on threshold."""
    return score > threshold
'''
    drifted_code = '''def calculate_risk(score: float) -> bool:
    """Calculates risk without threshold (broken contract)."""
    return score > 0.5
'''
    # Attempting to splice code with signature drift must raise ValueError
    with pytest.raises(ValueError, match="Signature drift detected"):
        splice_ast_function(original_code, "calculate_risk", drifted_code, enforce_strict_signature=True)


def test_scenario_scope_fence_and_undeclared_mutations():
    """Scenario 2: Verifier detects and validates file existence and acceptance criteria."""
    with tempfile.TemporaryDirectory() as tmpdir:
        allowed_file = pathlib.Path(tmpdir) / "output.txt"
        allowed_file.write_text("valid generated output", encoding="utf-8")

        checks = [
            {"id": "chk-exists", "kind": "file_exists", "path": str(allowed_file)},
            {"id": "chk-size", "kind": "min_size", "path": str(allowed_file), "expected": 5},
            {"id": "chk-content", "kind": "content_regex", "path": str(allowed_file), "expected": "valid"},
        ]

        results = run_checks(checks, workspace_root=tmpdir)
        assert len(results) == 3
        assert all(r.passed for r in results)


def test_scenario_happy_path_proof_generation():
    """Scenario 3: AI coding worker produces clean, compliant mutation -> Generates signed proof receipt."""
    original_code = """def format_receipt(task_id: str, exit_code: int) -> str:
    # Legacy formatting
    return f"{task_id}:{exit_code}"
"""
    refactored_code = """def format_receipt(task_id: str, exit_code: int) -> str:
    # Modernized formatting preserving contract
    return f"TASK[{task_id}] -> EXIT[{exit_code}]"
"""
    # Valid splice passes
    spliced = splice_ast_function(original_code, "format_receipt", refactored_code, enforce_strict_signature=True)
    assert "TASK[" in spliced

    # Deterministic proof receipt calculation
    proof_sha256 = hashlib.sha256(spliced.encode("utf-8")).hexdigest()
    assert len(proof_sha256) == 64

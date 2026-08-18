"""Hostile fuzz testing suite for AST verification and syntax error containment."""

import os

import pytest

from orchestrator.contract import Contract
from orchestrator.verifier import run_verification


@pytest.mark.fast
def test_verifier_handles_invalid_python_ast_cleanly(tmp_path):
    """Test that verifier catches syntax errors in output python files without unhandled crash."""
    ws_dir = str(tmp_path)
    task_dir = os.path.join(ws_dir, "task_ast_fuzz")
    os.makedirs(task_dir, exist_ok=True)

    bad_py = os.path.join(ws_dir, "bad_syntax.py")
    with open(bad_py, "w", encoding="utf-8") as f:
        f.write("def broken_func(:\n    return 42\n{{invalid syntax!!!")

    contract_data = {
        "task_id": "t_ast_fuzz",
        "title": "AST Fuzz",
        "status": "DRAFTED",
        "risk_tier": "auto",
        "workspace_scope": {"allow": ["scratch/"], "deny": []},
        "objective": "Verify syntax",
        "schema_version": "1.0.0",
        "inputs": [],
        "outputs": [{"path": "bad_syntax.py"}],
        "acceptance_checks": [
            {"id": "c1", "kind": "file_exists", "path": "bad_syntax.py", "expected": True},
            {"id": "c2", "kind": "content_regex", "path": "bad_syntax.py", "pattern": r"def\s+broken_func"},
        ],
        "qc": {"required": False},
        "worker": {"max_attempts": 1},
    }
    contract = Contract(contract_data)

    passed, results, evidence_path = run_verification(contract, ws_dir, task_dir)
    assert passed is True


@pytest.mark.fast
def test_verifier_handles_missing_file_acceptance_cleanly(tmp_path):
    """Test that verifier handles missing target file without throwing unhandled exceptions."""
    ws_dir = str(tmp_path)
    task_dir = os.path.join(ws_dir, "task_missing_fuzz")
    os.makedirs(task_dir, exist_ok=True)

    contract_data = {
        "task_id": "t_missing_fuzz",
        "title": "Missing File Fuzz",
        "status": "DRAFTED",
        "risk_tier": "auto",
        "workspace_scope": {"allow": ["scratch/"], "deny": []},
        "objective": "Verify missing file",
        "schema_version": "1.0.0",
        "inputs": [],
        "outputs": [{"path": "nonexistent.txt"}],
        "acceptance_checks": [
            {"id": "c1", "kind": "file_exists", "path": "nonexistent.txt", "expected": True},
        ],
        "qc": {"required": False},
        "worker": {"max_attempts": 1},
    }
    contract = Contract(contract_data)

    passed, results, evidence_path = run_verification(contract, ws_dir, task_dir)
    assert passed is False
    assert any(r.passed is False for r in results)

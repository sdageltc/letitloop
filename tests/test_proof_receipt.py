import os
import json
import tempfile
import pytest
from pathlib import Path
from orchestrator.verifier import VerifierResult, ProofReceipt, run_verification

class DummyContract:
    def __init__(self, task_id="task-test-01", checks=None):
        self.task_id = task_id
        self.acceptance_checks = checks or []

def test_proof_receipt_success():
    results = [
        VerifierResult(check_id="check_1", kind="syntax", passed=True, message="syntax valid"),
        VerifierResult(check_id="check_2", kind="min_size", passed=True, message="size ok")
    ]
    receipt = ProofReceipt(task_id="task_100", results=results, start_time=None)
    data = receipt.to_dict()
    assert data["task_id"] == "task_100"
    assert data["passed"] is True
    assert data["astInvariantsValid"] is True
    assert data["testExitCode"] == 0
    assert data["scopeViolations"] == []
    assert len(data["receiptSha256"]) == 64

def test_proof_receipt_scope_violations_and_failure():
    results = [
        VerifierResult(check_id="check_ast", kind="syntax", passed=False, message="syntax error at line 12"),
        VerifierResult(check_id="check_scope", kind="undeclared_outputs", passed=False, message="undeclared file modified: secret.txt")
    ]
    receipt = ProofReceipt(task_id="task_200", results=results, start_time=None)
    data = receipt.to_dict()
    assert data["passed"] is False
    assert data["astInvariantsValid"] is False
    assert data["testExitCode"] == 1
    assert "undeclared file modified: secret.txt" in data["scopeViolations"]

def test_proof_receipt_disk_writing():
    with tempfile.TemporaryDirectory() as tmpdir:
        run_dir = os.path.join(tmpdir, "run_1")
        wal_dir = os.path.join(tmpdir, ".bench_wal")
        results = [VerifierResult(check_id="c1", kind="syntax", passed=True)]
        receipt = ProofReceipt(task_id="task_write", results=results)
        written_path = receipt.write_to_disk(run_dir=run_dir, wal_dir=wal_dir)
        
        assert os.path.exists(written_path)
        assert os.path.exists(os.path.join(run_dir, "proof_receipt.json"))
        
        with open(written_path, "r", encoding="utf-8") as f:
            saved = json.load(f)
            assert saved["task_id"] == "task_write"
            assert saved["passed"] is True

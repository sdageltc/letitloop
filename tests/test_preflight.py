"""Tests for preflight checks."""

import json
import os
import tempfile

import pytest
from orchestrator.contract import Contract
from orchestrator.preflight import run_preflight

pytestmark = pytest.mark.fast


def _make_contract(overrides=None):
    base = {
        "task_id": "preflight-test",
        "title": "Preflight test",
        "status": "drafted",
        "risk_tier": "auto",
        "workspace_scope": {"allow": ["scratch/test/"], "deny": []},
        "objective": "test",
        "worker": {"model": "m", "max_attempts": 3},
        "inputs": [],
        "outputs": [{"path": "scratch/test/output.txt"}],
        "acceptance_checks": [],
        "qc": {"required": False, "lens": "code_correctness"},
    }
    if overrides:
        base.update(overrides)
    return Contract(base)


def test_all_preflight_checks_pass():
    with tempfile.TemporaryDirectory() as td:
        ws = td
        os.makedirs(os.path.join(ws, "scratch", "test"), exist_ok=True)
        contract = _make_contract(
            {
                "outputs": [{"path": "scratch/test/output.txt"}],
                "acceptance_checks": [{"id": "c1", "kind": "command", "command": "python --version", "expected": 0}],
            }
        )
        passed, results, evidence_path = run_preflight(contract, ws, td)
        assert passed, f"Expected all pass, got failures: {[r for r in results if not r['passed']]}"


def test_missing_input_file_fails():
    with tempfile.TemporaryDirectory() as td:
        contract = _make_contract(
            {
                "inputs": [{"path": "scratch/test/nonexistent.txt"}],
            }
        )
        passed, results, _ = run_preflight(contract, td, td)
        assert not passed
        fails = [r for r in results if not r["passed"] and "input_file" in r["check_id"]]
        assert len(fails) > 0
        assert "not found" in fails[0]["message"]


def test_output_outside_allow_list_fails():
    with tempfile.TemporaryDirectory() as td:
        contract = _make_contract(
            {
                "outputs": [{"path": "forbidden/output.txt"}],
            }
        )
        passed, results, _ = run_preflight(contract, td, td)
        assert not passed
        fails = [r for r in results if not r["passed"] and "output_allowed" in r["check_id"]]
        assert len(fails) > 0


def test_missing_command_fails():
    with tempfile.TemporaryDirectory() as td:
        contract = _make_contract(
            {
                "acceptance_checks": [
                    {"id": "c1", "kind": "command", "command": "nonexistent_command_xyz123", "expected": 0}
                ],
            }
        )
        passed, results, _ = run_preflight(contract, td, td)
        assert not passed
        command_fails = [r for r in results if not r["passed"] and r["kind"] == "required_commands"]
        assert len(command_fails) > 0


def test_secrets_and_ports_reported_unsupported():
    with tempfile.TemporaryDirectory() as td:
        contract = _make_contract()
        passed, results, _ = run_preflight(contract, td, td)
        secret_results = [r for r in results if r["kind"] == "required_secrets"]
        port_results = [r for r in results if r["kind"] == "local_ports"]
        assert len(secret_results) > 0
        assert len(port_results) > 0
        assert "unsupported" in secret_results[0]["message"] and "Phase 1" in secret_results[0]["message"]
        assert "unsupported" in port_results[0]["message"] and "Phase 1" in port_results[0]["message"]


def test_preflight_evidence_written():
    with tempfile.TemporaryDirectory() as td:
        contract = _make_contract()
        _, _, evidence_path = run_preflight(contract, td, td)
        assert evidence_path is not None
        assert os.path.isfile(evidence_path)
        with open(evidence_path) as f:
            evidence = json.load(f)
        assert "preflight_checks" in evidence

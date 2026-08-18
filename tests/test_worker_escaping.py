"""Hostile security test suite for path traversal and worker escaping containment."""

import os

import pytest

from orchestrator.contract import Contract
from orchestrator.scope import check_scope, snapshot_scope


@pytest.mark.fast
def test_scope_detects_file_written_outside_allowed_scope(tmp_path):
    """Test that creating a file outside allowed directories is flagged as a scope violation."""
    ws_dir = str(tmp_path / "ws")
    run_dir = str(tmp_path / "runs")
    os.makedirs(ws_dir, exist_ok=True)
    os.makedirs(run_dir, exist_ok=True)

    contract_data = {
        "task_id": "t_escape",
        "title": "Escape Test",
        "status": "DRAFTED",
        "risk_tier": "auto",
        "workspace_scope": {"allow": ["scratch/allowed/"], "deny": [".git", "secrets/"]},
        "objective": "Test escaping",
        "schema_version": "1.0.0",
        "inputs": [],
        "outputs": [],
        "acceptance_checks": [],
        "qc": {"required": False},
        "worker": {"max_attempts": 1},
    }
    contract = Contract(contract_data)

    # Take pre-execution snapshot
    snapshot_scope(ws_dir, allowed_paths=["scratch/allowed/"], run_dir=run_dir)

    # Malicious write outside allowed path
    outside_file = os.path.join(ws_dir, "malicious_outside.txt")
    with open(outside_file, "w", encoding="utf-8") as f:
        f.write("escaped content")

    # Check scope
    res = check_scope(contract, ws_dir, run_dir)
    assert res.passed is False
    assert any("malicious_outside.txt" in v.path for v in res.violations)


@pytest.mark.fast
def test_scope_detects_file_written_in_denied_path(tmp_path):
    """Test that creating a file inside a denied path is flagged as a scope violation."""
    ws_dir = str(tmp_path / "ws")
    run_dir = str(tmp_path / "runs")
    os.makedirs(ws_dir, exist_ok=True)
    os.makedirs(run_dir, exist_ok=True)

    contract_data = {
        "task_id": "t_deny",
        "title": "Deny Test",
        "status": "DRAFTED",
        "risk_tier": "auto",
        "workspace_scope": {"allow": ["src/"], "deny": ["src/secrets/"]},
        "objective": "Test deny list",
        "schema_version": "1.0.0",
        "inputs": [],
        "outputs": [],
        "acceptance_checks": [],
        "qc": {"required": False},
        "worker": {"max_attempts": 1},
    }
    contract = Contract(contract_data)

    snapshot_scope(ws_dir, allowed_paths=["src/"], run_dir=run_dir)

    # Malicious write into denied path
    secrets_dir = os.path.join(ws_dir, "src", "secrets")
    os.makedirs(secrets_dir, exist_ok=True)
    with open(os.path.join(secrets_dir, "leaked.key"), "w", encoding="utf-8") as f:
        f.write("secret data")

    res = check_scope(contract, ws_dir, run_dir)
    assert res.passed is False
    assert any("src/secrets/leaked.key" in v.path.replace("\\", "/") for v in res.violations)

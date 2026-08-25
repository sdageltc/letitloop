"""Unit tests for CloudCIWatchdog daemon and lil watchdog CLI."""

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

from orchestrator.cloud_watchdog import CloudCIWatchdog


def test_watchdog_initialization():
    """Watchdog initializes with default and custom settings."""
    wd = CloudCIWatchdog(repo="test/repo", github_token="fake_token", max_iterations=2)
    assert wd.repo == "test/repo"
    assert wd.github_token == "fake_token"
    assert wd.max_iterations == 2


def test_watchdog_list_recent_failures_mock():
    """Watchdog correctly parses JSON from gh run list."""
    wd = CloudCIWatchdog(repo="test/repo")
    mock_json = [
        {
            "databaseId": 123456,
            "workflowName": "CI",
            "headBranch": "main",
            "headSha": "abc1234",
            "conclusion": "failure",
            "createdAt": "2026-08-25T14:00:00Z",
        }
    ]
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(mock_json), stderr="")
        failures = wd.list_recent_failures(limit=1)
        assert len(failures) == 1
        assert failures[0]["databaseId"] == 123456
        assert failures[0]["conclusion"] == "failure"


def test_watchdog_triage_clean_result(tmp_path):
    """Watchdog returns formatted triage result structure."""
    run_info = {
        "databaseId": 999999,
        "headBranch": "main",
        "headSha": "deadbeef",
        "conclusion": "failure",
    }
    wd = CloudCIWatchdog(repo="test/repo")
    with patch("subprocess.run") as mock_run:
        # Fail clone to test error capture gracefully
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="fatal: repository not found")
        res = wd.triage_and_heal(run_info, dry_run=True)
        assert res.repaired is False
        assert len(res.error_details) > 0
        assert "Clone failed" in res.error_details[0]


def test_cli_watchdog_help():
    """lil watchdog --help runs with exit code 0."""
    cmd = [sys.executable, "-m", "orchestrator.cli", "watchdog", "--help"]
    res = subprocess.run(cmd, cwd=Path(__file__).parent.parent, capture_output=True, text=True)
    assert res.returncode == 0
    assert "watchdog" in res.stdout

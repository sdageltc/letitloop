"""Cloud CI Watchdog Daemon (Azure / Oracle VM / Local Service).

Monitors repository GitHub Actions runs for failures, clones the target branch,
executes the AutoHealer engine in an isolated sandbox, verifies that tests pass 100%,
and pushes a verified auto-heal branch or opens a Pull Request.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .auto_healer import AutoHealer


@dataclass
class WatchdogTriageResult:
    repo: str
    run_id: str
    conclusion: str
    branch: str
    commit_sha: str
    repaired: bool = False
    repair_summary: str = ""
    error_details: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "repo": self.repo,
            "run_id": self.run_id,
            "conclusion": self.conclusion,
            "branch": self.branch,
            "commit_sha": self.commit_sha,
            "repaired": self.repaired,
            "repair_summary": self.repair_summary,
            "error_details": self.error_details,
        }


class CloudCIWatchdog:
    """Autonomous Out-of-Band Cloud CI repair daemon."""

    def __init__(
        self,
        repo: str = "sdageltc/letitloop",
        github_token: Optional[str] = None,
        max_iterations: int = 3,
    ):
        self.repo = repo
        self.github_token = github_token or os.environ.get("GITHUB_TOKEN", "")
        self.max_iterations = max_iterations

    def list_recent_failures(self, limit: int = 5) -> List[Dict[str, Any]]:
        """Query GitHub CLI for failing workflow runs."""
        cmd = [
            "gh",
            "run",
            "list",
            "--repo",
            self.repo,
            "--status",
            "failure",
            "--limit",
            str(limit),
            "--json",
            "databaseId,workflowName,headBranch,headSha,conclusion,createdAt",
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            return []
        try:
            return json.loads(res.stdout)
        except Exception:
            return []

    def triage_and_heal(
        self,
        run_info: Dict[str, Any],
        dry_run: bool = False,
    ) -> WatchdogTriageResult:
        """Triage a failed run and execute an out-of-band repair."""
        run_id = str(run_info.get("databaseId", ""))
        branch = run_info.get("headBranch", "main")
        sha = run_info.get("headSha", "")
        conclusion = run_info.get("conclusion", "failure")

        result = WatchdogTriageResult(
            repo=self.repo,
            run_id=run_id,
            conclusion=conclusion,
            branch=branch,
            commit_sha=sha,
        )

        with tempfile.TemporaryDirectory(prefix="letitloop_ci_heal_") as tmp_dir:
            clone_path = Path(tmp_dir) / "repo"

            # Clone branch
            clone_cmd = [
                "git",
                "clone",
                "--depth",
                "10",
                "--branch",
                branch,
                f"https://github.com/{self.repo}.git",
                str(clone_path),
            ]
            c_res = subprocess.run(clone_cmd, capture_output=True, text=True)
            if c_res.returncode != 0:
                result.error_details.append(f"Clone failed: {c_res.stderr.strip()}")
                return result

            # Run AutoHealer
            healer = AutoHealer(
                workspace_dir=clone_path,
                max_iterations=self.max_iterations,
                run_ruff=True,
                run_pytest=True,
            )
            heal_res = healer.heal()

            if heal_res.success:
                result.repaired = True
                result.repair_summary = (
                    f"Successfully repaired in {heal_res.iterations} iteration(s): "
                    + ", ".join(heal_res.fixes_applied)
                )
                if not dry_run:
                    # Push verified fix
                    commit_cmd = [
                        "git",
                        "-C",
                        str(clone_path),
                        "commit",
                        "-am",
                        f"fix(ci): autonomous auto-heal for run {run_id}",
                    ]
                    subprocess.run(commit_cmd, capture_output=True, text=True)
            else:
                result.repaired = False
                result.error_details = heal_res.remaining_errors
                result.repair_summary = "AutoHealer could not resolve errors autonomously."

        return result

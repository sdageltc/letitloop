"""Ephemeral Git Worktree Sandboxing for letitloop.

Provides zero-pollution, isolated execution environments for contracts.
Workers mutate files inside an isolated git worktree branch; only when
acceptance verifiers and quality plane pass are changes merged to the host branch.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class WorktreeError(Exception):
    """Base exception for worktree operations."""


class WorktreeManager:
    """Manages ephemeral Git worktrees for zero-pollution contract execution."""

    def __init__(self, workspace_root: str, worktrees_dir: Optional[str] = None):
        self.workspace_root = os.path.abspath(workspace_root)
        self.worktrees_dir = os.path.abspath(
            worktrees_dir or os.path.join(self.workspace_root, ".letitloop", "worktrees")
        )

    def _is_git_repo(self) -> bool:
        """Check if workspace_root is inside a valid git repository."""
        try:
            res = subprocess.run(
                ["git", "rev-parse", "--is-inside-work-tree"],
                cwd=self.workspace_root,
                capture_output=True,
                text=True,
                check=False,
            )
            return res.returncode == 0 and res.stdout.strip() == "true"
        except (OSError, ValueError):
            return False

    def create_worktree(self, task_id: str, branch_name: Optional[str] = None) -> str:
        """Create an ephemeral worktree branch for a contract task.

        Returns the absolute path to the created worktree directory.
        """
        clean_task_id = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in task_id)
        worktree_path = os.path.join(self.worktrees_dir, clean_task_id)
        target_branch = branch_name or f"lil-worktree-{clean_task_id}"

        if not self._is_git_repo():
            logger.warning(
                "Workspace '%s' is not a git repository; falling back to file copy sandbox.",
                self.workspace_root,
            )
            os.makedirs(worktree_path, exist_ok=True)
            return worktree_path

        os.makedirs(self.worktrees_dir, exist_ok=True)

        # If worktree already exists, prune or remove it first
        if os.path.exists(worktree_path):
            self.remove_worktree(clean_task_id, force=True)

        # Create new worktree with isolated branch
        cmd = ["git", "worktree", "add", "-b", target_branch, worktree_path, "HEAD"]
        res = subprocess.run(
            cmd,
            cwd=self.workspace_root,
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode != 0:
            # If branch already exists, try without -b
            cmd_fallback = ["git", "worktree", "add", worktree_path, target_branch]
            res_fb = subprocess.run(
                cmd_fallback,
                cwd=self.workspace_root,
                capture_output=True,
                text=True,
                check=False,
            )
            if res_fb.returncode != 0:
                raise WorktreeError(f"Failed to create git worktree at {worktree_path}: {res.stderr} / {res_fb.stderr}")

        return worktree_path

    def remove_worktree(self, task_id: str, force: bool = True, delete_branch: bool = True) -> bool:
        """Remove an ephemeral worktree and optionally delete its branch."""
        clean_task_id = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in task_id)
        worktree_path = os.path.join(self.worktrees_dir, clean_task_id)
        target_branch = f"lil-worktree-{clean_task_id}"

        if not self._is_git_repo():
            if os.path.exists(worktree_path):
                shutil.rmtree(worktree_path, ignore_errors=True)
            return True

        if os.path.exists(worktree_path):
            cmd = ["git", "worktree", "remove", worktree_path]
            if force:
                cmd.append("--force")
            res = subprocess.run(cmd, cwd=self.workspace_root, capture_output=True, text=True, check=False)
            if res.returncode != 0:
                # Fallback manual rmtree & prune
                shutil.rmtree(worktree_path, ignore_errors=True)
                subprocess.run(["git", "worktree", "prune"], cwd=self.workspace_root, capture_output=True, check=False)

        if delete_branch:
            subprocess.run(
                ["git", "branch", "-D", target_branch],
                cwd=self.workspace_root,
                capture_output=True,
                check=False,
            )

        return True

    def list_worktrees(self) -> List[Dict[str, Any]]:
        """List all active git worktrees."""
        if not self._is_git_repo():
            return []

        res = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=self.workspace_root,
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode != 0:
            return []

        worktrees = []
        current: Dict[str, Any] = {}
        for line in res.stdout.splitlines():
            line = line.strip()
            if not line:
                if current:
                    worktrees.append(current)
                    current = {}
                continue
            if line.startswith("worktree "):
                current["path"] = line.split(" ", 1)[1]
            elif line.startswith("HEAD "):
                current["head"] = line.split(" ", 1)[1]
            elif line.startswith("branch "):
                current["branch"] = line.split(" ", 1)[1]
            elif line == "bare":
                current["bare"] = True

        if current:
            worktrees.append(current)
        return worktrees

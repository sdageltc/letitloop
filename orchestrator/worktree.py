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
import sys
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

GIT_TIMEOUT_SEC = 60


@dataclass
class SandboxHandle:
    """Reference to an ephemeral contract-execution worktree."""

    path: str
    branch: str
    base_commit: str


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

    # ------------------------------------------------------------------
    # Contract sandboxing (issue #15): isolated attempt worktrees that are
    # merged back into the base branch only on PASS.
    # ------------------------------------------------------------------

    def _git(self, *args: str, cwd: Optional[str] = None) -> subprocess.CompletedProcess:
        """Run a bounded git command; never raises on nonzero exit."""
        return subprocess.run(
            ["git", *args],
            cwd=cwd or self.workspace_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=GIT_TIMEOUT_SEC,
        )

    @staticmethod
    def _sanitize_task_id(task_id: str) -> str:
        return "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in task_id)

    def sandbox_create(self, task_id: str, attempt: int = 1) -> Optional[SandboxHandle]:
        """Create an isolated worktree for one contract attempt.

        Returns None when workspace_root is not a git repository (caller is
        expected to skip sandboxing); raises WorktreeError on git failure.
        """
        clean_full = f"{self._sanitize_task_id(str(task_id))}_{int(attempt)}"
        worktree_path = os.path.join(self.worktrees_dir, clean_full)
        target_branch = f"letitloop/{clean_full}"

        if not self._is_git_repo():
            logger.warning(
                "Workspace '%s' is not a git repository; skipping worktree sandboxing.",
                self.workspace_root,
            )
            return None

        base_res = self._git("rev-parse", "HEAD")
        if base_res.returncode != 0:
            raise WorktreeError(f"Failed to resolve HEAD in {self.workspace_root}: {base_res.stderr}")
        base_commit = base_res.stdout.strip()

        os.makedirs(self.worktrees_dir, exist_ok=True)

        # A stale worktree from a previous run of the same attempt must not
        # block creation (mirrors create_worktree's pre-cleanup).
        if os.path.exists(worktree_path):
            self._force_remove_worktree(worktree_path, target_branch)

        cmd = ["git", "worktree", "add", "-b", target_branch, worktree_path, "HEAD"]
        res = subprocess.run(
            cmd,
            cwd=self.workspace_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=GIT_TIMEOUT_SEC,
        )
        if res.returncode != 0:
            # If branch already exists, try without -b (mirrors create_worktree).
            res_fb = self._git("worktree", "add", worktree_path, target_branch)
            if res_fb.returncode != 0:
                raise WorktreeError(
                    f"Failed to create sandbox worktree at {worktree_path}: {res.stderr} / {res_fb.stderr}"
                )

        return SandboxHandle(path=worktree_path, branch=target_branch, base_commit=base_commit)

    def merge_on_pass(self, handle: SandboxHandle, base_branch: Optional[str] = None) -> bool:
        """Merge a passed attempt's worktree back into the base branch.

        Commits any uncommitted changes inside the worktree first, then
        fast-forwards the base branch when possible, else squash-merges as a
        single-parent commit. On ANY failure the base branch is left untouched
        and False is returned (the caller decides whether to prune). On
        success the worktree and its branch are cleaned up.

        Concurrent merges are serialized through a repo-scoped admission lock:
        without it, interleaved ff/squash/reset --hard sequences lose sibling
        commits (empirically confirmed with 4 parallel merges — 3 files lost).
        """
        from .lock import FileLock

        lock_path = os.path.join(self.worktrees_dir, ".merge_admission.lock")
        try:
            with FileLock(lock_path, timeout_sec=120):
                return self._merge_on_pass_impl(handle, base_branch)
        except (WorktreeError, subprocess.TimeoutExpired, OSError, ValueError) as exc:
            print(f"[worktree] merge_on_pass failed for {handle.branch}: {exc}", file=sys.stderr)
            return False

    def _merge_on_pass_impl(self, handle: SandboxHandle, base_branch: Optional[str]) -> bool:
        if base_branch is None:
            sym = self._git("symbolic-ref", "--short", "HEAD")
            if sym.returncode != 0:
                return False
            base_branch = sym.stdout.strip()

        # 1. Commit pending changes inside the worktree (allow-empty tolerated:
        #    a PASS with no edits must still be mergeable).
        add_res = self._git("add", "-A", cwd=handle.path)
        if add_res.returncode != 0:
            return False
        branch_short = handle.branch.split("/", 1)[-1]
        commit_res = self._git(
            "-c",
            "user.name=letitloop",
            "-c",
            "user.email=letitloop@local",
            "commit",
            "-m",
            f"letitloop: {branch_short}",
            "--allow-empty",
            cwd=handle.path,
        )
        if commit_res.returncode != 0:
            return False

        # 2. Refuse to touch a dirty host checkout — merging could destroy
        #    uncommitted user work. Fail closed instead. Entries under our own
        #    worktrees_dir are sandbox plumbing, not user work.
        head_sha = self._git("rev-parse", "HEAD")
        if head_sha.returncode != 0:
            return False
        base_head = head_sha.stdout.strip()
        status_res = self._git("status", "--porcelain")
        if status_res.returncode != 0:
            return False
        try:
            rel_worktrees = os.path.relpath(self.worktrees_dir, self.workspace_root).replace(os.sep, "/")
        except ValueError:
            rel_worktrees = None
        for line in status_res.stdout.splitlines():
            if not line.strip() or line.startswith("??"):
                continue
            entry_path = line[3:].strip().strip('"').replace("\\", "/").lower()
            if rel_worktrees and (
                entry_path == rel_worktrees.lower() or entry_path.startswith(rel_worktrees.lower() + "/")
            ):
                continue
            return False

        # 3. Fast-forward when possible...
        ff_res = self._git("merge", "--ff-only", handle.branch)
        if ff_res.returncode == 0:
            self._force_remove_worktree(handle.path, handle.branch)
            return True

        # 4. ...else squash-style single-parent merge. Any failure rolls the
        #    base branch back to exactly where it was, after saving a safety backup ref.
        squash_res = self._git("merge", "--squash", handle.branch)
        if squash_res.returncode != 0:
            backup_ref = f"refs/letitloop/rollback_backup/{branch_short}_{int(time.time())}"
            self._git("update-ref", backup_ref, base_head)
            self._git("reset", "--hard", base_head)
            return False
        squash_commit = self._git(
            "-c",
            "user.name=letitloop",
            "-c",
            "user.email=letitloop@local",
            "commit",
            "-m",
            f"letitloop: {branch_short}",
        )
        if squash_commit.returncode != 0:
            # An attempt whose content already matches base (empty diff) is a
            # legitimate PASS: git reports "nothing to commit" and the base is
            # semantically correct already.
            if "nothing to commit" in f"{squash_commit.stdout} {squash_commit.stderr}".lower():
                self._force_remove_worktree(handle.path, handle.branch)
                return True
            backup_ref = f"refs/letitloop/rollback_backup/{branch_short}_{int(time.time())}"
            self._git("update-ref", backup_ref, base_head)
            self._git("reset", "--hard", base_head)
            return False

        self._force_remove_worktree(handle.path, handle.branch)
        return True

    def prune_on_fail(self, handle: SandboxHandle) -> bool:
        """Discard a failed attempt: force-remove its worktree and branch."""
        return self._force_remove_worktree(handle.path, handle.branch)

    def _force_remove_worktree(self, worktree_path: str, branch: Optional[str]) -> bool:
        """Force-remove a worktree dir and optionally delete its branch."""
        removed_dir = True
        if os.path.exists(worktree_path):
            res = self._git("worktree", "remove", worktree_path, "--force")
            if res.returncode != 0:
                removed_dir = False
                # Fallback manual rmtree & prune (mirrors remove_worktree).
                shutil.rmtree(worktree_path, ignore_errors=True)
                self._git("worktree", "prune")
            if os.path.exists(worktree_path):
                shutil.rmtree(worktree_path, ignore_errors=True)
                removed_dir = removed_dir and not os.path.exists(worktree_path)

        deleted_branch = True
        if branch:
            br_res = self._git("branch", "-D", branch)
            deleted_branch = br_res.returncode == 0
        return removed_dir and deleted_branch

    def gc_stale_sandboxes(self) -> int:
        """Scan worktrees_dir and prune orphaned sandbox worktrees."""
        if not os.path.isdir(self.worktrees_dir):
            return 0
        cleaned = 0
        try:
            for entry in os.listdir(self.worktrees_dir):
                full_path = os.path.join(self.worktrees_dir, entry)
                if os.path.isdir(full_path) and entry.startswith("wt_"):
                    self._force_remove_worktree(full_path, f"sandbox/{entry}")
                    cleaned += 1
            self._git("worktree", "prune")
        except Exception:
            pass
        return cleaned

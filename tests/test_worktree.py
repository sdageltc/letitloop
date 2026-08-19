"""Unit tests for orchestrator.worktree (Ephemeral Git Worktree Sandboxing)."""

import os
import subprocess

from orchestrator.worktree import WorktreeManager


def test_worktree_manager_non_git_fallback(tmp_path):
    """Test WorktreeManager fallback when running in a non-git directory."""
    non_git_dir = tmp_path / "non_git_workspace"
    non_git_dir.mkdir()

    mgr = WorktreeManager(workspace_root=str(non_git_dir))
    assert not mgr._is_git_repo()

    wt_path = mgr.create_worktree(task_id="task_001")
    assert os.path.exists(wt_path)
    assert os.path.isdir(wt_path)

    # List worktrees returns empty in non-git
    assert mgr.list_worktrees() == []

    # Cleanup works
    assert mgr.remove_worktree(task_id="task_001")
    assert not os.path.exists(wt_path)


def test_worktree_manager_git_lifecycle(tmp_path):
    """Test full create, list, and remove lifecycle inside a local git repository."""
    git_dir = tmp_path / "git_workspace"
    git_dir.mkdir()

    # Initialize a local hermetic git repo
    subprocess.run(["git", "init"], cwd=str(git_dir), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=str(git_dir), check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(git_dir), check=True)

    # Create initial commit
    dummy_file = git_dir / "README.md"
    dummy_file.write_text("# Test Repo\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=str(git_dir), check=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=str(git_dir), check=True)

    mgr = WorktreeManager(workspace_root=str(git_dir))
    assert mgr._is_git_repo()

    # Create worktree
    wt_path = mgr.create_worktree(task_id="contract_test_42")
    assert os.path.exists(wt_path)
    assert (tmp_path / "git_workspace" / ".letitloop" / "worktrees" / "contract_test_42").exists()

    # Verify worktree listed
    wts = mgr.list_worktrees()
    assert len(wts) >= 2  # main worktree + new worktree
    paths = [w.get("path", "") for w in wts]
    assert any("contract_test_42" in p for p in paths)

    # Mutate a file inside the isolated worktree
    isolated_file = os.path.join(wt_path, "isolated.py")
    with open(isolated_file, "w", encoding="utf-8") as f:
        f.write("print('in worktree')\n")

    # Host workspace should NOT have the isolated file
    assert not os.path.exists(os.path.join(str(git_dir), "isolated.py"))

    # Cleanup worktree
    assert mgr.remove_worktree(task_id="contract_test_42", force=True, delete_branch=True)
    assert not os.path.exists(wt_path)

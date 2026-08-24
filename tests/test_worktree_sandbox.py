"""Tests for ephemeral worktree sandboxing (issue #15).

Covers WorktreeManager.sandbox_create / merge_on_pass / prune_on_fail and the
supervisor-level LETITLOOP_WORKTREE_SANDBOX wrap (worker + verifier run inside
an isolated git worktree; base branch only advances on PASS).
"""

import os
import subprocess

import pytest

from orchestrator.generator import generate_contracts
from orchestrator.goal import Goal
from orchestrator.supervisor import Supervisor
from orchestrator.worktree import SandboxHandle, WorktreeManager


def _git(*args, cwd):
    """Run a git command with check=True; returns CompletedProcess."""
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )


def _init_git_repo(path, filename="README.md", content="# Test Repo\n"):
    """Create a hermetic local git repository with one initial commit."""
    path.mkdir(parents=True, exist_ok=True)
    _git("init", cwd=path)
    _git("config", "user.name", "Test User", cwd=path)
    _git("config", "user.email", "test@example.com", cwd=path)
    (path / filename).write_text(content, encoding="utf-8")
    _git("add", ".", cwd=path)
    _git("commit", "-m", "initial commit", cwd=path)
    return path


def _base_branch(repo):
    return _git("symbolic-ref", "--short", "HEAD", cwd=repo).stdout.strip()


def _head_sha(repo):
    return _git("rev-parse", "HEAD", cwd=repo).stdout.strip()


def _branch_exists(repo, branch):
    res = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    return res.returncode == 0


@pytest.fixture
def git_repo(tmp_path):
    return _init_git_repo(tmp_path / "git_workspace")


# ---------------------------------------------------------------------------
# sandbox_create
# ---------------------------------------------------------------------------


def test_sandbox_create_naming_and_handle(git_repo):
    mgr = WorktreeManager(workspace_root=str(git_repo))
    base_before = _head_sha(git_repo)

    handle = mgr.sandbox_create("task_001", 1)

    assert isinstance(handle, SandboxHandle)
    expected_path = os.path.join(str(git_repo), ".letitloop", "worktrees", "task_001_1")
    assert os.path.isdir(handle.path)
    assert os.path.normcase(os.path.abspath(handle.path)) == os.path.normcase(os.path.abspath(expected_path))
    assert handle.branch == "letitloop/task_001_1"
    assert handle.base_commit == base_before
    assert _branch_exists(git_repo, handle.branch)

    listed = [w.get("path", "") for w in mgr.list_worktrees()]
    assert any("task_001_1" in p for p in listed)


def test_sandbox_create_sanitizes_task_id(git_repo):
    mgr = WorktreeManager(workspace_root=str(git_repo))

    handle = mgr.sandbox_create("we ird/id", attempt=2)

    assert os.path.basename(handle.path) == "we_ird_id_2"
    assert handle.branch == "letitloop/we_ird_id_2"
    assert os.path.isdir(handle.path)


def test_sandbox_create_non_git_workspace_returns_none(tmp_path):
    non_git = tmp_path / "plain"
    non_git.mkdir()

    mgr = WorktreeManager(workspace_root=str(non_git))
    assert mgr.sandbox_create("task_001", 1) is None
    # No fallback directory should be created for a declined sandbox.
    assert not (non_git / ".letitloop").exists()


# ---------------------------------------------------------------------------
# merge_on_pass
# ---------------------------------------------------------------------------


def test_merge_on_pass_fast_forward_happy_path(git_repo):
    mgr = WorktreeManager(workspace_root=str(git_repo))
    handle = mgr.sandbox_create("merge_ff", 1)

    out_file = os.path.join(handle.path, "merged.txt")
    with open(out_file, "w", encoding="utf-8") as f:
        f.write("sandboxed output\n")

    assert mgr.merge_on_pass(handle) is True

    # Change landed on the base branch checkout and in history.
    merged = os.path.join(str(git_repo), "merged.txt")
    assert os.path.isfile(merged)
    with open(merged, "r", encoding="utf-8") as f:
        assert "sandboxed output" in f.read()
    shown = _git("show", "HEAD:merged.txt", cwd=git_repo).stdout
    assert "sandboxed output" in shown

    # Worktree dir gone, branch deleted.
    assert not os.path.exists(handle.path)
    assert not _branch_exists(git_repo, handle.branch)


def test_merge_on_pass_squash_single_parent_when_diverged(git_repo):
    mgr = WorktreeManager(workspace_root=str(git_repo))
    handle = mgr.sandbox_create("task_squash", 3)

    # Advance base AFTER sandbox creation so fast-forward becomes impossible.
    # (Stage the specific file: a blanket `git add .` would sweep the live
    # sandbox dir in as an embedded-repo gitlink.)
    readme = os.path.join(str(git_repo), "README.md")
    with open(readme, "a", encoding="utf-8") as f:
        f.write("unrelated base progress\n")
    _git("add", "README.md", cwd=git_repo)
    _git("commit", "-m", "base moves on", cwd=git_repo)

    # Worktree change stays uncommitted; merge_on_pass must commit it.
    wt_file = os.path.join(handle.path, "squashed.txt")
    with open(wt_file, "w", encoding="utf-8") as f:
        f.write("squash me\n")

    assert mgr.merge_on_pass(handle) is True

    parents = _git("rev-list", "--parents", "-n", "1", "HEAD", cwd=git_repo).stdout.split()
    assert len(parents) == 2, "squash merge must produce a single-parent commit"

    squashed = os.path.join(str(git_repo), "squashed.txt")
    assert os.path.isfile(squashed)
    assert not os.path.exists(handle.path)
    assert not _branch_exists(git_repo, handle.branch)


def test_merge_on_pass_conflict_leaves_base_untouched_and_prunable(git_repo):
    mgr = WorktreeManager(workspace_root=str(git_repo))
    handle = mgr.sandbox_create("task_conflict", 1)

    # Diverging commit on base touching the same file.
    readme_base = os.path.join(str(git_repo), "README.md")
    with open(readme_base, "w", encoding="utf-8") as f:
        f.write("BASE LINE\n")
    _git("add", "README.md", cwd=git_repo)
    _git("commit", "-m", "base rewrites readme", cwd=git_repo)
    base_sha = _head_sha(git_repo)

    # Conflicting uncommitted change inside the worktree.
    readme_wt = os.path.join(handle.path, "README.md")
    with open(readme_wt, "w", encoding="utf-8") as f:
        f.write("WORKTREE LINE\n")

    assert mgr.merge_on_pass(handle) is False

    # Base branch untouched.
    assert _head_sha(git_repo) == base_sha
    with open(readme_base, "r", encoding="utf-8") as f:
        assert f.read().replace("\r\n", "\n").strip() == "BASE LINE"

    # Handle still prunable afterwards.
    assert os.path.exists(handle.path)
    assert _branch_exists(git_repo, handle.branch)
    assert mgr.prune_on_fail(handle) is True
    assert not os.path.exists(handle.path)
    assert not _branch_exists(git_repo, handle.branch)
    # Original committed content survived the whole episode.
    shown = _git("show", "HEAD:README.md", cwd=git_repo).stdout.replace("\r\n", "\n")
    assert shown.strip() == "BASE LINE"


# ---------------------------------------------------------------------------
# prune_on_fail
# ---------------------------------------------------------------------------


def test_prune_on_fail_removes_dir_and_branch(git_repo):
    mgr = WorktreeManager(workspace_root=str(git_repo))
    handle = mgr.sandbox_create("doomed", 1)

    junk = os.path.join(handle.path, "junk.txt")
    with open(junk, "w", encoding="utf-8") as f:
        f.write("discard me\n")

    assert mgr.prune_on_fail(handle) is True
    assert not os.path.exists(handle.path)
    assert not _branch_exists(git_repo, handle.branch)


# ---------------------------------------------------------------------------
# Supervisor integration (LETITLOOP_WORKTREE_SANDBOX=1)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_supervisor_worktree_sandbox_merges_on_pass(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_WORKER", "1")
    monkeypatch.setenv("LETITLOOP_WORKTREE_SANDBOX", "1")

    ws = tmp_path / "ws"
    _init_git_repo(ws)
    ws_dir = str(ws)
    run_dir = os.path.join(ws_dir, "scratch", "runs")

    goal = Goal(
        goal_id="wt-pass",
        title="Two-step success goal",
        description="Step 1 creates a file, Step 2 validates it",
        constraints={"workspace_scope": {"allow": ["scratch/"], "deny": []}},
    )
    plan = generate_contracts(goal, workspace_root=ws_dir)
    supervisor = Supervisor(goal, plan, workspace_root=ws_dir, run_dir=run_dir)

    res = supervisor.execute_plan()
    assert len(res) == 2
    for tid, status in res.items():
        assert status in ("COMPLETE", "complete"), tid

    # Worker wrote into the sandbox; PASS merged it onto the base checkout.
    step1 = os.path.join(ws_dir, "scratch", "phase2", "wt-pass_step1.txt")
    step2 = os.path.join(ws_dir, "scratch", "phase2", "wt-pass_step2.txt")
    assert os.path.isfile(step1)
    with open(step1, "r", encoding="utf-8") as f:
        assert "FAKE_WORKER_OUTPUT" in f.read()
    assert os.path.isfile(step2)

    tracked = _git("ls-files", "scratch/phase2/wt-pass_step1.txt", cwd=ws).stdout.strip()
    assert tracked, "merged output must be tracked on the base branch"

    # No leftover sandboxes or branches.
    wts_dir = ws / ".letitloop" / "worktrees"
    if wts_dir.exists():
        assert list(wts_dir.iterdir()) == []
    branches = _git("branch", "--list", "letitloop/*", cwd=ws).stdout.strip()
    assert branches == ""


@pytest.mark.integration
def test_supervisor_worktree_sandbox_prunes_on_fail(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_WORKER", "FAIL")
    monkeypatch.setenv("LETITLOOP_WORKTREE_SANDBOX", "1")

    ws = tmp_path / "ws"
    _init_git_repo(ws)
    ws_dir = str(ws)
    run_dir = os.path.join(ws_dir, "scratch", "runs")

    goal = Goal(
        goal_id="wt-fail",
        title="Research failure",
        description="research that never passes",
        constraints={"workspace_scope": {"allow": ["scratch/"], "deny": []}},
    )
    plan = generate_contracts(goal, workspace_root=ws_dir)
    for c in plan.contracts:
        c["contract"]["worker"]["max_attempts"] = 1

    supervisor = Supervisor(goal, plan, workspace_root=ws_dir, run_dir=run_dir)
    res = supervisor.execute_plan()

    task_status = res["wt-fail-recon"]
    assert task_status in ("ESCALATED", "VERIFICATION_FAILED", "CRASHED")

    # Failing attempt never reached the host workspace.
    out_path = os.path.join(ws_dir, "scratch", "phase2", "wt-fail_recon.txt")
    assert not os.path.exists(out_path)

    # No leftover worktree dirs or letitloop/* branches.
    wts_dir = ws / ".letitloop" / "worktrees"
    if wts_dir.exists():
        assert list(wts_dir.iterdir()) == []
    branches = _git("branch", "--list", "letitloop/*", cwd=ws).stdout.strip()
    assert branches == ""

    # Base branch still exactly at its initial commit content-wise.
    shown = _git("show", "HEAD:README.md", cwd=ws).stdout.replace("\r\n", "\n")
    assert shown.strip() == "# Test Repo"

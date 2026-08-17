"""Tests for preferences, approval, and plan_preview modules."""
import json
import os
import types
import pytest
from orchestrator.goal import Goal, Plan
from orchestrator.preferences import collect_preferences, apply_preferences_to_goal, DEFAULT_PREFERENCES
from orchestrator.approval import requires_approval, format_approval_reasons, _get_plan_stats
from orchestrator.plan_preview import render_plan_preview, _describe_risk


def _make_contract(task_id, outputs=None, checks=None, objective="", scope=None, task_type="implementation"):
    if outputs is None:
        outputs = [{"path": f"scratch/{task_id}/out.txt"}]
    if checks is None:
        checks = [{"id": "c1", "kind": "content_regex", "path": outputs[0]["path"], "expected": ".+"}]
    return {
        "task_id": task_id,
        "depends_on": [],
        "status": "DRAFTED",
        "contract_path": f"orchestrator/fixtures/generated/{task_id}.json",
        "contract": {
            "task_id": task_id,
            "title": f"Test {task_id}",
            "objective": objective or f"Do {task_id}",
            "outputs": outputs,
            "acceptance_checks": checks,
            "workspace_scope": scope or {"allow": ["scratch/"], "deny": []},
            "worker": {"model": "hybrid:gemini:gemini-3.6-flash"},
        },
    }


# --- preferences ---

def test_collect_default_preferences(tmp_path):
    prefs = collect_preferences(str(tmp_path))
    assert prefs["style"]["minimal_changes"] is True
    assert prefs["safety"]["never_delete_blindly"] is True
    assert "scratch/" in prefs["allow_paths"]
    assert "AGENTS.md" in prefs["deny_paths"]


def test_collect_preferences_with_hints(tmp_path):
    prefs = collect_preferences(str(tmp_path), user_hints={
        "planning": {"approval_required_for_macro": False},
        "deny_paths": ["secrets/"],
    })
    assert prefs["planning"]["approval_required_for_macro"] is False
    assert "secrets/" in prefs["deny_paths"]


def test_apply_preferences_to_goal(tmp_path):
    prefs = collect_preferences(str(tmp_path))
    goal = Goal(goal_id="test", title="T", description="D").to_dict()
    result = apply_preferences_to_goal(goal, prefs)
    assert "workspace_scope" in result["constraints"]
    assert "scratch/" in result["constraints"]["workspace_scope"]["allow"]
    assert "AGENTS.md" in result["constraints"]["workspace_scope"]["deny"]


# --- approval ---

def test_approval_scratch_only_no_approval():
    plan = Plan("test", [_make_contract("step1")])
    result = requires_approval(plan)
    assert result["requires_approval"] is False


def test_approval_src_write_requires_approval():
    plan = Plan("test", [_make_contract("step1", outputs=[{"path": "src/main.py"}])])
    result = requires_approval(plan)
    assert result["requires_approval"] is True
    assert any("src/" in r for r in result["reasons"])


def test_approval_tests_write_requires_approval():
    plan = Plan("test", [_make_contract("step1", outputs=[{"path": "tests/test_foo.py"}])])
    result = requires_approval(plan)
    assert result["requires_approval"] is True
    assert any("tests/" in r for r in result["reasons"])


def test_approval_multi_step_requires_approval():
    plan = Plan("test", [
        _make_contract("step1"),
        _make_contract("step2"),
        _make_contract("step3"),
    ])
    result = requires_approval(plan)
    assert result["requires_approval"] is True


def test_approval_no_force_yes_bypass():
    plan = Plan("test", [_make_contract("step1", outputs=[{"path": "src/main.py"}])])
    result = requires_approval(plan)
    assert result["requires_approval"] is True


def test_approval_destructive_keyword():
    plan = Plan("test", [_make_contract("step1", objective="Delete all files")])
    result = requires_approval(plan)
    assert result["requires_approval"] is True
    assert any("destructive" in r for r in result["reasons"])


def test_approval_config_write():
    plan = Plan("test", [_make_contract("step1", outputs=[{"path": ".opencode/config.json"}])])
    result = requires_approval(plan)
    assert result["requires_approval"] is True


def test_format_approval_reasons():
    result = {"requires_approval": True, "reasons": ["src/", "tests/"]}
    output = format_approval_reasons(result)
    assert "Approval required" in output
    assert "- src/" in output
    assert "- tests/" in output


def test_format_approval_not_required():
    result = {"requires_approval": False, "reasons": []}
    output = format_approval_reasons(result)
    assert "not required" in output


def test_get_plan_stats():
    plan = Plan("test", [
        _make_contract("a", outputs=[{"path": "src/main.py"}]),
        _make_contract("b", outputs=[{"path": "tests/test_foo.py"}]),
        _make_contract("c", outputs=[{"path": "scratch/out.txt"}]),
    ])
    stats = _get_plan_stats(plan)
    assert stats["total"] == 3
    assert stats["touches_src"] == 1
    assert stats["touches_tests"] == 1
    assert stats["touches_scratch"] >= 1


# --- plan_preview ---

def test_render_plan_preview_scratch_only():
    plan = Plan("test", [_make_contract("step1")])
    preview = render_plan_preview(plan)
    assert "Plan Preview" in preview
    assert "Test step1" in preview
    assert "Approval required" in preview


def test_render_plan_preview_with_goal():
    plan = Plan("test", [_make_contract("step1", outputs=[{"path": "src/main.py"}])])
    goal_dict = {"title": "Test Goal", "description": "Build something"}
    preview = render_plan_preview(plan, goal_dict=goal_dict)
    assert "Build something" in preview
    assert "Approval required:" in preview


def test_render_plan_preview_risk_levels():
    low_plan = Plan("test", [_make_contract("s1")])
    medium_plan = Plan("test", [
        _make_contract("s1", outputs=[{"path": "src/main.py"}]),
        _make_contract("s2", outputs=[{"path": "tests/test_main.py"}]),
    ])
    high_plan = Plan("test", [_make_contract("s1", objective="delete everything")])

    assert "Low" in render_plan_preview(low_plan)
    assert "Medium" in render_plan_preview(medium_plan)


def test_describe_risk():
    safe = Plan("test", [_make_contract("s1")])
    med = Plan("test", [
        _make_contract("s1", outputs=[{"path": "src/main.py"}]),
        _make_contract("s2"),
        _make_contract("s3"),
        _make_contract("s4"),
    ])
    assert _describe_risk(safe) == "Low"
    assert _describe_risk(med) == "Medium"


def test_render_plan_preview_shows_checks():
    checks = [
        {"id": "c1", "kind": "syntax", "path": "scratch/test/out.py"},
        {"id": "c2", "kind": "hygiene", "path": "scratch/test/out.py"},
    ]
    plan = Plan("test", [_make_contract("s1", checks=checks)])
    preview = render_plan_preview(plan)
    assert "syntax" in preview
    assert "hygiene" in preview


# --- propose/approve digest round-trip regression ---
# Bug: cmd_propose wrote goal.json BEFORE generate_contracts, which mutates
# goal.constraints["_preferences"] (injecting _brain_decisions), then hashed
# the post-mutation goal for plan_digest. cmd_approve re-derived the digest
# from the unmutated on-disk goal.json → permanent mismatch.
# Fix: serialize goal.json/plan.json AFTER generate_contracts and hash the
# same dicts that were written. This test pins that ordering invariant.

PROPOSE_ARGS = ["prompt", "goal_id", "description", "constraints", "title", "run"]


def _fake_generate_contracts(goal, workspace_root=None, prefs=None):
    """Mimic planner.py:436-437: mutate goal.constraints._preferences with
    _brain_decisions derived from memory/BRAIN.md, then produce a plan."""
    prefs = prefs or {}
    goal.constraints.setdefault("_preferences", {})
    goal.constraints["_preferences"]["_brain_decisions"] = ["2003: prefer minimal edits"]
    if "brains" not in goal.constraints["_preferences"]:
        goal.constraints["_preferences"]["brains"] = prefs.get("brains") or []
    plan = Plan("test", [_make_contract("step1", outputs=[{"path": "src/main.py"}])])
    return plan


def test_propose_approve_digest_roundtrip(tmp_path, monkeypatch):
    """propose (with _preferences mutation during plan generation) then approve
    must NOT fail with a digest mismatch."""
    import orchestrator.cli as cli

    run_dir = str(tmp_path / "runs")
    monkeypatch.setattr(cli, "_run_dir", lambda gid: os.path.join(run_dir, gid))
    monkeypatch.setattr(cli, "generate_contracts", _fake_generate_contracts)

    goal_id = "digest-regression"
    args = types.SimpleNamespace(
        prompt="Build a thing into src/",
        goal_id=goal_id,
        description=None,
        constraints=None,
        title="Digest Regression",
        run=False,
    )

    cli.cmd_propose(args)

    approval_path = os.path.join(run_dir, goal_id, "approval.json")
    goal_path = os.path.join(run_dir, goal_id, "goal.json")
    assert os.path.isfile(approval_path)
    assert os.path.isfile(goal_path)

    with open(approval_path, encoding="utf-8") as f:
        approval_data = json.load(f)
    assert approval_data["status"] == "pending"
    assert "plan_digest" in approval_data

    # approve must succeed: on-disk state == digest input state
    cli.cmd_approve(types.SimpleNamespace(goal_id=goal_id))

    with open(approval_path, encoding="utf-8") as f:
        approval_data = json.load(f)
    assert approval_data["status"] == "approved"

    # Ordering invariant: mutated _preferences MUST be on disk, not just in memory
    with open(goal_path, encoding="utf-8") as f:
        goal_dict = json.load(f)
    prefs = goal_dict["constraints"].get("_preferences")
    assert prefs is not None
    assert "_brain_decisions" in prefs


def test_propose_approve_digest_roundtrip_without_mutation(tmp_path, monkeypatch):
    """Same roundtrip when generate_contracts does NOT mutate goal (no brains in
    memory) — approve must still succeed."""
    import orchestrator.cli as cli

    run_dir = str(tmp_path / "runs2")
    monkeypatch.setattr(cli, "_run_dir", lambda gid: os.path.join(run_dir, gid))
    monkeypatch.setattr(
        cli, "generate_contracts",
        lambda goal, workspace_root=None, prefs=None: Plan(
            "test", [_make_contract("step1", outputs=[{"path": "src/main.py"}])]
        ),
    )

    goal_id = "digest-regression-plain"
    args = types.SimpleNamespace(
        prompt="Build a thing into src/",
        goal_id=goal_id,
        description=None,
        constraints=None,
        title="Digest Regression Plain",
        run=False,
    )

    cli.cmd_propose(args)
    cli.cmd_approve(types.SimpleNamespace(goal_id=goal_id))

    approval_path = os.path.join(run_dir, goal_id, "approval.json")
    with open(approval_path, encoding="utf-8") as f:
        approval_data = json.load(f)
    assert approval_data["status"] == "approved"

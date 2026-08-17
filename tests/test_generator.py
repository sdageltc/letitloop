"""Tests for Contract Generator."""

import os
from unittest.mock import patch

from orchestrator.contract import validate_contract
from orchestrator.exceptions import PlannerError
from orchestrator.generator import generate_contracts
from orchestrator.goal import Goal

WORKSPACE_ROOT = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))


def test_generate_contracts_default_goal(tmp_path):
    ws_dir = str(tmp_path)
    goal = Goal(
        goal_id="g-default",
        title="Default Goal",
        description="A simple single-step goal",
    )
    with patch("orchestrator.generator.decompose_goal", side_effect=PlannerError("LLM fail")):
        plan = generate_contracts(goal, workspace_root=ws_dir)
    assert plan.goal_id == "g-default"
    assert len(plan.contracts) >= 1
    assert plan.contracts[0]["task_id"] == "g-default-task-1"
    written_path = os.path.join(ws_dir, plan.contracts[0]["contract_path"])
    assert os.path.isfile(written_path)
    errs = validate_contract(plan.contracts[0]["contract"], workspace_root=ws_dir)
    assert not errs


def test_generate_contracts_subtasks_in_constraints(tmp_path):
    ws_dir = str(tmp_path)
    goal = Goal(
        goal_id="g-subtasks",
        title="Goal with explicit subtasks",
        description="Multi-step goal",
        constraints={
            "subtasks": [
                {
                    "task_id": "g-subtasks-1",
                    "title": "Subtask 1 Recon",
                    "type": "research",
                    "objective": "Research phase",
                    "depends_on": [],
                },
                {
                    "task_id": "g-subtasks-2",
                    "title": "Subtask 2 Implement",
                    "type": "implementation",
                    "objective": "Implementation phase",
                    "depends_on": ["g-subtasks-1"],
                },
            ]
        },
    )
    plan = generate_contracts(goal, workspace_root=ws_dir)
    assert len(plan.contracts) == 2
    assert plan.contracts[0]["task_id"] == "g-subtasks-1"
    assert plan.contracts[1]["depends_on"] == ["g-subtasks-1"]
    assert plan.contracts[0]["contract"]["worker"]["model"] == "gemini:gemini-3.6-flash"


def test_generate_contracts_two_step_keyword_detection(tmp_path):
    ws_dir = str(tmp_path)
    goal = Goal(
        goal_id="g-twostep",
        title="Two-step proof",
        description="Step 1 creates a file, Step 2 validates it",
        constraints={"workspace_scope": {"allow": ["scratch/proof/"], "deny": []}},
    )
    with patch("orchestrator.generator.decompose_goal", side_effect=PlannerError("LLM fail")):
        plan = generate_contracts(goal, workspace_root=ws_dir)
    assert len(plan.contracts) == 2
    c1 = plan.contracts[0]
    c2 = plan.contracts[1]
    assert c1["task_id"] == "g-twostep-step-1"
    assert c2["task_id"] == "g-twostep-step-2"
    assert c2["depends_on"] == ["g-twostep-step-1"]
    assert c1["contract"]["workspace_scope"]["allow"] == ["scratch/proof/"]


def test_generated_contracts_no_fake_output(tmp_path):
    ws_dir = str(tmp_path)
    g = Goal(goal_id="no-fake", title="Test no fake", description="Write a file and verify it")
    valid_kinds = {"command", "file_exists", "content_regex", "json_schema", "syntax", "hygiene", "min_size"}
    with patch("orchestrator.generator.decompose_goal", side_effect=PlannerError("LLM fail")):
        plan = generate_contracts(g, workspace_root=ws_dir)
    for c in plan.contracts:
        for check in c["contract"]["acceptance_checks"]:
            assert "FAKE_WORKER_OUTPUT" not in str(check.get("expected", "")), f"found FAKE in {check}"
            assert check.get("kind") in valid_kinds, f"invalid kind in {check}"
        for out in c["contract"]["outputs"]:
            assert out.get("path"), f"empty path in {out}"

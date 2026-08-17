"""Tests for LLM goal decomposer planner."""

import json
from unittest.mock import MagicMock, patch

import pytest

from orchestrator.exceptions import PlannerError
from orchestrator.generator import generate_contracts
from orchestrator.goal import Goal
from orchestrator.planner import _parse_llm_json, _preferences_block, decompose_goal

pytestmark = pytest.mark.fast


def test_parse_llm_json_skips_leading_example_block():
    """A leading ```json example/schema block must not shadow the real plan."""
    example = json.dumps({"contracts": [{"task_id": "example", "depends_on": []}]})
    real = json.dumps({"contracts": [{"task_id": "real-1", "depends_on": []}]})
    stdout = f"Here is a schema example:\n```json\n{example}\n```\nNow the actual plan:\n```json\n{real}\n```\n"
    data = _parse_llm_json(stdout)
    assert data["contracts"][0]["task_id"] == "real-1"


def test_parse_llm_json_plain_object_without_fences():
    """Bare JSON object with no code fences must still parse."""
    stdout = json.dumps({"contracts": [{"task_id": "bare-1", "depends_on": []}]})
    data = _parse_llm_json(stdout)
    assert data["contracts"][0]["task_id"] == "bare-1"


def test_preferences_block_preserves_custom_keys():
    """_preferences_block must not silently drop custom (non-whitelist) keys."""
    goal = Goal(
        goal_id="g-prefs",
        title="Prefs",
        description="desc",
        constraints={
            "_preferences": {
                "style": {"keep_simple": True},
                "safety": {"never_delete_blindly": True},
                "risk": {"max_scope_depth": 3, "aggressive_threshold": 0.9},
                "nested": {"routing": {"prefer": "openai"}},
            }
        },
    )
    block = _preferences_block(goal)
    assert "Keep implementation simple" in block
    assert "Never delete files without asking" in block
    assert "max_scope_depth: 3" in block
    assert "aggressive_threshold: 0.9" in block
    assert "prefer: openai" in block or "routing" in block


def test_preferences_block_empty_without_prefs():
    goal = Goal(goal_id="g-noprefs", title="No Prefs", description="desc")
    assert _preferences_block(goal) == ""


def test_planner_decomposes_simple_goal(tmp_path):
    goal = Goal(
        goal_id="test-llm",
        title="Write a Python script",
        description="Create a Python script that prints hello world",
    )

    llm_output_json = json.dumps(
        {
            "contracts": [
                {
                    "task_id": "test-llm-step-1",
                    "title": "Step 1: Write script",
                    "type": "implementation",
                    "objective": "Create a Python script that prints hello world",
                    "output_path": "scratch/phase2/test-llm_step1.py",
                    "depends_on": [],
                    "acceptance_checks": [
                        {
                            "id": "check-1",
                            "kind": "content_regex",
                            "path": "scratch/phase2/test-llm_step1.py",
                            "expected": ".+",
                        }
                    ],
                }
            ]
        }
    )

    with patch("orchestrator.planner.call_llm") as mock_llm:
        mock_llm.return_value = {
            "text": f"```json\n{llm_output_json}\n```",
            "provider": "openai",
            "model": "gpt-4o-mini",
        }

        plan = decompose_goal(goal, str(tmp_path))

    assert len(plan.contracts) >= 1
    for c in plan.contracts:
        assert c["task_id"]
        assert "contract" in c
        contract_dict = c["contract"]
        assert "acceptance_checks" in contract_dict
        assert len(contract_dict["acceptance_checks"]) > 0


def test_planner_rejects_invalid_llm_output(tmp_path):
    goal = Goal(
        goal_id="test-invalid",
        title="Invalid Test Goal",
        description="Test invalid output",
    )
    with patch("subprocess.run") as mock_run:
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "This is invalid non-JSON output"
        mock_run.return_value = mock_proc

        with pytest.raises(PlannerError):
            decompose_goal(goal, str(tmp_path))


def test_generator_falls_back_to_keyword(tmp_path):
    goal = Goal(
        goal_id="test-fallback",
        title="Simple goal with fallback",
        description="Generate simple output",
    )
    with patch("orchestrator.planner.decompose_goal", side_effect=PlannerError("LLM failed")):
        plan = generate_contracts(goal, str(tmp_path))
        assert len(plan.contracts) >= 1
        assert plan.goal_id == "test-fallback"

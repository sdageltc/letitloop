"""Tests for contract template system."""

import pytest
from orchestrator.templates import list_templates, template_details, apply_template


def test_list_templates():
    names = list_templates()
    assert len(names) == 6
    assert "research" in names
    assert "implementation" in names
    assert "verification" in names
    assert "adversarial_audit" in names
    assert "aggregation" in names
    assert "code_generation" in names


def test_template_details_known():
    details = template_details("research")
    assert details["name"] == "research"
    assert "description" in details
    assert "defaults" in details


def test_template_details_unknown():
    with pytest.raises(ValueError, match="unknown template"):
        template_details("nonexistent")


def test_apply_research_template():
    contract = apply_template("research", {
        "task_id": "r1", "objective": "research X", "outputs": [{"path": "scratch/r1_out.txt"}],
    })
    assert contract["task_id"] == "r1"
    assert contract["status"] == "DRAFTED"
    # AUT-001: research now gets 2 attempts to absorb nondeterministic LLM slips.
    assert contract["worker"]["max_attempts"] == 2


def test_apply_implementation_template():
    contract = apply_template("implementation", {
        "task_id": "impl1", "objective": "build Y", "outputs": [{"path": "scratch/impl1_out.txt"}],
    })
    assert contract["task_id"] == "impl1"
    assert contract["worker"]["max_attempts"] == 3
    assert len(contract["acceptance_checks"]) == 4
    assert contract["acceptance_checks"][0]["kind"] == "syntax"


def test_apply_code_generation_template():
    contract = apply_template("code_generation", {
        "task_id": "cg1", "objective": "generate Z", "outputs": [{"path": "scratch/cg1_out.txt"}],
    })
    assert contract["task_id"] == "cg1"
    assert "src/" in contract["workspace_scope"]["allow"]
    assert len(contract["acceptance_checks"]) == 4


def test_apply_with_all_overrides():
    contract = apply_template("research", {
        "task_id": "ov1", "objective": "override test", "outputs": [{"path": "scratch/ov1.txt"}],
        "worker": {"model": "custom", "max_attempts": 5},
    })
    assert contract["worker"]["model"] == "custom"
    assert contract["worker"]["max_attempts"] == 5


def test_apply_missing_task_id():
    with pytest.raises(ValueError, match="task_id"):
        apply_template("research", {"objective": "x", "outputs": [{"path": "scratch/x.txt"}]})


def test_apply_missing_objective():
    with pytest.raises(ValueError, match="objective"):
        apply_template("research", {"task_id": "x", "outputs": [{"path": "scratch/x.txt"}]})


def test_apply_missing_outputs():
    with pytest.raises(ValueError, match="outputs"):
        apply_template("research", {"task_id": "x", "objective": "y"})


def test_apply_unknown_template():
    with pytest.raises(ValueError, match="unknown template"):
        apply_template("bogus", {"task_id": "x", "objective": "y", "outputs": [{"path": "z"}]})


def test_apply_verification_template():
    contract = apply_template("verification", {
        "task_id": "v1", "objective": "verify Z", "outputs": [{"path": "scratch/v1_out.txt"}],
    })
    assert len(contract["acceptance_checks"]) == 1
    assert contract["acceptance_checks"][0]["kind"] == "content_regex"


def test_apply_aggregation_template():
    contract = apply_template("aggregation", {
        "task_id": "a1", "objective": "aggregate", "outputs": [{"path": "scratch/a1_out.json"}],
    })
    assert contract["acceptance_checks"][0]["kind"] == "json_schema"

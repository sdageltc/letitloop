"""Tests for contract DAG validation (issue #17): cycles, dangling deps, gates."""

import json
import os
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from orchestrator.dag_validator import (
    DUPLICATE_TASK_ID,
    DagIssue,
    DagValidationError,
    format_cycle_trace,
    raise_if_invalid,
    validate_contract_dag,
)
from orchestrator.exceptions import PlannerError
from orchestrator.goal import ContractGraph, Goal, Plan
from orchestrator.planner import decompose_goal
from orchestrator.supervisor import Supervisor

pytestmark = pytest.mark.fast


def C(task_id, depends_on):
    return {"task_id": task_id, "depends_on": list(depends_on)}


# --- valid shapes -----------------------------------------------------------


def test_linear_chain_has_no_issues():
    issues = validate_contract_dag([C("a", []), C("b", ["a"]), C("c", ["b"])])
    assert issues == []


def test_diamond_has_no_issues():
    issues = validate_contract_dag([C("a", []), C("b", ["a"]), C("c", ["a"]), C("d", ["b", "c"])])
    assert issues == []


def test_disjoint_trees_have_no_issues():
    issues = validate_contract_dag(
        [
            C("t1-root", []),
            C("t1-leaf", ["t1-root"]),
            C("t2-root", []),
            C("t2-leaf", ["t2-root"]),
        ]
    )
    assert issues == []


def test_accepts_attribute_style_contracts():
    items = [
        SimpleNamespace(task_id="x", depends_on=[]),
        SimpleNamespace(task_id="y", depends_on=["x"]),
    ]
    assert validate_contract_dag(items) == []


# --- invalid shapes ---------------------------------------------------------


def test_direct_cycle_two_nodes():
    issues = validate_contract_dag([C("A", ["B"]), C("B", ["A"])])
    kinds = [i.kind for i in issues]
    assert kinds == ["cycle"]
    path = issues[0].cycle_path
    assert path[0] == path[-1]
    assert set(path[:-1]) == {"A", "B"}


def test_indirect_cycle_three_nodes():
    issues = validate_contract_dag([C("A", ["C"]), C("B", ["A"]), C("C", ["B"])])
    assert [i.kind for i in issues] == ["cycle"]
    path = issues[0].cycle_path
    assert len(path) == 4
    assert path[0] == path[-1]
    assert set(path[:3]) == {"A", "B", "C"}


def test_self_reference_detected():
    issues = validate_contract_dag([C("A", ["A"]), C("B", [])])
    assert [(i.kind, i.task_id) for i in issues] == [("self_reference", "A")]


def test_dangling_dependency_detected():
    issues = validate_contract_dag([C("A", []), C("B", ["ghost"])])
    assert [(i.kind, i.task_id) for i in issues] == [("dangling_dependency", "B")]
    assert "ghost" in issues[0].message


def test_mixed_cycle_and_dangling_reported_together():
    issues = validate_contract_dag([C("A", ["B"]), C("B", ["A"]), C("Z", ["ghost"])])
    kinds = sorted(i.kind for i in issues)
    assert kinds == ["cycle", "dangling_dependency"]


# --- formatting / raising ---------------------------------------------------


def test_format_cycle_trace_shape():
    issue = DagIssue(kind="cycle", task_id="A", message="", cycle_path=["A", "B", "A"])
    assert format_cycle_trace(issue) == "Cycle detected: A -> B -> A"


def test_duplicate_task_ids_detected_not_silently_merged():
    """Regression for #39: duplicate task_ids used to overwrite the first node,
    erasing its edges and letting self-deps slip through the gate."""
    contracts = [
        {"task_id": "A", "depends_on": ["A", "A"]},
        {"task_id": "A", "depends_on": []},
    ]
    issues = validate_contract_dag(contracts)
    kinds = {i.kind for i in issues}
    assert DUPLICATE_TASK_ID in kinds
    assert "self_reference" in kinds  # first entry's self-dep must still be caught
    with pytest.raises(DagValidationError):
        raise_if_invalid(contracts)


def test_clean_graph_untouched_by_duplicate_detection():
    contracts = [
        {"task_id": "A", "depends_on": []},
        {"task_id": "B", "depends_on": ["A"]},
    ]
    assert validate_contract_dag(contracts) == []


def test_raise_if_invalid_collects_all_issues_in_message():
    with pytest.raises(DagValidationError) as ei:
        raise_if_invalid([C("A", ["B"]), C("B", ["A"]), C("Z", ["ghost"])])
    msg = str(ei.value)
    assert "Cycle detected:" in msg
    assert "unknown task: ghost" in msg
    assert len(ei.value.issues) == 2
    lines = msg.splitlines()
    assert len(lines) == 2


def test_raise_if_invalid_passes_valid_graph():
    assert raise_if_invalid([C("a", []), C("b", ["a"])]) == []


# --- ContractGraph.cycle_path / has_cycle delegation ------------------------


def _plan(contracts):
    return Plan(goal_id="g", contracts=contracts)


def test_graph_cycle_path_returns_closed_walk():
    g = ContractGraph(_plan([C("a", ["c"]), C("b", ["a"]), C("c", ["b"])]))
    path = g.cycle_path()
    assert path[0] == path[-1]
    assert set(path[:-1]) == {"a", "b", "c"}
    assert g.has_cycle() is True


def test_graph_cycle_path_empty_for_acyclic():
    g = ContractGraph(_plan([C("a", []), C("b", ["a"])]))
    assert g.cycle_path() == []
    assert g.has_cycle() is False


def test_graph_cycle_path_start_hint():
    g = ContractGraph(_plan([C("a", ["b"]), C("b", ["a"])]))
    path = g.cycle_path(start="b")
    assert set(path[:-1]) == {"a", "b"}
    assert path[0] == path[-1]


# --- planner creation gate --------------------------------------------------


def _planner_contract(task_id, idx, depends_on):
    out = f"scratch/phase2/cyc_{idx}.txt"
    return {
        "task_id": task_id,
        "title": f"Step {idx}",
        "type": "implementation",
        "objective": "produce output",
        "output_path": out,
        "depends_on": depends_on,
        "acceptance_checks": [{"id": f"check-{task_id}", "kind": "content_regex", "path": out, "expected": ".+"}],
    }


def test_planner_rejects_cyclic_plan_at_creation(tmp_path):
    goal = Goal(
        goal_id="cyc-plan",
        title="Cyclic planner goal",
        description="Two mutually dependent steps",
    )
    llm_output_json = json.dumps(
        {
            "contracts": [
                _planner_contract("cyc-plan-step-1", 1, ["cyc-plan-step-2"]),
                _planner_contract("cyc-plan-step-2", 2, ["cyc-plan-step-1"]),
            ]
        }
    )
    with patch("orchestrator.planner.call_llm") as mock_llm:
        mock_llm.return_value = {
            "text": f"```json\n{llm_output_json}\n```",
            "provider": "openai",
            "model": "gpt-4o-mini",
        }
        with pytest.raises((DagValidationError, PlannerError)) as ei:
            decompose_goal(goal, str(tmp_path))
    assert "Cycle detected:" in str(ei.value)


# --- supervisor gates -------------------------------------------------------


@pytest.fixture(autouse=True)
def set_fake_worker(monkeypatch):
    monkeypatch.setenv("FAKE_WORKER", "1")


def _cyclic_plan():
    return Plan(
        goal_id="cyc-sup",
        contracts=[
            {"task_id": "cyc-sup-step-1", "depends_on": ["cyc-sup-step-2"], "status": "DRAFTED"},
            {"task_id": "cyc-sup-step-2", "depends_on": ["cyc-sup-step-1"], "status": "DRAFTED"},
        ],
    )


def test_resume_gate_rejects_cyclic_plan(tmp_path):
    ws_dir = str(tmp_path)
    run_dir = os.path.join(ws_dir, "scratch", "runs")
    goal = Goal(goal_id="cyc-sup", title="Cyclic resume", description="d")
    supervisor = Supervisor(goal, _cyclic_plan(), workspace_root=ws_dir, run_dir=run_dir)
    with pytest.raises(DagValidationError) as ei:
        supervisor.resume_plan()
    assert "Cycle detected:" in str(ei.value)


def test_dispatch_gate_rejects_cyclic_plan_before_workers(tmp_path):
    ws_dir = str(tmp_path)
    run_dir = os.path.join(ws_dir, "scratch", "runs")
    goal = Goal(goal_id="cyc-sup", title="Cyclic dispatch", description="d")
    supervisor = Supervisor(goal, _cyclic_plan(), workspace_root=ws_dir, run_dir=run_dir)
    with pytest.raises(DagValidationError):
        supervisor.execute_plan()
    assert goal.status != "EXECUTING"
    assert not os.path.isfile(supervisor._state_path("cyc-sup-step-1"))

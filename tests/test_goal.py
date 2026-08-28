"""Tests for Goal, Plan, and ContractGraph data models."""

import pytest
from orchestrator.goal import ContractGraph, Goal, Plan


def test_goal_and_plan_initialization():
    goal = Goal(
        goal_id="test-goal-1",
        title="Test Goal",
        description="A test goal description",
        constraints={"risk_tier": "auto"},
    )
    assert goal.goal_id == "test-goal-1"
    assert goal.status == "DRAFTED"
    assert goal.constraints == {"risk_tier": "auto"}

    data = goal.to_dict()
    goal_loaded = Goal.from_dict(data)
    assert goal_loaded.goal_id == "test-goal-1"
    assert goal_loaded.title == "Test Goal"

    plan = Plan(
        goal_id="test-goal-1",
        contracts=[
            {"task_id": "t1", "depends_on": [], "status": "drafted"},
            {"task_id": "t2", "depends_on": ["t1"], "status": "drafted"},
        ],
    )
    assert plan.goal_id == "test-goal-1"
    assert len(plan.contracts) == 2


def test_contract_graph_topological_sort():
    plan = Plan(
        goal_id="g1",
        contracts=[
            {"task_id": "step-3", "depends_on": ["step-2"], "status": "drafted"},
            {"task_id": "step-1", "depends_on": [], "status": "drafted"},
            {"task_id": "step-2", "depends_on": ["step-1"], "status": "drafted"},
        ],
    )
    graph = ContractGraph(plan)
    assert not graph.has_cycle()
    assert graph.topological_sort() == ["step-1", "step-2", "step-3"]


def test_contract_graph_cycle_detection():
    plan = Plan(
        goal_id="g_cycle",
        contracts=[
            {"task_id": "t1", "depends_on": ["t2"], "status": "drafted"},
            {"task_id": "t2", "depends_on": ["t1"], "status": "drafted"},
        ],
    )
    graph = ContractGraph(plan)
    assert graph.has_cycle()
    with pytest.raises(ValueError, match="Cycle detected"):
        graph.topological_sort()


def test_contract_graph_ready_and_blocked_tasks():
    plan = Plan(
        goal_id="g_deps",
        contracts=[
            {"task_id": "task-a", "depends_on": [], "status": "drafted"},
            {"task_id": "task-b", "depends_on": ["task-a"], "status": "drafted"},
            {"task_id": "task-c", "depends_on": ["task-b"], "status": "drafted"},
        ],
    )
    graph = ContractGraph(plan)

    # Initially task-a is ready, task-b and task-c are blocked
    assert graph.get_ready_tasks() == ["task-a"]
    assert set(graph.get_blocked_tasks()) == {"task-b", "task-c"}

    # Mark task-a complete
    graph.mark_complete("task-a")
    assert graph.get_ready_tasks() == ["task-b"]
    assert graph.get_blocked_tasks() == ["task-c"]

    # Mark task-b complete
    graph.mark_complete("task-b")
    assert graph.get_ready_tasks() == ["task-c"]
    assert graph.get_blocked_tasks() == []

    # Mark task-c complete
    graph.mark_complete("task-c")
    assert graph.get_ready_tasks() == []
    assert graph.get_blocked_tasks() == []

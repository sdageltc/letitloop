"""Tests for provenance graph module."""

import os
import json
import pytest
from orchestrator.goal import Goal
from orchestrator.generator import generate_contracts
from orchestrator.supervisor import Supervisor
from orchestrator import provenance as prov


@pytest.fixture(autouse=True)
def set_fake_worker(monkeypatch):
    monkeypatch.setenv("FAKE_WORKER", "1")


def test_provenance_builds_after_execution(tmp_path):
    """Provenance graph builds successfully after plan execution."""
    ws_dir = str(tmp_path)
    run_dir = os.path.join(ws_dir, "scratch", "runs")
    goal = Goal(goal_id="prov-run", title="Run", description="Test provenance build")
    plan = generate_contracts(goal, workspace_root=ws_dir)
    supervisor = Supervisor(goal, plan, workspace_root=ws_dir, run_dir=run_dir)
    res = supervisor.execute_plan()
    assert all(s in ("COMPLETE", "complete") for s in res.values())

    graph = prov.build_provenance(goal.goal_id, goal.title, plan, ws_dir, run_dir)
    assert graph.goal_id == "prov-run"
    assert len(graph.nodes) == len(plan.contracts)
    for tid in ["prov-run-task-1"]:
        assert tid in graph.nodes
        node = graph.nodes[tid]
        assert node.status == "COMPLETE"
        assert len(node.outputs) > 0


def test_provenance_nodes_have_io_fields(tmp_path):
    """Each provenance node has inputs, outputs, evidence populated."""
    ws_dir = str(tmp_path)
    run_dir = os.path.join(ws_dir, "scratch", "runs")
    goal = Goal(goal_id="prov-io", title="IO", description="Test provenance I/O")
    plan = generate_contracts(goal, workspace_root=ws_dir)
    supervisor = Supervisor(goal, plan, workspace_root=ws_dir, run_dir=run_dir)
    supervisor.execute_plan()

    graph = prov.build_provenance(goal.goal_id, goal.title, plan, ws_dir, run_dir)
    for tid, node in graph.nodes.items():
        assert isinstance(node.outputs, list)
        assert isinstance(node.inputs, list)
        assert isinstance(node.evidence, dict)
        assert isinstance(node.attempt, int)
        # Outputs should exist on disk after execution
        for out in node.outputs:
            full_path = os.path.join(ws_dir, out) if not os.path.isabs(out) else out
            if node.status == "COMPLETE" and out != "":
                assert os.path.isfile(full_path) or not out


def test_provenance_resolve_output(tmp_path):
    """resolve_output finds which node produced a given output."""
    ws_dir = str(tmp_path)
    run_dir = os.path.join(ws_dir, "scratch", "runs")
    goal = Goal(goal_id="prov-resolve", title="Resolve", description="Test resolve")
    plan = generate_contracts(goal, workspace_root=ws_dir)
    supervisor = Supervisor(goal, plan, workspace_root=ws_dir, run_dir=run_dir)
    supervisor.execute_plan()

    graph = prov.build_provenance(goal.goal_id, goal.title, plan, ws_dir, run_dir)
    # Each node's outputs should be resolvable
    for tid, node in graph.nodes.items():
        for out in node.outputs:
            matches = graph.resolve_output(out)
            assert len(matches) >= 1
            assert any(m.task_id == tid for m in matches)


def test_provenance_edges_from_dependencies(tmp_path):
    """Provenance edges are created from plan dependencies."""
    ws_dir = str(tmp_path)
    run_dir = os.path.join(ws_dir, "scratch", "runs")
    goal = Goal(
        goal_id="prov-edges",
        title="Two-step edges",
        description="Step 1 creates, Step 2 validates",
    )
    plan = generate_contracts(goal, workspace_root=ws_dir)
    supervisor = Supervisor(goal, plan, workspace_root=ws_dir, run_dir=run_dir)
    supervisor.execute_plan()

    graph = prov.build_provenance(goal.goal_id, goal.title, plan, ws_dir, run_dir)
    # Step 2 depends on Step 1
    edge_found = False
    for e in graph.edges:
        if e.edge_type == "dependency":
            edge_found = True
            break
    assert edge_found


def test_provenance_to_dict(tmp_path):
    """ProvenanceGraph serializes to dict correctly."""
    ws_dir = str(tmp_path)
    run_dir = os.path.join(ws_dir, "scratch", "runs")
    goal = Goal(goal_id="prov-dict", title="Dict", description="Test to_dict")
    plan = generate_contracts(goal, workspace_root=ws_dir)
    supervisor = Supervisor(goal, plan, workspace_root=ws_dir, run_dir=run_dir)
    supervisor.execute_plan()

    graph = prov.build_provenance(goal.goal_id, goal.title, plan, ws_dir, run_dir)
    d = graph.to_dict()
    assert d["goal_id"] == "prov-dict"
    assert "nodes" in d
    assert "edges" in d
    assert len(d["nodes"]) == len(plan.contracts)


def test_provenance_without_execution(tmp_path):
    """Provenance builds even without full execution (partial/draft state)."""
    ws_dir = str(tmp_path)
    run_dir = os.path.join(ws_dir, "scratch", "runs")
    goal = Goal(goal_id="prov-draft", title="Draft", description="Test draft provenance")
    plan = generate_contracts(goal, workspace_root=ws_dir)

    graph = prov.build_provenance(goal.goal_id, goal.title, plan, ws_dir, run_dir)
    assert len(graph.nodes) == len(plan.contracts)
    for tid, node in graph.nodes.items():
        assert node.status in ("DRAFTED", "drafted")
        assert node.attempt == 0


def test_provenance_format(tmp_path):
    """format_provenance returns non-empty string."""
    ws_dir = str(tmp_path)
    run_dir = os.path.join(ws_dir, "scratch", "runs")
    goal = Goal(goal_id="prov-fmt", title="Format", description="Test format")
    plan = generate_contracts(goal, workspace_root=ws_dir)
    supervisor = Supervisor(goal, plan, workspace_root=ws_dir, run_dir=run_dir)
    supervisor.execute_plan()

    graph = prov.build_provenance(goal.goal_id, goal.title, plan, ws_dir, run_dir)
    output = prov.format_provenance(graph)
    assert isinstance(output, str)
    assert len(output) > 0
    assert "=== Provenance:" in output
    assert "COMPLETE" in output or "DRAFTED" in output


def test_provenance_trace_path(tmp_path):
    """trace_path returns chain info for an output."""
    ws_dir = str(tmp_path)
    run_dir = os.path.join(ws_dir, "scratch", "runs")
    goal = Goal(goal_id="prov-trace", title="Trace", description="Test trace")
    plan = generate_contracts(goal, workspace_root=ws_dir)
    supervisor = Supervisor(goal, plan, workspace_root=ws_dir, run_dir=run_dir)
    supervisor.execute_plan()

    graph = prov.build_provenance(goal.goal_id, goal.title, plan, ws_dir, run_dir)
    for tid, node in graph.nodes.items():
        for out in node.outputs:
            if out:
                chain = graph.trace_path(out)
                assert chain["output_path"] == out
                assert len(chain["producers"]) >= 1


def test_provenance_node_to_dict():
    """ProvenanceNode serializes correctly."""
    node = prov.ProvenanceNode(
        task_id="t1",
        title="Test",
        status="COMPLETE",
        objective="test objective",
        outputs=["out.txt"],
        inputs=["in.txt"],
        evidence={"preflight": "/tmp/ev.json"},
        attempt=2,
        failure_class="",
    )
    d = node.to_dict()
    assert d["task_id"] == "t1"
    assert d["status"] == "COMPLETE"
    assert d["outputs"] == ["out.txt"]
    assert d["inputs"] == ["in.txt"]
    assert d["attempt"] == 2


def test_provenance_edge_to_dict():
    """ProvenanceEdge serializes correctly."""
    edge = prov.ProvenanceEdge(source="a", target="b", edge_type="dependency", paths=["out.txt"])
    d = edge.to_dict()
    assert d["source"] == "a"
    assert d["target"] == "b"
    assert d["edge_type"] == "dependency"
    assert d["paths"] == ["out.txt"]

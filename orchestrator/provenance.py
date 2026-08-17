"""Provenance graph — tracks contract output lineage and dependency chains."""

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .contract import load_contract
from .state import load_state


class ProvenanceNode:
    """A node in the provenance graph — a contract with its I/O and state."""

    def __init__(
        self,
        task_id: str,
        title: str,
        status: str,
        objective: str,
        outputs: List[str],
        inputs: List[str],
        evidence: Dict[str, str],
        attempt: int,
        failure_class: str = "",
    ):
        self.task_id = task_id
        self.title = title
        self.status = status
        self.objective = objective
        self.outputs = outputs
        self.inputs = inputs
        self.evidence = evidence
        self.attempt = attempt
        self.failure_class = failure_class

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "title": self.title,
            "status": self.status,
            "objective": self.objective,
            "outputs": list(self.outputs),
            "inputs": list(self.inputs),
            "evidence": dict(self.evidence),
            "attempt": self.attempt,
            "failure_class": self.failure_class,
        }


class ProvenanceEdge:
    """An edge — dependency or evidence flow between two contracts."""

    def __init__(self, source: str, target: str, edge_type: str, paths: Optional[List[str]] = None):
        self.source = source
        self.target = target
        self.edge_type = edge_type
        self.paths = paths or []

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "edge_type": self.edge_type,
            "paths": list(self.paths),
        }


class ProvenanceGraph:
    """Full provenance graph for a goal execution."""

    def __init__(self, goal_id: str, goal_title: str):
        self.goal_id = goal_id
        self.goal_title = goal_title
        self.nodes: Dict[str, ProvenanceNode] = {}
        self.edges: List[ProvenanceEdge] = []
        self.created_at = datetime.now(timezone.utc).isoformat()

    def add_node(self, node: ProvenanceNode) -> None:
        self.nodes[node.task_id] = node

    def add_edge(self, edge: ProvenanceEdge) -> None:
        self.edges.append(edge)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "goal_id": self.goal_id,
            "goal_title": self.goal_title,
            "created_at": self.created_at,
            "nodes": [n.to_dict() for n in self.nodes.values()],
            "edges": [e.to_dict() for e in self.edges],
        }

    def resolve_output(self, output_path: str) -> List[ProvenanceNode]:
        """Find all nodes that produced a given output path."""
        abs_path = os.path.abspath(output_path) if not os.path.isabs(output_path) else output_path
        matches = []
        for node in self.nodes.values():
            for out in node.outputs:
                out_abs = os.path.abspath(out) if not os.path.isabs(out) else out
                if out_abs == abs_path or out == output_path:
                    matches.append(node)
        return matches

    def resolve_input_source(self, input_path: str) -> List[ProvenanceNode]:
        """Find which upstream nodes produced a given input path."""
        abs_path = os.path.abspath(input_path) if not os.path.isabs(input_path) else input_path
        matches = []
        for node in self.nodes.values():
            for out in node.outputs:
                out_abs = os.path.abspath(out) if not os.path.isabs(out) else out
                if out_abs == abs_path or out == input_path:
                    matches.append(node)
        return matches

    def trace_path(self, output_path: str) -> Dict[str, Any]:
        """Trace full provenance chain for an output — which contract, its inputs, and their sources."""
        producers = self.resolve_output(output_path)
        chain = {
            "output_path": output_path,
            "producers": [],
        }
        for p in producers:
            producer_info = p.to_dict()
            input_sources = {}
            for inp in p.inputs:
                sources = self.resolve_input_source(inp)
                input_sources[inp] = [s.task_id for s in sources]
            producer_info["input_sources"] = input_sources
            chain["producers"].append(producer_info)
        return chain


def build_provenance(goal_id: str, goal_title: str, plan, workspace_root: str, run_dir: str) -> ProvenanceGraph:
    """Build provenance graph from on-disk state for a completed/partial goal."""
    graph = ProvenanceGraph(goal_id, goal_title)

    from .failure import classify_failure

    def _as_dict(c):
        return c if isinstance(c, dict) else c._raw if hasattr(c, "_raw") else {}

    # Collect all edges first (from plan dependencies)
    dep_edges = {}  # target -> list of source task_ids
    for c in plan.contracts:
        c = _as_dict(c)
        tid = c["task_id"]
        deps = c.get("depends_on", [])
        if deps:
            dep_edges[tid] = deps

    # Build nodes from disk state
    for c in plan.contracts:
        c = _as_dict(c)
        tid = c["task_id"]
        task_dir = os.path.join(run_dir, tid)
        state_file = os.path.join(task_dir, "state.json")

        title = c.get("title", tid)
        contract_raw = c.get("contract") or {}
        objective = contract_raw.get("objective", "") or c.get("objective", "")

        if os.path.isfile(state_file):
            state = load_state(state_file)
            status = state.status
            attempt = state.attempt
            evidence = dict(state.evidence)

            # Get failure class if applicable
            fclass = ""
            if status in ("VERIFICATION_FAILED", "BLOCKED", "PREFLIGHT_FAILED"):
                contract_path = os.path.join(task_dir, "contract.json")
                contract, _ = load_contract(contract_path, workspace_root=workspace_root)
                fclass = classify_failure(state, contract)
        else:
            status = c.get("status", "DRAFTED")
            attempt = 0
            evidence = {}
            fclass = ""

        # Collect outputs and inputs from contract
        contract_dict = c.get("contract") or {}
        outputs = [o["path"] if isinstance(o, dict) else o for o in contract_dict.get("outputs", [])]
        inputs = [i["path"] if isinstance(i, dict) else i for i in contract_dict.get("inputs", [])]

        node = ProvenanceNode(
            task_id=tid,
            title=title,
            status=status,
            objective=objective,
            outputs=outputs,
            inputs=inputs,
            evidence=evidence,
            attempt=attempt,
            failure_class=fclass,
        )
        graph.add_node(node)

    # Build edges from plan dependencies
    for target, sources in dep_edges.items():
        for source in sources:
            edge = ProvenanceEdge(
                source=source,
                target=target,
                edge_type="dependency",
            )
            # Attach evidence paths if source node has outputs listed as target inputs
            if source in graph.nodes:
                source_outputs = set(graph.nodes[source].outputs)
                if target in graph.nodes:
                    target_inputs = graph.nodes[target].inputs
                    for inp in target_inputs:
                        if inp in source_outputs:
                            edge.paths.append(inp)
            graph.add_edge(edge)

    # Build evidence-flow edges from actual evidence injection
    # (evidence_store in supervisor can be reconstructed from state)
    for node in graph.nodes.values():
        for ev_key, ev_path in node.evidence.items():
            graph.add_edge(
                ProvenanceEdge(
                    source=node.task_id,
                    target=node.task_id,
                    edge_type="evidence",
                    paths=[ev_path],
                )
            )

    return graph


def format_provenance(graph: ProvenanceGraph) -> str:
    """Format provenance graph as human-readable string."""
    lines = [
        f"=== Provenance: {graph.goal_id} ===",
        f"Goal: {graph.goal_title}",
        f"Nodes: {len(graph.nodes)}, Edges: {len(graph.edges)}",
        "",
    ]

    for tid in sorted(graph.nodes.keys()):
        node = graph.nodes[tid]
        lines.append(f"  [{node.status}] {tid} (attempt {node.attempt})")
        if node.failure_class:
            lines.append(f"    Failure: {node.failure_class}")
        if node.objective:
            lines.append(f"    Objective: {node.objective[:80]}")
        if node.inputs:
            lines.append(f"    Inputs: {', '.join(node.inputs)}")
        if node.outputs:
            lines.append(f"    Outputs: {', '.join(node.outputs)}")
        if node.evidence:
            lines.append(f"    Evidence: {len(node.evidence)} file(s)")
        lines.append("")

    if graph.edges:
        lines.append("  Edges:")
        for e in graph.edges:
            path_info = f" [{', '.join(e.paths[:3])}]" if e.paths else ""
            lines.append(f"    {e.source} -> {e.target} ({e.edge_type}){path_info}")
        lines.append("")

    return "\n".join(lines)

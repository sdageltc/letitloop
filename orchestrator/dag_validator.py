"""Cycle & dangling-dependency validation for contract DAGs (issue #17).

Enforced at plan creation (planner), dispatch (supervisor.execute_plan) and
resume (supervisor.resume_plan) so cyclic or broken plans never reach the
execution loop, where ``get_ready_tasks`` would silently return nothing.
"""

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

CYCLE = "cycle"
DANGLING_DEPENDENCY = "dangling_dependency"
SELF_REFERENCE = "self_reference"
DUPLICATE_TASK_ID = "duplicate_task_id"


class DagValidationError(Exception):
    """Raised when a contract DAG contains cycles or broken dependencies."""

    def __init__(self, message: str, issues: Optional[List["DagIssue"]] = None):
        super().__init__(message)
        self.issues = issues if issues is not None else []


@dataclass
class DagIssue:
    kind: str  # "cycle" | "dangling_dependency" | "self_reference"
    task_id: str
    message: str
    cycle_path: List[str] = field(default_factory=list)


def _get_field(item: Any, name: str) -> Any:
    if isinstance(item, dict):
        return item.get(name)
    return getattr(item, name, None)


def _find_cycle_in(remaining: set, deps_of: Dict[str, List[str]]) -> Optional[List[str]]:
    """Iterative DFS restricted to ``remaining`` nodes; returns one concrete
    cycle as [n0, n1, ..., n0], or None."""
    color: Dict[str, int] = {}
    parent: Dict[str, str] = {}
    for root in sorted(remaining):
        if color.get(root):
            continue
        color[root] = 1
        stack = [(root, iter(deps_of.get(root, ())))]
        while stack:
            node, dep_iter = stack[-1]
            advanced = False
            for nxt in dep_iter:
                if nxt not in remaining:
                    continue
                state = color.get(nxt)
                if state is None:
                    color[nxt] = 1
                    parent[nxt] = node
                    stack.append((nxt, iter(deps_of.get(nxt, ()))))
                    advanced = True
                    break
                if state == 1:
                    chain = []
                    cur = node
                    while True:
                        chain.append(cur)
                        if cur == nxt:
                            break
                        cur = parent[cur]
                    chain.reverse()
                    chain.append(nxt)
                    return chain
            if not advanced:
                color[node] = 2
                stack.pop()
    return None


def validate_contract_dag(contracts: Iterable[Any]) -> List[DagIssue]:
    """Validate task_id/depends_on structure of contracts.

    Accepts dicts or objects exposing ``task_id`` / ``depends_on`` attributes.
    Returns a list of DagIssue; empty list means the DAG is valid.
    Cycle detection uses iterative Kahn's algorithm: nodes left after peeling
    zero-in-degree nodes belong to (or depend on) a cycle.
    """
    items = list(contracts)
    order: List[str] = []
    deps_of: Dict[str, List[str]] = {}
    id_set = set()
    duplicate_ids: set = set()
    for item in items:
        tid = _get_field(item, "task_id")
        if tid in id_set:
            # Duplicate task_id: later entries would silently overwrite the
            # first node's edges in the maps below, hiding cycles/self-deps.
            duplicate_ids.add(tid)
            continue
        raw_deps = _get_field(item, "depends_on") or []
        deps_of[tid] = list(raw_deps)
        order.append(tid)
        id_set.add(tid)

    issues: List[DagIssue] = []
    for tid in duplicate_ids:
        issues.append(
            DagIssue(DUPLICATE_TASK_ID, tid, f"Duplicate contract task_id: {tid} (later entries ignored)")
        )
    valid_edges: Dict[str, List[str]] = {tid: [] for tid in order}
    for tid in order:
        for dep in deps_of[tid]:
            if dep == tid:
                issues.append(DagIssue(SELF_REFERENCE, tid, f"Contract {tid} depends on itself"))
            elif dep not in id_set:
                issues.append(DagIssue(DANGLING_DEPENDENCY, tid, f"Contract {tid} depends on unknown task: {dep}"))
            else:
                valid_edges[tid].append(dep)

    indegree = {tid: 0 for tid in order}
    dependents: Dict[str, List[str]] = {tid: [] for tid in order}
    for tid in order:
        for dep in valid_edges[tid]:
            indegree[tid] += 1
            dependents[dep].append(tid)

    queue = deque(tid for tid in order if indegree[tid] == 0)
    peeled = 0
    while queue:
        node = queue.popleft()
        peeled += 1
        for m in dependents[node]:
            indegree[m] -= 1
            if indegree[m] == 0:
                queue.append(m)

    if peeled < len(order):
        remaining = {tid for tid in order if indegree[tid] > 0}
        path = _find_cycle_in(remaining, valid_edges)
        if path:
            issues.append(
                DagIssue(CYCLE, path[0], f"Cycle detected among contracts: {' -> '.join(path)}", cycle_path=path)
            )
        else:  # pragma: no cover - defensive, should not happen
            issues.append(
                DagIssue(
                    CYCLE,
                    sorted(remaining)[0],
                    f"Cycle detected involving contracts: {sorted(remaining)}",
                    cycle_path=[],
                )
            )
    return issues


def format_cycle_trace(issue: DagIssue) -> str:
    """Format a cycle issue as an actionable ASCII trace: 'Cycle detected: A -> B -> A'."""
    path = issue.cycle_path or [issue.task_id]
    return f"Cycle detected: {' -> '.join(path)}"


def format_issue(issue: DagIssue) -> str:
    if issue.kind == CYCLE:
        return format_cycle_trace(issue)
    return issue.message


def raise_if_invalid(contracts: Iterable[Any]) -> List[DagIssue]:
    """Raise DagValidationError listing every formatted issue if the DAG is invalid."""
    issues = validate_contract_dag(contracts)
    if issues:
        message = "\n".join(format_issue(i) for i in issues)
        raise DagValidationError(message, issues=issues)
    return issues

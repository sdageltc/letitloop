"""Data models and graph processing for goal-to-contract decomposition."""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

VALID_GOAL_STATUSES = {"DRAFTED", "PLANNED", "EXECUTING", "COMPLETE", "FAILED", "PAUSED", "CANCELLED"}

# Task statuses that are considered final — no further processing needed
FINAL_TASK_STATUSES = frozenset(
    {
        "COMPLETE",
        "complete",
        "VERIFIED",
        "DEGRADED_PASS",
        "degraded_pass",
        "FORCE_COMPLETE",
        "force_complete",
        "FAILED",
        "failed",
        "BLOCKED",
        "ESCALATED",
        "CRASHED",
    }
)


class Goal:
    """High-level goal representation."""

    def __init__(
        self,
        goal_id: str,
        title: str,
        description: str,
        constraints: Optional[Dict[str, Any]] = None,
        dependencies: Optional[List[str]] = None,
        status: str = "DRAFTED",
    ):
        self.goal_id = goal_id
        self.title = title
        self.description = description
        self.constraints = constraints if constraints is not None else {}
        self.dependencies = dependencies if dependencies is not None else []
        self.status = status.upper() if status and status.upper() in VALID_GOAL_STATUSES else "DRAFTED"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "goal_id": self.goal_id,
            "title": self.title,
            "description": self.description,
            "constraints": self.constraints,
            "dependencies": self.dependencies,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Goal":
        return cls(
            goal_id=data["goal_id"],
            title=data.get("title", ""),
            description=data.get("description", ""),
            constraints=data.get("constraints", {}),
            dependencies=data.get("dependencies", []),
            status=data.get("status", "DRAFTED"),
        )

    def __repr__(self) -> str:
        return f"<Goal {self.goal_id} status={self.status}>"


class Plan:
    """Execution plan containing structured contracts for a goal."""

    def __init__(
        self,
        goal_id: str,
        contracts: List[Dict[str, Any]],
        created_at: Optional[str] = None,
    ):
        self.goal_id = goal_id
        self.contracts = contracts
        self.created_at = created_at if created_at is not None else datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "goal_id": self.goal_id,
            "contracts": self.contracts,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Plan":
        return cls(
            goal_id=data["goal_id"],
            contracts=data.get("contracts", []),
            created_at=data.get("created_at"),
        )

    def __repr__(self) -> str:
        return f"<Plan goal={self.goal_id} contracts={len(self.contracts)}>"


class ContractGraph:
    """Graph structure to analyze contract dependencies, ordering, and state."""

    def __init__(self, plan: Plan):
        self.plan = plan
        self.nodes: Dict[str, Dict[str, Any]] = {}
        for c in plan.contracts:
            task_id = c["task_id"]
            self.nodes[task_id] = {
                "task_id": task_id,
                "depends_on": list(c.get("depends_on", [])),
                "status": c.get("status", "DRAFTED"),
                "contract": c.get("contract"),
                "contract_path": c.get("contract_path"),
            }

    def cycle_path(self, start: Optional[str] = None) -> List[str]:
        """Return one concrete dependency cycle as [A, B, ..., A]; empty list if acyclic.

        Iterative DFS with a color map (1=on stack, 2=done) and parent-chain
        reconstruction once a back edge hits an on-stack node.
        """
        color: Dict[str, int] = {}
        parent: Dict[str, str] = {}

        roots = [start] if (start is not None and start in self.nodes) else list(self.nodes.keys())
        for root in roots:
            if color.get(root):
                continue
            color[root] = 1
            stack = [(root, iter(self.nodes.get(root, {}).get("depends_on", [])))]
            while stack:
                node_id, dep_iter = stack[-1]
                advanced = False
                for dep in dep_iter:
                    if dep not in self.nodes:
                        continue
                    state = color.get(dep)
                    if state is None:
                        color[dep] = 1
                        parent[dep] = node_id
                        stack.append((dep, iter(self.nodes[dep].get("depends_on", []))))
                        advanced = True
                        break
                    if state == 1:
                        chain = []
                        cur = node_id
                        while True:
                            chain.append(cur)
                            if cur == dep:
                                break
                            cur = parent[cur]
                        chain.reverse()
                        chain.append(dep)
                        return chain
                if not advanced:
                    color[node_id] = 2
                    stack.pop()
        return []

    def has_cycle(self) -> bool:
        """Return True if a cycle exists in the graph."""
        return bool(self.cycle_path())

    def topological_sort(self) -> List[str]:
        """Return task IDs in dependency order (dependencies first).

        Raises ValueError if a cycle is detected.
        """
        if self.has_cycle():
            raise ValueError("Cycle detected in contracts")

        for node_id, node_data in self.nodes.items():
            for dep in node_data.get("depends_on", []):
                if dep not in self.nodes:
                    raise ValueError(f"Contract {node_id} depends on unknown task: {dep}")

        visited = set()
        order = []

        def dfs(node_id: str):
            visited.add(node_id)
            for dep in self.nodes.get(node_id, {}).get("depends_on", []):
                if dep in self.nodes and dep not in visited:
                    dfs(dep)
            order.append(node_id)

        for node_id in sorted(self.nodes.keys()):
            if node_id not in visited:
                dfs(node_id)

        return order

    def update_status(self, task_id: str, status: str):
        """Update contract status in graph and underlying plan."""
        if task_id in self.nodes:
            self.nodes[task_id]["status"] = status
        for c in self.plan.contracts:
            if c["task_id"] == task_id:
                c["status"] = status

    def mark_complete(self, task_id: str):
        """Mark task status as complete."""
        self.update_status(task_id, "COMPLETE")

    def get_ready_tasks(self) -> List[str]:
        """Return task IDs whose dependencies are all complete and not in a final state."""
        ready = []
        try:
            order = self.topological_sort()
        except ValueError:
            return []
        for task_id in order:
            node = self.nodes[task_id]
            if node["status"] in FINAL_TASK_STATUSES:
                continue
            deps_complete = True
            for dep in node["depends_on"]:
                dep_status = self.nodes.get(dep, {}).get("status", "")
                dep_status_upper = dep_status.upper()
                if dep_status_upper not in ("COMPLETE", "VERIFIED"):
                    deps_complete = False
                    break
            if deps_complete:
                ready.append(task_id)
        return ready

    def get_blocked_tasks(self) -> List[str]:
        """Return task IDs that have at least one incomplete dependency."""
        blocked = []
        for task_id, node in self.nodes.items():
            if node["status"] in FINAL_TASK_STATUSES:
                continue
            has_incomplete_dep = False
            for dep in node["depends_on"]:
                dep_status = self.nodes.get(dep, {}).get("status", "")
                dep_status_upper = dep_status.upper()
                if dep_status_upper not in ("COMPLETE", "VERIFIED"):
                    has_incomplete_dep = True
                    break
            if has_incomplete_dep:
                blocked.append(task_id)
        return blocked

"""Thread-pool worker pool for parallel execution of independent contracts."""

import concurrent.futures
import os
import threading
from typing import Any, Callable, Dict, List, Optional


class WorkerPool:
    """Manages concurrent execution of independent contracts.

    Each contract writes to its own state directory (task_dir = run_dir/<task_id>),
    so concurrent execution is safe as long as shared mutable state
    (results dict, graph node statuses) is protected by a lock.
    """

    def __init__(self, max_workers: int = 3):
        self.max_workers = max_workers
        self._lock = threading.Lock()

    @property
    def max_workers(self) -> int:
        return self._max_workers

    @max_workers.setter
    def max_workers(self, value: int) -> None:
        self._max_workers = max(1, min(value, 8))

    def execute_batch(
        self,
        tasks: List[Dict[str, Any]],
        execute_fn: Callable[[str], str],
        on_result: Optional[Callable[[str, str], None]] = None,
    ) -> Dict[str, str]:
        """Run a batch of tasks in parallel.

        Args:
            tasks: list of contract-info dicts (must contain 'task_id')
            execute_fn: callable(task_id) -> status string
            on_result: optional callback(task_id, status) after each completion

        Returns:
            dict mapping task_id -> status
        """
        if not tasks:
            return {}

        results: Dict[str, str] = {}
        pending = len(tasks)

        with concurrent.futures.ThreadPoolExecutor(max_workers=min(self._max_workers, len(tasks))) as pool:
            fut_map = {pool.submit(self._safe_execute, t["task_id"], execute_fn): t["task_id"] for t in tasks}

            for fut in concurrent.futures.as_completed(fut_map):
                tid = fut_map[fut]
                status = fut.result()
                with self._lock:
                    results[tid] = status
                if on_result:
                    on_result(tid, status)
                pending -= 1

        return results

    def _safe_execute(self, task_id: str, fn: Callable[[str], str]) -> str:
        try:
            return fn(task_id)
        except Exception as e:
            return f"FAILED: {e}"


def filter_independent_tasks(
    ready_tasks: List[str],
    graph,
) -> List[str]:
    """Filter ready_tasks to exclude those that depend on each other.

    Pre-normalizes all output paths once, then uses an O(n) indexed
    collision check (path -> task_id) instead of per-task set intersection.
    All ready tasks have their dependencies satisfied, so they're all
    mutually independent by definition. Returns all ready tasks.
    """
    if not ready_tasks:
        return []
    seen_paths: Dict[str, str] = {}
    result = []
    for tid in ready_tasks:
        node = graph.nodes.get(tid, {})
        contract = node.get("contract", {})
        outputs = contract.get("outputs", [])
        collision = False
        for o in outputs:
            raw = o.get("path", "")
            if not raw:
                continue
            normal = os.path.normcase(os.path.normpath(raw))
            if normal in seen_paths:
                collision = True
                break
        if collision:
            continue
        for o in outputs:
            raw = o.get("path", "")
            if raw:
                normal = os.path.normcase(os.path.normpath(raw))
                seen_paths[normal] = tid
        result.append(tid)
    return result


def format_pool_status(results: Dict[str, str]) -> str:
    """Format pool execution results."""
    if not results:
        return "No tasks executed."
    total = len(results)
    completed = sum(1 for s in results.values() if str(s).upper() in ("COMPLETE", "SUCCESS"))
    failed = total - completed
    lines = [f"Pool: {total} tasks, {completed} completed, {failed} failed"]
    for tid, status in results.items():
        lines.append(f"  {tid}: {status}")
    return "\n".join(lines)

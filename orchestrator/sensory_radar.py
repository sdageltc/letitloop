"""
orchestrator/sensory_radar.py
Autonomous sensory radar for scanning codebases and ranking evolutionary vectors.
"""

from __future__ import annotations
import ast
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional
from orchestrator.elasticity_governor import DynamicElasticityGovernor


@dataclass
class SensoryTask:
    task_id: str
    target_module: str
    target_function: Optional[str]
    optimization_goal: str
    complexity_score: float
    priority: int


class SensoryRadar:
    """Autonomous sensory radar for scanning codebases and ranking evolutionary vectors."""

    def __init__(self, workspace_root: Path):
        self.workspace_root = Path(workspace_root)

    def scan_workspace(self) -> List[SensoryTask]:
        tasks: List[SensoryTask] = []
        for py_file in self.workspace_root.rglob("*.py"):
            try:
                rel = py_file.relative_to(self.workspace_root)
            except ValueError:
                continue

            rel_parts = rel.parts
            if (
                "test" in py_file.stem
                or "tests" in rel_parts
                or ".venv" in rel_parts
                or ".git" in rel_parts
                or "scratch" in rel_parts
                or "build" in rel_parts
                or "dist" in rel_parts
            ):
                continue

            try:
                content = py_file.read_text(encoding="utf-8")
                tree = ast.parse(content)
            except Exception:
                continue

            lines = content.splitlines()
            rel_path = str(rel).replace("\\", "/")
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    start = getattr(node, "lineno", 1)
                    end = getattr(node, "end_lineno", len(lines))
                    func_src = "\n".join(lines[start - 1 : end])

                    score = DynamicElasticityGovernor.calculate_complexity(
                        func_src
                    )
                    if (
                        score >= 8.0
                    ):  # Optimization hotspot threshold (>= 8 LOC / cyclomatic)
                        tasks.append(
                            SensoryTask(
                                task_id=f"HOTSPOT-{py_file.stem}-{node.name}",
                                target_module=rel_path,
                                target_function=node.name,
                                optimization_goal=f"Decompose logic and reduce cyclomatic complexity from {score:.1f}.",
                                complexity_score=score,
                                priority=max(1, int(score // 10)),
                            )
                        )
        tasks.sort(key=lambda t: t.complexity_score, reverse=True)
        return tasks

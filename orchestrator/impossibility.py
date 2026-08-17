"""Impossibility theorem — durable artifacts for escalated/failed tasks."""

import os
import json
from datetime import datetime, timezone
from typing import Dict, Any, Optional

from .state import State
from .contract import Contract


IMPOSSIBILITY_DIR = os.path.join("scratch", "impossibility")


def _maybe_stderr_preview(state: State, max_chars: int = 500) -> str:
    if state.worker_results:
        last = state.worker_results[-1]
        return (last.get("stderr", "") or "")[:max_chars]
    return ""


def _maybe_last_stdout(state: State, max_chars: int = 200) -> str:
    if state.worker_results:
        last = state.worker_results[-1]
        return (last.get("stdout", "") or "")[:max_chars]
    return ""


def build_artifact(
    goal_id: str,
    task_id: str,
    contract: Contract,
    state: State,
    failure_class: str = "",
    workspace_root: str = "",
) -> Dict[str, Any]:
    """Build a structured impossibility artifact dict for an escalated task."""
    def _as_dict(c):
        return c if isinstance(c, dict) else c._raw if hasattr(c, '_raw') else {}
    c = _as_dict(contract)

    return {
        "artifact_type": "impossibility_theorem",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "goal_id": goal_id,
        "task_id": task_id,
        "title": c.get('title', ''),
        "objective": c.get('objective', ''),
        "failure_class": failure_class or state.data.get("last_failure_class", "unknown"),
        "max_attempts": c.get('worker', {}).get("max_attempts", 1),
        "attempts_made": state.attempt,
        "worker_runs": len(state.worker_results),
        "worker_results": [
            {
                "exit_code": r.get("exit_code"),
                "elapsed_sec": r.get("elapsed_sec"),
                "failure_class": r.get("failure_class", ""),
                "stderr_preview": (r.get("stderr", "") or "")[:300],
            }
            for r in state.worker_results
        ],
        "rejected_approaches": list(state.changed_approaches),
        "events": [
            {"from": e["from"], "to": e["to"], "reason": e.get("reason", "")}
            for e in state.events
        ],
        "evidence_paths": dict(state.evidence),
        "last_stderr_preview": _maybe_stderr_preview(state),
        "last_stdout_preview": _maybe_last_stdout(state),
        "recommended_human_action": (
            f"Task {task_id} failed after {state.attempt} attempt(s) "
            f"with failure class '{failure_class or state.data.get('last_failure_class', 'unknown')}'. "
            f"Review artifacts in scratch/impossibility/{goal_id}/{task_id}/ for full context."
        ),
    }


def artifact_dir(goal_id: str, task_id: str) -> str:
    return os.path.join(IMPOSSIBILITY_DIR, goal_id, task_id)


def write_artifact(
    artifact: Dict[str, Any],
    workspace_root: str,
    goal_id: str,
    task_id: str,
) -> tuple[str, str]:
    """Write JSON and Markdown impossibility artifacts to disk.

    Returns (json_path, md_path).
    """
    base_dir = os.path.join(workspace_root, artifact_dir(goal_id, task_id))
    os.makedirs(base_dir, exist_ok=True)

    json_path = os.path.join(base_dir, "impossibility.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(artifact, f, indent=2, ensure_ascii=False)

    md_lines = [
        f"# Impossibility Theorem: {artifact['task_id']}",
        "",
        f"**Goal:** {artifact['goal_id']}",
        f"**Task:** {artifact['task_id']}",
        f"**Title:** {artifact['title']}",
        f"**Objective:** {artifact['objective']}",
        f"**Failure Class:** {artifact['failure_class']}",
        f"**Max Attempts:** {artifact['max_attempts']}",
        f"**Attempts Made:** {artifact['attempts_made']}",
        f"**Created:** {artifact['created_at']}",
        "",
        "## Failure History",
    ]
    for wr in artifact["worker_results"]:
        md_lines.append("")
        md_lines.append(f"- Exit code: {wr.get('exit_code')}")
        md_lines.append(f"- Elapsed: {wr.get('elapsed_sec', '?')}s")
        md_lines.append(f"- Failure class: {wr.get('failure_class', '?')}")
        stderr = (wr.get("stderr_preview", "") or "")[:200]
        if stderr:
            md_lines.append(f"- Stderr: `{stderr}`")

    if artifact.get("rejected_approaches"):
        md_lines.append("")
        md_lines.append("## Rejected Approaches")
        for a in artifact["rejected_approaches"]:
            md_lines.append(f"- {a}")

    md_lines.append("")
    md_lines.append("## Evidence Files")
    for key, path in artifact.get("evidence_paths", {}).items():
        md_lines.append(f"- {key}: `{path}`")

    md_lines.append("")
    md_lines.append("## Recommended Human Action")
    md_lines.append("")
    md_lines.append(artifact.get("recommended_human_action", "Review and decide next steps."))

    md_path = os.path.join(base_dir, "impossibility.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines))

    return json_path, md_path


def write_impossibility(
    contract: Contract,
    state: State,
    goal_id: str,
    workspace_root: str,
    failure_class: str = "",
) -> tuple[str, str]:
    """Convenience: build + write artifact in one call.

    Returns (json_path, md_path).
    """
    artifact = build_artifact(
        goal_id=goal_id,
        task_id=contract.task_id,
        contract=contract,
        state=state,
        failure_class=failure_class,
        workspace_root=workspace_root,
    )
    return write_artifact(artifact, workspace_root, goal_id, contract.task_id)

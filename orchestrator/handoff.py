"""State-derived session handoff generation."""

import json
import os
from datetime import datetime, timezone

from .exceptions import HandoffError
from .state import LEGAL_TRANSITIONS


def _next_legal_actions(state):
    """Return sorted list of legal next transitions from current state."""
    return sorted(LEGAL_TRANSITIONS.get(state.status, set()))


def _outcome_classification(state):
    """Classify outcome: no_change, observation, lesson_candidate, skill_candidate."""
    if state.is_terminal():
        if state.status == "COMPLETE":
            return "lesson_candidate"
        elif state.status == "ESCALATED":
            return "skill_candidate"
        elif state.status == "BLOCKED":
            return "observation"
    return "observation"


def build_handoff(state, run_dir):
    """Build a handoff artifact strictly from state.

    Returns a dict with keys:
        handoff_id, generated_at, task_id, state, attempt,
        completed_checks, evidence_paths, blocker, next_legal_actions,
        outcome_classification, unresolved_human_decisions.

    Also writes handoff JSON to run_dir if provided.
    """
    if not state.task_id:
        raise HandoffError("state has no task_id")

    evidence_paths = list(state.evidence.values()) if state.evidence else []

    last_event = state.events[-1] if state.events else {}
    blocker = None
    if state.status == "BLOCKED":
        blocker = last_event.get("reason", "blocked by preflight failure")
    elif state.status == "ESCALATED":
        blocker = last_event.get("reason", "impossibility: task escalated after max retries")

    handoff = {
        "handoff_id": f"handoff-{state.task_id}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "task_id": state.task_id,
        "status": state.status,
        "attempt": state.attempt,
        "completed_checks": [e["to"] for e in state.events],
        "evidence_paths": evidence_paths,
        "blocker": blocker,
        "next_legal_actions": _next_legal_actions(state),
        "outcome_classification": _outcome_classification(state),
        "unresolved_human_decisions": _unresolved_decisions(state),
    }

    if run_dir:
        os.makedirs(run_dir, exist_ok=True)
        handoff_path = os.path.join(run_dir, "handoff.json")
        with open(handoff_path, "w", encoding="utf-8") as f:
            json.dump(handoff, f, indent=2, ensure_ascii=False)

    return handoff


def _unresolved_decisions(state):
    """Identify decisions requiring human input."""
    decisions = []
    if state.status == "BLOCKED":
        decisions.append("resolve blocker and transition to PREFLIGHT_RUNNING to retry")
    if state.status == "ESCALATED":
        decisions.append("review impossibility log; decide manual intervention or abort")
    if state.status == "RETRY_PENDING":
        decisions.append("choose changed approach and transition to WORKING")
    if state.status == "QC_REJECTED":
        decisions.append("review QC findings and decide approach or abort")
    if state.status == "QC_CONDITIONAL_PASS":
        decisions.append("review conditional QC findings and fix required items or abort")
    return decisions

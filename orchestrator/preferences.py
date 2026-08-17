"""Preference collector — merges user prompt, project rules, and defaults."""

import copy
import os
from typing import Any, Dict, List, Optional

PROJECT_RULES_FILE = "AGENTS.md"
MEMORY_DIR = "memory"
BRAIN_FILE = os.path.join("memory", "BRAIN.md")

DEFAULT_PREFERENCES: Dict[str, Any] = {
    "style": {
        "minimal_changes": True,
        "keep_simple": True,
        "avoid_overengineering": True,
    },
    "safety": {
        "never_delete_blindly": True,
        "ask_before_destructive": True,
    },
    "verification": {
        "run_tests": True,
        "require_empirical_proof": True,
    },
    "planning": {
        "approval_required_for_macro": True,
        "approval_required_for_src_changes": True,
        "approval_required_for_multi_file": True,
        "max_contracts_before_approval": 2,
    },
    "deny_paths": ["AGENTS.md", "memory/", ".opencode/", ".claude/", ".agents/"],
    "allow_paths": ["scratch/"],
}


def _try_read_lines(path: str, max_lines: int = 50) -> List[str]:
    """Read first max_lines lines of a file, return empty list on failure."""
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return [line.rstrip("\n") for _, line in zip(range(max_lines), f)]
    except (OSError, UnicodeDecodeError):
        return []


def collect_preferences(
    workspace_root: str,
    user_hints: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Collect and merge preferences from all sources.

    Priority (highest wins):
      1. explicit user_hints
      2. project rules (AGENTS.md)
      3. DEFAULT_PREFERENCES
    """
    prefs = copy.deepcopy(DEFAULT_PREFERENCES)

    # Source: AGENTS.md
    agents_lines = _try_read_lines(os.path.join(workspace_root, PROJECT_RULES_FILE))
    if agents_lines:
        prefs["_sources"] = prefs.get("_sources", []) + ["AGENTS.md"]
        for line in agents_lines:
            low = line.lower()
            if "never delete" in low:
                prefs.setdefault("safety", {})["never_delete_blindly"] = True
            if "do not touch" in low or "never commit" in low:
                prefs.setdefault("safety", {})["do_not_touch"] = True
            if "test" in low and ("run" in low or "pytest" in low):
                prefs.setdefault("verification", {})["run_tests"] = True
            if "scope" in low and ("allow" in low or "deny" in low):
                pass

    # Source: BRAIN.md scope·DECISIONS
    brain_lines = _try_read_lines(os.path.join(workspace_root, BRAIN_FILE))
    if brain_lines:
        in_decisions = False
        for line in brain_lines:
            if "[SCOPE: DECISIONS]" in line:
                in_decisions = True
                continue
            if in_decisions and line.startswith("#"):
                break
            if in_decisions and ":" in line:
                k, _, v = line.partition(":")
                prefs.setdefault("_brain_decisions", {})[k.strip()] = v.strip()

    # Override with user hints
    if user_hints:
        for section in ("style", "safety", "verification", "planning"):
            if section in user_hints:
                prefs.setdefault(section, {}).update(user_hints[section])
        if "deny_paths" in user_hints:
            prefs["deny_paths"] = list(set(prefs.get("deny_paths", []) + user_hints["deny_paths"]))
        if "allow_paths" in user_hints:
            prefs["allow_paths"] = list(set(prefs.get("allow_paths", []) + user_hints["allow_paths"]))

    return prefs


def apply_preferences_to_goal(goal_dict: Dict[str, Any], prefs: Dict[str, Any]) -> Dict[str, Any]:
    """Merge preferences into a goal's constraints."""
    constraints = goal_dict.get("constraints", {})
    scope = constraints.get("workspace_scope", {})
    if not scope:
        scope = {"allow": [], "deny": []}
    existing_allow = set(scope.get("allow", []))
    existing_deny = set(scope.get("deny", []))
    scope["allow"] = sorted(existing_allow | set(prefs.get("allow_paths", ["scratch/"])))
    scope["deny"] = sorted(existing_deny | set(prefs.get("deny_paths", [])))
    constraints["workspace_scope"] = scope
    constraints["_preferences"] = {k: v for k, v in prefs.items() if not k.startswith("_")}
    goal_dict["constraints"] = constraints
    return goal_dict

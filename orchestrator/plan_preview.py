"""Plan preview renderer — machine plan → human-readable markdown."""

from typing import Any, Dict, Optional

from .approval import format_approval_reasons, requires_approval
from .goal import Plan


def _describe_risk(plan: Plan) -> str:
    """Classify plan risk."""
    from .approval import _get_plan_stats

    stats = _get_plan_stats(plan)
    if stats["has_destructive"] or stats["touches_config"] > 0:
        return "High"
    if stats["touches_src"] > 0 or stats["touches_tests"] > 0 or stats["total"] > 3:
        return "Medium"
    return "Low"


def _contract_type_label(contract_dict: Dict[str, Any]) -> str:
    worker = contract_dict.get("worker", {})
    model = worker.get("model", "")
    if model.startswith("hybrid:"):
        return "code_generation (hybrid loop)"
    from .llm import provider_of

    return f"worker ({provider_of(model)})"


def render_plan_preview(
    plan: Plan,
    goal_dict: Optional[Dict[str, Any]] = None,
    prefs: Optional[Dict[str, Any]] = None,
) -> str:
    """Render a Plan as a human-readable markdown preview."""
    blocks = []
    blocks.append("# Plan Preview")
    blocks.append("")

    # Goal
    if goal_dict:
        blocks.append("## Summary")
        blocks.append("")
        blocks.append(goal_dict.get("description", goal_dict.get("title", "")))
        blocks.append("")

    # Risk
    risk = _describe_risk(plan)
    blocks.append(f"**Risk:** {risk}")
    blocks.append("")

    # Approval
    approval = requires_approval(plan, prefs=prefs)
    if approval["requires_approval"]:
        blocks.append("**Approval required:** Yes")
        blocks.append("")
        blocks.append(format_approval_reasons(approval))
    else:
        blocks.append("**Approval required:** No")
    blocks.append("")

    # Steps
    blocks.append("## Steps")
    blocks.append("")
    for i, c in enumerate(plan.contracts, 1):
        contract_dict = c.get("contract", {}) or {}
        c.get("task_id", contract_dict.get("task_id", f"step-{i}"))
        title = contract_dict.get("title", c.get("title", f"Step {i}"))
        objective = contract_dict.get("objective", c.get("objective", ""))
        worker = contract_dict.get("worker", c.get("worker", {}))
        model = worker.get("model", "default")
        outputs = contract_dict.get("outputs", c.get("outputs", []))
        checks = contract_dict.get("acceptance_checks", c.get("acceptance_checks", []))
        depends_on = c.get("depends_on", contract_dict.get("depends_on", []))
        task_type_ = _contract_type_label(contract_dict)

        blocks.append(f"### {i}. {title}")
        blocks.append("")
        blocks.append(f"**Type:** {task_type_}")
        blocks.append(f"**Model:** {model}")
        if depends_on:
            blocks.append(f"**Depends on:** {', '.join(depends_on)}")
        blocks.append("")
        if objective:
            blocks.append(objective)
            blocks.append("")

        if outputs:
            blocks.append("**Outputs:**")
            for out in outputs:
                p = out.get("path", "") if isinstance(out, dict) else str(out)
                blocks.append(f"- `{p}`")
            blocks.append("")

        if checks:
            blocks.append("**Acceptance checks:**")
            for ch in checks:
                kind = ch.get("kind", "?")
                desc = f"`{kind}`"
                if "expected" in ch:
                    exp = ch["expected"]
                    desc += f" (expected: `{exp}`)" if not isinstance(exp, dict) else ""
                if "path" in ch:
                    desc += f" on `{ch['path']}`"
                blocks.append(f"- {desc}")
            blocks.append("")

    # Summary stats
    blocks.append("---")
    blocks.append("")
    stats = _get_plan_stats_safe(plan)
    blocks.append(f"**Total steps:** {stats['total']}")
    blocks.append(f"**Files in src/:** {stats['touches_src']}")
    blocks.append(f"**Files in tests/:** {stats['touches_tests']}")
    blocks.append(f"**Files in scratch/:** {stats['touches_scratch']}")
    blocks.append("")

    return "\n".join(blocks)


def _get_plan_stats_safe(plan: Plan) -> Dict[str, int]:
    from .approval import _get_plan_stats as gps

    return gps(plan)


def write_plan_preview(
    plan: Plan, output_path: str, goal_dict: Optional[Dict[str, Any]] = None, prefs: Optional[Dict[str, Any]] = None
):
    """Render and write plan preview to a file."""
    content = render_plan_preview(plan, goal_dict=goal_dict, prefs=prefs)
    import os

    parent = os.path.dirname(output_path) or "."
    os.makedirs(parent, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)

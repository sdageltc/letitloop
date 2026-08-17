"""Terminal UI and ASCII Dashboard for letitloop.

Renders rich progress trees, DAG execution graphs, state matrices,
and telemetry directly to the console without heavy external GUI dependencies.
"""

import json
import os
import sys
from typing import Any, Dict, Optional


class TerminalDashboard:
    """Zero-dependency rich terminal dashboard for letitloop runs."""

    STATUS_ICONS = {
        "DRAFTED": "📝",
        "PLANNING": "📐",
        "RUNNING": "🔄",
        "WORKING": "⚙️ ",
        "VERIFYING": "🔍",
        "VERIFIED": "✅",
        "QC_PENDING": "⚖️ ",
        "QC_PASS": "🌟",
        "COMPLETE": "🎉",
        "FAILED": "❌",
        "ESCALATED": "🚨",
        "RETRY_PENDING": "🔁",
    }

    @classmethod
    def render_header(cls, title: str = "letitloop (LIL) — Autonomous Macro-Task Orchestrator"):
        bar = "═" * 72
        return f"\n{bar}\n  {title}\n{bar}\n"

    @classmethod
    def render_goal_summary(cls, goal_dict: Dict[str, Any], plan_dict: Optional[Dict[str, Any]] = None) -> str:
        lines = []
        gid = goal_dict.get("goal_id", "unknown")
        gtitle = goal_dict.get("title", "Untitled Goal")
        gstatus = goal_dict.get("status", "DRAFTED")
        icon = cls.STATUS_ICONS.get(gstatus, "▫️")

        lines.append(f"  🎯 Goal: [{gid}] {gtitle}")
        lines.append(f"  📊 Status: {icon} {gstatus}")

        if plan_dict and "contracts" in plan_dict:
            contracts = plan_dict["contracts"]
            lines.append(f"  📋 Plan DAG: {len(contracts)} tasks scheduled")
            lines.append("  " + "─" * 68)
            for idx, c in enumerate(contracts, 1):
                tid = c.get("task_id", f"task_{idx}")
                obj = c.get("objective", "No objective specified")
                tier = c.get("risk_tier", "standard")
                lines.append(f"    [{idx:02d}] 🔹 {tid:<20} | Tier: {tier:<8} | {obj[:35]}")

        lines.append("═" * 72 + "\n")
        return "\n".join(lines)

    @classmethod
    def render_run_status(cls, run_dir: str) -> str:
        """Inspect and format the live status of all tasks in a run directory."""
        if not os.path.isdir(run_dir):
            return f"No active run directory found at: {run_dir}"

        lines = [cls.render_header("Active Run State Matrix")]
        task_folders = [f for f in os.listdir(run_dir) if os.path.isdir(os.path.join(run_dir, f))]

        if not task_folders:
            lines.append("  (No tasks found in run directory)")
        else:
            lines.append(f"  {'Task ID':<22} {'Status':<16} {'Attempt':<10} {'Events':<8}")
            lines.append("  " + "─" * 60)
            for tf in sorted(task_folders):
                sf = os.path.join(run_dir, tf, "state.json")
                if os.path.isfile(sf):
                    try:
                        with open(sf, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        st = data.get("status", "UNKNOWN")
                        att = data.get("attempt", 1)
                        ev_count = len(data.get("events", []))
                        icon = cls.STATUS_ICONS.get(st, "▫️")
                        lines.append(f"  {tf:<22} {icon} {st:<13} {att:<10} {ev_count:<8}")
                    except Exception:
                        lines.append(f"  {tf:<22} ⚠️ Corrupt state file")
                else:
                    lines.append(f"  {tf:<22} ▫️ Initializing...")

        lines.append("═" * 72 + "\n")
        return "\n".join(lines)


def print_dashboard(run_dir: str):
    """Print terminal dashboard output directly to stdout."""
    out = TerminalDashboard.render_run_status(run_dir)
    sys.stdout.write(out)
    sys.stdout.flush()

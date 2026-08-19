"""Terminal UI and ASCII Dashboard for letitloop.

Renders rich progress trees, DAG execution graphs, state matrices,
and telemetry directly to the console without heavy external GUI dependencies.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List, Optional


class TerminalDashboard:
    """Zero-dependency rich terminal dashboard for letitloop runs."""

    # Unicode icons with clean ASCII fallback
    STATUS_ICONS = {
        "DRAFTED": "[DRAFT]",
        "PLANNED": "[PLAN]",
        "PLANNING": "[PLAN]",
        "RUNNING": "[RUN]",
        "WORKING": "[WORK]",
        "EXECUTING": "[EXEC]",
        "VERIFYING": "[VERIFY]",
        "VERIFIED": "[VERIFIED]",
        "QC_PENDING": "[QC]",
        "QC_PASS": "[QC_PASS]",
        "QC_PASSED": "[QC_PASS]",
        "COMPLETE": "[COMPLETE]",
        "FAILED": "[FAIL]",
        "CRASHED": "[CRASH]",
        "ESCALATED": "[ESCALATE]",
        "RETRY_PENDING": "[RETRY]",
        "DEGRADED_PASS": "[DEGRADED]",
        "PAUSED": "[PAUSED]",
        "CANCELLED": "[CANCELLED]",
    }

    @classmethod
    def render_header(cls, title: str = "letitloop (LIL) — Autonomous Macro-Task Orchestrator") -> str:
        bar = "=" * 78
        return f"\n{bar}\n  {title}\n{bar}\n"

    @classmethod
    def render_goal_summary(cls, goal_dict: Dict[str, Any], plan_dict: Optional[Dict[str, Any]] = None) -> str:
        lines = []
        gid = goal_dict.get("goal_id", "unknown")
        gtitle = goal_dict.get("title", "Untitled Goal")
        gstatus = goal_dict.get("status", "DRAFTED")
        icon = cls.STATUS_ICONS.get(gstatus, "[STATUS]")

        lines.append(f"  * Goal: [{gid}] {gtitle}")
        lines.append(f"  * Status: {icon} {gstatus}")

        if plan_dict and "contracts" in plan_dict:
            contracts = plan_dict["contracts"]
            lines.append(f"  * Plan DAG: {len(contracts)} tasks scheduled")
            lines.append("  " + "-" * 74)
            for idx, c in enumerate(contracts, 1):
                tid = c.get("task_id", f"task_{idx}")
                obj = c.get("objective", "No objective specified")
                tier = c.get("risk_tier", "standard")
                lines.append(f"    [{idx:02d}] - {tid:<20} | Tier: {tier:<8} | {obj[:35]}")

        lines.append("=" * 78 + "\n")
        return "\n".join(lines)

    @classmethod
    def render_run_status(cls, run_dir: str) -> str:
        """Inspect and format the live status of all goals/tasks in a run directory."""
        if not os.path.isdir(run_dir):
            return f"No active run directory found at: {run_dir}\n"

        # Case 1: Single Goal directory (contains goal.json or plan.json)
        goal_file = os.path.join(run_dir, "goal.json")
        plan_file = os.path.join(run_dir, "plan.json")
        if os.path.isfile(goal_file):
            try:
                with open(goal_file, "r", encoding="utf-8") as f:
                    goal_data = json.load(f)
                plan_data = None
                if os.path.isfile(plan_file):
                    with open(plan_file, "r", encoding="utf-8") as pf:
                        plan_data = json.load(pf)
                return cls.render_goal_summary(goal_data, plan_data)
            except Exception as e:
                return f"Error reading goal state: {e}\n"

        # Case 2: Root runs directory containing multiple goal folders
        lines = [cls.render_header("Active Orchestrator Runs Matrix")]
        entries = sorted([f for f in os.listdir(run_dir) if os.path.isdir(os.path.join(run_dir, f))])

        if not entries:
            lines.append("  (No runs found in directory)")
        else:
            lines.append(f"  {'Goal / Task ID':<32} {'Status':<16} {'Tasks':<12} {'Details':<14}")
            lines.append("  " + "-" * 74)

            for folder in entries:
                fpath = os.path.join(run_dir, folder)
                g_json = os.path.join(fpath, "goal.json")
                s_json = os.path.join(fpath, "state.json")

                if os.path.isfile(g_json):
                    try:
                        with open(g_json, "r", encoding="utf-8") as f:
                            g_data = json.load(f)
                        st = g_data.get("status", "DRAFTED")
                        title = g_data.get("title", "")
                        # Count child task folders
                        subfolders = [sf for sf in os.listdir(fpath) if os.path.isdir(os.path.join(fpath, sf)) and sf not in ("state_backups", "checkpoints")]
                        icon = cls.STATUS_ICONS.get(st, "[STATUS]")
                        lines.append(f"  {folder:<32} {icon:<14} {len(subfolders):<12} {title[:14]}")
                    except Exception:
                        lines.append(f"  {folder:<32} [ERROR] Corrupt goal.json")
                elif os.path.isfile(s_json):
                    try:
                        with open(s_json, "r", encoding="utf-8") as f:
                            s_data = json.load(f)
                        st = s_data.get("status", "UNKNOWN")
                        att = s_data.get("attempt", 1)
                        icon = cls.STATUS_ICONS.get(st, "[STATUS]")
                        lines.append(f"  {folder:<32} {icon:<14} {'attempt ' + str(att):<12} {'single task'}")
                    except Exception:
                        lines.append(f"  {folder:<32} [ERROR] Corrupt state.json")
                else:
                    lines.append(f"  {folder:<32} [INIT] Empty run folder")

        lines.append("=" * 78 + "\n")
        return "\n".join(lines)


def print_dashboard(run_dir: str) -> None:
    """Print terminal dashboard output safely to stdout."""
    out = TerminalDashboard.render_run_status(run_dir)
    try:
        sys.stdout.write(out)
        sys.stdout.flush()
    except UnicodeEncodeError:
        # Fallback for strict Windows charmap encodings
        safe_out = out.encode("ascii", errors="replace").decode("ascii")
        sys.stdout.write(safe_out)
        sys.stdout.flush()

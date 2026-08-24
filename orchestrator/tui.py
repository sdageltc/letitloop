"""Terminal UI and ASCII Dashboard for letitloop.

Renders rich progress trees, DAG execution graphs, state matrices,
and telemetry directly to the console without heavy external GUI dependencies.
"""

from __future__ import annotations

import datetime
import json
import os
import sys
import time
from typing import Any, Callable, Dict, List, Optional


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
    def render_header(cls, title: str = "letitloop (LIL) â€” Autonomous Macro-Task Orchestrator") -> str:
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
                        subfolders = [
                            sf
                            for sf in os.listdir(fpath)
                            if os.path.isdir(os.path.join(fpath, sf)) and sf not in ("state_backups", "checkpoints")
                        ]
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


# ---------------------------------------------------------------------------
# ANSI color support (zero-dependency; NO_COLOR respected)
# ---------------------------------------------------------------------------

RESET = "\x1b[0m"

STATE_COLORS: Dict[str, str] = {
    # planning
    "DRAFTED": "\x1b[90m",
    "PLANNED": "\x1b[36m",
    "PLANNING": "\x1b[36m",
    # queued / ready
    "READY": "\x1b[36m",
    "PREFLIGHT_RUNNING": "\x1b[33m",
    # active work
    "RUNNING": "\x1b[33m",
    "WORKING": "\x1b[33m",
    "WORK": "\x1b[33m",
    "EXECUTING": "\x1b[33m",
    # verification
    "VERIFYING": "\x1b[96m",
    "VERIFIED": "\x1b[92m",
    "VERIFICATION_FAILED": "\x1b[91m",
    # QC family + aliases
    "QC_RUNNING": "\x1b[96m",
    "QC_REVIEW": "\x1b[95m",
    "QC_PENDING": "\x1b[95m",
    "QC_PASS": "\x1b[92m",
    "QC_PASSED": "\x1b[92m",
    "QC_FAILED": "\x1b[91m",
    "QC_REJECTED": "\x1b[91m",
    "QC_INSUFFICIENT_EVIDENCE": "\x1b[95m",
    "QC_CONDITIONAL_PASS": "\x1b[93m",
    # terminal outcomes
    "COMPLETE": "\x1b[92m",
    "FORCE_COMPLETE": "\x1b[92m",
    "DEGRADED_PASS": "\x1b[92m",
    "FAILED": "\x1b[91m",
    "CRASHED": "\x1b[91m",
    "BLOCKED": "\x1b[31m",
    "ESCALATED": "\x1b[35m",
    # retry lifecycle
    "RETRY": "\x1b[93m",
    "RETRY_PENDING": "\x1b[93m",
    "PAUSED": "\x1b[94m",
    "CANCELLED": "\x1b[90m",
}


def color_enabled(out: Any = None) -> bool:
    """True when ANSI escape sequences should be emitted.

    Respects the NO_COLOR convention (https://no-color.org): any value set in
    the environment disables all escape output.
    """
    if os.environ.get("NO_COLOR"):
        return False
    return True


def colorize_status(status: str, text: Optional[str] = None, enabled: bool = True) -> str:
    """Wrap a status label with its ANSI color code (plain text when disabled)."""
    body = text if text is not None else status
    if not enabled:
        return body
    code = STATE_COLORS.get((status or "").upper())
    if not code:
        return body
    return f"{code}{body}{RESET}"


def _stream_is_ascii(stream: Any) -> bool:
    """Best-effort detection of streams that cannot encode box-drawing chars."""
    enc = getattr(stream, "encoding", None)
    if not enc:
        return False  # StringIO and similar in-memory streams handle unicode
    try:
        "\u251c\u2500".encode(enc)  # box-drawing chars used by render_dag_tree
    except (UnicodeEncodeError, LookupError):
        return True
    return False


# Box-drawing glyphs with ASCII fallbacks
GLYPHS_UNICODE = {"branch": "\u251c\u2500\u2500 ", "last": "\u2514\u2500\u2500 ", "pipe": "\u2502   ", "space": "    "}
GLYPHS_ASCII = {"branch": "|-- ", "last": "`-- ", "pipe": "|   ", "space": "    "}


def render_dag_tree(
    plan: Optional[Dict[str, Any]] = None,
    goal: Optional[Dict[str, Any]] = None,
    status_map: Optional[Dict[str, str]] = None,
    ascii_mode: bool = False,
) -> List[str]:
    """Render plan contracts as an indented dependency tree.

    Lines look like::

        root_task
        |-- child_a
        |   `-- grandchild
        `-- child_b

    Dependencies (``depends_on`` edges) drive indentation. Cycles are safe.
    """
    contracts: List[Dict[str, Any]] = []
    if plan and isinstance(plan.get("contracts"), list):
        contracts = [c for c in plan["contracts"] if isinstance(c, dict)]
    elif goal and isinstance(goal.get("plan"), dict):
        nested = goal["plan"]
        if isinstance(nested.get("contracts"), list):
            contracts = [c for c in nested["contracts"] if isinstance(c, dict)]

    ids_in_order = [str(c.get("task_id", f"task_{i}")) for i, c in enumerate(contracts, 1)]
    id_set = set(ids_in_order)

    children: Dict[str, List[str]] = {tid: [] for tid in ids_in_order}
    roots: List[str] = []
    seen_root = set()
    for c in contracts:
        tid = str(c.get("task_id", ""))
        deps = c.get("depends_on") or []
        if not isinstance(deps, list):
            deps = []
        internal_deps = [d for d in deps if d in id_set]
        if not internal_deps:
            if tid not in seen_root:
                roots.append(tid)
                seen_root.add(tid)
            continue
        for d in internal_deps:
            children[d].append(tid)

    glyphs = GLYPHS_ASCII if ascii_mode else GLYPHS_UNICODE
    lines: List[str] = []
    visited = set()

    def walk(tid: str, node_prefix: str, child_base: str) -> None:
        if tid in visited:
            return
        visited.add(tid)
        label = tid
        if status_map:
            st = status_map.get(tid)
            if st:
                icon = TerminalDashboard.STATUS_ICONS.get(str(st).upper(), "[STATUS]")
                label = f"{tid} {icon}"
        lines.append(node_prefix + label)
        kids = [k for k in children.get(tid, []) if k not in visited]
        for i, kid in enumerate(kids):
            is_last = i == len(kids) - 1
            connector = glyphs["last"] if is_last else glyphs["branch"]
            cont = glyphs["space"] if is_last else glyphs["pipe"]
            walk(kid, child_base + connector, child_base + cont)

    roots_final = roots if roots else ids_in_order[:1]
    for r in roots_final:
        walk(r, "", "")
    # Any tasks unreachable due to cycles get appended flat so nothing is lost
    for tid in ids_in_order:
        if tid not in visited:
            lines.append(glyphs["branch"] + tid)
            visited.add(tid)
    if not lines:
        lines.append("(empty plan)")
    return lines


def render_budget_gauge(used_tokens: float, max_tokens: float, width: int = 30) -> str:
    """Render a ``[#####-----]`` token usage bar with ``used/max (pct%)`` suffix."""
    try:
        max_tokens_f = float(max_tokens) if max_tokens else 0.0
        used_tokens_f = max(0.0, float(used_tokens))
    except (TypeError, ValueError):
        return "-"
    width = max(int(width), 1)
    if max_tokens_f <= 0:
        return "-"
    ratio = used_tokens_f / max_tokens_f
    ratio = min(max(ratio, 0.0), 1.0)
    filled = int(round(ratio * width))
    filled = min(max(filled, 0), width)
    bar = "[" + "#" * filled + "-" * (width - filled) + "]"
    pct = int(round(ratio * 100))
    used_disp = int(used_tokens_f) if float(used_tokens_f).is_integer() else used_tokens_f
    max_disp = int(max_tokens_f) if max_tokens_f.is_integer() else max_tokens_f
    return f"{bar} {used_disp}/{max_disp} ({pct}%)"


_BUDGET_FILE_CANDIDATES = ("usage.json", "budget.json", "token_usage.json", "usage_ledger.json")
_LOCK_FILENAME = ".goal.lock"


class LiveDashboard:
    """Single-frame / live-loop terminal dashboard fed from run_dir artifacts.

    All rendering goes through ``self.out.write`` so tests can inject any
    file-like object. Keyboard handling uses an injectable ``key_reader``
    callable; the real stdin reader is built lazily only when stdin is a tty.
    """

    PANE_DAG = "dag"
    PANE_METRICS = "metrics"

    def __init__(self, run_dir: str, interval: float = 2.0, out: Any = None):
        self.run_dir = run_dir
        self.interval = max(float(interval), 0.0)
        self.out = out if out is not None else sys.stdout
        self.active_pane = self.PANE_DAG
        self._ascii_mode = _stream_is_ascii(self.out)
        self._use_color = color_enabled(self.out)
        self._frames_written = 0
        self._stdin_reader: Optional[Callable[[], Optional[str]]] = None

    # -- data collection ----------------------------------------------------

    @staticmethod
    def _load_json(path: str) -> Optional[Dict[str, Any]]:
        if not os.path.isfile(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else None
        except (OSError, ValueError):
            return None

    @staticmethod
    def _extract_usage(data: Dict[str, Any]) -> Optional[Dict[str, float]]:
        """Pull token usage numbers out of a persisted artifact dict."""
        total = data.get("total_tokens")
        if isinstance(total, (int, float)) and not isinstance(total, bool):
            usage: Dict[str, float] = {"used": float(total)}
            cap = data.get("max_tokens", data.get("token_max", data.get("budget_max_tokens")))
            if isinstance(cap, (int, float)) and not isinstance(cap, bool) and cap > 0:
                usage["max"] = float(cap)
            return usage
        return None

    def collect(self) -> Dict[str, Any]:
        goal_data = self._load_json(os.path.join(self.run_dir, "goal.json")) or {}
        plan_data = self._load_json(os.path.join(self.run_dir, "plan.json")) or {}
        metrics_data = self._load_json(os.path.join(self.run_dir, "metrics.json")) or {}

        states: Dict[str, Dict[str, Any]] = {}
        leases = 0
        attempts_total = 0
        if os.path.isdir(self.run_dir):
            if os.path.isfile(os.path.join(self.run_dir, _LOCK_FILENAME)):
                leases += 1
            for entry in sorted(os.listdir(self.run_dir)):
                spath = os.path.join(self.run_dir, entry, "state.json")
                sdata = self._load_json(spath)
                if sdata is not None:
                    states[entry] = sdata
                    att = sdata.get("attempt", 1)
                    if isinstance(att, (int, float)):
                        attempts_total += int(att)

        # metrics attempt_counts are additive with on-disk states
        mc_attempts = metrics_data.get("attempt_counts") or {}
        metrics_attempts_total = 0
        if isinstance(mc_attempts, dict):
            metrics_attempts_total = sum(v for v in mc_attempts.values() if isinstance(v, (int, float)))

        usage: Optional[Dict[str, float]] = None
        if os.path.isdir(self.run_dir):
            candidates = list(_BUDGET_FILE_CANDIDATES)
            try:
                candidates += [f for f in os.listdir(self.run_dir) if f.endswith(".json")]
            except OSError:
                pass
            seen_files = set()
            for fname in candidates:
                if fname in seen_files:
                    continue
                seen_files.add(fname)
                blob = self._load_json(os.path.join(self.run_dir, fname))
                if blob is not None:
                    usage = self._extract_usage(blob)
                    if usage:
                        break

        return {
            "goal": goal_data,
            "plan": plan_data,
            "metrics": metrics_data,
            "states": states,
            "leases": leases,
            "attempts_total": attempts_total,
            "metrics_attempts_total": metrics_attempts_total,
            "usage": usage,
        }

    # -- frame rendering ----------------------------------------------------

    def _status_map(self, data: Dict[str, Any]) -> Dict[str, str]:
        return {tid: str(s.get("status", "UNKNOWN")) for tid, s in data["states"].items()}

    def _render_header(self, data: Dict[str, Any]) -> List[str]:
        bar = "=" * 78
        goal = data["goal"]
        gid = goal.get("goal_id", "-")
        gtitle = goal.get("title", "Untitled Goal")
        gstatus = str(goal.get("status", "DRAFTED"))
        now = datetime.datetime.now().strftime("%H:%M:%S")
        head = f"letitloop LIVE â€” [{gid}] {gtitle}"
        line1 = f"  {head}   refresh {now}   pane: {self.active_pane}   ([tab] panes, [q] quit)"
        colored = colorize_status(gstatus, line1, enabled=self._use_color) if self._use_color else line1
        return [bar, colored, bar]

    def _render_dag_pane(self, data: Dict[str, Any]) -> List[str]:
        lines: List[str] = ["", "  Task DAG:"]
        tree = render_dag_tree(plan=data["plan"], goal=data["goal"], ascii_mode=self._ascii_mode)
        lines.extend("  " + line for line in tree)
        lines.append("")
        lines.append("  Contracts:")
        states = data["states"]
        if states:
            for tid, sdata in states.items():
                st = str(sdata.get("status", "UNKNOWN"))
                att = sdata.get("attempt", 1)
                icon = TerminalDashboard.STATUS_ICONS.get(st.upper(), "[STATUS]")
                icon_txt = colorize_status(st, icon, enabled=self._use_color)
                lines.append(f"    {tid:<28} {icon_txt:<14} attempt {att}")
        else:
            contracts = (data["plan"] or {}).get("contracts") or []
            if contracts:
                for idx, c in enumerate(contracts, 1):
                    tid = c.get("task_id", f"task_{idx}")
                    lines.append(f"    {tid:<28} {'[INIT]':<14} no state yet")
            else:
                lines.append("    (no task state found)")
        return lines

    def _render_metrics_pane(self, data: Dict[str, Any]) -> List[str]:
        lines: List[str] = ["", "  Phase Timings:"]
        m = data["metrics"]
        phase_elapsed = m.get("phase_elapsed") or {}
        phase_counts = m.get("phase_counts") or {}
        if phase_elapsed:
            lines.append(f"    {'phase':<24} {'elapsed':>10} {'runs':>6}")
            for phase in sorted(phase_elapsed):
                el = phase_elapsed.get(phase, 0.0)
                cnt = phase_counts.get(phase, 0)
                lines.append(f"    {phase:<24} {el:>9.2f}s {cnt:>6}")
        else:
            lines.append("    (no phase timings recorded)")
        lines.append("")
        lines.append("  Attempt Counts:")
        ac = m.get("attempt_counts") or {}
        if ac:
            for tid in sorted(ac):
                lines.append(f"    {tid}: {ac[tid]}")
        else:
            lines.append("    (no retries recorded)")
        total_att = m.get("total_attempts", data["metrics_attempts_total"])
        lines.append(f"    total_attempts: {total_att}")
        return lines

    def render_frame(self, data: Dict[str, Any]) -> str:
        lines: List[str] = self._render_header(data)
        if self.active_pane == self.PANE_METRICS:
            lines.extend(self._render_metrics_pane(data))
        else:
            lines.extend(self._render_dag_pane(data))

        lines.append("")
        usage = data["usage"]
        if usage:
            used = usage.get("used", 0.0)
            cap = usage.get("max")
            if cap:
                gauge = render_budget_gauge(used, cap, width=30)
                gauge = colorize_status("FAILED" if cap and used >= cap else "WORKING", gauge, enabled=self._use_color)
                lines.append(f"  Budget: {gauge}")
            else:
                lines.append(f"  Tokens used: {int(used)} (no cap recorded)")
        lines.append(f"  Attempts: {data['attempts_total']} | Active leases: {data['leases']}")
        lines.append("=" * 78)
        lines.append("")
        text = "\n".join(lines)
        if self._ascii_mode:
            text = text.encode("ascii", errors="replace").decode("ascii")
        return text

    def _write_frame(self, frame: str) -> None:
        if self._use_color:
            clear = "\x1b[H\x1b[J"
            if self._frames_written > 0:
                self.out.write(clear)
        else:
            # NO_COLOR / ascii fallback: reprint separated by a blank band
            if self._frames_written > 0:
                self.out.write("\n" * 3)
        self.out.write(frame)
        try:
            self.out.flush()
        except AttributeError:
            pass
        self._frames_written += 1

    # -- keyboard -----------------------------------------------------------

    def _default_key_reader(self) -> Optional[str]:
        """Non-blocking single-key read from a real tty (built lazily)."""
        if self._stdin_reader is None:
            try:
                if not sys.stdin.isatty():
                    return None
            except (ValueError, OSError, AttributeError):
                return None
            self._stdin_reader = self._build_tty_reader()
            if self._stdin_reader is None:
                return None
        return self._stdin_reader()

    @staticmethod
    def _build_tty_reader() -> Optional[Callable[[], Optional[str]]]:
        if os.name == "nt":
            try:
                import msvcrt  # noqa: F401 - availability probe; used inside read_key_win
            except ImportError:
                return None

            def read_key_win() -> Optional[str]:
                import msvcrt

                if msvcrt.kbhit():
                    ch = msvcrt.getwch()
                    return ch if ch else None
                return None

            return read_key_win
        try:
            import select
            import termios
            import tty
        except ImportError:
            return None

        state = {"saved": None}

        def read_key_posix() -> Optional[str]:
            fd = sys.stdin.fileno()
            if state["saved"] is None:
                state["saved"] = termios.tcgetattr(fd)
                try:
                    tty.setcbreak(fd)
                except (termios.error, OSError):
                    return None
            dr, _, _ = select.select([sys.stdin], [], [], 0)
            if not dr:
                return None
            return sys.stdin.read(1)

        def restore() -> None:
            if state["saved"] is not None:
                try:
                    termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, state["saved"])
                except (termios.error, OSError, ValueError):
                    pass
                state["saved"] = None

        read_key_posix.restore = restore  # type: ignore[attr-defined]
        return read_key_posix

    def run(self, once: bool = False, key_reader: Optional[Callable[[], Optional[str]]] = None) -> None:
        reader = key_reader if key_reader is not None else self._default_key_reader
        try:
            while True:
                data = self.collect()
                frame = self.render_frame(data)
                self._write_frame(frame)
                if once:
                    break
                key = None
                try:
                    key = reader() if reader is not None else None
                except StopIteration:
                    break
                if key:
                    k = str(key).lower()
                    if k == "q":
                        break
                    if key == "\t" or k == "tab":
                        self.active_pane = self.PANE_METRICS if self.active_pane == self.PANE_DAG else self.PANE_DAG
                if self.interval > 0:
                    time.sleep(self.interval)
        except KeyboardInterrupt:
            pass
        finally:
            reader_obj = self._stdin_reader
            restore = getattr(reader_obj, "restore", None) if reader_obj is not None else None
            if callable(restore):
                restore()

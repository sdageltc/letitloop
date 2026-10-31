"""Streamlit UI for demonstrating LetItLoop crash recovery and WAL fast-forward."""

from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any

import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
WORKER_PATH = Path(__file__).with_name("worker.py")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from examples.interactive_demo.worker import WORKFLOW_STEPS, read_wal_records  # noqa: E402


def _init_state() -> None:
    defaults: dict[str, Any] = {
        "phase": "idle",
        "run_dir": None,
        "worker_process": None,
        "event_queue": queue.Queue(),
        "events": [],
        "step_statuses": {},
        "attempt_count": 0,
        "current_attempt": 0,
        "current_mode": "initial",
        "expecting_crash": False,
        "crash_at_step": 3,
        "pre_crash_completed_ids": [],
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def _read_worker_output(
    process: subprocess.Popen[str],
    output_queue: queue.Queue,
    attempt: int,
    mode: str,
) -> None:
    if process.stdout is None:
        return
    for line in process.stdout:
        content = line.strip()
        if not content:
            continue
        try:
            event = json.loads(content)
            if not isinstance(event, dict):
                raise ValueError("worker output is not a JSON object")
        except (json.JSONDecodeError, ValueError):
            event = {"event": "worker_output", "message": content}
        event["attempt"] = attempt
        event["mode"] = mode
        output_queue.put(event)


def _launch_worker(wal_dir: str, crash_at_step: int | None, mode: str) -> None:
    attempt = int(st.session_state.attempt_count) + 1
    command = [sys.executable, str(WORKER_PATH), "--wal-dir", wal_dir]
    if crash_at_step is not None:
        command.extend(["--crash-at", str(crash_at_step)])

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONPATH"] = str(ROOT) + (os.pathsep + env.get("PYTHONPATH", "") if env.get("PYTHONPATH") else "")

    process = subprocess.Popen(
        command,
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    st.session_state.worker_process = process
    st.session_state.attempt_count = attempt
    st.session_state.current_attempt = attempt
    st.session_state.current_mode = mode
    st.session_state.expecting_crash = mode == "initial"
    st.session_state.phase = "running"
    thread = threading.Thread(
        target=_read_worker_output,
        args=(process, st.session_state.event_queue, attempt, mode),
        daemon=True,
    )
    thread.start()


def _drain_events() -> None:
    event_queue = st.session_state.event_queue
    while True:
        try:
            event = event_queue.get_nowait()
        except queue.Empty:
            break
        st.session_state.events.append(event)
        event_type = event.get("event")
        step_id = event.get("step_id")
        if event_type == "step_started" and step_id:
            st.session_state.step_statuses[step_id] = "running"
        elif event_type == "ready_to_crash" and step_id:
            st.session_state.step_statuses[step_id] = "waiting"
            st.session_state.phase = "waiting"
        elif event_type == "step_completed" and step_id:
            st.session_state.step_statuses[step_id] = "restored" if event.get("cached") else "completed"
        elif event_type == "hold_timeout":
            st.session_state.phase = "failed"
        elif event_type == "workflow_completed":
            st.session_state.phase = "completed"


def _sync_process_status() -> None:
    process = st.session_state.worker_process
    if process is None or process.poll() is None:
        return
    if st.session_state.phase in {"completed", "failed"}:
        return
    if process.returncode == 0:
        st.session_state.phase = "completed"
        return
    if st.session_state.expecting_crash and st.session_state.phase in {"crash_requested", "waiting"}:
        st.session_state.phase = "crashed"
        selected_number = st.session_state.crash_at_step
        selected = WORKFLOW_STEPS[selected_number - 1]["id"]
        if st.session_state.step_statuses.get(selected) == "waiting":
            st.session_state.step_statuses[selected] = "crashed"
    else:
        st.session_state.phase = "failed"


def _send_sigkill(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        process.kill()
    else:
        process.send_signal(signal.SIGKILL)


def _event_summary(event: dict[str, Any]) -> str:
    event_type = event.get("event", "worker_output")
    label = event.get("label", event.get("step_id", ""))
    number = event.get("step_number", "")
    prefix = f"Attempt {event.get('attempt', '?')}"
    if event_type == "workflow_started":
        return f"{prefix}: workflow started"
    if event_type == "step_started":
        return f"{prefix}: started step {number} — {label}"
    if event_type == "ready_to_crash":
        return f"{prefix}: waiting before step {number} — click Inject SIGKILL / Crash"
    if event_type == "step_work_finished":
        return f"{prefix}: simulated operation finished — {event.get('simulated_tokens', 0)} synthetic tokens"
    if event_type == "step_completed":
        kind = "WAL cache hit" if event.get("cached") else "checkpoint committed"
        return f"{prefix}: {kind} for step {number} in {event.get('duration_ms', 0):.3f} ms"
    if event_type == "workflow_completed":
        return f"{prefix}: workflow complete in {event.get('elapsed_ms', 0):.3f} ms"
    if event_type == "worker_output":
        return f"{prefix}: {event.get('message', '')}"
    if event_type == "hold_timeout":
        return f"{prefix}: timed out waiting for the crash button"
    return f"{prefix}: {event_type} {label}".strip()


def _read_wal_rows(wal_path: Path) -> list[dict[str, Any]]:
    rows = []
    for index, record in enumerate(read_wal_records(wal_path), start=1):
        payload = record.get("payload", {})
        summary = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        rows.append(
            {
                "Seq": record.get("seq", index),
                "Event": record.get("event_type", "unknown"),
                "Timestamp": record.get("timestamp", ""),
                "Details": summary[:180] or record.get("raw", ""),
            }
        )
    return rows


def _metrics() -> tuple[str, str, int, int]:
    events = st.session_state.events
    current_attempt = st.session_state.current_attempt
    mode = st.session_state.current_mode
    cached_events = [
        event
        for event in events
        if event.get("attempt") == current_attempt
        and event.get("event") == "step_completed"
        and event.get("cached")
    ]
    resume_latency = sum(float(event.get("duration_ms", 0.0)) for event in cached_events) if mode == "resume" else 0.0

    previous_completed = set(st.session_state.pre_crash_completed_ids)
    duplicate_ids = {
        event.get("step_id")
        for event in events
        if event.get("attempt") == current_attempt
        and event.get("event") == "step_started"
        and mode == "resume"
        and event.get("step_id") in previous_completed
    }
    token_costs = {definition["id"]: definition["simulated_tokens"] for definition in WORKFLOW_STEPS}
    duplicate_tokens = sum(token_costs.get(step_id, 0) for step_id in duplicate_ids)
    total_tokens = sum(token_costs.values())
    waste_pct = (duplicate_tokens / total_tokens * 100.0) if total_tokens else 0.0
    restored_tokens = sum(int(event.get("simulated_tokens", 0)) for event in cached_events)
    return (
        f"{resume_latency:.3f} ms" if mode == "resume" else "—",
        f"{waste_pct:.1f}%" if mode == "resume" else "—",
        len(cached_events),
        restored_tokens,
    )


def _render_step_cards() -> None:
    statuses = st.session_state.step_statuses
    phase = st.session_state.phase
    columns = st.columns(len(WORKFLOW_STEPS))
    for column, definition in zip(columns, WORKFLOW_STEPS):
        state = statuses.get(definition["id"], "pending")
        with column:
            with st.container(border=True):
                st.caption(f"STEP {WORKFLOW_STEPS.index(definition) + 1}")
                st.markdown(f"**{definition['label']}**")
                st.caption(f"{definition['simulated_tokens']} simulated tokens")
                if state == "restored":
                    st.success("WAL-restored · skipped")
                elif state == "completed":
                    st.success("Checkpointed")
                elif state == "running":
                    st.info("Executing")
                elif state == "waiting":
                    st.warning("Ready for crash")
                elif state == "crashed":
                    st.error("Interrupted")
                else:
                    st.caption("Pending")
                if phase == "crashed" and state == "crashed":
                    st.caption("No result was committed for this step.")


def _render_wal_viewer() -> None:
    run_dir = st.session_state.run_dir
    if not run_dir:
        st.info("Start the workflow to create a WAL for this demo session.")
        return
    wal_path = Path(run_dir) / "state.wal.jsonl"
    rows = _read_wal_rows(wal_path)
    if not rows:
        st.info("Waiting for the first WAL record.")
        return
    st.dataframe(rows[-25:], use_container_width=True, hide_index=True)
    with st.expander("Raw WAL frames"):
        raw_lines = wal_path.read_text(encoding="utf-8", errors="replace").splitlines()
        st.code("\n".join(raw_lines[-25:]), language="text")


def _render_monitor() -> None:
    _drain_events()
    _sync_process_status()
    phase = st.session_state.phase
    process = st.session_state.worker_process

    if phase == "idle":
        st.info("Start the workflow to watch its steps and WAL records here.")
    elif phase == "running":
        st.info("The worker is executing the deterministic five-step workflow.")
    elif phase == "waiting":
        selected = WORKFLOW_STEPS[st.session_state.crash_at_step - 1]
        st.warning(f"Worker is paused before {selected['label']}. Inject a crash to continue the demo.")
        if st.button("Inject SIGKILL / Crash", type="primary", key="inject-crash"):
            current_attempt = st.session_state.current_attempt
            completed_ids = [
                event["step_id"]
                for event in st.session_state.events
                if event.get("attempt") == current_attempt
                and event.get("event") == "step_completed"
                and not event.get("cached")
            ]
            st.session_state.pre_crash_completed_ids = list(dict.fromkeys(completed_ids))
            st.session_state.phase = "crash_requested"
            st.session_state.expecting_crash = True
            if process is not None:
                _send_sigkill(process)
            st.rerun()
    elif phase == "crash_requested":
        st.warning("SIGKILL sent. Waiting for the worker to exit…")
    elif phase == "crashed":
        st.error("Worker terminated. Previously checkpointed steps remain available in the WAL.")
        if st.button("Resume from WAL", type="primary", key="resume-from-wal"):
            _launch_worker(st.session_state.run_dir, None, "resume")
            st.rerun()
    elif phase == "completed":
        st.success("Workflow complete. Restored steps were read from the WAL without re-executing their operations.")
    elif phase == "failed":
        st.error("The worker exited unexpectedly. Review the event log and WAL records below.")

    _render_step_cards()

    fast_forward_ms, waste_pct, restored_steps, restored_tokens = _metrics()
    metric_columns = st.columns(4)
    metric_columns[0].metric(
        "Resumption latency",
        fast_forward_ms,
        help="Sum of measured lookup time for cached step results during resume; excludes newly executed steps.",
    )
    metric_columns[1].metric(
        "Duplicate token waste",
        waste_pct,
        help="Synthetic token estimate for previously completed steps that execute again. This demo crashes before the selected operation starts and makes no LLM/API calls.",
    )
    metric_columns[2].metric("WAL-restored steps", restored_steps)
    metric_columns[3].metric("Synthetic tokens skipped", restored_tokens)

    with st.expander("Worker event log", expanded=True):
        if st.session_state.events:
            st.code("\n".join(_event_summary(event) for event in st.session_state.events[-30:]), language="text")
        else:
            st.caption("Worker events will appear here as execution progresses.")

    with st.expander("WAL event viewer", expanded=True):
        _render_wal_viewer()


st.set_page_config(page_title="LetItLoop crash visualizer", page_icon="🔁", layout="wide")
_init_state()

st.title("LetItLoop Crash & Fast-Forward Visualizer")
st.caption("An interactive, deterministic demo of SIGKILL recovery using the real LetItLoop durable decorator and WAL.")
st.warning("No model or network calls are made. Token costs are synthetic, so the UI is safe to run locally without API credentials.")

active_phases = {"running", "waiting", "crash_requested"}
selected_step = st.selectbox(
    "Inject the crash before which step?",
    options=range(1, len(WORKFLOW_STEPS) + 1),
    index=2,
    format_func=lambda number: f"Step {number}: {WORKFLOW_STEPS[number - 1]['label']}",
    disabled=st.session_state.phase in active_phases or st.session_state.phase == "crashed",
)
if st.button("Start Agent Workflow", type="primary", disabled=st.session_state.phase in active_phases or st.session_state.phase == "crashed"):
    run_dir = tempfile.mkdtemp(prefix="letitloop_interactive_demo_")
    st.session_state.run_dir = run_dir
    st.session_state.events = []
    st.session_state.step_statuses = {}
    st.session_state.attempt_count = 0
    st.session_state.current_attempt = 0
    st.session_state.current_mode = "initial"
    st.session_state.pre_crash_completed_ids = []
    st.session_state.crash_at_step = int(selected_step)
    st.session_state.event_queue = queue.Queue()
    _launch_worker(run_dir, int(selected_step), "initial")
    st.rerun()

if st.session_state.run_dir:
    st.caption(f"WAL directory: {st.session_state.run_dir}")

@st.fragment(run_every=0.5)
def _live_monitor() -> None:
    _render_monitor()

_live_monitor()

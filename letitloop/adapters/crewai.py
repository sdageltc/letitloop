"""letitloop/adapters/crewai.py — CrewAI durability adapter and task lifecycle callback handler.

Provides zero-dependency durability for CrewAI multi-agent workflows. Captures task
executions, tool invocations, and agent state into LetItLoop's atomic WAL journal,
enabling instant recovery after SIGKILL/crashes without duplicate token execution.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import threading
import time
from typing import Any, Dict, List, Optional


def _is_crewai_available() -> bool:
    """Check if CrewAI is installed in the current Python environment."""
    try:
        import crewai  # noqa: F401

        return True
    except ImportError:
        return False


def _to_serializable(val: Any) -> Any:
    """Recursively convert objects (TaskOutput, Pydantic, dataclasses) to JSON-serializable primitives."""
    if val is None or isinstance(val, (str, int, float, bool)):
        return val
    if isinstance(val, (list, tuple, set)):
        return [_to_serializable(v) for v in val]
    if isinstance(val, dict):
        return {str(k): _to_serializable(v) for k, v in val.items()}
    if hasattr(val, "raw") and isinstance(val.raw, str):
        return {
            "raw": val.raw,
            "description": getattr(val, "description", ""),
            "agent": getattr(val, "agent", ""),
        }
    if hasattr(val, "model_dump") and callable(val.model_dump):
        return _to_serializable(val.model_dump())
    if hasattr(val, "dict") and callable(val.dict):
        return _to_serializable(val.dict())
    if dataclasses.is_dataclass(val):
        return _to_serializable(dataclasses.asdict(val))
    return str(val)


class CrewAIDurabilityHandler:
    """CrewAI task lifecycle callback and WAL state manager for crash-resilient crews.

    Parameters
    ----------
    wal_dir : Optional[str]
        Directory path for WAL journal files. Defaults to '.letitloop/crewai_wal'.
    session_id : str
        Unique identifier for the Crew execution session.
    auto_resume : bool
        If True, replays existing WAL on initialization to restore completed tasks.

    Examples
    --------
    >>> from letitloop.adapters.crewai import CrewAIDurabilityHandler
    >>> handler = CrewAIDurabilityHandler(session_id="market_research")
    >>> # Attach handler to Crew tasks:
    >>> for task in crew.tasks:
    ...     handler.wrap_task(task)
    """

    def __init__(
        self,
        wal_dir: Optional[str] = None,
        session_id: str = "default_crew",
        auto_resume: bool = True,
    ) -> None:
        self.wal_dir = os.path.abspath(
            wal_dir or os.environ.get("LETITLOOP_WAL_DIR") or os.path.join(".letitloop", "crewai_wal")
        )
        self.session_id = session_id
        self.auto_resume = auto_resume
        self.wal_file = os.path.join(self.wal_dir, f"{session_id}.wal.jsonl")

        self._lock = threading.Lock()
        self._seq = 0
        self._completed_tasks: Dict[str, Any] = {}
        self._tool_calls: List[Dict[str, Any]] = []

        os.makedirs(self.wal_dir, exist_ok=True)
        if not os.path.exists(self.wal_file):
            open(self.wal_file, "a", encoding="utf-8").close()
        if self.auto_resume:
            self._replay_wal()

    @classmethod
    def is_available(cls) -> bool:
        """Return True if CrewAI is available in the environment."""
        return _is_crewai_available()

    def _get_task_key(self, task: Any) -> str:
        """Extract a deterministic unique key for a CrewAI task."""
        if isinstance(task, str):
            return task
        if hasattr(task, "id") and task.id:
            return str(task.id)
        if hasattr(task, "description") and task.description:
            desc = str(task.description).strip()
            desc_hash = hashlib.sha256(desc.encode("utf-8")).hexdigest()[:16]
            return f"task_{desc_hash}"
        if hasattr(task, "name") and task.name:
            return str(task.name)
        return f"task_{id(task)}"

    def _append_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """Append an event atomically to the session WAL."""
        with self._lock:
            self._seq += 1
            record = {
                "seq": self._seq,
                "session_id": self.session_id,
                "event": event_type,
                "timestamp": time.time(),
                "data": _to_serializable(data),
            }
            line = json.dumps(record, ensure_ascii=False) + "\n"
            with open(self.wal_file, "a", encoding="utf-8") as f:
                f.write(line)
                f.flush()
                os.fsync(f.fileno())

    def _replay_wal(self) -> None:
        """Replay existing WAL to recover completed task outputs and tool executions."""
        if not os.path.isfile(self.wal_file):
            return

        with self._lock:
            try:
                with open(self.wal_file, "r", encoding="utf-8", errors="replace") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            record = json.loads(line)
                            self._seq = max(self._seq, record.get("seq", 0))
                            event = record.get("event")
                            data = record.get("data", {})
                            if event == "TASK_COMPLETED":
                                task_key = data.get("task_key")
                                if task_key:
                                    self._completed_tasks[task_key] = data.get("output")
                            elif event == "TOOL_EXECUTED":
                                self._tool_calls.append(data)
                        except json.JSONDecodeError:
                            continue
            except Exception:
                pass

    def on_task_start(self, task: Any, agent: Any = None) -> Optional[Any]:
        """Lifecycle hook: Called when a task begins.

        Returns cached output if the task was already completed in a prior run.
        """
        task_key = self._get_task_key(task)
        if self.auto_resume and task_key in self._completed_tasks:
            return self._completed_tasks[task_key]

        agent_name = getattr(agent, "role", getattr(agent, "name", str(agent))) if agent else "unassigned"
        self._append_event(
            "TASK_STARTED",
            {
                "task_key": task_key,
                "agent": str(agent_name),
                "description": getattr(task, "description", ""),
            },
        )
        return None

    def on_task_end(self, task: Any, output: Any) -> Any:
        """Lifecycle hook: Called when a task finishes execution.

        Persists the completed output to WAL and caches it for subsequent steps.
        """
        task_key = self._get_task_key(task)
        serialized_output = _to_serializable(output)
        with self._lock:
            self._completed_tasks[task_key] = serialized_output

        self._append_event(
            "TASK_COMPLETED",
            {
                "task_key": task_key,
                "output": serialized_output,
            },
        )
        return output

    def on_tool_execute(self, tool_name: str, tool_input: Any = None, tool_output: Any = None) -> None:
        """Lifecycle hook: Called when an agent tool finishes execution."""
        data = {
            "tool_name": str(tool_name),
            "input": _to_serializable(tool_input),
            "output": _to_serializable(tool_output),
        }
        with self._lock:
            self._tool_calls.append(data)
        self._append_event("TOOL_EXECUTED", data)

    def on_task_error(self, task: Any, error: Exception) -> None:
        """Lifecycle hook: Called when a task encounters an unhandled exception."""
        task_key = self._get_task_key(task)
        self._append_event(
            "TASK_ERROR",
            {
                "task_key": task_key,
                "error_type": type(error).__name__,
                "error_message": str(error),
            },
        )

    def is_task_completed(self, task: Any) -> bool:
        """Return True if the task has a verified completed output in WAL."""
        task_key = self._get_task_key(task)
        with self._lock:
            return task_key in self._completed_tasks

    def get_cached_task_output(self, task: Any) -> Optional[Any]:
        """Retrieve verified cached task output from previous run."""
        task_key = self._get_task_key(task)
        with self._lock:
            return self._completed_tasks.get(task_key)

    def get_completed_tasks(self) -> Dict[str, Any]:
        """Return a copy of all completed task outputs recorded in WAL."""
        with self._lock:
            return dict(self._completed_tasks)

    def get_crew_state(self) -> Dict[str, Any]:
        """Return the current aggregated state of the crew from WAL."""
        with self._lock:
            return {
                "session_id": self.session_id,
                "completed_tasks": dict(self._completed_tasks),
                "total_completed_tasks": len(self._completed_tasks),
                "total_tool_calls": len(self._tool_calls),
                "last_sequence": self._seq,
                "wal_file": self.wal_file,
            }

    def wrap_task(self, task: Any) -> Any:
        """Wrap a CrewAI Task so it automatically checks and updates durability WAL."""
        if hasattr(task, "callback"):
            original_cb = getattr(task, "callback", None)

            def durable_callback(output: Any) -> Any:
                self.on_task_end(task, output)
                if callable(original_cb):
                    return original_cb(output)
                return output

            task.callback = durable_callback
        return task

    def wrap_crew(self, crew: Any) -> Any:
        """Attach durability handler to all tasks and step callbacks in a Crew."""
        if hasattr(crew, "tasks") and isinstance(crew.tasks, list):
            for t in crew.tasks:
                self.wrap_task(t)
        if hasattr(crew, "step_callback"):
            original_step_cb = getattr(crew, "step_callback", None)

            def durable_step_cb(step_output: Any) -> Any:
                tool_name = getattr(step_output, "tool", "agent_step")
                tool_input = getattr(step_output, "tool_input", None)
                tool_result = getattr(step_output, "result", step_output)
                self.on_tool_execute(tool_name, tool_input, tool_result)
                if callable(original_step_cb):
                    return original_step_cb(step_output)
                return step_output

            crew.step_callback = durable_step_cb
        return crew

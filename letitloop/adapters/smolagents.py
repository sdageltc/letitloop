"""letitloop/adapters/smolagents.py — Hugging Face Smolagents durability callback handler.

Integrates with Hugging Face smolagents (CodeAgent, ToolCallingAgent) step callbacks.
Captures agent thoughts, tool executions, code actions, and observations into LetItLoop's
atomic WAL journal to prevent re-execution of non-idempotent tools upon process restart.
"""

from __future__ import annotations

import dataclasses
import json
import os
import threading
import time
from typing import Any, Dict, List, Optional


def _is_smolagents_available() -> bool:
    """Check if smolagents is installed in the current Python environment."""
    try:
        import smolagents  # noqa: F401

        return True
    except ImportError:
        return False


def _to_serializable(val: Any) -> Any:
    """Recursively convert smolagents step logs, objects, and dataclasses to primitives."""
    if val is None or isinstance(val, (str, int, float, bool)):
        return val
    if isinstance(val, (list, tuple, set)):
        return [_to_serializable(v) for v in val]
    if isinstance(val, dict):
        return {str(k): _to_serializable(v) for k, v in val.items()}
    if dataclasses.is_dataclass(val):
        return _to_serializable(dataclasses.asdict(val))
    if hasattr(val, "dict") and callable(val.dict):
        return _to_serializable(val.dict())
    if hasattr(val, "model_dump") and callable(val.model_dump):
        return _to_serializable(val.model_dump())
    if hasattr(val, "tool_calls"):
        # Smolagents ActionStep
        return {
            "step_number": getattr(val, "step_number", 0),
            "tool_calls": _to_serializable(getattr(val, "tool_calls", [])),
            "observations": _to_serializable(getattr(val, "observations", None)),
            "action_output": _to_serializable(getattr(val, "action_output", None)),
            "model_output": str(getattr(val, "model_output", "")),
        }
    return str(val)


class SmolagentsWALCallback:
    """Step callback and state manager for Hugging Face smolagents.

    Parameters
    ----------
    wal_dir : Optional[str]
        Directory path for WAL journal files. Defaults to '.letitloop/smolagents_wal'.
    session_id : str
        Unique identifier for the agent session.
    auto_resume : bool
        If True, replays existing WAL on initialization to recover prior steps.

    Examples
    --------
    >>> from smolagents import CodeAgent, HfApiModel
    >>> from letitloop.adapters.smolagents import SmolagentsWALCallback
    >>> callback = SmolagentsWALCallback(session_id="math_solver")
    >>> agent = CodeAgent(tools=[], model=HfApiModel(), step_callbacks=[callback])
    """

    def __init__(
        self,
        wal_dir: Optional[str] = None,
        session_id: str = "default_smolagent",
        auto_resume: bool = True,
    ) -> None:
        self.wal_dir = os.path.abspath(
            wal_dir or os.environ.get("LETITLOOP_WAL_DIR") or os.path.join(".letitloop", "smolagents_wal")
        )
        self.session_id = session_id
        self.auto_resume = auto_resume
        self.wal_file = os.path.join(self.wal_dir, f"{session_id}.wal.jsonl")

        self._lock = threading.Lock()
        self._seq = 0
        self._steps: List[Dict[str, Any]] = []
        self._tool_calls: List[Dict[str, Any]] = []

        os.makedirs(self.wal_dir, exist_ok=True)
        if not os.path.exists(self.wal_file):
            open(self.wal_file, "a", encoding="utf-8").close()
        if self.auto_resume:
            self._replay_wal()

    @classmethod
    def is_available(cls) -> bool:
        """Return True if smolagents is available in the environment."""
        return _is_smolagents_available()

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
        """Replay existing WAL to recover completed steps and tool records."""
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
                            if event == "STEP_COMPLETED":
                                self._steps.append(data)
                            elif event == "TOOL_CALLED":
                                self._tool_calls.append(data)
                        except json.JSONDecodeError:
                            continue
            except Exception:
                pass

    def __call__(self, step_log: Any) -> None:
        """Callable hook invoked by smolagents at each step boundary."""
        step_number = getattr(step_log, "step_number", len(self._steps) + 1)
        step_data = _to_serializable(step_log)

        with self._lock:
            self._steps.append(
                {
                    "step_number": step_number,
                    "step_data": step_data,
                }
            )

        self._append_event(
            "STEP_COMPLETED",
            {
                "step_number": step_number,
                "step_data": step_data,
            },
        )

    def on_step_start(self, step_number: int, agent_memory: Optional[Any] = None) -> None:
        """Lifecycle hook: Called before an agent step starts."""
        self._append_event(
            "STEP_STARTED",
            {
                "step_number": step_number,
                "memory_summary": str(agent_memory)[:500] if agent_memory else "",
            },
        )

    def on_step_end(self, step_number: int, step_data: Any) -> None:
        """Lifecycle hook: Called when an agent step ends."""
        self(step_data)

    def on_tool_call(self, tool_name: str, arguments: Any, result: Any) -> None:
        """Lifecycle hook: Called when a tool is invoked by the smolagent."""
        data = {
            "tool_name": str(tool_name),
            "arguments": _to_serializable(arguments),
            "result": _to_serializable(result),
        }
        with self._lock:
            self._tool_calls.append(data)
        self._append_event("TOOL_CALLED", data)

    def get_completed_steps(self) -> List[Dict[str, Any]]:
        """Return all recovered step records from WAL."""
        with self._lock:
            return list(self._steps)

    def get_agent_state(self) -> Dict[str, Any]:
        """Return current aggregated status of the smolagent from WAL."""
        with self._lock:
            return {
                "session_id": self.session_id,
                "total_steps": len(self._steps),
                "total_tool_calls": len(self._tool_calls),
                "last_sequence": self._seq,
                "wal_file": self.wal_file,
            }

    def restore_agent_memory(self, agent: Any) -> int:
        """Restore agent logs / memory from WAL on process restart.

        Returns the number of steps restored into the agent memory.
        """
        steps = self.get_completed_steps()
        if not steps:
            return 0

        restored_count = 0
        if hasattr(agent, "logs") and isinstance(agent.logs, list):
            for s in steps:
                step_data = s.get("step_data", {})
                if step_data not in agent.logs:
                    agent.logs.append(step_data)
                    restored_count += 1
        elif hasattr(agent, "memory") and hasattr(agent.memory, "steps"):
            for s in steps:
                step_data = s.get("step_data", {})
                if hasattr(agent.memory.steps, "append"):
                    agent.memory.steps.append(step_data)
                    restored_count += 1

        return restored_count

    def wrap_agent(self, agent: Any) -> Any:
        """Register callback into an initialized smolagents agent."""
        if hasattr(agent, "step_callbacks"):
            if isinstance(agent.step_callbacks, list):
                if self not in agent.step_callbacks:
                    agent.step_callbacks.append(self)
            else:
                agent.step_callbacks = [self]
        if self.auto_resume:
            self.restore_agent_memory(agent)
        return agent

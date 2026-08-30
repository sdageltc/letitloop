"""letitloop/adapters/autogen.py — Microsoft AutoGen 0.4 / Magentic-One state serializer & checkpoint handler.

Provides zero-dependency state serialization and event-sourced checkpointing for Microsoft
AutoGen 0.4 (AgentChat / Magentic-One / Core) and legacy ConversableAgent architectures.
Flushes message histories, agent memories, and tool outputs atomically to LetItLoop WAL.
"""

from __future__ import annotations

import dataclasses
import json
import os
import threading
import time
from typing import Any, Dict, List, Optional


def _is_autogen_available() -> bool:
    """Check if AutoGen is installed in the current Python environment."""
    try:
        import autogen_core  # noqa: F401

        return True
    except ImportError:
        try:
            import autogen  # noqa: F401

            return True
        except ImportError:
            return False


def _to_serializable(val: Any) -> Any:
    """Recursively convert AutoGen messages, objects, and dataclasses to primitives."""
    if val is None or isinstance(val, (str, int, float, bool)):
        return val
    if isinstance(val, (list, tuple, set)):
        return [_to_serializable(v) for v in val]
    if isinstance(val, dict):
        return {str(k): _to_serializable(v) for k, v in val.items()}
    if dataclasses.is_dataclass(val):
        return _to_serializable(dataclasses.asdict(val))
    if hasattr(val, "model_dump") and callable(val.model_dump):
        return _to_serializable(val.model_dump())
    if hasattr(val, "dict") and callable(val.dict):
        return _to_serializable(val.dict())
    if hasattr(val, "to_dict") and callable(val.to_dict):
        return _to_serializable(val.to_dict())
    if hasattr(val, "content") and hasattr(val, "source"):
        # AutoGen 0.4 BaseChatMessage
        return {
            "source": str(val.source),
            "content": _to_serializable(val.content),
            "type": type(val).__name__,
        }
    return str(val)


class AutoGenStateSerializer:
    """State serializer and checkpoint manager for Microsoft AutoGen 0.4 and Magentic-One.

    Parameters
    ----------
    wal_dir : Optional[str]
        Directory path for WAL journal files. Defaults to '.letitloop/autogen_wal'.
    session_id : str
        Unique identifier for the multi-agent chat session.
    auto_resume : bool
        If True, replays existing WAL on initialization to recover prior agent states.

    Examples
    --------
    >>> from letitloop.adapters.autogen import AutoGenStateSerializer
    >>> serializer = AutoGenStateSerializer(session_id="magentic_one_run")
    >>> serializer.save_agent_state("AssistantAgent", {"memory": ["step 1"], "vars": {}})
    """

    def __init__(
        self,
        wal_dir: Optional[str] = None,
        session_id: str = "default_autogen",
        auto_resume: bool = True,
    ) -> None:
        self.wal_dir = os.path.abspath(
            wal_dir or os.environ.get("LETITLOOP_WAL_DIR") or os.path.join(".letitloop", "autogen_wal")
        )
        self.session_id = session_id
        self.auto_resume = auto_resume
        self.wal_file = os.path.join(self.wal_dir, f"{session_id}.wal.jsonl")

        self._lock = threading.Lock()
        self._seq = 0
        self._agent_states: Dict[str, Dict[str, Any]] = {}
        self._message_history: List[Dict[str, Any]] = []
        self._tool_calls: List[Dict[str, Any]] = []

        os.makedirs(self.wal_dir, exist_ok=True)
        if not os.path.exists(self.wal_file):
            open(self.wal_file, "a", encoding="utf-8").close()
        if self.auto_resume:
            self._replay_wal()

    @classmethod
    def is_available(cls) -> bool:
        """Return True if AutoGen is available in the environment."""
        return _is_autogen_available()

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
        """Replay existing WAL to recover agent states, message history, and tool records."""
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
                            if event == "AGENT_STATE_SAVED":
                                agent_name = data.get("agent_name")
                                if agent_name:
                                    self._agent_states[agent_name] = data.get("state", {})
                            elif event == "MESSAGE_SENT":
                                self._message_history.append(data)
                            elif event == "TOOL_CALLED":
                                self._tool_calls.append(data)
                        except json.JSONDecodeError:
                            continue
            except Exception:
                pass

    def save_agent_state(self, agent_name: str, state: Dict[str, Any]) -> str:
        """Atomically persist state for a named AutoGen agent."""
        serialized = _to_serializable(state)
        with self._lock:
            self._agent_states[agent_name] = serialized

        self._append_event(
            "AGENT_STATE_SAVED",
            {
                "agent_name": str(agent_name),
                "state": serialized,
            },
        )
        return f"state_{agent_name}_{self._seq}"

    def load_agent_state(self, agent_name: str) -> Optional[Dict[str, Any]]:
        """Load recovered state for a named AutoGen agent from WAL."""
        with self._lock:
            state = self._agent_states.get(agent_name)
            return dict(state) if state is not None else None

    def checkpoint_message(self, sender: str, recipient: str, message: Any) -> str:
        """Append an inter-agent message to the conversation history WAL."""
        serialized = _to_serializable(message)
        data = {
            "sender": str(sender),
            "recipient": str(recipient),
            "message": serialized,
        }
        with self._lock:
            self._message_history.append(data)

        self._append_event("MESSAGE_SENT", data)
        return f"msg_{self._seq}"

    def checkpoint_tool_call(self, agent_name: str, call_id: str, tool_name: str, args: Any, result: Any) -> str:
        """Append a tool invocation and result to the session WAL."""
        data = {
            "agent_name": str(agent_name),
            "call_id": str(call_id),
            "tool_name": str(tool_name),
            "args": _to_serializable(args),
            "result": _to_serializable(result),
        }
        with self._lock:
            self._tool_calls.append(data)

        self._append_event("TOOL_CALLED", data)
        return f"tool_{self._seq}"

    def create_checkpoint(self, session_id: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None) -> str:
        """Create a full group chat snapshot checkpoint."""
        sid = session_id or self.session_id
        with self._lock:
            snapshot = {
                "session_id": sid,
                "timestamp": time.time(),
                "agent_states": dict(self._agent_states),
                "total_messages": len(self._message_history),
                "total_tools": len(self._tool_calls),
                "metadata": metadata or {},
            }
        self._append_event("GROUP_SNAPSHOT_CREATED", snapshot)
        return f"chk_{sid}_{self._seq}"

    def get_message_history(self, session_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return the complete message history recovered from WAL."""
        with self._lock:
            return list(self._message_history)

    def get_all_agent_states(self) -> Dict[str, Dict[str, Any]]:
        """Return a copy of all active agent states from WAL."""
        with self._lock:
            return {k: dict(v) for k, v in self._agent_states.items()}

    def wrap_agent(self, agent: Any, agent_name: Optional[str] = None) -> Any:
        """Attach durability hooks to an AutoGen agent for transparent checkpointing."""
        name = agent_name or getattr(agent, "name", str(type(agent).__name__))

        # If agent has state restoration methods, restore it
        saved_state = self.load_agent_state(name)
        if saved_state and hasattr(agent, "load_state") and callable(agent.load_state):
            try:
                agent.load_state(saved_state)
            except Exception:
                pass

        # Wrap send/receive if present
        if hasattr(agent, "send") and callable(agent.send):
            original_send = agent.send

            def durable_send(message: Any, recipient: Any, request_reply: bool = True, silent: bool = False) -> Any:
                recip_name = getattr(recipient, "name", str(recipient))
                self.checkpoint_message(sender=name, recipient=recip_name, message=message)
                return original_send(message, recipient, request_reply=request_reply, silent=silent)

            agent.send = durable_send

        return agent

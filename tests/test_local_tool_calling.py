"""Unit tests for orchestrator.local_tool_calling (Local LLM Tool Calling Engine)."""

import json
import os
import sys

import pytest

from orchestrator.local_tool_calling import (
    LocalToolRegistry,
    ToolCallingError,
    build_default_registry,
)
from orchestrator.worker_adapters import LocalToolWorkerAdapter


def test_tool_registry_registration_and_execution():
    """Test registering and calling tools in LocalToolRegistry."""
    registry = LocalToolRegistry()

    # Register addition tool
    registry.register(
        name="add_numbers",
        description="Adds two numbers together",
        parameters={
            "type": "object",
            "properties": {
                "a": {"type": "number"},
                "b": {"type": "number"},
            },
            "required": ["a", "b"],
        },
        handler=lambda a, b: a + b,
    )

    tools = registry.get_openai_tools()
    assert len(tools) == 1
    assert tools[0]["function"]["name"] == "add_numbers"

    res = registry.execute_call("add_numbers", {"a": 10, "b": 32})
    assert res == 42


def test_tool_registry_missing_handler():
    """Test executing a tool with no handler raises ToolCallingError."""
    registry = LocalToolRegistry()
    registry.register(name="read_db", description="Read database", parameters={})

    with pytest.raises(ToolCallingError) as exc_info:
        registry.execute_call("read_db", {})
    assert "No handler registered" in str(exc_info.value)


def test_parse_tool_calls_hermes_tags():
    """Test parsing Hermes/Qwen <tool_call> XML tags."""
    text = """
    Let me check the database.
    <tool_call>
    {"name": "query_db", "arguments": {"table": "users", "limit": 5}}
    </tool_call>
    """
    calls = LocalToolRegistry.parse_tool_calls(text)
    assert len(calls) == 1
    assert calls[0]["name"] == "query_db"
    assert calls[0]["arguments"] == {"table": "users", "limit": 5}


def test_parse_tool_calls_markdown_code_fence():
    """Test parsing tool calls from markdown code fences."""
    text = """
    ```json
    {
        "name": "fetch_api",
        "arguments": {"endpoint": "/v1/health"}
    }
    ```
    """
    calls = LocalToolRegistry.parse_tool_calls(text)
    assert len(calls) == 1
    assert calls[0]["name"] == "fetch_api"
    assert calls[0]["arguments"] == {"endpoint": "/v1/health"}


# ---------------------------------------------------------------------------
# build_default_registry: sandboxed workspace tools
# ---------------------------------------------------------------------------


def test_default_registry_traversal_denied(tmp_path):
    """Path tools reject ../ traversal, absolute escapes, and deny-list hits."""
    (tmp_path / "src").mkdir(exist_ok=True)
    (tmp_path / "src" / "denied").mkdir(exist_ok=True)

    registry = build_default_registry(str(tmp_path), {"allow": ["src"], "deny": []})
    with pytest.raises(ToolCallingError):
        registry.execute_call("read_file", {"path": "../secret.txt"})
    with pytest.raises(ToolCallingError):
        registry.execute_call(
            "write_file",
            {"path": os.path.join(str(tmp_path.parent), "escape.txt"), "content": "x"},
        )

    guarded = build_default_registry(str(tmp_path), {"allow": ["src"], "deny": ["src/denied"]})
    with pytest.raises(ToolCallingError):
        guarded.execute_call("write_file", {"path": "src/denied/x.txt", "content": "x"})


def test_default_registry_write_read_replace_roundtrip(tmp_path):
    """write_file -> read_file -> replace_lines happy path."""
    registry = build_default_registry(str(tmp_path), {"allow": ["."], "deny": []})

    msg = registry.execute_call("write_file", {"path": "src/out.txt", "content": "line1\nline2\nline3\n"})
    assert "wrote" in msg
    assert registry.execute_call("read_file", {"path": "src/out.txt"}) == "line1\nline2\nline3\n"

    res = registry.execute_call(
        "replace_lines",
        {"path": "src/out.txt", "start_line": 2, "end_line": 2, "new_content": "replaced"},
    )
    assert res.startswith("replaced lines 2-2")
    assert registry.execute_call("read_file", {"path": "src/out.txt"}) == "line1\nreplaced\nline3\n"

    tools = registry.get_openai_tools()
    assert [t["function"]["name"] for t in tools] == ["read_file", "write_file", "replace_lines", "execute_command"]


def test_default_registry_execute_command_exit_code(tmp_path):
    """execute_command captures exit codes and returns a truncated output tail."""
    registry = build_default_registry(str(tmp_path), {"allow": ["."], "deny": []})
    res = registry.execute_call("execute_command", {"command": f'"{sys.executable}" -c "import sys; sys.exit(3)"'})
    assert res["exit_code"] == 3
    assert isinstance(res["output_tail"], str)


def test_default_registry_execute_command_output_tail(tmp_path):
    """Merged stdout/stderr is captured and tail-truncated to the cap."""
    from orchestrator.local_tool_calling import MAX_COMMAND_OUTPUT_CHARS

    registry = build_default_registry(str(tmp_path), {"allow": ["."], "deny": []})
    script = "import sys; sys.stdout.write('x' * 9000); sys.stderr.write('E' * 500)"
    res = registry.execute_call("execute_command", {"command": f'"{sys.executable}" -c "{script}"'})
    assert res["exit_code"] == 0
    assert len(res["output_tail"]) <= MAX_COMMAND_OUTPUT_CHARS
    assert res["output_tail"].endswith("EE")


# ---------------------------------------------------------------------------
# LocalToolWorkerAdapter: multi-turn tool-calling loop
# ---------------------------------------------------------------------------


def _scripted_transport(responses, captured):
    def transport(messages, tools):
        captured.append(list(messages))
        return responses.pop(0)

    return transport


def test_local_tool_adapter_loop_success(tmp_path):
    """Scripted fake transport: write_file -> read_file -> final content answer."""
    responses = [
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "c1",
                                "type": "function",
                                "function": {
                                    "name": "write_file",
                                    "arguments": json.dumps({"path": "out.txt", "content": "hello world"}),
                                },
                            }
                        ],
                    }
                }
            ]
        },
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "c2",
                                "type": "function",
                                "function": {"name": "read_file", "arguments": {"path": "out.txt"}},
                            }
                        ],
                    }
                }
            ]
        },
        {"choices": [{"message": {"role": "assistant", "content": "Done: out.txt contains hello world"}}]},
    ]
    captured = []
    adapter = LocalToolWorkerAdapter(config={"model": "test-model"}, transport=_scripted_transport(responses, captured))

    res = adapter.execute("create out.txt", str(tmp_path), "task_loop_ok")

    assert res["exit_code"] == 0
    assert res["stdout"] == "Done: out.txt contains hello world"
    assert res["approach"] == "local_tool_calling"
    assert (tmp_path / "out.txt").read_text(encoding="utf-8") == "hello world"

    journal_path = tmp_path / "scratch" / "orchestrator_runs" / "task_loop_ok" / "worker_output.log"
    journal = journal_path.read_text(encoding="utf-8")
    assert "=== TURN 1 ===" in journal
    assert "=== TURN 3 ===" in journal
    assert "write_file" in journal
    assert "FINAL ANSWER" in journal

    roles = [m["role"] for m in captured[2]]
    assert roles.count("tool") == 2
    assert captured[2][-1]["tool_call_id"] == "c2"


def test_local_tool_adapter_max_turns_termination(tmp_path):
    """Loop that never produces a final answer terminates with reason=max_turns."""

    def loop_transport(messages, tools):
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "cx",
                                "type": "function",
                                "function": {"name": "read_file", "arguments": {"path": "missing.txt"}},
                            }
                        ],
                    }
                }
            ]
        }

    adapter = LocalToolWorkerAdapter(config={"model": "test-model", "max_turns": 2}, transport=loop_transport)
    res = adapter.execute("loop forever", str(tmp_path), "task_max_turns")

    assert res["exit_code"] == 1
    assert res["success"] is False
    assert res["reason"] == "max_turns"


def test_local_tool_adapter_repair_nudge_once_then_valid(tmp_path):
    """One garbage turn triggers exactly one repair nudge; next turn recovers."""
    responses = [
        {"choices": [{"message": {"role": "assistant", "content": ""}}]},
        {"choices": [{"message": {"role": "assistant", "content": "final ok"}}]},
    ]
    captured = []
    adapter = LocalToolWorkerAdapter(config={"model": "test-model"}, transport=_scripted_transport(responses, captured))
    res = adapter.execute("do thing", str(tmp_path), "task_nudge")

    assert res["exit_code"] == 0
    assert res["stdout"] == "final ok"
    assert len(captured) == 2

    nudges_first = [m for m in captured[0] if m.get("role") == "user" and "no valid tool call" in m.get("content", "")]
    nudges_second = [m for m in captured[1] if m.get("role") == "user" and "no valid tool call" in m.get("content", "")]
    assert len(nudges_first) == 0
    assert len(nudges_second) == 1


def test_local_tool_adapter_second_garbage_after_nudge_fails(tmp_path):
    """Persistent garbage turns after the single nudge abort with an error."""

    def garbage_transport(messages, tools):
        return {"choices": [{"message": {"role": "assistant", "content": ""}}]}

    adapter = LocalToolWorkerAdapter(config={"model": "test-model", "max_turns": 5}, transport=garbage_transport)
    res = adapter.execute("x", str(tmp_path), "task_garbage2")

    assert res["exit_code"] == 1
    assert "repair nudge" in res["stderr"]
    assert res["approach"] == "error"


def test_local_tool_adapter_requires_model():
    """Missing model config yields a sibling-style error result."""
    adapter = LocalToolWorkerAdapter()
    res = adapter.execute("p", ".", "task_no_model")
    assert res["exit_code"] == 1
    assert "model" in res["stderr"].lower()
    assert res["approach"] == "error"


def test_local_tool_adapter_hermes_tag_fallback_calls(tmp_path):
    """Non-native <tool_call> tags in content are parsed and executed."""
    responses = [
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": '<tool_call>{"name": "write_file", "arguments": {"path": "tagged.txt", "content": "via tags"}}</tool_call>',
                    }
                }
            ]
        },
        {"choices": [{"message": {"role": "assistant", "content": "wrote via hermes tags"}}]},
    ]
    captured = []
    adapter = LocalToolWorkerAdapter(config={"model": "test-model"}, transport=_scripted_transport(responses, captured))
    res = adapter.execute("write tagged file", str(tmp_path), "task_tags")

    assert res["exit_code"] == 0
    assert (tmp_path / "tagged.txt").read_text(encoding="utf-8") == "via tags"
    assistant_msgs = [m for m in captured[1] if m.get("role") == "assistant" and m.get("tool_calls")]
    assert len(assistant_msgs) == 1

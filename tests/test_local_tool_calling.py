"""Unit tests for orchestrator.local_tool_calling (Local LLM Tool Calling Engine)."""

import pytest

from orchestrator.local_tool_calling import LocalToolRegistry, ToolCallingError


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

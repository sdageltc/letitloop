"""Local LLM First-Class Tool Calling Adapter for letitloop.

Supports native function/tool-calling schemas for local open-weights models
(Qwen 2.5 Coder, DeepSeek-Coder, Llama 3.3) via Ollama, vLLM, and LM Studio.
Handles tool declaration, schema validation, and tool-call parsing (JSON & Hermes/Qwen tags).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class ToolCallingError(Exception):
    """Base error for local tool calling operations."""


class LocalToolRegistry:
    """Registry and executor for tools callable by local LLM workers."""

    def __init__(self):
        self._tools: Dict[str, Dict[str, Any]] = {}
        self._handlers: Dict[str, Callable[..., Any]] = {}

    def register(
        self,
        name: str,
        description: str,
        parameters: Dict[str, Any],
        handler: Optional[Callable[..., Any]] = None,
    ) -> None:
        """Register a function tool with JSON Schema parameters."""
        self._tools[name] = {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": parameters,
            },
        }
        if handler:
            self._handlers[name] = handler

    def get_openai_tools(self) -> List[Dict[str, Any]]:
        """Return tools in OpenAI / Ollama standard tool definition format."""
        return list(self._tools.values())

    def execute_call(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        """Execute the registered handler for a tool call."""
        if tool_name not in self._handlers:
            raise ToolCallingError(f"No handler registered for tool '{tool_name}'")
        try:
            return self._handlers[tool_name](**arguments)
        except Exception as e:
            raise ToolCallingError(f"Error executing tool '{tool_name}': {e}") from e

    @staticmethod
    def parse_tool_calls(response_text: str) -> List[Dict[str, Any]]:
        """Extract tool calls from response text supporting:
        1. Pure JSON tool call arrays / objects
        2. Hermes / Qwen `<tool_call>{"name": ..., "arguments": ...}</tool_call>` tags
        3. Markdown ```json blocks with tool calls.
        """
        calls: List[Dict[str, Any]] = []
        if not response_text:
            return calls

        # 1. Check for XML-style <tool_call> tags (Hermes / Qwen standard)
        tag_matches = re.findall(r"<tool_call>(.*?)</tool_call>", response_text, re.DOTALL)
        for match in tag_matches:
            try:
                parsed = json.loads(match.strip())
                if isinstance(parsed, dict) and "name" in parsed:
                    calls.append(
                        {
                            "name": parsed["name"],
                            "arguments": parsed.get("arguments", parsed.get("parameters", {})),
                        }
                    )
            except json.JSONDecodeError:
                continue

        if calls:
            return calls

        # 2. Check for markdown code fence JSON blocks
        fence_matches = re.findall(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", response_text, re.DOTALL)
        for match in fence_matches:
            try:
                parsed = json.loads(match.strip())
                if isinstance(parsed, dict) and ("name" in parsed or "tool" in parsed):
                    calls.append(
                        {
                            "name": parsed.get("name") or parsed.get("tool"),
                            "arguments": parsed.get("arguments", parsed.get("parameters", {})),
                        }
                    )
                elif isinstance(parsed, list):
                    for item in parsed:
                        if isinstance(item, dict) and ("name" in item or "tool" in item):
                            calls.append(
                                {
                                    "name": item.get("name") or item.get("tool"),
                                    "arguments": item.get("arguments", item.get("parameters", {})),
                                }
                            )
            except json.JSONDecodeError:
                continue

        if calls:
            return calls

        # 3. Direct JSON string check
        stripped = response_text.strip()
        if (stripped.startswith("{") and stripped.endswith("}")) or (
            stripped.startswith("[") and stripped.endswith("]")
        ):
            try:
                parsed = json.loads(stripped)
                if isinstance(parsed, dict) and "name" in parsed:
                    calls.append(
                        {
                            "name": parsed["name"],
                            "arguments": parsed.get("arguments", parsed.get("parameters", {})),
                        }
                    )
                elif isinstance(parsed, list):
                    for item in parsed:
                        if isinstance(item, dict) and "name" in item:
                            calls.append(
                                {
                                    "name": item["name"],
                                    "arguments": item.get("arguments", item.get("parameters", {})),
                                }
                            )
            except json.JSONDecodeError:
                pass

        return calls

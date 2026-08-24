"""Local LLM First-Class Tool Calling Adapter for letitloop.

Supports native function/tool-calling schemas for local open-weights models
(Qwen 2.5 Coder, DeepSeek-Coder, Llama 3.3) via Ollama, vLLM, and LM Studio.
Handles tool declaration, schema validation, and tool-call parsing (JSON & Hermes/Qwen tags).
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from typing import Any, Callable, Dict, List, Optional

from .contract import check_path_allowed

logger = logging.getLogger(__name__)

DEFAULT_COMMAND_TIMEOUT_S = 120
MAX_COMMAND_OUTPUT_CHARS = 8000


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


def _scoped_resolver(workspace_root: str, contract_scope: Optional[Dict[str, Any]]) -> Callable[[str], str]:
    """Return a resolver that canonicalizes tool path args under workspace scope.

    Uses contract.check_path_allowed (realpath + commonpath) so symlink, `..`
    traversal, and absolute escapes are rejected before any file access.
    """
    allowed = list((contract_scope or {}).get("allow", []))
    denied = list((contract_scope or {}).get("deny", []))

    def resolve(path: str) -> str:
        ok, err = check_path_allowed(path, allowed, denied, workspace_root)
        if not ok:
            raise ToolCallingError(f"tool path rejected by workspace scope: {err}")
        ws = os.path.realpath(os.path.abspath(workspace_root))
        raw = path if os.path.isabs(path) else os.path.join(ws, path)
        return os.path.realpath(os.path.abspath(raw))

    return resolve


def build_default_registry(workspace_root: str, contract_scope: Optional[Dict[str, Any]] = None) -> LocalToolRegistry:
    """Build a LocalToolRegistry with the four default sandboxed workspace tools.

    Tools: read_file(path), write_file(path, content),
    replace_lines(path, start_line, end_line, new_content), execute_command(command).
    Path tools enforce the contract workspace scope; execute_command runs with
    cwd=workspace_root and returns {"exit_code", "output_tail"}.
    """
    scope = contract_scope or {"allow": ["."], "deny": []}
    resolve = _scoped_resolver(workspace_root, scope)
    registry = LocalToolRegistry()

    def read_file(path: str) -> str:
        full = resolve(path)
        with open(full, "r", encoding="utf-8") as f:
            return f.read()

    def write_file(path: str, content: str) -> str:
        full = resolve(path)
        parent = os.path.dirname(full)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)
        return f"wrote {len(content)} chars to {path}"

    def replace_lines(path: str, start_line: int, end_line: int, new_content: str) -> str:
        full = resolve(path)
        with open(full, "r", encoding="utf-8") as f:
            lines = f.readlines()
        if start_line < 1 or end_line < start_line or end_line > len(lines):
            raise ToolCallingError(f"invalid line range {start_line}-{end_line} for {path} ({len(lines)} lines)")
        new_lines = new_content.splitlines(keepends=True)
        if new_lines and not new_lines[-1].endswith("\n") and end_line < len(lines):
            new_lines[-1] += "\n"
        lines[start_line - 1 : end_line] = new_lines
        with open(full, "w", encoding="utf-8") as f:
            f.writelines(lines)
        return f"replaced lines {start_line}-{end_line} in {path}"

    def execute_command(command: str) -> Dict[str, Any]:
        try:
            proc = subprocess.run(
                command,
                shell=True,  # nosec B602
                cwd=workspace_root,
                capture_output=True,
                text=True,
                timeout=DEFAULT_COMMAND_TIMEOUT_S,
            )
            exit_code = proc.returncode
            output = (proc.stdout or "") + (proc.stderr or "")
        except subprocess.TimeoutExpired:
            exit_code = 124
            output = f"command timed out after {DEFAULT_COMMAND_TIMEOUT_S} seconds"
        except Exception as e:
            exit_code = 1
            output = f"command execution error: {e}"
        return {"exit_code": exit_code, "output_tail": output[-MAX_COMMAND_OUTPUT_CHARS:]}

    registry.register(
        name="read_file",
        description="Read a UTF-8 text file from the workspace.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to the workspace root"},
            },
            "required": ["path"],
        },
        handler=read_file,
    )
    registry.register(
        name="write_file",
        description="Write (create or overwrite) a UTF-8 text file in the workspace.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to the workspace root"},
                "content": {"type": "string", "description": "Full file content to write"},
            },
            "required": ["path", "content"],
        },
        handler=write_file,
    )
    registry.register(
        name="replace_lines",
        description="Replace an inclusive 1-based line range in an existing workspace file.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to the workspace root"},
                "start_line": {"type": "integer", "description": "First line to replace (1-based, inclusive)"},
                "end_line": {"type": "integer", "description": "Last line to replace (1-based, inclusive)"},
                "new_content": {"type": "string", "description": "Replacement text for the line range"},
            },
            "required": ["path", "start_line", "end_line", "new_content"],
        },
        handler=replace_lines,
    )
    registry.register(
        name="execute_command",
        description="Execute a shell command inside the workspace root.",
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command string to execute"},
            },
            "required": ["command"],
        },
        handler=execute_command,
    )
    return registry
